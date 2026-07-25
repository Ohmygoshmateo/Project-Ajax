"""Historical option pricing for the backtest, from two sources.

1. :class:`AlpacaBarsSource` — real traded option OHLCV. Strongly preferred:
   these are prices someone actually paid.
2. :class:`BlackScholesSource` — a model reconstruction, used where bars do not
   exist. Systematically optimistic unless the volatility haircut is applied
   (see :mod:`ajax.options.volatility`).

Strike *selection* always uses a modelled delta regardless of source, because
real historical greeks are not available at any accessible price. Only *pricing*
switches. Every priced point records which source produced it so reports can
break results out by provenance instead of blending the trustworthy with the
approximated.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from ajax.config import Config
from ajax.options.greeks import Right, bs_price, year_fraction
from ajax.options.volatility import (
    apply_vol_haircut,
    exit_volatility,
    realized_volatility,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PricedPoint:
    price: float
    source: str  # "alpaca_bars" | "black_scholes"
    detail: str = ""


class OptionPriceSource(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def price_on(
        self,
        occ_symbol: str,
        underlying: str,
        strike: float,
        expiry: date,
        right: Right,
        on: date,
        *,
        spot: float,
        closes: pd.Series,
        is_entry: bool,
    ) -> PricedPoint | None:
        """Mid price of the contract on ``on``, or None if unavailable."""


class AlpacaBarsSource(OptionPriceSource):
    """Real historical option bars, pre-loaded per contract."""

    name = "alpaca_bars"

    def __init__(self, bars: dict[str, pd.DataFrame] | None = None) -> None:
        self.bars = bars or {}

    def add(self, occ_symbol: str, frame: pd.DataFrame) -> None:
        self.bars[occ_symbol] = frame

    def price_on(
        self,
        occ_symbol: str,
        underlying: str,
        strike: float,
        expiry: date,
        right: Right,
        on: date,
        *,
        spot: float,
        closes: pd.Series,
        is_entry: bool,
    ) -> PricedPoint | None:
        frame = self.bars.get(occ_symbol)
        if frame is None or frame.empty:
            return None

        column = "open" if is_entry else "close"
        if column not in frame.columns:
            column = next((c for c in ("close", "open", "vwap") if c in frame.columns), None)
            if column is None:
                return None

        index = pd.to_datetime(frame.index if "timestamp" not in frame.columns
                               else frame["timestamp"])
        mask = pd.Series(index).dt.date == on
        if not mask.any():
            return None

        value = float(frame.loc[mask.values, column].iloc[0])
        if value <= 0:
            return None
        return PricedPoint(price=value, source=self.name, detail=column)


class BlackScholesSource(OptionPriceSource):
    """Model reconstruction from underlying price and a volatility estimate.

    On entry the volatility carries the configured haircut: implied vol exceeds
    subsequently realized vol roughly 85% of the time, so pricing an entry at
    bare realized vol buys the option too cheaply and flatters every result that
    follows. No haircut is applied on exit — doing so would cancel the effect.
    """

    name = "black_scholes"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def price_on(
        self,
        occ_symbol: str,
        underlying: str,
        strike: float,
        expiry: date,
        right: Right,
        on: date,
        *,
        spot: float,
        closes: pd.Series,
        is_entry: bool,
    ) -> PricedPoint | None:
        if spot is None or spot <= 0:
            return None

        tenor = year_fraction((expiry - on).days)
        if tenor <= 0:
            return None

        window = self.cfg.backtest.realized_vol_window
        history = closes[closes.index.date <= on] if len(closes) else closes

        if is_entry:
            rv = realized_volatility(history, window=window)
            sigma = apply_vol_haircut(
                rv if rv is not None else 0.30, self.cfg.backtest.vol_haircut_points
            )
            detail = "realized+haircut" if rv is not None else "fallback+haircut"
        else:
            sigma = exit_volatility(history, window=window)
            detail = "realized"

        price = bs_price(spot, strike, tenor, self.cfg.backtest.risk_free_rate, sigma, right)
        if price <= 0:
            return None
        return PricedPoint(price=price, source=self.name, detail=f"{detail} sigma={sigma:.3f}")


class ChainedPriceSource(OptionPriceSource):
    """Try real bars first, fall back to the model, and record which was used."""

    name = "chained"

    def __init__(self, primary: OptionPriceSource, fallback: OptionPriceSource) -> None:
        self.primary = primary
        self.fallback = fallback
        self.counts: dict[str, int] = {primary.name: 0, fallback.name: 0, "unpriced": 0}

    def price_on(self, *args, **kwargs) -> PricedPoint | None:  # noqa: ANN002, ANN003
        point = self.primary.price_on(*args, **kwargs)
        if point is not None:
            self.counts[self.primary.name] += 1
            return point

        point = self.fallback.price_on(*args, **kwargs)
        if point is not None:
            self.counts[self.fallback.name] += 1
            return point

        self.counts["unpriced"] += 1
        return None

    @property
    def coverage(self) -> dict[str, float]:
        """Fraction of pricing requests served by each source."""
        total = sum(self.counts.values())
        if total == 0:
            return {k: 0.0 for k in self.counts}
        return {k: v / total for k, v in self.counts.items()}


def build_price_source(cfg: Config, bars: dict[str, pd.DataFrame] | None = None):
    """Assemble the source stack named by ``cfg.backtest.price_source``."""
    mode = (cfg.backtest.price_source or "auto").lower()
    model = BlackScholesSource(cfg)

    if mode == "bs":
        return model
    if mode == "bars":
        return AlpacaBarsSource(bars)
    return ChainedPriceSource(AlpacaBarsSource(bars), model)
