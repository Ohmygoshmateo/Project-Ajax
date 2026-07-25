"""Technical indicators.

All functions take and return pandas Series aligned to a date index, and none of
them look forward: a value at index *i* uses only data at or before *i*. That
property is what makes the backtest's no-lookahead guarantee hold, so it is
tested directly rather than assumed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing."""
    delta = closes.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # All-gain windows have zero average loss, which is RSI 100 by definition.
    return out.where(avg_loss != 0.0, 100.0).where(avg_gain.notna())


def macd(
    closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return ``(macd_line, signal_line, histogram)``."""
    c = closes.astype(float)
    ema_fast = c.ewm(span=fast, min_periods=fast, adjust=False).mean()
    ema_slow = c.ewm(span=slow, min_periods=slow, adjust=False).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, min_periods=signal, adjust=False).mean()
    return line, sig, line - sig


def rate_of_change(closes: pd.Series, period: int) -> pd.Series:
    """Percent change over ``period`` bars."""
    c = closes.astype(float)
    return (c / c.shift(period) - 1.0) * 100.0


def relative_strength(closes: pd.Series, benchmark: pd.Series, period: int = 21) -> pd.Series:
    """Excess return versus a benchmark over ``period`` bars, in percent.

    This is the component that most directly operationalizes "strength" and
    "weakness": it is market-neutral, so a name that merely rose with the index
    does not register as strong.
    """
    own = rate_of_change(closes, period)
    bench = rate_of_change(benchmark.reindex(closes.index).ffill(), period)
    return own - bench


def volume_zscore(volumes: pd.Series, window: int = 20) -> pd.Series:
    """Standardized volume versus its own trailing distribution."""
    v = volumes.astype(float)
    mean = v.rolling(window, min_periods=window).mean()
    std = v.rolling(window, min_periods=window).std(ddof=1)
    return (v - mean) / std.replace(0.0, np.nan)


def sma(closes: pd.Series, window: int) -> pd.Series:
    return closes.astype(float).rolling(window, min_periods=window).mean()


def rsi_banded_score(rsi_values: pd.Series, low: float, high: float, overbought: float) -> pd.Series:
    """Map RSI onto a bullish-favorability score in roughly [-1, 1].

    Peaks across the ``low``-``high`` band, decays outside it, and goes negative
    above ``overbought``. RSI is used here as momentum *confirmation*, not as a
    mean-reversion signal — the point is to avoid buying exhaustion, not to buy
    dips.
    """
    r = rsi_values.astype(float)
    mid = (low + high) / 2.0
    half = max((high - low) / 2.0, 1e-9)

    score = 1.0 - ((r - mid).abs() / half)
    score = score.clip(lower=-1.0, upper=1.0)
    # Penalize genuine overbought readings beyond the mere distance decay.
    penalty = ((r - overbought) / max(100.0 - overbought, 1e-9)).clip(lower=0.0)
    return (score - 2.0 * penalty).clip(lower=-1.0, upper=1.0)


def trend_gate(closes: pd.Series, fast: int = 20, slow: int = 50) -> pd.Series:
    """Boolean-ish trend state: ``+1`` uptrend, ``-1`` downtrend, ``0`` neither.

    Deliberately a filter rather than a score component — a candidate either has
    a tradeable trend or it does not, and blending that into a continuous score
    would let a strong momentum reading paper over a broken chart.
    """
    c = closes.astype(float)
    fast_ma = sma(c, fast)
    slow_ma = sma(c, slow)
    fast_slope = fast_ma.diff()

    up = (c > fast_ma) & (fast_ma > slow_ma) & (fast_slope > 0)
    down = (c < fast_ma) & (fast_ma < slow_ma) & (fast_slope < 0)

    out = pd.Series(0, index=c.index, dtype="int64")
    out[up] = 1
    out[down] = -1
    return out.where(slow_ma.notna())
