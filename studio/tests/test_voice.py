"""The narration adapter: derived silence, and measurement that overrides it."""

from __future__ import annotations

from pathlib import Path

import pytest

from ajax_studio.model import Act, Beat, Episode, Shot, Source
from ajax_studio.voice import (
    BACKENDS,
    SilentVoice,
    VoiceBackend,
    VoiceError,
    get_backend,
    measure_duration,
    measured_seconds,
    narrate,
    write_silence,
)

# PCM silence is sample-exact, so the only slack needed is for ffmpeg reporting
# its progress clock in hundredths of a second.
TOLERANCE_S = 0.05


def _beat(beat_id: str, words: int, *, seconds: float | None = None, audio: str | None = None) -> Beat:
    return Beat(
        beat_id=beat_id,
        act=Act.SETUP,
        clock="19:02",
        voiceover=" ".join(["word"] * words),
        shot=Shot(description="A whiteboard, half erased."),
        authored_seconds=seconds,
        audio=audio,
    )


@pytest.fixture
def episode() -> Episode:
    return Episode(
        number=99,
        title="Test Shift",
        logline="Synthetic.",
        beats=[_beat("v01", 10), _beat("v02", 5), _beat("v03", 1)],
    )


def test_silent_voice_writes_real_measurable_audio(tmp_path: Path) -> None:
    voice = SilentVoice()
    out = tmp_path / "v01.wav"
    reported = voice.synthesize(" ".join(["word"] * 25), out)

    assert out.is_file()
    assert reported == pytest.approx(10.0)  # 25 words at 2.5 wps
    # The number returned and the file on disk are the same length. A backend that
    # returned a duration it had not produced would be the whole problem.
    assert measure_duration(out) == pytest.approx(reported, abs=TOLERANCE_S)


def test_derived_seconds_never_fall_below_the_floor() -> None:
    """A two-word line still needs long enough on screen to be read."""
    assert SilentVoice().estimate("Start at seven.") == pytest.approx(1.5)


def test_silent_voice_declares_its_timing_derived() -> None:
    voice = SilentVoice()
    assert voice.source is Source.DERIVED
    assert voice.name == "silent"
    assert isinstance(voice, VoiceBackend)


def test_narrate_labels_every_beat_as_derived(episode: Episode, tmp_path: Path) -> None:
    narrations = narrate(episode, tmp_path / "vo")

    assert [n.beat_id for n in narrations] == ["v01", "v02", "v03"]
    assert all(n.source is Source.DERIVED for n in narrations)
    assert all(n.is_derived for n in narrations)
    assert all(n.path is not None and n.path.is_file() for n in narrations)
    assert narrations[0].label == "4.0s derived"
    # Nothing was measured, so nothing may claim to have been.
    assert measured_seconds(narrations) == {}


def test_real_audio_overrides_the_estimate(episode: Episode, tmp_path: Path) -> None:
    """A recording on disk wins outright, and is reported as measured."""
    track = tmp_path / "real-v02.wav"
    write_silence(7.5, track)
    episode.beats[1] = _beat("v02", 5, audio=str(track))

    narrations = narrate(episode, tmp_path / "vo")
    voiced = narrations[1]

    assert voiced.source is Source.MEASURED
    assert voiced.backend == "measured"
    assert voiced.path == track
    assert voiced.seconds == pytest.approx(7.5, abs=TOLERANCE_S)
    # The derived estimate for five words would have been 2.0s; measurement won.
    assert voiced.seconds > 5.0
    assert set(measured_seconds(narrations)) == {"v02"}


def test_authored_seconds_cut_the_silence_to_match(episode: Episode, tmp_path: Path) -> None:
    """The writer's override reaches the track, so file and report agree."""
    episode.beats[0] = _beat("v01", 10, seconds=6.0)
    narrations = narrate(episode, tmp_path / "vo")

    assert narrations[0].source is Source.AUTHORED
    assert narrations[0].seconds == pytest.approx(6.0)
    assert measure_duration(narrations[0].path or Path()) == pytest.approx(6.0, abs=TOLERANCE_S)


def test_a_named_track_that_does_not_exist_falls_back_to_derived(
    episode: Episode, tmp_path: Path
) -> None:
    episode.beats[2] = _beat("v03", 1, audio="recordings/never-recorded.wav")
    narrations = narrate(episode, tmp_path / "vo", base_dir=tmp_path)

    assert narrations[2].source is Source.DERIVED
    assert narrations[2].backend == "silent"


def test_only_the_silent_backend_ships() -> None:
    """The seam is documented; the fake implementation is deliberately absent."""
    assert set(BACKENDS) == {"silent"}
    assert isinstance(get_backend("silent"), SilentVoice)

    with pytest.raises(VoiceError) as caught:
        get_backend("elevenlabs")
    assert "silent" in str(caught.value)


def test_measuring_a_missing_file_is_an_error_not_a_zero(tmp_path: Path) -> None:
    with pytest.raises(VoiceError):
        measure_duration(tmp_path / "nothing.wav")


def test_silence_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(VoiceError):
        write_silence(0.0, tmp_path / "empty.wav")
