"""Ticker format translation.

Dual-class share tickers are written differently by each provider: Yahoo uses a
dash (``BRK-B``), while Alpaca and OCC use a dot (``BRK.B``). Getting this wrong
silently drops exactly the names it affects, so translation has one home.
"""

from __future__ import annotations

# Known S&P 500 dual-class tickers, in canonical (dotted) form. Used to make the
# round-trip test meaningful rather than tautological.
KNOWN_DUAL_CLASS = ("BRK.B", "BF.B")


def to_yahoo(symbol: str) -> str:
    """Canonical/dotted form to Yahoo's dashed form."""
    return symbol.strip().upper().replace(".", "-")


def to_alpaca(symbol: str) -> str:
    """Yahoo's dashed form to the canonical/dotted form Alpaca and OCC expect."""
    return symbol.strip().upper().replace("-", ".")


def normalize(symbol: str) -> str:
    """Canonical internal form: uppercase, dotted."""
    return to_alpaca(symbol)


def to_yahoo_many(symbols: list[str]) -> list[str]:
    return [to_yahoo(s) for s in symbols]


def to_alpaca_many(symbols: list[str]) -> list[str]:
    return [to_alpaca(s) for s in symbols]
