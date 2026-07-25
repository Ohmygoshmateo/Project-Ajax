"""yfinance option chains — degraded fallback only.

Used when Alpaca chain data is unavailable. Deliberately capped at a handful of
tickers: option chains cannot be batched, and per-ticker chain calls across a
large list are exactly the access pattern Yahoo throttles.

yfinance publishes implied volatility but never delta, so every quote from here
is stamped ``bs_from_yf_iv`` after modelling.
"""

from __future__ import annotations

import logging
from datetime import date

from ajax.config import Config
from ajax.data import symbols as sym
from ajax.options.chain_source import ChainSource, ContractQuote
from ajax.options.greeks import Right, bs_delta, year_fraction
from ajax.utils.ratelimit import YAHOO_BUCKET, throttled

log = logging.getLogger(__name__)

# Hard ceiling on how many underlyings may be pulled from this source in one run.
MAX_TICKERS_PER_RUN = 8


class YahooChainBudgetExceeded(RuntimeError):
    """The fallback was asked for more chains than it is allowed to fetch."""


@throttled(YAHOO_BUCKET)
def _fetch_expiries(ticker: str) -> tuple[str, ...]:
    import yfinance as yf

    return tuple(yf.Ticker(sym.to_yahoo(ticker)).options or ())


@throttled(YAHOO_BUCKET)
def _fetch_chain_for_expiry(ticker: str, expiry: str):  # noqa: ANN202 - vendor namedtuple
    import yfinance as yf

    return yf.Ticker(sym.to_yahoo(ticker)).option_chain(expiry)


def fetch_chain(
    underlying: str,
    cfg: Config,
    right: Right,
    *,
    as_of: date | None = None,
    spot: float | None = None,
) -> list[ContractQuote]:
    """Chain for one underlying across expirations inside the DTE window."""
    as_of = as_of or date.today()
    opts = cfg.options

    try:
        expiries = _fetch_expiries(underlying)
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance expiry lookup failed for %s: %s", underlying, exc)
        return []

    wanted: list[str] = []
    for raw in expiries:
        try:
            expiry = date.fromisoformat(raw)
        except ValueError:
            continue
        dte = (expiry - as_of).days
        if opts.dte_hard_floor <= dte <= max(opts.dte_target_max * 2, 90):
            wanted.append(raw)

    quotes: list[ContractQuote] = []
    for raw in wanted:
        try:
            chain = _fetch_chain_for_expiry(underlying, raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance chain failed for %s %s: %s", underlying, raw, exc)
            continue

        table = chain.calls if right is Right.CALL else chain.puts
        expiry = date.fromisoformat(raw)
        for row in table.to_dict("records"):
            quote = _row_to_quote(row, underlying, right, expiry)
            if quote is not None:
                quotes.append(quote)

    if spot:
        quotes = _model_deltas(quotes, spot, as_of, cfg)
    return quotes


def _row_to_quote(
    row: dict, underlying: str, right: Right, expiry: date
) -> ContractQuote | None:
    try:
        strike = float(row["strike"])
    except (KeyError, TypeError, ValueError):
        return None

    return ContractQuote(
        symbol=str(row.get("contractSymbol") or f"{underlying}{expiry:%y%m%d}{right.value}{strike}"),
        underlying=sym.normalize(underlying),
        right=right,
        strike=strike,
        expiry=expiry,
        bid=_num(row.get("bid")),
        ask=_num(row.get("ask")),
        last=_num(row.get("lastPrice")),
        open_interest=_int(row.get("openInterest")),
        volume=_int(row.get("volume")),
        iv=_num(row.get("impliedVolatility")),
        delta=None,  # yfinance never publishes delta
        greeks_source="missing",
    )


def _model_deltas(
    quotes: list[ContractQuote], spot: float, as_of: date, cfg: Config
) -> list[ContractQuote]:
    import dataclasses

    out: list[ContractQuote] = []
    for quote in quotes:
        tenor = year_fraction(quote.dte(as_of))
        if not quote.iv or quote.iv <= 0 or tenor <= 0:
            out.append(quote)
            continue
        delta = bs_delta(spot, quote.strike, tenor, cfg.backtest.risk_free_rate, quote.iv,
                         quote.right)
        out.append(dataclasses.replace(quote, delta=delta, greeks_source="bs_from_yf_iv"))
    return out


def _num(value) -> float | None:  # noqa: ANN001
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out > 0 else None


def _int(value) -> int | None:  # noqa: ANN001
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class YahooChainSource(ChainSource):
    """Fallback chain source with a hard per-run budget."""

    def __init__(self, cfg: Config, spot_lookup=None) -> None:  # noqa: ANN001
        self.cfg = cfg
        self._spot_lookup = spot_lookup
        self._fetched: set[str] = set()

    def get_chain(self, underlying: str, as_of: date, right: Right) -> list[ContractQuote]:
        key = sym.normalize(underlying)
        if key not in self._fetched and len(self._fetched) >= MAX_TICKERS_PER_RUN:
            raise YahooChainBudgetExceeded(
                f"yfinance chain fallback is capped at {MAX_TICKERS_PER_RUN} underlyings per run; "
                "shortlist further or configure Alpaca market data"
            )
        self._fetched.add(key)
        spot = self._spot_lookup(underlying, as_of) if self._spot_lookup else None
        return fetch_chain(underlying, self.cfg, right, as_of=as_of, spot=spot)
