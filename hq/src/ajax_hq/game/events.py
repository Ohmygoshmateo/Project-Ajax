"""Real events, read from transcripts as they are written.

This is the part that keeps the game from being a screensaver. Every purposeful
move an actor makes comes from a record that appeared on disk: a tool call, a
shell command, a dispatch, a final report. Nothing here invents an event, and
the simulation cannot manufacture one — it can only decide where an actor
wanders when *no* event is driving it.

Two sources, same event shape:

``TranscriptTail``
    Follows the files under ``~/.claude`` and yields records as they are
    appended. Offsets are remembered per file, so a poll returns only what is
    new. This is the live mode.

``replay``
    Reads what is already on disk and hands it back in timestamp order, to be
    played on a compressed clock. Useful because a machine that is not currently
    running an agent would otherwise show an empty office — a replay is honest
    about being history, and the HUD says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ajax_hq.behaviour import BUILD_TOOLS, RESEARCH_TOOLS, count_commands
from ajax_hq.sources.transcripts import parse_timestamp

# Which wing a tool call sends an actor to. Deliberately the same classification
# the floor uses to seat agents, so an agent's errand and its desk agree.
DISPATCH_TOOLS = {"Agent", "Task", "AskUserQuestion", "ExitPlanMode"}


# What an actor is visibly doing. Each value is produced by a specific kind of
# record, never inferred from how long it has been since the last one — an actor
# with no recent record is idle, and says so, rather than being given something
# plausible to look busy with.
TYPING = "typing"        # Write / Edit — it is changing a file
READING = "reading"      # Read / Grep / WebSearch — it is looking something up
TESTING = "testing"      # a verification command
SHIPPING = "shipping"    # a commit or push
TALKING = "talking"      # dispatching an agent, or asking a question
REPORTING = "reporting"  # emitting text — writing its answer
WORKING = "working"      # a tool that maps to none of the above
IDLE = "idle"

ACTIVITY_BY_TOOL = {
    "Write": TYPING, "Edit": TYPING, "MultiEdit": TYPING, "NotebookEdit": TYPING,
    "Read": READING, "Grep": READING, "Glob": READING, "NotebookRead": READING,
    "WebSearch": READING, "WebFetch": READING, "ToolSearch": READING,
    "Agent": TALKING, "Task": TALKING, "AskUserQuestion": TALKING,
    "ExitPlanMode": TALKING,
}

ACTIVITY_LABEL = {
    TYPING: "editing", READING: "researching", TESTING: "running checks",
    SHIPPING: "shipping", TALKING: "in conversation", REPORTING: "writing up",
    WORKING: "working", IDLE: "idle",
}


def activity_for(name: str, command: str | None = None) -> str:
    """What a tool call looks like on the floor."""
    if name == "Bash" and command:
        verify, ship = count_commands([command])
        if ship:
            return SHIPPING
        if verify:
            return TESTING
        return WORKING
    return ACTIVITY_BY_TOOL.get(name, WORKING)


@dataclass(frozen=True)
class Event:
    """One thing that actually happened, attributable to one actor."""

    actor_id: str
    kind: str  # "tool" | "shell" | "text" | "start"
    detail: str
    at: datetime | None = None
    wing: str | None = None  # where it should send the actor, if anywhere
    activity: str = WORKING

    @property
    def is_errand(self) -> bool:
        return self.wing is not None


def wing_for_tool(name: str, command: str | None = None) -> str | None:
    """The wing a single tool call implies, or None if it implies nothing.

    Bash is the interesting case: the tool name says nothing, so the command
    decides. A command that is neither verification nor shipping — ``ls``,
    ``cat`` — returns None, and the actor stays where it is rather than being
    sent somewhere on no evidence.
    """
    if name in BUILD_TOOLS:
        return "ENG"
    if name in RESEARCH_TOOLS:
        return "RND"
    if name in DISPATCH_TOOLS:
        return "EXO"
    if name == "Bash" and command:
        verify, ship = count_commands([command])
        if ship:
            return "OPS"
        if verify:
            return "QA"
    return None


def _flat(text: str, width: int = 60) -> str:
    """One line. A detail spanning lines would break both renderers' layout."""
    return " ".join(text.split())[:width]


def _content_blocks(record: dict) -> list[dict]:
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def events_from_record(record: dict, actor_id: str) -> list[Event]:
    """Turn one transcript record into zero or more events."""
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return []

    at = parse_timestamp(record.get("timestamp"))
    events: list[Event] = []

    for block in _content_blocks(record):
        kind = block.get("type")
        if kind == "tool_use":
            name = block.get("name")
            if not isinstance(name, str):
                continue
            payload = block.get("input")
            command = payload.get("command") if isinstance(payload, dict) else None
            command = command if isinstance(command, str) else None
            events.append(
                Event(
                    actor_id=actor_id,
                    kind="shell" if name == "Bash" else "tool",
                    detail=_flat(command) if command else name,
                    at=at,
                    wing=wing_for_tool(name, command),
                    activity=activity_for(name, command),
                )
            )
        elif kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                events.append(
                    Event(actor_id=actor_id, kind="text", detail=_flat(text), at=at,
                          activity=REPORTING)
                )

    return events


def _actor_id(record: dict, fallback: str) -> str:
    for key in ("agentId", "sessionId"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def _transcript_files(claude_home: Path) -> list[Path]:
    root = claude_home / "projects"
    if not root.is_dir():
        return []
    files: list[Path] = []
    for project in root.iterdir():
        if not project.is_dir():
            continue
        files.extend(sorted(project.glob("*.jsonl")))
        for session_dir in project.iterdir():
            if session_dir.is_dir():
                files.extend(sorted((session_dir / "subagents").glob("agent-*.jsonl")))
    return files


class TranscriptTail:
    """Follows transcripts, yielding only records appended since the last poll.

    Deliberately forgiving: a half-written final line is normal for a file being
    appended to right now, so an unparseable trailing line is left unconsumed and
    retried on the next poll rather than dropped.
    """

    def __init__(self, claude_home: Path, *, from_start: bool = False) -> None:
        self.claude_home = claude_home
        self.offsets: dict[Path, int] = {}
        self.skipped = 0
        if not from_start:
            # Start at the current end of every file: the point is to watch what
            # happens next, not to replay the whole day on the first frame.
            for path in _transcript_files(claude_home):
                try:
                    self.offsets[path] = path.stat().st_size
                except OSError:
                    self.offsets[path] = 0

    def poll(self) -> list[Event]:
        events: list[Event] = []
        for path in _transcript_files(self.claude_home):
            events.extend(self._poll_file(path))
        return events

    def _poll_file(self, path: Path) -> list[Event]:
        start = self.offsets.get(path, 0)
        try:
            size = path.stat().st_size
        except OSError:
            return []
        if size <= start:
            if size < start:  # truncated or replaced
                self.offsets[path] = 0
            return []

        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(start)
                chunk = handle.read()
        except OSError:
            return []

        # Only whole lines are consumed; a trailing partial line stays unread.
        consumed = chunk.rfind("\n") + 1
        if consumed <= 0:
            return []
        self.offsets[path] = start + len(chunk[:consumed].encode("utf-8"))

        fallback = path.stem.removeprefix("agent-")
        events: list[Event] = []
        for line in chunk[:consumed].splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                self.skipped += 1
                continue
            if isinstance(record, dict):
                events.extend(events_from_record(record, _actor_id(record, fallback)))
        return events


def replay(claude_home: Path, limit: int = 4000) -> list[Event]:
    """Every event already on disk, oldest first.

    History, not live activity. Callers must label it as such — an office
    replaying yesterday while claiming to be live would be exactly the kind of
    invented presence this project refuses to ship.
    """
    events: list[Event] = []
    for path in _transcript_files(claude_home):
        fallback = path.stem.removeprefix("agent-")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(record, dict):
                events.extend(events_from_record(record, _actor_id(record, fallback)))

    events.sort(key=lambda e: (e.at is None, e.at or datetime.min))
    return events[-limit:]
