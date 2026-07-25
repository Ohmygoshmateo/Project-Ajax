"""Project discovery and health.

Walks a workspace root for git repositories and reports what each one contains:
dominant language, source and test file counts, lines of code, and git state.

Counts are measured from the filesystem, not inferred. Test *count* is the number
of discovered test functions found by a cheap text scan — not a run result, and
labelled as such in the UI so it is never mistaken for "tests passing".
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from ajax_hq.model import Project
from ajax_hq.sources import vcs

LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".sql": "SQL",
}

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache",
    ".ruff_cache", "dist", "build", ".mypy_cache", "site-packages", ".egg-info",
    "data_cache", "out",
}

# Cheap heuristics — a text scan, not an import or a test run.
_TEST_MARKERS = ("def test_", "it(", "test(", "func Test")


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts)


def discover(root: Path, max_depth: int = 3) -> list[Path]:
    """Find git repositories under ``root``."""
    if not root.is_dir():
        return []
    if (root / ".git").is_dir():
        return [root]

    found: list[Path] = []
    for candidate in sorted(root.rglob(".git")):
        if not candidate.is_dir():
            continue
        repo = candidate.parent
        if len(repo.relative_to(root).parts) > max_depth:
            continue
        found.append(repo)
    return found


def inspect(repo: Path) -> Project:
    """Measure one repository."""
    project = Project(name=repo.name, path=str(repo))

    languages: Counter[str] = Counter()
    loc = 0
    source_files = 0
    test_files = 0
    test_count = 0

    for path in repo.rglob("*"):
        if not path.is_file() or _should_skip(path.relative_to(repo)):
            continue
        language = LANGUAGE_BY_SUFFIX.get(path.suffix)
        if language is None:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = text.count("\n") + 1
        languages[language] += lines
        loc += lines
        source_files += 1

        name = path.name.lower()
        if name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".spec.ts")):
            test_files += 1
            test_count += sum(text.count(marker) for marker in _TEST_MARKERS)

    project.language = languages.most_common(1)[0][0] if languages else None
    project.loc = loc
    project.source_files = source_files
    project.test_files = test_files
    project.test_count = test_count
    project.branch = vcs.current_branch(repo)
    project.dirty = vcs.is_dirty(repo)

    recent = vcs.commits(repo, limit=1)
    if recent:
        project.last_commit = recent[0].timestamp

    return project


def project_for_path(file_path: str, projects: list[Project]) -> str | None:
    """Attribute a touched file to a project, longest path match wins."""
    best: str | None = None
    best_len = -1
    for project in projects:
        if file_path.startswith(project.path) and len(project.path) > best_len:
            best, best_len = project.name, len(project.path)
    return best


def load_plans(claude_home: Path) -> list:
    """Plan documents from ``~/.claude/plans`` — the record of intended work."""
    from ajax_hq.model import Plan

    root = claude_home / "plans"
    if not root.is_dir():
        return []

    plans = []
    for path in sorted(root.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            modified = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue

        heading = None
        for line in text.splitlines():
            if line.startswith("# "):
                heading = line[2:].strip()
                break

        plans.append(
            Plan(
                name=path.stem,
                path=str(path),
                modified=modified,
                heading=heading,
                words=len(text.split()),
            )
        )
    return plans
