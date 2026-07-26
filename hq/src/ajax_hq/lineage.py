"""Reporting lines — who dispatched whom.

The roster says what each agent did and the floor says which division it sat in.
Neither says who it answered to, yet that structure is already in the data: a
subagent's transcript records the session that dispatched it, so the org chart
can be read rather than designed.

The single rule this module exists to enforce is that **a parent is never
invented**. An org chart is exactly the kind of artefact that looks more
authoritative than its inputs — a tidy tree implies someone verified every edge —
so the two failure modes are treated as bugs of different severity:

* Attaching an agent to a session that did not dispatch it is a *lie*. There is
  no timestamp heuristic here, and no "there is only one session, so it must be
  that one" shortcut. An agent whose ``session_id`` is missing, or names a
  session absent from the snapshot, is grouped under :data:`UNATTRIBUTED` with
  the reason printed next to it.
* Dropping an agent because it has no parent is *worse*. The unattributed group
  exists so that the structural gap costs a line in the tree, never a record.
  ``len(org.reports) == len(snapshot.agents)`` is invariant and asserted.

Attribution reads ``Agent.session_id`` and nothing else. ``Session.agent_ids`` is
a second witness — the dispatching side of the same event — but it is populated
from Agent tool calls whose ``agentId`` came back in a tool result, so it names
agents that may have no transcript at all. Reconciling the two would mean
deciding which side wins when they disagree, and that decision is exactly the
kind of quiet guess this module refuses to make. The disagreement is reported
instead: see :attr:`Org.dispatched_without_transcript`.

Rendered with Rich, matching :mod:`ajax_hq.floor`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree

from ajax_hq.behaviour import wing_for
from ajax_hq.model import Agent, Provenance, Session, Snapshot

GOLD = "#C8A951"
DIM = "#8A93A8"
FAINT = "#5C6580"

UNATTRIBUTED = "Unattributed"

# Why an agent could not be placed under a session. Both are statements about
# the record, not about the agent — the work happened either way.
NO_SESSION_RECORDED = "no dispatching session recorded in its transcript"
SESSION_NOT_IN_SNAPSHOT = "dispatching session {session_id} is not in this snapshot"

TITLE_CHARS = 46


@dataclass
class Report:
    """One agent under whoever dispatched it — or under nobody, with a reason.

    ``reason`` is set only for unattributed reports and is printed verbatim, so
    a reader can tell a missing record apart from a session that fell outside
    the snapshot without going back to the transcripts.
    """

    agent: Agent
    reason: str | None = None

    @property
    def wing(self) -> str:
        """The division this agent's tool record puts it in."""
        return wing_for(self.agent)

    @property
    def restored(self) -> bool:
        return self.agent.provenance is Provenance.RESTORED


@dataclass
class Line:
    """A session and the agents it dispatched. No reports is a real answer.

    A session that delegated nothing is kept in the tree rather than filtered
    out: "this work stream did everything itself" is a finding about how the
    org actually operates, and omitting it would make delegation look universal.
    """

    session: Session
    reports: list[Report] = field(default_factory=list)

    @property
    def restored(self) -> bool:
        return self.session.provenance is Provenance.RESTORED

    @property
    def headcount_label(self) -> str:
        n = len(self.reports)
        return f"{n} report{'s' if n != 1 else ''}"


@dataclass
class Org:
    """The reporting structure of one snapshot."""

    lines: list[Line] = field(default_factory=list)
    unattributed: list[Report] = field(default_factory=list)
    # Agent ids a session claims to have dispatched but which are not in the
    # snapshot's agent list at all. Carried so the disagreement between the two
    # witnesses is visible rather than silently resolved.
    dispatched_without_transcript: list[str] = field(default_factory=list)

    @property
    def reports(self) -> list[Report]:
        """Every agent, once. The invariant the tests exist to protect."""
        return [report for line in self.lines for report in line.reports] + self.unattributed

    @property
    def agent_count(self) -> int:
        return len(self.reports)

    @property
    def restored_count(self) -> int:
        return sum(1 for report in self.reports if report.restored) + sum(
            1 for line in self.lines if line.restored
        )

    @property
    def is_empty(self) -> bool:
        return not (self.lines or self.unattributed)


def build(snapshot: Snapshot) -> Org:
    """Read the reporting lines out of a snapshot.

    Session order is preserved from the snapshot, and so is agent order within a
    session — the collector already emits agents oldest-first, which makes each
    line read as the dispatch sequence it was.
    """
    org = Org(lines=[Line(session=session) for session in snapshot.sessions])

    # First occurrence wins if a snapshot ever carries two Session objects with
    # the same id (live plus restored, say). Every session still gets a line;
    # only one of them can receive the reports, which keeps the agent total
    # exact instead of double-counting the overlap.
    by_id: dict[str, Line] = {}
    for line in org.lines:
        by_id.setdefault(line.session.session_id, line)

    for agent in snapshot.agents:
        session_id = agent.session_id
        if not session_id:
            org.unattributed.append(Report(agent=agent, reason=NO_SESSION_RECORDED))
            continue
        line = by_id.get(session_id)
        if line is None:
            org.unattributed.append(
                Report(agent=agent,
                       reason=SESSION_NOT_IN_SNAPSHOT.format(session_id=session_id[:8]))
            )
            continue
        line.reports.append(Report(agent=agent))

    known = {agent.agent_id for agent in snapshot.agents}
    for session in snapshot.sessions:
        org.dispatched_without_transcript.extend(
            agent_id for agent_id in session.agent_ids if agent_id not in known
        )

    return org


def _clip(text: str, width: int) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def _session_label(line: Line) -> Text:
    session = line.session
    label = Text()
    label.append(_clip(session.title, TITLE_CHARS), style=f"bold {GOLD}")
    label.append(f"  {session.session_id[:8]}", style=FAINT)
    if session.branch:
        label.append(f"  {_clip(session.branch, 30)}", style=FAINT)
    label.append(f"  ·  {line.headcount_label}", style=DIM)
    if line.restored:
        label.append("  ARCHIVAL", style="yellow3")
    return label


def _report_label(report: Report) -> Text:
    agent = report.agent
    label = Text()
    label.append(_clip(agent.title, TITLE_CHARS), style="white")
    label.append(f"  [{report.wing}]", style=GOLD)
    label.append(f"  {agent.duration_label}", style=DIM)
    label.append(f"  {agent.tools.total} tools", style=DIM)
    # Measured text emitted, never the reported output_tokens: that field is a
    # placeholder in subagent transcripts (Agent.output_tokens_are_plausible).
    label.append(f"  {agent.output_label} out", style=GOLD)
    if report.restored:
        label.append("  ARCHIVAL", style="yellow3")
    if report.reason:
        label.append(f"\n{report.reason}", style="yellow3")
    return label


def render(org: Org, console: Console | None = None) -> None:
    """Print the reporting lines."""
    console = console or Console()

    console.print()
    console.rule(Text("AJAX HQ  ·  보고 계통  ·  LINEAGE", style=f"bold {GOLD}"), style=GOLD)
    console.print()

    if org.is_empty:
        # An empty root node would still draw a tree and read as "the org is
        # this one box", which is a claim the snapshot does not support.
        console.print(
            Panel(
                Text(
                    "No sessions or agents found on this machine.\n"
                    "Reporting lines appear once work is delegated.",
                    style=FAINT,
                ),
                border_style="grey23",
                padding=(0, 1),
            )
        )
        console.print()
        return

    tree = Tree(Text("AJAX HQ", style=f"bold {GOLD}"), guide_style="grey35")

    for line in org.lines:
        node = tree.add(_session_label(line))
        if not line.reports:
            node.add(Text("no agents dispatched — this stream did the work itself",
                          style=FAINT))
            continue
        for report in line.reports:
            node.add(_report_label(report))

    if org.unattributed:
        group = tree.add(
            Text(UNATTRIBUTED, style="bold yellow3")
            + Text(f"  ·  {len(org.unattributed)} agent"
                   f"{'s' if len(org.unattributed) != 1 else ''}", style=DIM)
        )
        for report in org.unattributed:
            group.add(_report_label(report))

    console.print(tree)
    console.print()

    console.print(
        Text(
            f"  {org.agent_count} agent(s) shown, one line each — the same count the "
            f"snapshot holds.",
            style=FAINT,
        )
    )
    console.print(
        Text(
            "  Reporting lines come from each subagent's own record of its caller.",
            style=FAINT,
        )
    )
    console.print(
        Text(
            "  Agents with no usable caller are listed unattributed, never reassigned.",
            style=FAINT,
        )
    )
    console.print(
        Text(
            "  Division codes are derived from tool records; effort is measured text.",
            style=FAINT,
        )
    )
    if org.restored_count:
        console.print(
            Text(
                f"  ARCHIVAL marks {org.restored_count} entr"
                f"{'ies' if org.restored_count != 1 else 'y'} restored from committed "
                f"history, not read from this machine.",
                style="yellow3",
            )
        )
    if org.dispatched_without_transcript:
        console.print(
            Text(
                f"  ⚠ {len(org.dispatched_without_transcript)} agent(s) named by a session "
                f"have no record in this snapshot and are not shown.",
                style="yellow3",
            )
        )
    console.print()


__all__ = ["Line", "Org", "Report", "UNATTRIBUTED", "build", "render"]
