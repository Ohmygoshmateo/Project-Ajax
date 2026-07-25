"""Rendering, empty states, division derivation, and the server's bind address."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest

from ajax_hq import divisions as divisions_mod
from ajax_hq import serve as serve_mod
from ajax_hq.collect import collect
from ajax_hq.model import Session, Status
from ajax_hq.render import render
from ajax_hq.timeutil import now
from tests.conftest import SECRET_PROMPT, SECRET_REPORT

EXTERNAL_REF = re.compile(r'(?:src|href)\s*=\s*["\']https?://', re.I)
FETCH_CALL = re.compile(r"""fetch\(['"](https?:)?//""", re.I)


@pytest.fixture
def page(claude_home: Path, empty_workspace: Path) -> str:
    return render(collect(claude_home=claude_home, workspace=empty_workspace))


@pytest.fixture
def empty_page(empty_home: Path, empty_workspace: Path) -> str:
    return render(collect(claude_home=empty_home, workspace=empty_workspace))


class TestSelfContained:
    """The page must render identically with the network unplugged."""

    def test_no_external_stylesheets_or_images(self, page: str):
        assert not EXTERNAL_REF.search(page)

    def test_no_remote_fetches(self, page: str):
        assert not FETCH_CALL.search(page)

    def test_styles_are_inline(self, page: str):
        assert "<style>" in page and "--gold" in page

    def test_static_page_carries_no_script(self, page: str):
        """Only the served variant polls; the file on disk is inert."""
        assert "<script>" not in page

    def test_live_variant_adds_the_poller(self, claude_home, empty_workspace):
        live = render(collect(claude_home=claude_home, workspace=empty_workspace), live=True)
        assert "<script>" in live
        assert "/api/generated" in live


class TestContent:
    def test_agents_appear_by_description(self, page: str):
        assert "Explore the codebase" in page
        assert "Verify the API" in page

    def test_every_division_is_rendered(self, page: str):
        for code in ("EXO", "RND", "ENG", "QA", "OPS", "AST"):
            assert f">{code}<" in page

    def test_korean_subtitles_present(self, page: str):
        assert "연구개발부" in page and "에이잭스 본사" in page

    def test_schema_drift_is_surfaced(self, page: str):
        assert "Schema drift" in page
        assert "brand-new-type" in page

    def test_derived_figures_are_labelled(self, page: str):
        assert "not a filesystem diff" in page

    def test_read_only_is_stated(self, page: str):
        assert "read-only" in page.lower()

    def test_provenance_footer_lists_sources(self, page: str):
        assert "Session transcripts" in page and "Plans" in page


class TestDrilldown:
    def test_prompt_included_by_default(self, page: str):
        assert SECRET_PROMPT in page

    def test_report_included_by_default(self, page: str):
        assert SECRET_REPORT in page

    def test_text_can_be_suppressed(self, claude_home, empty_workspace):
        page = render(collect(claude_home=claude_home, workspace=empty_workspace),
                      include_text=False)
        assert SECRET_PROMPT not in page
        assert SECRET_REPORT not in page
        # The roster itself still renders.
        assert "Explore the codebase" in page

    def test_html_in_transcripts_is_escaped(self, claude_home, empty_workspace):
        """Transcript text is untrusted input as far as the page is concerned."""
        import json

        project = claude_home / "projects" / "-home-user-Demo"
        session_file = next(project.glob("*.jsonl"))
        records = session_file.read_text().splitlines()
        injected = json.dumps({
            "type": "assistant", "timestamp": "2026-03-02T09:40:00.000Z",
            "message": {"model": "claude-opus-5", "content": [{
                "type": "tool_use", "name": "Agent", "id": "tX",
                "input": {"description": "<script>alert(1)</script>",
                          "subagent_type": "Explore", "prompt": "x"},
            }]},
        })
        session_file.write_text("\n".join([*records, injected]) + "\n")

        page = render(collect(claude_home=claude_home, workspace=empty_workspace))
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page


class TestEmptyStates:
    def test_empty_workspace_renders_without_raising(self, empty_page: str):
        assert "AJAX HQ" in empty_page

    def test_empty_state_explains_rather_than_showing_zeros(self, empty_page: str):
        assert "No agent activity found" in empty_page
        assert "No subagents have been dispatched" in empty_page

    def test_empty_state_names_the_source(self, empty_page: str):
        assert "~/.claude/projects" in empty_page

    def test_no_repositories_is_stated(self, empty_page: str):
        assert "No git repositories found" in empty_page

    def test_divisions_report_never_active(self, empty_home, empty_workspace):
        built = collect(claude_home=empty_home, workspace=empty_workspace)
        assert all(d.status is Status.NEVER_ACTIVE for d in built.divisions)


class TestDivisionStatus:
    def _session(self, ended_hours_ago: float | None) -> Session:
        stamp = None if ended_hours_ago is None else now() - timedelta(hours=ended_hours_ago)
        return Session(session_id="s", started=stamp, ended=stamp)

    def test_never_active_without_data(self):
        division = divisions_mod.executive_office([], [])
        assert division.status is Status.NEVER_ACTIVE

    def test_active_when_recent(self):
        division = divisions_mod.executive_office([self._session(1.0)], [])
        assert division.status is Status.ACTIVE

    def test_idle_when_stale(self):
        division = divisions_mod.executive_office([self._session(72.0)], [])
        assert division.status is Status.IDLE

    def test_boundary_is_the_configured_window(self):
        just_inside = divisions_mod.executive_office(
            [self._session(divisions_mod.ACTIVE_WINDOW_HOURS - 0.5)], []
        )
        just_outside = divisions_mod.executive_office(
            [self._session(divisions_mod.ACTIVE_WINDOW_HOURS + 0.5)], []
        )
        assert just_inside.status is Status.ACTIVE
        assert just_outside.status is Status.IDLE

    def test_asset_management_names_the_command_when_absent(self):
        division = divisions_mod.asset_management({}, None)
        assert division.status is Status.NEVER_ACTIVE
        assert any("ajax agent run-once" in note for note in division.notes)

    def test_qa_disclaims_outcomes(self, claude_home, empty_workspace):
        built = collect(claude_home=claude_home, workspace=empty_workspace)
        qa = next(d for d in built.divisions if d.code == "QA")
        assert any("not outcomes" in note for note in qa.notes)

    def test_qa_counts_real_verification_runs(self, claude_home, empty_workspace):
        built = collect(claude_home=claude_home, workspace=empty_workspace)
        qa = next(d for d in built.divisions if d.code == "QA")
        assert dict(qa.metrics)["Verification runs"] == "1"  # the fixture's `pytest -q`


class TestServerBinding:
    """The page shows full transcripts; it must never leave loopback."""

    def test_bind_address_is_loopback(self):
        assert serve_mod.LOOPBACK == "127.0.0.1"

    def test_server_binds_loopback_in_practice(self, claude_home, empty_workspace):
        server = serve_mod.build_server(0, claude_home=claude_home, workspace=empty_workspace)
        try:
            assert server.server_address[0] == "127.0.0.1"
        finally:
            server.server_close()

    def test_source_never_binds_all_interfaces(self):
        source = Path(serve_mod.__file__).read_text()
        for wildcard in ('"0.0.0.0"', "'0.0.0.0'", '"::"'):
            assert wildcard not in source

    def test_bind_address_is_not_caller_configurable(self):
        import inspect

        for fn in (serve_mod.build_server, serve_mod.serve):
            params = inspect.signature(fn).parameters
            assert "host" not in params and "bind" not in params
