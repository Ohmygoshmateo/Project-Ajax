"""Reading a script against the bible, and refusing to fix it.

Two kinds of rule live here and they are not the same kind of thing.

**Structure** is craft. Five acts in order, a hook that lands inside fifteen
seconds, a tension curve that peaks where the crisis is. Most of these are errors
because a script that fails them is not the format; runtime is a warning, because
episode 1 really is short and a short episode is a note to the writer, not a
broken build.

**Boundaries** are risk. No dosages, no instructional second person, nothing
graphic. Those are errors without exception — the channel does not survive being
mistaken for medical advice, and the bible is explicit that the linter is what
enforces the part a linter can reach.

Nothing here mutates a script. A validator that silently rewrites your writing is
worse than no validator, because you stop reading the output.

The compliance rules are also written against the *opposite* failure. A rule that
fires on "the thing you have to understand about bay four" or "her blood pressure
was fine" gets switched off within a week, and a switched-off rule protects
nothing. So the patterns below are narrow on purpose, matched a sentence at a
time, and several are deliberately paired with a required clinical context word.
Every one has a near-miss test standing next to it in ``tests/test_validate.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ajax_studio.model import (
    ACT_ORDER,
    COLD_OPEN_CEILING_S,
    HOOK_DEADLINE_S,
    RUNTIME_CEILING_S,
    RUNTIME_FLOOR_S,
    Act,
    Episode,
)
from ajax_studio.timeline import build, mmss


class Level(str, Enum):
    """Two levels only. A third would be ignored, which is the same as absent."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """One thing wrong, addressed to whoever has to fix it."""

    level: Level
    code: str
    message: str
    beat_id: str | None = None

    @property
    def is_error(self) -> bool:
        return self.level is Level.ERROR


@dataclass
class Report:
    """Everything the validator found. Empty is the good case and says so."""

    episode_number: int
    episode_title: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level is Level.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level is Level.WARNING]

    @property
    def ok(self) -> bool:
        """No errors. Warnings do not fail a build — that is the whole point of them."""
        return not self.errors

    @property
    def clean(self) -> bool:
        return not self.findings

    def codes(self) -> set[str]:
        return {f.code for f in self.findings}


# --- sentence splitting -----------------------------------------------------
#
# Every compliance rule matches inside a single sentence. Across a full stop you
# get pairs like "I could teach that. I have taught that." landing in the same
# window as an unrelated clinical noun, and that is precisely the false positive
# that discredits the whole file. YAML folds paragraphs onto one line, so the
# newline case matters too.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


# --- boundary 3: no dosages -------------------------------------------------
#
# Units are enumerated rather than pattern-matched. Bare "g" and "l" would fire
# on "6 g" in a caption or any stray letter after a number, and this series
# writes numbers out in words anyway, so the cost of being specific is zero.
#
# Spelled-out numbers are included for the written-out unit words only. "Ten
# milligrams" is exactly how a dose gets past a digit-hunting regex, and it is
# unambiguous. Digits-plus-"units" is covered; *words*-plus-"units" is not,
# because "on the unit" is how everyone in this series refers to the ward.
_NUMBER_WORD = (
    r"(?:a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|half)"
)
_ABBREVIATED_UNIT = r"(?:mg|mcg|µg|ug|ml|cc|iu|mmol|meq)"
_SPELLED_UNIT = (
    r"(?:milligram|microgram|millilitre|milliliter|millimol|gram)s?"
)

DOSAGE_RULES: tuple[tuple[str, str], ...] = (
    (r"(?<![\w/.])\d+(?:[.,]\d+)?\s*" + _ABBREVIATED_UNIT + r"\b", "a number with a dose unit"),
    (r"(?<![\w-])(?:\d+|" + _NUMBER_WORD + r")[\s-]*" + _SPELLED_UNIT + r"\b",
     "a number with a spelled-out dose unit"),
    (r"(?<![\w/.])\d+(?:[.,]\d+)?\s*units?\b", "a number of units"),
    (r"\bper\s+kilo(?:gram)?\b|\bper\s+kg\b|/\s*kg\b", "a per-kilogram rate"),
)

# --- boundary 3: no instructional second person -----------------------------
#
# Rowan talks to the microphone constantly — "the thing you have to understand",
# "that's what you do", "I want you to know". Second person alone is therefore
# useless as a signal, so the ambiguous frames require a clinical word in the
# same sentence and the unambiguous ones do not.
#
# "you have to" is absent from the frames deliberately: it appears in the opening
# line of episode 1 and means "listen", not "do this".
_CLINICAL = (
    r"(?:symptom|chest pain|\bpain\b|bleed|dose|dosage|drug|medicat|tablet|pill|inject|"
    r"treat|therapy|diagnos|dizz|nausea|vomit|seizure|stroke|cardiac|artery|overdose|"
    r"blood pressure|pulse|airway|compression|antibiotic|allerg|prescri|paramedic|"
    r"emergency room|\ba and e\b|hospital)"
)

# (trigger pattern, requires a clinical word in the same sentence, description)
INSTRUCTIONAL_RULES: tuple[tuple[str, bool, str], ...] = (
    # "you should have seen the printer" is narration; "you should" alone is not.
    (r"\byou (?:should|must|ought to|need to)\b(?!\s*(?:have|'ve)\b)", True,
     "second-person advice"),
    (r"\bif you(?:'re| are| ever)?\s*(?:have|had|get|feel|felt|notice|experience|think)\b", True,
     "a second-person conditional"),
    # Sentence-initial imperative only. Mid-sentence "I'll take this one" is a
    # nurse assigning herself a patient, and it is everywhere in this series.
    (r"^(?:so|now|then|and|but|please|first|next|okay)?[,\s]*"
     r"\b(?:take|swallow|inject|apply|administer|give|start taking|stop taking)\b", True,
     "an imperative aimed at the viewer"),
    (r"\bthe (?:treatment|dose|dosage|correct dose|recommended dose) (?:is|for|would be)\b", False,
     "a stated treatment or dose"),
    (r"\bwhat (?:you|to) do (?:if|when)\b", False, "a what-to-do-if construction"),
    (r"\b(?:never|don't|do not) (?:take|give|inject|swallow|administer)\b", False,
     "a second-person prohibition"),
)

# --- boundary: nothing graphic ----------------------------------------------
#
# "Blood" is not graphic — blood pressure, bloods, a blood gas are the ordinary
# vocabulary of the setting and firing on them would make the rule useless. What
# is graphic is blood as *spectacle*, so that rule needs a spectacle verb in the
# same sentence. The second list is words with no non-graphic reading at all.
_GRAPHIC_SPECTACLE = (
    r"(?:spurt|gush|spray|sprayed|pooling|pooled|soak|drench|everywhere|all over|"
    r"running down|dripping off|puddle)"
)
_BLOOD = r"(?:blood(?!\s*(?:pressure|sugar|gas|test|count|work))|bleeding out|haemorrhag|hemorrhag)"

GRAPHIC_RULES: tuple[tuple[str, bool, str], ...] = (
    (_BLOOD, True, "blood described as spectacle"),
    (r"\b(?:deglov|eviscerat|disembowel|dismember|entrails|viscera|intestines|bowel spill|"
     r"(?:exposed|protruding|shattered|splintered) bone|"
     r"bone (?:\w+ ){0,2}(?:protrud|sticking out|exposed|visible)|brain matter|"
     r"skull (?:\w+ ){0,2}(?:caved|crushed|open)|gaping (?:wound|hole)|"
     r"severed (?:limb|arm|leg|hand|finger|head))",
     False, "an anatomically graphic description"),
)


def _search_sentence(pattern: str, sentence: str) -> re.Match[str] | None:
    return re.search(pattern, sentence, flags=re.IGNORECASE)


def _compliance_findings(episode: Episode) -> list[Finding]:
    """Boundary rules, sentence by sentence, over voiceover text only.

    Shot descriptions and captions are out of scope here: they are production
    notes, not something a viewer hears, and the graphic rule for imagery belongs
    to whoever buys the footage.
    """
    findings: list[Finding] = []
    for beat in episode.beats:
        for sentence in _sentences(beat.voiceover):
            for pattern, description in DOSAGE_RULES:
                match = _search_sentence(pattern, sentence)
                if match:
                    findings.append(
                        Finding(
                            Level.ERROR,
                            "boundary-dosage",
                            f"{description}: {match.group(0).strip()!r} — the bible forbids "
                            "drug doses outright, however fictional the drug.",
                            beat.beat_id,
                        )
                    )
            for pattern, needs_clinical, description in INSTRUCTIONAL_RULES:
                match = _search_sentence(pattern, sentence)
                if not match:
                    continue
                if needs_clinical and not _search_sentence(_CLINICAL, sentence):
                    continue
                findings.append(
                    Finding(
                        Level.ERROR,
                        "boundary-instructional",
                        f"{description}: {_excerpt(sentence)} — this reads as advice a viewer "
                        "could act on.",
                        beat.beat_id,
                    )
                )
            for pattern, needs_spectacle, description in GRAPHIC_RULES:
                match = _search_sentence(pattern, sentence)
                if not match:
                    continue
                if needs_spectacle and not _search_sentence(_GRAPHIC_SPECTACLE, sentence):
                    continue
                findings.append(
                    Finding(
                        Level.ERROR,
                        "boundary-graphic",
                        f"{description}: {_excerpt(sentence)} — the horror is supposed to be "
                        "in the narration, not the anatomy.",
                        beat.beat_id,
                    )
                )
    return findings


def _excerpt(sentence: str, limit: int = 72) -> str:
    flat = " ".join(sentence.split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return repr(flat)


def _structure_findings(episode: Episode) -> list[Finding]:
    """The bible's "Episode shape", as far as arithmetic can check it."""
    findings: list[Finding] = []
    beats = episode.beats

    # Acts: present, and in one contiguous run each, in the bible's order.
    runs: list[Act] = []
    for beat in beats:
        if not runs or runs[-1] is not beat.act:
            runs.append(beat.act)
    present = set(runs)

    for act in ACT_ORDER:
        if act not in present:
            findings.append(
                Finding(
                    Level.ERROR,
                    "act-missing",
                    f"no beats in act {act.value!r}; every episode uses all five.",
                )
            )
    if len(runs) != len(present):
        repeated = sorted({a.value for a in runs if runs.count(a) > 1})
        findings.append(
            Finding(
                Level.ERROR,
                "act-interleaved",
                f"beats leave and re-enter {', '.join(repeated)}; each act is one block.",
            )
        )
    elif runs != [a for a in ACT_ORDER if a in present]:
        order = " → ".join(a.value for a in runs)
        findings.append(
            Finding(
                Level.ERROR,
                "act-out-of-order",
                f"acts run {order}; the shape is cold_open → setup → rise → crisis → fallout.",
            )
        )

    # Runtime and per-act spans come off the schedule, not off raw word counts,
    # because the gaps are part of what the viewer sits through.
    timeline = build(episode)
    runtime = timeline.runtime()

    if runtime.delta_to_target_s != 0.0:
        code = "runtime-short" if runtime.delta_to_target_s < 0 else "runtime-long"
        findings.append(
            Finding(
                Level.WARNING,
                code,
                f"derived runtime {mmss(runtime.total_seconds)} "
                f"({mmss(runtime.narration_seconds)} narration + {mmss(runtime.gap_seconds)} "
                f"gaps) is {runtime.verdict}; target is "
                f"{mmss(RUNTIME_FLOOR_S)}–{mmss(RUNTIME_CEILING_S)}.",
            )
        )

    cold_open = timeline.act_seconds(Act.COLD_OPEN)
    if cold_open > COLD_OPEN_CEILING_S:
        findings.append(
            Finding(
                Level.ERROR,
                "cold-open-long",
                f"cold open runs {cold_open:.1f}s derived, over the "
                f"{COLD_OPEN_CEILING_S:.0f}s ceiling; it is thirty seconds with no context.",
            )
        )

    if timeline.cues:
        hook = timeline.cues[0]
        if hook.end > HOOK_DEADLINE_S:
            findings.append(
                Finding(
                    Level.ERROR,
                    "hook-late",
                    f"first beat ends at {hook.end:.1f}s {hook.source.value}, past the "
                    f"{HOOK_DEADLINE_S:.0f}s hook deadline; cut it or split it.",
                    hook.beat_id,
                )
            )

    findings.extend(_tension_findings(episode))

    if not episode.cliffhanger.strip():
        findings.append(
            Finding(
                Level.ERROR,
                "no-cliffhanger",
                "'cliffhanger' is empty; every episode ends unresolved, on a question the "
                "audience now holds.",
            )
        )

    return findings


def _tension_findings(episode: Episode) -> list[Finding]:
    """The curve: peaks in the crisis, and does not flatten across the rise."""
    findings: list[Finding] = []
    grouped = episode.acts()
    crisis = grouped[Act.CRISIS]
    rise = grouped[Act.RISE]

    if crisis:
        crisis_peak = max(b.tension for b in crisis)
        # `>=`, not `>`. The cold open *is* the crisis out of order and the
        # fallout is allowed to match it — episode 1 ties at 5 in all three, and
        # that is the format working, not a fault.
        elsewhere = [
            (act, max(b.tension for b in group))
            for act, group in grouped.items()
            if group and act is not Act.CRISIS
        ]
        higher = [act.value for act, peak in elsewhere if peak > crisis_peak]
        if higher:
            findings.append(
                Finding(
                    Level.ERROR,
                    "tension-peak-misplaced",
                    f"crisis peaks at {crisis_peak} but {', '.join(higher)} goes higher; "
                    "the worst moment belongs in the crisis.",
                )
            )

    if len(rise) >= 2:
        readings = [b.tension for b in rise]
        # Two ways to flatten: never change, or end no higher than you started.
        # Either one means the audience is not being wound up by the middle of
        # the episode, which is the only job the rise has.
        if len(set(readings)) < 2 or readings[-1] <= readings[0]:
            curve = "→".join(str(r) for r in readings)
            findings.append(
                Finding(
                    Level.ERROR,
                    "tension-flat",
                    f"rise tension goes {curve}; it has to climb, and it has to end higher "
                    "than it started.",
                )
            )

    return findings


def check(episode: Episode) -> Report:
    """Validate an episode. Reads only; the script is never touched.

    Structural findings come first because they are the ones a writer acts on;
    boundary findings come last because they stop the build regardless of order.
    """
    return Report(
        episode_number=episode.number,
        episode_title=episode.title,
        findings=_structure_findings(episode) + _compliance_findings(episode),
    )


GOLD = "#C8A951"
DIM = "#8A93A8"
FAINT = "#5C6580"

LEVEL_STYLE = {Level.ERROR: "red", Level.WARNING: "yellow3"}
LEVEL_GLYPH = {Level.ERROR: "✗", Level.WARNING: "!"}


def render(report: Report, console: Console | None = None) -> None:
    """Print findings grouped by level, errors first, with a clean-run state."""
    console = console or Console()

    title = Text()
    title.append(f"EP{report.episode_number:02d}  ", style=f"bold {GOLD}")
    title.append(report.episode_title, style="bold")
    title.append("  ·  validate", style=FAINT)
    console.print()
    console.print(title)
    console.print()

    if report.clean:
        console.print(Text("  ✓ no findings — structure and boundaries both clean.", style="green"))
        console.print()
        return

    for level in (Level.ERROR, Level.WARNING):
        group = [f for f in report.findings if f.level is level]
        if not group:
            continue
        style = LEVEL_STYLE[level]
        console.print(
            Text(f"  {len(group)} {level.value}{'s' if len(group) != 1 else ''}",
                 style=f"bold {style}")
        )
        table = Table(box=None, pad_edge=False, show_header=False, padding=(0, 1))
        table.add_column("glyph", style=style, width=1)
        table.add_column("code", style=style, no_wrap=True)
        table.add_column("beat", style=FAINT, no_wrap=True)
        table.add_column("message", overflow="fold")
        for finding in group:
            table.add_row(
                LEVEL_GLYPH[level],
                finding.code,
                finding.beat_id or "—",
                finding.message,
            )
        console.print(table)
        console.print()

    if report.ok:
        console.print(
            Text("  no errors — warnings are notes to the writer, not a failed build.",
                 style=FAINT)
        )
    else:
        console.print(Text("  errors block the build.", style=f"bold {LEVEL_STYLE[Level.ERROR]}"))
    console.print()
