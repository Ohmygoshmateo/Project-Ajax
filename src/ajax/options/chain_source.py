"""Normalized option-quote model and the chain-source interface.

Downstream code (selector, sizing, backtest, broker) depends only on
:class:`ContractQuote`, never on a vendor SDK type. That is what lets the live
Alpaca chain, the degraded yfinance fallback, and the synthetic backtest chain
be swapped without touching selection logic.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import date

from ajax.options.greeks import Right


@dataclass(frozen=True)
class ContractQuote:
    """One option contract, normalized across providers.

    ``greeks_source`` records where ``delta``/``iv`` came from:

    * ``alpaca``               — published by the exchange feed
    * ``bs_from_alpaca_iv``    — modelled from the feed's implied volatility
    * ``bs_from_realized_vol`` — modelled from realized vol (weakest provenance)

    This is carried all the way into the trade log so a model-derived delta is
    never silently presented as an exchange-published one.
    """

    symbol: str
    underlying: str
    right: Right
    strike: float
    expiry: date
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    open_interest: int | None = None
    volume: int | None = None
    iv: float | None = None
    delta: float | None = None
    greeks_source: str = "unknown"

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last if self.last and self.last > 0 else None

    @property
    def relative_spread(self) -> float | None:
        mid = self.mid
        if mid is None or mid <= 0 or self.bid is None or self.ask is None:
            return None
        return (self.ask - self.bid) / mid

    @property
    def abs_delta(self) -> float | None:
        """Delta magnitude. The configured band is compared against this, so a
        0.60-delta put and a 0.60-delta call are treated symmetrically."""
        return None if self.delta is None else abs(self.delta)

    def dte(self, as_of: date) -> int:
        return (self.expiry - as_of).days

    def cost(self, qty: int = 1) -> float | None:
        """Dollar cost of ``qty`` contracts at mid (100 shares per contract)."""
        mid = self.mid
        return None if mid is None else mid * 100.0 * qty


class ChainSource(abc.ABC):
    """Supplies option quotes for an underlying as of a date."""

    @abc.abstractmethod
    def get_chain(self, underlying: str, as_of: date, right: Right) -> list[ContractQuote]:
        """All quotes for ``underlying`` of the given right, as of ``as_of``."""

    def close(self) -> None:  # pragma: no cover - optional hook
        return None
