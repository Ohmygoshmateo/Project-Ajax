"""Derive the six divisions from real activity.

The chaebol structure is the organising metaphor; the figures underneath are
measured, not assigned. Every division reports the sources it read, and a
division with no underlying activity says ``NEVER ACTIVE`` rather than showing
zeros as though work had happened and produced nothing.

This module is a registry: adding a division later is appending one builder.
"""

from __future__ import annotations

from datetime import datetime

from ajax_hq.model import (
    Agent,
    BuiltFile,
    Commit,
    Division,
    Plan,
    Project,
    Session,
    Status,
)
from ajax_hq.timeutil import hours_since, newest

# A division counts as active if it did something within roughly a working day.
ACTIVE_WINDOW_HOURS = 12.0

RESEARCH_TYPES = {"explore", "plan", "general-purpose", "research"}
BUILD_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}
TEST_PATTERNS = ("pytest", "ruff", "npm test", "cargo test", "go test", "mypy", "eslint")


def _status(last_active: datetime | None, *, has_data: bool, degraded: bool = False) -> Status:
    if not has_data:
        return Status.NEVER_ACTIVE
    if degraded:
        return Status.DEGRADED
    age = hours_since(last_active)
    if age is None:
        return Status.IDLE
    return Status.ACTIVE if age <= ACTIVE_WINDOW_HOURS else Status.IDLE


def _fmt(n: int) -> str:
    return f"{n:,}"


def executive_office(sessions: list[Session], plans: list[Plan]) -> Division:
    """Sessions, plans, and the points where a decision was put to the user."""
    decisions = sum(s.decisions for s in sessions)
    turns = sum(s.user_turns for s in sessions)
    last = newest(*[s.ended for s in sessions], *[p.modified for p in plans])

    division = Division(
        code="EXO",
        name="Executive Office",
        korean="비서실",
        mandate="Direction, planning, and decisions referred to the principal.",
        status=_status(last, has_data=bool(sessions or plans)),
        last_active=last,
        sources=["~/.claude/sessions", "~/.claude/plans"],
    )
    division.metrics = [
        ("Work streams", _fmt(len(sessions))),
        ("Decision points", _fmt(decisions)),
        ("Principal turns", _fmt(turns)),
        ("Plans on file", _fmt(len(plans))),
    ]
    if plans:
        newest_plan = max(plans, key=lambda p: p.modified or datetime.min)
        division.notes.append(f"Latest plan: {newest_plan.heading or newest_plan.name}")
    return division


def research_and_development(agents: list[Agent]) -> Division:
    """Subagents dispatched to investigate, verify, or design."""
    research = [
        a for a in agents if (a.agent_type or "").lower() in RESEARCH_TYPES
    ]
    last = newest(*[a.ended for a in research])
    searches = sum(
        count
        for a in research
        for name, count in a.tools.counts.items()
        if name in {"WebSearch", "WebFetch"}
    )

    division = Division(
        code="RND",
        name="Research & Development",
        korean="연구개발부",
        mandate="Investigation, verification, and design of what gets built.",
        status=_status(last, has_data=bool(research)),
        last_active=last,
        sources=["~/.claude/projects/*/*/subagents"],
    )
    division.metrics = [
        ("Agents dispatched", _fmt(len(research))),
        ("Tool calls", _fmt(sum(a.tools.total for a in research))),
        ("External lookups", _fmt(searches)),
        ("Tokens", _fmt(sum(a.total_tokens for a in research))),
    ]
    by_type: dict[str, int] = {}
    for agent in research:
        key = agent.agent_type or "unknown"
        by_type[key] = by_type.get(key, 0) + 1
    if by_type:
        division.notes.append(
            "Roster: " + ", ".join(f"{k} ×{v}" for k, v in sorted(by_type.items()))
        )
    return division


def engineering(sessions: list[Session], agents: list[Agent], files: list[BuiltFile]) -> Division:
    """Files written and edited — derived from tool inputs, not a disk diff."""
    writes = sum(f.writes for f in files)
    edits = sum(f.edits for f in files)
    last = newest(*[f.last_seen for f in files])

    division = Division(
        code="ENG",
        name="Engineering",
        korean="엔지니어링부",
        mandate="Construction of the work product itself.",
        status=_status(last, has_data=bool(files)),
        last_active=last,
        sources=["session transcripts (Write/Edit tool inputs)"],
    )
    division.metrics = [
        ("Files touched", _fmt(len(files))),
        ("Files created", _fmt(writes)),
        ("Revisions", _fmt(edits)),
        ("Build calls", _fmt(writes + edits)),
    ]
    division.notes.append(
        "Derived from tool calls, not a filesystem diff — counts intent to change."
    )
    contributors = sum(1 for a in agents if a.files_touched)
    if contributors:
        division.notes.append(f"{contributors} subagent(s) also wrote files.")
    return division


def quality_assurance(sessions: list[Session], agents: list[Agent]) -> Division:
    """Test and lint invocations, counted from Bash commands actually run."""
    runs = 0
    matched: dict[str, int] = {}
    last = None

    for session in sessions:
        for command in session.commands_run:
            for pattern in TEST_PATTERNS:
                if pattern in command:
                    runs += 1
                    matched[pattern] = matched.get(pattern, 0) + 1
                    last = newest(last, session.ended)
                    break

    for agent in agents:
        for command in agent.commands_run:
            for pattern in TEST_PATTERNS:
                if pattern in command:
                    runs += 1
                    matched[pattern] = matched.get(pattern, 0) + 1
                    last = newest(last, agent.ended)
                    break

    session_bash = sum(s.tools.counts.get("Bash", 0) for s in sessions)
    session_bash += sum(a.tools.counts.get("Bash", 0) for a in agents)
    if session_bash and last is None:
        last = newest(*[s.ended for s in sessions])

    division = Division(
        code="QA",
        name="Quality Assurance",
        korean="품질관리부",
        mandate="Verification that what was built actually works.",
        status=_status(last, has_data=bool(session_bash or runs)),
        last_active=last,
        sources=["session transcripts (Bash tool inputs)"],
    )
    division.metrics = [
        ("Verification runs", _fmt(runs)),
        ("Shell invocations", _fmt(session_bash)),
        ("Suites invoked", _fmt(len(matched))),
    ]
    if matched:
        division.notes.append(
            "Tooling: " + ", ".join(f"{k} ×{v}" for k, v in sorted(matched.items()))
        )
    division.notes.append(
        "Counts invocations, not outcomes — a run appearing here is not a pass."
    )
    return division


def operations(commits: list[Commit], projects: list[Project]) -> Division:
    """Commits, branches, and repository state."""
    last = newest(*[c.timestamp for c in commits])
    churn = sum(c.churn for c in commits)
    dirty = [p.name for p in projects if p.dirty]

    division = Division(
        code="OPS",
        name="Operations",
        korean="운영부",
        mandate="Shipping: what landed in version control.",
        status=_status(last, has_data=bool(commits)),
        last_active=last,
        sources=["git log", "git status"],
    )
    division.metrics = [
        ("Commits", _fmt(len(commits))),
        ("Lines changed", _fmt(churn)),
        ("Repositories", _fmt(len(projects))),
        ("Uncommitted", _fmt(len(dirty))),
    ]
    if dirty:
        division.notes.append("Working tree dirty: " + ", ".join(sorted(dirty)))
    if commits:
        division.notes.append(f"Latest: {commits[0].subject[:70]}")
    return division


def asset_management(
    module_summary: dict[str, str], last_active: datetime | None
) -> Division:
    """The slot for a project module — a system HQ watches beyond agent activity.

    The other five divisions are derived from transcripts and git, which every
    workspace has. This one is deliberately generic: a project supplies its own
    figures through a module, and until one does the division is empty and says
    so rather than displaying a zeroed chart.
    """
    division = Division(
        code="AST",
        name="Asset Management",
        korean="자산운용부",
        mandate="Figures from an installed project module, if a project supplies one.",
        status=_status(last_active, has_data=bool(module_summary)),
        last_active=last_active,
        sources=["project module"],
    )
    if not module_summary:
        division.notes.append(
            "No project module installed — see hq/src/ajax_hq/sources/modules/."
        )
        return division

    division.metrics = [(k, v) for k, v in module_summary.items()][:4]
    division.notes.append("Supplied by a project module, not derived from transcripts.")
    return division


def build_all(
    *,
    sessions: list[Session],
    agents: list[Agent],
    files: list[BuiltFile],
    commits: list[Commit],
    projects: list[Project],
    plans: list[Plan],
    module_summary: dict[str, str] | None = None,
    module_last_active: datetime | None = None,
) -> list[Division]:
    """The division registry. Appending a builder here adds a division."""
    return [
        executive_office(sessions, plans),
        research_and_development(agents),
        engineering(sessions, agents, files),
        quality_assurance(sessions, agents),
        operations(commits, projects),
        asset_management(module_summary or {}, module_last_active),
    ]
