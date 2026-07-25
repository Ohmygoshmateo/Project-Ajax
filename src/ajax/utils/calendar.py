"""NYSE trading-day arithmetic.

The 5-day hold is 5 *trading* days, not 5 calendar days, and the backtest's
entry/exit dates must land on real sessions. Everything that counts days goes
through here.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

import pandas as pd


@lru_cache(maxsize=4)
def _schedule(start: date, end: date) -> pd.DatetimeIndex:
    try:
        import pandas_market_calendars as mcal

        nyse = mcal.get_calendar("NYSE")
        sched = nyse.schedule(start_date=start, end_date=end)
        return pd.DatetimeIndex(sched.index)
    except Exception:
        # Fall back to weekdays if the calendar package is unavailable. This is
        # less accurate around holidays; callers that care should check
        # `calendar_is_exact()`.
        return pd.DatetimeIndex(pd.bdate_range(start, end))


def calendar_is_exact() -> bool:
    try:
        import pandas_market_calendars  # noqa: F401

        return True
    except Exception:
        return False


def trading_days(start: date, end: date) -> list[date]:
    """All NYSE session dates in ``[start, end]``."""
    if end < start:
        return []
    idx = _schedule(start, end)
    return [ts.date() for ts in idx]


def is_trading_day(day: date) -> bool:
    return day in set(trading_days(day, day))


def next_trading_day(day: date, n: int = 1) -> date:
    """The nth trading day strictly after ``day``."""
    if n < 1:
        raise ValueError("n must be >= 1")
    horizon = day + timedelta(days=10 + n * 3)
    sessions = [d for d in trading_days(day + timedelta(days=1), horizon) if d > day]
    if len(sessions) < n:
        raise ValueError(f"could not find {n} trading days after {day}")
    return sessions[n - 1]


def add_trading_days(day: date, n: int) -> date:
    """Alias for :func:`next_trading_day` that reads better at call sites."""
    return next_trading_day(day, n)


def trading_days_between(start: date, end: date) -> int:
    """Number of sessions strictly after ``start`` up to and including ``end``."""
    if end <= start:
        return 0
    return len([d for d in trading_days(start, end) if d > start])


def to_date(value: str | date | datetime | pd.Timestamp) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime().date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
