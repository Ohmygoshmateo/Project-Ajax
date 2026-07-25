"""The virtual floor, and the measured-output fix it depends on.

The floor's whole claim is that it is not theatre, so the tests that matter most
are the ones asserting it cannot invent or lose an occupant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import pytest
from rich.console import Console

from ajax_hq import floor
from ajax_hq.collect import collect
from ajax_hq.model import Agent, Division, Session, Snapshot, Status, ToolUsage


@pytest.fixture
def snapshot(claude_home, empty_workspace) -> Snapshot:
    return collect(claude_home=claude_home, workspace=empty_workspace)


def render_to_text(snap: Snapshot, width: int = 100) -> str:
    buffer = StringIO()
    floor.render(snap, Console(file=buffer, width=width, force_terminal=False))
    return buffer.getvalue()


class TestNoInventedOccupants:
    """The core guarantee: a desk exists only where an agent does."""

    def test_desk_count_equals_agents_plus_sessions(self, snapshot):
        wings = floor.assign(snapshot)
        desks = sum(len(w.desks) for w in wings)
        assert desks == len(snapshot.agents) + len(snapshot.sessions)

    def test_every_desk_name_traces_to_a_real_occupant(self, snapshot):
        real = {a.title for a in snapshot.agents} | {s.title for s in snapshot.sessions}
        for wing in floor.assign(snapshot):
            for desk in wing.desks:
                assert desk.name in real

    def test_no_agent_is_dropped(self, snapshot):
        placed = {d.name for w in floor.assign(snapshot) for d in w.desks}
        for agent in snapshot.agents:
            assert agent.title in placed

    def test_empty_snapshot_produces_no_desks(self):
        wings = floor.assign(Snapshot(generated_at=datetime.now(UTC)))
        assert sum(len(w.desks) for w in wings) == 0

    def test_empty_snapshot_renders_without_raising(self):
        text = render_to_text(Snapshot(generated_at=datetime.now(UTC)))
        assert "No agents or sessions found" in text


class TestAssignment:
    def test_subagents_go_to_rnd(self, snapshot):
        rnd = next(w for w in floor.assign(snapshot) if w.code == "RND")
        assert len(rnd.desks) == len(snapshot.agents)

    def test_principal_gets_the_executive_office(self, snapshot):
        exo = next(w for w in floor.assign(snapshot) if w.code == "EXO")
        assert len(exo.desks) == 1
        assert exo.desks[0].principal is True

    @pytest.mark.parametrize("agent_type", ["Explore", "Plan", "general-purpose", "claude"])
    def test_known_types_are_placed(self, agent_type):
        assert floor._wing_for(Agent(agent_id="x", agent_type=agent_type)) == "RND"

    @pytest.mark.parametrize("agent_type", ["some-future-type", "", None, "UNKNOWN"])
    def test_unknown_types_are_placed_not_dropped(self, agent_type):
        """Losing a real agent to a classification gap is worse than a rough seat."""
        assert floor._wing_for(Agent(agent_id="x", agent_type=agent_type)) == "RND"

    def test_unknown_type_still_reaches_a_desk(self):
        snap = Snapshot(generated_at=datetime.now(UTC))
        snap.agents = [Agent(agent_id="zz", description="Mystery work",
                             agent_type="some-future-type", status=Status.COMPLETED)]
        placed = [d for w in floor.assign(snap) for d in w.desks]
        assert len(placed) == 1
        # The desk shows the real type even though the rule did not recognise it.
        assert placed[0].kind == "some-future-type"

    def test_busiest_agents_are_seated_first(self):
        snap = Snapshot(generated_at=datetime.now(UTC))
        quiet = Agent(agent_id="a", description="quiet", agent_type="Explore")
        busy = Agent(agent_id="b", description="busy", agent_type="Explore")
        busy.tools = ToolUsage(counts={"Bash": 40})
        snap.agents = [quiet, busy]
        rnd = next(w for w in floor.assign(snap) if w.code == "RND")
        assert [d.name for d in rnd.desks] == ["busy", "quiet"]


class TestVacancy:
    def test_unstaffed_wings_are_empty_not_padded(self, snapshot):
        for wing in floor.assign(snapshot):
            if wing.code not in {"RND", "EXO"}:
                assert wing.desks == []

    def test_vacant_wings_carry_a_reason(self, snapshot):
        for wing in floor.assign(snapshot):
            if not wing.occupied:
                assert wing.vacancy_reason

    def test_reason_cites_the_divisions_real_figure(self):
        snap = Snapshot(generated_at=datetime.now(UTC))
        snap.divisions = [Division(code="ENG", name="Engineering", korean="엔지니어링부",
                                   mandate="", metrics=[("Build calls", "93")])]
        reason = floor._vacancy_reason("ENG", snap.divisions)
        assert "93" in reason and "principal" in reason

    def test_reason_degrades_when_the_metric_is_absent(self):
        assert floor._vacancy_reason("ENG", []) == "No delegated work recorded."

    def test_zero_metric_does_not_claim_work_happened(self):
        divisions = [Division(code="OPS", name="Operations", korean="운영부",
                              mandate="", metrics=[("Commits", "0")])]
        assert floor._vacancy_reason("OPS", divisions) == "No delegated work recorded."

    def test_occupancy_label_is_singular_for_one(self):
        wing = floor.Wing("EXO", "Executive Office", "비서실")
        assert wing.occupancy_label == "0 desks"
        wing.desks.append(floor.Desk("n", "k", Status.COMPLETED, "1s", 1, "1 out", "t"))
        assert wing.occupancy_label == "1 desk"


class TestRendering:
    def test_all_six_wings_appear(self, snapshot):
        text = render_to_text(snapshot)
        for code in floor.WING_ORDER:
            assert code in text

    def test_agent_names_appear(self, snapshot):
        text = render_to_text(snapshot)
        assert "Explore the codebase" in text

    def test_states_that_vacancies_are_real(self, snapshot):
        assert "Vacant wings are real" in render_to_text(snapshot)

    def test_states_the_effort_measure(self, snapshot):
        assert "measured text emitted" in render_to_text(snapshot)

    def test_long_names_are_clipped_not_wrapped(self):
        long = "A" * 200
        assert len(floor._clip(long, 26)) == 26
        assert floor._clip(long, 26).endswith("…")

    def test_newlines_in_names_are_flattened(self):
        """A task description spanning lines would break the panel layout."""
        assert "\n" not in floor._clip("first\nsecond", 40)

    def test_narrow_console_does_not_raise(self, snapshot):
        assert "RND" in render_to_text(snapshot, width=60)

    def test_wrap_helper_respects_width(self):
        lines = floor._wrap("word " * 60, 40)
        assert all(len(line) <= 40 for line in lines)


class TestMeasuredOutput:
    """The fix: agent effort must not come from the unreliable usage field."""

    def test_output_chars_counted_from_content(self, snapshot):
        # The fixture's agent emits a known report string.
        agent = next(a for a in snapshot.agents if a.description == "Explore the codebase")
        assert agent.output_chars > 0
        assert agent.output_chars == len(agent.report)

    def test_desk_shows_measured_output_not_tokens(self):
        agent = Agent(agent_id="x", description="d", agent_type="Explore",
                      output_tokens=3, output_chars=46_861)
        desk = floor.Desk.from_agent(agent)
        assert "46.9k" in desk.output
        assert "3" not in desk.output.replace("46.9k out", "")

    def test_sessions_still_show_real_tokens(self):
        session = Session(session_id="s" * 12, input_tokens=700_000, output_tokens=29_387)
        assert "tok" in floor.Desk.from_session(session).output

    @pytest.mark.parametrize(
        ("tokens", "chars", "plausible"),
        [
            (28, 46_861, False),    # observed: Design plan agent
            (755, 13_346, False),   # observed: Verify agent
            (17, 1_990, False),     # observed: Explore agent
            (3_000, 12_000, True),  # a healthy 0.25 ratio
            (0, 100, True),         # too small to judge
        ],
    )
    def test_plausibility_detection(self, tokens, chars, plausible):
        agent = Agent(agent_id="x", output_tokens=tokens, output_chars=chars)
        assert agent.output_tokens_are_plausible is plausible

    def test_tool_heavy_agents_are_not_false_flagged(self):
        """Tool JSON counts as output tokens but not as text, skewing the ratio up.

        So a heavy tool user reads as *more* plausible, never less — the check
        can only fire on genuine under-reporting.
        """
        agent = Agent(agent_id="x", output_tokens=9_000, output_chars=1_000)
        assert agent.output_tokens_are_plausible is True

    def test_short_outputs_do_not_trigger_the_note(self, snapshot):
        """Below the judging floor there is nothing to conclude either way.

        The fixture's agents emit only a few dozen characters, so the ratio is
        not evidence of anything and no warning should fire.
        """
        assert snapshot.schema.token_note is None

    def test_schema_note_is_raised_once_not_per_row(self):
        from ajax_hq.model import SchemaHealth

        health = SchemaHealth(implausible_output_tokens=["a1", "b2", "c3", "d4"])
        note = health.token_note
        assert note is not None
        assert "4 subagent(s)" in note
        assert "measured text emitted" in note
        # One statement covering all affected agents, not one line each.
        assert note.count("subagent") == 1

    def test_no_note_when_counts_are_sound(self):
        from ajax_hq.model import SchemaHealth

        assert SchemaHealth().token_note is None

    def test_output_label_formats_thousands(self):
        assert Agent(agent_id="x", output_chars=46_861).output_label == "46.9k"
        assert Agent(agent_id="x", output_chars=812).output_label == "812"
