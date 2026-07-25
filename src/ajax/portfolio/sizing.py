"""Position sizing.

For a long option the entire premium is at risk, so the risk budget *is* the
maximum spend. There is no stop-loss that can do better than "we paid $X and it
expired worthless", which is why sizing here is deliberately blunt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ajax.config import AccountConfig, OptionsConfig
from ajax.options.chain_source import ContractQuote

CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class SizingResult:
    qty: int
    cost: float
    commission: float
    risk_dollars: float
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.qty >= 1

    @property
    def total_outlay(self) -> float:
        return self.cost + self.commission


def risk_budget(account: AccountConfig, equity: float | None = None) -> float:
    """Dollars at risk for one position."""
    eq = account.equity if equity is None else equity
    return max(eq, 0.0) * account.risk_pct_per_trade


def effective_premium_cap(
    account: AccountConfig, options: OptionsConfig, equity: float | None = None
) -> float:
    """Maximum premium per share this account may pay for one contract.

    The binding constraint is whichever is tighter: the configured cap, or what
    the risk budget can actually buy. Returning the tighter of the two keeps the
    selector's affordability veto consistent with real account size instead of
    letting a generous config quietly overspend a small account.
    """
    budget_cap = risk_budget(account, equity) / CONTRACT_MULTIPLIER
    return min(options.max_premium_per_contract, budget_cap)


def size_position(
    contract: ContractQuote,
    account: AccountConfig,
    *,
    equity: float | None = None,
) -> SizingResult:
    """How many contracts to buy, or a zero-quantity result explaining why not."""
    budget = risk_budget(account, equity)
    premium = contract.mid

    if premium is None or premium <= 0:
        return SizingResult(0, 0.0, 0.0, budget, "contract has no usable mid price")

    per_contract = premium * CONTRACT_MULTIPLIER
    commission = account.commission_per_contract

    qty = int(math.floor(budget / (per_contract + commission)))
    if qty < 1:
        return SizingResult(
            0,
            0.0,
            0.0,
            budget,
            f"one contract costs ${per_contract:.0f} + ${commission:.2f} commission, "
            f"exceeding the ${budget:.0f} risk budget",
        )

    return SizingResult(
        qty=qty,
        cost=per_contract * qty,
        commission=commission * qty,
        risk_dollars=budget,
    )
