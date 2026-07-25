"""Turn composite scores into actionable BUY_CALL / BUY_PUT / WATCH labels."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from ajax.config import SignalConfig
from ajax.options.greeks import Right
from ajax.signals.scoring import RankedUniverse


class Label(str, Enum):
    BUY_CALL = "BUY_CALL"
    BUY_PUT = "BUY_PUT"
    WATCH = "WATCH"

    @property
    def right(self) -> Right | None:
        if self is Label.BUY_CALL:
            return Right.CALL
        if self is Label.BUY_PUT:
            return Right.PUT
        return None


@dataclass(frozen=True)
class Candidate:
    ticker: str
    label: Label
    composite: float
    rank: int
    close: float | None
    trend: int
    reason: str = ""

    @property
    def actionable(self) -> bool:
        return self.label is not Label.WATCH

    @property
    def conviction(self) -> float:
        return abs(self.composite)


def label_candidates(ranked: RankedUniverse, cfg: SignalConfig) -> list[Candidate]:
    """Top-N strongest become call candidates, bottom-N weakest become puts.

    A candidate is only actionable if it also clears the score threshold and the
    trend gate; otherwise it is reported as WATCH with the reason recorded, so
    the scan output explains itself rather than silently dropping names.
    """
    if ranked.frame.empty:
        return []

    frame = ranked.frame
    strongest = frame.nlargest(cfg.top_n, "composite")
    weakest = frame.nsmallest(cfg.top_n, "composite")

    out: list[Candidate] = []
    for rank, (ticker, row) in enumerate(strongest.iterrows(), start=1):
        out.append(_build(ticker, row, Label.BUY_CALL, rank, cfg))
    for rank, (ticker, row) in enumerate(weakest.iterrows(), start=1):
        out.append(_build(ticker, row, Label.BUY_PUT, rank, cfg))
    return out


def _build(ticker: str, row: pd.Series, intended: Label, rank: int, cfg: SignalConfig) -> Candidate:
    composite = float(row.get("composite", 0.0))
    trend = int(row.get("trend", 0) or 0)
    close = row.get("close")
    close = float(close) if close is not None and pd.notna(close) else None

    if abs(composite) < cfg.entry_score_threshold:
        return Candidate(
            ticker, Label.WATCH, composite, rank, close, trend,
            reason=f"|score| {abs(composite):.2f} below threshold {cfg.entry_score_threshold:.2f}",
        )

    if cfg.require_trend_gate:
        wanted = 1 if intended is Label.BUY_CALL else -1
        if trend != wanted:
            return Candidate(
                ticker, Label.WATCH, composite, rank, close, trend,
                reason=f"trend gate not confirmed (state {trend}, needed {wanted})",
            )

    return Candidate(ticker, intended, composite, rank, close, trend)
