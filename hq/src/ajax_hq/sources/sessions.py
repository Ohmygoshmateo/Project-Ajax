"""Session registry — ``~/.claude/sessions/*.json``.

Small files holding a session's human-readable name, working directory, start
time, and client version. Transcripts do not carry the name, so this is what
turns an opaque UUID into "project-ajax-51".

Registry entries are best-effort: a session can have a transcript with no
registry file (the process exited and the file was cleaned up) or a registry
entry with no transcript. Both are handled by treating this purely as an
enrichment pass over transcript-derived sessions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ajax_hq.model import Session


def _load_entries(claude_home: Path) -> list[dict]:
    root = claude_home / "sessions"
    if not root.is_dir():
        return []

    entries: list[dict] = []
    for path in sorted(root.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def enrich(sessions: list[Session], claude_home: Path) -> list[str]:
    """Fill in names and metadata from the registry. Returns any warnings."""
    warnings: list[str] = []
    entries = _load_entries(claude_home)
    if not entries:
        return warnings

    by_id = {
        str(e.get("sessionId")): e for e in entries if isinstance(e.get("sessionId"), str)
    }

    for session in sessions:
        entry = by_id.get(session.session_id)
        if not entry:
            continue

        name = entry.get("name")
        if isinstance(name, str) and name:
            session.name = name

        for key, attr in (("cwd", "cwd"), ("version", "client_version"),
                          ("entrypoint", "entrypoint")):
            value = entry.get(key)
            if isinstance(value, str) and value and getattr(session, attr) is None:
                setattr(session, attr, value)

        started = entry.get("startedAt")
        if isinstance(started, (int, float)) and started > 0:
            try:
                # Registry timestamps are epoch milliseconds.
                stamp = datetime.fromtimestamp(started / 1000, tz=UTC)
            except (OverflowError, OSError, ValueError):
                continue
            if session.started is None or stamp < session.started:
                session.started = stamp

    return warnings
