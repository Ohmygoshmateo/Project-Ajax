"""Committed history — so the record survives the container.

This environment is ephemeral: when it is reclaimed, ``~/.claude`` goes with it
and every transcript is lost. Snapshots are compact JSON summaries written into
the repository so the history of what agents built accumulates across containers.

**No prompt or response text is ever written here.** Drill-down text stays in the
locally-generated page. Prompts can contain anything — credentials pasted into a
question, private context, half-formed ideas — and these files go into git, where
removing something later is much harder than never writing it. Metadata is enough
to preserve the history that matters, and :func:`to_payload` is the only writer,
so that guarantee lives in one place and is asserted in the tests.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ajax_hq.model import (
    SCHEMA_VERSION,
    Agent,
    BuiltFile,
    Commit,
    Project,
    Provenance,
    Session,
    Snapshot,
    Status,
    ToolUsage,
)
from ajax_hq.timeutil import aware

DEFAULT_HISTORY_DIR = Path(__file__).resolve().parents[2] / "data" / "history"

# Fields that must never be serialized. Enforced by construction below and
# verified by tests/test_snapshot.py.
FORBIDDEN_FIELDS = ("prompt", "report", "commands_run", "notes")


def _iso(value: datetime | None) -> str | None:
    stamp = aware(value)
    return stamp.isoformat(timespec="seconds") if stamp else None


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def to_payload(snapshot: Snapshot) -> dict[str, Any]:
    """Serialize to the committed shape — metadata only, no free text."""
    return {
        "schema": SCHEMA_VERSION,
        "captured_at": _iso(snapshot.generated_at),
        "client_versions": list(snapshot.schema.client_versions),
        "sessions": [
            {
                "id": s.session_id,
                "name": s.name,
                "cwd": s.cwd,
                "branch": s.branch,
                "started": _iso(s.started),
                "ended": _iso(s.ended),
                "models": list(s.models),
                "tool_counts": dict(s.tools.counts),
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "cache_tokens": s.cache_tokens,
                "user_turns": s.user_turns,
                "decisions": s.decisions,
                # Ids, not content. Without these a restored session has no
                # record of what it dispatched, so `ajax-hq lineage` could never
                # cross-check attribution for anything read back from history.
                "agent_ids": list(s.agent_ids),
                "files_touched": len(s.files_touched),
            }
            for s in snapshot.sessions
        ],
        "agents": [
            {
                "id": a.agent_id,
                # A short task label, not the prompt that produced it.
                "description": a.description,
                "type": a.agent_type,
                "session_id": a.session_id,
                "started": _iso(a.started),
                "ended": _iso(a.ended),
                "duration_s": a.duration_seconds,
                "status": a.status.value,
                "tool_counts": dict(a.tools.counts),
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
                "cache_tokens": a.cache_tokens,
                # A size, not content — restoring this keeps archived agents
                # from displaying a false zero for effort.
                "output_chars": a.output_chars,
                # Counts, not commands. The commands are forbidden here; these
                # two integers are what lets a restored agent still be seated in
                # the division whose work it actually did.
                "verify_runs": a.verify_runs,
                "ship_actions": a.ship_actions,
                "files_touched": list(a.files_touched),
            }
            for a in snapshot.agents
        ],
        "files": [
            {
                "path": f.path,
                "project": f.project,
                "writes": f.writes,
                "edits": f.edits,
                "first_seen": _iso(f.first_seen),
                "last_seen": _iso(f.last_seen),
            }
            for f in snapshot.files
        ],
        "commits": [
            {
                "sha": c.sha,
                "subject": c.subject,
                "author": c.author,
                "timestamp": _iso(c.timestamp),
                "files_changed": c.files_changed,
                "insertions": c.insertions,
                "deletions": c.deletions,
                "branch": c.branch,
            }
            for c in snapshot.commits
        ],
        "projects": [
            {
                "name": p.name,
                "path": p.path,
                "language": p.language,
                "loc": p.loc,
                "source_files": p.source_files,
                "test_files": p.test_files,
                "test_count": p.test_count,
                "branch": p.branch,
                "last_commit": _iso(p.last_commit),
            }
            for p in snapshot.projects
        ],
    }


def write(snapshot: Snapshot, directory: Path | None = None) -> Path:
    """Write one snapshot, named by capture date and the newest session."""
    target = directory or DEFAULT_HISTORY_DIR
    target.mkdir(parents=True, exist_ok=True)

    stamp = aware(snapshot.generated_at)
    date_part = stamp.strftime("%Y%m%d") if stamp else "unknown"
    session_part = snapshot.sessions[0].session_id[:8] if snapshot.sessions else "nosession"

    path = target / f"{date_part}-{session_part}.json"
    path.write_text(json.dumps(to_payload(snapshot), indent=2, sort_keys=True) + "\n")
    return path


def _load_payloads(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    payloads = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == SCHEMA_VERSION:
            payloads.append(payload)
    return payloads


def merge_history(snapshot: Snapshot, directory: Path | None = None) -> int:
    """Fold committed history into a live snapshot.

    Live records always win: anything present on disk right now is more accurate
    than an archived summary of it. Restored entries are marked so the page can
    badge them as archival rather than current, and the count is returned so the
    masthead can say how much of the view is history.
    """
    target = directory or DEFAULT_HISTORY_DIR
    payloads = _load_payloads(target)
    if not payloads:
        return 0

    known_sessions = {s.session_id for s in snapshot.sessions}
    known_agents = {a.agent_id for a in snapshot.agents}
    known_files = {f.path for f in snapshot.files}
    known_commits = {c.sha for c in snapshot.commits}
    known_projects = {p.name for p in snapshot.projects}

    restored = 0

    for payload in payloads:
        for row in payload.get("sessions") or []:
            session_id = row.get("id")
            if not isinstance(session_id, str) or session_id in known_sessions:
                continue
            known_sessions.add(session_id)
            restored += 1
            snapshot.sessions.append(
                Session(
                    session_id=session_id,
                    name=row.get("name"),
                    cwd=row.get("cwd"),
                    branch=row.get("branch"),
                    started=_parse(row.get("started")),
                    ended=_parse(row.get("ended")),
                    tools=ToolUsage(counts=dict(row.get("tool_counts") or {})),
                    models=list(row.get("models") or []),
                    input_tokens=int(row.get("input_tokens") or 0),
                    output_tokens=int(row.get("output_tokens") or 0),
                    cache_tokens=int(row.get("cache_tokens") or 0),
                    user_turns=int(row.get("user_turns") or 0),
                    decisions=int(row.get("decisions") or 0),
                    agent_ids=[a for a in (row.get("agent_ids") or []) if isinstance(a, str)],
                    provenance=Provenance.RESTORED,
                )
            )

        for row in payload.get("agents") or []:
            agent_id = row.get("id")
            if not isinstance(agent_id, str) or agent_id in known_agents:
                continue
            known_agents.add(agent_id)
            snapshot.agents.append(
                Agent(
                    agent_id=agent_id,
                    description=row.get("description"),
                    agent_type=row.get("type"),
                    session_id=row.get("session_id"),
                    started=_parse(row.get("started")),
                    ended=_parse(row.get("ended")),
                    status=_status(row.get("status")),
                    tools=ToolUsage(counts=dict(row.get("tool_counts") or {})),
                    input_tokens=int(row.get("input_tokens") or 0),
                    output_tokens=int(row.get("output_tokens") or 0),
                    cache_tokens=int(row.get("cache_tokens") or 0),
                    output_chars=int(row.get("output_chars") or 0),
                    verify_runs=int(row.get("verify_runs") or 0),
                    ship_actions=int(row.get("ship_actions") or 0),
                    files_touched=list(row.get("files_touched") or []),
                    provenance=Provenance.RESTORED,
                )
            )

        for row in payload.get("files") or []:
            path_value = row.get("path")
            if not isinstance(path_value, str) or path_value in known_files:
                continue
            known_files.add(path_value)
            snapshot.files.append(
                BuiltFile(
                    path=path_value,
                    project=row.get("project"),
                    writes=int(row.get("writes") or 0),
                    edits=int(row.get("edits") or 0),
                    first_seen=_parse(row.get("first_seen")),
                    last_seen=_parse(row.get("last_seen")),
                )
            )

        for row in payload.get("commits") or []:
            sha = row.get("sha")
            if not isinstance(sha, str) or sha in known_commits:
                continue
            known_commits.add(sha)
            snapshot.commits.append(
                Commit(
                    sha=sha,
                    subject=row.get("subject") or "",
                    author=row.get("author") or "",
                    timestamp=_parse(row.get("timestamp")),
                    files_changed=int(row.get("files_changed") or 0),
                    insertions=int(row.get("insertions") or 0),
                    deletions=int(row.get("deletions") or 0),
                    branch=row.get("branch"),
                )
            )

        for row in payload.get("projects") or []:
            name = row.get("name")
            if not isinstance(name, str) or name in known_projects:
                continue
            known_projects.add(name)
            snapshot.projects.append(
                Project(
                    name=name,
                    path=row.get("path") or "",
                    language=row.get("language"),
                    loc=int(row.get("loc") or 0),
                    source_files=int(row.get("source_files") or 0),
                    test_files=int(row.get("test_files") or 0),
                    test_count=int(row.get("test_count") or 0),
                    branch=row.get("branch"),
                    last_commit=_parse(row.get("last_commit")),
                )
            )

    snapshot.restored_sessions = restored
    return restored


def _status(value: Any) -> Status:
    try:
        return Status(value)
    except (ValueError, TypeError):
        return Status.UNKNOWN
