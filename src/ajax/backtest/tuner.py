"""Parameter grid search with a train/holdout split.

With six months of data and a handful of trades per configuration, a grid search
will find a "best" parameter set whether or not any edge exists. The holdout
split and the loud overfitting warning exist because the in-sample leaderboard
is the most dangerous output this project produces — it is the number most likely
to be mistaken for evidence.

``hold_trading_days`` is deliberately not sweepable: the 5-day hold is a
requirement, not a parameter.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ajax.backtest.engine import run_backtest
from ajax.backtest.metrics import Metrics, compute_metrics
from ajax.backtest.price_source import build_price_source
from ajax.config import Config, load_raw_config, set_by_path

log = logging.getLogger(__name__)

FORBIDDEN_KEYS = {"backtest.hold_trading_days"}


@dataclass
class GridPoint:
    params: dict[str, Any]
    in_sample: Metrics
    holdout: Metrics
    warnings: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """Rank by holdout expectancy, not in-sample win rate.

        Win rate is trivially gameable by a grid search — a configuration that
        takes two trades and wins both scores 100%. Expectancy on unseen data is
        harder to fake, though with samples this small it is still noisy.
        """
        if self.holdout.trade_count == 0:
            return float("-inf")
        return self.holdout.expectancy or 0.0


def load_grid(path: Path) -> dict[str, list[Any]]:
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}

    grid: dict[str, list[Any]] = {}
    for key, values in raw.items():
        if key in FORBIDDEN_KEYS:
            log.warning("ignoring %s — the 5-day hold is a requirement, not a parameter", key)
            continue
        grid[key] = list(values) if isinstance(values, (list, tuple)) else [values]
    return grid


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = sorted(grid)
    return [
        dict(zip(keys, combo, strict=True))
        for combo in itertools.product(*(grid[k] for k in keys))
    ]


def split_window(start: date, end: date, holdout_days: int = 30) -> tuple[tuple[date, date], tuple[date, date]]:
    """Train on everything but the most recent ``holdout_days``."""
    holdout_start = end - timedelta(days=holdout_days)
    if holdout_start <= start:
        holdout_start = start + (end - start) // 2
    return (start, holdout_start - timedelta(days=1)), (holdout_start, end)


def _config_for(params: dict[str, Any]) -> Config:
    raw = load_raw_config()
    for dotted, value in params.items():
        raw = set_by_path(raw, dotted, value)
    return Config.model_validate(raw)


def run_grid(
    prices: pd.DataFrame,
    grid: dict[str, list[Any]],
    *,
    start: date,
    end: date,
    holdout_days: int = 30,
    progress=None,  # noqa: ANN001
) -> list[GridPoint]:
    """Evaluate every grid point on train and holdout windows."""
    (train_start, train_end), (hold_start, hold_end) = split_window(start, end, holdout_days)
    points: list[GridPoint] = []

    for params in expand_grid(grid):
        cfg = _config_for(params)

        warnings: list[str] = []
        if cfg.options.delta_min >= cfg.options.delta_max:
            warnings.append("delta_min >= delta_max; grid point is degenerate")
        if cfg.options.dte_target_min > cfg.options.dte_target_max:
            warnings.append("dte_target_min > dte_target_max; grid point is degenerate")

        train = run_backtest(
            prices, cfg, build_price_source(cfg), start=train_start, end=train_end
        )
        holdout = run_backtest(
            prices, cfg, build_price_source(cfg), start=hold_start, end=hold_end
        )

        points.append(
            GridPoint(
                params=params,
                in_sample=compute_metrics(train.trades, cfg.account.equity),
                holdout=compute_metrics(holdout.trades, cfg.account.equity),
                warnings=warnings,
            )
        )
        if progress is not None:
            progress(params)

    return sorted(points, key=lambda p: p.score, reverse=True)


def leaderboard_frame(points: list[GridPoint]) -> pd.DataFrame:
    rows = []
    for point in points:
        row = dict(point.params)
        row.update(
            {
                "is_trades": point.in_sample.trade_count,
                "is_win_rate": point.in_sample.win_rate,
                "is_expectancy": point.in_sample.expectancy,
                "is_total_pnl": point.in_sample.total_pnl,
                "oos_trades": point.holdout.trade_count,
                "oos_win_rate": point.holdout.win_rate,
                "oos_expectancy": point.holdout.expectancy,
                "oos_total_pnl": point.holdout.total_pnl,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def write_tuned_config(best: GridPoint, path: Path) -> Path:
    """Write the winning parameters. Applying them is a separate manual step."""
    payload: dict[str, Any] = {}
    for dotted, value in best.params.items():
        payload = set_by_path(payload, dotted, value)

    header = (
        "# Written by `ajax tune`. These are the best-scoring parameters from a grid\n"
        "# search, NOT a validated edge — with a six-month window and few trades per\n"
        "# configuration, a grid search finds a winner whether or not one exists.\n"
        "#\n"
        "# This file is loaded automatically if present. Delete it to revert to defaults.\n"
        f"# Holdout: {best.holdout.trade_count} trades, "
        f"win rate {best.holdout.win_rate_display}.\n\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(payload, sort_keys=True))
    return path


OVERFITTING_WARNING = (
    "A grid search over a six-month window with few trades per configuration will always "
    "produce a leaderboard. Ranking is by HOLDOUT expectancy rather than in-sample win rate "
    "for that reason, but a small holdout is still noisy. Treat the top row as a hypothesis "
    "to test forward in paper trading, not as a tuned strategy."
)
