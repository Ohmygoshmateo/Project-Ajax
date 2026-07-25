"""Graduation criterion and the manual live-trading gate.

Two things are under test here, and the second matters more than the first:

1. the win-rate/sample-size arithmetic, and
2. that **nothing in the automated path can reach live trading**.

The second set of tests is structural: it inspects imports and call graphs rather
than behaviour, because the guarantee being protected is "this code path does not
exist", which behavioural tests cannot demonstrate.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ajax.agent.graduation import evaluate
from ajax.config import GraduationConfig

TODAY = date(2026, 3, 20)


def cfg(**overrides) -> GraduationConfig:
    base = {
        "min_win_rate": 0.80,
        "consecutive_weeks": 2,
        "min_closed_trades": 20,
        "mode": "strict_both_weeks",
    }
    base.update(overrides)
    return GraduationConfig(**base)


def trades(*, wins: int, losses: int, day_offset: int = 0) -> list[dict]:
    exit_date = (TODAY - timedelta(days=day_offset)).isoformat()
    out = [{"pnl": 100.0, "exit_date": exit_date} for _ in range(wins)]
    out += [{"pnl": -50.0, "exit_date": exit_date} for _ in range(losses)]
    return out


def spread_over_both_weeks(*, wins_per_week: int, losses_per_week: int) -> list[dict]:
    out: list[dict] = []
    for offset in (1, 8):  # one day inside each of the two 7-day windows
        out += trades(wins=wins_per_week, losses=losses_per_week, day_offset=offset)
    return out


class TestSampleSizeFloor:
    def test_small_sample_fails_even_at_a_perfect_win_rate(self):
        """4-for-4 is 100%, and it is still not evidence."""
        result = evaluate(spread_over_both_weeks(wins_per_week=2, losses_per_week=0), cfg(),
                          as_of=TODAY)
        assert not result.passed
        assert not result.sample_sufficient
        assert result.overall_win_rate == 1.0

    def test_headline_says_insufficient_rather_than_showing_a_flattering_rate(self):
        result = evaluate(spread_over_both_weeks(wins_per_week=2, losses_per_week=0), cfg(),
                          as_of=TODAY)
        headline = result.headline()
        assert "INSUFFICIENT SAMPLE" in headline
        assert "PASS" not in headline

    def test_headline_always_includes_the_trade_count(self):
        result = evaluate(spread_over_both_weeks(wins_per_week=2, losses_per_week=0), cfg(),
                          as_of=TODAY)
        assert "4" in result.headline()

    def test_remaining_trade_count_is_reported(self):
        result = evaluate(trades(wins=6, losses=0, day_offset=1), cfg(), as_of=TODAY)
        assert result.trades_remaining == 14

    def test_reason_explains_the_floor(self):
        result = evaluate(trades(wins=3, losses=0, day_offset=1), cfg(), as_of=TODAY)
        assert any("noise" in r or "short of" in r for r in result.reasons)


class TestWinRateThreshold:
    def test_passes_with_enough_trades_and_a_high_rate(self):
        result = evaluate(spread_over_both_weeks(wins_per_week=9, losses_per_week=1), cfg(),
                          as_of=TODAY)
        assert result.sample_sufficient
        assert result.passed
        assert "PASS" in result.headline()

    def test_fails_just_below_the_threshold(self):
        # 7/10 per week = 70%, under the 80% bar.
        result = evaluate(spread_over_both_weeks(wins_per_week=7, losses_per_week=3), cfg(),
                          as_of=TODAY)
        assert not result.passed
        assert any("below the 80% threshold" in r for r in result.reasons)

    def test_both_weeks_must_clear_the_bar(self):
        good = trades(wins=10, losses=0, day_offset=1)
        bad = trades(wins=5, losses=5, day_offset=8)
        result = evaluate(good + bad, cfg(), as_of=TODAY)
        assert not result.passed

    def test_an_empty_week_fails(self):
        result = evaluate(trades(wins=20, losses=0, day_offset=1), cfg(), as_of=TODAY)
        assert not result.passed
        assert any("no closed trades" in r for r in result.reasons)


class TestBlendedMode:
    def test_blended_uses_a_single_window(self):
        result = evaluate(
            spread_over_both_weeks(wins_per_week=9, losses_per_week=1),
            cfg(mode="blended_14d"),
            as_of=TODAY,
        )
        assert len(result.windows) == 1
        assert result.passed

    def test_blended_still_enforces_the_trade_floor(self):
        result = evaluate(
            spread_over_both_weeks(wins_per_week=2, losses_per_week=0),
            cfg(mode="blended_14d"),
            as_of=TODAY,
        )
        assert not result.passed


class TestEdgeCases:
    def test_no_trades_at_all(self):
        result = evaluate([], cfg(), as_of=TODAY)
        assert not result.passed
        assert result.total_closed == 0
        assert result.overall_win_rate is None
        assert "0" in result.headline()

    def test_breakeven_trade_is_not_a_win(self):
        result = evaluate([{"pnl": 0.0, "exit_date": TODAY.isoformat()}], cfg(), as_of=TODAY)
        assert result.overall_win_rate == 0.0

    def test_trades_outside_the_windows_still_count_toward_the_floor(self):
        old = trades(wins=30, losses=0, day_offset=200)
        result = evaluate(old, cfg(), as_of=TODAY)
        assert result.sample_sufficient
        assert not result.passed  # but the recent windows are empty


class TestGraduationIsReadOnly:
    """Passing the check must not itself change anything."""

    def test_module_cannot_place_orders_or_switch_modes(self):
        import ajax.agent.graduation as module

        source = open(module.__file__).read()
        for forbidden in ("submit_order", "TradingClient", "trading_mode", "paper=False"):
            assert forbidden not in source, (
                f"graduation.py references {forbidden!r}; it must remain a read-only report"
            )


class TestAutomationCannotReachLiveTrading:
    """Structural guarantees. These protect the single most important property."""

    def test_runner_imports_the_paper_client_directly(self):
        import ajax.agent.runner as runner

        source = open(runner.__file__).read()
        assert "get_paper_trading_client" in source

    @pytest.mark.parametrize("module_name", ["ajax.agent.runner", "ajax.agent.scheduler"])
    def test_automation_never_references_the_live_gate(self, module_name):
        import importlib

        module = importlib.import_module(module_name)
        source = open(module.__file__).read()
        for forbidden in ("live.gate", "_get_live_trading_client", "paper=False"):
            assert forbidden not in source, (
                f"{module_name} references {forbidden!r} — automation must have no route "
                f"to a live endpoint"
            )

    def test_live_client_factory_requires_the_acknowledgement_token(self):
        from ajax.broker.alpaca_client import LiveTradingRefused, _get_live_trading_client

        for attempt in ("", "yes", "I ACCEPT REAL MONEY RISK", "true"):
            with pytest.raises(LiveTradingRefused):
                _get_live_trading_client(attempt)

    def test_paper_client_factory_hardcodes_paper_true(self):
        import ajax.broker.alpaca_client as module

        source = open(module.__file__).read()
        assert "paper=True" in source

    def test_gate_denies_without_the_risk_flag(self):
        from ajax.live.gate import CONFIRMATION_PHRASE, evaluate_gate

        status = evaluate(spread_over_both_weeks(wins_per_week=9, losses_per_week=1), cfg(),
                          as_of=TODAY)
        assert status.passed

        decision = evaluate_gate(status, risk_flag=False, phrase=CONFIRMATION_PHRASE)
        assert not decision.allowed
        assert any("flag" in r for r in decision.reasons)

    def test_gate_denies_without_the_exact_phrase(self):
        from ajax.live.gate import evaluate_gate

        status = evaluate(spread_over_both_weeks(wins_per_week=9, losses_per_week=1), cfg(),
                          as_of=TODAY)
        decision = evaluate_gate(status, risk_flag=True, phrase="i accept the risk")
        assert not decision.allowed

    def test_gate_denies_on_an_insufficient_sample_even_with_everything_else_right(self):
        from ajax.live.gate import CONFIRMATION_PHRASE, evaluate_gate

        status = evaluate(spread_over_both_weeks(wins_per_week=2, losses_per_week=0), cfg(),
                          as_of=TODAY)
        decision = evaluate_gate(status, risk_flag=True, phrase=CONFIRMATION_PHRASE)
        assert not decision.allowed

    def test_gate_allows_only_when_every_condition_holds(self):
        from ajax.live.gate import CONFIRMATION_PHRASE, evaluate_gate

        status = evaluate(spread_over_both_weeks(wins_per_week=9, losses_per_week=1), cfg(),
                          as_of=TODAY)
        decision = evaluate_gate(status, risk_flag=True, phrase=CONFIRMATION_PHRASE)
        assert decision.allowed
        assert decision.reasons == []

    def test_v1_does_not_implement_live_execution(self):
        """The gate records an acknowledgement; it does not route orders."""
        import ajax.live.gate as gate

        source = open(gate.__file__).read()
        assert "submit_order" not in source
        assert "_get_live_trading_client" not in source
