"""Trade log persistence and realized-P&L arithmetic.

Pure sqlite — no network. The P&L computed here is what the graduation check
reads, so an error would corrupt the one number gating a move to real money.
"""

from __future__ import annotations

from datetime import date

import pytest

from ajax.agent.trade_log import TradeLog, TradeRecord
from ajax.backtest.tuner import FORBIDDEN_KEYS, expand_grid, load_grid, split_window


@pytest.fixture
def log(tmp_path) -> TradeLog:
    return TradeLog(tmp_path / "trades.db")


def entry(**overrides) -> TradeRecord:
    base = {
        "ticker": "AAPL",
        "direction": "call",
        "contract_symbol": "AAPL  260417C00190000",
        "entry_date": "2026-03-02",
        "strike": 190.0,
        "expiry": "2026-04-17",
        "dte_at_entry": 46,
        "entry_premium": 5.00,
        "qty": 1,
        "commission": 0.65,
        "delta_at_entry": 0.62,
        "greeks_source": "alpaca",
    }
    base.update(overrides)
    return TradeRecord(**base)


class TestSchema:
    def test_creates_tables_on_first_use(self, log):
        assert log.open_trades() == []
        assert log.closed_trades() == []

    def test_reopening_an_existing_database_is_safe(self, tmp_path):
        first = TradeLog(tmp_path / "t.db")
        first.record_entry(entry())
        second = TradeLog(tmp_path / "t.db")
        assert len(second.open_trades()) == 1


class TestEntries:
    def test_records_and_reads_back(self, log):
        trade_id = log.record_entry(entry())
        rows = log.open_trades()
        assert len(rows) == 1
        assert rows[0]["id"] == trade_id
        assert rows[0]["ticker"] == "AAPL"
        assert rows[0]["status"] == "open"

    def test_preserves_greeks_provenance(self, log):
        log.record_entry(entry(greeks_source="bs_from_realized_vol"))
        assert log.open_trades()[0]["greeks_source"] == "bs_from_realized_vol"

    def test_defaults_to_paper_mode(self, log):
        log.record_entry(entry())
        assert log.open_trades()[0]["mode"] == "paper"

    def test_open_underlyings(self, log):
        log.record_entry(entry(ticker="AAPL"))
        log.record_entry(entry(ticker="MSFT", contract_symbol="MSFT  260417C00400000"))
        assert log.open_underlyings() == {"AAPL", "MSFT"}


class TestExitsAndPnl:
    def test_a_winning_trade(self, log):
        trade_id = log.record_entry(entry(entry_premium=5.00, qty=1, commission=0.65))
        log.record_exit(trade_id, exit_date="2026-03-09", exit_premium=7.00, commission=0.65)

        trade = log.closed_trades()[0]
        # (7.00 - 5.00) * 100 = $200 gross, minus $1.30 total commission.
        assert trade["pnl"] == pytest.approx(198.70)
        assert trade["status"] == "closed"

    def test_a_losing_trade(self, log):
        trade_id = log.record_entry(entry(entry_premium=5.00, commission=0.65))
        log.record_exit(trade_id, exit_date="2026-03-09", exit_premium=3.00, commission=0.65)
        assert log.closed_trades()[0]["pnl"] == pytest.approx(-201.30)

    def test_pnl_scales_with_quantity(self, log):
        trade_id = log.record_entry(entry(entry_premium=2.00, qty=3, commission=1.95))
        log.record_exit(trade_id, exit_date="2026-03-09", exit_premium=3.00, commission=1.95)
        # (3-2) * 100 * 3 = $300 gross, minus $3.90 commission.
        assert log.closed_trades()[0]["pnl"] == pytest.approx(296.10)

    def test_percent_is_relative_to_cost_basis(self, log):
        trade_id = log.record_entry(entry(entry_premium=5.00, commission=0.0))
        log.record_exit(trade_id, exit_date="2026-03-09", exit_premium=7.50, commission=0.0)
        assert log.closed_trades()[0]["pnl_pct"] == pytest.approx(50.0)

    def test_a_total_loss_is_minus_one_hundred_percent(self, log):
        """An option expiring worthless loses the entire premium, not a fraction."""
        trade_id = log.record_entry(entry(entry_premium=5.00, commission=0.0))
        log.record_exit(trade_id, exit_date="2026-03-09", exit_premium=0.0, commission=0.0)
        assert log.closed_trades()[0]["pnl_pct"] == pytest.approx(-100.0)

    def test_closing_removes_it_from_open(self, log):
        trade_id = log.record_entry(entry())
        log.record_exit(trade_id, exit_date="2026-03-09", exit_premium=6.0)
        assert log.open_trades() == []
        assert len(log.closed_trades()) == 1

    def test_closing_an_unknown_trade_raises(self, log):
        with pytest.raises(KeyError):
            log.record_exit(999, exit_date="2026-03-09", exit_premium=6.0)

    def test_closed_trades_filter_by_date(self, log):
        first = log.record_entry(entry())
        second = log.record_entry(entry(ticker="MSFT"))
        log.record_exit(first, exit_date="2026-01-15", exit_premium=6.0)
        log.record_exit(second, exit_date="2026-03-09", exit_premium=6.0)

        recent = log.closed_trades(since=date(2026, 2, 1))
        assert len(recent) == 1
        assert recent[0]["ticker"] == "MSFT"


class TestSkipsAndNews:
    def test_records_skip_reasons(self, log):
        log.record_skip(
            as_of="2026-03-02", ticker="NVDA", direction="call",
            skip_reason="unaffordable", detail="cheapest in-band was $12.00",
        )
        assert log.skip_reason_counts() == {"unaffordable": 1}

    def test_counts_group_by_reason(self, log):
        for reason in ("unaffordable", "unaffordable", "illiquid"):
            log.record_skip(as_of="2026-03-02", ticker="X", direction="call", skip_reason=reason)
        assert log.skip_reason_counts() == {"unaffordable": 2, "illiquid": 1}

    def test_records_news_items(self, log):
        class Item:
            title, url, publisher, published_at = "Headline", "http://x", "Reuters", "2026-03-02"

        trade_id = log.record_entry(entry())
        log.record_news("AAPL", "2026-03-02", [Item()], trade_id=trade_id)
        # No public reader for news; assert it persisted via the raw connection.
        with log._connect() as conn:  # noqa: SLF001 - intentional white-box check
            rows = conn.execute("SELECT * FROM news_snapshots").fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "Headline"


class TestRuns:
    def test_start_and_finish_a_run(self, log):
        run_id = log.start_run("2026-03-02")
        log.finish_run(run_id, opened=1, closed=0, skipped=3, notes="ok")

        run = log.recent_runs()[0]
        assert run["opened"] == 1
        assert run["skipped"] == 3
        assert run["finished_at"] is not None

    def test_recent_runs_are_newest_first(self, log):
        log.finish_run(log.start_run("2026-03-02"), opened=0, closed=0, skipped=0)
        second = log.start_run("2026-03-03")
        log.finish_run(second, opened=0, closed=0, skipped=0)
        assert log.recent_runs()[0]["id"] == second


class TestGraduationReadsRealPnl:
    def test_end_to_end_win_rate(self, log):
        """The graduation check must agree with the P&L the log computed."""
        from ajax.agent.graduation import evaluate
        from ajax.config import GraduationConfig

        for index in range(10):
            trade_id = log.record_entry(entry(ticker=f"T{index}", entry_premium=5.0))
            # 8 winners, 2 losers.
            exit_premium = 7.0 if index < 8 else 3.0
            log.record_exit(trade_id, exit_date="2026-03-09", exit_premium=exit_premium)

        status = evaluate(
            log.closed_trades(),
            GraduationConfig(min_closed_trades=10, consecutive_weeks=1),
            as_of=date(2026, 3, 9),
        )
        assert status.overall_win_rate == pytest.approx(0.8)
        assert status.total_closed == 10


class TestTunerHelpers:
    def test_expand_grid_produces_the_cartesian_product(self):
        combos = expand_grid({"a": [1, 2], "b": [10, 20]})
        assert len(combos) == 4
        assert {"a": 1, "b": 10} in combos

    def test_empty_grid_yields_one_default_point(self):
        assert expand_grid({}) == [{}]

    def test_hold_period_cannot_be_swept(self, tmp_path):
        """5-day holds are a requirement; a grid must not be able to change it."""
        path = tmp_path / "grid.yaml"
        path.write_text("backtest.hold_trading_days: [3, 5, 10]\noptions.delta_min: [0.5]\n")
        grid = load_grid(path)
        assert "backtest.hold_trading_days" not in grid
        assert "options.delta_min" in grid

    def test_forbidden_keys_are_declared(self):
        assert "backtest.hold_trading_days" in FORBIDDEN_KEYS

    def test_split_reserves_the_most_recent_window(self):
        (train_start, train_end), (hold_start, hold_end) = split_window(
            date(2025, 10, 1), date(2026, 3, 31), holdout_days=30
        )
        assert train_start == date(2025, 10, 1)
        assert hold_end == date(2026, 3, 31)
        assert train_end < hold_start

    def test_split_degrades_gracefully_on_a_short_window(self):
        (_, train_end), (hold_start, _) = split_window(
            date(2026, 3, 1), date(2026, 3, 10), holdout_days=90
        )
        assert train_end < hold_start
