"""The previz renderer, checked against the only thing that matters: a real file.

These tests deliberately do not mock ffmpeg. The failure this module exists to
prevent is "the pipeline reported success and there is no watchable cut", and a
mocked encoder cannot catch it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from ajax_studio.model import ACT_ORDER, Act, Beat, Episode, Shot, Source
from ajax_studio.render import (
    ACT_STYLES,
    FRAME,
    VERTICAL,
    CardContext,
    build_cues,
    draw_card,
    previz,
)

# Slack allowed between the schedule handed in and the container measured back.
# It is bounded by three quantisations rather than by hope: the fps filter can
# extend the closing still by one frame period (1/12 s at TEST_FPS), AAC packs
# 1024 samples per frame (21 ms at 48 kHz), and -t trims on a frame boundary.
# 0.25 s is about double that worst case, and still two orders of magnitude
# tighter than the pacing judgements a previz cut is made for.
TOLERANCE_S = 0.25

# Low, because the frames are stills and every encoded frame is test wall-clock.
TEST_FPS = 12

MAGENTA = (255, 0, 255)


def _beat(
    beat_id: str,
    act: Act,
    *,
    tension: int = 3,
    seconds: float | None = 2.0,
    asset: str | None = None,
    caption: str | None = None,
    voiceover: str = "That was a bad one. I am not going to dress it up for you.",
) -> Beat:
    return Beat(
        beat_id=beat_id,
        act=act,
        clock="02:14",
        voiceover=voiceover,
        shot=Shot(description=f"Corridor, sodium light, {beat_id}.", motion="push", asset=asset),
        tension=tension,
        caption=caption,
        authored_seconds=seconds,
    )


@pytest.fixture
def episode() -> Episode:
    """Three beats, ~5.5s, spanning three acts and one authored/derived mix."""
    return Episode(
        number=99,
        title="Test Shift",
        logline="A synthetic episode used only to prove the renderer produces media.",
        beats=[
            _beat("t01", Act.COLD_OPEN, tension=5, seconds=2.0, caption="02:14"),
            _beat("t02", Act.RISE, tension=3, seconds=2.0),
            # No authored seconds: this one exercises the derived path, and at four
            # words it lands on the 1.5s floor in Beat.duration.
            _beat("t03", Act.CRISIS, tension=5, seconds=None, voiceover="Boring is beautiful."),
        ],
    )


@pytest.fixture
def magenta_asset(tmp_path: Path) -> Path:
    """A flat, unmistakable image — a colour no card in the palette contains."""
    path = tmp_path / "assets" / "bay-four.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 400), MAGENTA).save(path)
    return path


def _context(episode: Episode, base_dir: Path | None = None) -> CardContext:
    return CardContext.for_episode(episode, build_cues(episode.beats), base_dir=base_dir)


# ------------------------------------------------------------------- the cut


@pytest.mark.slow
def test_previz_writes_a_real_mp4(episode: Episode, tmp_path: Path) -> None:
    out = tmp_path / "previz.mp4"
    result = previz(episode, out, fps=TEST_FPS)

    assert out.is_file()
    # Non-trivial: three 1920x1080 stills with text on them cannot compress to
    # less than a few kilobytes, and an empty or header-only MP4 would.
    assert result.size_bytes > 8_000
    assert result.size_bytes == out.stat().st_size
    assert result.frames == 3
    assert result.width, result.height == FRAME
    assert result.video_frames > 0


@pytest.mark.slow
def test_reported_duration_matches_the_schedule(episode: Episode, tmp_path: Path) -> None:
    """The container has to agree with the cue list, or pacing judgements are void."""
    schedule = [
        (episode.beats[0], 0.0, 2.0),
        (episode.beats[1], 2.0, 3.0),
        (episode.beats[2], 5.0, 1.5),
    ]
    result = previz(episode, tmp_path / "scheduled.mp4", schedule, fps=TEST_FPS)

    assert result.scheduled_s == pytest.approx(6.5)
    assert result.duration_s == pytest.approx(6.5, abs=TOLERANCE_S)
    assert abs(result.drift_s) <= TOLERANCE_S


@pytest.mark.slow
def test_placeholder_and_asset_beats_are_counted(
    episode: Episode, magenta_asset: Path, tmp_path: Path
) -> None:
    episode.beats[1] = _beat("t02", Act.RISE, seconds=2.0, asset=str(magenta_asset))
    result = previz(episode, tmp_path / "mixed.mp4", fps=TEST_FPS)

    assert (result.asset_beats, result.placeholder_beats) == (1, 2)
    assert result.missing_assets == 0
    # The mix is what makes an incremental asset drop safe; both states coexist.
    assert result.frames == result.asset_beats + result.placeholder_beats


# ------------------------------------------------------------------ the cards


def test_real_asset_replaces_the_card(episode: Episode, magenta_asset: Path) -> None:
    """A beat with a real image must show the image, not a description of it."""
    episode.beats[1] = _beat("t02", Act.SETUP, seconds=2.0, asset=str(magenta_asset))
    cues = build_cues(episode.beats)
    card = draw_card(cues[1], _context(episode), FRAME)

    assert card.used_asset is True
    assert card.is_placeholder is False
    assert card.state_label.startswith("REAL ASSET")
    # Centre of frame: inside the letterbox, above the subtitle scrim, and where
    # the vignette contributes nothing. It is the asset's own pixels or nothing.
    centre = card.image.getpixel((FRAME[0] // 2, FRAME[1] // 2))
    assert all(abs(a - b) <= 12 for a, b in zip(centre, MAGENTA, strict=True))


def test_asset_is_letterboxed_not_stretched(episode: Episode, tmp_path: Path) -> None:
    """A wide asset keeps its aspect; the frame gains bars, the art does not skew."""
    wide = tmp_path / "wide.png"
    Image.new("RGB", (800, 200), MAGENTA).save(wide)
    episode.beats[1] = _beat("t02", Act.SETUP, seconds=2.0, asset=str(wide))
    card = draw_card(build_cues(episode.beats)[1], _context(episode), FRAME)

    # 800x200 fitted to 1920x1080 is 1920x480, so the frame's vertical middle is
    # magenta and a point well above it is not.
    assert card.image.getpixel((FRAME[0] // 2, FRAME[1] // 2))[0] > 200
    assert card.image.getpixel((FRAME[0] // 2, 120)) != MAGENTA


def test_placeholder_card_is_visibly_marked(episode: Episode) -> None:
    """The marking is load-bearing: a previz still must never read as finished."""
    card = draw_card(build_cues(episode.beats)[0], _context(episode), FRAME)

    assert card.is_placeholder is True
    assert "PLACEHOLDER" in card.state_label
    assert "NO ASSET" in card.state_label
    # The band is drawn, not merely described: the bottom rows carry the sodium
    # warning colour, which appears nowhere else at that saturation.
    bottom_row = [card.image.getpixel((x, FRAME[1] - 12)) for x in range(0, FRAME[0], 40)]
    assert any(r > 180 and 90 < g < 190 and b < 110 for r, g, b in bottom_row)


def test_missing_asset_is_reported_not_swallowed(episode: Episode) -> None:
    """A shot naming a file that is not there is a script error, and says so."""
    episode.beats[1] = _beat("t02", Act.RISE, seconds=2.0, asset="assets/never-shot.png")
    card = draw_card(build_cues(episode.beats)[1], _context(episode), FRAME)

    assert card.used_asset is False
    assert card.missing_asset is True
    assert "ASSET MISSING" in card.state_label


def test_card_renders_vertically_at_the_short_size(episode: Episode) -> None:
    card = draw_card(build_cues(episode.beats)[0], _context(episode), VERTICAL)
    assert card.image.size == VERTICAL


def test_every_act_has_its_own_treatment() -> None:
    """The five-act shape has to be visible while scrubbing, so the acts differ."""
    assert set(ACT_STYLES) == set(ACT_ORDER)
    grounds = {style.ground_top for style in ACT_STYLES.values()}
    assert len(grounds) == len(ACT_ORDER)


# ----------------------------------------------------------------- the timings


def test_cues_are_laid_out_cumulatively(episode: Episode) -> None:
    cues = build_cues(episode.beats)
    assert [cue.start for cue in cues] == [0.0, 2.0, 4.0]
    assert cues[-1].end == pytest.approx(5.5)
    assert [cue.index for cue in cues] == [1, 2, 3]


def test_measured_seconds_beat_authored_and_derived(episode: Episode) -> None:
    """A real recording always wins, and the frame's label says which it was."""
    cues = build_cues(episode.beats, measured={"t01": 9.75})

    assert cues[0].duration == pytest.approx(9.75)
    assert cues[0].source is Source.MEASURED
    assert "measured" in cues[0].timing_label
    # The untouched beats keep their own provenance rather than inheriting it.
    assert cues[1].source is Source.AUTHORED
    assert cues[2].source is Source.DERIVED
    assert "derived" in cues[2].timing_label


def test_schedule_is_taken_as_given_and_provenance_recovered(episode: Episode) -> None:
    """A pre-computed timeline decides the timing; the beat still decides the label."""
    schedule = [(beat, 3.0 * index, 3.0) for index, beat in enumerate(episode.beats)]
    cues = build_cues(episode.beats, schedule, sources={"t03": Source.MEASURED})

    assert [cue.duration for cue in cues] == [3.0, 3.0, 3.0]
    assert [cue.start for cue in cues] == [0.0, 3.0, 6.0]
    assert cues[2].source is Source.MEASURED
    assert cues[0].source is Source.AUTHORED


def test_result_never_drops_timing_provenance(episode: Episode) -> None:
    cues = build_cues(episode.beats)
    context = CardContext.for_episode(episode, cues)
    card = draw_card(cues[2], context, FRAME)

    # Not a render test — this asserts the contract that the words "derived",
    # "authored" or "measured" travel with the number everywhere it is shown.
    assert cues[2].timing_label == "1.5s derived"
    assert card.state_label
