"""Transaction cost modelling.

Options are not equities: spreads run several percent of mid rather than a
penny, and ignoring them is one of the largest single sources of backtest
optimism. Historical NBBO is not available at any free price, so spreads are
modelled by bucket — an approximation that is disclosed in every report rather
than assumed away.

Convention: buys fill at the ask, sells fill at the bid.
"""

from __future__ import annotations

from dataclasses import dataclass

from ajax.config import AccountConfig, BacktestConfig


@dataclass(frozen=True)
class Fill:
    price: float
    mid: float
    slippage: float
    commission: float

    @property
    def total_per_contract(self) -> float:
        return self.price * 100.0 + self.commission


def spread_fraction(dte: int, cfg: BacktestConfig) -> float:
    """Modelled bid-ask spread as a fraction of mid.

    Widens for short-dated contracts, which trade thinner.
    """
    model = cfg.spread_pct_of_mid
    return model.wide_dte if dte < 28 else model.default


def buy_fill(mid: float, dte: int, backtest: BacktestConfig, account: AccountConfig) -> Fill:
    """Fill a buy at the modelled ask."""
    half = spread_fraction(dte, backtest) / 2.0
    price = mid * (1.0 + half)
    return Fill(
        price=price,
        mid=mid,
        slippage=(price - mid) * 100.0,
        commission=account.commission_per_contract,
    )


def sell_fill(mid: float, dte: int, backtest: BacktestConfig, account: AccountConfig) -> Fill:
    """Fill a sell at the modelled bid, floored at zero."""
    half = spread_fraction(dte, backtest) / 2.0
    price = max(mid * (1.0 - half), 0.0)
    return Fill(
        price=price,
        mid=mid,
        slippage=(mid - price) * 100.0,
        commission=account.commission_per_contract,
    )


def round_trip_pnl(
    entry: Fill, exit_: Fill, qty: int
) -> tuple[float, float]:
    """Net P&L in dollars and as a percent of cost basis."""
    gross = (exit_.price - entry.price) * 100.0 * qty
    commissions = (entry.commission + exit_.commission) * qty
    pnl = gross - commissions
    basis = entry.price * 100.0 * qty
    return pnl, (pnl / basis * 100.0 if basis > 0 else 0.0)
