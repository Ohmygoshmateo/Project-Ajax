"""The validator, and the false positives that would get it switched off.

Every compliance rule here is tested twice: once with a line that must fail the
build, and once with a *near miss* — a line the series genuinely wants to write —
that must not. The near misses are the more important half. A rule that fires on
"the thing you have to understand about bay four" or "her blood pressure was
fine" gets disabled, and a disabled rule protects nothing at all.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ajax_studio import validate as V
from ajax_studio.model import Act, Beat, Episode, Shot, load_episode

EPISODE_PATH = Path(__file__).resolve().parents[1] / "series" / "episodes" / "ep01-bay-four.yaml"

TEN_WORDS = "One two three four five six seven eight nine ten."


def beat(
    beat_id: str,
    act: Act,
    *,
    vo: str = TEN_WORDS,
    tension: int = 1,
    seconds: float | None = None,
    clock: str = "19:00",
) -> Beat:
    return Beat(
        beat_id=beat_id,
        act=act,
        clock=clock,
        voiceover=vo,
        shot=Shot(description="a corridor"),
        tension=tension,
        authored_seconds=seconds,
    )


def well_formed(**overrides: object) -> Episode:
    """A synthetic episode that passes every rule, including the runtime target.

    Tests break one thing at a time by replacing beats, so the baseline has to be
    genuinely clean — otherwise a test asserting a code is absent proves nothing.
    """
    beats = [
        beat("open-1", Act.COLD_OPEN, tension=5, seconds=10.0, clock="02:14"),
        beat("open-2", Act.COLD_OPEN, tension=5, seconds=15.0, clock="02:14"),
        beat("open-3", Act.COLD_OPEN, tension=4, seconds=10.0, clock="02:14"),
        beat("set-1", Act.SETUP, tension=1, seconds=60.0),
        beat("set-2", Act.SETUP, tension=2, seconds=60.0),
        beat("rise-1", Act.RISE, tension=2, seconds=60.0),
        beat("rise-2", Act.RISE, tension=3, seconds=60.0),
        beat("rise-3", Act.RISE, tension=3, seconds=60.0),
        beat("rise-4", Act.RISE, tension=4, seconds=60.0),
        beat("crisis-1", Act.CRISIS, tension=5, seconds=60.0),
        beat("crisis-2", Act.CRISIS, tension=5, seconds=60.0),
        beat("fall-1", Act.FALLOUT, tension=3, seconds=50.0),
        beat("fall-2", Act.FALLOUT, tension=4, seconds=50.0),
    ]
    data: dict[str, object] = {
        "number": 9,
        "title": "Test",
        "logline": "a test",
        "beats": beats,
        "cliffhanger": "she does not send it",
    }
    data.update(overrides)
    return Episode(**data)  # type: ignore[arg-type]


def one_line(vo: str) -> Episode:
    """The well-formed episode with a single line of narration replaced."""
    ep = well_formed()
    ep.beats[6] = beat("rise-2", Act.RISE, vo=vo, tension=3, seconds=60.0)
    return ep


def codes_for(vo: str) -> set[str]:
    return V.check(one_line(vo)).codes()


@pytest.fixture
def ep01() -> Episode:
    return load_episode(EPISODE_PATH)


def render_to_text(report: V.Report, width: int = 110) -> str:
    buffer = StringIO()
    V.render(report, Console(file=buffer, width=width, force_terminal=False))
    return buffer.getvalue()


class TestBaseline:
    def test_the_synthetic_baseline_is_completely_clean(self):
        report = V.check(well_formed())
        assert report.findings == []
        assert report.clean is True and report.ok is True


class TestRealEpisode:
    """Episode 1 passes everything except the runtime, which is a note not a fault."""

    def test_no_errors(self, ep01):
        report = V.check(ep01)
        assert report.errors == []
        assert report.ok is True

    def test_the_only_finding_is_the_short_runtime(self, ep01):
        assert V.check(ep01).codes() == {"runtime-short"}

    def test_the_runtime_warning_is_a_warning(self, ep01):
        warning = V.check(ep01).warnings[0]
        assert warning.level is V.Level.WARNING
        assert warning.code == "runtime-short"

    def test_the_runtime_warning_labels_its_figure_as_derived(self, ep01):
        assert "derived runtime" in V.check(ep01).warnings[0].message

    def test_every_boundary_rule_passes_cleanly(self, ep01):
        boundary = [f for f in V.check(ep01).findings if f.code.startswith("boundary-")]
        assert boundary == []

    def test_checking_does_not_mutate_the_script(self, ep01):
        before = [(b.beat_id, b.voiceover, b.tension, b.act) for b in ep01.beats]
        V.check(ep01)
        after = [(b.beat_id, b.voiceover, b.tension, b.act) for b in ep01.beats]
        assert before == after and ep01.cliffhanger


class TestActStructure:
    def test_a_missing_act_is_an_error(self):
        ep = well_formed()
        ep.beats = [b for b in ep.beats if b.act is not Act.CRISIS]
        report = V.check(ep)
        assert "act-missing" in report.codes()
        assert any("crisis" in f.message for f in report.errors)

    def test_acts_out_of_order_are_an_error(self):
        ep = well_formed()
        ep.beats = ep.beats[3:5] + ep.beats[0:3] + ep.beats[5:]
        assert "act-out-of-order" in V.check(ep).codes()

    def test_an_act_that_is_left_and_re_entered_is_an_error(self):
        ep = well_formed()
        ep.beats.append(beat("rise-5", Act.RISE, tension=3, seconds=30.0))
        report = V.check(ep)
        assert "act-interleaved" in report.codes()
        assert "act-out-of-order" not in report.codes()  # one diagnosis, not two

    def test_a_correctly_ordered_episode_reports_neither(self):
        assert not {"act-missing", "act-out-of-order", "act-interleaved"} & V.check(
            well_formed()
        ).codes()


class TestOpeningRules:
    def test_a_long_cold_open_is_an_error(self):
        ep = well_formed()
        ep.beats[1] = beat("open-2", Act.COLD_OPEN, tension=5, seconds=40.0, clock="02:14")
        assert "cold-open-long" in V.check(ep).codes()

    def test_a_cold_open_inside_the_ceiling_is_not(self):
        assert "cold-open-long" not in V.check(well_formed()).codes()

    def test_a_hook_that_overruns_the_deadline_is_an_error(self):
        ep = well_formed()
        # Two cold-open beats, same 35s total, so only the hook rule can fire.
        ep.beats = [
            beat("open-1", Act.COLD_OPEN, tension=5, seconds=20.0, clock="02:14"),
            beat("open-2", Act.COLD_OPEN, tension=5, seconds=15.0, clock="02:14"),
        ] + ep.beats[3:]
        report = V.check(ep)
        assert report.codes() == {"hook-late"}
        assert report.errors[0].beat_id == "open-1"

    def test_a_hook_inside_the_deadline_is_not(self):
        assert "hook-late" not in V.check(well_formed()).codes()


class TestRuntimeIsAWarning:
    def test_a_short_episode_warns_and_does_not_fail(self):
        ep = well_formed()
        ep.beats = [beat("open-1", Act.COLD_OPEN, tension=5, seconds=5.0)] + ep.beats[1:4] + [
            beat("rise-1", Act.RISE, tension=2, seconds=5.0),
            beat("rise-2", Act.RISE, tension=4, seconds=5.0),
            beat("crisis-1", Act.CRISIS, tension=5, seconds=5.0),
            beat("fall-1", Act.FALLOUT, tension=3, seconds=5.0),
        ]
        report = V.check(ep)
        assert "runtime-short" in report.codes()
        assert report.ok is True

    def test_a_long_episode_warns_and_does_not_fail(self):
        ep = well_formed()
        ep.beats = ep.beats[:5] + [
            beat(f"rise-{i}", Act.RISE, tension=2 + (i % 3), seconds=90.0) for i in range(1, 9)
        ] + ep.beats[9:]
        report = V.check(ep)
        assert "runtime-long" in report.codes()
        assert report.ok is True

    def test_an_in_target_episode_says_nothing_about_runtime(self):
        assert not {"runtime-short", "runtime-long"} & V.check(well_formed()).codes()


class TestTensionCurve:
    def test_a_peak_outside_the_crisis_is_an_error(self):
        ep = well_formed()
        ep.beats[8] = beat("rise-4", Act.RISE, tension=5, seconds=60.0)
        ep.beats[9] = beat("crisis-1", Act.CRISIS, tension=4, seconds=60.0)
        ep.beats[10] = beat("crisis-2", Act.CRISIS, tension=4, seconds=60.0)
        report = V.check(ep)
        assert "tension-peak-misplaced" in report.codes()
        assert "rise" in report.errors[0].message

    def test_the_cold_open_may_tie_the_crisis(self):
        # The cold open *is* the crisis, out of order. Episode 1 ties at 5 in
        # three acts and that is the format working.
        assert "tension-peak-misplaced" not in V.check(well_formed()).codes()

    def test_a_completely_flat_rise_is_an_error(self):
        ep = well_formed()
        for index in (5, 6, 7, 8):
            ep.beats[index] = beat(f"rise-{index}", Act.RISE, tension=3, seconds=60.0)
        assert "tension-flat" in V.check(ep).codes()

    def test_a_rise_that_ends_where_it_started_is_an_error(self):
        ep = well_formed()
        ep.beats[8] = beat("rise-4", Act.RISE, tension=2, seconds=60.0)
        report = V.check(ep)
        assert "tension-flat" in report.codes()
        assert "2→3→3→2" in report.errors[0].message

    def test_a_climbing_rise_is_fine(self):
        assert "tension-flat" not in V.check(well_formed()).codes()


class TestCliffhanger:
    def test_a_missing_cliffhanger_is_an_error(self):
        assert "no-cliffhanger" in V.check(well_formed(cliffhanger="")).codes()

    def test_whitespace_does_not_count_as_a_cliffhanger(self):
        assert "no-cliffhanger" in V.check(well_formed(cliffhanger="   \n ")).codes()

    def test_a_real_cliffhanger_passes(self):
        assert "no-cliffhanger" not in V.check(well_formed()).codes()


class TestDosageRule:
    @pytest.mark.parametrize(
        "line",
        [
            "I drew up 5mg and checked it twice.",
            "It was 2.5 mg, not 25.",
            "The chart said 500 mcg and the vial said something else.",
            "Ten milligrams, and she signed for it.",
            "Half a milligram, which is nothing, except when it is not.",
            "It runs at 40 ml an hour.",
            "Two grams, over an hour.",
            "4 units, and she wrote it down wrong.",
            "It is dosed per kilogram, which is where it went wrong.",
        ],
    )
    def test_dosages_are_errors(self, line):
        assert "boundary-dosage" in codes_for(line)

    @pytest.mark.parametrize(
        "line",
        [
            # Every one of these is how this series actually writes.
            "Twenty-two in the waiting room, four boarding, one of them since Tuesday.",
            "Bay four is a fifty-one-year-old woman, chest pain, probably not her heart.",
            "I have been on my feet for six and a half hours and I have eaten a cereal bar.",
            "Priya cried in the clean utility room for ninety seconds.",
            "Two ambulances, then a third, and the third one eats the department.",
            "She's eighty-six and she's confused and she needs four of us for an hour.",
            "She weighs about ninety kilograms and the trolley complains about it.",
            "I said it maybe four hundred times.",
            # Spelled numbers are matched against spelled dose units only. "Four
            # units" is not scanned, because "on the unit" is how this series
            # says "ward" and the pair is not worth the false positives.
            "She is the fastest new grad on the unit and that is not a compliment.",
        ],
    )
    def test_near_misses_do_not_fire(self, line):
        assert "boundary-dosage" not in codes_for(line)


class TestInstructionalRule:
    @pytest.mark.parametrize(
        "line",
        [
            "If you have chest pain that goes into your jaw, that is the one to worry about.",
            "You should stop taking it and get a blood pressure check.",
            "You need to get to an emergency room if the pain moves.",
            "Take this before the antibiotic, not after.",
            "The treatment is straightforward once somebody has actually diagnosed it.",
            "Here is what to do if the bleeding does not stop.",
            "Never inject it into the same site twice.",
            "The dose is the whole question and nobody wants to say it out loud.",
        ],
    )
    def test_instruction_is_an_error(self, line):
        assert "boundary-instructional" in codes_for(line)

    @pytest.mark.parametrize(
        "line",
        [
            # "you have to" opens episode 1. It means listen, not do this.
            "Okay. So the thing you have to understand about bay four is that I was in there.",
            "I have been on my feet for six hours and I have eaten a cereal bar that I found.",
            "That's what you do, that is the entire point of the form, it is not a punishment.",
            "I want that on the record, whatever else ends up on the record.",
            "You should have seen the printer. It works but it lies about it.",
            "I want you to know I thought that.",
            "Give me a minute, Deshawn, I am reading the board.",
            # A nurse assigning herself a patient, mid-sentence, with a symptom in it.
            "I'll take this one, she's got chest pain and nobody has seen her yet.",
            "I could teach that. I have taught that.",
            "She needed monitoring and one thing giving and one thing stopping.",
            "If you have ever worked a night shift you know what eleven o'clock does.",
            "And I know all that, and I did it anyway, and you can decide what that makes me.",
        ],
    )
    def test_near_misses_do_not_fire(self, line):
        assert "boundary-instructional" not in codes_for(line)


class TestGraphicRule:
    @pytest.mark.parametrize(
        "line",
        [
            "There was blood pooling under the trolley and I stood in it.",
            "Blood everywhere, and the smell of it, and I am not going to describe the rest.",
            "The bone was protruding and I looked away, which I never do.",
            "It was a gaping wound and the room went quiet.",
            "Her leg was degloved from the knee down.",
        ],
    )
    def test_graphic_description_is_an_error(self, line):
        assert "boundary-graphic" in codes_for(line)

    @pytest.mark.parametrize(
        "line",
        [
            # The routine vocabulary of the setting. Firing here kills the rule.
            "Her blood pressure was fine and her numbers were boring.",
            "I sent off bloods at half one and chased them at three.",
            "The blood gas came back better than anyone expected.",
            "She was bleeding, and we stopped it, and that was the easy part of the night.",
            "Somebody spilled coffee everywhere and it is still on the floor.",
            "There is a puddle in the ambulance bay because it has rained all night.",
        ],
    )
    def test_near_misses_do_not_fire(self, line):
        assert "boundary-graphic" not in codes_for(line)


class TestFindingShape:
    def test_a_boundary_finding_names_the_beat(self):
        report = V.check(one_line("I drew up 5mg and did not check it."))
        assert [f.beat_id for f in report.errors] == ["rise-2"]

    def test_a_boundary_finding_is_always_an_error(self):
        report = V.check(one_line("You should take two tablets for the pain."))
        assert all(f.is_error for f in report.errors if f.code.startswith("boundary-"))
        assert report.ok is False

    def test_findings_never_repeat_a_level_outside_the_two(self, ep01):
        assert {f.level for f in V.check(ep01).findings} <= {V.Level.ERROR, V.Level.WARNING}

    def test_level_compares_equal_to_its_string(self):
        assert V.Level.ERROR == "error" and V.Level.WARNING == "warning"


class TestRender:
    def test_clean_report_says_so_explicitly(self):
        assert "no findings" in render_to_text(V.check(well_formed()))

    def test_warnings_are_grouped_and_labelled(self, ep01):
        text = render_to_text(V.check(ep01))
        assert "1 warning" in text
        assert "runtime-short" in text
        assert "not a failed build" in text

    def test_errors_are_grouped_and_block_the_build(self):
        text = render_to_text(V.check(one_line("I drew up 5mg and did not check it.")))
        assert "1 error" in text
        assert "boundary-dosage" in text
        assert "errors block the build" in text

    def test_both_levels_appear_when_both_exist(self):
        ep = well_formed(cliffhanger="")
        ep.beats = [
            beat("open-1", Act.COLD_OPEN, tension=5, seconds=5.0),
            beat("set-1", Act.SETUP, tension=1, seconds=5.0),
            beat("rise-1", Act.RISE, tension=2, seconds=5.0, vo="I drew up 5mg."),
            beat("rise-2", Act.RISE, tension=4, seconds=5.0),
            beat("crisis-1", Act.CRISIS, tension=5, seconds=5.0),
            beat("fall-1", Act.FALLOUT, tension=3, seconds=5.0),
        ]
        text = render_to_text(V.check(ep))
        assert "error" in text and "warning" in text
        assert "no-cliffhanger" in text and "runtime-short" in text
