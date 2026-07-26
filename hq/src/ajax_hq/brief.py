"""The daily brief — what happened since yesterday.

The dashboard answers "what exists"; this answers "what changed". It is the
thing you read once a day, so the whole value is in it being trustworthy at a
glance: if it says four agents ran, four agents ran.

Three rules make that hold, and they are the reason this module is more careful
than its size suggests.

**The window is fixed before anything is counted, and never moved.** A digest
that quietly widened itself until it found something to report would be worse
than useless — it would make a quiet day look like a busy one, and you could not
tell which you were reading. An empty window says so and stops.

**A record with no timestamp cannot be placed.** Transcripts are undocumented
internals and some records arrive without a usable stamp. Assuming those are
recent would inflate the brief; dropping them silently would hide a gap in the
data. They are counted separately and reported as unplaceable.

**Figures keep the caveats they were born with.** Individual tool calls are
timestamped, but the aggregates built from them are not: a :class:`BuiltFile`
carries lifetime write/edit totals with only first- and last-touch stamps, and a
session's shell commands are stored as a flat list with no per-command time. So
this module reports what the window can actually support — *which* files were
touched in the window, not how many times they were touched inside it — and says
which figures span more than the window.

Agent effort is measured text emitted, never ``output_tokens``; see
:attr:`ajax_hq.model.Agent.output_tokens_are_plausible` for why that field is
unusable in subagent transcripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ajax_hq.behaviour import count_commands, wing_for
from ajax_hq.floor import DIM, FAINT, GOLD, WING_NAMES
from ajax_hq.model import Agent, BuiltFile, Commit, Session, Snapshot, ToolUsage
from ajax_hq.timeutil import aware, now

DEFAULT_WINDOW_HOURS = 24.0

TOP_FILES = 6
TOP_TOOLS = 6
TOP_COMMITS = 10


# --------------------------------------------------------------------- window


@dataclass(frozen=True)
class Window:
    """The interval the brief covers. Decided once, up front, and never moved."""

    start: datetime
    end: datetime

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0

    def contains(self, value: datetime | None) -> bool:
        """Half-open: ``start <= t < end``.

        Deliberate, because briefs are read back to back. Half-open intervals
        tile exactly, so a record sitting on a boundary appears in precisely one
        day's brief — inclusive-both would report it twice and exclusive-both
        would lose it. The lower edge is the inclusive one because that is the
        edge a reader names ("since yesterday"); the upper edge is the moment
        the snapshot was taken, which no record read from it can reach.

        ``None`` is never in the window. It is not "not recent" — it is
        unplaceable, and counted as such by :class:`Unplaceable`.
        """
        stamp = aware(value)
        if stamp is None:
            return False
        return self.start <= stamp < self.end

    def overlaps(self, start: datetime | None, end: datetime | None) -> bool:
        """Does a record spanning ``start``-``end`` touch the window at all?

        Used for sessions, which are intervals rather than instants. A session
        with only one usable bound is treated as an instant at that bound; one
        with neither is unplaceable, exactly as for a stamp-less record.
        """
        first, last = aware(start), aware(end)
        if first is None and last is None:
            return False
        first = first or last
        last = last or first
        return first < self.end and last >= self.start  # type: ignore[operator]

    @property
    def label(self) -> str:
        return (
            f"{self.start:%Y-%m-%d %H:%M} → {self.end:%Y-%m-%d %H:%M} UTC "
            f"({_hours_label(self.hours)})"
        )


def _hours_label(hours: float) -> str:
    if hours >= 48:
        return f"{hours / 24:.0f}d"
    if hours == int(hours):
        return f"{int(hours)}h"
    return f"{hours:.1f}h"


# ---------------------------------------------------------------- components


@dataclass(frozen=True)
class Unplaceable:
    """Records that carry no timestamp, and so belong to no window.

    Reported rather than absorbed: the count is the reader's warning that the
    brief is not a complete account of the period.
    """

    agents: int = 0
    files: int = 0
    commits: int = 0
    sessions: int = 0

    @property
    def total(self) -> int:
        return self.agents + self.files + self.commits + self.sessions

    @property
    def summary(self) -> str:
        parts = [
            (self.agents, "agent"),
            (self.files, "file"),
            (self.commits, "commit"),
            (self.sessions, "session"),
        ]
        listed = ", ".join(f"{n} {noun}{'' if n == 1 else 's'}" for n, noun in parts if n)
        return listed or "none"


@dataclass(frozen=True)
class FileActivity:
    """Files touched in the window, derived from Write/Edit tool calls.

    ``top`` carries each path's *lifetime* touch count, not a within-window one.
    A :class:`~ajax_hq.model.BuiltFile` records only its first and last touch, so
    the touches between them cannot be placed; reporting the lifetime figure and
    labelling it is the honest option, and inventing a window-local count is not.
    """

    touched: int = 0
    created: int = 0
    revised: int = 0
    top: tuple[tuple[str, int], ...] = ()

    @property
    def any(self) -> bool:
        return self.touched > 0


@dataclass(frozen=True)
class Verification:
    """Test and lint invocations, counted from commands actually recorded.

    Split by how well each half can be placed. Agent commands travel with an
    agent that has a dispatch time, so they land in the window with it. Session
    commands are a flat list with no per-command stamp, so they can only be
    attributed when the *whole session* fits inside the window — a session that
    straddles an edge contributes nothing rather than a guess, and is counted in
    ``straddling_sessions`` so the shortfall is visible.
    """

    agent_runs: int = 0
    session_runs: int = 0
    straddling_sessions: int = 0

    @property
    def total(self) -> int:
        return self.agent_runs + self.session_runs


@dataclass(frozen=True)
class DivisionActivity:
    """One division's share of the window, and what that number is made of."""

    code: str
    name: str
    events: int
    basis: str


@dataclass(frozen=True)
class Brief:
    """One window's worth of activity. Every field is measured or absent."""

    generated_at: datetime
    window: Window
    agents: tuple[Agent, ...] = ()
    sessions: tuple[Session, ...] = ()
    files: FileActivity = field(default_factory=FileActivity)
    commits: tuple[Commit, ...] = ()
    verification: Verification = field(default_factory=Verification)
    tools: tuple[tuple[str, int], ...] = ()
    tool_calls: int = 0
    tool_sources: int = 0
    divisions: tuple[DivisionActivity, ...] = ()
    unplaceable: Unplaceable = field(default_factory=Unplaceable)

    @property
    def is_empty(self) -> bool:
        """Nothing at all happened in the window — a real answer, not a failure."""
        return not (
            self.agents
            or self.sessions
            or self.commits
            or self.files.any
            or self.verification.total
            or self.tool_calls
        )

    @property
    def busiest_division(self) -> DivisionActivity | None:
        """The single busiest division, or ``None`` when nothing separates them.

        A tie has no winner. Breaking one by declaring precedence would put a
        name on the brief that the figures did not choose, so a tie is reported
        as a tie — see :attr:`tied_divisions`.
        """
        ranked = self.ranked_divisions
        if not ranked:
            return None
        if len(ranked) > 1 and ranked[1].events == ranked[0].events:
            return None
        return ranked[0]

    @property
    def ranked_divisions(self) -> tuple[DivisionActivity, ...]:
        active = [d for d in self.divisions if d.events]
        return tuple(sorted(active, key=lambda d: (-d.events, d.code)))

    @property
    def tied_divisions(self) -> tuple[DivisionActivity, ...]:
        ranked = self.ranked_divisions
        if len(ranked) < 2 or ranked[1].events != ranked[0].events:
            return ()
        return tuple(d for d in ranked if d.events == ranked[0].events)


# ------------------------------------------------------------------ building


def _window(snapshot: Snapshot, since: datetime | None, window_hours: float) -> Window:
    """Fix the interval before counting anything.

    The end is the snapshot's generation time rather than "now": the snapshot is
    the only thing being read, so a window extending past the moment it was taken
    would claim to cover time no record could occupy.
    """
    end = aware(snapshot.generated_at) or now()
    start = aware(since) or (end - timedelta(hours=window_hours))
    # An explicit `since` after the snapshot collapses the window to nothing.
    # That reports zero activity, which is correct — it must never flip around
    # into a backwards interval that matches every record.
    return Window(start=min(start, end), end=end)


def _file_activity(files: list[BuiltFile], window: Window) -> tuple[FileActivity, int]:
    touched = [f for f in files if window.contains(f.last_seen)]
    undated = sum(1 for f in files if f.last_seen is None)

    # "Created in the window" means the file's first recorded touch is also
    # inside it. A file first written last week and edited today is a revision,
    # regardless of how many Write calls its lifetime total holds.
    created = sum(1 for f in touched if window.contains(f.first_seen) and f.writes)
    top = tuple(
        (f.path, f.touches)
        for f in sorted(touched, key=lambda f: (-f.touches, f.path))[:TOP_FILES]
    )
    return (
        FileActivity(
            touched=len(touched),
            created=created,
            revised=len(touched) - created,
            top=top,
        ),
        undated,
    )


def _verification(
    agents: list[Agent], sessions: list[Session], window: Window
) -> Verification:
    agent_runs = sum(a.verify_runs for a in agents)

    session_runs = straddling = 0
    for session in sessions:
        if window.contains(session.started) and window.contains(session.ended):
            session_runs += count_commands(session.commands_run)[0]
        elif session.commands_run:
            straddling += 1

    return Verification(
        agent_runs=agent_runs,
        session_runs=session_runs,
        straddling_sessions=straddling,
    )


def _tool_usage(
    agents: list[Agent], sessions: list[Session], window: Window
) -> tuple[ToolUsage, int]:
    """Tool calls that can be placed in the window, and how many records supplied them.

    An agent's counts travel with its dispatch time. A session's do not — they
    are a running total over the session's whole life — so a session only
    contributes when it began and ended inside the window and its totals
    therefore cannot include anything outside it.
    """
    usage = ToolUsage()
    contributors = 0

    for agent in agents:
        usage.merge(agent.tools)
        contributors += 1
    for session in sessions:
        if window.contains(session.started) and window.contains(session.ended):
            usage.merge(session.tools)
            contributors += 1

    return usage, contributors


def _divisions(
    agents: list[Agent],
    sessions: list[Session],
    files: FileActivity,
    commits: list[Commit],
    verification: Verification,
) -> tuple[DivisionActivity, ...]:
    """Rank divisions by the records the window can attribute to each.

    The units are mixed — an agent, a file, and a commit are not comparable
    quantities — so this ranks *activity*, not output, and every entry publishes
    its composition in ``basis`` so the number can be taken apart. Agents are
    seated by their tool record rather than their declared type, matching
    :mod:`ajax_hq.floor`; the alternative is two places in HQ disagreeing about
    where the same agent works.
    """
    events: dict[str, int] = {code: 0 for code in WING_NAMES}
    parts: dict[str, list[str]] = {code: [] for code in WING_NAMES}

    seated: dict[str, int] = {}
    for agent in agents:
        code = wing_for(agent)
        seated[code] = seated.get(code, 0) + 1
    for code, count in seated.items():
        events[code] += count
        parts[code].append(f"{count} agent{'' if count == 1 else 's'}")

    if sessions:
        events["EXO"] += len(sessions)
        parts["EXO"].append(f"{len(sessions)} work stream{'' if len(sessions) == 1 else 's'}")
    if files.touched:
        events["ENG"] += files.touched
        parts["ENG"].append(f"{files.touched} file{'' if files.touched == 1 else 's'}")
    if commits:
        events["OPS"] += len(commits)
        parts["OPS"].append(f"{len(commits)} commit{'' if len(commits) == 1 else 's'}")
    if verification.total:
        events["QA"] += verification.total
        total = verification.total
        parts["QA"].append(f"{total} verification run{'' if total == 1 else 's'}")

    return tuple(
        DivisionActivity(
            code=code,
            name=WING_NAMES[code][0],
            events=events[code],
            basis=", ".join(parts[code]) or "nothing recorded",
        )
        for code in WING_NAMES
    )


def build(
    snapshot: Snapshot,
    since: datetime | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
) -> Brief:
    """Summarise one window of ``snapshot``.

    ``since`` overrides the window start; otherwise it is ``window_hours``
    before the snapshot was taken. The window is never adjusted afterwards —
    not to reach an earlier record, and not because the result came back empty.
    """
    window = _window(snapshot, since, window_hours)

    # Agents are placed by dispatch time. An agent that started inside the
    # window and is still running belongs to it; one dispatched before it does
    # not, even if it finished inside — the window reports work *begun*, and
    # picking whichever bound happened to land inside would make membership
    # depend on when a run ended rather than on a single stated rule.
    agents = [a for a in snapshot.agents if window.contains(a.started)]
    sessions = [s for s in snapshot.sessions if window.overlaps(s.started, s.ended)]
    commits = [c for c in snapshot.commits if window.contains(c.timestamp)]
    files, undated_files = _file_activity(snapshot.files, window)

    verification = _verification(agents, sessions, window)
    usage, contributors = _tool_usage(agents, sessions, window)

    unplaceable = Unplaceable(
        agents=sum(1 for a in snapshot.agents if a.started is None),
        files=undated_files,
        commits=sum(1 for c in snapshot.commits if c.timestamp is None),
        sessions=sum(
            1 for s in snapshot.sessions if s.started is None and s.ended is None
        ),
    )

    return Brief(
        generated_at=aware(snapshot.generated_at) or now(),
        window=window,
        agents=tuple(sorted(agents, key=lambda a: (aware(a.started) or window.start, a.agent_id))),
        sessions=tuple(sessions),
        files=files,
        commits=tuple(commits[:TOP_COMMITS]),
        verification=verification,
        tools=tuple(usage.top(TOP_TOOLS)),
        tool_calls=usage.total,
        tool_sources=contributors,
        divisions=_divisions(agents, sessions, files, commits, verification),
        unplaceable=unplaceable,
    )


# ----------------------------------------------------------------- rendering


def _stamp(value: datetime | None, fmt: str = "%m-%d %H:%M") -> str:
    stamp = aware(value)
    return stamp.strftime(fmt) if stamp else "—"


def _table() -> Table:
    return Table(box=None, pad_edge=False, header_style=f"bold {DIM}", expand=False)


def _column(table: Table, header: str, *, width: int | None = None,
            right: bool = False) -> None:
    """Add a column that clips rather than wraps.

    Widths are capped on the free-text columns only. Left to itself Rich shares
    the deficit across every column when the terminal is narrow, which turns the
    short factual ones — a duration, a tool count — into ellipses while the long
    prose column keeps most of the space. Clipping the prose instead keeps the
    figures readable, which is what the brief is for.
    """
    table.add_column(
        header,
        max_width=width,
        justify="right" if right else "left",
        overflow="ellipsis",
        no_wrap=True,
    )


def _note(console: Console, text: str) -> None:
    console.print(Text(f"  {text}", style=FAINT))


def render(brief: Brief, console: Console | None = None) -> None:
    """Print the brief."""
    console = console or Console()

    console.print()
    console.rule(Text("AJAX HQ  ·  일일 보고  ·  DAILY BRIEF", style=f"bold {GOLD}"), style=GOLD)
    console.print()
    console.print(Text(f"  {brief.window.label}", style=DIM))
    console.print()

    if brief.is_empty:
        _render_quiet(brief, console)
        return

    _render_headline(brief, console)
    _render_agents(brief, console)
    _render_files(brief, console)
    _render_commits(brief, console)
    _render_tools(brief, console)
    _render_division(brief, console)
    _render_footnotes(brief, console)


def _render_quiet(brief: Brief, console: Console) -> None:
    """The empty window. Stated plainly, and the window stays where it was."""
    body = Text()
    body.append(
        f"Nothing recorded in the last {_hours_label(brief.window.hours)}.\n", style="bold"
    )
    body.append(
        "No agents dispatched, no files touched, no commits landed, no shell work.\n",
        style=FAINT,
    )
    body.append(
        "The window is not widened to find something to show — a quiet day is a "
        "real answer.",
        style=FAINT,
    )
    console.print(Panel(body, border_style="grey23", padding=(0, 1)))

    if brief.unplaceable.total:
        console.print()
        _render_unplaceable(brief, console)
    console.print()


def _render_headline(brief: Brief, console: Console) -> None:
    line = Text("  ")
    tiles: list[tuple[str, str]] = [
        (str(len(brief.agents)), "agents"),
        (str(brief.files.touched), "files"),
        (str(len(brief.commits)), "commits"),
        (str(brief.verification.total), "verify runs"),
        (f"{brief.tool_calls:,}", "tool calls"),
    ]
    for index, (value, label) in enumerate(tiles):
        if index:
            line.append("   ·   ", style=FAINT)
        line.append(value, style=f"bold {GOLD}")
        line.append(f" {label}", style=DIM)
    console.print(line)
    console.print()


def _render_agents(brief: Brief, console: Console) -> None:
    if not brief.agents:
        return
    console.print(Text("  Agents dispatched", style="bold"))
    table = _table()
    _column(table, "", width=2)
    _column(table, "Task", width=38)
    _column(table, "Type", width=15)
    _column(table, "Dispatched")
    for header in ("Elapsed", "Tools", "Output"):
        _column(table, header, right=True)
    for agent in brief.agents:
        table.add_row(
            "  ",
            agent.title,
            agent.agent_type or "—",
            _stamp(agent.started),
            agent.duration_label,
            str(agent.tools.total),
            # Measured text emitted. Never output_tokens — a placeholder value
            # in subagent transcripts.
            f"{agent.output_label} out",
        )
    console.print(table)
    console.print()


def _render_files(brief: Brief, console: Console) -> None:
    if not brief.files.any:
        return
    summary = Text("  Files touched  ", style="bold")
    summary.append(f"{brief.files.touched}", style=GOLD)
    summary.append(
        f"  ({brief.files.created} first written in window, {brief.files.revised} revised)",
        style=DIM,
    )
    console.print(summary)

    table = _table()
    _column(table, "", width=2)
    _column(table, "Path", width=70)
    _column(table, "Touches (lifetime)", right=True)
    for path, touches in brief.files.top:
        table.add_row("  ", path, str(touches))
    console.print(table)
    console.print()


def _render_commits(brief: Brief, console: Console) -> None:
    if not brief.commits:
        return
    console.print(Text("  Commits landed", style="bold"))
    table = _table()
    _column(table, "", width=2)
    _column(table, "SHA")
    _column(table, "Subject", width=62)
    _column(table, "When")
    for commit in brief.commits:
        table.add_row("  ", commit.short_sha, commit.subject, _stamp(commit.timestamp))
    console.print(table)
    console.print()


def _render_tools(brief: Brief, console: Console) -> None:
    if not brief.tools:
        return
    header = Text("  Tool calls  ", style="bold")
    header.append(f"{brief.tool_calls:,}", style=GOLD)
    header.append(f"  from {brief.tool_sources} record(s) placeable in the window", style=DIM)
    console.print(header)
    line = Text("  ")
    for index, (name, count) in enumerate(brief.tools):
        if index:
            line.append("   ", style=FAINT)
        line.append(name, style="white")
        line.append(f" {count}", style=DIM)
    console.print(line)
    console.print()


def _render_division(brief: Brief, console: Console) -> None:
    busiest = brief.busiest_division
    if busiest is not None:
        line = Text("  Busiest division  ", style="bold")
        line.append(f"{busiest.code} {busiest.name}", style=GOLD)
        line.append(f"  — {busiest.basis}", style=DIM)
        console.print(line)
        console.print()
        return

    tied = brief.tied_divisions
    if tied:
        names = ", ".join(f"{d.code} {d.name}" for d in tied)
        line = Text("  Busiest division  ", style="bold")
        line.append("tied", style=GOLD)
        line.append(f"  — {names}, {tied[0].events} records each", style=DIM)
        console.print(line)
        console.print()


def _render_unplaceable(brief: Brief, console: Console) -> None:
    console.print(
        Text(
            f"  ⚠ {brief.unplaceable.total} record(s) carry no timestamp and cannot be "
            f"placed in any window: {brief.unplaceable.summary}.",
            style="yellow3",
        )
    )
    _note(console, "They are excluded from the figures above rather than assumed recent.")


def _render_footnotes(brief: Brief, console: Console) -> None:
    _note(console, "File counts come from Write/Edit tool calls — intent to change, "
                   "not a filesystem diff.")
    _note(console, "Lifetime touches span each file's whole record; individual touches "
                   "are not separately timestamped.")
    _note(console, "Agent effort is measured text emitted, not the reported token count.")
    _note(console, f"Window is fixed at {_hours_label(brief.window.hours)} and is never "
                   f"widened to find activity.")

    if brief.verification.straddling_sessions:
        n = brief.verification.straddling_sessions
        _note(console, f"{n} session(s) overlap the window but began or ended outside it; "
                       "their shell commands carry no per-command timestamp, so none are "
                       "counted as verification runs.")

    if brief.unplaceable.total:
        console.print()
        _render_unplaceable(brief, console)
    console.print()
