"""The daily brief.

The brief is read once a day and believed, so the tests that matter are the ones
asserting it cannot flatter a quiet period: the window must hold still, records
outside it must stay out, and records that cannot be placed must be counted
rather than assumed recent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from rich.console import Console

from ajax_hq import behaviour
from ajax_hq import brief as brief_mod
from ajax_hq.collect import collect
from ajax_hq.model import Agent, BuiltFile, Commit, Session, Snapshot, Status, ToolUsage

# A fixed "now" so window arithmetic in these tests is exact rather than
# approximate — the boundary cases below are meaningless against a moving clock.
GENERATED = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)
WINDOW_START = GENERATED - timedelta(hours=24)


def snap(**kwargs) -> Snapshot:
    return Snapshot(generated_at=GENERATED, **kwargs)


def agent(agent_id: str, started: datetime | None, *, name: str | None = None,
          ended: datetime | None = None, tools: dict[str, int] | None = None,
          commands: list[str] | None = None, agent_type: str = "general-purpose") -> Agent:
    made = Agent(agent_id=agent_id, description=name or agent_id, agent_type=agent_type,
                 started=started, ended=ended, status=Status.COMPLETED,
                 tools=ToolUsage(counts=dict(tools or {})), commands_run=list(commands or []))
    made.verify_runs, made.ship_actions = behaviour.count_commands(made.commands_run)
    return made


def render_to_text(brief, width: int = 100) -> str:
    buffer = StringIO()
    brief_mod.render(brief, Console(file=buffer, width=width, force_terminal=False))
    return buffer.getvalue()


def flat(brief, width: int = 100) -> str:
    """Rendered output with wrapping collapsed, for asserting on long sentences."""
    return " ".join(render_to_text(brief, width).split())


# --------------------------------------------------------------------- window


class TestWindowIsFixed:
    """The window is decided before anything is counted, and never moved."""

    def test_default_window_ends_at_the_snapshot_not_at_wall_clock(self):
        window = brief_mod.build(snap()).window
        assert window.end == GENERATED
        assert window.start == WINDOW_START
        assert window.hours == 24.0

    def test_window_is_not_widened_when_the_result_is_empty(self):
        """The failure mode this whole module exists to prevent."""
        old = agent("a", GENERATED - timedelta(days=40), name="ancient work")
        result = brief_mod.build(snap(agents=[old]))
        assert result.is_empty
        assert result.window.start == WINDOW_START  # unchanged despite finding nothing
        assert result.agents == ()

    def test_window_hours_is_honoured(self):
        window = brief_mod.build(snap(), window_hours=6).window
        assert window.start == GENERATED - timedelta(hours=6)

    def test_explicit_since_sets_the_start(self):
        since = datetime(2026, 7, 1, tzinfo=UTC)
        assert brief_mod.build(snap(), since=since).window.start == since

    def test_a_since_after_the_snapshot_collapses_rather_than_inverting(self):
        """A backwards interval would match every record; a zero-length one matches none."""
        window = brief_mod.build(snap(), since=GENERATED + timedelta(days=1)).window
        assert window.start == window.end
        assert window.hours == 0.0

    def test_naive_since_is_normalised_not_rejected(self):
        result = brief_mod.build(snap(), since=datetime(2026, 7, 26, 0, 0))
        assert result.window.start.tzinfo is not None


class TestBoundary:
    """Half-open, ``start <= t < end``, so consecutive briefs tile exactly.

    A record on the lower edge belongs to the newer window; one on the upper
    edge belongs to the next. Either rule would do — the point is that exactly
    one window claims each record, so nothing is reported twice or lost.
    """

    def test_record_exactly_on_the_lower_edge_is_included(self):
        result = brief_mod.build(snap(agents=[agent("a", WINDOW_START)]))
        assert [a.agent_id for a in result.agents] == ["a"]

    def test_record_one_second_before_the_lower_edge_is_excluded(self):
        stamp = WINDOW_START - timedelta(seconds=1)
        assert brief_mod.build(snap(agents=[agent("a", stamp)])).agents == ()

    def test_record_exactly_on_the_upper_edge_is_excluded(self):
        assert brief_mod.build(snap(agents=[agent("a", GENERATED)])).agents == ()

    def test_consecutive_windows_claim_a_boundary_record_exactly_once(self):
        edge = agent("edge", WINDOW_START)
        today = brief_mod.build(snap(agents=[edge]))
        yesterday = brief_mod.build(
            Snapshot(generated_at=WINDOW_START, agents=[edge]), window_hours=24
        )
        assert len(today.agents) == 1
        assert yesterday.agents == ()

    def test_the_rule_holds_for_commits_too(self):
        on_edge = Commit(sha="a" * 12, subject="edge", timestamp=WINDOW_START)
        past_edge = Commit(sha="b" * 12, subject="over", timestamp=GENERATED)
        result = brief_mod.build(snap(commits=[on_edge, past_edge]))
        assert [c.subject for c in result.commits] == ["edge"]


class TestOutsideTheWindow:
    def test_an_agent_dispatched_before_the_window_is_excluded(self):
        inside = agent("in", GENERATED - timedelta(hours=2), name="today")
        outside = agent("out", GENERATED - timedelta(hours=30), name="two days ago")
        result = brief_mod.build(snap(agents=[inside, outside]))
        assert [a.title for a in result.agents] == ["today"]

    def test_membership_is_dispatch_time_not_completion_time(self):
        """One stated rule, so membership never depends on which bound landed inside."""
        straddler = agent("s", GENERATED - timedelta(hours=30),
                          ended=GENERATED - timedelta(hours=1))
        assert brief_mod.build(snap(agents=[straddler])).agents == ()

    def test_a_file_last_touched_before_the_window_is_excluded(self):
        old = BuiltFile(path="/old.py", writes=1, first_seen=GENERATED - timedelta(days=9),
                        last_seen=GENERATED - timedelta(days=9))
        assert brief_mod.build(snap(files=[old])).files.touched == 0

    def test_real_fixture_history_falls_outside_a_fresh_daily_window(
        self, claude_home, empty_workspace
    ):
        """The fixture's transcripts are dated months back; the brief must say so."""
        collected = collect(claude_home=claude_home, workspace=empty_workspace)
        assert collected.agents  # the data exists...
        assert brief_mod.build(collected).is_empty  # ...but not in the last 24h


# ------------------------------------------------------------------ contents


class TestAgents:
    def test_task_descriptions_and_durations_are_carried(self):
        started = GENERATED - timedelta(hours=3)
        dispatched = agent("a1", started, name="Explore the codebase",
                           ended=started + timedelta(minutes=5))
        result = brief_mod.build(snap(agents=[dispatched]))
        assert result.agents[0].title == "Explore the codebase"
        assert result.agents[0].duration_label == "5m 0s"

    def test_a_running_agent_has_no_invented_duration(self):
        running = agent("a1", GENERATED - timedelta(hours=1), ended=None)
        assert brief_mod.build(snap(agents=[running])).agents[0].duration_label == "—"

    def test_agents_are_ordered_by_dispatch(self):
        late = agent("z", GENERATED - timedelta(hours=1), name="late")
        early = agent("a", GENERATED - timedelta(hours=9), name="early")
        result = brief_mod.build(snap(agents=[late, early]))
        assert [a.title for a in result.agents] == ["early", "late"]


class TestFiles:
    def test_created_and_revised_are_split_by_first_touch(self):
        fresh = BuiltFile(path="/new.py", writes=1, first_seen=GENERATED - timedelta(hours=2),
                          last_seen=GENERATED - timedelta(hours=1))
        old = BuiltFile(path="/old.py", writes=1, edits=4,
                        first_seen=GENERATED - timedelta(days=6),
                        last_seen=GENERATED - timedelta(hours=1))
        files = brief_mod.build(snap(files=[fresh, old])).files
        assert (files.touched, files.created, files.revised) == (2, 1, 1)

    def test_most_touched_paths_lead(self):
        recent = GENERATED - timedelta(hours=1)
        quiet = BuiltFile(path="/quiet.py", writes=1, first_seen=recent, last_seen=recent)
        busy = BuiltFile(path="/busy.py", writes=1, edits=20, first_seen=recent,
                         last_seen=recent)
        files = brief_mod.build(snap(files=[quiet, busy])).files
        assert [path for path, _ in files.top] == ["/busy.py", "/quiet.py"]

    def test_touch_counts_are_lifetime_and_labelled_as_such(self):
        """The individual touches are not timestamped, so they cannot be windowed."""
        built = BuiltFile(path="/a.py", writes=1, edits=9,
                          first_seen=GENERATED - timedelta(days=30),
                          last_seen=GENERATED - timedelta(hours=1))
        result = brief_mod.build(snap(files=[built]))
        assert result.files.top == (("/a.py", 10),)
        assert "lifetime" in render_to_text(result)


class TestVerification:
    def test_agent_runs_travel_with_their_dispatch_time(self):
        tester = agent("t", GENERATED - timedelta(hours=2), tools={"Bash": 2},
                       commands=["pytest -q", "ruff check src"])
        assert brief_mod.build(snap(agents=[tester])).verification.agent_runs == 2

    def test_a_session_wholly_inside_the_window_contributes_its_runs(self):
        session = Session(session_id="s1", started=GENERATED - timedelta(hours=5),
                          ended=GENERATED - timedelta(hours=4),
                          commands_run=["pytest -q", "ls"])
        result = brief_mod.build(snap(sessions=[session]))
        assert result.verification.session_runs == 1
        assert result.verification.straddling_sessions == 0

    def test_a_straddling_session_contributes_nothing_and_says_so(self):
        """Session commands carry no per-command stamp, so they cannot be split."""
        session = Session(session_id="s1", started=GENERATED - timedelta(days=4),
                          ended=GENERATED - timedelta(hours=2),
                          commands_run=["pytest -q", "pytest -x"])
        result = brief_mod.build(snap(sessions=[session]))
        assert result.verification.session_runs == 0
        assert result.verification.straddling_sessions == 1
        assert "shell commands carry no per-command timestamp" in flat(result)


class TestToolCalls:
    def test_agent_tool_counts_are_placeable(self):
        worker = agent("a", GENERATED - timedelta(hours=1),
                       tools={"Read": 9, "Edit": 4, "Bash": 4})
        result = brief_mod.build(snap(agents=[worker]))
        assert result.tool_calls == 17
        assert result.tools[0] == ("Read", 9)

    def test_a_straddling_sessions_tool_counts_are_left_out(self):
        """A session total spans its whole life, so it cannot be attributed in part."""
        session = Session(session_id="s1", started=GENERATED - timedelta(days=3),
                          ended=GENERATED - timedelta(hours=1),
                          tools=ToolUsage(counts={"Read": 500}))
        result = brief_mod.build(snap(sessions=[session]))
        assert result.tool_calls == 0
        assert result.tool_sources == 0

    def test_a_contained_session_does_contribute(self):
        session = Session(session_id="s1", started=GENERATED - timedelta(hours=3),
                          ended=GENERATED - timedelta(hours=2),
                          tools=ToolUsage(counts={"Read": 5}))
        assert brief_mod.build(snap(sessions=[session])).tool_calls == 5


class TestBusiestDivision:
    def test_the_division_with_the_most_records_wins(self):
        commits = [Commit(sha=f"{i}" * 12, subject=f"c{i}",
                          timestamp=GENERATED - timedelta(hours=1)) for i in range(5)]
        result = brief_mod.build(snap(commits=commits))
        busiest = result.busiest_division
        assert busiest is not None
        assert busiest.code == "OPS"
        assert "5 commits" in busiest.basis

    def test_a_tie_is_reported_as_a_tie_not_broken_arbitrarily(self):
        recent = GENERATED - timedelta(hours=1)
        built = BuiltFile(path="/a.py", writes=1, first_seen=recent, last_seen=recent)
        commit = Commit(sha="a" * 12, subject="one", timestamp=recent)
        result = brief_mod.build(snap(files=[built], commits=[commit]))
        assert result.busiest_division is None
        assert {d.code for d in result.tied_divisions} == {"ENG", "OPS"}
        assert "tied" in render_to_text(result)

    def test_an_empty_window_names_no_busiest_division(self):
        assert brief_mod.build(snap()).busiest_division is None

    def test_agents_are_seated_by_their_tool_record_like_the_floor(self):
        """Two places in HQ must not disagree about where the same agent works."""
        builder = agent("b", GENERATED - timedelta(hours=1), agent_type="Explore",
                        tools={"Write": 6, "Edit": 6})
        result = brief_mod.build(snap(agents=[builder]))
        assert result.busiest_division is not None
        assert result.busiest_division.code == "ENG"


# ------------------------------------------------------------- unplaceability


class TestRecordsWithoutTimestamps:
    """Neither assumed recent nor dropped — counted, and reported."""

    def test_undated_records_are_counted_by_kind(self):
        result = brief_mod.build(snap(
            agents=[agent("a", None)],
            files=[BuiltFile(path="/x.py", writes=1)],
            commits=[Commit(sha="c" * 12, subject="no date")],
            sessions=[Session(session_id="s1")],
        ))
        assert result.unplaceable.agents == 1
        assert result.unplaceable.files == 1
        assert result.unplaceable.commits == 1
        assert result.unplaceable.sessions == 1
        assert result.unplaceable.total == 4

    def test_undated_records_do_not_enter_the_window(self):
        result = brief_mod.build(snap(agents=[agent("a", None)]))
        assert result.agents == ()
        assert result.files.touched == 0

    def test_the_count_is_reported_even_when_the_window_is_empty(self):
        result = brief_mod.build(snap(commits=[Commit(sha="c" * 12, subject="no date")]))
        text = render_to_text(result)
        assert result.is_empty
        assert "carry no timestamp" in text
        assert "1 commit" in text

    def test_the_count_is_reported_alongside_a_busy_window(self):
        result = brief_mod.build(snap(
            agents=[agent("a", GENERATED - timedelta(hours=1)), agent("b", None)],
        ))
        assert "carry no timestamp" in render_to_text(result)

    def test_nothing_is_said_when_every_record_is_dated(self):
        result = brief_mod.build(snap(agents=[agent("a", GENERATED - timedelta(hours=1))]))
        assert "carry no timestamp" not in render_to_text(result)

    @pytest.mark.parametrize("started", [None, GENERATED - timedelta(hours=1)])
    def test_a_session_with_one_usable_bound_is_still_placeable(self, started):
        """Half a span is enough to know the session touched the window."""
        session = Session(session_id="s1", started=started,
                          ended=GENERATED - timedelta(minutes=30))
        result = brief_mod.build(snap(sessions=[session]))
        assert len(result.sessions) == 1
        assert result.unplaceable.sessions == 0


# ----------------------------------------------------------------- rendering


class TestQuietDay:
    def test_an_empty_window_says_so_rather_than_showing_zeros(self):
        text = render_to_text(brief_mod.build(snap()))
        assert "Nothing recorded in the last 24h" in text
        assert "a quiet day is a real answer" in text

    def test_an_empty_window_shows_no_tables_of_zeros(self):
        text = render_to_text(brief_mod.build(snap()))
        for heading in ("Agents dispatched", "Commits landed", "Busiest division"):
            assert heading not in text

    def test_the_empty_state_states_the_window_was_not_widened(self):
        assert "not widened" in render_to_text(brief_mod.build(snap()))

    def test_a_shorter_window_is_named_accurately_in_the_empty_state(self):
        text = render_to_text(brief_mod.build(snap(), window_hours=6))
        assert "Nothing recorded in the last 6h" in text

    def test_empty_snapshot_renders_without_raising(self):
        render_to_text(brief_mod.build(Snapshot(generated_at=datetime.now(UTC))))


class TestRendering:
    @pytest.fixture
    def busy(self):
        started = GENERATED - timedelta(hours=2)
        return brief_mod.build(snap(
            agents=[agent("a1", started, name="Explore the codebase",
                          ended=started + timedelta(minutes=4),
                          tools={"Read": 12, "Grep": 3},
                          commands=["pytest -q"])],
            files=[BuiltFile(path="/home/user/Demo/a.py", writes=1, edits=2,
                             first_seen=started, last_seen=started)],
            commits=[Commit(sha="abcdef123456", subject="Add the brief",
                            timestamp=started)],
        ))

    def test_the_window_is_printed(self, busy):
        assert "2026-07-26 12:00 UTC (24h)" in render_to_text(busy)

    def test_agent_task_and_duration_appear(self, busy):
        text = render_to_text(busy)
        assert "Explore the codebase" in text
        assert "4m 0s" in text

    def test_commit_subject_appears(self, busy):
        assert "Add the brief" in render_to_text(busy)

    def test_tool_breakdown_appears(self, busy):
        assert "Read 12" in render_to_text(busy)

    def test_file_counts_are_labelled_as_derived_from_tool_calls(self, busy):
        assert "not a filesystem diff" in render_to_text(busy)

    def test_effort_measure_is_stated(self, busy):
        assert "measured text emitted" in render_to_text(busy)

    def test_narrow_console_does_not_raise(self, busy):
        assert "DAILY BRIEF" in render_to_text(busy, width=60)

    def test_real_collected_data_renders(self, claude_home, empty_workspace):
        collected = collect(claude_home=claude_home, workspace=empty_workspace)
        text = render_to_text(brief_mod.build(collected, since=datetime(2026, 1, 1,
                                                                       tzinfo=UTC)))
        assert "Explore the codebase" in text
