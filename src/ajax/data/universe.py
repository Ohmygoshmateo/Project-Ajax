"""S&P 500 constituent list, with a cached last-good copy.

A scheduled unattended job must never silently trade a corrupted universe. The
Wikipedia page can 403, get vandalized, or change structure, so a refresh is only
accepted if it passes sanity checks; otherwise the cached list stands and the
failure is loud.

Refresh is never triggered implicitly by a scan — it is an explicit command.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ajax.config import UniverseConfig
from ajax.data import symbols as sym

log = logging.getLogger(__name__)

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CACHE_PATH = Path(__file__).parent / "sp500_cached.json"
CHANGES_PATH = Path(__file__).parent / "sp500_changes.csv"

# Wikipedia rejects bare urllib user agents.
_USER_AGENT = "Mozilla/5.0 (compatible; ajax-scanner/0.1; +https://github.com/)"


class UniverseRefreshRejected(RuntimeError):
    """A refresh failed its sanity checks and the cached list was kept."""


@dataclass(frozen=True)
class Universe:
    tickers: list[str]
    as_of: str
    source: str

    def __len__(self) -> int:
        return len(self.tickers)

    def yahoo(self) -> list[str]:
        return sym.to_yahoo_many(self.tickers)


def load_cached() -> Universe:
    if not CACHE_PATH.exists():
        raise FileNotFoundError(
            f"no cached universe at {CACHE_PATH}; run `ajax universe refresh` once with network access"
        )
    with open(CACHE_PATH) as fh:
        payload = json.load(fh)
    return Universe(
        tickers=[sym.normalize(t) for t in payload["tickers"]],
        as_of=payload.get("as_of", "unknown"),
        source=payload.get("source", "cache"),
    )


def _write_cache(tickers: list[str], source: str) -> Universe:
    payload = {
        "tickers": sorted(tickers),
        "as_of": date.today().isoformat(),
        "source": source,
    }
    CACHE_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    return Universe(tickers=sorted(tickers), as_of=payload["as_of"], source=source)


def fetch_from_wikipedia() -> list[str]:
    """Scrape the constituents table. Raises on any failure — callers decide."""
    import pandas as pd
    import requests

    response = requests.get(WIKIPEDIA_URL, headers={"User-Agent": _USER_AGENT}, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(response.text)
    if not tables:
        raise UniverseRefreshRejected("no tables found on the constituents page")

    table = tables[0]
    column = next((c for c in table.columns if str(c).strip().lower() == "symbol"), None)
    if column is None:
        raise UniverseRefreshRejected(f"no 'Symbol' column found; saw {list(table.columns)}")

    return [sym.normalize(str(t)) for t in table[column].dropna().tolist()]


def validate_refresh(
    candidate: list[str], cached: list[str], cfg: UniverseConfig
) -> tuple[bool, str]:
    """Would this refresh be accepted? Returns ``(ok, reason)``."""
    count = len(candidate)
    if count < cfg.min_expected_tickers:
        return False, f"only {count} tickers, below the {cfg.min_expected_tickers} floor"
    if count > cfg.max_expected_tickers:
        return False, f"{count} tickers, above the {cfg.max_expected_tickers} ceiling"

    if cached:
        churn = len(set(candidate).symmetric_difference(set(cached))) // 2
        if churn > cfg.max_churn_per_refresh:
            return False, (
                f"{churn} names changed in one refresh, above the "
                f"{cfg.max_churn_per_refresh} limit — likely a bad scrape, not a real rebalance"
            )
    return True, "ok"


def refresh(cfg: UniverseConfig) -> Universe:
    """Attempt a refresh; keep the cached list and raise if it fails validation."""
    try:
        cached = load_cached().tickers
    except FileNotFoundError:
        cached = []

    candidate = fetch_from_wikipedia()
    ok, reason = validate_refresh(candidate, cached, cfg)
    if not ok:
        raise UniverseRefreshRejected(
            f"refresh rejected ({reason}); keeping cached list of {len(cached)} tickers"
        )

    log.info("universe refresh accepted: %d tickers", len(candidate))
    return _write_cache(candidate, source="wikipedia")


def load(cfg: UniverseConfig | None = None) -> Universe:
    """The universe to trade. Always the cached list — never a live scrape."""
    return load_cached()


def constituents_as_of(day: date) -> set[str] | None:
    """Reconstruct the index membership on a past date, for survivorship control.

    Returns ``None`` when no change history is available, which the backtest
    reports rather than hiding — applying today's membership to a six-month-old
    date is a real and well-known source of optimistic bias.
    """
    if not CHANGES_PATH.exists():
        return None

    import csv

    try:
        current = set(load_cached().tickers)
    except FileNotFoundError:
        return None

    rows: list[tuple[date, str, str]] = []
    with open(CHANGES_PATH, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                changed = date.fromisoformat(row["date"].strip())
            except (KeyError, ValueError):
                continue
            rows.append((changed, sym.normalize(row.get("added", "")),
                         sym.normalize(row.get("removed", ""))))

    # Walk backwards from today, undoing each change that happened after `day`.
    for changed, added, removed in sorted(rows, key=lambda r: r[0], reverse=True):
        if changed <= day:
            break
        if added:
            current.discard(added)
        if removed:
            current.add(removed)
    return current
