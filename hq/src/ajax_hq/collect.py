"""Assemble a Snapshot from every source.

One entry point, :func:`collect`, so the CLI, the server, and the tests all build
state the same way. Each source is wrapped: a failing source costs its panels,
never the page.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ajax_hq import divisions as divisions_mod
from ajax_hq.model import Snapshot, SourceRef
from ajax_hq.sources import projects as projects_mod
from ajax_hq.sources import sessions as sessions_mod
from ajax_hq.sources import transcripts, vcs
from ajax_hq.sources.modules import ajax_trading
from ajax_hq.timeutil import aware, now

DEFAULT_WORKSPACE = Path("/home/user")


def _source_ref(label: str, path: Path) -> SourceRef:
    try:
        stat = path.stat()
        return SourceRef(
            label=label,
            path=str(path),
            exists=True,
            modified=aware(datetime.fromtimestamp(stat.st_mtime)),
        )
    except OSError:
        return SourceRef(label=label, path=str(path), exists=False)


def collect(
    *,
    claude_home: Path | None = None,
    workspace: Path | None = None,
    commit_limit: int = 60,
) -> Snapshot:
    """Read everything and return one Snapshot."""
    home = claude_home or transcripts.DEFAULT_CLAUDE_HOME
    root = workspace or DEFAULT_WORKSPACE

    snapshot = Snapshot(generated_at=now())

    # --- agent activity -----------------------------------------------------
    try:
        sessions, agents, files, health = transcripts.load(home)
    except Exception as exc:  # noqa: BLE001 - a source failure must not kill the page
        sessions, agents, files = [], [], []
        from ajax_hq.model import SchemaHealth

        health = SchemaHealth(warnings=[f"transcript load failed: {exc}"])
    snapshot.sessions, snapshot.agents, snapshot.files = sessions, agents, files
    snapshot.schema = health

    try:
        snapshot.warnings.extend(sessions_mod.enrich(sessions, home))
    except Exception as exc:  # noqa: BLE001
        snapshot.warnings.append(f"session registry unreadable: {exc}")

    try:
        snapshot.plans = projects_mod.load_plans(home)
    except Exception as exc:  # noqa: BLE001
        snapshot.warnings.append(f"plans unreadable: {exc}")

    # --- projects and version control ---------------------------------------
    repos: list[Path] = []
    try:
        repos = projects_mod.discover(root)
        snapshot.projects = [projects_mod.inspect(repo) for repo in repos]
    except Exception as exc:  # noqa: BLE001
        snapshot.warnings.append(f"project discovery failed: {exc}")

    commits = []
    for repo in repos:
        try:
            commits.extend(vcs.commits(repo, limit=commit_limit))
        except Exception as exc:  # noqa: BLE001
            snapshot.warnings.append(f"git history unreadable for {repo.name}: {exc}")
    commits.sort(key=lambda c: aware(c.timestamp) or now(), reverse=True)
    snapshot.commits = commits

    # Attribute touched files to the project that contains them.
    for built in snapshot.files:
        built.project = projects_mod.project_for_path(built.path, snapshot.projects)

    # --- optional project module: Ajax trading ------------------------------
    trading_summary: dict[str, str] = {}
    trading_last: datetime | None = None
    for repo in repos:
        if ajax_trading.available(repo):
            try:
                trading_summary = ajax_trading.summarize(repo)
                trading_last = ajax_trading.last_activity(repo)
            except Exception as exc:  # noqa: BLE001
                snapshot.warnings.append(f"trading module unreadable: {exc}")
            break

    # --- divisions ----------------------------------------------------------
    snapshot.divisions = divisions_mod.build_all(
        sessions=snapshot.sessions,
        agents=snapshot.agents,
        files=snapshot.files,
        commits=snapshot.commits,
        projects=snapshot.projects,
        plans=snapshot.plans,
        trading_summary=trading_summary,
        trading_last_active=trading_last,
    )

    # --- provenance ---------------------------------------------------------
    snapshot.sources = [
        _source_ref("Session transcripts", home / "projects"),
        _source_ref("Session registry", home / "sessions"),
        _source_ref("Plans", home / "plans"),
        _source_ref("Workspace", root),
    ]

    return snapshot
