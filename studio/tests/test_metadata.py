"""Publishing metadata, and the disclosures it is required to carry.

The disclosure tests are the ones that matter. A missing line here is not a
cosmetic defect — it is the difference between a labelled work of fiction and
an unlabelled synthetic recording of a person who does not exist.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ajax_studio import metadata as meta_mod
from ajax_studio.model import Act, Beat, Episode, Shot, load_episode

SERIES = Path(__file__).resolve().parents[1] / "series" / "episodes"


@pytest.fixture
def episode() -> Episode:
    return load_episode(SERIES / "ep01-bay-four.yaml")


@pytest.fixture
def built(episode: Episode):
    return meta_mod.build(episode)


class TestDisclosures:
    """Every required disclosure, present and findable."""

    def test_fiction_is_declared(self, built):
        assert "work of fiction" in built.description.lower()

    def test_the_people_and_place_are_declared_invented(self, built):
        lowered = built.description.lower()
        assert "does not exist" in lowered

    def test_synthetic_narration_is_declared(self, built):
        assert "synthetic" in built.description.lower()

    def test_not_medical_advice_is_declared(self, built):
        assert "medical advice" in built.description.lower()

    def test_disclosures_are_near_the_top_not_buried(self, built):
        """A disclosure below the fold is a disclosure nobody reads."""
        head = "\n".join(built.description.splitlines()[:12]).lower()
        for phrase in ("fiction", "synthetic", "medical advice"):
            assert phrase in head, f"{phrase!r} is not in the first 12 lines"

    def test_a_missing_disclosure_is_caught(self, built):
        """The check must fail loudly rather than pass a stripped description."""
        stripped = dataclasses.replace(built, description="Episode 2. Rowan works a shift.")
        assert meta_mod.check(stripped)

    def test_a_complete_description_reports_no_problems(self, built):
        assert meta_mod.check(built) == []


class TestAlteredContent:
    """YouTube's synthetic-media question, answered in the object.

    Left to the uploader's memory it is answered wrong eventually; as a field it
    travels with the episode.
    """

    def test_the_answer_is_yes(self, built):
        assert built.altered_content.form_answer.lower().startswith("yes")

    def test_the_reason_is_stated(self, built):
        assert built.altered_content.reason.strip()


class TestTitles:
    def test_several_variants_for_ab_testing(self, built):
        assert len(built.titles) >= 2
        assert len(set(built.titles)) == len(built.titles)

    def test_the_default_is_the_first(self, built):
        assert built.title == built.titles[0]

    def test_every_variant_names_the_series_and_episode(self, built):
        for variant in built.titles:
            assert "Bay Four" in variant
            assert "Ep" in variant

    def test_a_title_implying_truth_is_caught(self, built):
        """"A real nurse's diary" is the exact claim this series must never make."""
        lying = dataclasses.replace(
            built, titles=["A REAL nurse's true story — Bay Four, Ep. 1", *built.titles]
        )
        assert meta_mod.check(lying)


class TestChapters:
    def test_one_chapter_per_act(self, built, episode):
        assert len(built.chapters) == len({b.act for b in episode.beats})

    def test_chapters_start_at_zero(self, built):
        """YouTube ignores a chapter list whose first mark is not 00:00."""
        assert built.chapters[0].start == 0.0

    def test_chapters_are_ordered_and_distinct(self, built):
        starts = [c.start for c in built.chapters]
        assert starts == sorted(starts)
        assert len(set(starts)) == len(starts)

    def test_chapter_lines_carry_a_timestamp(self, built):
        assert ":" in built.chapters[0].line()


class TestTags:
    def test_fiction_is_tagged(self, built):
        assert any("fiction" in tag for tag in built.tags)

    def test_no_tag_implies_a_real_place(self, built):
        """A tag naming a real hospital would attach this fiction to it."""
        for tag in built.tags:
            assert "st brendan" not in tag.lower().replace("'", "")

    def test_tags_are_lowercase_and_unique(self, built):
        assert built.tags == [t.lower() for t in built.tags]
        assert len(set(built.tags)) == len(built.tags)


class TestEveryEpisode:
    """The written season, not just the one the fixtures use."""

    @pytest.mark.parametrize("path", sorted(SERIES.glob("*.yaml")), ids=lambda p: p.stem)
    def test_metadata_builds_and_passes_its_own_check(self, path):
        assert meta_mod.check(meta_mod.build(load_episode(path))) == []


class TestSerialisation:
    def test_as_dict_round_trips_the_fields_an_uploader_needs(self, built):
        payload = built.as_dict()
        for key in ("title", "description", "tags"):
            assert key in payload, f"{key} missing from the upload payload"

    def test_the_payload_records_that_timing_is_estimated(self, built):
        """A previz cut and a finished cut must not be confused downstream."""
        assert built.timing_is_estimated is True


class TestDegenerateInput:
    def test_an_episode_with_one_beat_does_not_crash_the_builder(self):
        episode = Episode(
            number=99,
            title="Fragment",
            logline="A single beat.",
            cliffhanger="Unresolved.",
            beats=[
                Beat(
                    beat_id="b01",
                    act=Act.COLD_OPEN,
                    clock="00:00",
                    voiceover="One line, and then nothing.",
                    shot=Shot(description="Black."),
                    tension=3,
                )
            ],
        )
        built = meta_mod.build(episode)
        assert built.chapters
        assert "fiction" in built.description.lower()
