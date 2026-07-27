"""The virtual floor — a terminal office plan of who works here.

The roster table answers "what did each agent do". This answers a different
question: *what shape is the org?* How many staff, in which division, and which
parts are empty.

A virtual office is the kind of thing that easily becomes theatre, so the rule
here is strict: **every desk is backed by a real agent found in a transcript**,
and the vacancies are real too. Most wings are currently empty because that work
was done by the principal directly rather than delegated. A decorative office
would have put someone at every desk; this one shows the org as it actually is.

Which wing an agent sits in comes from its tool record rather than its declared
type — see :mod:`ajax_hq.behaviour`.

Rendered with Rich, which is already a dependency — no HTML, no browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.cells import cell_len
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ajax_hq.behaviour import wing_for as _wing_for
from ajax_hq.model import Agent, Division, Session, Snapshot, Status

GOLD = "#C8A951"
DIM = "#8A93A8"
FAINT = "#5C6580"

DESK_WIDTH = 34
NAMEPLATE_CHARS = 26

# Status → (glyph, colour). RUNNING gets a distinct glyph rather than an
# animation: it is derived from a transcript with no terminal record, and a
# static page should not imply motion it cannot observe.
LAMPS: dict[Status, tuple[str, str]] = {
    Status.COMPLETED: ("●", "green"),
    Status.ACTIVE: ("●", GOLD),
    Status.RUNNING: ("◐", GOLD),
    Status.UNKNOWN: ("○", "yellow3"),
    Status.IDLE: ("○", DIM),
}

WING_NAMES = {
    "EXO": ("Executive Office", "비서실"),
    "RND": ("Research & Development", "연구개발부"),
    "ENG": ("Engineering", "엔지니어링부"),
    "QA": ("Quality Assurance", "품질관리부"),
    "OPS": ("Operations", "운영부"),
    "AST": ("Asset Management", "자산운용부"),
}

WING_ORDER = ("EXO", "RND", "ENG", "QA", "OPS", "AST")

# For an empty wing, the division metric that explains who did the work instead.
VACANCY_METRIC = {
    "ENG": "Build calls",
    "QA": "Verification runs",
    "OPS": "Commits",
    "AST": "Agent runs",
    "EXO": "Work streams",
    "RND": "Agents dispatched",
}


@dataclass
class Desk:
    """One occupied workstation. Never constructed without a real occupant."""

    name: str
    kind: str
    status: Status
    elapsed: str
    tools: int
    output: str
    tag: str
    principal: bool = False

    @classmethod
    def from_agent(cls, agent: Agent) -> Desk:
        return cls(
            name=agent.title,
            kind=agent.agent_type or "agent",
            status=agent.status,
            elapsed=agent.duration_label,
            tools=agent.tools.total,
            # Measured text emitted — never the reported output_tokens, which is
            # a placeholder in subagent transcripts.
            output=f"{agent.output_label} out",
            tag=f"{agent.agent_id[:8]} · {len(agent.files_touched)} files",
        )

    @classmethod
    def from_session(cls, session: Session) -> Desk:
        return cls(
            name=session.title,
            kind="principal session",
            status=Status.ACTIVE,
            elapsed=session.duration_label,
            tools=session.tools.total,
            # Session token counts are sound, so they are shown as tokens.
            output=f"{session.total_tokens / 1000:.0f}k tok",
            tag=f"{session.session_id[:8]} · {len(session.files_touched)} files",
            principal=True,
        )

    def render(self) -> Panel:
        glyph, colour = LAMPS.get(self.status, LAMPS[Status.UNKNOWN])
        body = Text()
        body.append(f"{glyph} ", style=colour)
        body.append(_clip(self.name, NAMEPLATE_CHARS), style="bold")
        body.append(f"\n  {_clip(self.kind, NAMEPLATE_CHARS)}\n\n", style=FAINT)
        body.append(f"  {self.elapsed:<8}", style="white")
        body.append(f"{self.tools:>3} tools", style=DIM)
        body.append(f"{self.output:>11}", style=GOLD)
        body.append(f"\n  {self.tag}", style=FAINT)
        return Panel(
            body,
            width=DESK_WIDTH,
            border_style=GOLD if self.principal else "grey35",
            padding=(0, 1),
        )


@dataclass
class Wing:
    """A division's floor space. Empty is a valid, meaningful state."""

    code: str
    name: str
    korean: str
    desks: list[Desk] = field(default_factory=list)
    vacancy_reason: str | None = None

    @property
    def occupied(self) -> bool:
        return bool(self.desks)

    @property
    def occupancy_label(self) -> str:
        n = len(self.desks)
        return f"{n} desk{'s' if n != 1 else ''}"


def _clip(text: str, width: int) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def _vacancy_reason(code: str, divisions: list[Division], *, unattributed: int = 0) -> str:
    """Explain an empty wing using that division's real figures.

    An empty Engineering wing is the one vacancy that could be an artefact rather
    than a fact — it is the wing that depends on per-agent file attribution — so
    when ``unattributed`` dispatched agents have no transcript to attribute, the
    caveat is stated here instead of letting the empty wing read as settled.
    """
    division = next((d for d in divisions if d.code == code), None)
    if division is None or not division.metrics:
        reason = "No delegated work recorded."
    else:
        metrics = dict(division.metrics)
        key = VACANCY_METRIC.get(code)
        value = metrics.get(key) if key else None
        if value in (None, "0", "not distinguishable"):
            reason = "No delegated work recorded."
        else:
            reason = f"No delegated work — {value} {key.lower()} by the principal."

    if code == "ENG" and unattributed:
        reason += (
            f"\nCaveat: {unattributed} dispatched agent(s) have no transcript, so files they "
            f"built cannot be attributed. This wing may be empty by measurement rather than "
            f"in fact."
        )
    return reason


def assign(snapshot: Snapshot) -> list[Wing]:
    """Build the floor. Desk count always equals agent count plus sessions."""
    wings = {code: Wing(code, *WING_NAMES[code]) for code in WING_ORDER}

    for session in snapshot.sessions:
        wings["EXO"].desks.append(Desk.from_session(session))

    # Busiest first, so the most active agents read first in each wing.
    for agent in sorted(snapshot.agents, key=lambda a: (-a.tools.total, a.agent_id)):
        wings[_wing_for(agent)].desks.append(Desk.from_agent(agent))

    unattributed = len(snapshot.schema.agents_without_transcript)
    for code, wing in wings.items():
        if not wing.occupied:
            wing.vacancy_reason = _vacancy_reason(code, snapshot.divisions,
                                                  unattributed=unattributed)

    return [wings[code] for code in WING_ORDER]


def render(snapshot: Snapshot, console: Console | None = None) -> None:
    """Print the floor."""
    console = console or Console()
    wings = assign(snapshot)

    console.print()
    console.rule(Text("AJAX HQ  ·  사무실  ·  FLOOR", style=f"bold {GOLD}"), style=GOLD)
    console.print()

    if not snapshot.agents and not snapshot.sessions:
        console.print(
            Panel(
                Text(
                    "No agents or sessions found on this machine.\n"
                    "The floor fills in as work is delegated.",
                    style=FAINT,
                ),
                border_style="grey23",
                padding=(0, 1),
            )
        )
        console.print()
        return

    for wing in wings:
        _render_wing(wing, console)

    console.print(
        Text(
            "  One desk per agent found in transcripts. Vacant wings are real.",
            style=FAINT,
        )
    )
    console.print(
        Text(
            "  Wings are assigned from each agent's tool record, not its declared type.",
            style=FAINT,
        )
    )
    console.print(
        Text(
            "  Agent effort is measured text emitted; sessions show real tokens.",
            style=FAINT,
        )
    )
    console.print(
        Text(
            "  A desk's file count is that agent's own Write/Edit record, not its "
            "session's.",
            style=FAINT,
        )
    )

    for note in (snapshot.schema.token_note, snapshot.schema.attribution_note):
        if not note:
            continue
        console.print()
        lines = _wrap(note, max(30, console.width - 6))
        for index, line in enumerate(lines):
            prefix = "  ⚠ " if index == 0 else "    "
            console.print(Text(prefix + line, style="yellow3"))
    console.print()


def _render_wing(wing: Wing, console: Console) -> None:
    header = Text()
    header.append(f" {wing.code} ", style=f"bold black on {GOLD}")
    header.append(f"  {wing.name}  ", style="bold")
    header.append(wing.korean, style=FAINT)

    # cell_len, not len: Korean glyphs occupy two terminal columns each, so
    # character counting under-measures the header and the occupancy label
    # wraps onto its own line.
    pad = max(1, console.width - header.cell_len - cell_len(wing.occupancy_label) - 1)
    header.append(" " * pad)
    header.append(wing.occupancy_label, style=FAINT)
    console.print(header)

    if wing.occupied:
        console.print(Columns([d.render() for d in wing.desks], padding=(0, 1)))
    else:
        width = min(console.width - 2, 70)
        console.print(
            Panel(Text(wing.vacancy_reason or "", style=FAINT),
                  width=width, border_style="grey23", padding=(0, 1))
        )
    console.print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines
