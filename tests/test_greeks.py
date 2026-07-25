"""Black-Scholes correctness.

These carry the densest coverage in the project: the math is pure, deterministic,
and wrong answers here would silently corrupt both strike selection and every
backtest price.
"""

from __future__ import annotations

import math

import pytest

from ajax.options.greeks import (
    Right,
    bs_delta,
    bs_greeks,
    bs_price,
    implied_volatility,
    strike_for_delta,
    year_fraction,
)


class TestKnownValues:
    def test_textbook_atm_call(self):
        # S=100, K=100, T=1, r=5%, sigma=20% -> 10.4506 (standard reference case)
        price = bs_price(100, 100, 1.0, 0.05, 0.20, Right.CALL)
        assert price == pytest.approx(10.4506, abs=1e-3)

    def test_textbook_atm_put(self):
        price = bs_price(100, 100, 1.0, 0.05, 0.20, Right.PUT)
        assert price == pytest.approx(5.5735, abs=1e-3)

    def test_atm_call_delta_slightly_above_half(self):
        # With positive drift, an ATM call's delta sits just above 0.5.
        delta = bs_delta(100, 100, 1.0, 0.05, 0.20, Right.CALL)
        assert 0.5 < delta < 0.7
        assert delta == pytest.approx(0.6368, abs=1e-3)


class TestPutCallParity:
    @pytest.mark.parametrize(
        ("spot", "strike", "tenor", "rate", "sigma"),
        [
            (100, 100, 1.0, 0.05, 0.20),
            (100, 90, 0.5, 0.03, 0.35),
            (50, 60, 0.25, 0.01, 0.45),
            (250, 240, 0.1, 0.04, 0.15),
        ],
    )
    def test_parity_holds(self, spot, strike, tenor, rate, sigma):
        call = bs_price(spot, strike, tenor, rate, sigma, Right.CALL)
        put = bs_price(spot, strike, tenor, rate, sigma, Right.PUT)
        # C - P = S - K*e^{-rT}
        assert call - put == pytest.approx(spot - strike * math.exp(-rate * tenor), abs=1e-8)

    def test_delta_parity(self):
        # delta_call - delta_put = 1 when there is no dividend yield.
        call = bs_delta(100, 105, 0.5, 0.03, 0.25, Right.CALL)
        put = bs_delta(100, 105, 0.5, 0.03, 0.25, Right.PUT)
        assert call - put == pytest.approx(1.0, abs=1e-9)


class TestDeltaBounds:
    @pytest.mark.parametrize("strike", [50, 80, 100, 120, 200])
    def test_call_delta_in_unit_interval(self, strike):
        assert 0.0 <= bs_delta(100, strike, 0.5, 0.04, 0.3, Right.CALL) <= 1.0

    @pytest.mark.parametrize("strike", [50, 80, 100, 120, 200])
    def test_put_delta_in_negative_unit_interval(self, strike):
        assert -1.0 <= bs_delta(100, strike, 0.5, 0.04, 0.3, Right.PUT) <= 0.0

    def test_deep_itm_call_delta_approaches_one(self):
        assert bs_delta(200, 50, 0.5, 0.04, 0.2, Right.CALL) == pytest.approx(1.0, abs=1e-3)

    def test_deep_otm_call_delta_approaches_zero(self):
        assert bs_delta(50, 200, 0.5, 0.04, 0.2, Right.CALL) == pytest.approx(0.0, abs=1e-3)

    def test_delta_is_monotonic_in_spot(self):
        deltas = [bs_delta(s, 100, 0.5, 0.04, 0.3, Right.CALL) for s in range(60, 160, 10)]
        assert deltas == sorted(deltas)


class TestDegenerateInputs:
    def test_zero_tenor_returns_intrinsic_call(self):
        assert bs_price(120, 100, 0.0, 0.05, 0.2, Right.CALL) == pytest.approx(20.0)
        assert bs_price(80, 100, 0.0, 0.05, 0.2, Right.CALL) == pytest.approx(0.0)

    def test_zero_tenor_returns_intrinsic_put(self):
        assert bs_price(80, 100, 0.0, 0.05, 0.2, Right.PUT) == pytest.approx(20.0)
        assert bs_price(120, 100, 0.0, 0.05, 0.2, Right.PUT) == pytest.approx(0.0)

    def test_zero_vol_returns_intrinsic(self):
        assert bs_price(120, 100, 1.0, 0.0, 0.0, Right.CALL) == pytest.approx(20.0)

    def test_zero_tenor_delta_is_a_step_function(self):
        assert bs_delta(120, 100, 0.0, 0.05, 0.2, Right.CALL) == 1.0
        assert bs_delta(80, 100, 0.0, 0.05, 0.2, Right.CALL) == 0.0
        assert bs_delta(80, 100, 0.0, 0.05, 0.2, Right.PUT) == -1.0

    def test_zero_tenor_greeks_do_not_divide_by_zero(self):
        greeks = bs_greeks(100, 100, 0.0, 0.05, 0.2, Right.CALL)
        assert greeks.gamma == 0.0
        assert greeks.vega == 0.0
        assert greeks.theta == 0.0

    @pytest.mark.parametrize(("spot", "strike"), [(0, 100), (100, 0), (-5, 100)])
    def test_nonpositive_spot_or_strike_rejected(self, spot, strike):
        with pytest.raises(ValueError):
            bs_price(spot, strike, 1.0, 0.05, 0.2, Right.CALL)


class TestGreeks:
    def test_gamma_and_vega_are_positive(self):
        greeks = bs_greeks(100, 100, 0.5, 0.04, 0.25, Right.CALL)
        assert greeks.gamma > 0
        assert greeks.vega > 0

    def test_long_option_theta_is_negative(self):
        for right in (Right.CALL, Right.PUT):
            assert bs_greeks(100, 100, 0.5, 0.04, 0.25, right).theta < 0

    def test_gamma_is_identical_for_calls_and_puts(self):
        call = bs_greeks(100, 105, 0.4, 0.03, 0.3, Right.CALL)
        put = bs_greeks(100, 105, 0.4, 0.03, 0.3, Right.PUT)
        assert call.gamma == pytest.approx(put.gamma)
        assert call.vega == pytest.approx(put.vega)

    def test_gamma_peaks_near_the_money(self):
        atm = bs_greeks(100, 100, 0.5, 0.04, 0.25, Right.CALL).gamma
        otm = bs_greeks(100, 150, 0.5, 0.04, 0.25, Right.CALL).gamma
        itm = bs_greeks(100, 50, 0.5, 0.04, 0.25, Right.CALL).gamma
        assert atm > otm
        assert atm > itm

    def test_delta_matches_a_numerical_derivative(self):
        spot, bump = 100.0, 1e-4
        up = bs_price(spot + bump, 100, 0.5, 0.04, 0.3, Right.CALL)
        down = bs_price(spot - bump, 100, 0.5, 0.04, 0.3, Right.CALL)
        numerical = (up - down) / (2 * bump)
        assert bs_delta(spot, 100, 0.5, 0.04, 0.3, Right.CALL) == pytest.approx(
            numerical, abs=1e-6
        )


class TestImpliedVolatility:
    def test_round_trips_through_price(self):
        sigma = 0.2734
        price = bs_price(100, 105, 0.5, 0.04, sigma, Right.CALL)
        assert implied_volatility(price, 100, 105, 0.5, 0.04, Right.CALL) == pytest.approx(
            sigma, abs=1e-5
        )

    def test_round_trips_for_puts(self):
        sigma = 0.41
        price = bs_price(80, 95, 0.3, 0.02, sigma, Right.PUT)
        assert implied_volatility(price, 80, 95, 0.3, 0.02, Right.PUT) == pytest.approx(
            sigma, abs=1e-5
        )

    def test_below_intrinsic_returns_none(self):
        # An arbitrage-violating quote has no solution; the selector should skip it.
        assert implied_volatility(5.0, 120, 100, 0.5, 0.0, Right.CALL) is None

    def test_zero_tenor_returns_none(self):
        assert implied_volatility(5.0, 100, 100, 0.0, 0.04, Right.CALL) is None

    def test_nonpositive_price_returns_none(self):
        assert implied_volatility(0.0, 100, 100, 0.5, 0.04, Right.CALL) is None


class TestStrikeForDelta:
    @pytest.mark.parametrize("target", [0.30, 0.45, 0.55, 0.60, 0.70])
    def test_inverts_delta_correctly(self, target):
        strike = strike_for_delta(target, 100, 0.1, 0.04, 0.30, Right.CALL)
        recovered = bs_delta(100, strike, 0.1, 0.04, 0.30, Right.CALL)
        assert recovered == pytest.approx(target, abs=1e-6)

    def test_higher_delta_means_lower_strike_for_calls(self):
        low = strike_for_delta(0.30, 100, 0.1, 0.04, 0.3, Right.CALL)
        high = strike_for_delta(0.70, 100, 0.1, 0.04, 0.3, Right.CALL)
        assert high < low  # higher delta == deeper ITM == cheaper strike

    @pytest.mark.parametrize("target", [0.0, 1.0, -0.1, 1.5])
    def test_rejects_out_of_range_targets(self, target):
        with pytest.raises(ValueError):
            strike_for_delta(target, 100, 0.1, 0.04, 0.3, Right.CALL)


class TestYearFraction:
    def test_converts_days_to_years(self):
        assert year_fraction(365) == pytest.approx(1.0)
        assert year_fraction(30) == pytest.approx(30 / 365)

    def test_negative_days_floor_at_zero(self):
        assert year_fraction(-5) == 0.0
