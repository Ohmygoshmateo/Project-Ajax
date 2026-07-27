"""Domain model for Ajax HQ.

Everything here describes work that actually happened. There is no field for
simulated state, and nothing is populated by default — an absent value means the
underlying record did not contain it, and the UI says so rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

SCHEMA_VERSION = 1


class Status(str, Enum):
    """Derived from real timestamps and recorded failures — never assigned."""

    NEVER_ACTIVE = "never_active"
    ACTIVE = "active"
    IDLE = "idle"
    DEGRADED = "degraded"
    RUNNING = "running"
    COMPLETED = "completed"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").upper()


class Provenance(str, Enum):
    """Whether a record was read from this container or restored from history."""

    LIVE = "live"
    RESTORED = "restored"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m {seconds % 60:.0f}s"
    return f"{seconds / 3600:.1f}h"


@dataclass
class ToolUsage:
    """Tool call counts, the closest thing to a measure of effort."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, name: str | None) -> None:
        if name:
            self.counts[name] = self.counts.get(name, 0) + 1

    def merge(self, other: ToolUsage) -> None:
        for name, count in other.counts.items():
            self.counts[name] = self.counts.get(name, 0) + count

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def top(self, n: int = 4) -> list[tuple[str, int]]:
        return sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def summary(self) -> str:
        return ", ".join(f"{name} {count}" for name, count in self.top()) or "none"


@dataclass
class Agent:
    """One dispatched subagent, reconstructed from its own transcript.

    ``description`` and ``agent_type`` come from the dispatching session's Agent
    tool call; everything else comes from the subagent's transcript. Either half
    can be missing if a record could not be parsed, which is why both are
    optional rather than defaulted to something plausible.
    """

    agent_id: str
    description: str | None = None
    agent_type: str | None = None
    session_id: str | None = None
    started: datetime | None = None
    ended: datetime | None = None
    status: Status = Status.UNKNOWN
    tools: ToolUsage = field(default_factory=ToolUsage)
    input_tokens: int = 0
    output_tokens: int = 0  # UNRELIABLE for subagents — see output_chars
    cache_tokens: int = 0
    output_chars: int = 0
    files_touched: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    # Counts derived from commands_run at parse time. The commands themselves
    # are never archived — they can carry credentials as arguments — so these
    # integers are what survives into a snapshot, and they are what the floor
    # uses to seat an agent in Quality Assurance or Operations.
    verify_runs: int = 0
    ship_actions: int = 0
    models: list[str] = field(default_factory=list)
    record_count: int = 0
    prompt: str | None = None  # local page only; never enters a snapshot
    report: str | None = None  # local page only; never enters a snapshot
    provenance: Provenance = Provenance.LIVE

    @property
    def duration_seconds(self) -> float | None:
        if self.started and self.ended:
            return max((self.ended - self.started).total_seconds(), 0.0)
        return None

    @property
    def duration_label(self) -> str:
        return _fmt_duration(self.duration_seconds)

    @property
    def title(self) -> str:
        return self.description or f"agent {self.agent_id[:8]}"

    @property
    def total_tokens(self) -> int:
        """Fresh tokens only. Cache reads are counted separately in
        ``cache_tokens`` — folding them in here would inflate the figure by
        orders of magnitude and read as usage rather than context reuse.

        **Not displayed for agents.** ``output_tokens`` is unusable in subagent
        transcripts (see :attr:`output_chars`), which makes this sum meaningless
        at agent level — it reduces to ``input_tokens``. Kept for completeness
        and for the schema-health check; the UI shows :attr:`output_label`.
        """
        return self.input_tokens + self.output_tokens

    @property
    def output_tokens_are_plausible(self) -> bool:
        """Does the reported output count agree with the text actually emitted?

        Roughly four characters per token, so a healthy ratio is ~0.25. Observed
        subagent transcripts report 0.001-0.06 — one to two orders of magnitude
        low.

        The 0.10 threshold is deliberately generous in one direction only: an
        agent that emits many tool calls has *more* output tokens per character
        of text (tool JSON is output but is not text), so a heavy tool user
        skews this ratio **up**. A low ratio therefore always means the count is
        under-reported, never that the agent was merely tool-heavy.
        """
        if self.output_chars < 500:
            return True  # too small to judge either way
        return (self.output_tokens / self.output_chars) >= 0.10

    @property
    def output_label(self) -> str:
        """Measured output size, the figure the UI shows instead of tokens."""
        if self.output_chars >= 1000:
            return f"{self.output_chars / 1000:.1f}k"
        return str(self.output_chars)


@dataclass
class Session:
    """A work stream — one Claude Code session."""

    session_id: str
    name: str | None = None
    cwd: str | None = None
    branch: str | None = None
    started: datetime | None = None
    ended: datetime | None = None
    tools: ToolUsage = field(default_factory=ToolUsage)
    models: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    files_touched: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    agent_ids: list[str] = field(default_factory=list)
    decisions: int = 0  # AskUserQuestion / ExitPlanMode events
    user_turns: int = 0
    record_count: int = 0
    unparsed_records: int = 0
    client_version: str | None = None
    entrypoint: str | None = None
    provenance: Provenance = Provenance.LIVE

    @property
    def duration_seconds(self) -> float | None:
        if self.started and self.ended:
            return max((self.ended - self.started).total_seconds(), 0.0)
        return None

    @property
    def duration_label(self) -> str:
        return _fmt_duration(self.duration_seconds)

    @property
    def title(self) -> str:
        return self.name or self.session_id[:8]

    @property
    def total_tokens(self) -> int:
        """Fresh tokens only. Cache reads are counted separately in
        ``cache_tokens`` — folding them in here would inflate the figure by
        orders of magnitude and read as usage rather than context reuse."""
        return self.input_tokens + self.output_tokens


@dataclass
class BuiltFile:
    """A file an agent wrote or edited, derived from tool inputs.

    Derived, not observed: this counts Write/Edit *calls*, so it reflects intent
    to change rather than a filesystem diff. The UI labels it accordingly.
    """

    path: str
    project: str | None = None
    writes: int = 0
    edits: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    # Subagents whose own transcript shows a build call on this path. Identifiers
    # only, so the list is safe to archive. An empty list is a real answer: every
    # recorded touch came from a principal session directly.
    agent_ids: list[str] = field(default_factory=list)

    @property
    def touches(self) -> int:
        return self.writes + self.edits

    @property
    def delegated(self) -> bool:
        """Was any recorded touch made by a subagent rather than the principal?"""
        return bool(self.agent_ids)


@dataclass
class Commit:
    sha: str
    subject: str = ""
    author: str = ""
    timestamp: datetime | None = None
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    branch: str | None = None

    @property
    def short_sha(self) -> str:
        return self.sha[:8]

    @property
    def churn(self) -> int:
        return self.insertions + self.deletions


@dataclass
class Project:
    name: str
    path: str
    language: str | None = None
    loc: int = 0
    source_files: int = 0
    test_files: int = 0
    test_count: int = 0
    branch: str | None = None
    last_commit: datetime | None = None
    dirty: bool = False
    extras: dict[str, str] = field(default_factory=dict)


@dataclass
class Plan:
    name: str
    path: str
    modified: datetime | None = None
    heading: str | None = None
    words: int = 0


@dataclass
class Division:
    """A functional grouping whose figures come from real activity."""

    code: str
    name: str
    korean: str
    mandate: str
    status: Status = Status.NEVER_ACTIVE
    last_active: datetime | None = None
    metrics: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def headcount_label(self) -> str:
        return self.metrics[0][1] if self.metrics else "—"


@dataclass
class SourceRef:
    """Provenance for a panel: where the data came from and how fresh it is."""

    label: str
    path: str
    exists: bool = True
    modified: datetime | None = None

    def age_label(self, now: datetime | None = None) -> str:
        if not self.modified:
            return "unknown"
        delta = (now or datetime.now(self.modified.tzinfo)) - self.modified
        return _humanize(delta)


def _humanize(delta: timedelta) -> str:
    seconds = delta.total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 172_800:
        return f"{seconds / 3600:.0f}h ago"
    return f"{seconds / 86400:.0f}d ago"


@dataclass
class SchemaHealth:
    """Surfaced, not hidden — these are undocumented internals that can drift."""

    client_versions: list[str] = field(default_factory=list)
    files_read: int = 0
    records_read: int = 0
    records_unparsed: int = 0
    unknown_record_types: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Agents whose reported output_tokens contradicts the text they emitted.
    implausible_output_tokens: list[str] = field(default_factory=list)
    # Agents a session dispatched but which have no transcript on disk. They are
    # the one case where per-agent file attribution genuinely cannot be derived,
    # so they are named rather than left to read as agents that built nothing.
    agents_without_transcript: list[str] = field(default_factory=list)

    @property
    def token_note(self) -> str | None:
        """Standing note about the unusable subagent output-token field.

        Stated once rather than flagged per row: every subagent transcript
        observed so far reports a placeholder value, so a per-agent warning
        would mark every line and tell the reader nothing.
        """
        if not self.implausible_output_tokens:
            return None
        return (
            f"Reported output tokens are unusable for {len(self.implausible_output_tokens)} "
            f"subagent(s) — the field reports a placeholder value regardless of response "
            f"size. Agent effort is shown as measured text emitted instead."
        )

    @property
    def attribution_note(self) -> str | None:
        """Standing note on the limits of per-agent file attribution.

        Attribution is sound wherever a subagent transcript exists: each one
        records the agent's own ``Write``/``Edit`` calls with the file path in the
        tool input, and those records are disjoint from the dispatching session's,
        so a delegated build is credited to the agent that made it without being
        double-counted. A dispatch with no transcript on disk is the sole
        exception, and it is disclosed because the alternative reading — an agent
        that touched no files — is a claim the records do not support.
        """
        if not self.agents_without_transcript:
            return None
        return (
            f"Per-agent file attribution is unavailable for "
            f"{len(self.agents_without_transcript)} dispatched agent(s) with no transcript on "
            f"disk. Anything they built cannot be credited to them, so their file counts read "
            f"zero where the honest value is unknown."
        )

    @property
    def healthy(self) -> bool:
        return self.records_unparsed == 0 and not self.warnings

    @property
    def summary(self) -> str:
        if self.records_read == 0:
            return "no transcript records found"
        if self.healthy:
            return f"{self.records_read:,} records parsed cleanly"
        return f"{self.records_unparsed} of {self.records_read:,} records unreadable"


@dataclass
class Snapshot:
    """Everything HQ knows, at one moment."""

    generated_at: datetime
    agents: list[Agent] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    files: list[BuiltFile] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    plans: list[Plan] = field(default_factory=list)
    divisions: list[Division] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    schema: SchemaHealth = field(default_factory=SchemaHealth)
    restored_sessions: int = 0
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- aggregates

    @property
    def total_tokens(self) -> int:
        return sum(s.total_tokens for s in self.sessions)

    @property
    def total_cache_tokens(self) -> int:
        return sum(s.cache_tokens for s in self.sessions)

    @property
    def total_tool_calls(self) -> int:
        total = ToolUsage()
        for session in self.sessions:
            total.merge(session.tools)
        for agent in self.agents:
            total.merge(agent.tools)
        return total.total

    @property
    def span(self) -> tuple[datetime | None, datetime | None]:
        stamps = [s.started for s in self.sessions if s.started]
        ends = [s.ended for s in self.sessions if s.ended]
        return (min(stamps) if stamps else None, max(ends) if ends else None)

    @property
    def span_label(self) -> str:
        start, end = self.span
        if not start or not end:
            return "—"
        return _fmt_duration((end - start).total_seconds())

    @property
    def is_empty(self) -> bool:
        return not (self.agents or self.sessions or self.commits)
