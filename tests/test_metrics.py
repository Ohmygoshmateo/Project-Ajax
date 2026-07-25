"""Performance metrics and cost modelling."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ajax.backtest import costs
from ajax.backtest.engine import BacktestTrade
from ajax.backtest.metrics import compute_metrics, equity_curve, max_drawdown
from ajax.config import AccountConfig, BacktestConfig
from ajax.options.greeks import Right

START = date(2026, 1, 5)


def trade(pnl: float, *, pnl_pct: float | None = None, day: int = 0,
          ticker: str = "TEST", source: str = "alpaca_bars") -> BacktestTrade:
    exit_date = START + timedelta(days=day)
    return BacktestTrade(
        ticker=ticker, right=Right.CALL, occ_symbol="X", strike=100.0,
        expiry=exit_date + timedelta(days=30), signal_date=START, entry_date=START,
        exit_date=exit_date, dte_at_entry=35, qty=1, entry_price=5.0, exit_price=6.0,
        entry_source=source, exit_source=source, entry_underlying=100.0,
        exit_underlying=105.0, delta_at_entry=0.6, sigma_at_entry=0.3, composite=1.5,
        pnl=pnl, pnl_pct=pnl_pct if pnl_pct is not None else pnl / 5.0,
        commission=1.30,
    )


class TestWinRateAlwaysCarriesSampleSize:
    def test_display_includes_the_trade_count(self):
        metrics = compute_metrics([trade(100), trade(-50), trade(100)])
        assert metrics.win_rate == pytest.approx(2 / 3)
        assert "2/3" in metrics.win_rate_display

    def test_zero_trades_reports_no_rate(self):
        metrics = compute_metrics([])
        assert metrics.win_rate is None
        assert "0 trades" in metrics.win_rate_display

    def test_small_samples_carry_a_warning(self):
        assert compute_metrics([trade(100)] * 5).sample_warning is not None

    def test_large_samples_do_not(self):
        assert compute_metrics([trade(100)] * 25).sample_warning is None

    def test_warning_quantifies_the_sensitivity(self):
        warning = compute_metrics([trade(100)] * 4).sample_warning
        assert "25%" in warning  # one trade out of four moves the rate 25 points


class TestBasicMetrics:
    def test_counts_wins_and_losses(self):
        metrics = compute_metrics([trade(100), trade(200), trade(-50)])
        assert (metrics.wins, metrics.losses, metrics.trade_count) == (2, 1, 3)

    def test_breakeven_counts_as_a_loss(self):
        assert compute_metrics([trade(0.0)]).wins == 0

    def test_totals_and_averages(self):
        metrics = compute_metrics([trade(100), trade(-40)])
        assert metrics.total_pnl == pytest.approx(60.0)
        assert metrics.avg_pnl == pytest.approx(30.0)

    def test_avg_win_and_loss_are_separate(self):
        metrics = compute_metrics([trade(100), trade(200), trade(-60)])
        assert metrics.avg_win == pytest.approx(150.0)
        assert metrics.avg_loss == pytest.approx(-60.0)

    def test_best_and_worst(self):
        metrics = compute_metrics([trade(100), trade(-250), trade(75)])
        assert metrics.best_trade == pytest.approx(100.0)
        assert metrics.worst_trade == pytest.approx(-250.0)


class TestProfitFactorAndExpectancy:
    def test_profit_factor_is_gross_win_over_gross_loss(self):
        metrics = compute_metrics([trade(200), trade(100), trade(-100)])
        assert metrics.profit_factor == pytest.approx(3.0)

    def test_profit_factor_is_none_with_no_losses(self):
        assert compute_metrics([trade(100), trade(50)]).profit_factor is None

    def test_expectancy_weights_by_win_rate(self):
        # 50% win rate, +100 avg win, -50 avg loss -> +25 expectancy
        metrics = compute_metrics([trade(100), trade(-50)])
        assert metrics.expectancy == pytest.approx(25.0)

    def test_a_high_win_rate_can_still_lose_money(self):
        """Nine small wins and one large loss: 90% win rate, negative expectancy."""
        metrics = compute_metrics([trade(10)] * 9 + [trade(-500)])
        assert metrics.win_rate == pytest.approx(0.9)
        assert metrics.total_pnl < 0
        assert metrics.expectancy < 0


class TestDrawdown:
    def test_equity_curve_accumulates(self):
        assert equity_curve([trade(100, day=1), trade(-50, day=2)], 1000.0) == [1000, 1100, 1050]

    def test_no_drawdown_on_a_rising_curve(self):
        assert max_drawdown([1000, 1100, 1200])[0] == 0.0

    def test_measures_peak_to_trough(self):
        drop, pct = max_drawdown([1000, 1500, 900, 1200])
        assert drop == pytest.approx(600.0)
        assert pct == pytest.approx(0.4)

    def test_computed_from_trades(self):
        metrics = compute_metrics([trade(500, day=1), trade(-600, day=2)], 1000.0)
        assert metrics.max_drawdown == pytest.approx(600.0)


class TestBreakdowns:
    def test_grouped_by_price_source(self):
        metrics = compute_metrics(
            [trade(100, source="alpaca_bars"), trade(-50, source="black_scholes")]
        )
        assert set(metrics.by_source) == {"alpaca_bars", "black_scholes"}
        assert metrics.by_source["alpaca_bars"]["win_rate"] == 1.0

    def test_grouped_by_ticker(self):
        metrics = compute_metrics([trade(100, ticker="AAPL"), trade(-50, ticker="MSFT")])
        assert metrics.by_ticker["AAPL"]["trades"] == 1

    def test_every_breakdown_carries_its_own_trade_count(self):
        metrics = compute_metrics([trade(100), trade(-50)])
        for stats in metrics.by_source.values():
            assert "trades" in stats and "wins" in stats


class TestCostModel:
    def test_buys_fill_above_mid_and_sells_below(self):
        backtest, acct = BacktestConfig(), AccountConfig()
        buy = costs.buy_fill(5.00, 35, backtest, acct)
        sell = costs.sell_fill(5.00, 35, backtest, acct)
        assert buy.price > 5.00 > sell.price

    def test_short_dated_contracts_are_modelled_wider(self):
        backtest = BacktestConfig()
        assert costs.spread_fraction(20, backtest) > costs.spread_fraction(40, backtest)

    def test_sell_price_is_floored_at_zero(self):
        assert costs.sell_fill(0.0, 35, BacktestConfig(), AccountConfig()).price == 0.0

    def test_round_trip_subtracts_both_commissions(self):
        backtest, acct = BacktestConfig(), AccountConfig(commission_per_contract=1.00)
        entry = costs.buy_fill(5.00, 35, backtest, acct)
        exit_ = costs.sell_fill(5.00, 35, backtest, acct)
        pnl, _ = costs.round_trip_pnl(entry, exit_, qty=1)
        # Flat mid still loses the spread plus both commissions.
        assert pnl < -2.0

    def test_spread_costs_scale_with_quantity(self):
        backtest, acct = BacktestConfig(), AccountConfig()
        entry = costs.buy_fill(5.00, 35, backtest, acct)
        exit_ = costs.sell_fill(5.00, 35, backtest, acct)
        one, _ = costs.round_trip_pnl(entry, exit_, qty=1)
        three, _ = costs.round_trip_pnl(entry, exit_, qty=3)
        assert three == pytest.approx(one * 3)
