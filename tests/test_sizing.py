"""Position sizing and slot allocation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ajax.config import AccountConfig, OptionsConfig
from ajax.options.chain_source import ContractQuote
from ajax.options.greeks import Right
from ajax.portfolio.allocator import allocate, free_slots
from ajax.portfolio.sizing import effective_premium_cap, risk_budget, size_position
from ajax.signals.labels import Candidate, Label

AS_OF = date(2026, 3, 2)


def account(**overrides) -> AccountConfig:
    base = {
        "equity": 5000.0,
        "risk_pct_per_trade": 0.15,
        "max_concurrent_positions": 2,
        "commission_per_contract": 0.65,
    }
    base.update(overrides)
    return AccountConfig(**base)


def contract(premium: float) -> ContractQuote:
    return ContractQuote(
        symbol="TEST", underlying="TEST", right=Right.CALL, strike=100.0,
        expiry=AS_OF + timedelta(days=35), bid=premium - 0.05, ask=premium + 0.05,
    )


def candidate(ticker: str, composite: float, label: Label = Label.BUY_CALL) -> Candidate:
    return Candidate(ticker, label, composite, 1, 100.0, 1 if composite > 0 else -1)


class TestRiskBudget:
    def test_budget_is_equity_times_risk_pct(self):
        assert risk_budget(account()) == pytest.approx(750.0)

    def test_equity_override_is_respected(self):
        assert risk_budget(account(), equity=10_000) == pytest.approx(1500.0)


class TestEffectivePremiumCap:
    def test_takes_the_tighter_of_config_and_budget(self):
        # $750 budget / 100 = $7.50 per share; config cap of $20 is looser.
        opts = OptionsConfig(max_premium_per_contract=20.0)
        assert effective_premium_cap(account(), opts) == pytest.approx(7.50)

    def test_config_cap_binds_when_it_is_tighter(self):
        opts = OptionsConfig(max_premium_per_contract=3.0)
        assert effective_premium_cap(account(), opts) == pytest.approx(3.0)

    def test_a_small_account_cannot_be_overspent_by_a_generous_config(self):
        opts = OptionsConfig(max_premium_per_contract=50.0)
        cap = effective_premium_cap(account(equity=1000, risk_pct_per_trade=0.05), opts)
        assert cap == pytest.approx(0.50)


class TestSizePosition:
    def test_buys_what_the_budget_allows(self):
        result = size_position(contract(2.00), account())  # $200/contract, $750 budget
        assert result.qty == 3
        assert result.ok

    def test_single_contract_when_budget_barely_covers_it(self):
        result = size_position(contract(7.00), account())  # $700 + commission
        assert result.qty == 1

    def test_rejects_when_one_contract_exceeds_the_budget(self):
        result = size_position(contract(9.00), account())  # $900 > $750
        assert not result.ok
        assert result.qty == 0
        assert "risk budget" in result.reason

    def test_commission_is_included_in_affordability(self):
        # $749.50 premium + $0.65 commission tips just past the $750 budget.
        result = size_position(contract(7.495), account())
        assert result.qty == 0

    def test_rejects_a_contract_with_no_price(self):
        quote = ContractQuote(
            symbol="X", underlying="T", right=Right.CALL, strike=100.0,
            expiry=AS_OF + timedelta(days=30), bid=None, ask=None, last=None,
        )
        assert not size_position(quote, account()).ok

    def test_total_outlay_includes_commission(self):
        result = size_position(contract(2.00), account())
        assert result.total_outlay == pytest.approx(result.cost + result.commission)


class TestFreeSlots:
    @pytest.mark.parametrize(("open_count", "expected"), [(0, 2), (1, 1), (2, 0), (5, 0)])
    def test_slot_arithmetic(self, open_count, expected):
        assert free_slots(account(), open_count) == expected


class TestAllocator:
    def test_fills_slots_by_conviction(self):
        candidates = [
            candidate("LOW", 0.9),
            candidate("HIGH", 2.5),
            candidate("MID", 1.5),
        ]
        plan = allocate(candidates, account(), open_positions=0)
        assert [c.ticker for c in plan.selected] == ["HIGH", "MID"]

    def test_conviction_uses_magnitude_so_puts_compete_fairly(self):
        candidates = [candidate("UP", 1.0), candidate("DOWN", -2.5, Label.BUY_PUT)]
        plan = allocate(candidates, account(max_concurrent_positions=1))
        assert [c.ticker for c in plan.selected] == ["DOWN"]

    def test_respects_occupied_slots(self):
        plan = allocate([candidate("A", 2.0), candidate("B", 1.0)], account(), open_positions=1)
        assert len(plan.selected) == 1
        assert len(plan.skipped_for_slots) == 1

    def test_never_doubles_up_on_one_underlying(self):
        candidates = [candidate("AAPL", 2.0), candidate("AAPL", 1.9)]
        plan = allocate(candidates, account())
        assert len(plan.selected) == 1
        assert len(plan.skipped_for_duplicate) == 1

    def test_skips_underlyings_already_held(self):
        plan = allocate(
            [candidate("AAPL", 2.0), candidate("MSFT", 1.0)],
            account(),
            open_positions=1,
            open_underlyings={"AAPL"},
        )
        assert [c.ticker for c in plan.selected] == ["MSFT"]

    def test_ignores_non_actionable_candidates(self):
        watch = Candidate("X", Label.WATCH, 3.0, 1, 100.0, 1, reason="below threshold")
        plan = allocate([watch, candidate("Y", 1.0)], account())
        assert [c.ticker for c in plan.selected] == ["Y"]

    def test_no_free_slots_selects_nothing(self):
        plan = allocate([candidate("A", 2.0)], account(), open_positions=2)
        assert plan.selected == []

    def test_ordering_is_deterministic_on_ties(self):
        candidates = [candidate("BBB", 1.0), candidate("AAA", 1.0)]
        plan = allocate(candidates, account(max_concurrent_positions=1))
        assert plan.selected[0].ticker == "AAA"
