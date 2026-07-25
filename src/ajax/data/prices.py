"""Batched equity price history.

One ``yf.download()`` call covers the whole universe. Looping ``Ticker.history()``
over ~500 names is the exact access pattern Yahoo throttles, and a partial
universe would silently corrupt the cross-sectional ranking — every score here is
relative to the other names in the frame, so missing half of them changes the
answer rather than merely shrinking it. A partial result is therefore an error,
not a degraded success.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from ajax.config import Config
from ajax.data import symbols as sym
from ajax.utils.cache import DiskCache
from ajax.utils.ratelimit import YAHOO_BUCKET, RateLimitExceeded, throttled

log = logging.getLogger(__name__)

REQUIRED_FIELDS = ("Close", "Volume")


class PriceDataUnavailable(RuntimeError):
    """Price history could not be obtained for enough of the universe."""


@throttled(YAHOO_BUCKET)
def _download(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        tickers=tickers,
        start=start,
        end=end + timedelta(days=1),
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=True,
    )


def _normalize(frame: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Force a ``(ticker, field)`` MultiIndex regardless of yfinance's shape.

    yfinance returns flat columns for a single ticker and a MultiIndex for many,
    so this normalizes both into the same layout.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()

    if not isinstance(frame.columns, pd.MultiIndex):
        if len(tickers) != 1:
            return pd.DataFrame()
        frame = pd.concat({tickers[0]: frame}, axis=1)

    # yfinance has shipped both orderings; detect which level holds the tickers.
    level0 = set(frame.columns.get_level_values(0))
    if not (level0 & set(tickers)) and (set(frame.columns.get_level_values(1)) & set(tickers)):
        frame = frame.swaplevel(axis=1)

    frame = frame.rename(columns=sym.to_alpaca, level=0)
    return frame.sort_index(axis=1)


def fetch_history(
    tickers: list[str],
    cfg: Config,
    *,
    start: date,
    end: date | None = None,
    use_cache: bool = True,
    min_coverage: float = 0.80,
) -> pd.DataFrame:
    """Daily OHLCV for ``tickers`` as a ``(ticker, field)`` frame.

    Raises :class:`PriceDataUnavailable` if fewer than ``min_coverage`` of the
    requested tickers came back usable.
    """
    end = end or date.today()
    canonical = [sym.normalize(t) for t in tickers]
    cache = DiskCache(cfg.paths.resolve("data_cache"), "prices")
    key = f"{start}:{end}:{len(canonical)}:{hash(tuple(sorted(canonical)))}"

    if use_cache:
        cached = cache.get_frame(key, max_age=timedelta(hours=12))
        if cached is not None and not cached.empty:
            log.info("using cached price history (%d columns)", cached.shape[1])
            return cached

    yahoo_tickers = sym.to_yahoo_many(canonical)
    log.info("downloading %d tickers from %s to %s", len(yahoo_tickers), start, end)
    try:
        raw = _download(yahoo_tickers, start, end)
    except RateLimitExceeded as exc:
        raise PriceDataUnavailable(
            "Yahoo rate limiting persisted through every retry. Re-run later, or "
            "reduce the universe. Continuing with partial data would corrupt the "
            "cross-sectional ranking, so no partial result is returned."
        ) from exc

    frame = _normalize(raw, yahoo_tickers)
    if frame.empty:
        raise PriceDataUnavailable("provider returned no usable price data")

    usable = [
        t
        for t in {c[0] for c in frame.columns}
        if all((t, f) in frame.columns for f in REQUIRED_FIELDS)
        and frame[(t, "Close")].notna().any()
    ]
    coverage = len(usable) / max(len(canonical), 1)
    if coverage < min_coverage:
        raise PriceDataUnavailable(
            f"only {len(usable)}/{len(canonical)} tickers ({coverage:.0%}) returned usable "
            f"data, below the {min_coverage:.0%} floor"
        )
    if coverage < 1.0:
        log.warning("%d/%d tickers usable (%.0f%%)", len(usable), len(canonical), coverage * 100)

    frame = frame.loc[:, [c for c in frame.columns if c[0] in usable]]
    if use_cache:
        cache.put_frame(key, frame)
    return frame


def history_window(cfg: Config, months: int, warmup_days: int) -> tuple[date, date]:
    """Start/end dates covering ``months`` of history plus indicator warmup."""
    end = date.today()
    # Calendar days per trading day is ~1.45; pad generously, it only costs cache.
    start = end - timedelta(days=int(months * 31) + int(warmup_days * 1.6) + 10)
    return start, end


def close_series(frame: pd.DataFrame, ticker: str) -> pd.Series:
    return frame[(sym.normalize(ticker), "Close")].dropna()


def spot_on(frame: pd.DataFrame, ticker: str, day: date) -> float | None:
    """Last close at or before ``day`` — never after, to preserve no-lookahead."""
    try:
        series = close_series(frame, ticker)
    except KeyError:
        return None
    eligible = series[series.index.date <= day]
    return float(eligible.iloc[-1]) if len(eligible) else None
