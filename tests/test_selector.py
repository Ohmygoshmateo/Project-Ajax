"""Contract selection.

The highest-value suite in the project. The selector encodes the one strategy
decision that is easy to get subtly wrong — that "cheapest" is chosen *within*
the delta band, never by leaving it — and these tests exist to make that
property impossible to regress silently.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ajax.config import OptionsConfig
from ajax.options.chain_source import ContractQuote
from ajax.options.greeks import Right
from ajax.options.selector import (
    SelectionResult,
    SkipReason,
    describe_selection,
    filter_by_dte,
    select_contract,
)

AS_OF = date(2026, 3, 2)


def make_config(**overrides) -> OptionsConfig:
    base = {
        "dte_target_min": 30,
        "dte_target_max": 45,
        "dte_hard_floor": 21,
        "delta_min": 0.55,
        "delta_max": 0.70,
        "min_open_interest": 100,
        "min_volume": 0,
        "max_relative_spread": 0.15,
        "max_premium_per_contract": 7.50,
    }
    base.update(overrides)
    return OptionsConfig(**base)


def quote(
    *,
    strike: float = 100.0,
    dte: int = 35,
    delta: float | None = 0.60,
    bid: float | None = 5.00,
    ask: float | None = 5.20,
    oi: int | None = 500,
    volume: int | None = 100,
    right: Right = Right.CALL,
    symbol: str | None = None,
) -> ContractQuote:
    expiry = AS_OF + timedelta(days=dte)
    return ContractQuote(
        symbol=symbol or f"TEST{expiry:%y%m%d}{'C' if right is Right.CALL else 'P'}{int(strike * 1000):08d}",
        underlying="TEST",
        right=right,
        strike=strike,
        expiry=expiry,
        bid=bid,
        ask=ask,
        open_interest=oi,
        volume=volume,
        delta=delta,
        greeks_source="test",
    )


class TestDteWindow:
    def test_prefers_the_target_window(self):
        cfg = make_config()
        quotes = [quote(dte=25, strike=90), quote(dte=35, strike=100), quote(dte=60, strike=110)]
        assert [q.dte(AS_OF) for q in filter_by_dte(quotes, AS_OF, cfg)] == [35]

    def test_widens_beyond_the_target_when_empty(self):
        cfg = make_config()
        quotes = [quote(dte=25), quote(dte=60)]
        got = {q.dte(AS_OF) for q in filter_by_dte(quotes, AS_OF, cfg)}
        assert got == {25, 60}

    def test_never_goes_below_the_hard_floor(self):
        cfg = make_config()
        quotes = [quote(dte=5), quote(dte=14), quote(dte=20)]
        assert filter_by_dte(quotes, AS_OF, cfg) == []

    def test_floor_is_inclusive(self):
        cfg = make_config()
        assert len(filter_by_dte([quote(dte=21)], AS_OF, cfg)) == 1

    def test_selection_rejects_everything_under_the_floor(self):
        """Even a perfect delta at a perfect price cannot buy its way under the floor."""
        cfg = make_config()
        result = select_contract([quote(dte=7, delta=0.62, bid=1.0, ask=1.02)], AS_OF, cfg)
        assert not result.selected
        assert result.skip_reason is SkipReason.NO_CONTRACT_IN_DTE_WINDOW


class TestLiquidity:
    def test_rejects_low_open_interest(self):
        cfg = make_config()
        result = select_contract([quote(oi=5)], AS_OF, cfg)
        assert result.skip_reason is SkipReason.ILLIQUID

    def test_rejects_zero_bid(self):
        cfg = make_config()
        result = select_contract([quote(bid=0.0)], AS_OF, cfg)
        assert result.skip_reason is SkipReason.ILLIQUID

    def test_rejects_wide_spreads(self):
        cfg = make_config(max_relative_spread=0.10)
        # bid 4.00 / ask 6.00 -> mid 5.00, spread 40%
        result = select_contract([quote(bid=4.00, ask=6.00)], AS_OF, cfg)
        assert result.skip_reason is SkipReason.ILLIQUID

    def test_accepts_a_tight_spread(self):
        cfg = make_config(max_relative_spread=0.10)
        result = select_contract([quote(bid=4.95, ask=5.05)], AS_OF, cfg)
        assert result.selected

    def test_missing_open_interest_is_not_disqualifying(self):
        # Alpaca chain snapshots do not publish OI; absence must not veto.
        cfg = make_config()
        assert select_contract([quote(oi=None)], AS_OF, cfg).selected


class TestDeltaBand:
    def test_selects_inside_the_band(self):
        cfg = make_config()
        result = select_contract([quote(delta=0.60)], AS_OF, cfg)
        assert result.selected
        assert result.contract.abs_delta == 0.60

    @pytest.mark.parametrize("delta", [0.20, 0.35, 0.54, 0.71, 0.90])
    def test_rejects_outside_the_band(self, delta):
        cfg = make_config()
        result = select_contract([quote(delta=delta)], AS_OF, cfg)
        assert result.skip_reason is SkipReason.NO_CONTRACT_IN_DELTA_BAND

    @pytest.mark.parametrize("delta", [0.55, 0.70])
    def test_band_edges_are_inclusive(self, delta):
        assert select_contract([quote(delta=delta)], AS_OF, make_config()).selected

    def test_puts_are_matched_on_delta_magnitude(self):
        """A -0.60 put sits in the same band as a +0.60 call."""
        cfg = make_config()
        result = select_contract([quote(delta=-0.60, right=Right.PUT)], AS_OF, cfg)
        assert result.selected
        assert result.contract.abs_delta == pytest.approx(0.60)

    def test_missing_delta_is_reported_distinctly(self):
        cfg = make_config()
        result = select_contract([quote(delta=None)], AS_OF, cfg)
        assert result.skip_reason is SkipReason.MISSING_DELTA

    def test_skip_detail_names_the_closest_available_delta(self):
        cfg = make_config()
        result = select_contract([quote(delta=0.35)], AS_OF, cfg)
        assert "0.35" in result.detail


class TestAffordabilityNeverRelaxesTheBand:
    """The core invariant. Budget pressure must never widen the delta band."""

    def test_unaffordable_in_band_is_skipped_not_downgraded(self):
        cfg = make_config(max_premium_per_contract=3.00)
        quotes = [
            quote(delta=0.60, bid=8.00, ask=8.20, strike=95),   # in band, too dear
            quote(delta=0.25, bid=0.50, ask=0.55, strike=130),  # affordable, wrong band
        ]
        result = select_contract(quotes, AS_OF, cfg)

        assert not result.selected, "must not fall back to a cheap out-of-band contract"
        assert result.skip_reason is SkipReason.UNAFFORDABLE

    def test_skip_detail_explains_the_shortfall(self):
        cfg = make_config(max_premium_per_contract=3.00)
        result = select_contract([quote(delta=0.60, bid=8.00, ask=8.20)], AS_OF, cfg)
        assert "$8.10" in result.detail or "810" in result.detail
        assert "3.00" in result.detail or "300" in result.detail

    def test_caller_supplied_cap_overrides_config(self):
        cfg = make_config(max_premium_per_contract=100.0)
        result = select_contract([quote(bid=5.0, ask=5.2)], AS_OF, cfg, max_premium=1.00)
        assert result.skip_reason is SkipReason.UNAFFORDABLE

    def test_affordable_in_band_still_selected(self):
        cfg = make_config(max_premium_per_contract=6.00)
        result = select_contract([quote(delta=0.60, bid=5.00, ask=5.20)], AS_OF, cfg)
        assert result.selected

    def test_out_of_band_cheap_contracts_never_win(self):
        """Even with many cheap out-of-band options, only the band is considered."""
        cfg = make_config()
        quotes = [quote(delta=d, bid=0.10, ask=0.12, strike=200 + i) for i, d in
                  enumerate([0.10, 0.20, 0.30, 0.40, 0.50])]
        quotes.append(quote(delta=0.60, bid=6.00, ask=6.10, strike=100))
        result = select_contract(quotes, AS_OF, cfg)
        assert result.selected
        assert result.contract.abs_delta == pytest.approx(0.60)


class TestCheapestAmongSurvivors:
    def test_picks_the_cheapest_in_band(self):
        cfg = make_config()
        quotes = [
            quote(delta=0.60, bid=6.00, ask=6.10, strike=100),
            quote(delta=0.65, bid=4.00, ask=4.10, strike=105),
            quote(delta=0.58, bid=7.00, ask=7.10, strike=95),
        ]
        result = select_contract(quotes, AS_OF, cfg)
        assert result.contract.strike == 105

    def test_ties_break_toward_the_band_midpoint(self):
        cfg = make_config()  # band 0.55-0.70, midpoint 0.625
        quotes = [
            quote(delta=0.56, bid=5.00, ask=5.10, strike=100),
            quote(delta=0.63, bid=5.00, ask=5.10, strike=101),
        ]
        result = select_contract(quotes, AS_OF, cfg)
        assert result.contract.strike == 101

    def test_selection_is_deterministic(self):
        cfg = make_config()
        quotes = [
            quote(delta=0.60, bid=5.00, ask=5.10, strike=100, symbol="B"),
            quote(delta=0.60, bid=5.00, ask=5.10, strike=100, symbol="A"),
        ]
        first = select_contract(quotes, AS_OF, cfg).contract.symbol
        second = select_contract(list(reversed(quotes)), AS_OF, cfg).contract.symbol
        assert first == second == "A"


class TestSkipReasonOrdering:
    """Each failure mode reports the stage that actually stopped it."""

    def test_empty_chain(self):
        assert select_contract([], AS_OF, make_config()).skip_reason is SkipReason.NO_CONTRACTS

    def test_dte_checked_before_liquidity(self):
        result = select_contract([quote(dte=5, oi=1)], AS_OF, make_config())
        assert result.skip_reason is SkipReason.NO_CONTRACT_IN_DTE_WINDOW

    def test_liquidity_checked_before_delta(self):
        result = select_contract([quote(oi=1, delta=0.10)], AS_OF, make_config())
        assert result.skip_reason is SkipReason.ILLIQUID

    def test_delta_checked_before_affordability(self):
        cfg = make_config(max_premium_per_contract=0.01)
        result = select_contract([quote(delta=0.10, bid=5.0, ask=5.1)], AS_OF, cfg)
        assert result.skip_reason is SkipReason.NO_CONTRACT_IN_DELTA_BAND

    def test_every_reason_has_an_explanation(self):
        for reason in SkipReason:
            assert reason.explanation


class TestSelectionResult:
    def test_selected_and_skip_are_mutually_exclusive(self):
        chosen = SelectionResult.choose(quote(), 1)
        assert chosen.selected and chosen.skip_reason is None

        skipped = SelectionResult.skip(SkipReason.ILLIQUID, 3)
        assert not skipped.selected and skipped.contract is None

    def test_describe_selection_reads_clearly(self):
        result = select_contract([quote(delta=0.60, strike=100)], AS_OF, make_config())
        text = describe_selection(result, "TEST", Right.CALL)
        assert "TEST" in text and "CALL" in text and "0.60" in text

    def test_describe_skip_includes_the_reason(self):
        result = select_contract([], AS_OF, make_config())
        assert "SKIPPED" in describe_selection(result, "TEST", Right.CALL)


class TestContractQuote:
    def test_mid_from_bid_and_ask(self):
        assert quote(bid=4.0, ask=6.0).mid == pytest.approx(5.0)

    def test_mid_falls_back_to_last(self):
        q = ContractQuote(
            symbol="X", underlying="T", right=Right.CALL, strike=100,
            expiry=AS_OF + timedelta(days=30), bid=None, ask=None, last=3.25,
        )
        assert q.mid == pytest.approx(3.25)

    def test_cost_is_premium_times_one_hundred(self):
        assert quote(bid=4.0, ask=6.0).cost() == pytest.approx(500.0)
        assert quote(bid=4.0, ask=6.0).cost(3) == pytest.approx(1500.0)

    def test_relative_spread(self):
        assert quote(bid=4.0, ask=6.0).relative_spread == pytest.approx(0.4)
