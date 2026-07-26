"""The clock: events in, movement out.

One tick does three things in order — drain events, expire timers, then move.
That order matters: an event arriving this frame should redirect an actor
immediately rather than a frame later, or a burst of fast tool calls reads as
the floor lagging behind the work.

The simulation has exactly one degree of freedom that is not driven by data,
and it is deliberately fenced: where an *idle* actor drifts inside its own
wing. Everything else is a consequence of a record on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ajax_hq.game.actors import BUSY_SECONDS, Actor, ActorState, Floor
from ajax_hq.game.events import Event, TranscriptTail, replay
from ajax_hq.game.world import World
from ajax_hq.model import Snapshot


@dataclass
class Ticker:
    """A record of what the simulation has actually seen, for the HUD."""

    events_applied: int = 0
    errands: int = 0
    unknown_actors: int = 0
    last_event: str = ""
    last_event_at: datetime | None = None
    started_at: float = 0.0


@dataclass
class Simulation:
    world: World
    floor: Floor
    live: bool = True
    stats: Ticker = field(default_factory=Ticker)
    tail: TranscriptTail | None = None
    pending: list[Event] = field(default_factory=list)
    replay_index: int = 0
    clock: float = 0.0
    # Replay pacing: events per second of wall time.
    replay_rate: float = 6.0

    # ------------------------------------------------------------ construction

    @classmethod
    def create(
        cls,
        snapshot: Snapshot,
        claude_home: Path,
        *,
        live: bool = True,
        seed: int | None = None,
    ) -> Simulation:
        world = World.build()
        floor = Floor.staff(world, snapshot, seed=seed)
        sim = cls(world=world, floor=floor, live=live)
        if live:
            sim.tail = TranscriptTail(claude_home)
        else:
            sim.pending = replay(claude_home)
        return sim

    # -------------------------------------------------------------------- tick

    def tick(self, dt: float) -> None:
        self.clock += dt
        for event in self._collect(dt):
            self._apply(event)
        self._expire(dt)
        self._move(dt)

    def _collect(self, dt: float) -> list[Event]:
        if self.live:
            return self.tail.poll() if self.tail else []

        # Replay: hand out events on a compressed clock, oldest first.
        due = int(self.replay_rate * dt) or (1 if self.replay_rate * dt > 0.35 else 0)
        if due <= 0:
            return []
        chunk = self.pending[self.replay_index : self.replay_index + due]
        self.replay_index += len(chunk)
        return chunk

    def _apply(self, event: Event) -> None:
        actor = self.floor.actors.get(event.actor_id)
        if actor is None:
            actor = self.floor.add_unknown(event.actor_id)
            self.stats.unknown_actors += 1

        actor.events_seen += 1
        actor.last_detail = event.detail
        actor.busy_for = BUSY_SECONDS
        self.stats.events_applied += 1
        self.stats.last_event = f"{actor.name}: {event.detail}"
        self.stats.last_event_at = event.at

        if event.is_errand and event.wing and event.wing != actor.home_wing:
            target = self._errand_target(actor, event.wing)
            if target:
                actor.send_to(self.world, target, ActorState.ERRAND)
                self.stats.errands += 1
                return

        # A tool call in the actor's own wing: it is working, at its desk.
        if actor.at_desk:
            actor.state = ActorState.WORKING
        elif not actor.path:
            actor.send_to(self.world, actor.desk, ActorState.RETURNING)

    def _errand_target(self, actor: Actor, wing: str):
        """Where in ``wing`` an errand puts an actor.

        A free desk if there is one, otherwise the doorway — standing in the
        doorway of a full room is a truthful picture of visiting a wing you do
        not work in.
        """
        room = self.world.rooms.get(wing)
        if room is None:
            return None
        free = self.floor.free_desks.get(wing) or []
        if free:
            return free[0]
        return room.door

    def _expire(self, dt: float) -> None:
        for actor in self.floor.actors.values():
            if actor.busy_for > 0:
                actor.busy_for = max(0.0, actor.busy_for - dt)

    def _move(self, dt: float) -> None:
        for actor in self.floor.actors.values():
            actor.advance(dt)

            if actor.path:
                # An actor in motion must never read as working: the roster and
                # the map would disagree about the same actor in the same frame.
                if actor.state not in (
                    ActorState.ERRAND, ActorState.RETURNING, ActorState.ROAMING
                ):
                    actor.state = ActorState.RETURNING
                continue

            # Arrived. Decide what standing still means for this actor.
            if actor.state is ActorState.ERRAND:
                actor.state = ActorState.VISITING
            elif actor.state is ActorState.RETURNING:
                actor.state = ActorState.WORKING if actor.busy_for > 0 else ActorState.IDLE
            elif actor.busy_for > 0:
                actor.state = (
                    ActorState.WORKING if actor.at_desk else ActorState.VISITING
                )
            else:
                self._drift(actor)

    def _drift(self, actor: Actor) -> None:
        """The one non-data-driven behaviour in the simulation.

        An actor with nothing happening either heads back to its desk or takes a
        step somewhere inside its own wing. It never leaves the wing: crossing
        the floor asserts an errand, and only an event may assert one.
        """
        if not actor.at_desk and actor.state is not ActorState.ROAMING:
            actor.send_to(self.world, actor.desk, ActorState.RETURNING)
            return

        actor.state = ActorState.ROAMING if actor.busy_for <= 0 else ActorState.IDLE
        if actor.state is ActorState.ROAMING and self.floor.random.random() < 0.35:
            actor.send_to(self.world, self.floor.wander_target(actor), ActorState.ROAMING)

    # ------------------------------------------------------------------- views

    @property
    def busy_actors(self) -> list[Actor]:
        return [a for a in self.floor.actors.values() if a.state.is_real_activity]

    @property
    def exhausted(self) -> bool:
        """Replay has played everything it had."""
        return not self.live and self.replay_index >= len(self.pending)
