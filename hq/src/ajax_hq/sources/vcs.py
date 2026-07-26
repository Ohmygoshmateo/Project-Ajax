"""Git history — the durable record of what agents actually shipped.

Transcripts show intent to write a file; commits show what survived review and
landed. Both are displayed, and the UI keeps them distinct.

Every git call is wrapped: a missing binary, a non-repo directory, or a repo with
no commits yet all yield an empty result rather than an error.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ajax_hq.model import Commit

_TIMEOUT = 20
_SEP = "\x1f"  # unit separator — safe inside commit subjects


def _git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def is_repo(path: Path) -> bool:
    return _git(path, "rev-parse", "--is-inside-work-tree") is not None


def current_branch(repo: Path) -> str | None:
    out = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if out and out.strip() else None


def branches(repo: Path) -> list[str]:
    out = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [line.strip() for line in (out or "").splitlines() if line.strip()]


def is_dirty(repo: Path) -> bool:
    out = _git(repo, "status", "--porcelain")
    return bool(out and out.strip())


def commits(repo: Path, limit: int = 50) -> list[Commit]:
    """Recent commits with churn statistics."""
    fmt = _SEP.join(["%H", "%s", "%an", "%aI"])
    out = _git(repo, "log", f"-{limit}", f"--pretty=format:{fmt}", "--numstat")
    if not out:
        return []

    branch = current_branch(repo)
    found: list[Commit] = []
    current: Commit | None = None

    for raw in out.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        if _SEP in line:
            parts = line.split(_SEP)
            if len(parts) >= 4:
                current = Commit(
                    sha=parts[0].strip(),
                    subject=parts[1].strip(),
                    author=parts[2].strip(),
                    timestamp=_parse_iso(parts[3].strip()),
                    branch=branch,
                )
                found.append(current)
            continue

        # numstat rows: "<added>\t<deleted>\t<path>", with '-' for binary files.
        if current is None:
            continue
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        current.files_changed += 1
        current.insertions += _int(fields[0])
        current.deletions += _int(fields[1])

    return found


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0  # '-' marks a binary file, which has no line counts


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.fromtimestamp(int(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
