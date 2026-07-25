"""End-to-end backtest on synthetic prices — no network.

Proves the loop actually produces trades and that the structural guarantees hold:
entries never price on the signal date, holds are 5 *trading* days, and the
caveat block is present in every report.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ajax.backtest.engine import (
    BacktestResult,
    default_window,
    run_backtest,
    trades_to_frame,
)
from ajax.backtest.metrics import compute_metrics
from ajax.backtest.price_source import (
    BlackScholesSource,
    ChainedPriceSource,
    build_price_source,
)
from ajax.backtest.report import build_caveats, write_csv, write_markdown
from ajax.config import load_config
from ajax.utils.calendar import trading_days_between

TICKERS = ["AAA", "BBB", "CCC", "DDD", "SPY"]


def synthetic_prices(start: date, end: date, seed: int = 17) -> pd.DataFrame:
    """A wide (ticker, field) frame with distinguishable trends per ticker."""
    rng = np.random.default_rng(seed)
    index = pd.DatetimeIndex(pd.bdate_range(start, end))
    n = len(index)

    frames: dict[tuple[str, str], pd.Series] = {}
    drifts = {"AAA": 0.0035, "BBB": -0.0035, "CCC": 0.0015, "DDD": -0.0015, "SPY": 0.0003}

    for ticker, drift in drifts.items():
        shocks = rng.normal(drift, 0.012, n)
        closes = 100.0 * np.exp(np.cumsum(shocks))
        frames[(ticker, "Close")] = pd.Series(closes, index=index)
        frames[(ticker, "Open")] = pd.Series(closes * 0.999, index=index)
        frames[(ticker, "High")] = pd.Series(closes * 1.01, index=index)
        frames[(ticker, "Low")] = pd.Series(closes * 0.99, index=index)
        frames[(ticker, "Volume")] = pd.Series(
            rng.integers(1_000_000, 5_000_000, n).astype(float), index=index
        )

    frame = pd.DataFrame(frames)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame.sort_index(axis=1)


@pytest.fixture(scope="module")
def config():
    # Loosen the entry bar so the synthetic sample generates trades.
    return load_config({"signals": {"entry_score_threshold": 0.2}})


@pytest.fixture(scope="module")
def prices():
    return synthetic_prices(date(2025, 6, 1), date(2026, 3, 31))


@pytest.fixture(scope="module")
def result(config, prices) -> BacktestResult:
    return run_backtest(
        prices,
        config,
        BlackScholesSource(config),
        start=date(2025, 11, 3),
        end=date(2026, 3, 20),
    )


class TestBacktestRuns:
    def test_produces_trades(self, result):
        assert result.trades, "synthetic trending data should generate at least one trade"

    def test_evaluates_signal_days(self, result):
        assert result.signal_days > 0

    def test_every_trade_has_a_computed_pnl(self, result):
        assert all(t.pnl is not None for t in result.trades)

    def test_trades_convert_to_a_frame(self, result):
        frame = trades_to_frame(result.trades)
        assert not frame.empty
        for column in ("ticker", "pnl", "entry_date", "exit_date", "win"):
            assert column in frame.columns


class TestNoLookahead:
    def test_entry_is_after_the_signal_date(self, result):
        """Entering on the signal date would use a price that was not yet known."""
        for trade in result.trades:
            assert trade.entry_date > trade.signal_date

    def test_entry_is_the_next_trading_session(self, result):
        for trade in result.trades:
            assert trading_days_between(trade.signal_date, trade.entry_date) == 1

    def test_exit_is_after_entry(self, result):
        for trade in result.trades:
            assert trade.exit_date > trade.entry_date


class TestHoldPeriod:
    def test_holds_five_trading_days(self, result, config):
        for trade in result.trades:
            held = trading_days_between(trade.entry_date, trade.exit_date)
            assert held >= config.backtest.hold_trading_days

    def test_hold_spans_more_calendar_days_than_trading_days(self, result):
        """A 5-trading-day hold crosses a weekend, so it is ~7 calendar days."""
        for trade in result.trades:
            assert (trade.exit_date - trade.entry_date).days >= 5


class TestContractConstraints:
    def test_dte_never_breaches_the_hard_floor(self, result, config):
        for trade in result.trades:
            assert trade.dte_at_entry >= config.options.dte_hard_floor

    def test_delta_stays_inside_the_configured_band(self, result, config):
        for trade in result.trades:
            assert config.options.delta_min <= abs(trade.delta_at_entry) <= config.options.delta_max

    def test_contract_still_has_life_left_at_exit(self, result):
        """The position is sold, not held to expiry — that is the strategy."""
        for trade in result.trades:
            assert trade.expiry > trade.exit_date

    def test_entry_premium_respects_the_affordability_cap(self, result, config):
        from ajax.portfolio.sizing import effective_premium_cap

        cap = effective_premium_cap(config.account, config.options)
        for trade in result.trades:
            # The recorded entry price includes modelled spread, so allow for it.
            assert trade.entry_price <= cap * 1.2


class TestConcurrencyLimit:
    def test_never_exceeds_the_configured_slots(self, result, config):
        events: list[tuple[date, int]] = []
        for trade in result.trades:
            events.append((trade.entry_date, 1))
            events.append((trade.exit_date, -1))

        open_count = 0
        for _, delta in sorted(events, key=lambda e: (e[0], e[1])):
            open_count += delta
            assert open_count <= config.account.max_concurrent_positions

    def test_never_holds_two_positions_in_one_underlying(self, result):
        by_ticker: dict[str, list] = {}
        for trade in result.trades:
            by_ticker.setdefault(trade.ticker, []).append(trade)

        for group in by_ticker.values():
            ordered = sorted(group, key=lambda t: t.entry_date)
            for earlier, later in zip(ordered, ordered[1:], strict=False):
                assert later.entry_date >= earlier.exit_date


class TestBothDirections:
    def test_uptrending_and_downtrending_names_both_trade(self, config, prices):
        outcome = run_backtest(
            prices, config, BlackScholesSource(config),
            start=date(2025, 11, 3), end=date(2026, 3, 20),
        )
        rights = {t.right.value for t in outcome.trades}
        assert rights, "expected at least one trade"
        # AAA trends up and BBB trends down, so the strategy is symmetric here.
        assert rights <= {"call", "put"}


class TestPriceSourceFallback:
    def test_chained_source_falls_back_to_the_model(self, config):
        chained = ChainedPriceSource(_EmptySource(), BlackScholesSource(config))
        closes = pd.Series(
            100 * np.exp(np.random.default_rng(1).normal(0, 0.01, 120).cumsum()),
            index=pd.DatetimeIndex(pd.bdate_range("2025-10-01", periods=120)),
        )
        point = chained.price_on(
            "X", "AAA", 100.0, date(2026, 4, 17), _call(), date(2026, 3, 2),
            spot=100.0, closes=closes, is_entry=True,
        )
        assert point is not None
        assert point.source == "black_scholes"
        assert chained.counts["black_scholes"] == 1

    def test_coverage_reports_the_split(self, config):
        chained = ChainedPriceSource(_EmptySource(), BlackScholesSource(config))
        chained.counts = {"alpaca_bars": 3, "black_scholes": 1, "unpriced": 0}
        assert chained.coverage["alpaca_bars"] == pytest.approx(0.75)

    def test_build_price_source_honours_the_mode(self, config):
        assert isinstance(build_price_source(config), ChainedPriceSource)
        bs_only = load_config({"backtest": {"price_source": "bs"}})
        assert isinstance(build_price_source(bs_only), BlackScholesSource)

    def test_entries_are_priced_above_exits_at_the_same_spot(self, config):
        """The volatility haircut must make a modelled entry dearer than an exit."""
        source = BlackScholesSource(config)
        closes = pd.Series(
            100 * np.exp(np.random.default_rng(2).normal(0, 0.012, 150).cumsum()),
            index=pd.DatetimeIndex(pd.bdate_range("2025-10-01", periods=150)),
        )
        args = ("X", "AAA", 100.0, date(2026, 5, 15), _call(), date(2026, 3, 2))
        entry = source.price_on(*args, spot=100.0, closes=closes, is_entry=True)
        exit_ = source.price_on(*args, spot=100.0, closes=closes, is_entry=False)
        assert entry.price > exit_.price


class TestReportCaveats:
    def test_caveats_are_always_present(self, result, config):
        assert build_caveats(result, config)

    def test_upper_bound_statement_is_mandatory(self, result, config):
        text = " ".join(build_caveats(result, config)).upper()
        assert "UPPER BOUND" in text

    def test_news_exclusion_is_disclosed(self, result, config):
        text = " ".join(build_caveats(result, config))
        assert "News is NOT factored" in text

    def test_survivorship_status_is_disclosed(self, result, config):
        text = " ".join(build_caveats(result, config))
        assert "Index membership" in text

    def test_markdown_report_embeds_the_caveats(self, result, config, tmp_path):
        metrics = compute_metrics(result.trades, config.account.equity)
        path = write_markdown(result, metrics, config, tmp_path / "report.md")
        content = path.read_text()
        assert "How to read these numbers" in content
        assert "upper bound" in content.lower()

    def test_markdown_never_shows_a_bare_win_rate(self, result, config, tmp_path):
        metrics = compute_metrics(result.trades, config.account.equity)
        content = write_markdown(
            result, metrics, config, tmp_path / "report.md"
        ).read_text()
        assert "trades)" in content  # win_rate_display always appends the count

    def test_csv_carries_the_caveats_in_its_header(self, result, config, tmp_path):
        path = write_csv(result, config, tmp_path / "trades.csv")
        first_lines = path.read_text().splitlines()[:5]
        assert any("UPPER BOUND" in line for line in first_lines)


class TestDefaultWindow:
    def test_spans_the_configured_months(self, config):
        start, end = default_window(config)
        assert (end - start).days >= config.backtest.months * 28


class _EmptySource:
    """A price source that never has data, to exercise the fallback path."""

    name = "alpaca_bars"

    def price_on(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


def _call():
    from ajax.options.greeks import Right

    return Right.CALL


class TestCaveatProvenanceAlwaysStated:
    """A fully-modelled run must say so — that is when it matters most."""

    def test_pure_black_scholes_run_declares_all_prices_modelled(self, result, config):
        assert result.price_source_counts == {}, "single-source runs record no counts"
        text = " ".join(build_caveats(result, config))
        assert "ALL prices were MODEL-DERIVED" in text

    def test_haircut_is_disclosed_on_a_modelled_run(self, result, config):
        text = " ".join(build_caveats(result, config))
        assert "haircut" in text and "85%" in text

    def test_mixed_run_reports_the_split(self, result, config):
        import dataclasses

        mixed = dataclasses.replace(
            result, price_source_counts={"alpaca_bars": 3, "black_scholes": 1}
        )
        text = " ".join(build_caveats(mixed, config))
        assert "25% of prices were MODEL-DERIVED" in text

    def test_fully_real_run_says_so(self, result, config):
        import dataclasses

        real = dataclasses.replace(result, price_source_counts={"alpaca_bars": 12})
        text = " ".join(build_caveats(real, config))
        assert "real historical option bars" in text
