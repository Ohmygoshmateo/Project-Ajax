"""The archive read as a series, and the four ways that reading can lie.

Every test here is really one of: don't draw a trend through one point, don't
present irregular captures as a smooth line, don't confuse cumulative totals
with new work, and don't coerce a payload written by a different schema. The
fixtures are real snapshots written by ``snapshot.write`` rather than
hand-rolled JSON, so the series is read through the same shape the CLI commits.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ajax_hq import snapshot as snapshot_mod
from ajax_hq import trends as trends_mod
from ajax_hq.model import Agent, BuiltFile, Commit, Session, Snapshot

BLOCKS = set(trends_mod.BLOCKS)


def build(when: datetime, *, agents: int = 1, sessions: int = 1, files: int = 0,
          commits: int = 0, tokens: int = 0) -> Snapshot:
    """A snapshot with known counts, stamped at a known moment."""
    built = Snapshot(generated_at=when)
    stamp = when.strftime("%Y%m%d%H%M%S")
    built.sessions = [
        Session(session_id=f"{stamp}-session-{i}", input_tokens=tokens if i == 0 else 0)
        for i in range(sessions)
    ]
    built.agents = [Agent(agent_id=f"{stamp}-agent-{i}") for i in range(agents)]
    built.files = [BuiltFile(path=f"/w/f{i}.py", writes=1) for i in range(files)]
    built.commits = [Commit(sha=f"{stamp}{i:04d}") for i in range(commits)]
    return built


def capture(history: Path, when: datetime, **counts) -> Path:
    return snapshot_mod.write(build(when, **counts), history)


def render_to_text(trends: trends_mod.Trends, width: int = 100) -> str:
    buffer = StringIO()
    trends_mod.render(trends, Console(file=buffer, width=width, force_terminal=False))
    return buffer.getvalue()


@pytest.fixture
def history(tmp_path: Path) -> Path:
    return tmp_path / "history"


class TestTwoPointsAreNotATrend:
    """The single most important behaviour: one capture is not a direction."""

    def test_one_capture_reports_not_enough_history(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=3)
        trends = trends_mod.series(history)

        assert len(trends.captures) == 1
        assert not trends.enough
        assert "Not enough history" in render_to_text(trends)

    def test_one_capture_draws_no_line_at_all(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=3)
        text = render_to_text(trends_mod.series(history))
        assert not (BLOCKS & set(text)), "a sparkline was drawn through a single point"

    def test_one_capture_still_shows_its_figures(self, history):
        """Refusing a trend is not refusing the numbers — they are measured."""
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=3)
        assert "2026-07-01" in render_to_text(trends_mod.series(history))

    def test_two_captures_do_draw_a_line(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=1)
        capture(history, datetime(2026, 7, 4, 9, tzinfo=UTC), agents=4)
        trends = trends_mod.series(history)

        assert trends.enough
        text = render_to_text(trends)
        assert BLOCKS & set(text)
        assert "Not enough history" not in text


class TestEmptyAndBrokenHistory:
    def test_empty_directory_is_handled(self, history):
        history.mkdir()
        trends = trends_mod.series(history)
        assert trends.captures == []
        assert not trends.enough

    def test_empty_directory_renders_without_raising(self, history):
        history.mkdir()
        assert "nothing to trend" in render_to_text(trends_mod.series(history))

    def test_missing_directory_is_harmless(self, tmp_path):
        assert trends_mod.series(tmp_path / "nope").captures == []

    def test_corrupt_file_is_skipped_and_counted(self, history):
        history.mkdir()
        (history / "broken.json").write_text("{not json")
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))

        trends = trends_mod.series(history)
        assert len(trends.captures) == 1
        assert trends.skipped_unreadable == 1

    def test_undated_payload_cannot_be_placed_in_a_series(self, history):
        """A guessed position would be indistinguishable from a measured one."""
        history.mkdir()
        (history / "undated.json").write_text(
            json.dumps({"schema": 1, "captured_at": None, "sessions": [], "agents": []})
        )
        trends = trends_mod.series(history)
        assert trends.captures == []
        assert trends.skipped_undated == 1


class TestSchemaVersion:
    def test_wrong_schema_version_is_skipped_not_coerced(self, history):
        history.mkdir()
        (history / "old.json").write_text(
            json.dumps({"schema": 999, "captured_at": "2026-01-01T00:00:00+00:00",
                        "agents": [{"id": "x"}], "sessions": [{"id": "y"}]})
        )
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=2)

        trends = trends_mod.series(history)
        assert trends.skipped_schema == 1
        assert [c.agents for c in trends.captures] == [2]

    def test_skipped_count_is_reported_in_the_output(self, history):
        history.mkdir()
        (history / "old.json").write_text(json.dumps({"schema": 999}))
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))

        text = render_to_text(trends_mod.series(history))
        assert "different schema version" in text
        assert "skipped" in text


class TestOrdering:
    def test_captures_are_ordered_by_capture_time_not_filename(self, history):
        """Filenames encode the date, but a renamed file must not reorder time."""
        early = capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=1)
        late = capture(history, datetime(2026, 7, 9, 9, tzinfo=UTC), agents=7)
        early.rename(history / "zzz-early.json")
        late.rename(history / "aaa-late.json")

        trends = trends_mod.series(history)
        assert [c.agents for c in trends.captures] == [1, 7]
        assert trends.captures[0].captured_at < trends.captures[1].captured_at

    def test_live_snapshot_is_the_last_point(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=1)
        live = build(datetime(2026, 7, 20, 9, tzinfo=UTC), agents=5)

        trends = trends_mod.series(history, live=live)
        assert len(trends.captures) == 2
        assert trends.captures[-1].live
        assert trends.captures[-1].agents == 5

    def test_live_snapshot_is_labelled_as_uncommitted(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))
        live = build(datetime(2026, 7, 20, 9, tzinfo=UTC), agents=5)
        assert "live" in render_to_text(trends_mod.series(history, live=live))


class TestCumulativeVersusNew:
    def test_raw_figures_are_the_cumulative_totals(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=3)
        capture(history, datetime(2026, 7, 5, 9, tzinfo=UTC), agents=10)
        assert trends_mod.series(history).values("agents") == [3, 10]

    def test_delta_is_new_records_not_the_total(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=3)
        capture(history, datetime(2026, 7, 5, 9, tzinfo=UTC), agents=10)
        assert trends_mod.series(history).deltas("agents") == [None, 7]

    def test_first_capture_has_no_delta(self, history):
        """Nothing to be new against; claiming otherwise back-dates all of it."""
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=3)
        assert trends_mod.series(history).deltas("agents")[0] is None

    def test_a_shrinking_series_never_reports_a_negative_delta(self, history):
        """A capture written before history was merged lists fewer records."""
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=5)
        capture(history, datetime(2026, 7, 5, 9, tzinfo=UTC), agents=2)
        trends = trends_mod.series(history)

        assert trends.deltas("agents") == [None, 0]
        assert trends.clamped("agents") == 1

    def test_negative_delta_never_renders(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=5)
        capture(history, datetime(2026, 7, 5, 9, tzinfo=UTC), agents=2)
        text = render_to_text(trends_mod.series(history))

        assert "-3" not in text
        assert "+0" in text
        assert "floored at zero" in text

    def test_net_new_does_not_hide_a_clamped_step(self, history):
        """last - first would be 2 here; the honest sum of steps is 4."""
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=4)
        capture(history, datetime(2026, 7, 2, 9, tzinfo=UTC), agents=2)
        capture(history, datetime(2026, 7, 3, 9, tzinfo=UTC), agents=6)
        assert trends_mod.series(history).net("agents") == 4

    def test_every_metric_is_captured(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC),
                agents=2, sessions=3, files=4, commits=5, tokens=1200)
        point = trends_mod.series(history).captures[0]
        assert (point.agents, point.sessions, point.files, point.commits) == (2, 3, 4, 5)
        assert point.session_tokens == 1200


class TestIrregularCaptures:
    def test_real_intervals_are_measured_not_assumed(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))
        capture(history, datetime(2026, 7, 2, 9, tzinfo=UTC))
        capture(history, datetime(2026, 7, 20, 9, tzinfo=UTC))

        gaps = trends_mod.series(history).gaps
        assert gaps[0] is None
        assert gaps[1] == timedelta(days=1)
        assert gaps[2] == timedelta(days=18)

    def test_output_states_that_captures_are_irregular(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))
        capture(history, datetime(2026, 7, 20, 9, tzinfo=UTC))
        text = render_to_text(trends_mod.series(history))

        assert "irregular" in text
        assert "not on a schedule" in text
        assert "interpolated" in text

    def test_output_states_which_figures_are_cumulative(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))
        capture(history, datetime(2026, 7, 20, 9, tzinfo=UTC))
        text = render_to_text(trends_mod.series(history))

        assert "cumulative" in text
        assert "delta" in text

    def test_the_gap_column_shows_the_uneven_spacing(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))
        capture(history, datetime(2026, 7, 20, 9, tzinfo=UTC))
        assert "19d" in render_to_text(trends_mod.series(history))


class TestSparkline:
    def test_one_bar_per_capture_by_default(self):
        assert len(trends_mod.sparkline([1, 2, 3, 4])) == 4

    def test_requested_width_is_honoured_when_padding(self):
        assert len(trends_mod.sparkline([1, 2, 3], width=10)) == 10

    def test_requested_width_is_honoured_when_thinning(self):
        assert len(trends_mod.sparkline(list(range(50)), width=8)) == 8

    def test_padding_is_blank_rather_than_invented(self):
        line = trends_mod.sparkline([1, 5], width=8)
        assert len(line) == 8
        assert line.endswith("      ")
        assert sum(1 for char in line if char in BLOCKS) == 2

    def test_thinning_only_uses_real_samples(self):
        values = [0, 100, 0, 100, 0, 100]
        drawn = trends_mod.sparkline(values, width=3)
        assert set(drawn) <= {trends_mod.BLOCKS[0], trends_mod.BLOCKS[-1]}

    def test_all_equal_values_do_not_crash_or_imply_a_slope(self):
        line = trends_mod.sparkline([7, 7, 7, 7])
        assert len(line) == 4
        assert len(set(line)) == 1

    def test_a_single_value_does_not_crash(self):
        assert len(trends_mod.sparkline([42])) == 1

    def test_no_values_produces_nothing(self):
        assert trends_mod.sparkline([]) == ""

    def test_zero_width_produces_nothing(self):
        assert trends_mod.sparkline([1, 2, 3], width=0) == ""

    def test_rising_series_rises(self):
        line = trends_mod.sparkline([1, 2, 3, 4, 5])
        assert line[0] == trends_mod.BLOCKS[0]
        assert line[-1] == trends_mod.BLOCKS[-1]

    def test_every_character_is_a_block(self):
        assert set(trends_mod.sparkline([3, 1, 4, 1, 5])) <= BLOCKS


class TestRenderedOutput:
    def test_a_row_per_capture(self, history):
        for day in (1, 4, 9):
            capture(history, datetime(2026, 7, day, 9, tzinfo=UTC))
        text = render_to_text(trends_mod.series(history))
        for day in (1, 4, 9):
            assert f"2026-07-0{day}" in text

    def test_every_metric_has_a_sparkline(self, history):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC), agents=1, files=1, commits=1)
        capture(history, datetime(2026, 7, 4, 9, tzinfo=UTC), agents=4, files=3, commits=2)
        text = render_to_text(trends_mod.series(history))
        for _, header in trends_mod.METRICS:
            assert header in text

    def test_nothing_is_invented_for_a_missing_capture(self, history):
        """Three captures spanning three weeks stay three rows, not twenty-one."""
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))
        capture(history, datetime(2026, 7, 8, 9, tzinfo=UTC))
        capture(history, datetime(2026, 7, 22, 9, tzinfo=UTC))
        trends = trends_mod.series(history)

        assert len(trends.captures) == 3
        assert len(trends_mod.sparkline(trends.values("agents"))) == 3

    def test_render_defaults_to_its_own_console(self, history, capsys):
        capture(history, datetime(2026, 7, 1, 9, tzinfo=UTC))
        trends_mod.render(trends_mod.series(history))
        assert "TRENDS" in capsys.readouterr().out
