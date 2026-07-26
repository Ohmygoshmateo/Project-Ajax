"""The vertical cut: one real window of the episode, chosen for tension.

A short for a faceless serial has one job — make someone want the long one. That
rules out the two obvious cheats. It must not be a highlight reel stitched from
beats that were never next to each other, because the appeal of this series is
that Rowan is talking continuously and a jump cut in the narration destroys it. And
it must not give away the crisis, because the whole format is built on dread: the
audience is supposed to know something is coming, not what.

So :func:`pick` searches *contiguous* windows only, scores them by total tension,
and prefers the best window that stops short of the crisis act's resolution — the
beat where the worst moment turns out to be survivable. If no window that fits the
target avoids that beat, the excerpt says so on the way out rather than quietly
shipping the ending.

The frames are the same cards as the horizontal previz, reflowed to 1080×1920, so
there is one renderer to keep honest and the vertical cut is marked as previz too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ajax_studio.model import Act, Beat, Episode, Source
from ajax_studio.render import (
    DEFAULT_FPS,
    VERTICAL,
    CardContext,
    RenderResult,
    build_cues,
    render_cues,
)

# Long enough to carry a whole thought of Rowan's, short enough for every vertical
# surface's default cutoff. One number, because the clip is posted everywhere.
DEFAULT_TARGET_S = 45.0


@dataclass(frozen=True)
class Excerpt:
    """A contiguous run of beats, and the honest story of how it was chosen."""

    start_index: int          # inclusive index into episode.beats
    end_index: int            # inclusive
    beats: tuple[Beat, ...]
    seconds: float
    target_seconds: float
    tension_total: int
    spoils_crisis: bool
    overruns_target: bool
    note: str

    @property
    def beat_ids(self) -> tuple[str, ...]:
        return tuple(beat.beat_id for beat in self.beats)

    @property
    def is_contiguous(self) -> bool:
        """True by construction — kept as an assertion the tests can make."""
        return len(self.beats) == self.end_index - self.start_index + 1

    @property
    def tension_mean(self) -> float:
        return self.tension_total / max(len(self.beats), 1)

    def summary(self) -> str:
        window = f"{self.beat_ids[0]}..{self.beat_ids[-1]}" if self.beats else "empty"
        flags = []
        if self.spoils_crisis:
            flags.append("SPOILS CRISIS")
        if self.overruns_target:
            flags.append("OVER TARGET")
        tail = f" [{', '.join(flags)}]" if flags else ""
        return (
            f"{window} · {len(self.beats)} beats · {self.seconds:.1f}s "
            f"of {self.target_seconds:.0f}s · tension {self.tension_total} "
            f"(mean {self.tension_mean:.1f}){tail}"
        )


def crisis_resolution_index(episode: Episode) -> int | None:
    """Index of the beat that resolves the crisis, or ``None`` if there is none.

    That is the crisis act's last beat. By the bible's shape the crisis *is* the
    cold open in context, so the front of the act is fair game for a clip — it is
    material the episode itself opens with. What must not leak is how it lands,
    and that is the beat the act ends on.
    """
    crisis = [index for index, beat in enumerate(episode.beats) if beat.act is Act.CRISIS]
    return crisis[-1] if crisis else None


def _durations(
    beats: Sequence[Beat], measured: Mapping[str, float] | None
) -> list[float]:
    measured = measured or {}
    return [beat.duration(measured.get(beat.beat_id))[0] for beat in beats]


def _windows(durations: Sequence[float], limit: float) -> list[tuple[int, int, float]]:
    """Every contiguous ``(start, end, seconds)`` that fits inside ``limit``.

    Quadratic, and deliberately so: an episode is twenty beats, and a sliding
    window with early exit would be harder to read for no measurable gain.
    """
    found: list[tuple[int, int, float]] = []
    for start in range(len(durations)):
        total = 0.0
        for end in range(start, len(durations)):
            total += durations[end]
            if total > limit:
                break
            found.append((start, end, total))
    return found


def _score(window: tuple[int, int, float], tensions: Sequence[int]) -> tuple[int, float, int]:
    """Rank key: total tension, then tension per second, then earlier start.

    Density is the tie-breaker because two windows carrying the same tension are
    not equal — the shorter one spends less of a viewer's patience on it. Earlier
    starts win the final tie so the choice is stable across runs.
    """
    start, end, seconds = window
    total = sum(tensions[start : end + 1])
    return (total, total / max(seconds, 1e-9), -start)


def pick(
    episode: Episode,
    seconds: float = DEFAULT_TARGET_S,
    *,
    measured: Mapping[str, float] | None = None,
) -> Excerpt:
    """The best contiguous window of at most ``seconds``, spoiler-free if possible."""
    if not episode.beats:
        raise ValueError(f"{episode.slug}: no beats to excerpt")

    durations = _durations(episode.beats, measured)
    tensions = episode.tension_curve()
    resolution = crisis_resolution_index(episode)
    windows = _windows(durations, seconds)

    if not windows:
        # Every single beat is longer than the target. Returning the strongest one
        # over length is more useful than returning nothing, but it is flagged:
        # the fix is a shorter beat or a longer target, and that is the writer's
        # call rather than the renderer's.
        index = max(range(len(durations)), key=lambda i: (tensions[i], -durations[i]))
        return _build(
            episode, index, index, durations, tensions, seconds,
            spoils=resolution == index,
            overruns=True,
            note=(
                f"no window of {seconds:.0f}s fits — the shortest beat runs "
                f"{min(durations):.1f}s. Returning the single strongest beat, over target."
            ),
        )

    clean = [w for w in windows if resolution is None or not w[0] <= resolution <= w[1]]
    if clean:
        start, end, _ = max(clean, key=lambda w: _score(w, tensions))
        note = (
            "highest-tension window that fits and stops short of the crisis resolution"
            if resolution is not None
            else "highest-tension window that fits; this episode has no crisis act to protect"
        )
        return _build(episode, start, end, durations, tensions, seconds,
                      spoils=False, overruns=False, note=note)

    # Only reachable when the crisis resolution is unavoidable — an episode short
    # enough that every fitting window contains it. Said out loud, not hidden.
    start, end, _ = max(windows, key=lambda w: _score(w, tensions))
    resolved_id = episode.beats[resolution].beat_id if resolution is not None else "?"
    return _build(
        episode, start, end, durations, tensions, seconds,
        spoils=True,
        overruns=False,
        note=(
            f"every window that fits {seconds:.0f}s contains {resolved_id}, the crisis "
            "resolution — this clip spoils the crisis. Shorten the target or do not post it."
        ),
    )


def _build(
    episode: Episode,
    start: int,
    end: int,
    durations: Sequence[float],
    tensions: Sequence[int],
    target: float,
    *,
    spoils: bool,
    overruns: bool,
    note: str,
) -> Excerpt:
    return Excerpt(
        start_index=start,
        end_index=end,
        beats=tuple(episode.beats[start : end + 1]),
        seconds=sum(durations[start : end + 1]),
        target_seconds=target,
        tension_total=sum(tensions[start : end + 1]),
        spoils_crisis=spoils,
        overruns_target=overruns,
        note=note,
    )


def render_vertical(
    episode: Episode,
    excerpt: Excerpt,
    out_path: Path,
    *,
    measured: Mapping[str, float] | None = None,
    sources: Mapping[str, Source] | None = None,
    size: tuple[int, int] = VERTICAL,
    fps: int = DEFAULT_FPS,
    base_dir: Path | None = None,
) -> RenderResult:
    """Render ``excerpt`` as a vertical previz clip.

    The excerpt's beats are re-timed from zero — the clip is its own piece of
    media and its cue clock should read as one, not carry the episode's offsets.
    """
    cues = build_cues(excerpt.beats, measured=measured, sources=sources)
    base = base_dir or (episode.source_path.parent if episode.source_path else None)
    kicker = "VERTICAL PREVIZ" + (" · SPOILER" if excerpt.spoils_crisis else "")
    context = CardContext(
        episode_label=f"EP{episode.number:02d} · {episode.title.upper()}",
        total_cues=len(cues),
        total_seconds=sum(cue.duration for cue in cues),
        base_dir=base,
        kicker=kicker,
    )
    return render_cues(cues, Path(out_path), context, size=size, fps=fps)
