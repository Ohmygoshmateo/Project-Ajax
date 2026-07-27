"""From a script to a schedule: when every beat is on screen, and for how long.

:mod:`ajax_studio.model` answers "how long is this beat". This module answers the
harder question the renderer actually asks: "what second does it start at". The
difference between those two is silence, and silence is most of the reason
episode 1's five minutes of words is meant to be an eight minute episode.

Three numbers are kept apart on purpose and never summed into one figure:

* **narration** — the beats, back to back, nothing between them.
* **gaps** — breath, room tone, and clock cards *between* beats.
* **distance to target** — how far the total sits outside 8–12 minutes.

Collapsing those would let a schedule pass the runtime target on padding alone,
which is how you end up with an eight minute video containing five minutes of
episode. The caller gets all three and decides.

Provenance survives the same way. A cue whose length came from word-count
arithmetic and one whose length came from a real voice track are both floats, so
every cue carries its :class:`~ajax_studio.model.Source` and the timeline counts
them. Nothing here is labelled a runtime without also saying where it came from.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ajax_studio.model import (
    ACT_ORDER,
    RUNTIME_CEILING_S,
    RUNTIME_FLOOR_S,
    WORDS_PER_SECOND,
    Act,
    Beat,
    Episode,
    Source,
)

# --- the gap rule -----------------------------------------------------------
#
# Rowan's read is slow and full of stops, and the edit is slower still. Between
# any two beats there is at minimum a breath and a shot change, which is roughly
# a second and a bit of real time — hence 1.2s as the floor.
#
# Two things stretch it, both for reasons that come from the bible rather than
# from a need to hit a runtime:
#
#   * A beat at tension 5 is a line the episode is built around ("for about
#     eleven seconds I was the only person in that room who knew"). Cutting off
#     that line's air throws the landing away, so the gap after it doubles. Note
#     this keys off the beat that just *ended* — the pause belongs to the line
#     that earned it, not to whatever follows.
#   * A clock card is not a pause, it is a picture: `02:14` in the corner,
#     holding the passage of the shift. It occupies its own second of screen
#     time, added to the gap ahead of the beat it introduces.
#
# The last cue gets no gap; the episode ends when Rowan stops talking. The
# opening clock card is treated as an overlay on the first shot rather than a
# lead-in, so the timeline still starts at 0.0 and every start time is an offset
# a renderer can seek to directly.
DEFAULT_GAP = 1.2
HOLD_MULTIPLIER = 2.0
CLOCK_CARD_S = 1.0

# Tension reading that earns the held pause. Not a magic number — the bible's
# 1–5 scale tops out at 5 and reserves it for the crisis and its echo.
HOLD_TENSION = 5


@dataclass(frozen=True)
class Cue:
    """One beat, placed. ``start`` and ``end`` are seconds from the first frame."""

    beat: Beat
    start: float
    duration: float
    source: Source
    gap_after: float
    clock_card: bool

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def beat_id(self) -> str:
        return self.beat.beat_id

    @property
    def act(self) -> Act:
        return self.beat.act

    @property
    def is_measured(self) -> bool:
        return self.source is Source.MEASURED

    @property
    def duration_label(self) -> str:
        """Never a bare number — the provenance travels with the figure."""
        return f"{self.duration:.1f}s {self.source.value}"


@dataclass(frozen=True)
class RuntimeReport:
    """The three runtimes, separately, plus the distance to the target."""

    narration_seconds: float
    gap_seconds: float

    @property
    def total_seconds(self) -> float:
        return self.narration_seconds + self.gap_seconds

    @property
    def within_target(self) -> bool:
        return RUNTIME_FLOOR_S <= self.total_seconds <= RUNTIME_CEILING_S

    @property
    def delta_to_target_s(self) -> float:
        """Signed distance to the nearest bound: negative short, positive long, 0 inside."""
        if self.total_seconds < RUNTIME_FLOOR_S:
            return self.total_seconds - RUNTIME_FLOOR_S
        if self.total_seconds > RUNTIME_CEILING_S:
            return self.total_seconds - RUNTIME_CEILING_S
        return 0.0

    @property
    def gap_share(self) -> float:
        """Fraction of the total that is silence. A sanity check on padding."""
        return self.gap_seconds / self.total_seconds if self.total_seconds else 0.0

    @property
    def verdict(self) -> str:
        delta = self.delta_to_target_s
        if delta == 0.0:
            return f"inside the {mmss(RUNTIME_FLOOR_S)}–{mmss(RUNTIME_CEILING_S)} target"
        if delta < 0:
            return f"{mmss(-delta)} short of the {mmss(RUNTIME_FLOOR_S)} floor"
        return f"{mmss(delta)} over the {mmss(RUNTIME_CEILING_S)} ceiling"


@dataclass(frozen=True)
class Timeline:
    """A whole episode, scheduled. Immutable: rebuild it, do not edit it."""

    episode_number: int
    episode_title: str
    cues: tuple[Cue, ...]
    gap: float

    def __iter__(self) -> Iterator[Cue]:
        return iter(self.cues)

    def __len__(self) -> int:
        return len(self.cues)

    # -- runtime ------------------------------------------------------------

    @property
    def narration_seconds(self) -> float:
        return sum(cue.duration for cue in self.cues)

    @property
    def gap_seconds(self) -> float:
        return sum(cue.gap_after for cue in self.cues)

    @property
    def total_seconds(self) -> float:
        """Where the last cue's gap has already been zeroed, this is simply the end."""
        return self.cues[-1].end if self.cues else 0.0

    def runtime(self) -> RuntimeReport:
        return RuntimeReport(
            narration_seconds=self.narration_seconds,
            gap_seconds=self.gap_seconds,
        )

    # -- provenance ---------------------------------------------------------

    @property
    def source_counts(self) -> dict[Source, int]:
        """Every :class:`Source`, including the zeroes — an absent kind is information."""
        counts = {source: 0 for source in Source}
        for cue in self.cues:
            counts[cue.source] += 1
        return counts

    @property
    def derived_cues(self) -> tuple[Cue, ...]:
        return tuple(c for c in self.cues if c.source is Source.DERIVED)

    @property
    def measured_cues(self) -> tuple[Cue, ...]:
        return tuple(c for c in self.cues if c.source is Source.MEASURED)

    @property
    def authored_cues(self) -> tuple[Cue, ...]:
        return tuple(c for c in self.cues if c.source is Source.AUTHORED)

    @property
    def is_fully_measured(self) -> bool:
        """True only when no cue length is an estimate. One derived cue spoils it."""
        return bool(self.cues) and all(cue.is_measured for cue in self.cues)

    @property
    def provenance_label(self) -> str:
        counts = self.source_counts
        parts = [f"{counts[s]} {s.value}" for s in Source if counts[s]]
        return ", ".join(parts) if parts else "no cues"

    @property
    def provenance_caveat(self) -> str | None:
        """The sentence to print next to any figure this schedule produces."""
        derived = len(self.derived_cues)
        if not derived:
            return None
        return (
            f"{derived} of {len(self.cues)} cue lengths are derived from word count at "
            f"{WORDS_PER_SECOND} words/second — not measured from audio."
        )

    # -- slices -------------------------------------------------------------

    def for_act(self, act: Act) -> tuple[Cue, ...]:
        return tuple(cue for cue in self.cues if cue.act is act)

    def act_span(self, act: Act) -> tuple[float, float] | None:
        """First start to last end for an act, gaps inside it included.

        The bible's per-act runtimes are on-screen figures, so an act's length is
        the window it occupies, not the sum of its narration.
        """
        cues = self.for_act(act)
        if not cues:
            return None
        return (cues[0].start, cues[-1].end)

    def act_seconds(self, act: Act) -> float:
        span = self.act_span(act)
        return span[1] - span[0] if span else 0.0

    def clock_cards(self) -> tuple[Cue, ...]:
        return tuple(cue for cue in self.cues if cue.clock_card)


def _shows_clock_card(beat: Beat, previous: Beat | None) -> bool:
    """Whether a card carrying the in-world time sits ahead of this beat.

    The writer's ``caption`` is the intent when it repeats the beat's clock —
    that is a person deciding this is where the shift visibly moves. Beyond
    that, only the opening beat gets one automatically, because the audience has
    to be told what time it is once before a later card means anything.
    """
    if not beat.clock:
        return False
    if beat.caption and beat.caption.strip() == beat.clock.strip():
        return True
    return previous is None


def _gap_after(beat: Beat, following: Beat | None, gap: float, card_ahead: bool) -> float:
    """The silence owed after ``beat``. See the gap rule at the top of the module."""
    if following is None:
        return 0.0
    seconds = gap * (HOLD_MULTIPLIER if beat.tension >= HOLD_TENSION else 1.0)
    if card_ahead:
        seconds += CLOCK_CARD_S
    return seconds


def build(
    episode: Episode,
    voice_durations: Mapping[str, float] | None = None,
    gap: float = DEFAULT_GAP,
) -> Timeline:
    """Schedule an episode.

    ``voice_durations`` maps ``beat_id`` to seconds measured from a real voice
    track and takes precedence over anything the script says. An id in that
    mapping that matches no beat is an error rather than a silent no-op: it means
    the audio and the script have drifted, and a schedule built from the wrong
    recording looks exactly like a correct one.
    """
    if gap < 0:
        raise ValueError(f"gap must not be negative, got {gap}")

    measured = dict(voice_durations or {})
    unknown = sorted(set(measured) - {beat.beat_id for beat in episode.beats})
    if unknown:
        raise ValueError(
            f"voice_durations has no matching beat for: {', '.join(unknown)}"
        )

    cues: list[Cue] = []
    cursor = 0.0
    for index, beat in enumerate(episode.beats):
        previous = episode.beats[index - 1] if index else None
        following = episode.beats[index + 1] if index + 1 < len(episode.beats) else None

        duration, source = beat.duration(measured.get(beat.beat_id))
        card_ahead = _shows_clock_card(following, beat) if following else False
        gap_after = _gap_after(beat, following, gap, card_ahead)

        cues.append(
            Cue(
                beat=beat,
                start=cursor,
                duration=duration,
                source=source,
                gap_after=gap_after,
                clock_card=_shows_clock_card(beat, previous),
            )
        )
        cursor += duration + gap_after

    return Timeline(
        episode_number=episode.number,
        episode_title=episode.title,
        cues=tuple(cues),
        gap=gap,
    )


def mmss(seconds: float) -> str:
    """``M:SS``. Used for runtimes, where minutes are the unit people think in."""
    total = int(round(abs(seconds)))
    sign = "-" if seconds < 0 else ""
    return f"{sign}{total // 60}:{total % 60:02d}"


GOLD = "#C8A951"
DIM = "#8A93A8"
FAINT = "#5C6580"

# Derived figures are dimmed and measured ones are not, so the eye can tell an
# estimated schedule from a real one without reading the source column.
SOURCE_STYLE = {
    Source.MEASURED: "green",
    Source.AUTHORED: GOLD,
    Source.DERIVED: DIM,
}


def render(timeline: Timeline, console: Console | None = None) -> None:
    """Print the schedule, then the three runtimes, then the provenance."""
    console = console or Console()

    title = Text()
    title.append(f"EP{timeline.episode_number:02d}  ", style=f"bold {GOLD}")
    title.append(timeline.episode_title, style="bold")
    console.print()
    console.print(title)
    console.print()

    table = Table(box=None, pad_edge=False, header_style=FAINT)
    table.add_column("start", justify="right", style=DIM)
    table.add_column("beat")
    table.add_column("act", style=FAINT)
    table.add_column("len", justify="right")
    table.add_column("from", style=FAINT)
    table.add_column("gap", justify="right", style=FAINT)
    table.add_column("card", justify="center", style=FAINT)

    for cue in timeline:
        table.add_row(
            f"{cue.start:7.1f}",
            cue.beat_id,
            cue.act.value,
            Text(f"{cue.duration:5.1f}s", style=SOURCE_STYLE[cue.source]),
            cue.source.value,
            f"{cue.gap_after:4.1f}s" if cue.gap_after else "—",
            f"[{cue.beat.clock}]" if cue.clock_card else "",
        )
    console.print(table)

    report = timeline.runtime()
    console.print()
    console.print(Text(f"  narration          {mmss(report.narration_seconds)}", style="white"))
    console.print(
        Text(
            f"  gaps               {mmss(report.gap_seconds)}"
            f"   ({timeline.gap:.1f}s base, {HOLD_MULTIPLIER:g}× after tension"
            f" {HOLD_TENSION}, +{CLOCK_CARD_S:g}s per clock card)",
            style=FAINT,
        )
    )
    console.print(
        Text(
            f"  total              {mmss(report.total_seconds)}",
            style="bold " + ("green" if report.within_target else "yellow3"),
        )
    )
    console.print(
        Text(
            f"  target             {report.verdict}",
            style="green" if report.within_target else "yellow3",
        )
    )

    console.print()
    counts = timeline.source_counts
    console.print(
        Text(
            f"  {len(timeline)} cues — {timeline.provenance_label}"
            f"; {len(timeline.clock_cards())} clock cards",
            style=FAINT,
        )
    )
    for act in ACT_ORDER:
        if timeline.for_act(act):
            console.print(
                Text(f"  {act.value:<10} {mmss(timeline.act_seconds(act))}", style=FAINT)
            )

    caveat = timeline.provenance_caveat
    if caveat:
        console.print()
        console.print(Text(f"  ⚠ {caveat}", style="yellow3"))
    elif counts[Source.MEASURED] == len(timeline) and timeline.cues:
        console.print()
        console.print(Text("  ✓ every cue length measured from audio.", style="green"))
    console.print()
