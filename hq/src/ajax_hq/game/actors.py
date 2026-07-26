"""The staff: one actor per real agent or session, and what they are doing.

**No actor exists without a record behind it.** Actors are created from the
snapshot's agents and sessions, or from an event carrying an id nobody has seen
before — which means a new agent was dispatched while the game was running, and
it walks in. There is no other way to make one, and that is asserted by test.

What is honest here and what is not, stated plainly because the whole point of
this project is not to blur it:

- **Who is on the floor** — real. Every actor is an agent or session found in a
  transcript.
- **Where an actor goes on an errand** — real. It is sent by a tool call that
  actually happened, to the wing that tool implies.
- **Whether an actor is busy** — real. Busy means events are arriving for it.
- **The wandering** — decoration. An idle actor drifts around its wing because a
  motionless grid reads as broken, not because anything moved. The HUD says so.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from ajax_hq.behaviour import wing_for
from ajax_hq.game.world import Point, World
from ajax_hq.model import Agent, Session, Snapshot

# How long an actor lingers where an event sent it before it is free to drift.
BUSY_SECONDS = 2.5
# Cells per second. Slow enough to follow, fast enough to cross the floor.
WALK_SPEED = 6.0


class ActorState(str, Enum):
    WORKING = "working"     # at its desk, an event arrived recently
    ERRAND = "errand"       # walking somewhere a real event sent it
    VISITING = "visiting"   # standing where the errand took it
    RETURNING = "returning"  # walking home
    IDLE = "idle"           # at its desk, nothing happening
    ROAMING = "roaming"     # drifting — decorative

    @property
    def is_real_activity(self) -> bool:
        """True only for states an actual transcript record produced."""
        return self in (ActorState.WORKING, ActorState.ERRAND, ActorState.VISITING)


@dataclass
class Actor:
    """One agent or session on the floor."""

    actor_id: str
    name: str
    kind: str
    home_wing: str
    desk: Point
    position: Point
    principal: bool = False
    state: ActorState = ActorState.IDLE
    path: list[Point] = field(default_factory=list)
    busy_for: float = 0.0
    step_progress: float = 0.0
    last_detail: str = ""
    events_seen: int = 0
    # Set when an actor was created by an event rather than from the snapshot —
    # it appeared while the game was running.
    walked_in: bool = False

    @property
    def at_desk(self) -> bool:
        return self.position == self.desk

    @property
    def label(self) -> str:
        return self.name

    def send_to(self, world: World, target: Point, state: ActorState) -> None:
        route = world.path(self.position, target)
        if route:
            self.path = route
            self.state = state
        else:
            # Unreachable is a bug, but stranding an actor mid-floor would hide
            # it. Snap home instead, visibly.
            self.path = []
            self.position = self.desk
            self.state = ActorState.IDLE

    def advance(self, dt: float) -> None:
        """Move along the current path by ``dt`` seconds of travel."""
        if not self.path:
            return
        self.step_progress += dt * WALK_SPEED
        while self.step_progress >= 1.0 and self.path:
            self.step_progress -= 1.0
            self.position = self.path.pop(0)


def _desk_allocator(world: World) -> dict[str, list[Point]]:
    """Free desks per wing, in seating order."""
    return {code: list(world.desks_of(code)) for code in world.rooms}


class Floor:
    """The staffed floor: actors, their desks, and the seating rules."""

    def __init__(self, world: World, *, seed: int | None = None) -> None:
        self.world = world
        self.actors: dict[str, Actor] = {}
        self.free_desks = _desk_allocator(world)
        self.overflow: list[str] = []
        self.random = random.Random(seed)

    # ---------------------------------------------------------------- seating

    def _take_desk(self, wing: str) -> tuple[str, Point]:
        """A desk in ``wing``, falling back so nobody is ever left unseated."""
        for code in (wing, *[c for c in self.world.rooms if c != wing]):
            desks = self.free_desks.get(code) or []
            if desks:
                return code, desks.pop(0)
        # Every desk in the building is taken. Rather than drop a real agent,
        # seat it on top of an existing desk and record that we did.
        self.overflow.append(wing)
        return wing, self.world.desks_of(wing)[0]

    def add_agent(self, agent: Agent) -> Actor:
        wing, desk = self._take_desk(wing_for(agent))
        actor = Actor(
            actor_id=agent.agent_id,
            name=agent.title,
            kind=agent.agent_type or "agent",
            home_wing=wing,
            desk=desk,
            position=desk,
        )
        self.actors[agent.agent_id] = actor
        return actor

    def add_session(self, session: Session) -> Actor:
        wing, desk = self._take_desk("EXO")
        actor = Actor(
            actor_id=session.session_id,
            name=session.title,
            kind="principal session",
            home_wing=wing,
            desk=desk,
            position=desk,
            principal=True,
        )
        self.actors[session.session_id] = actor
        return actor

    def add_unknown(self, actor_id: str) -> Actor:
        """An id that arrived in an event before any record described it.

        Real work by a real agent — it was dispatched after the snapshot was
        taken. It gets a desk in R&D and a provisional name, and the display
        marks it as having walked in.
        """
        wing, desk = self._take_desk("RND")
        actor = Actor(
            actor_id=actor_id,
            name=f"agent {actor_id[:8]}",
            kind="just dispatched",
            home_wing=wing,
            desk=desk,
            position=desk,
            walked_in=True,
        )
        self.actors[actor_id] = actor
        return actor

    @classmethod
    def staff(cls, world: World, snapshot: Snapshot, *, seed: int | None = None) -> Floor:
        """Seat everyone in the snapshot. Sessions first, so EXO is theirs."""
        floor = cls(world, seed=seed)
        for session in snapshot.sessions:
            floor.add_session(session)
        for agent in sorted(snapshot.agents, key=lambda a: (-a.tools.total, a.agent_id)):
            floor.add_agent(agent)
        return floor

    # ---------------------------------------------------------------- movement

    def wander_target(self, actor: Actor) -> Point:
        """A drifting destination inside the actor's own wing.

        Confined to the wing on purpose: an actor crossing the building is a
        claim that it went somewhere, and only a real event gets to make that
        claim.
        """
        room = self.world.rooms[actor.home_wing]
        x0, y0, w, h = room.interior
        for _ in range(12):
            candidate = (self.random.randrange(x0, x0 + w), self.random.randrange(y0, y0 + h))
            if self.world.walkable(candidate) and candidate != actor.position:
                return candidate
        return actor.desk
