"""How the organisation changes over time.

The rest of HQ answers *what is true now*. This module answers *what changed*,
by reading the committed snapshots in ``hq/data/history/`` as a series instead of
as a pile of records to merge back into the present.

Turning an archive into a trend is where an honest dashboard most easily becomes
a dishonest one, so the rules are enforced here rather than left to the reader:

* **Two points are not a trend.** With one capture there is no line, no
  direction and no sparkline — only the figures and a sentence saying why. A
  rising sparkline drawn from a single data point is a lie, and it is the
  easiest lie in this codebase to tell by accident.
* **The x-axis is not time.** A snapshot is written when someone runs
  ``ajax-hq snapshot``, not on a schedule, so captures are irregularly spaced.
  A row of block characters can only space them evenly, so the table prints the
  real interval beside each capture and the output says so out loud. Nothing is
  interpolated to smooth a gap, and no capture is invented to fill one.
* **The raw figures are cumulative.** Each snapshot lists everything known at
  capture time, so 12 agents after 9 is three new agents, not twelve. Deltas are
  labelled as deltas and can never render negative — see :func:`_delta`.

A payload whose schema version does not match is skipped and counted, never
coerced, matching :func:`ajax_hq.snapshot._load_payloads`. Snapshots are
metadata-only by design; nothing here reads or needs prompt or response text.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ajax_hq.model import SCHEMA_VERSION, Snapshot
from ajax_hq.snapshot import DEFAULT_HISTORY_DIR, to_payload
from ajax_hq.timeutil import aware, now

GOLD = "#C8A951"
DIM = "#8A93A8"
FAINT = "#5C6580"

BLOCKS = "▁▂▃▄▅▆▇█"

LIVE_SOURCE = "live (uncommitted)"

# The series, in table order. Every one of these is a count of records present
# at capture time, which is what makes the whole table cumulative.
METRICS: tuple[tuple[str, str], ...] = (
    ("agents", "Agents"),
    ("files", "Files"),
    ("commits", "Commits"),
    ("sessions", "Sessions"),
    ("session_tokens", "Tokens"),
)


# --------------------------------------------------------------------- model


@dataclass(frozen=True)
class Capture:
    """One snapshot, reduced to the numbers that can be compared across time.

    All five figures are *cumulative*: a snapshot lists everything the machine
    knew when it was written, not the work done since the last one.
    """

    captured_at: datetime
    source: str
    agents: int = 0
    files: int = 0
    commits: int = 0
    sessions: int = 0
    session_tokens: int = 0
    live: bool = False

    @property
    def label(self) -> str:
        """Date and time. Two captures can share a date, so the clock stays."""
        return self.captured_at.strftime("%Y-%m-%d %H:%M")

    def value(self, metric: str) -> int:
        return int(getattr(self, metric))


@dataclass
class Trends:
    """A time series of captures, plus an account of what was left out."""

    captures: list[Capture] = field(default_factory=list)
    history_dir: Path | None = None
    skipped_schema: int = 0
    skipped_unreadable: int = 0
    skipped_undated: int = 0

    @property
    def enough(self) -> bool:
        """Whether a direction may be stated at all. Nothing else may override."""
        return len(self.captures) >= 2

    @property
    def skipped(self) -> int:
        return self.skipped_schema + self.skipped_unreadable + self.skipped_undated

    def values(self, metric: str) -> list[int]:
        return [c.value(metric) for c in self.captures]

    def deltas(self, metric: str) -> list[int | None]:
        """New records per capture. ``None`` for the first — it has no predecessor.

        A first capture has nothing to be new against, and reporting its whole
        cumulative figure as "new" would attribute every agent ever run to the
        moment someone first ran the command.
        """
        out: list[int | None] = [None]
        values = self.values(metric)
        for previous, current in zip(values, values[1:], strict=False):
            out.append(_delta(previous, current)[0])
        return out

    def clamped(self, metric: str) -> int:
        """How many steps in this metric went backwards. See :func:`_delta`."""
        values = self.values(metric)
        return sum(
            1 for previous, current in zip(values, values[1:], strict=False)
            if _delta(previous, current)[1]
        )

    @property
    def clamped_total(self) -> int:
        return sum(self.clamped(metric) for metric, _ in METRICS)

    def net(self, metric: str) -> int:
        """Total new across the whole series — the sum of the clamped steps.

        Deliberately not ``last - first``: that would quietly re-introduce the
        negative step that :func:`_delta` refused to report.
        """
        return sum(d for d in self.deltas(metric) if d is not None)

    @property
    def gaps(self) -> list[timedelta | None]:
        """Real interval before each capture. This is the evidence for irregular."""
        out: list[timedelta | None] = [None]
        for previous, current in zip(self.captures, self.captures[1:], strict=False):
            out.append(current.captured_at - previous.captured_at)
        return out

    @property
    def span(self) -> tuple[datetime | None, datetime | None]:
        if not self.captures:
            return (None, None)
        return (self.captures[0].captured_at, self.captures[-1].captured_at)


# ------------------------------------------------------------------- reading


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def _capture(payload: dict[str, Any], source: str, *, live: bool = False) -> Capture | None:
    """Reduce one payload to a point, or ``None`` if it cannot be placed in time.

    An undated payload is dropped rather than given a guessed position: an
    ordering invented here would look exactly like a measured one.
    """
    captured_at = _parse(payload.get("captured_at"))
    if captured_at is None:
        # A live snapshot is being read right now, so its stamp is not a guess.
        captured_at = now() if live else None
    if captured_at is None:
        return None

    sessions = _rows(payload, "sessions")
    return Capture(
        captured_at=captured_at,
        source=source,
        agents=len(_rows(payload, "agents")),
        files=len(_rows(payload, "files")),
        commits=len(_rows(payload, "commits")),
        sessions=len(sessions),
        # Fresh tokens only, matching Session.total_tokens. Cache reads live in
        # their own field and folding them in would inflate the line by orders
        # of magnitude, reading as usage where it is context reuse.
        session_tokens=sum(
            _int(row.get("input_tokens")) + _int(row.get("output_tokens")) for row in sessions
        ),
        live=live,
    )


def series(history_dir: Path | None = None, live: Snapshot | None = None) -> Trends:
    """Read every committed snapshot, plus the live one if given, as a series.

    The live snapshot is put through :func:`ajax_hq.snapshot.to_payload` rather
    than counted directly, so the newest point is measured by exactly the same
    code as the archived ones — otherwise the last step of every trend would be
    a comparison between two different definitions.
    """
    directory = history_dir or DEFAULT_HISTORY_DIR
    trends = Trends(history_dir=directory)

    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError, ValueError):
                trends.skipped_unreadable += 1
                continue
            if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
                # Skipped, not coerced: a payload from another schema may have
                # counted these fields differently, and a converted point would
                # be indistinguishable from a measured one.
                trends.skipped_schema += 1
                continue
            capture = _capture(payload, path.name)
            if capture is None:
                trends.skipped_undated += 1
                continue
            trends.captures.append(capture)

    if live is not None:
        capture = _capture(to_payload(live), LIVE_SOURCE, live=True)
        if capture is not None:
            trends.captures.append(capture)

    # Filenames start with the capture date, but a renamed or back-dated file
    # would order wrongly, so the timestamp inside the payload decides. Sorting
    # is stable, so captures sharing a stamp keep their filename order.
    trends.captures.sort(key=lambda c: c.captured_at)
    return trends


# ------------------------------------------------------------------- helpers


def _delta(previous: int, current: int) -> tuple[int, bool]:
    """New since the previous capture, floored at zero, and whether it floored.

    A raw subtraction can go negative with no work undone: a snapshot written in
    a fresh container before history was merged lists fewer records than its
    predecessor. That is a merge artefact, not a decrease, and "-7 agents" would
    be a claim about the world. The flag lets the footnote report how often it
    happened instead of the table stating something false.
    """
    difference = current - previous
    return (difference, False) if difference >= 0 else (0, True)


def sparkline(values: Sequence[float], width: int | None = None) -> str:
    """A one-line bar chart in block characters, scaled to its own range.

    Shorter than the requested width, the line is padded with spaces rather than
    stretched: a repeated or interpolated bar would be indistinguishable from a
    measured one. Longer, it is thinned by picking real samples — never by
    averaging neighbours into a value no capture ever reported.
    """
    points = [float(v) for v in values]
    if not points:
        return " " * width if width else ""

    target = len(points) if width is None else max(width, 0)
    if target == 0:
        return ""

    if target < len(points):
        if target == 1:
            points = [points[-1]]
        else:
            step = (len(points) - 1) / (target - 1)
            points = [points[round(index * step)] for index in range(target)]

    low, high = min(points), max(points)
    if high == low:
        # A flat series is neither high nor low relative to itself. ▁ would read
        # as "near zero" for a line that may be flat at a large number, so the
        # bar sits mid-height and the figures beside it carry the level.
        drawn = BLOCKS[len(BLOCKS) // 2] * len(points)
    else:
        span = high - low
        drawn = "".join(
            BLOCKS[min(int((point - low) / span * len(BLOCKS)), len(BLOCKS) - 1)]
            for point in points
        )
    return drawn.ljust(target)


def _gap_label(gap: timedelta | None) -> str:
    if gap is None:
        return "—"
    seconds = max(gap.total_seconds(), 0.0)
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 172_800:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.0f}d"


# ------------------------------------------------------------------ renderer


def render(trends: Trends, console: Console | None = None) -> None:
    """Print the series, or say plainly why there is not one yet."""
    console = console or Console()

    console.print()
    console.rule(Text("AJAX HQ  ·  추이  ·  TRENDS", style=f"bold {GOLD}"), style=GOLD)
    console.print()

    if not trends.captures:
        console.print(
            Panel(
                Text(
                    "No snapshots on file, so there is nothing to trend.\n"
                    "Run `ajax-hq snapshot` to record the first capture.",
                    style=FAINT,
                ),
                border_style="grey23",
                padding=(0, 1),
            )
        )
        _render_skips(trends, console)
        console.print()
        return

    _render_table(trends, console)

    if trends.enough:
        _render_sparklines(trends, console)
    else:
        console.print()
        console.print(
            Panel(
                Text(
                    "Not enough history for a trend.\n"
                    f"{len(trends.captures)} capture on file; a trend needs at least two.\n"
                    "No line is drawn and no direction is stated — a sparkline through\n"
                    "one point would be a drawing, not a measurement.",
                    style=FAINT,
                ),
                border_style="grey23",
                padding=(0, 1),
                width=min(console.width - 2, 72),
            )
        )

    _render_notes(trends, console)
    _render_skips(trends, console)
    console.print()


def _render_table(trends: Trends, console: Console) -> None:
    table = Table(title="Captures — every figure is cumulative", title_style=DIM)
    table.add_column("Captured", no_wrap=True)
    table.add_column("Since prev.", justify="right", no_wrap=True)
    for _, header in METRICS:
        table.add_column(header, justify="right", no_wrap=True)

    deltas = {metric: trends.deltas(metric) for metric, _ in METRICS}
    gaps = trends.gaps

    for index, capture in enumerate(trends.captures):
        label = Text(capture.label, style=GOLD if capture.live else "white")
        if capture.live:
            label.append("  live", style=FAINT)
        cells = [label, Text(_gap_label(gaps[index]), style=DIM)]
        for metric, _ in METRICS:
            cell = Text(f"{capture.value(metric):,}", style="white")
            delta = deltas[metric][index]
            if delta is not None:
                cell.append(f"  +{delta:,}", style=FAINT)
            cells.append(cell)
        table.add_row(*cells)

    console.print(table)


def _render_sparklines(trends: Trends, console: Console) -> None:
    console.print()
    console.print(Text("  Shape of each series, one bar per capture:", style=DIM))
    for metric, header in METRICS:
        values = trends.values(metric)
        line = Text("  ")
        line.append(f"{header:<9}", style="white")
        line.append(sparkline(values), style=GOLD)
        line.append(f"   {values[0]:,} → {values[-1]:,}", style=DIM)
        line.append(f"   +{trends.net(metric):,} new", style=FAINT)
        console.print(line)


def _render_notes(trends: Trends, console: Console) -> None:
    console.print()
    console.print(Text(
        "  Figures are cumulative — each capture lists everything known at that moment.",
        style=FAINT,
    ))
    console.print(Text(
        "  +n is a delta: new since the previous capture, floored at zero.",
        style=FAINT,
    ))
    console.print(Text(
        "  Captures are irregular — written when the command is run, not on a schedule.",
        style=FAINT,
    ))
    if trends.enough:
        console.print(Text(
            "  Bars are evenly spaced; the real intervals are in the Since prev. column.",
            style=FAINT,
        ))
    console.print(Text(
        "  Nothing is interpolated, and no capture is invented to fill a gap.",
        style=FAINT,
    ))

    if trends.clamped_total:
        console.print(Text(
            f"  ⚠ {trends.clamped_total} delta(s) floored at zero: a capture listed fewer "
            "records than",
            style="yellow3",
        ))
        console.print(Text(
            "    the one before it, which is a merge artefact rather than work undone.",
            style="yellow3",
        ))


def _render_skips(trends: Trends, console: Console) -> None:
    if trends.skipped_schema:
        console.print(Text(
            f"  {trends.skipped_schema} snapshot(s) on a different schema version were "
            f"skipped, not converted.",
            style="yellow3",
        ))
    if trends.skipped_unreadable:
        console.print(Text(
            f"  {trends.skipped_unreadable} snapshot(s) could not be read and were skipped.",
            style="yellow3",
        ))
    if trends.skipped_undated:
        console.print(Text(
            f"  {trends.skipped_undated} snapshot(s) carried no capture time and cannot be "
            f"placed in a series.",
            style="yellow3",
        ))
