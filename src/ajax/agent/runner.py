"""The scheduled agent: one scan → allocate → order → log → news cycle.

Stateless by design — every run reconciles against the broker and the local log
rather than trusting in-process state, so an external scheduler can invoke it and
a missed run is self-healing.

**This module imports ``get_paper_trading_client`` directly and never branches on
a trading-mode flag.** There is no code path from here to a live endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ajax.broker import orders
from ajax.broker.alpaca_client import get_paper_trading_client
from ajax.config import Config
from ajax.data import news as news_mod
from ajax.data import prices as price_data
from ajax.data import universe as universe_mod
from ajax.options.chain_source import ContractQuote
from ajax.options.selector import select_contract
from ajax.portfolio.allocator import allocate
from ajax.portfolio.sizing import effective_premium_cap, size_position
from ajax.signals.labels import Candidate, label_candidates
from ajax.signals.scoring import compute_features, score_universe
from ajax.utils.calendar import add_trading_days, to_date, trading_days_between

log = logging.getLogger(__name__)


@dataclass
class ScanOutcome:
    as_of: date
    candidates: list[Candidate] = field(default_factory=list)
    selections: list[tuple[Candidate, ContractQuote]] = field(default_factory=list)
    skips: list[tuple[Candidate, str, str]] = field(default_factory=list)
    shortlist: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RunSummary:
    as_of: date
    opened: int = 0
    closed: int = 0
    skipped: int = 0
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)


def load_prices_for_scan(cfg: Config, tickers: list[str] | None = None) -> pd.DataFrame:
    """Batched price history covering the indicator warmup period."""
    if tickers is None:
        tickers = universe_mod.load(cfg.universe).tickers
    benchmark = cfg.universe.benchmark
    if benchmark not in tickers:
        tickers = [*tickers, benchmark]

    start, end = price_data.history_window(cfg, months=3, warmup_days=cfg.signals.warmup_days)
    return price_data.fetch_history(tickers, cfg, start=start, end=end)


def scan(
    cfg: Config,
    prices: pd.DataFrame,
    *,
    as_of: date | None = None,
    chain_source=None,  # noqa: ANN001
    equity: float | None = None,
    slots: int | None = None,
) -> ScanOutcome:
    """Rank the universe, shortlist, and select contracts.

    Stage 1 (ranking) touches only the batched price frame — zero chain calls.
    Stage 2 fetches chains for the shortlist only. Option chains cannot be
    batched, so pulling one per S&P 500 name would be several hundred sequential
    requests and would get throttled long before it finished.
    """
    as_of = as_of or date.today()
    outcome = ScanOutcome(as_of=as_of)

    try:
        bench_closes = price_data.close_series(prices, cfg.universe.benchmark)
    except KeyError:
        bench_closes = pd.Series(dtype=float)
        outcome.warnings.append(
            f"benchmark {cfg.universe.benchmark} missing — relative strength disabled"
        )

    features = compute_features(prices, bench_closes, cfg.signals, as_of=as_of)
    if features.empty:
        outcome.warnings.append("no ticker had enough history to score")
        return outcome

    ranked = score_universe(features, cfg.signals, as_of)
    outcome.candidates = label_candidates(ranked, cfg.signals)

    for candidate in outcome.candidates:
        if not candidate.actionable:
            outcome.skips.append((candidate, "not_actionable", candidate.reason))

    if chain_source is None:
        return outcome

    plan = allocate(
        [c for c in outcome.candidates if c.actionable],
        cfg.account,
        open_positions=0 if slots is None else cfg.account.max_concurrent_positions - slots,
    )
    shortlist = plan.selected[: cfg.options.shortlist_size]
    outcome.shortlist = [c.ticker for c in shortlist]
    premium_cap = effective_premium_cap(cfg.account, cfg.options, equity)

    for candidate in shortlist:
        right = candidate.label.right
        if right is None:
            continue
        try:
            quotes = chain_source.get_chain(candidate.ticker, as_of, right)
        except Exception as exc:  # noqa: BLE001 - one bad chain must not kill the run
            outcome.skips.append((candidate, "chain_unavailable", str(exc)))
            continue

        result = select_contract(quotes, as_of, cfg.options, max_premium=premium_cap)
        if result.selected:
            outcome.selections.append((candidate, result.contract))
        else:
            outcome.skips.append((candidate, result.skip_reason.value, result.detail))

    return outcome


def run_once(
    cfg: Config,
    trade_log,  # noqa: ANN001
    *,
    as_of: date | None = None,
    dry_run: bool = False,
    client=None,  # noqa: ANN001
    chain_source=None,  # noqa: ANN001
) -> RunSummary:
    """One complete agent cycle against the PAPER account."""
    as_of = as_of or date.today()
    summary = RunSummary(as_of=as_of, dry_run=dry_run)
    run_id = trade_log.start_run(as_of.isoformat(), mode="paper")

    client = client or (None if dry_run else get_paper_trading_client())

    equity = cfg.account.equity
    if client is not None:
        try:
            from ajax.broker.alpaca_client import account_snapshot

            snapshot = account_snapshot(client)
            equity = snapshot.equity or equity
            if snapshot.options_level is not None and snapshot.options_level < 2:
                summary.notes.append(
                    f"account options level is {snapshot.options_level}; long calls/puts need "
                    f"level 2 (paper accounts normally get level 3 automatically)"
                )
        except Exception as exc:  # noqa: BLE001
            summary.notes.append(f"could not read account: {exc}")

    # 1. Close positions that have reached the hold period.
    summary.closed = _close_due_positions(cfg, trade_log, client, as_of, dry_run, summary)

    # 2. Free slots?
    open_trades = trade_log.open_trades()
    free = cfg.account.max_concurrent_positions - len(open_trades)
    if free <= 0:
        summary.notes.append(
            f"all {cfg.account.max_concurrent_positions} position slots are occupied"
        )
        trade_log.finish_run(run_id, opened=0, closed=summary.closed, skipped=0,
                             notes="; ".join(summary.notes))
        _log_news_for_open(cfg, trade_log, open_trades, as_of)
        return summary

    # 3. Scan and select.
    prices = load_prices_for_scan(cfg)
    outcome = scan(cfg, prices, as_of=as_of, chain_source=chain_source, equity=equity, slots=free)
    summary.notes.extend(outcome.warnings)

    for candidate, reason, detail in outcome.skips:
        trade_log.record_skip(
            as_of=as_of.isoformat(),
            ticker=candidate.ticker,
            direction=candidate.label.right.value if candidate.label.right else None,
            skip_reason=reason,
            detail=detail,
            composite=candidate.composite,
            run_id=run_id,
        )
    summary.skipped = len(outcome.skips)

    # 4. Open positions.
    held = {t["ticker"] for t in open_trades}
    for candidate, contract in outcome.selections:
        if free <= 0:
            break
        if candidate.ticker in held:
            continue
        if _open_position(cfg, trade_log, client, candidate, contract, as_of, dry_run, summary):
            summary.opened += 1
            free -= 1
            held.add(candidate.ticker)

    # 5. News for anything now open.
    _log_news_for_open(cfg, trade_log, trade_log.open_trades(), as_of)

    trade_log.finish_run(
        run_id,
        opened=summary.opened,
        closed=summary.closed,
        skipped=summary.skipped,
        notes="; ".join(summary.notes),
    )
    return summary


def _close_due_positions(
    cfg: Config,
    trade_log,  # noqa: ANN001
    client,  # noqa: ANN001
    as_of: date,
    dry_run: bool,
    summary: RunSummary,
) -> int:
    closed = 0
    broker_positions = {}
    if client is not None:
        broker_positions = {p.symbol: p for p in orders.list_option_positions(client)}

    for trade in trade_log.open_trades():
        entry = to_date(trade["entry_date"])
        held_days = trading_days_between(entry, as_of)
        if held_days < cfg.backtest.hold_trading_days:
            continue

        symbol = trade["contract_symbol"]
        position = broker_positions.get(symbol)
        exit_premium = None
        order_id = None

        if position is not None and position.market_value is not None and position.qty:
            exit_premium = abs(position.market_value) / (abs(position.qty) * 100.0)

        if not dry_run and client is not None:
            result = orders.submit_sell_to_close(client, symbol, int(trade["qty"] or 1))
            if not result.ok:
                summary.notes.append(f"could not close {symbol}: {result.error}")
                continue
            order_id = result.order_id

        if exit_premium is None:
            summary.notes.append(
                f"closed {symbol} but no market value was available; exit premium recorded as 0"
            )
            exit_premium = 0.0

        trade_log.record_exit(
            int(trade["id"]),
            exit_date=as_of.isoformat(),
            exit_premium=float(exit_premium),
            order_id_exit=order_id,
            commission=cfg.account.commission_per_contract * int(trade["qty"] or 1),
        )
        closed += 1

    return closed


def _open_position(
    cfg: Config,
    trade_log,  # noqa: ANN001
    client,  # noqa: ANN001
    candidate: Candidate,
    contract: ContractQuote,
    as_of: date,
    dry_run: bool,
    summary: RunSummary,
) -> bool:
    sizing = size_position(contract, cfg.account)
    if not sizing.ok:
        trade_log.record_skip(
            as_of=as_of.isoformat(),
            ticker=candidate.ticker,
            direction=contract.right.value,
            skip_reason="unaffordable",
            detail=sizing.reason,
            composite=candidate.composite,
        )
        return False

    order_id = None
    if not dry_run and client is not None:
        result = orders.submit_buy_to_open(
            client, contract.symbol, sizing.qty, limit_price=contract.ask or contract.mid
        )
        if not result.ok:
            summary.notes.append(f"order rejected for {contract.symbol}: {result.error}")
            return False
        order_id = result.order_id

    from ajax.agent.trade_log import TradeRecord

    try:
        planned_exit = add_trading_days(as_of, cfg.backtest.hold_trading_days).isoformat()
    except ValueError:
        planned_exit = None

    trade_log.record_entry(
        TradeRecord(
            ticker=candidate.ticker,
            direction=contract.right.value,
            contract_symbol=contract.symbol,
            entry_date=as_of.isoformat(),
            strike=contract.strike,
            expiry=contract.expiry.isoformat(),
            dte_at_entry=contract.dte(as_of),
            entry_underlying=candidate.close,
            entry_premium=contract.mid,
            delta_at_entry=contract.delta,
            iv_at_entry=contract.iv,
            greeks_source=contract.greeks_source,
            qty=sizing.qty,
            risk_dollars=sizing.risk_dollars,
            commission=sizing.commission,
            order_id_entry=order_id,
            planned_exit_date=planned_exit,
            mode="paper",
        )
    )
    return True


def _log_news_for_open(cfg: Config, trade_log, open_trades: list[dict], as_of: date) -> None:  # noqa: ANN001
    """Fetch headlines for tickers currently held. Never fatal."""
    for trade in open_trades:
        try:
            items = news_mod.fetch_news(trade["ticker"], limit=5)
        except Exception as exc:  # noqa: BLE001
            log.warning("news fetch failed for %s: %s", trade["ticker"], exc)
            continue
        if items:
            trade_log.record_news(
                trade["ticker"], as_of.isoformat(), items, trade_id=int(trade["id"])
            )


def build_chain_source(cfg: Config, prices: pd.DataFrame):  # noqa: ANN001
    """Alpaca chains, with a spot lookup so missing greeks can be modelled."""
    from ajax.data.alpaca_options import AlpacaChainSource

    def spot_lookup(ticker: str, on: date) -> float | None:
        return price_data.spot_on(prices, ticker, on)

    return AlpacaChainSource(cfg).with_spot_lookup(spot_lookup)


__all__ = [
    "RunSummary",
    "ScanOutcome",
    "build_chain_source",
    "load_prices_for_scan",
    "run_once",
    "scan",
]


# Guard against the single most dangerous refactor mistake in this codebase:
# swapping the paper client factory for a mode-dependent one. If this assertion
# ever fails, automation has gained a route to real money.
assert "get_paper_trading_client" in globals(), (
    "runner must import get_paper_trading_client directly; a mode-dependent client "
    "factory would give the scheduler a path to live trading"
)
