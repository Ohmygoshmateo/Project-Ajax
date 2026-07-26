"""Reporting lines, and the two ways an org chart can lie.

A tree looks authoritative, so these tests are mostly about what the module
refuses to do: it must not invent an edge, and it must not lose an agent that
has no edge to draw.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import pytest
from rich.console import Console

from ajax_hq import lineage
from ajax_hq.collect import collect
from ajax_hq.model import Agent, Provenance, Session, Snapshot, Status, ToolUsage

OTHER_SESSION = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def snapshot(claude_home, empty_workspace) -> Snapshot:
    return collect(claude_home=claude_home, workspace=empty_workspace)


def render_to_text(org: lineage.Org, width: int = 140) -> str:
    buffer = StringIO()
    lineage.render(org, Console(file=buffer, width=width, force_terminal=False))
    return buffer.getvalue()


def worker(agent_id: str, *, session_id: str | None, name: str | None = None,
           tools: dict[str, int] | None = None,
           provenance: Provenance = Provenance.LIVE) -> Agent:
    return Agent(
        agent_id=agent_id,
        description=name or agent_id,
        agent_type="general-purpose",
        session_id=session_id,
        status=Status.COMPLETED,
        tools=ToolUsage(counts=dict(tools or {})),
        provenance=provenance,
    )


def snap(*, sessions: list[Session], agents: list[Agent]) -> Snapshot:
    snapshot = Snapshot(generated_at=datetime.now(UTC))
    snapshot.sessions = sessions
    snapshot.agents = agents
    return snapshot


class TestNoAgentIsLost:
    """The worst failure this module can have is a structural gap eating a row."""

    def test_every_agent_appears_exactly_once(self, snapshot):
        org = lineage.build(snapshot)
        seen = [report.agent.agent_id for report in org.reports]
        assert sorted(seen) == sorted(a.agent_id for a in snapshot.agents)
        assert len(seen) == len(set(seen))

    def test_rendered_tree_holds_every_agent_in_the_snapshot(self, snapshot):
        text = render_to_text(lineage.build(snapshot))
        for agent in snapshot.agents:
            assert agent.title in text

    def test_count_survives_every_kind_of_gap(self):
        """Attached, orphaned, and misattributed agents all still reach a line."""
        snapshot = snap(
            sessions=[Session(session_id="s1")],
            agents=[
                worker("a1", session_id="s1"),
                worker("a2", session_id=None),
                worker("a3", session_id="nowhere"),
                worker("a4", session_id=""),
            ],
        )
        org = lineage.build(snapshot)
        assert org.agent_count == len(snapshot.agents) == 4

    def test_duplicate_session_ids_do_not_double_count(self):
        """Two Session objects sharing an id must not clone the agents beneath."""
        snapshot = snap(
            sessions=[Session(session_id="s1"), Session(session_id="s1")],
            agents=[worker("a1", session_id="s1")],
        )
        org = lineage.build(snapshot)
        assert org.agent_count == 1
        assert len(org.lines) == 2  # both sessions still get a line


class TestNoInventedParent:
    def test_missing_session_id_is_unattributed_not_dropped(self):
        snapshot = snap(sessions=[Session(session_id="s1")],
                        agents=[worker("orphan", session_id=None)])
        org = lineage.build(snapshot)
        assert [r.agent.agent_id for r in org.unattributed] == ["orphan"]
        assert org.unattributed[0].reason == lineage.NO_SESSION_RECORDED

    def test_a_lone_session_does_not_adopt_an_orphan(self):
        """The tempting shortcut: one session present, so it must be the parent."""
        snapshot = snap(sessions=[Session(session_id="s1")],
                        agents=[worker("orphan", session_id=None)])
        org = lineage.build(snapshot)
        assert org.lines[0].reports == []

    def test_timestamp_proximity_does_not_create_an_edge(self):
        """An orphan sitting inside a session's window is still an orphan."""
        started = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
        ended = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
        session = Session(session_id="s1", started=started, ended=ended)
        orphan = worker("orphan", session_id=None)
        orphan.started, orphan.ended = started, ended
        org = lineage.build(snap(sessions=[session], agents=[orphan]))
        assert org.lines[0].reports == []
        assert len(org.unattributed) == 1

    def test_unknown_session_id_is_unattributed_with_the_id_stated(self):
        snapshot = snap(sessions=[Session(session_id="s1")],
                        agents=[worker("stray", session_id=OTHER_SESSION)])
        org = lineage.build(snapshot)
        assert len(org.unattributed) == 1
        reason = org.unattributed[0].reason
        assert OTHER_SESSION[:8] in reason
        assert "not in this snapshot" in reason

    def test_reasons_are_printed_not_just_stored(self):
        snapshot = snap(
            sessions=[Session(session_id="s1")],
            agents=[worker("o1", session_id=None), worker("o2", session_id=OTHER_SESSION)],
        )
        text = render_to_text(lineage.build(snapshot))
        assert lineage.UNATTRIBUTED in text
        assert "no dispatching session recorded" in text
        assert "not in this snapshot" in text

    def test_agents_attach_only_to_the_session_they_name(self):
        snapshot = snap(
            sessions=[Session(session_id="s1"), Session(session_id="s2")],
            agents=[worker("a1", session_id="s2"), worker("a2", session_id="s1")],
        )
        org = lineage.build(snapshot)
        placed = {line.session.session_id: [r.agent.agent_id for r in line.reports]
                  for line in org.lines}
        assert placed == {"s1": ["a2"], "s2": ["a1"]}


class TestSessionsWithoutReports:
    def test_a_session_that_delegated_nothing_is_kept(self):
        org = lineage.build(snap(sessions=[Session(session_id="s1", name="solo")], agents=[]))
        assert len(org.lines) == 1
        assert org.lines[0].reports == []

    def test_it_renders_as_a_session_with_no_reports(self):
        text = render_to_text(
            lineage.build(snap(sessions=[Session(session_id="s1", name="solo")], agents=[]))
        )
        assert "solo" in text
        assert "no agents dispatched" in text

    def test_headcount_label_is_singular_for_one(self):
        line = lineage.Line(session=Session(session_id="s1"))
        assert line.headcount_label == "0 reports"
        line.reports.append(lineage.Report(agent=worker("a", session_id="s1")))
        assert line.headcount_label == "1 report"


class TestRestoredRecordsAreMarked:
    """Archival rows came from a committed snapshot, not from this machine."""

    def test_a_restored_agent_is_flagged(self):
        org = lineage.build(snap(
            sessions=[Session(session_id="s1")],
            agents=[worker("a1", session_id="s1", provenance=Provenance.RESTORED)],
        ))
        assert org.lines[0].reports[0].restored is True
        assert org.restored_count == 1

    def test_a_restored_agent_is_badged_in_the_tree(self):
        text = render_to_text(lineage.build(snap(
            sessions=[Session(session_id="s1")],
            agents=[worker("a1", session_id="s1", name="archived work",
                           provenance=Provenance.RESTORED)],
        )))
        assert "ARCHIVAL" in text
        assert "restored from committed history" in text

    def test_a_restored_session_is_badged_too(self):
        text = render_to_text(lineage.build(snap(
            sessions=[Session(session_id="s1", name="old stream",
                              provenance=Provenance.RESTORED)],
            agents=[],
        )))
        assert "ARCHIVAL" in text

    def test_live_records_carry_no_badge(self, snapshot):
        org = lineage.build(snapshot)
        assert org.restored_count == 0
        assert "ARCHIVAL" not in render_to_text(org)


class TestEmptyState:
    def test_empty_snapshot_builds_an_empty_org(self):
        org = lineage.build(Snapshot(generated_at=datetime.now(UTC)))
        assert org.is_empty
        assert org.reports == []

    def test_empty_snapshot_renders_an_explicit_state_without_raising(self):
        text = render_to_text(lineage.build(Snapshot(generated_at=datetime.now(UTC))))
        assert "No sessions or agents found" in text
        # An empty tree root would read as a claim about the org's shape.
        assert "AJAX HQ" in text

    def test_agents_but_no_sessions_still_render(self):
        """Everything unattributed is a valid org, and must not fall to the empty state."""
        text = render_to_text(lineage.build(
            snap(sessions=[], agents=[worker("a1", session_id="gone", name="loose work")])
        ))
        assert "No sessions or agents found" not in text
        assert "loose work" in text


class TestRowContents:
    def test_each_report_shows_task_wing_duration_tools_and_output(self):
        agent = worker("a1", session_id="s1", name="Build the parser",
                       tools={"Write": 4, "Read": 2})
        agent.started = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
        agent.ended = datetime(2026, 3, 2, 9, 0, 42, tzinfo=UTC)
        agent.output_chars = 12_400
        text = render_to_text(lineage.build(
            snap(sessions=[Session(session_id="s1")], agents=[agent])
        ))
        assert "Build the parser" in text
        assert "[ENG]" in text       # division from the tool record
        assert "42s" in text         # duration
        assert "6 tools" in text     # tool count
        assert "12.4k out" in text   # measured output

    def test_output_is_measured_chars_never_reported_tokens(self):
        """output_tokens is a placeholder in subagent transcripts."""
        agent = worker("a1", session_id="s1", name="wrote a lot", tools={"Write": 1})
        agent.output_tokens = 28
        agent.output_chars = 46_861
        text = render_to_text(lineage.build(
            snap(sessions=[Session(session_id="s1")], agents=[agent])
        ))
        assert "46.9k out" in text
        assert "28 out" not in text

    def test_wing_comes_from_behaviour_not_declared_type(self):
        agent = worker("a1", session_id="s1", tools={"Write": 5, "Edit": 5})
        agent.agent_type = "Explore"
        report = lineage.Report(agent=agent)
        assert report.wing == "ENG"

    def test_fixture_agents_report_to_the_session_that_dispatched_them(self, snapshot):
        org = lineage.build(snapshot)
        assert len(org.lines) == 1
        assert org.unattributed == []
        titles = [r.agent.title for r in org.lines[0].reports]
        assert "Explore the codebase" in titles
        assert "Verify the API" in titles

    def test_long_task_descriptions_are_clipped_not_wrapped(self):
        clipped = lineage._clip("A" * 300, lineage.TITLE_CHARS)
        assert len(clipped) == lineage.TITLE_CHARS
        assert clipped.endswith("…")

    def test_newlines_in_a_description_do_not_break_a_row(self):
        assert "\n" not in lineage._clip("first\nsecond", 40)

    def test_narrow_console_does_not_raise(self, snapshot):
        assert "LINEAGE" in render_to_text(lineage.build(snapshot), width=60)


class TestDispatchDisagreement:
    """The two witnesses can disagree; the disagreement is reported, not resolved."""

    def test_an_id_named_only_by_the_session_is_flagged(self):
        session = Session(session_id="s1", agent_ids=["a1", "ghost"])
        org = lineage.build(snap(sessions=[session], agents=[worker("a1", session_id="s1")]))
        assert org.dispatched_without_transcript == ["ghost"]
        assert "no record in this snapshot" in render_to_text(org)

    def test_no_flag_when_the_witnesses_agree(self, snapshot):
        assert lineage.build(snapshot).dispatched_without_transcript == []
