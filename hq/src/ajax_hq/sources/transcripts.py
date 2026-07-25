"""Parse Claude Code session and subagent transcripts.

These are **undocumented internals**. The layout observed at the time of writing
(client 2.1.220) is:

    ~/.claude/projects/<slug>/<session-id>.jsonl
    ~/.claude/projects/<slug>/<session-id>/subagents/agent-<agent-id>.jsonl

Every record is one JSON object with ``type``, ``timestamp``, and usually a
``message``. Assistant messages carry content blocks, of which ``tool_use`` is
the interesting one.

The shapes can change between client versions without notice, so parsing here is
deliberately paranoid: every line is guarded, every field access is optional, and
anything unreadable is *counted and skipped* rather than raised. A format change
should cost the dashboard some panels, never the whole page. The counts feed the
schema-health banner so drift is visible instead of silent — the same discipline
used for the yfinance news schema in the trading project.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ajax_hq.model import Agent, BuiltFile, SchemaHealth, Session, Status, ToolUsage

DEFAULT_CLAUDE_HOME = Path.home() / ".claude"

# Record types seen in practice. Anything else is counted so drift is visible,
# but is not an error — new types are expected over time.
KNOWN_RECORD_TYPES = {
    "user",
    "assistant",
    "attachment",
    "system",
    "mode",
    "queue-operation",
    "last-prompt",
    "summary",
    "progress",
}

# Tool inputs that name a file, in priority order.
_FILE_KEYS = ("file_path", "notebook_path", "path")

# Tools that represent producing or changing work product.
WRITE_TOOLS = {"Write"}
EDIT_TOOLS = {"Edit", "NotebookEdit", "MultiEdit"}

# Tool calls that represent a decision point with the user.
DECISION_TOOLS = {"AskUserQuestion", "ExitPlanMode", "EnterPlanMode"}

_AGENT_ID_RE = re.compile(r"agentId:\s*([A-Za-z0-9_-]+)")


def parse_timestamp(value: object) -> datetime | None:
    """Lenient ISO-8601 parsing. Returns None rather than raising."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def iter_records(path: Path, health: SchemaHealth) -> Iterator[dict]:
    """Yield parsed records, counting anything unreadable.

    A truncated final line is common when a session is still running, so a bad
    line is normal operation rather than an error condition.
    """
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError as exc:
        health.warnings.append(f"could not open {path.name}: {exc}")
        return

    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            health.records_read += 1
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                health.records_unparsed += 1
                continue
            if not isinstance(record, dict):
                health.records_unparsed += 1
                continue

            record_type = record.get("type")
            if record_type not in KNOWN_RECORD_TYPES and record_type is not None:
                if record_type not in health.unknown_record_types:
                    health.unknown_record_types.append(str(record_type))
            yield record


def _content_blocks(record: dict) -> list[dict]:
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return [c for c in content if isinstance(c, dict)]
    return []


def _tool_uses(record: dict) -> Iterator[tuple[str, dict, str | None]]:
    """Yield ``(tool_name, input_dict, tool_use_id)`` for a record."""
    for block in _content_blocks(record):
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if not isinstance(name, str):
            continue
        payload = block.get("input")
        yield name, payload if isinstance(payload, dict) else {}, block.get("id")


def _file_from_input(payload: dict) -> str | None:
    for key in _FILE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _usage(record: dict) -> tuple[int, int, int]:
    """Return ``(fresh_input, output, cache_read)``.

    Cache reads are kept separate deliberately. They dwarf fresh input by orders
    of magnitude on a long session, and folding them into one "tokens" figure
    would read as consumption rather than context reuse.
    """
    message = record.get("message")
    if not isinstance(message, dict):
        return 0, 0, 0
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0

    def _num(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and value >= 0 else 0

    return (
        _num("input_tokens"),
        _num("output_tokens"),
        _num("cache_read_input_tokens") + _num("cache_creation_input_tokens"),
    )


def _model(record: dict) -> str | None:
    message = record.get("message")
    if isinstance(message, dict):
        model = message.get("model")
        if isinstance(model, str) and model and not model.startswith("<"):
            return model
    return None


def _tool_result_text(record: dict) -> Iterator[tuple[str | None, str]]:
    """Yield ``(tool_use_id, text)`` for tool results in a user record."""
    for block in _content_blocks(record):
        if block.get("type") != "tool_result":
            continue
        content = block.get("content")
        if isinstance(content, list):
            text = " ".join(
                str(c.get("text", "")) for c in content if isinstance(c, dict)
            )
        else:
            text = str(content or "")
        yield block.get("tool_use_id"), text


class SessionParse:
    """Result of reading one session transcript."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.files: dict[str, BuiltFile] = {}
        # agent_id -> {description, agent_type, prompt}
        self.dispatched: dict[str, dict] = {}


def parse_session_file(path: Path, health: SchemaHealth) -> SessionParse:
    """Read one session transcript into a Session plus its dispatch records."""
    session_id = path.stem
    session = Session(session_id=session_id)
    parse = SessionParse(session)

    # tool_use_id -> Agent tool call details, resolved to an agentId once the
    # matching tool_result arrives.
    pending: dict[str, dict] = {}
    models: list[str] = []
    before = health.records_unparsed

    for record in iter_records(path, health):
        session.record_count += 1

        stamp = parse_timestamp(record.get("timestamp"))
        if stamp:
            session.started = min(session.started or stamp, stamp)
            session.ended = max(session.ended or stamp, stamp)

        for key, attr in (("cwd", "cwd"), ("gitBranch", "branch"), ("version", "client_version"),
                          ("entrypoint", "entrypoint")):
            value = record.get(key)
            if isinstance(value, str) and value and getattr(session, attr) is None:
                setattr(session, attr, value)

        record_type = record.get("type")

        if record_type == "user":
            # Only genuine user turns, not tool results fed back as user records.
            blocks = _content_blocks(record)
            if not any(b.get("type") == "tool_result" for b in blocks):
                session.user_turns += 1
            for tool_use_id, text in _tool_result_text(record):
                match = _AGENT_ID_RE.search(text)
                if match and tool_use_id in pending:
                    parse.dispatched[match.group(1)] = pending.pop(tool_use_id)

        elif record_type == "assistant":
            model = _model(record)
            if model and model not in models:
                models.append(model)
            inputs, outputs, cached = _usage(record)
            session.input_tokens += inputs
            session.output_tokens += outputs
            session.cache_tokens += cached

            for name, payload, tool_use_id in _tool_uses(record):
                session.tools.add(name)

                if name in DECISION_TOOLS:
                    session.decisions += 1

                if name in WRITE_TOOLS or name in EDIT_TOOLS:
                    file_path = _file_from_input(payload)
                    if file_path:
                        entry = parse.files.setdefault(file_path, BuiltFile(path=file_path))
                        if name in WRITE_TOOLS:
                            entry.writes += 1
                        else:
                            entry.edits += 1
                        if stamp:
                            entry.first_seen = min(entry.first_seen or stamp, stamp)
                            entry.last_seen = max(entry.last_seen or stamp, stamp)
                        if file_path not in session.files_touched:
                            session.files_touched.append(file_path)

                elif name == "Bash":
                    command = payload.get("command")
                    if isinstance(command, str) and command.strip():
                        session.commands_run.append(command.strip()[:400])

                if name == "Agent" and tool_use_id:
                    pending[tool_use_id] = {
                        "description": _str_or_none(payload.get("description")),
                        "agent_type": _str_or_none(payload.get("subagent_type")) or "claude",
                        "prompt": _str_or_none(payload.get("prompt")),
                        "dispatched_at": stamp,
                    }

    # Dispatches whose tool_result never resolved (still running, or the result
    # format changed). Keep them under a synthetic key so the roster can still
    # show that the work was requested.
    for index, leftover in enumerate(pending.values()):
        parse.dispatched[f"unresolved-{session_id[:8]}-{index}"] = leftover

    session.models = models
    session.agent_ids = list(parse.dispatched)
    session.unparsed_records = health.records_unparsed - before
    return parse


def parse_subagent_file(path: Path, health: SchemaHealth) -> Agent:
    """Read one subagent transcript into an Agent."""
    agent_id = path.stem.removeprefix("agent-")
    agent = Agent(agent_id=agent_id)
    models: list[str] = []
    last_text: str | None = None

    for record in iter_records(path, health):
        agent.record_count += 1

        if agent.session_id is None:
            value = record.get("sessionId")
            if isinstance(value, str):
                agent.session_id = value
        declared = record.get("agentId")
        if isinstance(declared, str) and declared:
            agent.agent_id = declared

        stamp = parse_timestamp(record.get("timestamp"))
        if stamp:
            agent.started = min(agent.started or stamp, stamp)
            agent.ended = max(agent.ended or stamp, stamp)

        if record.get("type") != "assistant":
            continue

        model = _model(record)
        if model and model not in models:
            models.append(model)
        inputs, outputs, cached = _usage(record)
        agent.input_tokens += inputs
        agent.output_tokens += outputs
        agent.cache_tokens += cached

        for block in _content_blocks(record):
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    last_text = text
                    # Measured output. The reported output_tokens is a
                    # placeholder in subagent transcripts (see
                    # Agent.output_tokens_are_plausible), so effort is counted
                    # from the content itself rather than trusted from usage.
                    agent.output_chars += len(text)

        for name, payload, _ in _tool_uses(record):
            agent.tools.add(name)
            if name in WRITE_TOOLS or name in EDIT_TOOLS:
                file_path = _file_from_input(payload)
                if file_path and file_path not in agent.files_touched:
                    agent.files_touched.append(file_path)
            elif name == "Bash":
                command = payload.get("command")
                if isinstance(command, str) and command.strip():
                    agent.commands_run.append(command.strip()[:200])

    agent.models = models
    # The last assistant text block is the agent's final report to its caller.
    agent.report = last_text
    agent.status = Status.COMPLETED if agent.record_count else Status.UNKNOWN
    return agent


def _str_or_none(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def project_dirs(claude_home: Path) -> list[Path]:
    root = claude_home / "projects"
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def session_files(project_dir: Path) -> list[Path]:
    return sorted(project_dir.glob("*.jsonl"))


def subagent_files(project_dir: Path, session_id: str) -> list[Path]:
    return sorted((project_dir / session_id / "subagents").glob("agent-*.jsonl"))


def load(claude_home: Path | None = None) -> tuple[list[Session], list[Agent], list[BuiltFile], SchemaHealth]:
    """Read every transcript under ``claude_home``.

    Subagent transcripts are the authoritative list of agents that ran; the
    dispatching session supplies the task description and type. Either source can
    be missing, and an agent appears in the roster if *either* has it — a
    dispatch with no transcript still happened, and a transcript with no dispatch
    record still represents real work.
    """
    home = claude_home or DEFAULT_CLAUDE_HOME
    health = SchemaHealth()
    sessions: list[Session] = []
    agents: dict[str, Agent] = {}
    files: dict[str, BuiltFile] = {}
    dispatched: dict[str, dict] = {}

    for project_dir in project_dirs(home):
        for session_path in session_files(project_dir):
            health.files_read += 1
            parse = parse_session_file(session_path, health)
            sessions.append(parse.session)
            dispatched.update(parse.dispatched)

            if parse.session.client_version:
                if parse.session.client_version not in health.client_versions:
                    health.client_versions.append(parse.session.client_version)

            for path_key, entry in parse.files.items():
                existing = files.get(path_key)
                if existing is None:
                    files[path_key] = entry
                    continue
                existing.writes += entry.writes
                existing.edits += entry.edits
                if entry.first_seen:
                    existing.first_seen = min(existing.first_seen or entry.first_seen,
                                              entry.first_seen)
                if entry.last_seen:
                    existing.last_seen = max(existing.last_seen or entry.last_seen, entry.last_seen)

            for agent_path in subagent_files(project_dir, parse.session.session_id):
                health.files_read += 1
                agent = parse_subagent_file(agent_path, health)
                agent.session_id = agent.session_id or parse.session.session_id
                agents[agent.agent_id] = agent

    # Attach dispatch metadata to the transcripts it belongs to.
    for agent_id, meta in dispatched.items():
        agent = agents.get(agent_id)
        if agent is None:
            # Dispatched but no transcript on disk — still real work, shown as
            # running/unknown rather than dropped.
            agent = Agent(agent_id=agent_id, status=Status.RUNNING)
            agents[agent_id] = agent
        agent.description = agent.description or meta.get("description")
        agent.agent_type = agent.agent_type or meta.get("agent_type")
        agent.prompt = agent.prompt or meta.get("prompt")
        if agent.started is None:
            agent.started = meta.get("dispatched_at")

    # Record which agents report an output-token count that contradicts what
    # they actually emitted, so the schema-health line can state it once.
    for agent in agents.values():
        if not agent.output_tokens_are_plausible:
            health.implausible_output_tokens.append(agent.agent_id)

    ordered_agents = sorted(
        agents.values(), key=lambda a: (a.started or datetime.max.replace(tzinfo=None), a.agent_id)
    )
    return sessions, ordered_agents, list(files.values()), health


__all__ = [
    "DEFAULT_CLAUDE_HOME",
    "SchemaHealth",
    "SessionParse",
    "ToolUsage",
    "load",
    "parse_session_file",
    "parse_subagent_file",
    "parse_timestamp",
]
