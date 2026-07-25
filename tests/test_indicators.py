"""Indicator correctness and the no-lookahead property."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ajax.signals import indicators


def series(values: list[float], start: str = "2026-01-01") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def rising(n: int = 120, start: float = 100.0, step: float = 1.0) -> pd.Series:
    return series([start + i * step for i in range(n)])


def falling(n: int = 120, start: float = 200.0, step: float = 1.0) -> pd.Series:
    return series([start - i * step for i in range(n)])


class TestRsi:
    def test_all_gains_gives_one_hundred(self):
        assert indicators.rsi(rising(40), 14).iloc[-1] == pytest.approx(100.0)

    def test_all_losses_gives_zero(self):
        assert indicators.rsi(falling(40), 14).iloc[-1] == pytest.approx(0.0)

    def test_bounded_between_zero_and_hundred(self):
        rng = np.random.default_rng(42)
        noisy = series(list(100 + rng.normal(0, 2, 200).cumsum()))
        values = indicators.rsi(noisy, 14).dropna()
        assert values.between(0, 100).all()

    def test_warmup_period_is_nan(self):
        assert indicators.rsi(rising(40), 14).iloc[:13].isna().all()


class TestMacd:
    def test_histogram_is_line_minus_signal(self):
        line, signal, hist = indicators.macd(rising(120))
        valid = hist.dropna().index
        assert np.allclose(hist[valid], (line - signal)[valid])

    def test_positive_in_an_uptrend(self):
        line, _, _ = indicators.macd(rising(120))
        assert line.iloc[-1] > 0

    def test_negative_in_a_downtrend(self):
        line, _, _ = indicators.macd(falling(120))
        assert line.iloc[-1] < 0


class TestRateOfChange:
    def test_computes_percent_change(self):
        prices = series([100, 105, 110, 121])
        assert indicators.rate_of_change(prices, 1).iloc[-1] == pytest.approx(10.0)

    def test_uses_the_correct_lookback(self):
        prices = series([100, 200, 300, 400])
        assert indicators.rate_of_change(prices, 3).iloc[-1] == pytest.approx(300.0)


class TestRelativeStrength:
    def test_zero_when_matching_the_benchmark(self):
        prices = rising(60)
        assert indicators.relative_strength(prices, prices.copy(), 21).iloc[-1] == pytest.approx(0.0)

    def test_positive_when_outperforming(self):
        fast = series([100 * (1.02**i) for i in range(60)])
        slow = series([100 * (1.01**i) for i in range(60)])
        assert indicators.relative_strength(fast, slow, 21).iloc[-1] > 0

    def test_negative_when_underperforming(self):
        fast = series([100 * (1.02**i) for i in range(60)])
        slow = series([100 * (1.01**i) for i in range(60)])
        assert indicators.relative_strength(slow, fast, 21).iloc[-1] < 0


class TestVolumeZscore:
    def test_spike_scores_high(self):
        volumes = series([1000.0] * 30 + [5000.0])
        assert indicators.volume_zscore(volumes, 20).iloc[-1] > 3

    def test_flat_volume_is_not_a_signal(self):
        volumes = series([1000.0] * 40)
        # Zero variance produces NaN rather than a spurious extreme value.
        assert pd.isna(indicators.volume_zscore(volumes, 20).iloc[-1])


class TestRsiBandedScore:
    def test_peaks_inside_the_sweet_band(self):
        values = series([60.0] * 5)
        assert indicators.rsi_banded_score(values, 50, 70, 80).iloc[-1] == pytest.approx(1.0)

    def test_penalizes_overbought(self):
        mid = indicators.rsi_banded_score(series([60.0]), 50, 70, 80).iloc[-1]
        hot = indicators.rsi_banded_score(series([95.0]), 50, 70, 80).iloc[-1]
        assert hot < mid
        assert hot < 0

    def test_stays_in_range(self):
        values = series([float(v) for v in range(0, 101, 5)])
        scored = indicators.rsi_banded_score(values, 50, 70, 80)
        assert scored.between(-1, 1).all()


class TestTrendGate:
    def test_uptrend_detected(self):
        assert indicators.trend_gate(rising(120), 20, 50).iloc[-1] == 1

    def test_downtrend_detected(self):
        assert indicators.trend_gate(falling(120), 20, 50).iloc[-1] == -1

    def test_flat_market_is_neutral(self):
        assert indicators.trend_gate(series([100.0] * 120), 20, 50).iloc[-1] == 0


class TestNoLookahead:
    """A value at index i must not change when later data is appended.

    This is the property the backtest's correctness rests on.
    """

    @pytest.mark.parametrize(
        "fn",
        [
            lambda s: indicators.rsi(s, 14),
            lambda s: indicators.macd(s)[2],
            lambda s: indicators.rate_of_change(s, 10),
            lambda s: indicators.sma(s, 20),
            lambda s: indicators.trend_gate(s, 20, 50),
        ],
    )
    def test_truncated_history_matches_full_history(self, fn):
        rng = np.random.default_rng(7)
        full = series(list(100 + rng.normal(0, 1, 200).cumsum()))
        cut = 150

        from_full = fn(full).iloc[:cut]
        from_truncated = fn(full.iloc[:cut])

        pd.testing.assert_series_equal(from_full, from_truncated, check_names=False)
