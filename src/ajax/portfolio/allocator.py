"""Slot allocation across candidates.

With only 1-2 concurrent positions, which candidates get the slots matters more
than usual — there is no diversification to average out a bad pick, so slots go
to the highest-conviction signals and never two to the same underlying.
"""

from __future__ import annotations

from dataclasses import dataclass

from ajax.config import AccountConfig
from ajax.signals.labels import Candidate


@dataclass(frozen=True)
class AllocationPlan:
    selected: list[Candidate]
    free_slots: int
    open_positions: int
    skipped_for_slots: list[Candidate]
    skipped_for_duplicate: list[Candidate]


def free_slots(account: AccountConfig, open_positions: int) -> int:
    return max(account.max_concurrent_positions - open_positions, 0)


def allocate(
    candidates: list[Candidate],
    account: AccountConfig,
    *,
    open_positions: int = 0,
    open_underlyings: set[str] | None = None,
) -> AllocationPlan:
    """Order actionable candidates by conviction and fill the free slots."""
    held = set(open_underlyings or set())
    slots = free_slots(account, open_positions)

    actionable = [c for c in candidates if c.actionable]
    actionable.sort(key=lambda c: (-c.conviction, c.ticker))

    selected: list[Candidate] = []
    dup: list[Candidate] = []
    overflow: list[Candidate] = []

    for candidate in actionable:
        if candidate.ticker in held:
            dup.append(candidate)
            continue
        if len(selected) >= slots:
            overflow.append(candidate)
            continue
        selected.append(candidate)
        held.add(candidate.ticker)

    return AllocationPlan(
        selected=selected,
        free_slots=slots,
        open_positions=open_positions,
        skipped_for_slots=overflow,
        skipped_for_duplicate=dup,
    )
