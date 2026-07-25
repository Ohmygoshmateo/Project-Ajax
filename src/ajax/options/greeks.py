"""Black-Scholes pricing, greeks, and an implied-volatility solver.

Two consumers:

* **Live selection fallback** — used only when Alpaca's chain returns null
  greeks (the free indicative feed may not populate them). Provenance is always
  stamped on the resulting quote so a model-derived delta is never mistaken for
  an exchange-published one.
* **Backtest** — strike selection always needs a delta, and real historical
  greeks do not exist at any accessible price, so they are always modelled.

Everything here is pure and deterministic, which is why it carries the densest
test coverage in the project.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from scipy.optimize import brentq
from scipy.stats import norm

# Below this many years to expiry, or this volatility, the model degenerates and
# we return intrinsic value rather than dividing by ~zero.
_MIN_T = 1e-9
_MIN_SIGMA = 1e-9


class Right(str, Enum):
    CALL = "call"
    PUT = "put"

    @property
    def sign(self) -> int:
        return 1 if self is Right.CALL else -1


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 volatility point (i.e. per 0.01 of sigma)
    rho: float  # per 1 rate point


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float) -> tuple[float, float]:
    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


def _intrinsic(S: float, K: float, right: Right) -> float:
    return max(0.0, right.sign * (S - K))


def bs_price(
    S: float, K: float, T: float, r: float, sigma: float, right: Right, q: float = 0.0
) -> float:
    """Black-Scholes-Merton price of a European option."""
    if S <= 0 or K <= 0:
        raise ValueError("spot and strike must be positive")
    if T <= _MIN_T or sigma <= _MIN_SIGMA:
        return _intrinsic(S, K, right)
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    disc_r, disc_q = math.exp(-r * T), math.exp(-q * T)
    if right is Right.CALL:
        return S * disc_q * norm.cdf(d1) - K * disc_r * norm.cdf(d2)
    return K * disc_r * norm.cdf(-d2) - S * disc_q * norm.cdf(-d1)


def bs_delta(
    S: float, K: float, T: float, r: float, sigma: float, right: Right, q: float = 0.0
) -> float:
    """Delta. Calls are positive, puts negative; callers comparing against a
    configured band should use the absolute value."""
    if S <= 0 or K <= 0:
        raise ValueError("spot and strike must be positive")
    if T <= _MIN_T or sigma <= _MIN_SIGMA:
        # Degenerate to a step function at expiry.
        if right is Right.CALL:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1, _ = _d1_d2(S, K, T, r, sigma, q)
    disc_q = math.exp(-q * T)
    if right is Right.CALL:
        return disc_q * norm.cdf(d1)
    return -disc_q * norm.cdf(-d1)


def bs_greeks(
    S: float, K: float, T: float, r: float, sigma: float, right: Right, q: float = 0.0
) -> Greeks:
    """Full greek set. Theta is per calendar day, vega and rho per point."""
    price = bs_price(S, K, T, r, sigma, right, q)
    delta = bs_delta(S, K, T, r, sigma, right, q)
    if T <= _MIN_T or sigma <= _MIN_SIGMA:
        return Greeks(price=price, delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    sqrt_t = math.sqrt(T)
    disc_r, disc_q = math.exp(-r * T), math.exp(-q * T)
    pdf_d1 = norm.pdf(d1)

    gamma = disc_q * pdf_d1 / (S * sigma * sqrt_t)
    vega = S * disc_q * pdf_d1 * sqrt_t / 100.0

    common_theta = -(S * disc_q * pdf_d1 * sigma) / (2 * sqrt_t)
    if right is Right.CALL:
        theta = common_theta - r * K * disc_r * norm.cdf(d2) + q * S * disc_q * norm.cdf(d1)
        rho = K * T * disc_r * norm.cdf(d2) / 100.0
    else:
        theta = common_theta + r * K * disc_r * norm.cdf(-d2) - q * S * disc_q * norm.cdf(-d1)
        rho = -K * T * disc_r * norm.cdf(-d2) / 100.0

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        theta=theta / 365.0,
        vega=vega,
        rho=rho,
    )


def implied_volatility(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    right: Right,
    q: float = 0.0,
    *,
    lo: float = 1e-4,
    hi: float = 5.0,
) -> float | None:
    """Solve for sigma given a market price. Returns None if no root exists.

    A None result usually means the quote is stale or arbitrage-violating (below
    intrinsic), which is exactly the kind of contract the selector should skip.
    """
    if T <= _MIN_T or price <= 0:
        return None
    intrinsic = _intrinsic(S, K, right)
    if price < intrinsic - 1e-8:
        return None

    def objective(sigma: float) -> float:
        return bs_price(S, K, T, r, sigma, right, q) - price

    try:
        if objective(lo) > 0 or objective(hi) < 0:
            return None
        return float(brentq(objective, lo, hi, maxiter=100, xtol=1e-8))
    except (ValueError, RuntimeError):
        return None


def strike_for_delta(
    target_delta: float,
    S: float,
    T: float,
    r: float,
    sigma: float,
    right: Right,
    q: float = 0.0,
) -> float:
    """Invert delta to a strike. Used by the backtest to pick a contract when no
    real chain exists for a historical date.

    ``target_delta`` is given as a magnitude in (0, 1) for both rights.
    """
    if not 0 < target_delta < 1:
        raise ValueError("target_delta must be a magnitude in (0, 1)")
    if T <= _MIN_T or sigma <= _MIN_SIGMA:
        return S

    # Invert the closed form: delta_call = e^{-qT} N(d1)  =>  d1 = N^-1(delta e^{qT})
    adjusted = min(max(target_delta * math.exp(q * T), 1e-9), 1 - 1e-9)
    d1 = norm.ppf(adjusted)
    vol_sqrt_t = sigma * math.sqrt(T)
    return float(S * math.exp(-d1 * vol_sqrt_t + (r - q + 0.5 * sigma * sigma) * T))


def year_fraction(days: int | float) -> float:
    """Calendar days to years, the convention used throughout the codebase."""
    return max(float(days), 0.0) / 365.0
