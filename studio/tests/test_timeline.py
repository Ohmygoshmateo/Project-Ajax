"""The schedule, and the two things it must never do.

It must never lose or invent screen time — every start is the previous end plus a
gap the module can explain — and it must never let a derived duration pass as a
measured one. Those two, plus the gap rule, are what the tests below hold down.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ajax_studio import timeline as T
from ajax_studio.model import (
    RUNTIME_CEILING_S,
    RUNTIME_FLOOR_S,
    Act,
    Beat,
    Episode,
    Shot,
    Source,
    load_episode,
)

EPISODE_PATH = Path(__file__).resolve().parents[1] / "series" / "episodes" / "ep01-bay-four.yaml"

WORDS = "One two three four five six seven eight nine ten."  # exactly ten words → 4.0s derived


def beat(
    beat_id: str,
    act: Act,
    *,
    vo: str = WORDS,
    tension: int = 1,
    clock: str = "19:00",
    caption: str | None = None,
    seconds: float | None = None,
) -> Beat:
    return Beat(
        beat_id=beat_id,
        act=act,
        clock=clock,
        voiceover=vo,
        shot=Shot(description="a corridor"),
        tension=tension,
        caption=caption,
        authored_seconds=seconds,
    )


def episode(*beats: Beat) -> Episode:
    return Episode(number=9, title="Test", logline="a test", beats=list(beats))


@pytest.fixture
def ep01() -> Episode:
    return load_episode(EPISODE_PATH)


def render_to_text(tl: T.Timeline, width: int = 120) -> str:
    buffer = StringIO()
    T.render(tl, Console(file=buffer, width=width, force_terminal=False))
    return buffer.getvalue()


class TestNoLostOrInventedTime:
    def test_first_cue_starts_at_zero(self):
        tl = T.build(episode(beat("a", Act.COLD_OPEN), beat("b", Act.SETUP)))
        assert tl.cues[0].start == 0.0

    def test_each_cue_starts_where_the_previous_one_left_off_plus_its_gap(self):
        tl = T.build(episode(*[beat(f"b{i}", Act.RISE, tension=(i % 5) + 1) for i in range(6)]))
        for earlier, later in zip(tl.cues, tl.cues[1:], strict=False):
            assert later.start == pytest.approx(earlier.end + earlier.gap_after)

    def test_total_is_narration_plus_gaps_exactly(self, ep01):
        tl = T.build(ep01)
        report = tl.runtime()
        assert report.total_seconds == pytest.approx(report.narration_seconds + report.gap_seconds)
        assert tl.total_seconds == pytest.approx(report.total_seconds)

    def test_every_beat_gets_exactly_one_cue_in_script_order(self, ep01):
        tl = T.build(ep01)
        assert [c.beat_id for c in tl] == [b.beat_id for b in ep01.beats]

    def test_last_cue_has_no_trailing_gap(self, ep01):
        assert T.build(ep01).cues[-1].gap_after == 0.0

    def test_empty_beat_list_is_a_zero_length_timeline(self):
        tl = T.build(episode())
        assert len(tl) == 0
        assert tl.total_seconds == 0.0
        assert tl.runtime().total_seconds == 0.0


class TestGapRule:
    """1.2s base, doubled after a tension-5 line, +1.0s for a clock card ahead."""

    def test_base_gap_between_ordinary_beats(self):
        tl = T.build(episode(beat("a", Act.RISE, tension=2), beat("b", Act.RISE, tension=3)))
        assert tl.cues[0].gap_after == pytest.approx(T.DEFAULT_GAP)

    def test_gap_after_a_tension_five_beat_is_doubled(self):
        tl = T.build(episode(beat("a", Act.CRISIS, tension=5), beat("b", Act.CRISIS, tension=3)))
        assert tl.cues[0].gap_after == pytest.approx(T.DEFAULT_GAP * T.HOLD_MULTIPLIER)

    def test_the_hold_belongs_to_the_line_that_ended_not_the_one_that_follows(self):
        tl = T.build(episode(beat("a", Act.RISE, tension=1), beat("b", Act.RISE, tension=5)))
        assert tl.cues[0].gap_after == pytest.approx(T.DEFAULT_GAP)

    def test_a_clock_card_ahead_adds_its_own_screen_time(self):
        plain = T.build(
            episode(beat("a", Act.RISE, tension=2), beat("b", Act.RISE, tension=2, clock="21:00"))
        )
        carded = T.build(
            episode(
                beat("a", Act.RISE, tension=2),
                beat("b", Act.RISE, tension=2, clock="21:00", caption="21:00"),
            )
        )
        assert carded.cues[0].gap_after - plain.cues[0].gap_after == pytest.approx(T.CLOCK_CARD_S)

    def test_gap_is_configurable_and_scales_the_hold_with_it(self):
        tl = T.build(
            episode(beat("a", Act.CRISIS, tension=5), beat("b", Act.CRISIS, tension=1)),
            gap=3.0,
        )
        assert tl.cues[0].gap_after == pytest.approx(6.0)

    def test_zero_gap_leaves_only_clock_card_time(self, ep01):
        tl = T.build(ep01, gap=0.0)
        # Clock cards still cost their second — they are a picture, not a pause.
        assert tl.gap_seconds == pytest.approx(T.CLOCK_CARD_S * (len(tl.clock_cards()) - 1))

    def test_negative_gap_is_refused(self):
        with pytest.raises(ValueError, match="negative"):
            T.build(episode(beat("a", Act.RISE)), gap=-1.0)


class TestClockCards:
    def test_the_opening_beat_always_establishes_the_time(self):
        tl = T.build(episode(beat("a", Act.COLD_OPEN, clock="02:14"), beat("b", Act.SETUP)))
        assert tl.cues[0].clock_card is True

    def test_a_caption_repeating_the_clock_is_the_writer_asking_for_a_card(self):
        tl = T.build(
            episode(
                beat("a", Act.COLD_OPEN, clock="02:14"),
                beat("b", Act.SETUP, clock="19:02", caption="19:02"),
            )
        )
        assert tl.cues[1].clock_card is True

    def test_a_caption_that_is_not_the_clock_is_not_a_card(self):
        tl = T.build(
            episode(
                beat("a", Act.COLD_OPEN, clock="02:14"),
                beat("b", Act.SETUP, clock="19:02", caption="ST BRENDAN'S"),
            )
        )
        assert tl.cues[1].clock_card is False

    def test_a_beat_with_no_clock_never_cards(self):
        tl = T.build(episode(beat("a", Act.COLD_OPEN, clock=""), beat("b", Act.SETUP, clock="")))
        assert [c.clock_card for c in tl] == [False, False]

    def test_real_episode_cards_only_where_the_writer_marked_them(self, ep01):
        carded = {c.beat_id for c in T.build(ep01).clock_cards()}
        assert carded == {"open-01", "set-01", "rise-01", "rise-03", "crisis-01",
                          "fall-01", "fall-02"}


class TestProvenance:
    def test_word_count_alone_is_derived(self):
        tl = T.build(episode(beat("a", Act.RISE)))
        assert tl.cues[0].source is Source.DERIVED
        assert tl.cues[0].duration == pytest.approx(4.0)

    def test_authored_seconds_beat_arithmetic(self):
        tl = T.build(episode(beat("a", Act.RISE, seconds=9.0)))
        assert (tl.cues[0].source, tl.cues[0].duration) == (Source.AUTHORED, 9.0)

    def test_a_measured_voice_track_beats_everything(self):
        tl = T.build(episode(beat("a", Act.RISE, seconds=9.0)), voice_durations={"a": 6.5})
        assert (tl.cues[0].source, tl.cues[0].duration) == (Source.MEASURED, 6.5)

    def test_counts_cover_every_source_including_the_zeroes(self):
        tl = T.build(
            episode(beat("a", Act.RISE), beat("b", Act.RISE, seconds=5.0)),
            voice_durations={"a": 3.0},
        )
        assert tl.source_counts == {Source.DERIVED: 0, Source.MEASURED: 1, Source.AUTHORED: 1}

    def test_one_derived_cue_spoils_fully_measured(self):
        tl = T.build(episode(beat("a", Act.RISE), beat("b", Act.RISE)), voice_durations={"a": 3.0})
        assert tl.is_fully_measured is False
        assert len(tl.measured_cues) == 1 and len(tl.derived_cues) == 1

    def test_fully_measured_drops_the_caveat(self):
        tl = T.build(
            episode(beat("a", Act.RISE), beat("b", Act.RISE)),
            voice_durations={"a": 3.0, "b": 4.0},
        )
        assert tl.is_fully_measured is True
        assert tl.provenance_caveat is None

    def test_a_derived_schedule_says_so_in_words(self, ep01):
        caveat = T.build(ep01).provenance_caveat
        assert caveat is not None and "derived from word count" in caveat

    def test_a_beat_id_with_no_audio_match_is_an_error_not_a_shrug(self):
        # Audio and script drifting apart produces a schedule that looks correct.
        with pytest.raises(ValueError, match="typo-01"):
            T.build(episode(beat("a", Act.RISE)), voice_durations={"typo-01": 3.0})

    def test_duration_label_never_shows_a_bare_number(self, ep01):
        assert all(cue.source.value in cue.duration_label for cue in T.build(ep01))


class TestRuntimeReport:
    def test_the_three_figures_are_separate_values(self, ep01):
        report = T.build(ep01).runtime()
        assert report.narration_seconds < report.total_seconds
        assert report.gap_seconds > 0
        assert report.delta_to_target_s != report.total_seconds

    def test_short_episode_reports_a_negative_delta(self, ep01):
        report = T.build(ep01).runtime()
        assert report.within_target is False
        assert report.delta_to_target_s < 0
        assert "short of" in report.verdict

    def test_inside_target_reports_zero_delta(self):
        beats = [beat(f"b{i}", Act.RISE, seconds=30.0) for i in range(20)]
        report = T.build(episode(*beats)).runtime()
        assert RUNTIME_FLOOR_S <= report.total_seconds <= RUNTIME_CEILING_S
        assert report.delta_to_target_s == 0.0
        assert report.within_target is True
        assert "inside" in report.verdict

    def test_overlong_episode_reports_a_positive_delta(self):
        beats = [beat(f"b{i}", Act.RISE, seconds=60.0) for i in range(20)]
        report = T.build(episode(*beats)).runtime()
        assert report.delta_to_target_s > 0
        assert "over" in report.verdict

    def test_gap_share_shows_how_much_of_the_total_is_silence(self, ep01):
        report = T.build(ep01).runtime()
        assert 0.0 < report.gap_share < 0.25


class TestActSpans:
    def test_span_includes_the_gaps_inside_the_act(self):
        tl = T.build(
            episode(
                beat("a", Act.RISE, seconds=10.0, tension=1),
                beat("b", Act.RISE, seconds=10.0, tension=1),
                beat("c", Act.CRISIS, seconds=10.0),
            )
        )
        assert tl.act_seconds(Act.RISE) == pytest.approx(20.0 + T.DEFAULT_GAP)

    def test_an_absent_act_has_no_span(self):
        tl = T.build(episode(beat("a", Act.RISE)))
        assert tl.act_span(Act.CRISIS) is None
        assert tl.act_seconds(Act.CRISIS) == 0.0

    def test_real_cold_open_fits_inside_its_ceiling(self, ep01):
        assert T.build(ep01).act_seconds(Act.COLD_OPEN) <= 45.0

    def test_real_acts_run_in_order_without_overlapping(self, ep01):
        tl = T.build(ep01)
        order = (Act.COLD_OPEN, Act.SETUP, Act.RISE, Act.CRISIS, Act.FALLOUT)
        spans = [tl.act_span(act) for act in order]
        assert None not in spans
        for earlier, later in zip(spans, spans[1:], strict=False):
            assert earlier[1] <= later[0]


class TestRealEpisode:
    def test_twenty_one_beats_all_derived(self, ep01):
        tl = T.build(ep01)
        assert len(tl) == 21
        assert tl.source_counts[Source.DERIVED] == 21

    def test_hook_lands_well_inside_fifteen_seconds(self, ep01):
        assert T.build(ep01).cues[0].end < 15.0

    def test_narration_is_about_five_minutes_and_the_schedule_is_longer(self, ep01):
        tl = T.build(ep01)
        assert 280 < tl.narration_seconds < 320
        assert tl.total_seconds > tl.narration_seconds + 30


class TestRender:
    def test_render_labels_the_source_of_every_figure(self, ep01):
        text = render_to_text(T.build(ep01))
        assert "derived" in text
        assert "narration" in text and "gaps" in text and "total" in text
        assert "short of the 8:00 floor" in text

    def test_render_states_the_gap_rule_it_used(self, ep01):
        assert "after tension 5" in render_to_text(T.build(ep01))

    def test_render_of_a_measured_schedule_says_measured(self):
        tl = T.build(episode(beat("a", Act.RISE)), voice_durations={"a": 4.0})
        assert "measured from audio" in render_to_text(tl)

    def test_render_of_an_empty_timeline_does_not_raise(self):
        assert "0:00" in render_to_text(T.build(episode()))


class TestMmss:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(0, "0:00"), (9.4, "0:09"), (59.6, "1:00"), (300, "5:00"), (-143, "-2:23")],
    )
    def test_formatting(self, seconds, expected):
        assert T.mmss(seconds) == expected
