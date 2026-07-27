"""The narration adapter: where a beat's seconds actually come from.

There is no text-to-speech on this machine and no subscription behind it, so this
module ships exactly one working backend — :class:`SilentVoice`, which writes a
silence track of the derived length. That is enough to cut a watchable animatic
and judge pacing, which is the whole point of previz.

What this module deliberately does **not** do is fake a voice. A backend that
returned a plausible-sounding duration without producing audio would poison every
downstream number, because the renderer and the validator cannot tell an invented
second from a real one. So the only durations that exist here are:

* **derived** — word count over :data:`ajax_studio.model.WORDS_PER_SECOND`, and
  labelled ``derived`` everywhere it surfaces, including on the previz frame;
* **measured** — read out of a real audio file with ffmpeg.

Real audio always wins. If a beat names an ``audio`` file that exists on disk,
its true length is measured and returned as :attr:`Source.MEASURED` regardless of
which backend is configured, because a recording beats arithmetic.

Adding a real backend
---------------------

:class:`VoiceBackend` is the seam. A real backend (ElevenLabs, Azure, Piper, a
local model — the pipeline does not care) must:

1. satisfy :class:`VoiceBackend`: a ``name``, a ``source``, and
   ``synthesize(text, out_path) -> float``;
2. write real decodable audio to ``out_path`` and return the length **of that
   file**, obtained from :func:`measure_duration` — never from an estimate the
   API happened to report, and never from word count;
3. declare ``source = Source.MEASURED``, which is what tells the renderer to stop
   printing "derived timing" on the frame;
4. raise :class:`VoiceError` on failure rather than emitting silence, so a broken
   key fails the build instead of quietly shipping a mute episode;
5. register itself in :data:`BACKENDS` so ``get_backend`` can find it.

Nothing else in the pipeline needs to change. There is intentionally no stub
implementation here to "fill in later" — an empty class that returns a number is
indistinguishable, from the outside, from one that works.

ffmpeg lives here rather than in a utility module because audio is the only
reason this project shells out at all, and the renderer's silent track is an
audio concern that happens to be muxed by the same binary.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

from imageio_ffmpeg import get_ffmpeg_exe

from ajax_studio.model import WORDS_PER_SECOND, Beat, Episode, Source

# Mirrors the floor in ``Beat.duration``. A beat under a second and a half reads
# as a mistake on screen even when the line really is two words long, and the
# silence track has to agree with the card the renderer draws over it.
MINIMUM_BEAT_SECONDS = 1.5

# 48 kHz stereo because that is what every editor and every player expects; the
# track is silence, so the cost of being conventional is nil.
SAMPLE_RATE = 48_000
CHANNEL_LAYOUT = "stereo"

_FFMPEG_TIMEOUT_S = 600.0


class VoiceError(RuntimeError):
    """Narration could not be produced or measured."""


class FfmpegError(VoiceError):
    """The bundled ffmpeg exited non-zero. Carries the tail of its stderr."""


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """Path to the bundled static ffmpeg.

    Resolved through imageio-ffmpeg, never through ``PATH``: there is no system
    ffmpeg on a stock laptop or in CI, and the wheel is a dependency precisely so
    that ``pip install -e studio`` is the entire setup.
    """
    return get_ffmpeg_exe()


def run_ffmpeg(args: Sequence[str], *, timeout: float = _FFMPEG_TIMEOUT_S) -> str:
    """Run ffmpeg with ``args`` and return its stderr, which is where it talks.

    ``-nostdin`` matters: without it a long encode steals the terminal from the
    CLI that launched it.
    """
    command = [ffmpeg_exe(), "-hide_banner", "-nostdin", *args]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed binary, no shell
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(f"ffmpeg timed out after {timeout:.0f}s: {' '.join(args)}") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise FfmpegError(f"ffmpeg exited {proc.returncode} for {' '.join(args)}\n{tail}")
    return proc.stderr


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_TIME_RE = re.compile(r"time=\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def _hms(match: re.Match[str]) -> float:
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def measure_duration(path: Path) -> float:
    """Seconds of media in ``path``, measured — never estimated.

    The imageio-ffmpeg wheel ships ffmpeg but not ffprobe, so the length is taken
    from a decode pass to null. The final ``time=`` of that pass is the length
    actually decoded, which is what a player will see; the container header is
    only a fallback, because some formats round it or omit it entirely.
    """
    if not path.is_file():
        raise VoiceError(f"cannot measure {path}: no such file")

    stderr = run_ffmpeg(["-i", str(path), "-f", "null", "-"])

    progress = list(_TIME_RE.finditer(stderr))
    if progress:
        return _hms(progress[-1])

    header = _DURATION_RE.search(stderr)
    if header is not None:
        return _hms(header)

    raise VoiceError(f"ffmpeg reported no duration for {path}")


def write_silence(seconds: float, out_path: Path) -> None:
    """Write ``seconds`` of real, decodable silence as 16-bit PCM WAV.

    PCM rather than a compressed codec so the file's length is exactly the length
    asked for, with no encoder padding to explain away later.
    """
    if seconds <= 0:
        raise VoiceError(f"silence must be positive, got {seconds!r}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout={CHANNEL_LAYOUT}:sample_rate={SAMPLE_RATE}",
            "-t", f"{seconds:.3f}",
            "-c:a", "pcm_s16le",
            str(out_path),
        ]
    )


@runtime_checkable
class VoiceBackend(Protocol):
    """A source of narration audio for one line of voiceover.

    ``source`` is part of the contract, not decoration: it is how the rest of the
    pipeline knows whether the seconds it is holding were measured off a waveform
    or computed from a word count, and the previz frame prints it.
    """

    name: str
    source: Source

    def synthesize(self, text: str, out_path: Path) -> float:
        """Write audio for ``text`` to ``out_path`` and return its duration."""


@dataclass(frozen=True)
class SilentVoice:
    """The previz backend: silence of the derived length.

    Honest by construction — it makes no claim to speak, and the duration it
    returns is arithmetic on word count, reported as :attr:`Source.DERIVED` so
    nothing downstream can mistake it for a performance.
    """

    words_per_second: float = WORDS_PER_SECOND
    minimum_seconds: float = MINIMUM_BEAT_SECONDS
    name: str = "silent"
    source: Source = Source.DERIVED

    def estimate(self, text: str) -> float:
        """Derived seconds for ``text``, matching ``Beat.duration`` exactly."""
        return max(self.minimum_seconds, len(text.split()) / self.words_per_second)

    def synthesize(self, text: str, out_path: Path) -> float:
        seconds = self.estimate(text)
        write_silence(seconds, out_path)
        return seconds


# The registry a real backend joins. Deliberately one entry long: everything the
# pipeline can actually do today is in it.
BACKENDS: dict[str, Callable[[], VoiceBackend]] = {"silent": SilentVoice}


def get_backend(name: str) -> VoiceBackend:
    """Look up a backend by name, failing loudly on anything not shipped.

    The error names what exists so the answer to "why is there no elevenlabs"
    arrives at the point of use rather than as a mute episode.
    """
    try:
        factory = BACKENDS[name]
    except KeyError as exc:
        available = ", ".join(sorted(BACKENDS)) or "none"
        raise VoiceError(
            f"no voice backend named {name!r}; available: {available}. "
            "Real text-to-speech is a documented seam in ajax_studio.voice, "
            "not something this repository ships."
        ) from exc
    return factory()


@dataclass(frozen=True)
class Narration:
    """One beat's narration: the seconds, and where they came from."""

    beat_id: str
    seconds: float
    source: Source
    backend: str
    path: Path | None = None

    @property
    def is_derived(self) -> bool:
        return self.source is Source.DERIVED

    @property
    def label(self) -> str:
        """What the renderer prints on the frame. Provenance is never dropped."""
        return f"{self.seconds:.1f}s {self.source.value}"


def _resolve_audio(reference: str, base_dir: Path | None) -> Path:
    """Resolve a beat's ``audio`` reference the way a writer would expect."""
    path = Path(reference).expanduser()
    if path.is_absolute() or base_dir is None:
        return path
    return base_dir / path


def narrate(
    episode: Episode,
    out_dir: Path,
    backend: VoiceBackend | None = None,
    *,
    base_dir: Path | None = None,
) -> list[Narration]:
    """Narration for every beat, in beat order.

    A real track named by the beat wins outright: it is measured and returned as
    ``MEASURED`` without the backend being consulted, so an episode can be voiced
    one line at a time and the timing sharpens as recordings land.
    """
    backend = backend or SilentVoice()
    base = base_dir or (episode.source_path.parent if episode.source_path else None)
    out_dir.mkdir(parents=True, exist_ok=True)

    narrations: list[Narration] = []
    for beat in episode.beats:
        real = _real_track(beat, base)
        if real is not None:
            narrations.append(
                Narration(
                    beat_id=beat.beat_id,
                    seconds=measure_duration(real),
                    source=Source.MEASURED,
                    backend="measured",
                    path=real,
                )
            )
            continue

        target = out_dir / f"{beat.beat_id}.wav"
        if backend.source is Source.DERIVED and beat.authored_seconds is not None:
            # The writer overrode the estimate on purpose, so the silence is cut
            # to the authored length instead of the word-count one. The file on
            # disk and the number reported for it must never disagree.
            seconds, source = beat.duration()
            write_silence(seconds, target)
        else:
            seconds = backend.synthesize(beat.voiceover, target)
            source = backend.source
        narrations.append(
            Narration(
                beat_id=beat.beat_id,
                seconds=seconds,
                source=source,
                backend=backend.name,
                path=target,
            )
        )
    return narrations


def _real_track(beat: Beat, base_dir: Path | None) -> Path | None:
    """The beat's audio file if it genuinely exists, else ``None``."""
    if not beat.audio:
        return None
    candidate = _resolve_audio(beat.audio, base_dir)
    return candidate if candidate.is_file() else None


def measured_seconds(narrations: Sequence[Narration]) -> dict[str, float]:
    """Beat id → seconds, for the narrations that were actually measured.

    This is the map the renderer and the timeline take as ``measured``: only real
    recordings belong in it, so passing it can never upgrade a guess.
    """
    return {n.beat_id: n.seconds for n in narrations if n.source is Source.MEASURED}
