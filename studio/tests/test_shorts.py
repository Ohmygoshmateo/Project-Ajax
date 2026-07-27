"""Choosing the vertical clip: contiguous, tense, and not a spoiler."""

from __future__ import annotations

from pathlib import Path

import pytest

from ajax_studio.model import Act, Beat, Episode, Shot
from ajax_studio.render import VERTICAL
from ajax_studio.shorts import Excerpt, crisis_resolution_index, pick, render_vertical

TEST_FPS = 12
TOLERANCE_S = 0.25  # see tests/test_render.py for how this bound is derived


def _beat(beat_id: str, act: Act, tension: int, seconds: float) -> Beat:
    return Beat(
        beat_id=beat_id,
        act=act,
        clock="02:14",
        voiceover=f"This is {beat_id}, and I want that on the record.",
        shot=Shot(description=f"Corridor, sodium light, {beat_id}."),
        tension=tension,
        authored_seconds=seconds,
    )


@pytest.fixture
def episode() -> Episode:
    """Six beats of 5s each. The tension peak sits on the crisis resolution."""
    return Episode(
        number=99,
        title="Test Shift",
        logline="Synthetic.",
        beats=[
            _beat("s01", Act.COLD_OPEN, 4, 5.0),
            _beat("s02", Act.SETUP, 1, 5.0),
            _beat("s03", Act.RISE, 3, 5.0),
            _beat("s04", Act.CRISIS, 5, 5.0),
            _beat("s05", Act.CRISIS, 5, 5.0),   # the crisis resolution
            _beat("s06", Act.FALLOUT, 2, 5.0),
        ],
    )


def _assert_contiguous(excerpt: Excerpt, episode: Episode) -> None:
    """The clip must be a run of the episode, not a compilation of moments."""
    assert excerpt.is_contiguous
    assert excerpt.beats == tuple(episode.beats[excerpt.start_index : excerpt.end_index + 1])
    indices = [episode.beats.index(beat) for beat in excerpt.beats]
    assert indices == list(range(indices[0], indices[0] + len(indices)))


def test_window_is_contiguous_and_fits(episode: Episode) -> None:
    excerpt = pick(episode, seconds=12.0)

    _assert_contiguous(excerpt, episode)
    assert excerpt.seconds <= 12.0
    assert excerpt.overruns_target is False


def test_the_highest_tension_window_wins(episode: Episode) -> None:
    """Two beats fit in 12s; among the spoiler-free pairs s03+s04 is the tensest."""
    excerpt = pick(episode, seconds=12.0)

    assert excerpt.beat_ids == ("s03", "s04")
    assert excerpt.tension_total == 8
    assert excerpt.tension_mean == pytest.approx(4.0)


def test_the_crisis_resolution_is_not_spoiled(episode: Episode) -> None:
    """s04+s05 scores higher, and is refused because s05 resolves the crisis."""
    resolution = crisis_resolution_index(episode)
    assert resolution == 4

    excerpt = pick(episode, seconds=12.0)
    assert excerpt.spoils_crisis is False
    assert "s05" not in excerpt.beat_ids
    assert not excerpt.start_index <= resolution <= excerpt.end_index
    assert "stops short" in excerpt.note


def test_an_unavoidable_spoiler_is_declared_not_hidden() -> None:
    """When only the resolution fits, the excerpt says so instead of shipping quietly."""
    episode = Episode(
        number=98,
        title="Tight Shift",
        logline="Synthetic.",
        beats=[
            _beat("x01", Act.SETUP, 4, 40.0),      # too long to fit the target
            _beat("x02", Act.CRISIS, 5, 3.0),      # the only beat that fits, and the
        ],                                          # crisis act's last beat
    )
    excerpt = pick(episode, seconds=5.0)

    assert excerpt.beat_ids == ("x02",)
    assert excerpt.spoils_crisis is True
    assert "spoils the crisis" in excerpt.note
    assert "SPOILS CRISIS" in excerpt.summary()


def test_no_window_fits_is_flagged_rather_than_silently_trimmed() -> None:
    episode = Episode(
        number=97,
        title="Long Shift",
        logline="Synthetic.",
        beats=[_beat("y01", Act.RISE, 3, 60.0), _beat("y02", Act.RISE, 5, 90.0)],
    )
    excerpt = pick(episode, seconds=45.0)

    assert excerpt.overruns_target is True
    assert excerpt.seconds > excerpt.target_seconds
    assert "over target" in excerpt.note
    assert excerpt.beat_ids == ("y02",)  # the strongest beat, not the first


def test_an_episode_with_no_crisis_act_has_nothing_to_protect() -> None:
    episode = Episode(
        number=96,
        title="Quiet Shift",
        logline="Synthetic.",
        beats=[_beat("z01", Act.SETUP, 2, 5.0), _beat("z02", Act.RISE, 4, 5.0)],
    )
    excerpt = pick(episode, seconds=12.0)

    assert crisis_resolution_index(episode) is None
    assert excerpt.spoils_crisis is False
    assert excerpt.beat_ids == ("z01", "z02")


def test_measured_seconds_change_which_window_fits(episode: Episode) -> None:
    """Real recordings reshape the clip, because they reshape the timings."""
    tight = pick(episode, seconds=12.0, measured={"s03": 9.0, "s04": 9.0})

    assert tight.seconds <= 12.0
    _assert_contiguous(tight, episode)
    assert tight.beat_ids != ("s03", "s04")


def test_empty_episode_is_refused() -> None:
    with pytest.raises(ValueError):
        pick(Episode(number=95, title="Nothing", logline=""))


@pytest.mark.slow
def test_render_vertical_writes_a_real_portrait_mp4(episode: Episode, tmp_path: Path) -> None:
    excerpt = pick(episode, seconds=12.0)
    out = tmp_path / "short.mp4"
    result = render_vertical(episode, excerpt, out, fps=TEST_FPS)

    assert out.is_file()
    assert (result.width, result.height) == VERTICAL
    assert result.size_bytes > 8_000
    assert result.frames == len(excerpt.beats)
    assert result.duration_s == pytest.approx(excerpt.seconds, abs=TOLERANCE_S)
    # The clip is its own piece of media, timed from zero.
    assert result.scheduled_s == pytest.approx(excerpt.seconds)
