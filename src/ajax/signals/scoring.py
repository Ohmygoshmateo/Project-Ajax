"""Cross-sectional composite scoring.

Each indicator is z-scored *across the universe* on a given day before being
weighted. That matters: a MACD histogram of 0.8 means nothing in isolation, but
"top decile MACD histogram among the S&P 500 today" is comparable to "top decile
relative strength today", which is what makes a weighted blend meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ajax.config import SignalConfig
from ajax.signals import indicators


@dataclass
class TickerFeatures:
    ticker: str
    relative_strength: float | None = None
    macd_histogram: float | None = None
    rsi: float | None = None
    rsi_banded: float | None = None
    roc_10: float | None = None
    roc_20: float | None = None
    volume_zscore: float | None = None
    trend: int = 0
    close: float | None = None

    def as_row(self) -> dict[str, float | None]:
        return {
            "ticker": self.ticker,
            "relative_strength": self.relative_strength,
            "macd_histogram": self.macd_histogram,
            "rsi": self.rsi,
            "rsi_banded": self.rsi_banded,
            "roc_10": self.roc_10,
            "roc_20": self.roc_20,
            "volume_zscore": self.volume_zscore,
            "trend": self.trend,
            "close": self.close,
        }


@dataclass
class RankedUniverse:
    """Scored snapshot of the universe on one date."""

    as_of: date
    frame: pd.DataFrame
    weights: dict[str, float] = field(default_factory=dict)

    def top(self, n: int) -> pd.DataFrame:
        return self.frame.nlargest(n, "composite")

    def bottom(self, n: int) -> pd.DataFrame:
        return self.frame.nsmallest(n, "composite").iloc[::-1]


def compute_features(
    prices: pd.DataFrame,
    benchmark_closes: pd.Series,
    cfg: SignalConfig,
    as_of: date | None = None,
) -> pd.DataFrame:
    """Compute per-ticker features as of the last row at or before ``as_of``.

    ``prices`` is a wide frame with a MultiIndex column ``(ticker, field)`` where
    field includes at least ``Close`` and ``Volume``.
    """
    lb = cfg.lookbacks
    rows: list[dict[str, float | None]] = []

    tickers = sorted({c[0] for c in prices.columns})
    for ticker in tickers:
        try:
            closes = prices[(ticker, "Close")].dropna()
            volumes = prices[(ticker, "Volume")].reindex(closes.index)
        except KeyError:
            continue

        if as_of is not None:
            closes = closes[closes.index.date <= as_of]
            volumes = volumes[volumes.index.date <= as_of]
        if len(closes) < cfg.warmup_days:
            continue

        rsi_series = indicators.rsi(closes, lb.rsi_period)
        _, _, hist = indicators.macd(closes, lb.macd_fast, lb.macd_slow, lb.macd_signal)
        banded = indicators.rsi_banded_score(
            rsi_series, cfg.rsi_sweet_low, cfg.rsi_sweet_high, cfg.rsi_overbought
        )
        rel = indicators.relative_strength(closes, benchmark_closes, lb.relative_strength_days)
        gate = indicators.trend_gate(closes, lb.sma_fast, lb.sma_slow)

        feats = TickerFeatures(
            ticker=ticker,
            relative_strength=_last(rel),
            macd_histogram=_last(hist),
            rsi=_last(rsi_series),
            rsi_banded=_last(banded),
            roc_10=_last(indicators.rate_of_change(closes, lb.roc_short)),
            roc_20=_last(indicators.rate_of_change(closes, lb.roc_long)),
            volume_zscore=_last(indicators.volume_zscore(volumes, lb.volume_window)),
            trend=int(_last(gate) or 0),
            close=_last(closes),
        )
        rows.append(feats.as_row())

    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def _last(series: pd.Series) -> float | None:
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    return float(value)


def zscore(column: pd.Series) -> pd.Series:
    """Cross-sectional z-score, robust to a degenerate (zero-variance) column."""
    values = column.astype(float)
    std = values.std(ddof=1)
    if not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=values.index)
    return ((values - values.mean()) / std).fillna(0.0)


def score_universe(
    features: pd.DataFrame, cfg: SignalConfig, as_of: date
) -> RankedUniverse:
    """Z-score each component across the universe and blend into ``composite``."""
    if features.empty:
        return RankedUniverse(as_of=as_of, frame=features, weights=dict(cfg.weights))

    frame = features.copy()
    weights = dict(cfg.weights)
    # Weight keys are also the feature column names.
    components = (
        "relative_strength",
        "macd_histogram",
        "rsi_banded",
        "roc_10",
        "roc_20",
        "volume_zscore",
    )

    composite = pd.Series(0.0, index=frame.index)
    total_weight = 0.0
    for column in components:
        weight = weights.get(column, 0.0)
        if weight == 0 or column not in frame.columns:
            continue
        z = zscore(frame[column])
        frame[f"z_{column}"] = z
        composite = composite + weight * z
        total_weight += weight

    if total_weight > 0:
        composite = composite / total_weight

    frame["composite"] = composite
    return RankedUniverse(as_of=as_of, frame=frame.sort_values("composite", ascending=False),
                          weights=weights)
