"""The episode format: what a script *is*, before anything renders it.

One file defines the contract so the validator, the timeline, the renderer, and
the metadata generator cannot drift apart. A script is plain YAML — readable and
editable by a person who does not write Python, because the writing is the part
of this project that actually matters.

Nothing here estimates or invents. A duration derived from word count is marked
as derived and carries the rate it used; a duration supplied by a real voice
track overrides it and says so. The distinction survives all the way to the
render, because pacing decided from a guess and pacing decided from a recording
are different things.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1

# Narration pace, words per second. 2.5 is a deliberately slow read — this is a
# tense first-person diary, not an explainer. Measured against real voice tracks
# it will be wrong in both directions; that is why every derived duration is
# labelled derived and can be replaced by a measured one.
WORDS_PER_SECOND = 2.5

# Runtime targets. Below the floor the episode cannot carry an arc; above the
# ceiling retention collapses on a channel with no audience yet.
RUNTIME_FLOOR_S = 8 * 60
RUNTIME_CEILING_S = 12 * 60

# The hook has to land before a viewer decides to leave.
HOOK_DEADLINE_S = 15.0
COLD_OPEN_CEILING_S = 45.0


class Act(str, Enum):
    """Five acts, because suspense needs a shape and a shape needs names."""

    COLD_OPEN = "cold_open"   # the worst moment of the shift, out of order
    SETUP = "setup"           # the shift begins, ordinary, seeded
    RISE = "rise"             # complications compound
    CRISIS = "crisis"         # the worst moment, now in context
    FALLOUT = "fallout"       # consequence, and the thread left hanging


ACT_ORDER = (Act.COLD_OPEN, Act.SETUP, Act.RISE, Act.CRISIS, Act.FALLOUT)


class Source(str, Enum):
    """Where a duration came from. Never guess which one you are holding."""

    DERIVED = "derived"    # computed from word count at WORDS_PER_SECOND
    MEASURED = "measured"  # read from a real audio file
    AUTHORED = "authored"  # written into the script by hand


@dataclass(frozen=True)
class Shot:
    """What is on screen. Deliberately a description, not an asset path.

    The visuals are bought or generated later, so a beat names what it needs
    rather than pointing at a file that does not exist yet. ``asset`` is filled
    in once something real exists; until then the renderer draws a card from
    ``description`` so pacing can be judged before any money is spent.
    """

    description: str
    motion: str = "hold"          # hold | push | pull | drift
    asset: str | None = None      # a real image or clip, when there is one

    @property
    def is_placeholder(self) -> bool:
        return self.asset is None


@dataclass(frozen=True)
class Beat:
    """One unit of the episode: a clock label, some narration, and a shot."""

    beat_id: str
    act: Act
    clock: str                    # in-world time, e.g. "23:41"
    voiceover: str
    shot: Shot
    tension: int = 1              # 1-5, the writer's own reading
    caption: str | None = None    # on-screen text, if any
    audio: str | None = None      # a real voice track, when there is one
    authored_seconds: float | None = None

    @property
    def words(self) -> int:
        return len(self.voiceover.split())

    def duration(self, measured: float | None = None) -> tuple[float, Source]:
        """Seconds on screen, and where that number came from.

        Precedence is measurement, then authorship, then derivation — a real
        recording beats a writer's estimate, and both beat arithmetic on word
        count.
        """
        if measured is not None:
            return (measured, Source.MEASURED)
        if self.authored_seconds is not None:
            return (self.authored_seconds, Source.AUTHORED)
        return (max(1.5, self.words / WORDS_PER_SECOND), Source.DERIVED)


@dataclass
class Episode:
    """A whole episode, as loaded from YAML."""

    number: int
    title: str
    logline: str
    beats: list[Beat] = field(default_factory=list)
    thread: str = ""              # the arc thread this episode advances
    cliffhanger: str = ""         # what is deliberately left unresolved
    source_path: Path | None = None

    @property
    def slug(self) -> str:
        stem = "".join(c.lower() if c.isalnum() else "-" for c in self.title)
        while "--" in stem:
            stem = stem.replace("--", "-")
        return f"ep{self.number:02d}-{stem.strip('-')}"

    def acts(self) -> dict[Act, list[Beat]]:
        grouped: dict[Act, list[Beat]] = {act: [] for act in ACT_ORDER}
        for beat in self.beats:
            grouped[beat.act].append(beat)
        return grouped

    def runtime(self) -> float:
        return sum(beat.duration()[0] for beat in self.beats)

    def tension_curve(self) -> list[int]:
        return [beat.tension for beat in self.beats]


class ScriptError(ValueError):
    """A script that cannot be loaded. Raised with the file and the field."""


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data:
        raise ScriptError(f"{where}: missing required field {key!r}")
    return data[key]


def load_episode(path: Path) -> Episode:
    """Parse one episode YAML file.

    Strict on purpose, unlike the transcript parsing in Ajax HQ. A transcript is
    someone else's format arriving unannounced, so it is read defensively; a
    script is ours, and a typo in it should stop the build rather than silently
    produce a shorter episode.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ScriptError(f"{path}: unreadable — {exc}") from exc

    if not isinstance(raw, dict):
        raise ScriptError(f"{path}: top level must be a mapping")

    version = raw.get("schema", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ScriptError(f"{path}: schema {version} is not supported (expected {SCHEMA_VERSION})")

    beats: list[Beat] = []
    raw_beats = _require(raw, "beats", str(path))
    if not isinstance(raw_beats, list) or not raw_beats:
        raise ScriptError(f"{path}: 'beats' must be a non-empty list")

    for index, item in enumerate(raw_beats, start=1):
        where = f"{path}: beat {index}"
        if not isinstance(item, dict):
            raise ScriptError(f"{where}: must be a mapping")

        act_name = _require(item, "act", where)
        try:
            act = Act(act_name)
        except ValueError as exc:
            names = ", ".join(a.value for a in ACT_ORDER)
            raise ScriptError(f"{where}: unknown act {act_name!r} (expected one of {names})") from exc

        shot_raw = item.get("shot")
        if isinstance(shot_raw, str):
            shot = Shot(description=shot_raw)
        elif isinstance(shot_raw, dict):
            shot = Shot(
                description=str(_require(shot_raw, "description", where)),
                motion=str(shot_raw.get("motion", "hold")),
                asset=shot_raw.get("asset"),
            )
        else:
            raise ScriptError(f"{where}: 'shot' must be a string or a mapping")

        tension = item.get("tension", 1)
        if not isinstance(tension, int) or not 1 <= tension <= 5:
            raise ScriptError(f"{where}: 'tension' must be an integer 1-5, got {tension!r}")

        beats.append(
            Beat(
                beat_id=str(item.get("id") or f"b{index:02d}"),
                act=act,
                clock=str(item.get("clock", "")),
                voiceover=str(_require(item, "vo", where)).strip(),
                shot=shot,
                tension=tension,
                caption=item.get("caption"),
                audio=item.get("audio"),
                authored_seconds=item.get("seconds"),
            )
        )

    return Episode(
        number=int(_require(raw, "number", str(path))),
        title=str(_require(raw, "title", str(path))),
        logline=str(raw.get("logline", "")).strip(),
        beats=beats,
        thread=str(raw.get("thread", "")).strip(),
        cliffhanger=str(raw.get("cliffhanger", "")).strip(),
        source_path=path,
    )


def load_series(directory: Path) -> list[Episode]:
    """Every episode in a directory, in episode-number order."""
    episodes = [load_episode(path) for path in sorted(directory.glob("*.yaml"))]
    return sorted(episodes, key=lambda e: e.number)
