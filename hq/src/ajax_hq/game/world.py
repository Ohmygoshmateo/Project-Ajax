"""The building: six walled wings opening onto a shared corridor.

A tile grid rather than free space, because both renderers — a terminal made of
character cells and a canvas drawing squares — are grids already, and pathing on
a grid is a breadth-first search anyone can read.

Layout is fixed rather than generated. There are exactly six divisions and they
do not change, so a procedural floor plan would add variance with nothing to
gain from it.

    ┌────────┐ ║ ┌────────┐
    │  EXO   ├─╫─┤  RND   │
    └────────┘ ║ └────────┘
    ┌────────┐ ║ ┌────────┐
    │  ENG   ├─╫─┤  QA    │     a corridor spine, every wing opening onto it
    └────────┘ ║ └────────┘
    ┌────────┐ ║ ┌────────┐
    │  OPS   ├─╫─┤  AST   │
    └────────┘ ║ └────────┘

Two columns rather than three: at three the building was over three times wider
than it was tall, which left characters a few pixels high once the floor was
scaled to fit a window. This shape suits both a canvas and an 80-column
terminal.

Every room opens onto the one corridor, so any desk is reachable from any other
desk. That is asserted in the tests: an unreachable desk would strand an actor
somewhere the simulation could never move it out of.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

ROOM_W = 16
ROOM_H = 9
CORRIDOR_W = 3

# Room interiors are inset by one cell of wall on each side.
DESK_COLUMNS = (3, 7, 11)
DESK_ROWS = (2, 5)


class Tile(str, Enum):
    WALL = "wall"
    FLOOR = "floor"
    DOOR = "door"
    DESK = "desk"

    @property
    def walkable(self) -> bool:
        # Desks are walkable: an actor stands *at* its desk rather than on a
        # tile beside it, which keeps "is it home" a single coordinate compare.
        return self is not Tile.WALL


Point = tuple[int, int]


@dataclass(frozen=True)
class Room:
    """One wing. ``code`` matches the division codes used everywhere else."""

    code: str
    name: str
    korean: str
    x: int
    y: int
    width: int = ROOM_W
    height: int = ROOM_H
    door: Point = (0, 0)
    desks: tuple[Point, ...] = ()

    @property
    def interior(self) -> tuple[int, int, int, int]:
        return (self.x + 1, self.y + 1, self.width - 2, self.height - 2)

    def contains(self, point: Point) -> bool:
        px, py = point
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height

    @property
    def label_anchor(self) -> Point:
        return (self.x + 2, self.y)


# (code, name, korean) in floor order: top row left-to-right, then bottom row.
ROOM_SPECS = (
    ("EXO", "Executive Office", "비서실"),
    ("RND", "Research & Development", "연구개발부"),
    ("ENG", "Engineering", "엔지니어링부"),
    ("QA", "Quality Assurance", "품질관리부"),
    ("OPS", "Operations", "운영부"),
    ("AST", "Asset Management", "자산운용부"),
)


@dataclass
class World:
    """The tile map plus the rooms laid over it."""

    width: int
    height: int
    tiles: list[list[Tile]] = field(default_factory=list)
    rooms: dict[str, Room] = field(default_factory=dict)
    corridor_x: int = 0

    # ------------------------------------------------------------ construction

    @classmethod
    def build(cls) -> World:
        width = ROOM_W * 2 + CORRIDOR_W
        height = ROOM_H * 3
        world = cls(width=width, height=height)
        world.tiles = [[Tile.FLOOR for _ in range(width)] for _ in range(height)]
        world.corridor_x = ROOM_W

        for index, (code, name, korean) in enumerate(ROOM_SPECS):
            column = index % 2
            row = index // 2
            x = 0 if column == 0 else ROOM_W + CORRIDOR_W
            y = row * ROOM_H
            # Left-column rooms open east onto the spine; right-column rooms west.
            door_x = x + ROOM_W - 1 if column == 0 else x
            door = (door_x, y + ROOM_H // 2)
            desks = tuple(
                (x + dx, y + dy) for dy in DESK_ROWS for dx in DESK_COLUMNS
            )
            world.rooms[code] = Room(
                code=code, name=name, korean=korean, x=x, y=y, door=door, desks=desks
            )

        world._stamp()
        return world

    def _stamp(self) -> None:
        """Write walls, doors and desks into the tile grid."""
        for room in self.rooms.values():
            for y in range(room.y, room.y + room.height):
                for x in range(room.x, room.x + room.width):
                    edge = (
                        x in (room.x, room.x + room.width - 1)
                        or y in (room.y, room.y + room.height - 1)
                    )
                    self.tiles[y][x] = Tile.WALL if edge else Tile.FLOOR
            for desk in room.desks:
                self.tiles[desk[1]][desk[0]] = Tile.DESK
            self.tiles[room.door[1]][room.door[0]] = Tile.DOOR

    # ------------------------------------------------------------------ queries

    def tile(self, point: Point) -> Tile:
        x, y = point
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.tiles[y][x]
        return Tile.WALL

    def walkable(self, point: Point) -> bool:
        return self.tile(point).walkable

    def room_at(self, point: Point) -> Room | None:
        return next((r for r in self.rooms.values() if r.contains(point)), None)

    def corridor_cells(self) -> list[Point]:
        return [
            (x, y)
            for x in range(self.corridor_x, self.corridor_x + CORRIDOR_W)
            for y in range(self.height)
            if self.walkable((x, y))
        ]

    def desks_of(self, code: str) -> tuple[Point, ...]:
        room = self.rooms.get(code)
        return room.desks if room else ()

    # ------------------------------------------------------------------ pathing

    def neighbours(self, point: Point) -> list[Point]:
        x, y = point
        return [p for p in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)) if self.walkable(p)]

    def path(self, start: Point, goal: Point) -> list[Point]:
        """Shortest walkable path, excluding ``start``. Empty if unreachable.

        Breadth-first rather than A*: the map is 60x18, so the whole grid is
        cheaper to search than the heuristic is to justify.
        """
        if start == goal or not self.walkable(goal):
            return []

        came: dict[Point, Point | None] = {start: None}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if current == goal:
                break
            for nxt in self.neighbours(current):
                if nxt not in came:
                    came[nxt] = current
                    queue.append(nxt)

        if goal not in came:
            return []

        route: list[Point] = []
        node: Point | None = goal
        while node is not None and node != start:
            route.append(node)
            node = came[node]
        route.reverse()
        return route
