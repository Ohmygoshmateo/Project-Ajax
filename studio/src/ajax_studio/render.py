"""Previz: a watchable animatic, built before anything has been bought.

The owner has no voice subscription and no footage. The tempting move is to wait
for both and rewrite the script in the meantime on faith. This module exists so
that is unnecessary: it renders one card per beat at the beat's real duration,
muxes them into an MP4 with a matching silent track, and hands back something you
can scrub. Pacing is the thing that gets fixed cheapest before assets exist and
most expensively after, so the pipeline puts a cut in front of the writer on day
one.

Three rules shape everything below.

**It must be a real file.** Not an HTML preview, not a contact sheet — an H.264
MP4 with an audio stream, because the point is to watch it in a player, drop it in
an editor, and send it to someone. That is why there is a silent track at all: a
video with no audio stream behaves badly in exactly the tools a cut gets judged
in.

**It must never pass for finished.** Every placeholder frame carries a hatched
overlay and a striped band reading PLACEHOLDER, and every frame — asset or not —
prints its duration next to the word ``derived``, ``authored`` or ``measured``. A
previz cut leaking out as a finished one is the failure mode worth engineering
against, so the marking is deliberately ugly.

**Real assets displace cards one at a time.** Art arrives in dribs. A beat whose
shot names an image that actually exists gets that image, letterboxed; its
neighbours keep their cards. There is never a moment where the pipeline stops
working because the asset set is half done.

The frames are stills and stay stills. ``Shot.motion`` is printed on the card
rather than animated: a Ken Burns push on a placeholder card would add encode time
and production sheen to a frame whose entire job is to look unfinished.

Timings come from :meth:`Beat.duration`, or from a schedule the caller computed
elsewhere — see :func:`build_cues`.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ajax_studio.model import ACT_ORDER, Act, Beat, Episode, Source
from ajax_studio.voice import (
    CHANNEL_LAYOUT,
    SAMPLE_RATE,
    measure_duration,
    run_ffmpeg,
)

RGB = tuple[int, int, int]

FRAME: tuple[int, int] = (1920, 1080)
VERTICAL: tuple[int, int] = (1080, 1920)

# Stills only, so frame rate buys nothing visually. 24 is chosen because it is
# what editors and players expect to see in the metadata, and static content
# costs x264 almost nothing per duplicated frame.
DEFAULT_FPS = 24

# ----------------------------------------------------------------- the palette
# Straight from the bible's "Look and sound": sodium-orange corridor light,
# clinical green, night blue, high contrast, deep blacks.
SODIUM: RGB = (233, 138, 51)
CLINICAL: RGB = (104, 214, 163)
NIGHT: RGB = (26, 40, 68)
BLACK: RGB = (7, 8, 11)
DAWN: RGB = (108, 122, 146)
BONE: RGB = (233, 236, 242)
FAINT: RGB = (126, 137, 158)


@dataclass(frozen=True)
class ActStyle:
    """How one act looks. The five differ enough to be told apart while scrubbing.

    That is the requirement: dragging the playhead across a ten-minute animatic
    should make the five-act shape obvious without reading a word. So each act
    owns a ground colour, an accent, and a vignette weight, and the act ladder in
    the corner says where you are.
    """

    label: str
    ground_top: RGB
    ground_bottom: RGB
    accent: RGB
    vignette: float
    sound: str


# ``sound`` is the bible's sound design, printed on the card. Previz has no audio
# to give, but the intent is part of the pacing judgement — the crisis reads
# differently when you remember it plays silent.
ACT_STYLES: dict[Act, ActStyle] = {
    Act.COLD_OPEN: ActStyle(
        label="COLD OPEN",
        ground_top=(28, 16, 8),
        ground_bottom=BLACK,
        accent=SODIUM,
        vignette=0.85,
        sound="music under · room tone",
    ),
    Act.SETUP: ActStyle(
        label="SETUP",
        ground_top=NIGHT,
        ground_bottom=(12, 18, 32),
        accent=SODIUM,
        vignette=0.35,
        sound="room tone · distant monitors",
    ),
    Act.RISE: ActStyle(
        label="RISE",
        ground_top=(14, 34, 40),
        ground_bottom=(8, 16, 24),
        accent=CLINICAL,
        vignette=0.5,
        sound="room tone · monitors · a door",
    ),
    Act.CRISIS: ActStyle(
        label="CRISIS",
        ground_top=(4, 5, 7),
        ground_bottom=(2, 2, 3),
        accent=CLINICAL,
        vignette=0.95,
        sound="SILENCE — no music",
    ),
    Act.FALLOUT: ActStyle(
        label="FALLOUT",
        ground_top=(44, 52, 66),
        ground_bottom=(18, 22, 30),
        accent=DAWN,
        vignette=0.2,
        sound="music under · engine off",
    ),
}


# --------------------------------------------------------------------- timings


@dataclass(frozen=True)
class Cue:
    """One beat, placed on a clock. The unit the renderer actually consumes."""

    index: int          # 1-based position in the rendered cut
    beat: Beat
    start: float
    duration: float
    source: Source

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def timing_label(self) -> str:
        """Duration with its provenance. Never printed without the provenance."""
        return f"{self.duration:.1f}s {self.source.value}"


def build_cues(
    beats: Sequence[Beat],
    schedule: Sequence[tuple[Beat, float, float]] | None = None,
    *,
    measured: Mapping[str, float] | None = None,
    sources: Mapping[str, Source] | None = None,
) -> list[Cue]:
    """Lay ``beats`` out on a timeline.

    Without a schedule the timings are computed here, cumulatively, from
    :meth:`Beat.duration` — with ``measured`` seconds substituted in where a real
    recording exists, because measurement outranks arithmetic.

    With a schedule, the ``(beat, start, duration)`` tuples are taken as given.
    Those tuples carry no provenance, so it is recovered from the beat itself
    (and from ``measured``); a caller that reshaped durations for its own reasons
    should pass ``sources`` to say what they now are, rather than let the frame
    print a label that is no longer true.
    """
    measured = measured or {}
    sources = sources or {}

    if schedule is not None:
        return [
            Cue(
                index=position,
                beat=beat,
                start=float(start),
                duration=float(duration),
                source=_provenance(beat, measured, sources),
            )
            for position, (beat, start, duration) in enumerate(schedule, start=1)
        ]

    cues: list[Cue] = []
    clock = 0.0
    for position, beat in enumerate(beats, start=1):
        duration, source = beat.duration(measured.get(beat.beat_id))
        cues.append(Cue(position, beat, clock, duration, source))
        clock += duration
    return cues


def _provenance(
    beat: Beat, measured: Mapping[str, float], sources: Mapping[str, Source]
) -> Source:
    if beat.beat_id in sources:
        return sources[beat.beat_id]
    if beat.beat_id in measured:
        return Source.MEASURED
    return beat.duration()[1]


# ------------------------------------------------------------------ typography


_FONT_FILES: dict[str, tuple[str, ...]] = {
    # Absolute paths first, then bare names so Pillow's own font search can find
    # them on a machine that puts DejaVu somewhere else. Everything falls back to
    # the bundled default, because no font can be downloaded here.
    "regular": ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"),
    "bold": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"),
    "mono": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "DejaVuSansMono-Bold.ttf",
    ),
}


@lru_cache(maxsize=64)
def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A font at ``size``, degrading rather than failing.

    The cards are legible with the default bitmap font and handsome with DejaVu;
    neither outcome should be able to break a render, so every step is optional.
    """
    for candidate in _FONT_FILES.get(weight, ()):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 cannot scale the default font
        return ImageFont.load_default()


@lru_cache(maxsize=128)
def _line_height(font_key: tuple[int, str]) -> int:
    """Measured, not assumed — the default font's metrics differ from DejaVu's."""
    font = _font(*font_key)
    # "AgÅy" spans ring to descender, so the measure holds for accented text too.
    box = font.getbbox("AgÅy")
    return int((box[3] - box[1]) * 1.42) or 12


def _width(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> float:
    return font.getlength(text)


def _wrap(
    text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: float
) -> list[str]:
    """Greedy wrap on measured pixel width, so it holds in both orientations."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and _width(font, trial) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
        # A single unbreakable token wider than the frame is hard-split rather
        # than allowed to run off the edge.
        while _width(font, current) > max_width and len(current) > 1:
            cut = max(1, int(len(current) * max_width / max(_width(font, current), 1.0)))
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    return lines or [""]


def _fit(
    text: str,
    max_width: float,
    max_lines: int,
    sizes: Sequence[int],
    weight: str = "regular",
) -> tuple[tuple[int, str], list[str]]:
    """Largest of ``sizes`` whose wrap fits in ``max_lines``, else truncate.

    Subtitles have to stay readable, and a beat with sixty words in it is a
    pacing problem the writer should see rather than a rendering problem to hide.
    """
    for size in sizes:
        key = (size, weight)
        lines = _wrap(text, _font(*key), max_width)
        if len(lines) <= max_lines:
            return key, lines
    key = (sizes[-1], weight)
    lines = _wrap(text, _font(*key), max_width)[:max_lines]
    if lines:
        lines[-1] = lines[-1].rstrip() + " …"
    return key, lines


def _two_column_line(
    draw: ImageDraw.ImageDraw,
    left: str,
    right: str,
    sizes: Sequence[int],
    box: tuple[int, int, int],
    fill: RGB | tuple[int, int, int, int],
    weight: str = "bold",
) -> None:
    """Draw ``left`` flush left and ``right`` flush right on one line.

    Sized down until both fit, because the same card is rendered at 1920 wide and
    at 1080 wide and the vertical cut must not overlap its own footer.
    """
    x0, x1, y = box
    available = x1 - x0
    key = (sizes[-1], weight)
    for size in sizes:
        candidate = (size, weight)
        font = _font(*candidate)
        gap = size * 1.5
        if _width(font, left) + _width(font, right) + gap <= available:
            key = candidate
            break
    font = _font(*key)
    draw.text((x0, y), left, font=font, fill=fill)
    draw.text((x1 - _width(font, right), y), right, font=font, fill=fill)


# ---------------------------------------------------------------- frame layout


@dataclass(frozen=True)
class Metrics:
    """Card geometry, derived from the frame size.

    Averaging width and height means 1920×1080 and 1080×1920 produce identical
    type sizes — the vertical cut is the same design reflowed, not a second
    design to keep in sync.
    """

    width: int
    height: int
    margin: int
    subject: int
    subtitle: int
    label: int
    clock: int
    band: int

    @classmethod
    def for_size(cls, size: tuple[int, int]) -> Metrics:
        width, height = size
        unit = (width + height) / 2
        return cls(
            width=width,
            height=height,
            margin=round(unit * 0.048),
            subject=round(unit * 0.040),
            subtitle=round(unit * 0.027),
            label=round(unit * 0.0155),
            clock=round(unit * 0.046),
            band=round(unit * 0.050),
        )

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.margin


@dataclass(frozen=True)
class CardContext:
    """The parts of a frame that come from the cut rather than from the beat."""

    episode_label: str
    total_cues: int
    total_seconds: float
    base_dir: Path | None = None
    kicker: str = "PREVIZ ANIMATIC"

    @classmethod
    def for_episode(
        cls,
        episode: Episode,
        cues: Sequence[Cue],
        *,
        base_dir: Path | None = None,
        kicker: str = "PREVIZ ANIMATIC",
    ) -> CardContext:
        base = base_dir or (episode.source_path.parent if episode.source_path else None)
        return cls(
            episode_label=f"EP{episode.number:02d} · {episode.title.upper()}",
            total_cues=len(cues),
            total_seconds=sum(cue.duration for cue in cues),
            base_dir=base,
            kicker=kicker,
        )


@dataclass
class Card:
    """A rendered frame and what it turned out to be."""

    image: Image.Image
    used_asset: bool
    missing_asset: bool
    asset_path: Path | None

    @property
    def is_placeholder(self) -> bool:
        return not self.used_asset

    @property
    def state_label(self) -> str:
        """The words on the band. Asserted in the tests, so it is API."""
        if self.used_asset and self.asset_path is not None:
            return f"REAL ASSET · {self.asset_path.name}"
        if self.missing_asset and self.asset_path is not None:
            return f"ASSET MISSING · {self.asset_path.name}"
        return "PLACEHOLDER CARD · NO ASSET"


# ------------------------------------------------------------------- ingredients


@lru_cache(maxsize=16)
def _ground(size: tuple[int, int], top: RGB, bottom: RGB) -> Image.Image:
    """A vertical wash. Corridor light falls from above; flat fill reads as slides."""
    width, height = size
    column = Image.new("RGB", (1, height))
    pixels = column.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        pixels[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
    return column.resize(size, Image.Resampling.BILINEAR)


@lru_cache(maxsize=16)
def _vignette(size: tuple[int, int], strength: int) -> Image.Image:
    """Cached darkening mask. Deep blacks at the edge are half the look."""
    scale = strength / 100.0
    radial = Image.radial_gradient("L").resize(size, Image.Resampling.BILINEAR)
    return radial.point(lambda value: int(value * scale))


def _open_asset(reference: str, base_dir: Path | None) -> tuple[Path, Image.Image | None]:
    """Resolve and load a shot's asset. Returns the path even when it fails.

    The path is returned regardless so a missing asset can be named on the frame:
    silently drawing a card for a shot the writer believes is finished is how a
    typo survives to the grade.
    """
    path = Path(reference).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.is_file():
        return path, None
    try:
        image = Image.open(path)
        image.load()
        return path, image.convert("RGB")
    except (OSError, ValueError):
        # Pillow raises a family of errors for "this is not an image"; none of
        # them should stop a twenty-beat render.
        return path, None


def _letterbox(base: Image.Image, art: Image.Image) -> None:
    """Fit ``art`` inside ``base`` preserving aspect. Never crop, never stretch.

    Cropping a previz frame would hide what the real asset does at the edges,
    which is one of the things the writer is looking at.
    """
    frame_w, frame_h = base.size
    art_w, art_h = art.size
    scale = min(frame_w / art_w, frame_h / art_h)
    new = art.resize((max(1, round(art_w * scale)), max(1, round(art_h * scale))),
                     Image.Resampling.LANCZOS)
    base.paste(new, ((frame_w - new.width) // 2, (frame_h - new.height) // 2))


def _hatch(draw: ImageDraw.ImageDraw, size: tuple[int, int], accent: RGB) -> None:
    """Diagonal stripes across the whole frame: the primary placeholder marking.

    Chosen because it survives being scaled to a thumbnail and being screenshotted
    into a chat window, which are the two ways a previz frame escapes.
    """
    width, height = size
    step = max(28, width // 46)
    stripe = (*accent, 26)
    for x in range(-height, width + height, step):
        draw.line([(x, height), (x + height, 0)], fill=stripe, width=2)


def _act_ladder(draw: ImageDraw.ImageDraw, metrics: Metrics, act: Act, accent: RGB) -> int:
    """Five blocks, current one lit. Reads the five-act shape at a glance."""
    block_w = round(metrics.width * 0.021)
    gap = max(4, block_w // 5)
    tall = max(6, round(metrics.label * 0.55))
    short = max(3, tall // 2)
    x = metrics.margin
    y = metrics.margin
    current = ACT_ORDER.index(act)
    for position, _ in enumerate(ACT_ORDER):
        height = tall if position == current else short
        top = y + (tall - height)
        box = (x, top, x + block_w, y + tall)
        if position == current:
            draw.rectangle(box, fill=accent)
        elif position < current:
            draw.rectangle(box, fill=(*accent, 120))
        else:
            draw.rectangle(box, outline=(*FAINT, 110), width=1)
        x += block_w + gap
    return y + tall


def _tension_pips(draw: ImageDraw.ImageDraw, metrics: Metrics, x: int, y: int,
                  tension: int, accent: RGB) -> None:
    """The writer's own 1-5 reading, drawn so the curve is visible while scrubbing."""
    size = max(6, round(metrics.label * 0.5))
    gap = max(3, size // 2)
    for level in range(1, 6):
        box = (x, y, x + size, y + size)
        if level <= tension:
            draw.rectangle(box, fill=accent)
        else:
            draw.rectangle(box, outline=(*FAINT, 110), width=1)
        x += size + gap


def _text_block(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[str],
    font_key: tuple[int, str],
    x: int,
    y: int,
    fill: RGB | tuple[int, int, int, int],
    *,
    centre_width: int | None = None,
) -> int:
    """Draw wrapped lines and return the y below them."""
    font = _font(*font_key)
    step = _line_height(font_key)
    for line in lines:
        left = x if centre_width is None else x + (centre_width - _width(font, line)) / 2
        draw.text((left, y), line, font=font, fill=fill)
        y += step
    return y


def _small_caps(draw: ImageDraw.ImageDraw, text: str, font_key: tuple[int, str],
                x: int, y: int, fill: RGB | tuple[int, int, int, int]) -> None:
    """Letterspaced upper-case labels — the one bit of chrome the cards allow."""
    font = _font(*font_key)
    for character in text.upper():
        draw.text((x, y), character, font=font, fill=fill)
        x += _width(font, character) + max(1, font_key[0] * 0.14)


# ------------------------------------------------------------------ the card


def draw_card(cue: Cue, context: CardContext, size: tuple[int, int] = FRAME) -> Card:
    """One frame for one beat.

    Layout, top to bottom: act ladder and act name; the clock card in the corner
    as the bible specifies; the shot description as the subject line; the beat's
    caption in a box if it has one; the voiceover as subtitles in the lower third;
    then a footer of intent and a state band that says what this frame is.
    """
    metrics = Metrics.for_size(size)
    style = ACT_STYLES[cue.beat.act]
    beat = cue.beat

    asset_path: Path | None = None
    art: Image.Image | None = None
    if beat.shot.asset:
        asset_path, art = _open_asset(beat.shot.asset, context.base_dir)

    base = _ground(size, style.ground_top, style.ground_bottom).copy()
    if art is not None:
        _letterbox(base, art)
    base = Image.composite(
        Image.new("RGB", size, BLACK), base, _vignette(size, round(style.vignette * 100))
    )

    # RGBA draw mode on an RGB image gives blended fills, which is what the
    # scrims and the hatch need without compositing separate layers.
    draw = ImageDraw.Draw(base, "RGBA")

    placeholder = art is None
    if placeholder:
        _hatch(draw, size, style.accent)

    label_key = (metrics.label, "bold")

    # --- header: act ladder, act name, clock card
    ladder_bottom = _act_ladder(draw, metrics, beat.act, style.accent)
    _small_caps(
        draw, style.label, label_key, metrics.margin,
        ladder_bottom + round(metrics.label * 0.7), style.accent,
    )

    clock_font = _font(metrics.clock, "mono")
    clock_text = beat.clock or "--:--"
    pad = round(metrics.clock * 0.32)
    clock_w = _width(clock_font, clock_text) + 2 * pad
    clock_h = _line_height((metrics.clock, "mono")) + pad
    clock_box = (
        metrics.width - metrics.margin - clock_w,
        metrics.margin,
        metrics.width - metrics.margin,
        metrics.margin + clock_h,
    )
    draw.rectangle(clock_box, fill=(0, 0, 0, 170), outline=style.accent, width=2)
    draw.text(
        (clock_box[0] + pad, clock_box[1] + pad * 0.35), clock_text,
        font=clock_font, fill=BONE,
    )
    pips_top = round(clock_box[3] + metrics.label * 0.6)
    _tension_pips(draw, metrics, round(clock_box[0]), pips_top, beat.tension, style.accent)

    # --- subject line: what is on screen
    # Kept clear of the clock card rather than wrapped around it: the corner is
    # the bible's, and the subject line is the thing you read first.
    subject_top = max(
        round(metrics.height * 0.16),
        pips_top + round(metrics.label * 1.4),
    )
    _small_caps(
        draw, f"SHOT · {beat.shot.motion}", label_key,
        metrics.margin, subject_top, (*FAINT, 235),
    )
    subject_key, subject_lines = _fit(
        beat.shot.description,
        metrics.content_width,
        3,
        (metrics.subject, round(metrics.subject * 0.84), round(metrics.subject * 0.7)),
        "bold",
    )
    _text_block(
        draw, subject_lines, subject_key,
        metrics.margin, subject_top + round(metrics.label * 2.1), BONE,
    )

    # --- subtitles: the voiceover, readable, anchored to the lower third
    band_top = metrics.height - metrics.band
    footer_y = band_top - round(metrics.label * 2.0)
    subtitle_key_sizes = (
        metrics.subtitle,
        round(metrics.subtitle * 0.88),
        round(metrics.subtitle * 0.76),
        round(metrics.subtitle * 0.66),
    )
    sub_key, sub_lines = _fit(
        beat.voiceover, metrics.content_width, 6, subtitle_key_sizes
    )
    sub_step = _line_height(sub_key)
    sub_height = sub_step * len(sub_lines)
    sub_top = footer_y - round(metrics.margin * 0.5) - sub_height
    scrim = (
        metrics.margin - round(metrics.margin * 0.45),
        sub_top - round(metrics.margin * 0.42),
        metrics.width - metrics.margin + round(metrics.margin * 0.45),
        sub_top + sub_height + round(metrics.margin * 0.3),
    )
    draw.rectangle(scrim, fill=(0, 0, 0, 165))
    draw.line([(scrim[0], scrim[1]), (scrim[0], scrim[3])], fill=style.accent, width=3)
    _text_block(
        draw, sub_lines, sub_key, metrics.margin, sub_top, BONE,
        centre_width=metrics.content_width,
    )

    # --- caption: on-screen text the script asked for, boxed above the subtitles
    if beat.caption:
        caption_key = (round(metrics.subtitle * 0.9), "mono")
        caption_font = _font(*caption_key)
        caption_pad = round(metrics.subtitle * 0.4)
        caption_w = _width(caption_font, beat.caption) + 2 * caption_pad
        caption_h = _line_height(caption_key) + caption_pad
        caption_bottom = scrim[1] - round(metrics.margin * 0.6)
        caption_box = (
            metrics.margin, caption_bottom - caption_h,
            metrics.margin + caption_w, caption_bottom,
        )
        draw.rectangle(caption_box, fill=(0, 0, 0, 190), outline=(*BONE, 200), width=2)
        draw.text(
            (caption_box[0] + caption_pad, caption_box[1] + caption_pad * 0.3),
            beat.caption, font=caption_font, fill=BONE,
        )
        _small_caps(
            draw, "caption", (round(metrics.label * 0.85), "bold"),
            metrics.margin, caption_box[1] - round(metrics.label * 1.4), (*FAINT, 220),
        )

    # --- footer: the bible's sound intent, and which cut this is
    _two_column_line(
        draw,
        f"SOUND · {style.sound}",
        f"{context.episode_label} · {context.kicker}",
        (metrics.label, round(metrics.label * 0.85), round(metrics.label * 0.72)),
        (metrics.margin, metrics.width - metrics.margin, footer_y),
        (*FAINT, 235),
    )

    card = Card(image=base, used_asset=art is not None,
                missing_asset=bool(beat.shot.asset) and art is None, asset_path=asset_path)
    _state_band(draw, metrics, cue, context, card, style)
    return card


def _state_band(
    draw: ImageDraw.ImageDraw,
    metrics: Metrics,
    cue: Cue,
    context: CardContext,
    card: Card,
    style: ActStyle,
) -> None:
    """The bottom band: what this frame is, and where its seconds came from.

    Sodium and striped for a placeholder, clinical green and clean for a real
    asset. It is the loudest element on the card on purpose — the band is the
    thing that stops a previz still being mistaken for a grade.
    """
    band_top = metrics.height - metrics.band
    fill = SODIUM if card.is_placeholder else CLINICAL
    if card.missing_asset:
        # A named asset that is not there is a script error, not a design state.
        fill = (214, 92, 74)
    draw.rectangle((0, band_top, metrics.width, metrics.height), fill=fill)

    if card.is_placeholder:
        # Barber-pole stripes, clipped to the band by construction: every line
        # runs exactly from the band's bottom edge to its top.
        step = max(18, metrics.band // 2)
        for x in range(-metrics.band, metrics.width + metrics.band, step * 2):
            draw.line(
                [(x, metrics.height), (x + metrics.band, band_top)],
                fill=(0, 0, 0, 46), width=step,
            )

    # Progress hairline along the band's top edge, so scrubbing has a scale.
    if context.total_seconds > 0:
        elapsed = min(1.0, cue.end / context.total_seconds)
        draw.rectangle((0, band_top, metrics.width, band_top + 4), fill=(0, 0, 0, 90))
        draw.rectangle((0, band_top, metrics.width * elapsed, band_top + 4), fill=BONE)

    band_size = round(metrics.label * 1.1)
    text_y = band_top + (metrics.band - _line_height((band_size, "bold"))) / 2 + 2
    _two_column_line(
        draw,
        f"PREVIZ · {card.state_label}",
        f"{cue.timing_label} timing · cue {cue.index:02d}/{context.total_cues:02d}"
        f" · {cue.beat.beat_id}",
        (band_size, round(band_size * 0.86), round(band_size * 0.74)),
        (metrics.margin, metrics.width - metrics.margin, round(text_y)),
        (0, 0, 0),
    )
    # A hairline of the act accent under the band ties it back to the act.
    draw.rectangle((0, metrics.height - 3, metrics.width, metrics.height), fill=style.accent)


# ------------------------------------------------------------------ assembly


@dataclass(frozen=True)
class RenderResult:
    """What a previz render produced, in numbers that can be checked."""

    out_path: Path
    duration_s: float          # measured out of the finished container
    scheduled_s: float         # sum of the cue durations that went in
    frames: int                # cards rendered — one per cue
    video_frames: int          # frames encoded, after duplication to fps
    placeholder_beats: int
    asset_beats: int
    missing_assets: int
    width: int
    height: int
    fps: int
    size_bytes: int
    derived_beats: int
    authored_beats: int
    measured_beats: int
    audio_note: str

    @property
    def drift_s(self) -> float:
        """How far the container landed from the schedule it was given."""
        return self.duration_s - self.scheduled_s

    @property
    def timing_note(self) -> str:
        """Provenance summary. Printed wherever the render is reported."""
        parts = [
            f"{self.derived_beats} derived",
            f"{self.authored_beats} authored",
            f"{self.measured_beats} measured",
        ]
        return ", ".join(parts)

    def summary(self) -> str:
        minutes, seconds = divmod(self.duration_s, 60)
        return (
            f"{self.out_path.name} · {int(minutes)}m{seconds:04.1f}s · "
            f"{self.width}x{self.height}@{self.fps} · {self.size_bytes / 1_048_576:.1f} MB · "
            f"{self.frames} cards ({self.placeholder_beats} placeholder, "
            f"{self.asset_beats} asset) · timing: {self.timing_note} · {self.audio_note}"
        )


def render_cues(
    cues: Sequence[Cue],
    out_path: Path,
    context: CardContext,
    *,
    size: tuple[int, int] = FRAME,
    fps: int = DEFAULT_FPS,
    keep_frames: Path | None = None,
) -> RenderResult:
    """Draw every cue and mux the result into a real MP4.

    One still per cue held for the cue's own duration, via ffmpeg's concat
    demuxer — the alternative, emitting ``fps × duration`` identical PNGs, spends
    minutes writing files x264 would have deduplicated anyway.

    The silent stereo track is generated in the same invocation rather than
    written to disk first, and ``-t`` pins the output to the scheduled length so
    the two streams cannot drift apart.
    """
    if not cues:
        raise ValueError("nothing to render: no cues")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scheduled = sum(cue.duration for cue in cues)
    minimum = 1.0 / fps

    with tempfile.TemporaryDirectory(prefix="ajax-previz-") as tmp:
        work = Path(keep_frames) if keep_frames else Path(tmp)
        work.mkdir(parents=True, exist_ok=True)

        placeholders = assets = missing = 0
        entries: list[str] = []
        for position, cue in enumerate(cues):
            card = draw_card(cue, context, size)
            frame_path = work / f"cue{position:04d}.png"
            card.image.save(frame_path)
            placeholders += card.is_placeholder
            assets += card.used_asset
            missing += card.missing_asset
            # A cue shorter than one frame period would vanish entirely; clamping
            # keeps it visible and the drift is a fraction of a frame.
            entries.append(f"file '{frame_path.name}'\nduration {max(cue.duration, minimum):.3f}")

        # The last frame is listed a second time because the concat demuxer gives
        # the final entry no onward timestamp to hold against, and without it the
        # closing beat lasts a single frame.
        listing = work / "cues.txt"
        listing.write_text("\n".join(entries) + f"\nfile 'cue{len(cues) - 1:04d}.png'\n")

        run_ffmpeg(
            [
                "-y",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-f", "lavfi",
                "-i", f"anullsrc=channel_layout={CHANNEL_LAYOUT}:sample_rate={SAMPLE_RATE}",
                "-map", "0:v", "-map", "1:a",
                "-t", f"{scheduled:.3f}",
                # The fps *filter*, not -r. Output-rate conversion decides how long
                # to hold a frame from the packet's own duration, which for a still
                # is one frame; the filter holds it until the next timestamp, which
                # is the only reading that reproduces the schedule. Measured: -r
                # loses about 2% of the runtime across a twenty-beat episode.
                "-vf", f"fps={fps}",
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
                "-crf", "23", "-g", str(fps * 10),
                "-pix_fmt", "yuv420p",  # yuv420p or half the world cannot play it
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                str(out_path),
            ]
        )

    measured_length = measure_duration(out_path)
    return RenderResult(
        out_path=out_path,
        duration_s=measured_length,
        scheduled_s=scheduled,
        frames=len(cues),
        video_frames=round(measured_length * fps),
        placeholder_beats=placeholders,
        asset_beats=assets,
        missing_assets=missing,
        width=size[0],
        height=size[1],
        fps=fps,
        size_bytes=out_path.stat().st_size,
        derived_beats=sum(c.source is Source.DERIVED for c in cues),
        authored_beats=sum(c.source is Source.AUTHORED for c in cues),
        measured_beats=sum(c.source is Source.MEASURED for c in cues),
        audio_note=(
            "silent track — no narration recorded; every derived duration is a "
            "word-count estimate, not a performance"
        ),
    )


def previz(
    episode: Episode,
    out_path: Path,
    schedule: Sequence[tuple[Beat, float, float]] | None = None,
    *,
    measured: Mapping[str, float] | None = None,
    sources: Mapping[str, Source] | None = None,
    size: tuple[int, int] = FRAME,
    fps: int = DEFAULT_FPS,
    base_dir: Path | None = None,
    keep_frames: Path | None = None,
) -> RenderResult:
    """Render an episode as a previz animatic.

    ``schedule`` is an optional pre-computed ``(beat, start, duration)`` list from
    whatever built the timeline; without it the cues are laid out here from
    :meth:`Beat.duration`. ``measured`` carries real recorded lengths by beat id
    — see :func:`ajax_studio.voice.measured_seconds` — and always beats an
    estimate.
    """
    cues = build_cues(episode.beats, schedule, measured=measured, sources=sources)
    context = CardContext.for_episode(episode, cues, base_dir=base_dir)
    return render_cues(cues, Path(out_path), context, size=size, fps=fps,
                       keep_frames=keep_frames)
