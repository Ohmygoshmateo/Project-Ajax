"""The terminal view: the floor as characters, redrawn on a Rich Live loop.

Drawn as one `Text` per frame rather than a widget tree, because the map is a
grid of single cells and any layout engine between the grid and the screen is
overhead that shows up as flicker.
"""

from __future__ import annotations

import time
import zlib

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ajax_hq.game.actors import Actor, ActorState
from ajax_hq.game.sim import Simulation
from ajax_hq.game.world import Tile, World

GOLD = "#C8A951"
DIM = "#8A93A8"
FAINT = "#5C6580"

FRAME_SECONDS = 1 / 12

TILE_GLYPH = {
    Tile.WALL: ("█", "grey23"),
    Tile.FLOOR: ("·", "grey15"),
    Tile.DOOR: ("╬", FAINT),
    Tile.DESK: ("▭", "grey35"),
}

# An actor's glyph is its state, so the floor is readable without the legend.
STATE_GLYPH: dict[ActorState, tuple[str, str]] = {
    ActorState.WORKING: ("◉", "green"),
    ActorState.ERRAND: ("◈", GOLD),
    ActorState.VISITING: ("◈", "cyan"),
    ActorState.RETURNING: ("◇", DIM),
    ActorState.IDLE: ("○", DIM),
    ActorState.ROAMING: ("○", FAINT),
}

STATE_LABEL = {
    ActorState.WORKING: "working",
    ActorState.ERRAND: "on errand",
    ActorState.VISITING: "visiting",
    ActorState.RETURNING: "returning",
    ActorState.IDLE: "idle",
    ActorState.ROAMING: "roaming",
}


# A terminal cannot draw a sprite, so identity is carried by letter and colour
# instead: the same agent is the same letter in the same colour every frame, and
# the browser gives that agent a character in a matching palette slot.
ACTOR_COLOURS = (
    "cyan", "magenta", "green", "yellow", "blue", "red",
    "bright_cyan", "bright_magenta", "bright_green", "bright_blue",
)


def _initial(actor: Actor, index: int) -> str:
    """A per-actor letter, so individuals are trackable across frames."""
    return "@" if actor.principal else chr(ord("a") + index % 26)


def _actor_colour(actor: Actor) -> str:
    """Stable across runs — ``hash()`` is salted per process, ``crc32`` is not.

    An agent that changed colour every time you launched the floor would defeat
    the point of colouring it at all.
    """
    if actor.principal:
        return GOLD
    return ACTOR_COLOURS[zlib.crc32(actor.actor_id.encode()) % len(ACTOR_COLOURS)]


def _walk_glyph(actor: Actor, letter: str, phase: int) -> str:
    """Alternate case on alternate steps — a two-frame walk cycle.

    Deliberately not a fancier glyph set: block and arrow characters fall back
    inconsistently across terminal fonts, and a character that renders as a
    replacement box is worse than one that simply changes case.
    """
    if not actor.moving:
        return letter
    return letter.upper() if phase else letter


def render_map(sim: Simulation) -> Text:
    world: World = sim.world
    grid = [[TILE_GLYPH[world.tiles[y][x]] for x in range(world.width)]
            for y in range(world.height)]

    phase = int(sim.clock * 5) % 2
    for index, actor in enumerate(sorted(sim.floor.actors.values(), key=lambda a: a.actor_id)):
        x, y = actor.position
        if 0 <= x < world.width and 0 <= y < world.height:
            letter = _walk_glyph(actor, _initial(actor, index), phase)
            grid[y][x] = (letter, f"bold {_actor_colour(actor)}")

    # Wing names are written into the top wall of each room, so they cost no
    # extra rows and can never overlap an actor.
    for room in world.rooms.values():
        x, y = room.label_anchor
        for offset, char in enumerate(f" {room.code} "):
            if x + offset < world.width:
                grid[y][x + offset] = (char, f"bold black on {GOLD}")

    text = Text()
    for y, row in enumerate(grid):
        for glyph, style in row:
            text.append(glyph, style=style)
        if y < world.height - 1:
            text.append("\n")
    return text


def render_roster(sim: Simulation) -> Table:
    table = Table(box=None, padding=(0, 1), expand=False)
    for column in ("", "Who", "Wing", "State", "Events", "Last real event"):
        table.add_column(column, overflow="ellipsis", no_wrap=True)

    for index, actor in enumerate(sorted(sim.floor.actors.values(), key=lambda a: a.actor_id)):
        _, colour = STATE_GLYPH[actor.state]
        table.add_row(
            Text(_initial(actor, index), style=f"bold {_actor_colour(actor)}"),
            Text(actor.name[:26], style="bold" if actor.principal else ""),
            actor.home_wing,
            Text(STATE_LABEL[actor.state], style=colour),
            str(actor.events_seen),
            Text(actor.last_detail[:34] or "—", style=FAINT),
        )
    return table


def render_hud(sim: Simulation) -> Text:
    hud = Text()
    mode = "LIVE" if sim.live else "REPLAY"
    hud.append(f" {mode} ", style=f"bold black on {GOLD if sim.live else 'cyan'}")
    hud.append(
        f"  {sim.stats.events_applied} real events applied"
        f" · {sim.stats.errands} errands · {len(sim.floor.actors)} on the floor",
        style=DIM,
    )
    if not sim.live:
        remaining = max(0, len(sim.pending) - sim.replay_index)
        hud.append(f" · {remaining} left to replay", style=FAINT)
    return hud


def render_frame(sim: Simulation) -> Group:
    caveat = (
        "Movement between wings is a real tool call. Drifting inside a wing is "
        "decoration — nothing on disk records where anyone stands."
    )
    footer = Text(f"  {caveat}", style=FAINT)
    if not sim.live:
        footer = Text(
            "  REPLAY of events already on disk — this is history, not live activity.\n"
            f"  {caveat}",
            style=FAINT,
        )

    return Group(
        render_hud(sim),
        Text(),
        render_map(sim),
        Text(),
        Panel(render_roster(sim), border_style="grey23", padding=(0, 1), expand=False),
        footer,
    )


def play(sim: Simulation, console: Console | None = None, *, max_frames: int | None = None) -> int:
    """Run the loop until interrupted. Returns the number of frames drawn."""
    console = console or Console()
    frames = 0
    with Live(render_frame(sim), console=console, refresh_per_second=12,
              screen=True, transient=False) as live:
        try:
            while max_frames is None or frames < max_frames:
                time.sleep(FRAME_SECONDS)
                sim.tick(FRAME_SECONDS)
                live.update(render_frame(sim))
                frames += 1
                if sim.exhausted and not sim.live:
                    break
        except KeyboardInterrupt:
            pass
    return frames
