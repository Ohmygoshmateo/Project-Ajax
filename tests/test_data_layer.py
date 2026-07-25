"""Symbol translation, OCC parsing, news schema tolerance, universe validation.

All network-free: the provider payloads are canned fixtures. Real calls are a
manual checklist in the README, never part of the test suite.
"""

from __future__ import annotations

from datetime import date

import pytest

from ajax.broker.contracts import (
    build_occ_symbol,
    monthly_expiries_between,
    parse_occ_symbol,
    third_friday,
)
from ajax.config import UniverseConfig
from ajax.data import symbols as sym
from ajax.data.news import parse_news_item
from ajax.data.universe import validate_refresh
from ajax.options.greeks import Right


class TestSymbolTranslation:
    @pytest.mark.parametrize(
        ("dotted", "dashed"), [("BRK.B", "BRK-B"), ("BF.B", "BF-B"), ("AAPL", "AAPL")]
    )
    def test_round_trip(self, dotted, dashed):
        assert sym.to_yahoo(dotted) == dashed
        assert sym.to_alpaca(dashed) == dotted

    def test_normalizes_case_and_whitespace(self):
        assert sym.normalize(" brk-b ") == "BRK.B"

    def test_known_dual_class_names_survive_a_round_trip(self):
        for ticker in sym.KNOWN_DUAL_CLASS:
            assert sym.to_alpaca(sym.to_yahoo(ticker)) == ticker

    def test_batch_helpers(self):
        assert sym.to_yahoo_many(["BRK.B", "AAPL"]) == ["BRK-B", "AAPL"]
        assert sym.to_alpaca_many(["BRK-B", "AAPL"]) == ["BRK.B", "AAPL"]


class TestOccSymbols:
    def test_builds_the_standard_format(self):
        built = build_occ_symbol("AAPL", date(2024, 1, 19), Right.CALL, 190.0)
        assert built == "AAPL  240119C00190000"
        assert len(built) == 21

    def test_round_trips(self):
        original = build_occ_symbol("AAPL", date(2024, 1, 19), Right.CALL, 190.0)
        parsed = parse_occ_symbol(original)
        assert parsed.underlying == "AAPL"
        assert parsed.expiry == date(2024, 1, 19)
        assert parsed.right is Right.CALL
        assert parsed.strike == pytest.approx(190.0)

    def test_round_trips_puts_and_fractional_strikes(self):
        original = build_occ_symbol("SPY", date(2026, 6, 19), Right.PUT, 472.5)
        parsed = parse_occ_symbol(original)
        assert parsed.right is Right.PUT
        assert parsed.strike == pytest.approx(472.5)

    def test_parses_the_unpadded_form(self):
        parsed = parse_occ_symbol("AAPL240119C00190000")
        assert parsed.underlying == "AAPL"
        assert parsed.strike == pytest.approx(190.0)

    def test_dual_class_root_drops_the_dot(self):
        built = build_occ_symbol("BRK.B", date(2026, 6, 19), Right.CALL, 400.0)
        assert built.startswith("BRKB")

    @pytest.mark.parametrize("bad", ["", "NOTASYMBOL", "AAPL999999C00190000", "12345"])
    def test_unparseable_symbols_return_none_rather_than_raising(self, bad):
        assert parse_occ_symbol(bad) is None

    def test_rejects_an_over_long_root(self):
        with pytest.raises(ValueError):
            build_occ_symbol("TOOLONGX", date(2026, 6, 19), Right.CALL, 100.0)

    def test_rejects_a_nonpositive_strike(self):
        with pytest.raises(ValueError):
            build_occ_symbol("AAPL", date(2026, 6, 19), Right.CALL, 0.0)


class TestExpiries:
    @pytest.mark.parametrize(
        ("year", "month", "expected"),
        [
            (2026, 1, date(2026, 1, 16)),
            (2026, 6, date(2026, 6, 19)),
            (2024, 2, date(2024, 2, 16)),
        ],
    )
    def test_third_friday(self, year, month, expected):
        assert third_friday(year, month) == expected

    def test_third_friday_is_always_a_friday(self):
        for month in range(1, 13):
            assert third_friday(2026, month).weekday() == 4

    def test_monthly_expiries_in_range(self):
        found = monthly_expiries_between(date(2026, 3, 1), date(2026, 6, 30))
        assert len(found) == 4
        assert all(d.weekday() == 4 for d in found)

    def test_empty_range_returns_nothing(self):
        # 2026-03-20 is itself the third Friday, so start the window after it.
        assert monthly_expiries_between(date(2026, 3, 21), date(2026, 3, 25)) == []

    def test_range_containing_an_expiry_includes_it(self):
        assert monthly_expiries_between(date(2026, 3, 20), date(2026, 3, 25)) == [
            date(2026, 3, 20)
        ]


class TestNewsSchemaTolerance:
    """yfinance has shipped several shapes; none may crash the parser."""

    def test_legacy_flat_schema(self):
        item = parse_news_item(
            {
                "title": "Company beats earnings",
                "link": "https://example.com/a",
                "publisher": "Reuters",
                "providerPublishTime": 1_700_000_000,
            },
            "AAPL",
        )
        assert item.title == "Company beats earnings"
        assert item.url == "https://example.com/a"
        assert item.publisher == "Reuters"
        assert item.published_at is not None

    def test_modern_nested_schema(self):
        item = parse_news_item(
            {
                "id": "abc123",
                "content": {
                    "title": "Stock climbs on upgrade",
                    "canonicalUrl": {"url": "https://example.com/b"},
                    "provider": {"displayName": "Bloomberg"},
                    "pubDate": "2026-03-02T14:00:00Z",
                },
            },
            "MSFT",
        )
        assert item.title == "Stock climbs on upgrade"
        assert item.url == "https://example.com/b"
        assert item.publisher == "Bloomberg"

    def test_partial_schema_with_only_a_title(self):
        item = parse_news_item({"content": {"title": "Headline only"}}, "NVDA")
        assert item.usable
        assert item.url is None
        assert item.publisher is None

    def test_click_through_url_is_accepted(self):
        item = parse_news_item(
            {"content": {"title": "T", "clickThroughUrl": {"url": "https://example.com/c"}}},
            "AMD",
        )
        assert item.url == "https://example.com/c"

    @pytest.mark.parametrize("payload", [{}, {"content": {}}, None, "a string", 42, []])
    def test_unusable_payloads_return_none_rather_than_raising(self, payload):
        assert parse_news_item(payload, "TEST") is None

    def test_a_bad_timestamp_does_not_crash(self):
        item = parse_news_item(
            {"title": "T", "providerPublishTime": 99_999_999_999_999}, "TEST"
        )
        assert item.usable

    def test_one_line_summary_renders(self):
        item = parse_news_item(
            {"title": "T", "publisher": "P", "providerPublishTime": 1_700_000_000}, "TEST"
        )
        assert "T" in item.one_line() and "P" in item.one_line()


class TestUniverseValidation:
    def setup_method(self):
        self.cfg = UniverseConfig(
            min_expected_tickers=450, max_expected_tickers=520, max_churn_per_refresh=10
        )
        self.cached = [f"T{i:03d}" for i in range(500)]

    def test_accepts_an_identical_list(self):
        ok, _ = validate_refresh(self.cached, self.cached, self.cfg)
        assert ok

    def test_accepts_a_small_rebalance(self):
        candidate = self.cached[:-3] + ["NEW1", "NEW2", "NEW3"]
        ok, _ = validate_refresh(candidate, self.cached, self.cfg)
        assert ok

    def test_rejects_a_short_list(self):
        ok, reason = validate_refresh(self.cached[:100], self.cached, self.cfg)
        assert not ok and "below" in reason

    def test_rejects_an_over_long_list(self):
        ok, reason = validate_refresh([f"T{i}" for i in range(600)], self.cached, self.cfg)
        assert not ok and "above" in reason

    def test_rejects_excessive_churn(self):
        """A scrape that swaps 50 names is a broken parse, not a rebalance."""
        candidate = self.cached[:-50] + [f"NEW{i}" for i in range(50)]
        ok, reason = validate_refresh(candidate, self.cached, self.cfg)
        assert not ok and "changed in one refresh" in reason

    def test_rejects_an_empty_scrape(self):
        ok, _ = validate_refresh([], self.cached, self.cfg)
        assert not ok

    def test_first_run_without_a_cache_skips_the_churn_check(self):
        ok, _ = validate_refresh(self.cached, [], self.cfg)
        assert ok


class TestBundledUniverse:
    def test_cached_list_loads_and_is_plausible(self):
        from ajax.data import universe as universe_mod

        universe = universe_mod.load_cached()
        assert 450 <= len(universe) <= 520
        assert "AAPL" in universe.tickers
        assert all(t == t.upper() for t in universe.tickers)

    def test_cached_list_uses_canonical_dotted_form(self):
        from ajax.data import universe as universe_mod

        assert not any("-" in t for t in universe_mod.load_cached().tickers)
