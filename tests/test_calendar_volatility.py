"""Trading-day arithmetic and volatility estimation."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ajax.options.volatility import (
    TRADING_DAYS_PER_YEAR,
    apply_vol_haircut,
    entry_volatility,
    exit_volatility,
    realized_volatility,
)
from ajax.utils import calendar


class TestTradingDays:
    def test_weekends_are_excluded(self):
        # 2026-03-07 and 03-08 are a Saturday and Sunday.
        days = calendar.trading_days(date(2026, 3, 6), date(2026, 3, 9))
        assert date(2026, 3, 7) not in days
        assert date(2026, 3, 8) not in days

    def test_next_trading_day_skips_the_weekend(self):
        # Friday -> Monday
        assert calendar.next_trading_day(date(2026, 3, 6)) == date(2026, 3, 9)

    def test_five_trading_days_is_about_a_week(self):
        start = date(2026, 3, 2)  # Monday
        assert calendar.add_trading_days(start, 5) == date(2026, 3, 9)

    def test_counting_is_symmetric_with_adding(self):
        start = date(2026, 3, 2)
        for n in (1, 3, 5, 10):
            assert calendar.trading_days_between(start, calendar.add_trading_days(start, n)) == n

    def test_same_day_span_is_zero(self):
        assert calendar.trading_days_between(date(2026, 3, 2), date(2026, 3, 2)) == 0

    def test_reversed_range_is_empty(self):
        assert calendar.trading_days(date(2026, 3, 10), date(2026, 3, 1)) == []

    def test_n_must_be_positive(self):
        with pytest.raises(ValueError):
            calendar.next_trading_day(date(2026, 3, 2), 0)

    def test_holidays_are_skipped_when_the_calendar_is_exact(self):
        if not calendar.calendar_is_exact():
            pytest.skip("pandas_market_calendars not installed")
        # US markets are closed on Christmas Day 2026 (a Friday).
        assert date(2026, 12, 25) not in calendar.trading_days(
            date(2026, 12, 24), date(2026, 12, 28)
        )


class TestToDate:
    @pytest.mark.parametrize(
        "value",
        ["2026-03-02", date(2026, 3, 2), pd.Timestamp("2026-03-02"), "2026-03-02T10:00:00"],
    )
    def test_coerces_common_shapes(self, value):
        assert calendar.to_date(value) == date(2026, 3, 2)


class TestRealizedVolatility:
    def test_constant_prices_have_no_volatility(self):
        closes = pd.Series([100.0] * 40)
        assert realized_volatility(closes, 21) is None  # zero std -> no estimate

    def test_annualizes_daily_moves(self):
        rng = np.random.default_rng(11)
        daily_sigma = 0.01
        returns = rng.normal(0, daily_sigma, 400)
        closes = pd.Series(100 * np.exp(returns.cumsum()))

        estimated = realized_volatility(closes, 250)
        expected = daily_sigma * np.sqrt(TRADING_DAYS_PER_YEAR)
        assert estimated == pytest.approx(expected, rel=0.25)

    def test_insufficient_history_returns_none(self):
        assert realized_volatility(pd.Series([100.0, 101.0]), 21) is None

    def test_a_more_volatile_series_scores_higher(self):
        rng = np.random.default_rng(3)
        calm = pd.Series(100 * np.exp(rng.normal(0, 0.005, 100).cumsum()))
        wild = pd.Series(100 * np.exp(rng.normal(0, 0.03, 100).cumsum()))
        assert realized_volatility(wild, 50) > realized_volatility(calm, 50)


class TestVolHaircut:
    def test_adds_points_not_percent(self):
        assert apply_vol_haircut(0.25, 3.5) == pytest.approx(0.285)

    def test_zero_haircut_is_a_no_op(self):
        assert apply_vol_haircut(0.25, 0.0) == pytest.approx(0.25)

    def test_result_stays_positive(self):
        assert apply_vol_haircut(0.01, -50.0) > 0

    def test_entry_volatility_exceeds_exit_volatility(self):
        """The haircut must make entries more expensive than exits.

        This is the whole point: pricing entries at bare realized vol buys the
        options too cheaply and flatters every downstream result.
        """
        rng = np.random.default_rng(5)
        closes = pd.Series(100 * np.exp(rng.normal(0, 0.015, 120).cumsum()))

        entry_sigma, source = entry_volatility(closes, window=21, haircut_points=3.5)
        exit_sigma = exit_volatility(closes, window=21)

        assert entry_sigma > exit_sigma
        assert source == "realized+haircut"

    def test_falls_back_when_history_is_too_short(self):
        sigma, source = entry_volatility(pd.Series([100.0, 101.0]), haircut_points=3.5)
        assert source == "fallback+haircut"
        assert sigma > 0
