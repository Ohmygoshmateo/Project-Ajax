"""Transcript parsing, including drift and malformed input."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ajax_hq.behaviour import wing_for
from ajax_hq.model import SchemaHealth, Status
from ajax_hq.sources import transcripts
from tests.conftest import (
    AGENT_A,
    AGENT_B,
    AGENT_ONLY_FILE,
    BUILDER,
    SECRET_PROMPT,
    SECRET_REPORT,
    SESSION_ID,
    SHARED_FILE,
    SHARED_SESSION_ID,
)


class TestTimestamps:
    def test_parses_zulu(self):
        stamp = transcripts.parse_timestamp("2026-03-02T09:00:00.000Z")
        assert stamp is not None and stamp.year == 2026

    def test_parses_offset(self):
        assert transcripts.parse_timestamp("2026-03-02T09:00:00+00:00") is not None

    @pytest.mark.parametrize("value", [None, "", "not a date", 12345, {}, "2026-13-45"])
    def test_bad_values_return_none(self, value):
        assert transcripts.parse_timestamp(value) is None


class TestResilience:
    """A format change must cost panels, never the page."""

    def test_malformed_lines_are_counted_not_raised(self, claude_home: Path):
        _, _, _, health = transcripts.load(claude_home)
        assert health.records_unparsed == 2  # one invalid, one truncated
        assert health.records_read > 2
        assert not health.healthy

    def test_unknown_record_types_are_recorded(self, claude_home: Path):
        _, _, _, health = transcripts.load(claude_home)
        assert "brand-new-type" in health.unknown_record_types

    def test_client_version_detected(self, claude_home: Path):
        _, _, _, health = transcripts.load(claude_home)
        assert health.client_versions == ["2.1.220"]

    def test_missing_home_is_not_an_error(self, tmp_path: Path):
        sessions, agents, files, health = transcripts.load(tmp_path / "nope")
        assert (sessions, agents, files) == ([], [], [])
        assert health.records_read == 0

    def test_unreadable_file_is_reported_not_raised(self, tmp_path: Path):
        health = SchemaHealth()
        list(transcripts.iter_records(tmp_path / "missing.jsonl", health))
        assert health.warnings

    def test_summary_reads_clearly(self, claude_home: Path):
        _, _, _, health = transcripts.load(claude_home)
        assert "unreadable" in health.summary


class TestSessionParsing:
    def test_one_session_found(self, claude_home: Path):
        sessions, _, _, _ = transcripts.load(claude_home)
        assert len(sessions) == 1
        assert sessions[0].session_id == SESSION_ID

    def test_metadata_extracted(self, claude_home: Path):
        session = transcripts.load(claude_home)[0][0]
        assert session.branch == "feature/demo"
        assert session.cwd == "/home/user/Demo"
        assert session.client_version == "2.1.220"

    def test_span_covers_first_to_last_record(self, claude_home: Path):
        session = transcripts.load(claude_home)[0][0]
        assert session.duration_seconds == pytest.approx(33 * 60, abs=90)

    def test_user_turns_exclude_tool_results(self, claude_home: Path):
        """Tool results arrive as user records but are not user turns."""
        session = transcripts.load(claude_home)[0][0]
        assert session.user_turns == 1

    def test_decision_points_counted(self, claude_home: Path):
        session = transcripts.load(claude_home)[0][0]
        assert session.decisions == 2  # AskUserQuestion + ExitPlanMode

    def test_tool_counts(self, claude_home: Path):
        session = transcripts.load(claude_home)[0][0]
        assert session.tools.counts["Write"] == 1
        assert session.tools.counts["Agent"] == 2

    def test_bash_commands_retained(self, claude_home: Path):
        session = transcripts.load(claude_home)[0][0]
        assert any("pytest" in c for c in session.commands_run)

    def test_models_recorded(self, claude_home: Path):
        session = transcripts.load(claude_home)[0][0]
        assert "claude-opus-5" in session.models


class TestAgentExtraction:
    def test_both_agents_found(self, claude_home: Path):
        _, agents, _, _ = transcripts.load(claude_home)
        assert {a.agent_id for a in agents} == {AGENT_A, AGENT_B}

    def test_description_and_type_come_from_the_dispatcher(self, claude_home: Path):
        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        assert agents[AGENT_A].description == "Explore the codebase"
        assert agents[AGENT_A].agent_type == "Explore"
        assert agents[AGENT_B].agent_type == "general-purpose"

    def test_duration_from_the_agents_own_transcript(self, claude_home: Path):
        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        assert agents[AGENT_A].duration_seconds == pytest.approx(30, abs=2)

    def test_tools_and_files_attributed_to_the_agent(self, claude_home: Path):
        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        assert agents[AGENT_B].tools.counts["WebSearch"] == 1
        assert "/home/user/Demo/b.py" in agents[AGENT_B].files_touched

    def test_status_completed_when_a_transcript_exists(self, claude_home: Path):
        agents = transcripts.load(claude_home)[1]
        assert all(a.status is Status.COMPLETED for a in agents)

    def test_final_text_block_becomes_the_report(self, claude_home: Path):
        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        assert agents[AGENT_A].report == SECRET_REPORT

    def test_prompt_captured_for_local_drilldown(self, claude_home: Path):
        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        assert agents[AGENT_A].prompt == SECRET_PROMPT

    def test_agents_sorted_by_dispatch_time(self, claude_home: Path):
        agents = transcripts.load(claude_home)[1]
        assert [a.agent_id for a in agents] == [AGENT_A, AGENT_B]

    def test_shell_record_is_classified_at_parse_time(self, claude_home: Path):
        """The counts must be taken while the commands still exist — they are
        never archived, so nothing downstream can recompute them."""
        subagents = claude_home / "projects" / "-home-user-Demo" / SESSION_ID / "subagents"
        path = subagents / f"agent-{AGENT_A}.jsonl"
        with path.open("a") as fh:
            for command in ("pytest -q", "ruff check .", "git commit -m 'done'", "ls -la"):
                fh.write(json.dumps({
                    "type": "assistant", "agentId": AGENT_A, "sessionId": SESSION_ID,
                    "timestamp": "2026-03-02T09:01:50.000Z",
                    "message": {"content": [
                        {"type": "tool_use", "name": "Bash",
                         "input": {"command": command}, "id": "z1"}]},
                }) + "\n")

        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        assert agents[AGENT_A].verify_runs == 2
        assert agents[AGENT_A].ship_actions == 1

    def test_no_commands_means_no_counts(self, claude_home: Path):
        """`ls` is neither verification nor shipping, and is not counted as either."""
        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        assert agents[AGENT_A].commands_run == ["ls"]
        assert (agents[AGENT_A].verify_runs, agents[AGENT_A].ship_actions) == (0, 0)

    def test_dispatch_without_a_transcript_still_appears(self, claude_home: Path, tmp_path):
        """Work that was requested happened, even if the transcript is missing."""
        subagents = claude_home / "projects" / "-home-user-Demo" / SESSION_ID / "subagents"
        (subagents / f"agent-{AGENT_A}.jsonl").unlink()

        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        assert AGENT_A in agents
        assert agents[AGENT_A].status is Status.RUNNING
        assert agents[AGENT_A].description == "Explore the codebase"


class TestTokenAccounting:
    def test_cache_reads_are_kept_separate(self, claude_home: Path):
        """Folding cache reads into the headline would inflate it enormously."""
        agents = {a.agent_id: a for a in transcripts.load(claude_home)[1]}
        agent = agents[AGENT_A]
        assert agent.total_tokens == 75  # 40+20 then 10+5
        assert agent.cache_tokens == 9000

    def test_session_tokens_exclude_cache(self, claude_home: Path):
        session = transcripts.load(claude_home)[0][0]
        assert session.total_tokens == session.input_tokens + session.output_tokens


class TestFileAttribution:
    def test_writes_and_edits_are_distinguished(self, claude_home: Path):
        files = {f.path: f for f in transcripts.load(claude_home)[2]}
        entry = files["/home/user/Demo/a.py"]
        assert entry.writes == 1
        assert entry.edits == 1
        assert entry.touches == 2

    def test_first_and_last_seen_recorded(self, claude_home: Path):
        files = {f.path: f for f in transcripts.load(claude_home)[2]}
        entry = files["/home/user/Demo/a.py"]
        assert entry.first_seen is not None and entry.last_seen is not None
        assert entry.last_seen > entry.first_seen

    def test_a_file_only_a_subagent_built_is_in_the_inventory(self, claude_home: Path):
        """The regression this class exists for.

        A subagent writes through the principal's tree but records the call only
        in its own transcript, so a reader that walks session transcripts alone
        reports the file as never having been built at all.
        """
        files = {f.path: f for f in transcripts.load(claude_home)[2]}
        assert "/home/user/Demo/b.py" in files

    def test_a_principal_built_file_credits_no_agent(self, claude_home: Path):
        files = {f.path: f for f in transcripts.load(claude_home)[2]}
        assert files["/home/user/Demo/a.py"].agent_ids == []
        assert files["/home/user/Demo/a.py"].delegated is False

    def test_a_delegated_file_names_the_agent_that_built_it(self, claude_home: Path):
        files = {f.path: f for f in transcripts.load(claude_home)[2]}
        entry = files["/home/user/Demo/b.py"]
        assert entry.agent_ids == [AGENT_B]
        assert entry.delegated is True


class TestSharedTreeAttribution:
    """Attribution when a session and a subagent build through the same tree."""

    def test_agent_only_file_counts_every_call(self, shared_tree_home: Path):
        files = {f.path: f for f in transcripts.load(shared_tree_home)[2]}
        entry = files[AGENT_ONLY_FILE]
        assert (entry.writes, entry.edits) == (1, 2)
        assert entry.agent_ids == [BUILDER]

    def test_a_shared_file_sums_both_parties_without_double_counting(
        self, shared_tree_home: Path
    ):
        """1 write + 1 edit from the session, 1 edit from the agent — exactly.

        The two record sets are disjoint, so summing is correct; this is the test
        that fails if a subagent's calls are ever echoed into its session's
        transcript and counted twice.
        """
        files = {f.path: f for f in transcripts.load(shared_tree_home)[2]}
        entry = files[SHARED_FILE]
        assert (entry.writes, entry.edits) == (1, 2)
        assert entry.touches == 3

    def test_a_shared_file_credits_only_the_subagent_that_touched_it(
        self, shared_tree_home: Path
    ):
        """agent_ids answers "which agents", not "who did the most"."""
        files = {f.path: f for f in transcripts.load(shared_tree_home)[2]}
        assert files[SHARED_FILE].agent_ids == [BUILDER]

    def test_the_agent_still_lists_the_files_itself(self, shared_tree_home: Path):
        agents = {a.agent_id: a for a in transcripts.load(shared_tree_home)[1]}
        assert sorted(agents[BUILDER].files_touched) == sorted([AGENT_ONLY_FILE, SHARED_FILE])

    def test_the_builder_is_seated_in_engineering(self, shared_tree_home: Path):
        """The placement the roadmap suspected was unreachable."""
        agents = {a.agent_id: a for a in transcripts.load(shared_tree_home)[1]}
        assert wing_for(agents[BUILDER]) == "ENG"

    def test_a_file_path_is_credited_to_the_declared_agent_id(self, shared_tree_home: Path):
        """The id in the record wins over the one in the filename.

        A declared agentId can arrive on any record, so credit is assigned after
        the whole transcript is read rather than as each call is seen.
        """
        subagents = (shared_tree_home / "projects" / "-home-user-Demo"
                     / SHARED_SESSION_ID / "subagents")
        (subagents / f"agent-{BUILDER}.jsonl").rename(subagents / "agent-stale-name.jsonl")

        files = {f.path: f for f in transcripts.load(shared_tree_home)[2]}
        assert files[AGENT_ONLY_FILE].agent_ids == [BUILDER]


class TestUnattributableDispatches:
    """The one case where per-agent attribution genuinely cannot be derived."""

    def test_a_dispatch_without_a_transcript_is_recorded(self, claude_home: Path):
        subagents = claude_home / "projects" / "-home-user-Demo" / SESSION_ID / "subagents"
        (subagents / f"agent-{AGENT_A}.jsonl").unlink()

        health = transcripts.load(claude_home)[3]
        assert health.agents_without_transcript == [AGENT_A]

    def test_the_gap_is_disclosed_in_words(self, claude_home: Path):
        subagents = claude_home / "projects" / "-home-user-Demo" / SESSION_ID / "subagents"
        (subagents / f"agent-{AGENT_A}.jsonl").unlink()

        note = transcripts.load(claude_home)[3].attribution_note
        assert note is not None
        assert "unavailable" in note
        assert "no transcript" in note

    def test_no_note_when_every_agent_has_a_transcript(self, claude_home: Path):
        """The disclosure is conditional on a real gap, not boilerplate."""
        health = transcripts.load(claude_home)[3]
        assert health.agents_without_transcript == []
        assert health.attribution_note is None
