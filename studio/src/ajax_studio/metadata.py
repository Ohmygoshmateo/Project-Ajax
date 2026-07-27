"""Publishing metadata: the boxes above the video, written from the script.

Titles, description, tags and chapters are not a separate creative act — they
are a *view* of the episode, so they are derived from it. A title variant here
is always a line Rowan actually says or an image the script actually shows,
because a title promising something the episode does not contain is the exact
failure mode this channel cannot afford: it reads as generated, and generated is
what the policy is looking for.

Three things in this module are obligations rather than choices, and they are
all in one place so they cannot be forgotten under deadline.

**The disclosures go at the top of the description, in prose.** The bible
requires four statements — the series is fiction, everyone and everywhere in it
is invented, the narration is synthetic, and none of it is medical advice — and
requires them where a viewer sees them rather than at the bottom under the
hashtags. :data:`REQUIRED_DISCLOSURES` holds them as sentences a person would
actually read, and :func:`check` proves each one is present *and* near the top.

**The altered-content answer is stored, not remembered.** YouTube asks the
uploader a yes/no question about synthetic content at upload time, and this
series answers yes every single episode because the narration is a realistic
synthetic voice. Leaving that to whoever is doing the upload at 1am is how it
eventually gets answered wrong, so it is a field on the object with its reason
attached — see :class:`AlteredContent`.

**:func:`check` reports; it never repairs.** A title that implies a true story
or a tag that implies a real hospital is a writing decision that went wrong, and
silently rewriting it would hide the decision from the person who made it. Every
problem comes back as a sentence naming what is wrong.

The chapter times come from :meth:`ajax_studio.model.Beat.duration`, which is
word-count arithmetic until real voice tracks exist. That is flagged on the
object as :attr:`Metadata.timing_is_estimated`; chapters must be rebuilt once
the narration is recorded, because chapter marks that are thirty seconds out are
worse than no chapters at all.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Any

from ajax_studio.model import Act, Episode, Source

SERIES_TITLE = "Bay Four"

# Platform limits, as of writing. They are here rather than inline because when
# they change, they change in one place.
TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000
TAGS_CHARACTER_LIMIT = 500
MAX_TAGS = 15

# YouTube only builds a chapter list if the first mark is at zero and there are
# at least three of them, each at least ten seconds long. A description that
# almost satisfies that silently gets no chapters at all.
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10.0

# How much of the description counts as "the top". A viewer sees roughly the
# first three lines before clicking "more", and a disclosure below this point is
# a disclosure nobody read.
DISCLOSURE_WINDOW = 900

# How many title variants an A/B test wants: enough to compare, few enough that
# each one was actually thought about.
MIN_TITLE_VARIANTS = 2
MAX_TITLE_VARIANTS = 3


# ---------------------------------------------------------------- disclosures


@dataclass(frozen=True)
class Disclosure:
    """One thing the description is required to say, and how to tell it said it.

    ``probes`` exists because the sentence will be rewritten — it should be,
    it is prose — and a check that only recognised one exact wording would fail
    the moment someone improved it. The probes are the load-bearing words: any
    honest rewording of the sentence keeps at least one.
    """

    key: str
    text: str
    probes: tuple[str, ...]

    def found_at(self, collapsed: str) -> int:
        """Character offset of the earliest probe in *collapsed*, or -1."""
        hits = [collapsed.find(probe) for probe in self.probes]
        hits = [index for index in hits if index >= 0]
        return min(hits) if hits else -1


REQUIRED_DISCLOSURES: tuple[Disclosure, ...] = (
    Disclosure(
        key="fiction",
        text=(
            "Bay Four is a work of fiction. It is a scripted drama written to be "
            "listened to, not a record of anything that happened to anyone."
        ),
        probes=("work of fiction", "is fiction", "fiction"),
    ),
    Disclosure(
        key="invented",
        text=(
            "St. Brendan's does not exist and neither does Rowan. She, her "
            "colleagues, every patient she describes, the city they are all in and "
            "the voice diary she keeps were invented for this story; no actual "
            "hospital, place or person is being written about, and no case is drawn "
            "from one."
        ),
        probes=("were invented", "invented for", "invented"),
    ),
    Disclosure(
        key="synthetic",
        text=(
            "The narration is synthetic. Rowan's voice was generated from the "
            "script — nobody sat down and recorded any of this."
        ),
        probes=("narration is synthetic", "synthetic", "ai-generated", "ai generated"),
    ),
    Disclosure(
        key="not_advice",
        text=(
            "None of it is medical advice and none of it is instructions. What the "
            "characters do is written to make a story work, which is a completely "
            "different job from being correct. If something is wrong with you, "
            "talk to someone who can actually see you."
        ),
        probes=("not medical advice", "medical advice"),
    ),
)

CLOSING_NOTE = (
    "Bay Four is a serial: twelve episodes, one shift each, one story underneath. "
    "It starts at episode one and it is meant to be heard in order. "
    "Every script is written by hand, one at a time."
)


def disclosure_block(width: int = 88) -> str:
    """The four required statements as one readable paragraph.

    Wrapped, because a description is read in a narrow column and an unwrapped
    hundred-and-eighty-character line is a wall. The wrapping is invisible to
    :func:`check`, which collapses whitespace before it looks for anything.
    """
    paragraph = " ".join(disclosure.text for disclosure in REQUIRED_DISCLOSURES)
    return textwrap.fill(paragraph, width=width)


# ------------------------------------------------------- altered content form


@dataclass(frozen=True)
class AlteredContent:
    """The answer to YouTube's altered-or-synthetic content question.

    The question is asked per upload and the answer for this series never
    changes, so it is recorded here with its reasoning rather than reconstructed
    by whoever is publishing. The three sub-flags mirror the three cases the
    disclosure actually covers, and two of them are ``False`` on purpose: this
    series never puts words in a real person's mouth and never alters footage of
    a real event, because there are no real people or events in it. The voice
    alone is what triggers the label.
    """

    answer: bool = True
    reason: str = (
        "The narration is a synthetic voice performing a first-person diary, and it is "
        "realistic enough that a listener could reasonably take it for a recording of a "
        "real nurse. That is the situation this disclosure exists for, so the answer is "
        "yes on every episode of this series, without exception and without rechecking."
    )
    synthetic_voice: bool = True
    realistic_synthetic_scene: bool = True
    alters_real_person: bool = False
    alters_real_event: bool = False

    @property
    def form_answer(self) -> str:
        return "Yes" if self.answer else "No"


ALTERED_CONTENT = AlteredContent()


# -------------------------------------------------------------------- chapters


ACT_CHAPTER_LABELS: dict[Act, str] = {
    Act.COLD_OPEN: "cold open",
    Act.SETUP: "handover",
    Act.RISE: "the shift turns",
    Act.CRISIS: "the worst of it",
    Act.FALLOUT: "the car park",
}


@dataclass(frozen=True)
class Chapter:
    """One chapter mark: where it starts, what it is called, which act it is."""

    start: float
    label: str
    act: Act

    @property
    def timestamp(self) -> str:
        total = int(round(self.start))
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def line(self) -> str:
        return f"{self.timestamp} {self.label}"


def chapters(episode: Episode) -> list[Chapter]:
    """A chapter mark at each act boundary, labelled with the clock it opens on.

    The label pairs the in-world time with the act because that is what the
    episode is actually organised by — a viewer scrubbing back to "19:04
    handover" is looking for a moment in a shift, not for act two.
    """
    marks: list[Chapter] = []
    seen: set[Act] = set()
    elapsed = 0.0
    for beat in episode.beats:
        if beat.act not in seen:
            seen.add(beat.act)
            act_label = ACT_CHAPTER_LABELS[beat.act]
            clock = beat.clock.strip()
            label = f"{clock} — {act_label}" if clock else act_label
            # The first mark must be exactly zero or YouTube ignores the list.
            marks.append(Chapter(start=0.0 if not marks else elapsed, label=label, act=beat.act))
        elapsed += beat.duration()[0]
    return marks


# ---------------------------------------------------------------------- titles


_SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+")

# Words that make a title a claim about reality rather than a description of a
# drama. Checked against titles, not against the description, which is required
# to talk about what is and is not real.
TRUTH_CLAIMS = (
    "true story",
    "true events",
    "based on a true",
    "real story",
    "real nurse",
    "real hospital",
    "really happened",
    "actually happened",
    "not fiction",
    "documentary",
    "leaked",
    "actual footage",
    "caught on camera",
    "bodycam",
    "confessions of a",
    "exposed",
    "my story",
)

# The register this channel cannot be in. A title that shouts has to be earned
# by a video that shouts, and this one is a tired woman in a car park.
CLICKBAIT_MARKERS = (
    "you won't believe",
    "you wont believe",
    "gone wrong",
    "shocking",
    "insane",
    "must watch",
    "what happened next",
    "will make you",
    "*",
    "!!",
)

# Tags naming somewhere or something real. Not a whitelist and not a substitute
# for reading the tags — a tripwire for the handful of names most likely to be
# reached for by reflex.
REAL_WORLD_TAG_MARKERS = (
    "true story",
    "true events",
    "true crime",
    "real life",
    "real story",
    "real hospital",
    "real nurse",
    "really happened",
    "documentary",
    "leaked",
    "bodycam",
    "actual footage",
    "nhs",
    "mayo clinic",
    "cleveland clinic",
    "kaiser",
    "johns hopkins",
    "mount sinai",
    "cedars",
    "bellevue",
    "st thomas",
    "great ormond",
)

_TITLE_STOPWORDS = frozenset({"the", "a", "an", "of", "and", "in", "on", "at", "to"})

# Act order for picking a quotable line. The cold open is where the hook lives,
# so it is the first place to look for one; the setup is deliberately last,
# because a title drawn from the ordinary part of the shift promises an ordinary
# episode.
_QUOTE_PREFERENCE = {
    Act.COLD_OPEN: 4,
    Act.FALLOUT: 3,
    Act.CRISIS: 2,
    Act.RISE: 1,
    Act.SETUP: 0,
}

_QUOTE_MIN_WORDS = 4
_QUOTE_MAX_WORDS = 11
_QUOTE_IDEAL_WORDS = 7


def _frame(text: str, number: int) -> str:
    """Put a fragment in its place in the series, without shouting."""
    if text.strip().lower() == SERIES_TITLE.lower():
        return f"{SERIES_TITLE} — Ep. {number}"
    return f"{text} — {SERIES_TITLE}, Ep. {number}"


def _implies_truth(text: str) -> bool:
    low = text.lower()
    return any(claim in low for claim in TRUTH_CLAIMS)


def _quotable(episode: Episode) -> str | None:
    """The best short line Rowan actually says, for a title made of her words."""
    best: tuple[int, int, int] | None = None
    best_text: str | None = None
    for beat in episode.beats:
        for raw in _SENTENCE_SPLIT.split(beat.voiceover):
            sentence = raw.strip().rstrip(".")
            if not sentence or not sentence[0].isalpha():
                continue
            count = len(sentence.split())
            if not _QUOTE_MIN_WORDS <= count <= _QUOTE_MAX_WORDS:
                continue
            if any(char.isdigit() for char in sentence) or _implies_truth(sentence):
                continue
            score = (
                _QUOTE_PREFERENCE[beat.act],
                beat.tension,
                -abs(count - _QUOTE_IDEAL_WORDS),
            )
            if best is None or score > best:
                best, best_text = score, sentence
    return best_text


def _image(episode: Episode) -> str | None:
    """A fragment of a shot description, for a title made of what is on screen."""
    for beat in episode.beats:
        if beat.act is not Act.COLD_OPEN:
            continue
        for raw in beat.shot.description.split(","):
            fragment = raw.strip().rstrip(".")
            words = fragment.split()
            if not 2 <= len(words) <= 6:
                continue
            low = fragment.lower()
            if "cut to" in low or low.startswith("hard cut") or _implies_truth(low):
                continue
            return fragment[0].upper() + fragment[1:]
    return None


def title_variants(episode: Episode) -> list[str]:
    """Two or three titles for the same episode, all of them made of the episode.

    The first is the plain one and doubles as the control arm of the test: the
    episode's own title, which the writer chose. The others are a line from the
    narration and an image from the cold open — different promises about the
    same video, both of which the video keeps.
    """
    candidates = [_frame(episode.title, episode.number)]
    quote = _quotable(episode)
    if quote:
        candidates.append(_frame(f"“{quote}”", episode.number))
    image = _image(episode)
    if image:
        candidates.append(_frame(image, episode.number))

    variants: list[str] = []
    for candidate in candidates:
        if candidate in variants:
            continue
        if len(candidate) > TITLE_LIMIT and variants:
            continue
        variants.append(candidate)
    return variants[:MAX_TITLE_VARIANTS]


# ------------------------------------------------------------------------ tags


BASE_TAGS: tuple[str, ...] = (
    "bay four",
    "fiction",
    "audio drama",
    "serial fiction",
    "night shift",
    "er nurse",
    "hospital drama",
    "voice diary",
    "first person narration",
    "slow burn thriller",
)


def tags(episode: Episode) -> list[str]:
    """Series tags plus whatever this particular episode is about.

    Episode-specific tags come from the title, which is a word the writer chose,
    rather than from keyword mining the narration — which is how a tag list ends
    up claiming things the episode does not contain.
    """
    collected: list[str] = list(BASE_TAGS)
    collected.append(f"bay four episode {episode.number}")

    title_words = [
        word.strip(".,'’").lower()
        for word in episode.title.split()
        if word.strip(".,'’").lower() not in _TITLE_STOPWORDS
    ]
    if len(title_words) > 1:
        collected.append(" ".join(title_words))
    collected.extend(word for word in title_words if len(word) > 5)

    chosen: list[str] = []
    budget = TAGS_CHARACTER_LIMIT
    for tag in collected:
        if tag in chosen or len(chosen) >= MAX_TAGS:
            continue
        cost = len(tag) + (1 if chosen else 0)
        if cost > budget:
            continue
        chosen.append(tag)
        budget -= cost
    return chosen


# ----------------------------------------------------------------- the object


@dataclass(frozen=True)
class Metadata:
    """Everything that goes in a box on the upload page, for one episode."""

    episode_number: int
    titles: list[str]
    description: str
    tags: list[str]
    chapters: list[Chapter]
    altered_content: AlteredContent = ALTERED_CONTENT
    timing_is_estimated: bool = True

    @property
    def title(self) -> str:
        """The variant to publish first. The rest are the test."""
        return self.titles[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode_number,
            "title": self.title,
            "title_variants": list(self.titles),
            "description": self.description,
            "tags": list(self.tags),
            "chapters": [
                {"timestamp": c.timestamp, "seconds": c.start, "label": c.label, "act": c.act.value}
                for c in self.chapters
            ],
            "altered_content": {
                "answer": self.altered_content.form_answer,
                "reason": self.altered_content.reason,
                "synthetic_voice": self.altered_content.synthetic_voice,
                "realistic_synthetic_scene": self.altered_content.realistic_synthetic_scene,
                "alters_real_person": self.altered_content.alters_real_person,
                "alters_real_event": self.altered_content.alters_real_event,
            },
            "timing_is_estimated": self.timing_is_estimated,
        }


def build(episode: Episode) -> Metadata:
    """Publishing metadata for one episode, derived entirely from the episode."""
    marks = chapters(episode)
    estimated = any(beat.duration()[1] is Source.DERIVED for beat in episode.beats)
    return Metadata(
        episode_number=episode.number,
        titles=title_variants(episode),
        description=describe(episode, marks),
        tags=tags(episode),
        chapters=marks,
        altered_content=ALTERED_CONTENT,
        timing_is_estimated=estimated,
    )


def describe(episode: Episode, marks: list[Chapter] | None = None) -> str:
    """The description: heading, disclosures, logline, chapters, closing note.

    The disclosures come second, above the logline, and that ordering is the
    whole point — they are in the three lines a viewer sees before they click
    "more", not below a fold that nobody opens.
    """
    if marks is None:
        marks = chapters(episode)
    heading = f"{SERIES_TITLE} — Episode {episode.number}"
    if episode.title.strip().lower() != SERIES_TITLE.lower():
        heading = f"{heading}: {episode.title}"
    blocks = [heading, disclosure_block()]
    if episode.logline:
        blocks.append(textwrap.fill(" ".join(episode.logline.split()), width=88))
    if marks:
        blocks.append("Chapters\n" + "\n".join(mark.line() for mark in marks))
    blocks.append(textwrap.fill(CLOSING_NOTE, width=88))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------- check


def check(metadata: Metadata) -> list[str]:
    """Everything wrong with this metadata, as sentences. Nothing is rewritten.

    The list is empty when the metadata is publishable. It is deliberately
    ordered by box — titles, description, tags, chapters, disclosure form — so
    that reading it top to bottom walks the upload page.
    """
    problems: list[str] = []
    collapsed = " ".join(metadata.description.split()).lower()

    # ---- titles
    if not metadata.titles:
        problems.append("titles: none generated, so there is nothing to publish")
    elif len(metadata.titles) < MIN_TITLE_VARIANTS:
        problems.append(
            f"titles: only {len(metadata.titles)} variant, "
            f"and an A/B test needs at least {MIN_TITLE_VARIANTS}"
        )
    if len(metadata.titles) > MAX_TITLE_VARIANTS:
        problems.append(
            f"titles: {len(metadata.titles)} variants is more than the "
            f"{MAX_TITLE_VARIANTS} anyone will actually compare"
        )
    for title in metadata.titles:
        low = title.lower()
        for claim in TRUTH_CLAIMS:
            if claim in low:
                problems.append(
                    f"title {title!r} implies this is true: it contains {claim!r}, and the "
                    "series is fiction in every description and on a card on screen"
                )
        for marker in CLICKBAIT_MARKERS:
            if marker in low:
                problems.append(
                    f"title {title!r} is out of register: {marker!r} belongs to a "
                    "different kind of channel"
                )
        if len(title) > TITLE_LIMIT:
            problems.append(
                f"title {title!r} is {len(title)} characters, over the {TITLE_LIMIT} limit"
            )
        if title.strip() != title:
            problems.append(f"title {title!r} has leading or trailing whitespace")

    # ---- description
    if not collapsed:
        problems.append("description: empty")
    for disclosure in REQUIRED_DISCLOSURES:
        where = disclosure.found_at(collapsed)
        if where < 0:
            problems.append(
                f"description: missing the {disclosure.key!r} disclosure — it must say, "
                f"in its own words: {disclosure.text}"
            )
        elif where > DISCLOSURE_WINDOW:
            problems.append(
                f"description: the {disclosure.key!r} disclosure is buried at character "
                f"{where}; disclosures belong in the first {DISCLOSURE_WINDOW}, above the fold"
            )
    if len(metadata.description) > DESCRIPTION_LIMIT:
        problems.append(
            f"description: {len(metadata.description)} characters, "
            f"over the {DESCRIPTION_LIMIT} limit"
        )

    # ---- tags
    for tag in metadata.tags:
        low = tag.lower()
        for marker in REAL_WORLD_TAG_MARKERS:
            if marker in low:
                problems.append(
                    f"tag {tag!r} implies something real: {marker!r} claims a place or an "
                    "event outside the fiction"
                )
        if low != tag:
            problems.append(f"tag {tag!r} should be lower case; tags are not titles")
    joined = len(",".join(metadata.tags))
    if joined > TAGS_CHARACTER_LIMIT:
        problems.append(f"tags: {joined} characters total, over the {TAGS_CHARACTER_LIMIT} limit")
    if len(set(metadata.tags)) != len(metadata.tags):
        problems.append("tags: contains duplicates")

    # ---- chapters
    if len(metadata.chapters) < MIN_CHAPTERS:
        problems.append(
            f"chapters: {len(metadata.chapters)} marks, and YouTube needs at least "
            f"{MIN_CHAPTERS} before it shows any of them"
        )
    if metadata.chapters:
        if metadata.chapters[0].start != 0.0:
            problems.append(
                f"chapters: the first mark is at {metadata.chapters[0].timestamp}; it has to "
                "be at 0:00 or the whole list is ignored"
            )
        previous = metadata.chapters[0]
        for mark in metadata.chapters[1:]:
            gap = mark.start - previous.start
            if gap < MIN_CHAPTER_SECONDS:
                problems.append(
                    f"chapters: {previous.label!r} runs {gap:.0f}s before {mark.label!r}, "
                    f"under the {MIN_CHAPTER_SECONDS:.0f}s minimum"
                )
            previous = mark
        for mark in metadata.chapters:
            if mark.line().lower() not in collapsed:
                problems.append(
                    f"chapters: {mark.line()!r} is not in the description, so YouTube will "
                    "never see it"
                )

    # ---- the altered-content form
    disclosure_form = metadata.altered_content
    if not disclosure_form.answer:
        problems.append(
            "altered content: answered No, but the narration is a synthetic voice of a "
            "realistic person, which is exactly what the disclosure is for"
        )
    if not disclosure_form.reason.strip():
        problems.append(
            "altered content: answered Yes with no reason recorded, which leaves the "
            "uploader guessing next time"
        )
    if disclosure_form.alters_real_person or disclosure_form.alters_real_event:
        problems.append(
            "altered content: claims to alter a real person or a real event, which would "
            "break the first boundary in the bible — nothing in this series is real"
        )

    return problems
