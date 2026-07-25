"""Paper-trading graduation check.

**Read-only by design.** Nothing in this module can change the trading mode,
place an order, or unlock anything. It reports whether a track record has met the
bar, and that is all it does. Acting on the result is a separate, manual step
(``ajax enable-live``).

The bar is two conditions, both required:

* a win rate at or above the threshold across two consecutive weeks, and
* a minimum number of *closed* trades.

The trade-count floor is the important one. With 1-2 concurrent positions and a
5-day hold, a fortnight produces roughly 2-4 closed trades — going 3-for-4 is a
75% win rate and 4-for-4 is 100%, both essentially by luck. Reporting a headline
percentage on that sample would be actively misleading, so below the floor the
result reads "insufficient sample" instead of a flattering number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ajax.config import GraduationConfig
from ajax.utils.calendar import to_date


@dataclass(frozen=True)
class WindowStats:
    label: str
    start: date
    end: date
    trades: int
    wins: int

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.trades if self.trades else None

    def describe(self) -> str:
        if not self.trades:
            return f"{self.label}: no closed trades"
        return f"{self.label}: {self.wins}/{self.trades} ({self.win_rate:.0%})"


@dataclass(frozen=True)
class GraduationStatus:
    passed: bool
    total_closed: int
    min_closed_required: int
    overall_win_rate: float | None
    windows: list[WindowStats]
    reasons: list[str]
    mode: str

    @property
    def sample_sufficient(self) -> bool:
        return self.total_closed >= self.min_closed_required

    @property
    def trades_remaining(self) -> int:
        return max(self.min_closed_required - self.total_closed, 0)

    def headline(self) -> str:
        """Win rate is never shown without its trade count."""
        if not self.sample_sufficient:
            rate = f"{self.overall_win_rate:.0%}" if self.overall_win_rate is not None else "n/a"
            return (
                f"INSUFFICIENT SAMPLE — {self.total_closed}/{self.min_closed_required} closed "
                f"trades (running win rate {rate} on {self.total_closed} trades is not yet "
                f"statistically meaningful)"
            )
        rate = f"{self.overall_win_rate:.0%}" if self.overall_win_rate is not None else "n/a"
        verdict = "PASS" if self.passed else "FAIL"
        return f"{verdict} — {rate} win rate over {self.total_closed} closed trades"


def _is_win(trade: dict[str, Any]) -> bool:
    pnl = trade.get("pnl")
    return pnl is not None and float(pnl) > 0


def _window_stats(
    trades: list[dict[str, Any]], start: date, end: date, label: str
) -> WindowStats:
    in_window = [
        t
        for t in trades
        if t.get("exit_date") and start <= to_date(t["exit_date"]) <= end
    ]
    return WindowStats(
        label=label,
        start=start,
        end=end,
        trades=len(in_window),
        wins=sum(1 for t in in_window if _is_win(t)),
    )


def evaluate(
    closed_trades: list[dict[str, Any]],
    cfg: GraduationConfig,
    *,
    as_of: date | None = None,
) -> GraduationStatus:
    """Evaluate the graduation criterion against closed trades."""
    as_of = as_of or date.today()
    total = len(closed_trades)
    wins = sum(1 for t in closed_trades if _is_win(t))
    overall = wins / total if total else None

    windows: list[WindowStats] = []
    for index in range(cfg.consecutive_weeks):
        end = as_of - timedelta(days=7 * index)
        start = end - timedelta(days=6)
        windows.append(_window_stats(closed_trades, start, end, f"week -{index}"))
    windows.reverse()

    reasons: list[str] = []

    if total < cfg.min_closed_trades:
        reasons.append(
            f"only {total} closed trades, {cfg.min_closed_trades - total} short of the "
            f"{cfg.min_closed_trades} minimum — a win rate on this few trades is noise, "
            f"not evidence"
        )

    if cfg.mode == "blended_14d":
        span_start = as_of - timedelta(days=13)
        blended = _window_stats(closed_trades, span_start, as_of, "last 14 days")
        windows = [blended]
        if blended.win_rate is None:
            reasons.append("no closed trades in the last 14 days")
        elif blended.win_rate < cfg.min_win_rate:
            reasons.append(
                f"14-day win rate {blended.win_rate:.0%} is below the "
                f"{cfg.min_win_rate:.0%} threshold"
            )
    else:
        for window in windows:
            if window.win_rate is None:
                reasons.append(f"{window.label} has no closed trades")
            elif window.win_rate < cfg.min_win_rate:
                reasons.append(
                    f"{window.label} win rate {window.win_rate:.0%} is below the "
                    f"{cfg.min_win_rate:.0%} threshold"
                )

    return GraduationStatus(
        passed=not reasons,
        total_closed=total,
        min_closed_required=cfg.min_closed_trades,
        overall_win_rate=overall,
        windows=windows,
        reasons=reasons,
        mode=cfg.mode,
    )
