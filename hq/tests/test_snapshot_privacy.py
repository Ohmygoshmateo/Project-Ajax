"""Snapshot serialization, merge, and the privacy guarantee.

The privacy tests are the important ones here. Snapshots are committed to git,
where removing something later is far harder than never writing it, so "no
prompt text ever reaches a snapshot" is enforced by test rather than by care.
"""

from __future__ import annotations

import json
from pathlib import Path

from ajax_hq import snapshot as snapshot_mod
from ajax_hq.collect import collect
from ajax_hq.model import Provenance
from tests.conftest import AGENT_A, SECRET_PROMPT, SECRET_REPORT, SESSION_ID


def _built(claude_home: Path, workspace: Path):
    return collect(claude_home=claude_home, workspace=workspace)


class TestPrivacy:
    def test_prompt_text_never_reaches_the_payload(self, claude_home, empty_workspace):
        built = _built(claude_home, empty_workspace)
        # Confirm the fixture really did carry the secret into memory first,
        # otherwise this test could pass for the wrong reason.
        assert any(a.prompt == SECRET_PROMPT for a in built.agents)

        serialized = json.dumps(snapshot_mod.to_payload(built))
        assert SECRET_PROMPT not in serialized

    def test_report_text_never_reaches_the_payload(self, claude_home, empty_workspace):
        built = _built(claude_home, empty_workspace)
        assert any(a.report == SECRET_REPORT for a in built.agents)

        serialized = json.dumps(snapshot_mod.to_payload(built))
        assert SECRET_REPORT not in serialized

    def test_written_file_contains_no_prompt_text(self, claude_home, empty_workspace, tmp_path):
        built = _built(claude_home, empty_workspace)
        path = snapshot_mod.write(built, tmp_path / "history")
        content = path.read_text()
        assert SECRET_PROMPT not in content
        assert SECRET_REPORT not in content

    def test_forbidden_keys_absent_from_every_record(self, claude_home, empty_workspace):
        payload = snapshot_mod.to_payload(_built(claude_home, empty_workspace))
        for collection in ("agents", "sessions"):
            for row in payload[collection]:
                for forbidden in snapshot_mod.FORBIDDEN_FIELDS:
                    assert forbidden not in row, f"{forbidden!r} leaked into {collection}"

    def test_shell_commands_are_not_serialized(self, claude_home, empty_workspace):
        """Commands can contain credentials passed as arguments."""
        built = _built(claude_home, empty_workspace)
        assert any(s.commands_run for s in built.sessions)

        serialized = json.dumps(snapshot_mod.to_payload(built))
        assert "pytest -q" not in serialized


class TestPayload:
    def test_schema_version_recorded(self, claude_home, empty_workspace):
        payload = snapshot_mod.to_payload(_built(claude_home, empty_workspace))
        assert payload["schema"] == 1

    def test_metadata_survives(self, claude_home, empty_workspace):
        payload = snapshot_mod.to_payload(_built(claude_home, empty_workspace))
        agent = next(a for a in payload["agents"] if a["id"] == AGENT_A)
        assert agent["description"] == "Explore the codebase"
        assert agent["type"] == "Explore"
        assert agent["tool_counts"]["Bash"] == 1
        assert agent["duration_s"] > 0

    def test_filename_encodes_date_and_session(self, claude_home, empty_workspace, tmp_path):
        path = snapshot_mod.write(_built(claude_home, empty_workspace), tmp_path / "h")
        assert path.suffix == ".json"
        assert SESSION_ID[:8] in path.name

    def test_json_is_stable_across_writes(self, claude_home, empty_workspace, tmp_path):
        built = _built(claude_home, empty_workspace)
        first = json.dumps(snapshot_mod.to_payload(built), sort_keys=True)
        second = json.dumps(snapshot_mod.to_payload(built), sort_keys=True)
        assert first == second


class TestMerge:
    def test_history_restores_absent_sessions(self, claude_home, empty_workspace, empty_home,
                                              tmp_path):
        history = tmp_path / "history"
        snapshot_mod.write(_built(claude_home, empty_workspace), history)

        # A fresh container: no transcripts at all.
        fresh = collect(claude_home=empty_home, workspace=empty_workspace)
        assert fresh.is_empty

        restored = snapshot_mod.merge_history(fresh, history)
        assert restored == 1
        assert not fresh.is_empty
        assert {a.agent_id for a in fresh.agents} >= {AGENT_A}

    def test_restored_records_are_marked_archival(self, claude_home, empty_workspace,
                                                  empty_home, tmp_path):
        history = tmp_path / "history"
        snapshot_mod.write(_built(claude_home, empty_workspace), history)

        fresh = collect(claude_home=empty_home, workspace=empty_workspace)
        snapshot_mod.merge_history(fresh, history)
        assert all(s.provenance is Provenance.RESTORED for s in fresh.sessions)
        assert all(a.provenance is Provenance.RESTORED for a in fresh.agents)

    def test_live_records_win_over_history(self, claude_home, empty_workspace, tmp_path):
        """On-disk data is more accurate than an archived summary of it."""
        history = tmp_path / "history"
        snapshot_mod.write(_built(claude_home, empty_workspace), history)

        live = _built(claude_home, empty_workspace)
        restored = snapshot_mod.merge_history(live, history)

        assert restored == 0
        assert len(live.sessions) == 1
        assert all(s.provenance is Provenance.LIVE for s in live.sessions)
        # Live agents keep their drill-down text; restored ones never have it.
        assert any(a.prompt for a in live.agents)

    def test_merging_two_snapshots_accumulates(self, claude_home, empty_workspace,
                                               empty_home, tmp_path):
        history = tmp_path / "history"
        built = _built(claude_home, empty_workspace)
        snapshot_mod.write(built, history)

        # A second snapshot describing a different session.
        built.sessions[0].session_id = "99999999-0000-0000-0000-000000000000"
        built.agents[0].agent_id = "ffff9999"
        snapshot_mod.write(built, history)

        fresh = collect(claude_home=empty_home, workspace=empty_workspace)
        restored = snapshot_mod.merge_history(fresh, history)
        assert restored == 2
        assert len(fresh.agents) >= 3

    def test_missing_history_directory_is_harmless(self, claude_home, empty_workspace, tmp_path):
        built = _built(claude_home, empty_workspace)
        assert snapshot_mod.merge_history(built, tmp_path / "nope") == 0

    def test_corrupt_history_file_is_skipped(self, claude_home, empty_workspace, empty_home,
                                             tmp_path):
        history = tmp_path / "history"
        history.mkdir()
        (history / "broken.json").write_text("{not json")
        snapshot_mod.write(_built(claude_home, empty_workspace), history)

        fresh = collect(claude_home=empty_home, workspace=empty_workspace)
        assert snapshot_mod.merge_history(fresh, history) == 1

    def test_wrong_schema_version_is_ignored(self, empty_home, empty_workspace, tmp_path):
        history = tmp_path / "history"
        history.mkdir()
        (history / "old.json").write_text(json.dumps({"schema": 999, "sessions": [{"id": "x"}]}))

        fresh = collect(claude_home=empty_home, workspace=empty_workspace)
        assert snapshot_mod.merge_history(fresh, history) == 0


class TestMeasuredOutputSurvivesArchiving:
    """Without this, agents restored on another machine show a false zero."""

    def test_output_chars_is_persisted(self, claude_home, empty_workspace):
        payload = snapshot_mod.to_payload(_built(claude_home, empty_workspace))
        assert all("output_chars" in row for row in payload["agents"])

    def test_output_chars_survives_a_round_trip(self, claude_home, empty_workspace,
                                                empty_home, tmp_path):
        history = tmp_path / "history"
        original = _built(claude_home, empty_workspace)
        expected = {a.agent_id: a.output_chars for a in original.agents}
        assert any(v > 0 for v in expected.values())

        snapshot_mod.write(original, history)
        fresh = collect(claude_home=empty_home, workspace=empty_workspace)
        snapshot_mod.merge_history(fresh, history)

        for agent in fresh.agents:
            assert agent.output_chars == expected[agent.agent_id]

    def test_a_size_is_not_content(self, claude_home, empty_workspace):
        """The count travels; the text it was counted from does not."""
        import json

        serialized = json.dumps(snapshot_mod.to_payload(_built(claude_home, empty_workspace)))
        assert SECRET_REPORT not in serialized
