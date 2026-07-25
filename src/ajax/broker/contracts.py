"""OCC option symbol construction and parsing.

Format (21 chars): ``ROOT`` left-justified to 6, ``YYMMDD``, ``C``/``P``, then
the strike times 1000 zero-padded to 8 digits.

    AAPL  240119C00190000  ->  AAPL, 2024-01-19, call, strike 190.0

Alpaca's chain endpoint returns snapshots keyed by this symbol and does not
repeat the strike, expiry, or right as separate fields, so parsing it correctly
is load-bearing rather than cosmetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ajax.data import symbols as sym
from ajax.options.greeks import Right

_OCC_RE = re.compile(r"^(?P<root>[A-Z0-9.]{1,6})\s*(?P<ymd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")

STRIKE_SCALE = 1000


@dataclass(frozen=True)
class OccSymbol:
    underlying: str
    expiry: date
    right: Right
    strike: float

    def format(self) -> str:
        return build_occ_symbol(self.underlying, self.expiry, self.right, self.strike)


def build_occ_symbol(underlying: str, expiry: date, right: Right, strike: float) -> str:
    root = sym.normalize(underlying).replace(".", "")
    if len(root) > 6:
        raise ValueError(f"root symbol too long for OCC format: {root}")
    strike_int = int(round(strike * STRIKE_SCALE))
    if strike_int <= 0 or strike_int > 99_999_999:
        raise ValueError(f"strike out of OCC range: {strike}")
    return f"{root:<6}{expiry:%y%m%d}{'C' if right is Right.CALL else 'P'}{strike_int:08d}"


def parse_occ_symbol(symbol: str) -> OccSymbol | None:
    """Parse an OCC symbol, tolerating the padded and unpadded forms.

    Returns ``None`` rather than raising: an unrecognized symbol from a provider
    should cause that contract to be skipped, not abort the whole chain.
    """
    if not symbol:
        return None
    raw = symbol.strip().upper()
    match = _OCC_RE.match(raw)
    if not match:
        # Unpadded form, e.g. "AAPL240119C00190000".
        compact = raw.replace(" ", "")
        if len(compact) < 15:
            return None
        tail = compact[-15:]
        match = _OCC_RE.match(f"{compact[:-15]:<6}{tail}")
        if not match:
            return None

    try:
        expiry = date(
            2000 + int(match["ymd"][0:2]), int(match["ymd"][2:4]), int(match["ymd"][4:6])
        )
    except ValueError:
        return None

    return OccSymbol(
        underlying=match["root"].strip(),
        expiry=expiry,
        right=Right.CALL if match["right"] == "C" else Right.PUT,
        strike=int(match["strike"]) / STRIKE_SCALE,
    )


def third_friday(year: int, month: int) -> date:
    """Standard monthly expiration date."""
    first = date(year, month, 1)
    # weekday(): Monday=0 ... Friday=4
    offset = (4 - first.weekday()) % 7
    return date(year, month, 1 + offset + 14)


def monthly_expiries_between(start: date, end: date) -> list[date]:
    """Standard monthly expirations in ``[start, end]``.

    Used by the backtest to enumerate plausible contracts for a historical date
    when expired-contract listing is unavailable.
    """
    out: list[date] = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        expiry = third_friday(year, month)
        if start <= expiry <= end:
            out.append(expiry)
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out
