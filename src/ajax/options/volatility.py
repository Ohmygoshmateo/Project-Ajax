"""Volatility estimation and the volatility-risk-premium haircut.

The backtest cannot see historical implied volatility — no free source publishes
a per-ticker IV history. It therefore prices Black-Scholes entries off *realized*
volatility, which is systematically too low: implied vol exceeds subsequently
realized vol roughly 85% of the time, by about 2-4 volatility points. Pricing at
bare realized vol underprices every option the strategy buys and flatters the
results.

:func:`entry_volatility` applies a configurable haircut to counteract that. It
is an approximation, not a correction — see docs/LIMITATIONS.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def realized_volatility(closes: pd.Series, window: int = 21) -> float | None:
    """Annualized close-to-close volatility over the trailing ``window`` bars."""
    if closes is None or len(closes) < window + 1:
        return None
    returns = np.log(closes.astype(float)).diff().dropna()
    if len(returns) < window:
        return None
    sigma = float(returns.tail(window).std(ddof=1))
    if not np.isfinite(sigma) or sigma <= 0:
        return None
    return sigma * np.sqrt(TRADING_DAYS_PER_YEAR)


def realized_volatility_series(closes: pd.Series, window: int = 21) -> pd.Series:
    """Rolling annualized volatility, aligned to ``closes``' index."""
    returns = np.log(closes.astype(float)).diff()
    return returns.rolling(window).std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)


def apply_vol_haircut(sigma: float, haircut_points: float) -> float:
    """Add ``haircut_points`` volatility *points* to a decimal sigma.

    A haircut of 3.5 turns sigma 0.25 into 0.285. The result is floored at a
    small positive value so downstream pricing never divides by zero.
    """
    if sigma is None:
        raise ValueError("sigma is required")
    return max(float(sigma) + float(haircut_points) / 100.0, 1e-6)


def entry_volatility(
    closes: pd.Series,
    *,
    window: int = 21,
    haircut_points: float = 3.5,
    fallback: float = 0.30,
) -> tuple[float, str]:
    """Volatility to price a modelled *entry* at, plus a provenance label.

    Returns ``(sigma, source)`` where source is ``"realized+haircut"`` or
    ``"fallback+haircut"``. The label is recorded per trade so a report can show
    how much of a run leaned on the fallback.
    """
    rv = realized_volatility(closes, window=window)
    if rv is None:
        return apply_vol_haircut(fallback, haircut_points), "fallback+haircut"
    return apply_vol_haircut(rv, haircut_points), "realized+haircut"


def exit_volatility(closes: pd.Series, *, window: int = 21, fallback: float = 0.30) -> float:
    """Volatility to price a modelled *exit* at.

    No haircut here: the haircut exists to stop us buying too cheaply. Applying
    it on the way out as well would silently cancel its effect.
    """
    rv = realized_volatility(closes, window=window)
    return rv if rv is not None else fallback
