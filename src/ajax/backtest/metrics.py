"""Performance metrics.

Win rate is never reported alone. On the trade counts this strategy produces —
1-2 concurrent positions, 5-day holds — a bare percentage invites reading noise
as signal, so every accessor that exposes a win rate exposes the sample size
beside it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ajax.backtest.engine import BacktestTrade


@dataclass
class Metrics:
    trade_count: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    total_pnl: float = 0.0
    avg_pnl: float | None = None
    avg_return_pct: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    max_drawdown: float = 0.0
    max_drawdown_pct: float | None = None
    sharpe_like: float | None = None
    best_trade: float | None = None
    worst_trade: float | None = None
    by_source: dict[str, dict] = field(default_factory=dict)
    by_ticker: dict[str, dict] = field(default_factory=dict)

    @property
    def win_rate_display(self) -> str:
        """Win rate with its sample size attached — the only sanctioned format."""
        if self.win_rate is None:
            return "n/a (0 trades)"
        return f"{self.win_rate:.0%} ({self.wins}/{self.trade_count} trades)"

    @property
    def sample_warning(self) -> str | None:
        if self.trade_count == 0:
            return "No closed trades — nothing to evaluate."
        if self.trade_count < 20:
            return (
                f"Only {self.trade_count} closed trades. A win rate on this sample is not "
                f"statistically meaningful; a single outcome moves it by "
                f"{1 / self.trade_count:.0%}."
            )
        return None


def equity_curve(trades: list[BacktestTrade], starting_equity: float) -> list[float]:
    """Cumulative equity, ordered by exit date."""
    curve = [starting_equity]
    for trade in sorted(trades, key=lambda t: t.exit_date):
        curve.append(curve[-1] + trade.pnl)
    return curve


def max_drawdown(curve: list[float]) -> tuple[float, float | None]:
    """Largest peak-to-trough decline, in dollars and as a fraction of the peak."""
    if len(curve) < 2:
        return 0.0, None
    peak = curve[0]
    worst = 0.0
    worst_pct: float | None = None
    for value in curve:
        peak = max(peak, value)
        drop = peak - value
        if drop > worst:
            worst = drop
            worst_pct = drop / peak if peak > 0 else None
    return worst, worst_pct


def compute_metrics(trades: list[BacktestTrade], starting_equity: float = 5000.0) -> Metrics:
    metrics = Metrics(trade_count=len(trades))
    if not trades:
        return metrics

    pnls = np.array([t.pnl for t in trades], dtype=float)
    returns = np.array([t.pnl_pct for t in trades], dtype=float)

    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]

    metrics.wins = len(wins)
    metrics.losses = len(losses)
    metrics.win_rate = len(wins) / len(trades)
    metrics.total_pnl = float(pnls.sum())
    metrics.avg_pnl = float(pnls.mean())
    metrics.avg_return_pct = float(returns.mean())
    metrics.avg_win = float(np.mean([t.pnl for t in wins])) if wins else None
    metrics.avg_loss = float(np.mean([t.pnl for t in losses])) if losses else None
    metrics.best_trade = float(pnls.max())
    metrics.worst_trade = float(pnls.min())

    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    metrics.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    win_rate = metrics.win_rate
    if metrics.avg_win is not None and metrics.avg_loss is not None:
        metrics.expectancy = win_rate * metrics.avg_win + (1 - win_rate) * metrics.avg_loss
    elif metrics.avg_win is not None:
        metrics.expectancy = metrics.avg_win

    curve = equity_curve(trades, starting_equity)
    metrics.max_drawdown, metrics.max_drawdown_pct = max_drawdown(curve)

    if len(returns) > 1:
        std = float(returns.std(ddof=1))
        if std > 0 and math.isfinite(std):
            # Per-trade Sharpe-like ratio, annualized by trade frequency rather
            # than calendar time. Not a true Sharpe — no risk-free adjustment.
            metrics.sharpe_like = float(returns.mean() / std)

    metrics.by_source = _group(trades, lambda t: t.entry_source)
    metrics.by_ticker = _group(trades, lambda t: t.ticker)
    return metrics


def _group(trades: list[BacktestTrade], key) -> dict[str, dict]:  # noqa: ANN001
    buckets: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        buckets.setdefault(key(trade), []).append(trade)

    out: dict[str, dict] = {}
    for name, group in sorted(buckets.items()):
        group_wins = sum(1 for t in group if t.is_win)
        out[name] = {
            "trades": len(group),
            "wins": group_wins,
            "win_rate": group_wins / len(group),
            "total_pnl": sum(t.pnl for t in group),
            "avg_pnl": sum(t.pnl for t in group) / len(group),
        }
    return out
