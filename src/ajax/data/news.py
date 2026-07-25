"""Ticker news for open positions.

Live/paper only. yfinance returns only recent articles, so news for a date six
months ago is not retrievable at any free price — the backtest therefore does not
use news at all and says so in its reports rather than implying otherwise.

yfinance's news schema has changed across versions (a flat ``{title, link,
publisher}`` shape in older releases, a nested ``{'id', 'content': {...}}`` shape
in newer ones) and is not contractually stable. Every field access here is
defensive; an unparseable item is logged and skipped rather than raising, because
losing a headline should never take down a trading run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ajax.data import symbols as sym
from ajax.utils.ratelimit import YAHOO_BUCKET, throttled

log = logging.getLogger(__name__)


@dataclass
class NewsItem:
    ticker: str
    title: str | None = None
    url: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    raw_keys: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.title)

    def one_line(self) -> str:
        when = f" ({self.published_at[:10]})" if self.published_at else ""
        who = f" — {self.publisher}" if self.publisher else ""
        return f"{self.title}{who}{when}"


def _first(payload: dict[str, Any], *paths: str) -> Any | None:
    """First present value among dotted paths, e.g. ``"canonicalUrl.url"``."""
    for path in paths:
        cursor: Any = payload
        for part in path.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                cursor = None
                break
            cursor = cursor[part]
        if cursor not in (None, "", []):
            return cursor
    return None


def _coerce_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(float(value)).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def parse_news_item(item: Any, ticker: str) -> NewsItem | None:
    """Normalize one provider item across known schema shapes."""
    if not isinstance(item, dict):
        return None

    # Newer yfinance nests the payload under "content"; older versions are flat.
    body = item.get("content") if isinstance(item.get("content"), dict) else item

    title = _first(body, "title", "headline", "summary")
    url = _first(
        body,
        "canonicalUrl.url",
        "clickThroughUrl.url",
        "link",
        "url",
        "previewUrl",
    )
    publisher = _first(body, "provider.displayName", "publisher", "provider")
    published = _first(body, "pubDate", "displayTime", "providerPublishTime", "published_at")

    if title is None:
        log.debug("skipping unparseable news item for %s (keys: %s)", ticker, list(body)[:8])
        return None

    return NewsItem(
        ticker=ticker,
        title=str(title),
        url=str(url) if url else None,
        publisher=str(publisher) if publisher else None,
        published_at=_coerce_timestamp(published),
        raw_keys=sorted(body)[:12],
    )


@throttled(YAHOO_BUCKET)
def _fetch_raw(ticker: str) -> list[Any]:
    import yfinance as yf

    return list(yf.Ticker(sym.to_yahoo(ticker)).news or [])


def fetch_news(ticker: str, limit: int = 5) -> list[NewsItem]:
    """Recent headlines for ``ticker``. Returns ``[]`` on any provider failure."""
    try:
        raw = _fetch_raw(ticker)
    except Exception as exc:  # noqa: BLE001 - news must never break a trading run
        log.warning("news fetch failed for %s: %s", ticker, exc)
        return []

    items: list[NewsItem] = []
    for entry in raw:
        parsed = parse_news_item(entry, sym.normalize(ticker))
        if parsed and parsed.usable:
            items.append(parsed)
        if len(items) >= limit:
            break
    return items


def fetch_news_many(tickers: list[str], limit: int = 5) -> dict[str, list[NewsItem]]:
    return {sym.normalize(t): fetch_news(t, limit=limit) for t in tickers}
