"""Fixtures building a synthetic ~/.claude tree.

Everything is network-free and self-contained: the tests construct transcripts in
the shape the real client writes, so parsing is exercised without depending on
this machine's actual history.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SESSION_ID = "11111111-2222-3333-4444-555555555555"
AGENT_A = "aaaa1111bbbb2222"
AGENT_B = "cccc3333dddd4444"

SECRET_PROMPT = "SENSITIVE-PROMPT-TEXT-should-never-be-committed"
SECRET_REPORT = "SENSITIVE-REPORT-TEXT-should-never-be-committed"


def _assistant(ts: str, blocks: list[dict], *, model: str = "claude-opus-5",
               usage: dict | None = None) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": SESSION_ID,
        "gitBranch": "feature/demo",
        "cwd": "/home/user/Demo",
        "version": "2.1.220",
        "message": {
            "model": model,
            "content": blocks,
            "usage": usage or {"input_tokens": 100, "output_tokens": 50},
        },
    }


def _tool_use(name: str, payload: dict, tool_id: str) -> dict:
    return {"type": "tool_use", "name": name, "input": payload, "id": tool_id}


def _tool_result(ts: str, tool_use_id: str, text: str) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "sessionId": SESSION_ID,
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_use_id,
             "content": [{"type": "text", "text": text}]}
        ]},
    }


def _user_turn(ts: str, text: str) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "sessionId": SESSION_ID,
        "message": {"content": [{"type": "text", "text": text}]},
    }


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    """A synthetic ~/.claude with one session and two subagents."""
    home = tmp_path / "dot-claude"
    project = home / "projects" / "-home-user-Demo"
    subagents = project / SESSION_ID / "subagents"
    subagents.mkdir(parents=True)
    (home / "sessions").mkdir(parents=True)
    (home / "plans").mkdir(parents=True)

    session_records = [
        _user_turn("2026-03-02T09:00:00.000Z", "please build the thing"),
        _assistant("2026-03-02T09:00:10.000Z", [
            _tool_use("Write", {"file_path": "/home/user/Demo/a.py", "content": "x"}, "t1"),
            _tool_use("Bash", {"command": "pytest -q"}, "t2"),
        ]),
        _assistant("2026-03-02T09:01:00.000Z", [
            _tool_use("Agent", {
                "description": "Explore the codebase",
                "subagent_type": "Explore",
                "prompt": SECRET_PROMPT,
            }, "t3"),
        ]),
        _tool_result("2026-03-02T09:01:05.000Z", "t3",
                     f"Async agent launched successfully.\nagentId: {AGENT_A} (internal)"),
        _assistant("2026-03-02T09:02:00.000Z", [
            _tool_use("Agent", {
                "description": "Verify the API",
                "subagent_type": "general-purpose",
                "prompt": "check the docs",
            }, "t4"),
        ]),
        _tool_result("2026-03-02T09:02:05.000Z", "t4",
                     f"Async agent launched successfully.\nagentId: {AGENT_B} (internal)"),
        _assistant("2026-03-02T09:30:00.000Z", [
            _tool_use("Edit", {"file_path": "/home/user/Demo/a.py"}, "t5"),
            _tool_use("AskUserQuestion", {"questions": []}, "t6"),
            _tool_use("ExitPlanMode", {}, "t7"),
        ]),
        {"type": "mode", "timestamp": "2026-03-02T09:31:00.000Z", "sessionId": SESSION_ID},
    ]

    with (project / f"{SESSION_ID}.jsonl").open("w") as fh:
        for record in session_records:
            fh.write(json.dumps(record) + "\n")
        # Malformed and truncated lines are normal for a live session.
        fh.write("{not valid json\n")
        fh.write('{"type": "assistant", "timestamp": "2026-03-02T09:32:00.000Z"\n')
        fh.write(json.dumps({"type": "brand-new-type", "timestamp":
                             "2026-03-02T09:33:00.000Z"}) + "\n")

    agent_a = [
        {"type": "assistant", "agentId": AGENT_A, "sessionId": SESSION_ID,
         "timestamp": "2026-03-02T09:01:10.000Z",
         "message": {"model": "claude-opus-5",
                     "content": [_tool_use("Bash", {"command": "ls"}, "a1")],
                     "usage": {"input_tokens": 40, "output_tokens": 20,
                               "cache_read_input_tokens": 9000}}},
        {"type": "assistant", "agentId": AGENT_A, "sessionId": SESSION_ID,
         "timestamp": "2026-03-02T09:01:40.000Z",
         "message": {"model": "claude-opus-5",
                     "content": [{"type": "text", "text": SECRET_REPORT}],
                     "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    with (subagents / f"agent-{AGENT_A}.jsonl").open("w") as fh:
        for record in agent_a:
            fh.write(json.dumps(record) + "\n")

    agent_b = [
        {"type": "assistant", "agentId": AGENT_B, "sessionId": SESSION_ID,
         "timestamp": "2026-03-02T09:02:10.000Z",
         "message": {"model": "claude-sonnet-5",
                     "content": [
                         _tool_use("WebSearch", {"query": "docs"}, "b1"),
                         _tool_use("Write", {"file_path": "/home/user/Demo/b.py"}, "b2"),
                     ],
                     "usage": {"input_tokens": 60, "output_tokens": 30}}},
    ]
    with (subagents / f"agent-{AGENT_B}.jsonl").open("w") as fh:
        for record in agent_b:
            fh.write(json.dumps(record) + "\n")

    (home / "sessions" / "900.json").write_text(json.dumps({
        "pid": 900, "sessionId": SESSION_ID, "cwd": "/home/user/Demo",
        "startedAt": 1772442000000, "version": "2.1.220",
        "name": "demo-session-1", "entrypoint": "cli",
    }))

    (home / "plans" / "demo-plan.md").write_text("# Demo plan\n\nBuild the thing.\n")
    return home


@pytest.fixture
def empty_home(tmp_path: Path) -> Path:
    """A ~/.claude with the directories present but nothing in them."""
    home = tmp_path / "empty-claude"
    (home / "projects").mkdir(parents=True)
    (home / "sessions").mkdir(parents=True)
    return home


@pytest.fixture
def empty_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
