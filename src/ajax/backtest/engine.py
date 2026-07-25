"""Walk-forward backtest.

No-lookahead is enforced structurally rather than by convention:

* features on day D are computed from a price frame truncated at D,
* entry is priced at D+1's open — never D's close, which was not knowable when
  the signal fired,
* exit is 5 *trading* days after entry.

The result mirrors how the live agent actually runs: signals on the prior close,
orders the next morning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ajax.backtest import costs
from ajax.backtest.price_source import ChainedPriceSource, OptionPriceSource
from ajax.broker.contracts import build_occ_symbol, monthly_expiries_between
from ajax.config import Config
from ajax.data import prices as price_data
from ajax.data import universe as universe_mod
from ajax.options.greeks import Right, bs_delta, strike_for_delta, year_fraction
from ajax.options.volatility import apply_vol_haircut, realized_volatility
from ajax.portfolio.allocator import allocate
from ajax.portfolio.sizing import effective_premium_cap, size_position
from ajax.signals.labels import Label, label_candidates
from ajax.signals.scoring import compute_features, score_universe
from ajax.utils.calendar import add_trading_days, to_date, trading_days

log = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    ticker: str
    right: Right
    occ_symbol: str
    strike: float
    expiry: date
    signal_date: date
    entry_date: date
    exit_date: date
    dte_at_entry: int
    qty: int
    entry_price: float
    exit_price: float
    entry_source: str
    exit_source: str
    entry_underlying: float
    exit_underlying: float | None
    delta_at_entry: float
    sigma_at_entry: float
    composite: float
    pnl: float
    pnl_pct: float
    commission: float

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    skips: dict[str, int] = field(default_factory=dict)
    signal_days: int = 0
    price_source_counts: dict[str, int] = field(default_factory=dict)
    constituent_reconstruction: str = "unavailable"
    warnings: list[str] = field(default_factory=list)
    start: date | None = None
    end: date | None = None

    def add_skip(self, reason: str) -> None:
        self.skips[reason] = self.skips.get(reason, 0) + 1


@dataclass
class _OpenPosition:
    trade_stub: dict
    exit_on: date


def _round_strike(strike: float) -> float:
    """Snap to a plausible listed strike increment."""
    if strike >= 200:
        return round(strike / 5.0) * 5.0
    if strike >= 50:
        return round(strike / 2.5) * 2.5
    return round(strike)


def _choose_expiry(as_of: date, cfg: Config) -> date | None:
    opts = cfg.options
    horizon = monthly_expiries_between(as_of, as_of + pd.Timedelta(days=180).to_pytimedelta())
    in_window = [
        e for e in horizon if opts.dte_target_min <= (e - as_of).days <= opts.dte_target_max
    ]
    if in_window:
        return min(in_window, key=lambda e: abs((e - as_of).days - (opts.dte_target_min + opts.dte_target_max) / 2))
    above_floor = [e for e in horizon if (e - as_of).days >= opts.dte_hard_floor]
    return min(above_floor, key=lambda e: (e - as_of).days) if above_floor else None


def _synthesize_contract(
    ticker: str,
    right: Right,
    as_of: date,
    entry_date: date,
    spot: float,
    closes: pd.Series,
    cfg: Config,
) -> tuple[str, float, date, float, float] | None:
    """Pick a plausible contract at the target delta for a historical date.

    Returns ``(occ_symbol, strike, expiry, delta, sigma)``. Real historical
    greeks do not exist at any accessible price, so the delta here is always
    modelled — the report says so rather than implying otherwise.

    The DTE window is measured from ``entry_date``, not ``as_of``: the floor has
    to hold when the position is actually opened, and entry is the next session.
    Both dates are known at signal time, so this introduces no lookahead — only
    the spot and volatility inputs come from ``as_of``.
    """
    expiry = _choose_expiry(entry_date, cfg)
    if expiry is None:
        return None

    rv = realized_volatility(closes[closes.index.date <= as_of], cfg.backtest.realized_vol_window)
    if rv is None:
        return None
    # Realized vol plus the haircut stands in for implied vol when choosing a
    # strike, since IV is what a real chain's delta would have been based on.
    sigma = apply_vol_haircut(rv, cfg.backtest.vol_haircut_points)

    tenor = year_fraction((expiry - entry_date).days)
    target = (cfg.options.delta_min + cfg.options.delta_max) / 2.0
    raw_strike = strike_for_delta(
        target, spot, tenor, cfg.backtest.risk_free_rate, sigma,
        Right.CALL if right is Right.CALL else Right.PUT,
    )
    if right is Right.PUT:
        # strike_for_delta inverts the call formula; mirror it for puts so the
        # magnitude of the put's delta lands in the same band.
        raw_strike = strike_for_delta(
            1.0 - target, spot, tenor, cfg.backtest.risk_free_rate, sigma, Right.CALL
        )

    strike = _round_strike(raw_strike)
    if strike <= 0:
        return None

    delta = bs_delta(spot, strike, tenor, cfg.backtest.risk_free_rate, sigma, right)
    if abs(delta) < cfg.options.delta_min or abs(delta) > cfg.options.delta_max:
        return None  # rounding pushed it out of band — skip rather than drift

    try:
        occ = build_occ_symbol(ticker, expiry, right, strike)
    except ValueError:
        return None
    return occ, strike, expiry, delta, sigma


def run_backtest(
    prices: pd.DataFrame,
    cfg: Config,
    price_source: OptionPriceSource,
    *,
    start: date,
    end: date,
    benchmark: str | None = None,
) -> BacktestResult:
    """Simulate the strategy over ``[start, end]``."""
    result = BacktestResult(start=start, end=end)
    benchmark = benchmark or cfg.universe.benchmark

    try:
        bench_closes = price_data.close_series(prices, benchmark)
    except KeyError:
        result.warnings.append(
            f"benchmark {benchmark} missing from price data — relative strength disabled"
        )
        bench_closes = pd.Series(dtype=float)

    sessions = trading_days(start, end)
    open_positions: list[_OpenPosition] = []
    premium_cap = effective_premium_cap(cfg.account, cfg.options)

    reconstruction = universe_mod.constituents_as_of(start)
    result.constituent_reconstruction = (
        "reconstructed" if reconstruction else "unavailable (today's membership used)"
    )
    if not reconstruction:
        result.warnings.append(
            "Index membership could not be reconstructed for historical dates, so today's "
            "constituents were used throughout. This is survivorship bias and it flatters "
            "results — names that left the index are missing from the sample."
        )

    for session in sessions:
        # 1. Close anything whose hold period has elapsed.
        still_open: list[_OpenPosition] = []
        for position in open_positions:
            if session >= position.exit_on:
                trade = _close_position(position, session, prices, cfg, price_source, result)
                if trade is not None:
                    result.trades.append(trade)
            else:
                still_open.append(position)
        open_positions = still_open

        free = cfg.account.max_concurrent_positions - len(open_positions)
        if free <= 0:
            continue

        # 2. Score the universe using only data available on this session.
        features = compute_features(prices, bench_closes, cfg.signals, as_of=session)
        if features.empty:
            continue
        result.signal_days += 1

        ranked = score_universe(features, cfg.signals, session)
        candidates = label_candidates(ranked, cfg.signals)
        for candidate in candidates:
            if not candidate.actionable:
                result.add_skip("not_actionable")

        plan = allocate(
            candidates,
            cfg.account,
            open_positions=len(open_positions),
            open_underlyings={p.trade_stub["ticker"] for p in open_positions},
        )

        # 3. Open positions, priced at the NEXT session's open.
        for candidate in plan.selected:
            try:
                entry_date = add_trading_days(session, 1)
            except ValueError:
                continue
            if entry_date > end:
                continue

            position = _open_position(
                candidate.ticker,
                candidate.label,
                candidate.composite,
                session,
                entry_date,
                prices,
                cfg,
                price_source,
                premium_cap,
                result,
            )
            if position is not None:
                open_positions.append(position)

    if isinstance(price_source, ChainedPriceSource):
        result.price_source_counts = dict(price_source.counts)

    return result


def _open_position(
    ticker: str,
    label: Label,
    composite: float,
    signal_date: date,
    entry_date: date,
    prices: pd.DataFrame,
    cfg: Config,
    price_source: OptionPriceSource,
    premium_cap: float,
    result: BacktestResult,
) -> _OpenPosition | None:
    right = label.right
    if right is None:
        return None

    try:
        closes = price_data.close_series(prices, ticker)
    except KeyError:
        result.add_skip("no_price_data")
        return None

    spot = price_data.spot_on(prices, ticker, signal_date)
    if not spot:
        result.add_skip("no_price_data")
        return None

    synthesized = _synthesize_contract(ticker, right, signal_date, entry_date, spot, closes, cfg)
    if synthesized is None:
        result.add_skip("no_contract_in_delta_band")
        return None
    occ, strike, expiry, delta, sigma = synthesized

    entry_spot = price_data.spot_on(prices, ticker, entry_date)
    if not entry_spot:
        result.add_skip("no_price_data")
        return None

    point = price_source.price_on(
        occ, ticker, strike, expiry, right, entry_date,
        spot=entry_spot, closes=closes, is_entry=True,
    )
    if point is None:
        result.add_skip("unpriced_contract")
        return None

    if point.price > premium_cap:
        result.add_skip("unaffordable")
        return None

    dte = (expiry - entry_date).days
    fill = costs.buy_fill(point.price, dte, cfg.backtest, cfg.account)

    from ajax.options.chain_source import ContractQuote

    quote = ContractQuote(
        symbol=occ, underlying=ticker, right=right, strike=strike, expiry=expiry,
        bid=fill.price, ask=fill.price, delta=delta,
    )
    sizing = size_position(quote, cfg.account)
    if not sizing.ok:
        result.add_skip("unaffordable")
        return None

    try:
        exit_on = add_trading_days(entry_date, cfg.backtest.hold_trading_days)
    except ValueError:
        return None

    return _OpenPosition(
        trade_stub={
            "ticker": ticker,
            "right": right,
            "occ": occ,
            "strike": strike,
            "expiry": expiry,
            "signal_date": signal_date,
            "entry_date": entry_date,
            "entry_fill": fill,
            "entry_source": point.source,
            "entry_underlying": entry_spot,
            "delta": delta,
            "sigma": sigma,
            "composite": composite,
            "qty": sizing.qty,
        },
        exit_on=exit_on,
    )


def _close_position(
    position: _OpenPosition,
    session: date,
    prices: pd.DataFrame,
    cfg: Config,
    price_source: OptionPriceSource,
    result: BacktestResult,
) -> BacktestTrade | None:
    stub = position.trade_stub
    ticker = stub["ticker"]

    try:
        closes = price_data.close_series(prices, ticker)
    except KeyError:
        return None

    exit_spot = price_data.spot_on(prices, ticker, session)
    expiry: date = stub["expiry"]
    dte = (expiry - session).days

    point = None
    if exit_spot:
        point = price_source.price_on(
            stub["occ"], ticker, stub["strike"], expiry, stub["right"], session,
            spot=exit_spot, closes=closes, is_entry=False,
        )

    if point is None:
        result.add_skip("unpriced_exit")
        return None

    exit_fill = costs.sell_fill(point.price, dte, cfg.backtest, cfg.account)
    entry_fill = stub["entry_fill"]
    pnl, pnl_pct = costs.round_trip_pnl(entry_fill, exit_fill, stub["qty"])

    return BacktestTrade(
        ticker=ticker,
        right=stub["right"],
        occ_symbol=stub["occ"],
        strike=stub["strike"],
        expiry=expiry,
        signal_date=stub["signal_date"],
        entry_date=stub["entry_date"],
        exit_date=session,
        dte_at_entry=(expiry - stub["entry_date"]).days,
        qty=stub["qty"],
        entry_price=entry_fill.price,
        exit_price=exit_fill.price,
        entry_source=stub["entry_source"],
        exit_source=point.source,
        entry_underlying=stub["entry_underlying"],
        exit_underlying=exit_spot,
        delta_at_entry=stub["delta"],
        sigma_at_entry=stub["sigma"],
        composite=stub["composite"],
        pnl=pnl,
        pnl_pct=pnl_pct,
        commission=(entry_fill.commission + exit_fill.commission) * stub["qty"],
    )


def trades_to_frame(trades: list[BacktestTrade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    rows = []
    for t in trades:
        rows.append(
            {
                "ticker": t.ticker,
                "right": t.right.value,
                "occ_symbol": t.occ_symbol,
                "strike": t.strike,
                "expiry": t.expiry,
                "signal_date": t.signal_date,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "dte_at_entry": t.dte_at_entry,
                "qty": t.qty,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "entry_source": t.entry_source,
                "exit_source": t.exit_source,
                "delta_at_entry": t.delta_at_entry,
                "composite": t.composite,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "commission": t.commission,
                "win": t.is_win,
            }
        )
    return pd.DataFrame(rows)


def default_window(cfg: Config) -> tuple[date, date]:
    end = date.today()
    start = to_date(pd.Timestamp(end) - pd.DateOffset(months=cfg.backtest.months))
    return start, end
