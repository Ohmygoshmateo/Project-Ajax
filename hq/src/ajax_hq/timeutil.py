"""Timezone normalization.

Sources disagree: transcript timestamps are UTC-aware, git is offset-aware, and
filesystem mtimes are naive local. Comparing them directly raises, so everything
is normalized to aware-UTC at the boundary and compared only after that.
"""

from __future__ import annotations

from datetime import UTC, datetime


def aware(value: datetime | None) -> datetime | None:
    """Coerce to timezone-aware UTC. Naive values are assumed local."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.astimezone(UTC)
    return value.astimezone(UTC)


def now() -> datetime:
    return datetime.now(UTC)


def newest(*values: datetime | None) -> datetime | None:
    stamps = [s for s in (aware(v) for v in values) if s is not None]
    return max(stamps) if stamps else None


def hours_since(value: datetime | None) -> float | None:
    stamp = aware(value)
    if stamp is None:
        return None
    return (now() - stamp).total_seconds() / 3600.0
