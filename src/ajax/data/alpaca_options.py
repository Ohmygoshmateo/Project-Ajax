"""Alpaca option chains, bars, and contract discovery.

Alpaca's chain snapshots carry ``implied_volatility`` and ``greeks`` per
contract, which is why this — not a hand-rolled Black-Scholes pass over a
yfinance chain — is the primary live source.

One caveat drives the whole design: the free Basic plan serves the *indicative*
feed rather than OPRA, and whether greeks are populated there is not documented.
Instead of assuming either way, every quote records where its delta came from via
``greeks_source``, and :func:`backfill_greeks` models the missing ones. ``ajax
doctor`` measures which path is actually live on the user's account.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd

from ajax.broker.contracts import parse_occ_symbol
from ajax.config import Config
from ajax.data import symbols as sym
from ajax.options.chain_source import ChainSource, ContractQuote
from ajax.options.greeks import Right, bs_delta, year_fraction
from ajax.utils.cache import DiskCache
from ajax.utils.ratelimit import ALPACA_BUCKET, throttled

log = logging.getLogger(__name__)


class AlpacaDataUnavailable(RuntimeError):
    """Alpaca could not serve the requested option data."""


def _data_client(cfg: Config):
    from alpaca.data.historical.option import OptionHistoricalDataClient

    from ajax.config import load_paper_credentials

    creds = load_paper_credentials()
    if not creds.has_paper:
        raise AlpacaDataUnavailable(
            "ALPACA_PAPER_API_KEY / ALPACA_PAPER_SECRET_KEY are not set — copy "
            ".env.example to .env and fill them in"
        )
    return OptionHistoricalDataClient(creds.paper_key, creds.paper_secret)


def _to_right(value: Right) -> object:
    from alpaca.trading.enums import ContractType

    return ContractType.CALL if value is Right.CALL else ContractType.PUT


@throttled(ALPACA_BUCKET)
def _request_chain(client, request):  # noqa: ANN001 - vendor types
    return client.get_option_chain(request)


def fetch_chain(
    underlying: str,
    cfg: Config,
    right: Right,
    *,
    as_of: date | None = None,
    client=None,  # noqa: ANN001
) -> list[ContractQuote]:
    """Live chain for one underlying, filtered to the configured DTE window.

    The chain endpoint is a live snapshot — it has no historical lookup — so
    ``as_of`` only bounds which expirations are requested.
    """
    from alpaca.data.requests import OptionChainRequest

    as_of = as_of or date.today()
    client = client or _data_client(cfg)
    opts = cfg.options

    request = OptionChainRequest(
        underlying_symbol=sym.to_alpaca(underlying),
        type=_to_right(right),
        expiration_date_gte=as_of + timedelta(days=opts.dte_hard_floor),
        expiration_date_lte=as_of + timedelta(days=max(opts.dte_target_max * 2, 90)),
    )

    try:
        snapshots = _request_chain(client, request) or {}
    except Exception as exc:  # noqa: BLE001 - surfaced as a domain error
        raise AlpacaDataUnavailable(f"chain request failed for {underlying}: {exc}") from exc

    quotes: list[ContractQuote] = []
    for occ, snap in snapshots.items():
        quote = _snapshot_to_quote(occ, snap, underlying)
        if quote is not None and quote.right is right:
            quotes.append(quote)

    log.info("chain %s %s: %d contracts", underlying, right.value, len(quotes))
    return quotes


def _snapshot_to_quote(occ: str, snap, underlying: str) -> ContractQuote | None:  # noqa: ANN001
    parsed = parse_occ_symbol(occ)
    if parsed is None:
        log.debug("unparseable OCC symbol from provider: %s", occ)
        return None

    quote = getattr(snap, "latest_quote", None)
    trade = getattr(snap, "latest_trade", None)
    greeks = getattr(snap, "greeks", None)
    delta = getattr(greeks, "delta", None) if greeks is not None else None
    iv = getattr(snap, "implied_volatility", None)

    return ContractQuote(
        symbol=occ,
        underlying=sym.normalize(underlying),
        right=parsed.right,
        strike=parsed.strike,
        expiry=parsed.expiry,
        bid=_num(getattr(quote, "bid_price", None)),
        ask=_num(getattr(quote, "ask_price", None)),
        last=_num(getattr(trade, "price", None)),
        open_interest=None,  # not published on the chain snapshot
        volume=None,
        iv=_num(iv),
        delta=_num(delta),
        greeks_source="alpaca" if delta is not None else "missing",
    )


def _num(value) -> float | None:  # noqa: ANN001
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # filter NaN


def backfill_greeks(
    quotes: list[ContractQuote],
    spot: float,
    as_of: date,
    cfg: Config,
    *,
    fallback_sigma: float | None = None,
) -> list[ContractQuote]:
    """Model any missing deltas rather than dropping those contracts.

    Uses each contract's own implied volatility when the feed published one, and
    a supplied realized-vol estimate otherwise. The resulting quote is stamped
    ``bs_from_alpaca_iv`` or ``bs_from_realized_vol`` so the weaker provenance
    stays visible all the way into the trade log.
    """
    import dataclasses

    rate = cfg.backtest.risk_free_rate
    out: list[ContractQuote] = []

    for quote in quotes:
        if quote.delta is not None:
            out.append(quote)
            continue

        sigma = quote.iv if quote.iv and quote.iv > 0 else fallback_sigma
        if not sigma or sigma <= 0:
            out.append(quote)  # selector will skip it via MISSING_DELTA
            continue

        tenor = year_fraction(quote.dte(as_of))
        if tenor <= 0:
            out.append(quote)
            continue

        delta = bs_delta(spot, quote.strike, tenor, rate, sigma, quote.right)
        source = "bs_from_alpaca_iv" if quote.iv else "bs_from_realized_vol"
        out.append(dataclasses.replace(quote, delta=delta, greeks_source=source))

    return out


@throttled(ALPACA_BUCKET)
def _request_bars(client, request):  # noqa: ANN001
    return client.get_option_bars(request)


def fetch_option_bars(
    occ_symbols: list[str],
    cfg: Config,
    start: date,
    end: date,
    *,
    client=None,  # noqa: ANN001
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Historical daily bars for specific contracts, keyed by OCC symbol.

    This is what makes a trustworthy backtest possible: real traded option
    prices instead of model reconstruction. Symbols with no data are simply
    absent from the result.
    """
    from alpaca.data.requests import OptionBarsRequest
    from alpaca.data.timeframe import TimeFrame

    if not occ_symbols:
        return {}

    cache = DiskCache(cfg.paths.resolve("data_cache"), "option_bars")
    key = f"{start}:{end}:{hash(tuple(sorted(occ_symbols)))}"
    if use_cache:
        cached = cache.get_frame(key, max_age=timedelta(days=7))
        if cached is not None and not cached.empty:
            return {s: g.drop(columns="symbol") for s, g in cached.groupby("symbol")}

    client = client or _data_client(cfg)
    request = OptionBarsRequest(
        symbol_or_symbols=list(occ_symbols),
        start=datetime.combine(start, datetime.min.time()),
        end=datetime.combine(end, datetime.max.time()),
        timeframe=TimeFrame.Day,
    )

    try:
        response = _request_bars(client, request)
    except Exception as exc:  # noqa: BLE001
        log.warning("option bars request failed (%d symbols): %s", len(occ_symbols), exc)
        return {}

    frame = getattr(response, "df", None)
    if frame is None or frame.empty:
        return {}

    frame = frame.reset_index()
    if "symbol" not in frame.columns:
        return {}

    if use_cache:
        cache.put_frame(key, frame)
    return {s: g.drop(columns="symbol") for s, g in frame.groupby("symbol")}


@throttled(ALPACA_BUCKET)
def list_contracts(
    underlying: str,
    cfg: Config,
    expiry_from: date,
    expiry_to: date,
    right: Right,
    *,
    include_expired: bool = False,
    trading_client=None,  # noqa: ANN001
) -> list[str]:
    """Discover OCC symbols for an underlying via the reference-data endpoint."""
    from alpaca.trading.requests import GetOptionContractsRequest

    if trading_client is None:
        from ajax.broker.alpaca_client import get_paper_trading_client

        trading_client = get_paper_trading_client()

    request = GetOptionContractsRequest(
        underlying_symbols=[sym.to_alpaca(underlying)],
        expiration_date_gte=expiry_from,
        expiration_date_lte=expiry_to,
        type=_to_right(right),
        limit=10_000,
    )
    try:
        response = trading_client.get_option_contracts(request)
    except Exception as exc:  # noqa: BLE001
        log.warning("contract discovery failed for %s: %s", underlying, exc)
        return []

    contracts = getattr(response, "option_contracts", None) or []
    return [c.symbol for c in contracts]


class AlpacaChainSource(ChainSource):
    """Live chain source backed by Alpaca, with modelled-greek backfill."""

    def __init__(self, cfg: Config, client=None) -> None:  # noqa: ANN001
        self.cfg = cfg
        self._client = client
        self._spot_lookup = None

    def with_spot_lookup(self, fn) -> AlpacaChainSource:  # noqa: ANN001
        """Supply ``(ticker, date) -> spot`` so greeks can be backfilled."""
        self._spot_lookup = fn
        return self

    def get_chain(self, underlying: str, as_of: date, right: Right) -> list[ContractQuote]:
        client = self._client or _data_client(self.cfg)
        quotes = fetch_chain(underlying, self.cfg, right, as_of=as_of, client=client)
        if self._spot_lookup is not None and any(q.delta is None for q in quotes):
            spot = self._spot_lookup(underlying, as_of)
            if spot:
                quotes = backfill_greeks(quotes, spot, as_of, self.cfg)
        return quotes
