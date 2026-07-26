"""The roaming floor: the world, the events driving it, and the honesty rules.

The tests that matter most are in :class:`TestNothingIsInvented`. A game is
exactly where a dashboard starts lying — it is easy to make a busy-looking
office out of nothing — so the constraints are asserted rather than intended.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ajax_hq.collect import collect
from ajax_hq.game import events as events_mod
from ajax_hq.game.actors import Actor, ActorState, Floor
from ajax_hq.game.sim import Simulation
from ajax_hq.game.tui import render_frame, render_map
from ajax_hq.game.web import state_payload, world_payload
from ajax_hq.game.world import ROOM_SPECS, Tile, World
from ajax_hq.model import Agent, Session, Snapshot, ToolUsage
from tests.conftest import AGENT_A, SESSION_ID


@pytest.fixture
def world() -> World:
    return World.build()


@pytest.fixture
def snapshot(claude_home, empty_workspace) -> Snapshot:
    return collect(claude_home=claude_home, workspace=empty_workspace)


@pytest.fixture
def sim(snapshot, claude_home) -> Simulation:
    return Simulation.create(snapshot, claude_home, live=True, seed=11)


class TestWorld:
    def test_all_six_wings_exist(self, world):
        assert set(world.rooms) == {code for code, _, _ in ROOM_SPECS}

    def test_rooms_do_not_overlap(self, world):
        seen: set[tuple[int, int]] = set()
        for room in world.rooms.values():
            cells = {
                (x, y)
                for y in range(room.y, room.y + room.height)
                for x in range(room.x, room.x + room.width)
            }
            assert not (cells & seen), f"{room.code} overlaps another wing"
            seen |= cells

    def test_every_room_has_a_door_onto_the_corridor(self, world):
        for room in world.rooms.values():
            assert world.tile(room.door) is Tile.DOOR
            beyond = [
                p
                for p in world.neighbours(room.door)
                if not room.contains(p)
            ]
            assert beyond, f"{room.code}'s door opens onto nothing"

    def test_desks_are_inside_their_room(self, world):
        for room in world.rooms.values():
            for desk in room.desks:
                assert room.contains(desk)
                assert world.tile(desk) is Tile.DESK

    def test_every_desk_reaches_every_other_desk(self, world):
        """An unreachable desk would strand an actor where nothing can move it."""
        desks = [d for room in world.rooms.values() for d in room.desks]
        origin = desks[0]
        for desk in desks[1:]:
            assert world.path(origin, desk), f"no route from {origin} to {desk}"

    def test_walls_are_not_walkable(self, world):
        assert not world.walkable((0, 0))
        assert world.path((0, 0), world.rooms["ENG"].desks[0]) == []

    def test_path_excludes_the_starting_cell(self, world):
        start = world.rooms["RND"].desks[0]
        goal = world.rooms["RND"].desks[3]
        route = world.path(start, goal)
        assert start not in route
        assert route[-1] == goal

    def test_path_to_self_is_empty(self, world):
        desk = world.rooms["QA"].desks[0]
        assert world.path(desk, desk) == []


class TestEventClassification:
    @pytest.mark.parametrize(
        ("tool", "command", "wing"),
        [
            ("Write", None, "ENG"),
            ("Edit", None, "ENG"),
            ("WebSearch", None, "RND"),
            ("Read", None, "RND"),
            ("Agent", None, "EXO"),
            ("Bash", "pytest -q", "QA"),
            ("Bash", "git push origin main", "OPS"),
            ("Bash", "ls -la", None),
            ("Bash", "git status", None),
            ("SomeFutureTool", None, None),
        ],
    )
    def test_a_tool_call_implies_a_wing_or_nothing(self, tool, command, wing):
        assert events_mod.wing_for_tool(tool, command) == wing

    def test_neutral_commands_do_not_move_anyone(self):
        """No evidence is not weak evidence — the actor simply stays put."""
        event = events_mod.Event(actor_id="a", kind="shell", detail="ls", wing=None)
        assert event.is_errand is False

    def test_records_that_are_not_assistant_turns_produce_nothing(self):
        assert events_mod.events_from_record({"type": "user"}, "a") == []
        assert events_mod.events_from_record({}, "a") == []

    def test_multiline_detail_is_flattened(self):
        record = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "first\nsecond\nthird"}]},
        }
        (event,) = events_mod.events_from_record(record, "a")
        assert "\n" not in event.detail


class TestTail:
    """Following a file that is still being written to."""

    def _append(self, path: Path, records: list[dict]) -> None:
        with path.open("a") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")

    def _assistant(self, tool: str, payload: dict | None = None) -> dict:
        return {
            "type": "assistant",
            "agentId": AGENT_A,
            "timestamp": "2026-03-02T09:05:00.000Z",
            "message": {"content": [
                {"type": "tool_use", "name": tool, "input": payload or {}, "id": "t1"}
            ]},
        }

    def _agent_file(self, claude_home: Path) -> Path:
        return (claude_home / "projects" / "-home-user-Demo" / SESSION_ID
                / "subagents" / f"agent-{AGENT_A}.jsonl")

    def test_live_tail_starts_at_the_end_not_the_beginning(self, claude_home):
        """Live mode watches what happens next, not the whole day at once."""
        tail = events_mod.TranscriptTail(claude_home)
        assert tail.poll() == []

    def test_appended_records_are_picked_up(self, claude_home):
        tail = events_mod.TranscriptTail(claude_home)
        self._append(self._agent_file(claude_home), [self._assistant("Write")])

        found = tail.poll()
        assert [e.wing for e in found] == ["ENG"]
        assert found[0].actor_id == AGENT_A

    def test_records_are_never_delivered_twice(self, claude_home):
        tail = events_mod.TranscriptTail(claude_home)
        self._append(self._agent_file(claude_home), [self._assistant("Write")])
        assert len(tail.poll()) == 1
        assert tail.poll() == []

    def test_a_half_written_line_is_retried_not_dropped(self, claude_home):
        """A file being appended to right now often ends mid-record."""
        tail = events_mod.TranscriptTail(claude_home)
        path = self._agent_file(claude_home)
        record = json.dumps(self._assistant("Edit"))

        with path.open("a") as fh:
            fh.write(record[:40])          # a torn write
        assert tail.poll() == []

        with path.open("a") as fh:
            fh.write(record[40:] + "\n")   # the rest arrives
        assert [e.wing for e in tail.poll()] == ["ENG"]

    def test_a_truncated_file_is_re_read_from_zero(self, claude_home):
        tail = events_mod.TranscriptTail(claude_home)
        path = self._agent_file(claude_home)
        path.write_text("")
        assert tail.poll() == []

    def test_replay_returns_history_in_time_order(self, claude_home):
        found = events_mod.replay(claude_home)
        stamps = [e.at for e in found if e.at]
        assert stamps == sorted(stamps)
        assert found


class TestNothingIsInvented:
    """The honesty rules, as assertions."""

    def test_every_actor_traces_to_a_real_agent_or_session(self, sim, snapshot):
        real = {a.agent_id for a in snapshot.agents} | {s.session_id for s in snapshot.sessions}
        assert set(sim.floor.actors) <= real

    def test_actor_count_equals_agents_plus_sessions(self, sim, snapshot):
        assert len(sim.floor.actors) == len(snapshot.agents) + len(snapshot.sessions)

    def test_an_empty_snapshot_staffs_an_empty_office(self, claude_home):
        empty = Snapshot(generated_at=datetime.now(UTC))
        sim = Simulation.create(empty, claude_home, live=True)
        assert sim.floor.actors == {}

    def test_no_events_means_no_errands(self, sim):
        for _ in range(200):
            sim.tick(1 / 12)
        assert sim.stats.events_applied == 0
        assert sim.stats.errands == 0

    def test_idle_actors_never_leave_their_own_wing(self, sim):
        """Crossing the floor asserts an errand, and only an event may assert one."""
        homes = {a.actor_id: sim.world.rooms[a.home_wing] for a in sim.floor.actors.values()}
        for _ in range(400):
            sim.tick(1 / 12)
            for actor in sim.floor.actors.values():
                assert homes[actor.actor_id].contains(actor.position), (
                    f"{actor.name} drifted out of {actor.home_wing} with no event"
                )

    def test_only_real_activity_states_count_as_real(self):
        assert ActorState.WORKING.is_real_activity
        assert ActorState.ERRAND.is_real_activity
        assert ActorState.VISITING.is_real_activity
        assert not ActorState.ROAMING.is_real_activity
        assert not ActorState.IDLE.is_real_activity

    def test_the_terminal_frame_states_what_is_decoration(self, sim):
        buffer = StringIO()
        Console(file=buffer, width=120).print(render_frame(sim))
        text = buffer.getvalue()
        assert "decoration" in text

    def test_replay_is_labelled_as_history(self, snapshot, claude_home):
        sim = Simulation.create(snapshot, claude_home, live=False)
        buffer = StringIO()
        Console(file=buffer, width=120).print(render_frame(sim))
        assert "REPLAY" in buffer.getvalue()


class TestSimulation:
    def test_a_real_event_sends_an_actor_to_the_matching_wing(self, sim):
        actor = next(a for a in sim.floor.actors.values() if a.home_wing == "RND")
        sim._apply(events_mod.Event(actor_id=actor.actor_id, kind="tool",
                                    detail="Write", wing="ENG"))
        assert actor.state is ActorState.ERRAND
        assert sim.world.rooms["ENG"].contains(actor.path[-1])

    def test_an_actor_walks_the_whole_route(self, sim):
        actor = next(a for a in sim.floor.actors.values() if a.home_wing == "RND")
        sim._apply(events_mod.Event(actor_id=actor.actor_id, kind="tool",
                                    detail="Write", wing="ENG"))
        for _ in range(600):
            sim.tick(1 / 12)
            if not actor.path:
                break
        assert sim.world.rooms["ENG"].contains(actor.position)

    def test_an_event_for_the_home_wing_does_not_start_a_journey(self, sim):
        actor = next(a for a in sim.floor.actors.values() if a.home_wing == "RND")
        sim._apply(events_mod.Event(actor_id=actor.actor_id, kind="tool",
                                    detail="Read", wing="RND"))
        assert sim.stats.errands == 0

    def test_an_unknown_id_walks_in_rather_than_being_ignored(self, sim):
        """A new agent dispatched mid-session is real work and must appear."""
        sim._apply(events_mod.Event(actor_id="brand-new-agent", kind="tool", detail="Read"))
        actor = sim.floor.actors["brand-new-agent"]
        assert actor.walked_in is True
        assert sim.stats.unknown_actors == 1

    def test_a_walking_actor_never_reads_as_working(self, sim):
        """The map and the roster must not disagree about the same actor."""
        actor = next(a for a in sim.floor.actors.values() if a.home_wing == "RND")
        sim._apply(events_mod.Event(actor_id=actor.actor_id, kind="tool",
                                    detail="Write", wing="ENG"))
        for _ in range(400):
            sim.tick(1 / 12)
            if actor.path:
                assert actor.state is not ActorState.WORKING
            if not actor.path and actor.state is ActorState.VISITING:
                break

    def test_replay_drains_and_reports_exhausted(self, snapshot, claude_home):
        sim = Simulation.create(snapshot, claude_home, live=False)
        assert not sim.exhausted
        for _ in range(3000):
            sim.tick(1 / 12)
            if sim.exhausted:
                break
        assert sim.exhausted


class TestSeating:
    def test_everyone_gets_their_own_desk(self, sim):
        seats = [a.desk for a in sim.floor.actors.values()]
        assert len(seats) == len(set(seats))

    def test_the_principal_sits_in_the_executive_office(self, sim):
        principal = next(a for a in sim.floor.actors.values() if a.principal)
        assert principal.home_wing == "EXO"

    def test_seating_follows_the_same_rule_as_the_static_floor(self):
        """An agent's desk in the game agrees with its desk on `ajax-hq floor`."""
        world = World.build()
        snap = Snapshot(generated_at=datetime.now(UTC))
        snap.agents = [
            Agent(agent_id="b", description="builder",
                  tools=ToolUsage(counts={"Write": 9, "Edit": 4})),
            Agent(agent_id="r", description="reader",
                  tools=ToolUsage(counts={"WebSearch": 12})),
        ]
        floor = Floor.staff(world, snap)
        assert floor.actors["b"].home_wing == "ENG"
        assert floor.actors["r"].home_wing == "RND"

    def test_nobody_is_dropped_when_a_wing_fills_up(self):
        """More agents than desks must cost tidiness, never a person."""
        world = World.build()
        snap = Snapshot(generated_at=datetime.now(UTC))
        snap.agents = [
            Agent(agent_id=f"a{i}", description=f"agent {i}",
                  tools=ToolUsage(counts={"Read": 1}))
            for i in range(60)
        ]
        floor = Floor.staff(world, snap)
        assert len(floor.actors) == 60

    def test_sessions_are_seated_before_agents(self):
        world = World.build()
        snap = Snapshot(generated_at=datetime.now(UTC))
        snap.sessions = [Session(session_id="s" * 8, name="principal")]
        snap.agents = [Agent(agent_id="a", description="researcher",
                             tools=ToolUsage(counts={"Read": 1}))]
        floor = Floor.staff(world, snap)
        assert floor.actors["s" * 8].desk == world.desks_of("EXO")[0]


class TestRendering:
    def test_map_is_one_line_per_row(self, sim):
        assert render_map(sim).plain.count("\n") == sim.world.height - 1

    def test_map_row_width_matches_the_world(self, sim):
        first = render_map(sim).plain.split("\n")[0]
        assert len(first) == sim.world.width

    def test_wing_codes_appear_on_the_map(self, sim):
        plain = render_map(sim).plain
        for code in sim.world.rooms:
            assert code in plain

    def test_narrow_console_does_not_raise(self, sim):
        buffer = StringIO()
        Console(file=buffer, width=60).print(render_frame(sim))
        assert buffer.getvalue()

    def test_actor_colour_is_stable_across_runs(self, sim):
        """A character that changed colour every launch would not be trackable."""
        from ajax_hq.game.tui import ACTOR_COLOURS, GOLD, _actor_colour

        actor = next(a for a in sim.floor.actors.values() if not a.principal)
        # crc32, not hash(): the latter is salted per process, so this value
        # would differ between runs.
        assert _actor_colour(actor) in ACTOR_COLOURS
        assert _actor_colour(actor) == _actor_colour(actor)

        principal = next(a for a in sim.floor.actors.values() if a.principal)
        assert _actor_colour(principal) == GOLD

    def test_a_walking_character_animates(self, sim):
        from ajax_hq.game.tui import _walk_glyph

        actor = next(iter(sim.floor.actors.values()))
        actor.path = [(1, 1)]
        assert _walk_glyph(actor, "a", 0) != _walk_glyph(actor, "a", 1)

    def test_a_standing_character_does_not(self, sim):
        from ajax_hq.game.tui import _walk_glyph

        actor = next(iter(sim.floor.actors.values()))
        actor.path = []
        assert _walk_glyph(actor, "a", 0) == _walk_glyph(actor, "a", 1) == "a"


class TestWebPayloads:
    def test_world_payload_is_json_serializable(self, sim):
        payload = world_payload(sim)
        assert json.loads(json.dumps(payload))["width"] == sim.world.width

    def test_state_payload_lists_every_actor(self, sim):
        payload = state_payload(sim)
        assert {a["id"] for a in payload["actors"]} == set(sim.floor.actors)

    def test_state_payload_carries_no_prompt_or_report_text(self, sim, snapshot):
        """The page shows what an agent is doing, never what it was asked."""
        serialized = json.dumps(state_payload(sim))
        for agent in snapshot.agents:
            if agent.prompt:
                assert agent.prompt not in serialized
            if agent.report:
                assert agent.report not in serialized

    def test_server_binds_loopback_only(self):
        from ajax_hq.game import web

        assert web.LOOPBACK == "127.0.0.1"
        source = Path(web.__file__).read_text()
        assert "0.0.0.0" not in source

    def test_page_loads_nothing_from_the_network(self):
        from ajax_hq.game.page import PAGE

        assert "http://" not in PAGE.replace("http://127.0.0.1", "")
        assert "https://" not in PAGE
        assert "<script src" not in PAGE


class TestActorMovement:
    def test_advance_consumes_the_path_in_order(self):
        world = World.build()
        desk = world.rooms["RND"].desks[0]
        actor = Actor(actor_id="a", name="a", kind="k", home_wing="RND",
                      desk=desk, position=desk)
        target = world.rooms["RND"].desks[1]
        actor.send_to(world, target, ActorState.ROAMING)
        steps = len(actor.path)
        for _ in range(steps * 4):
            actor.advance(1 / 12)
        assert actor.position == target

    def test_an_unreachable_target_sends_an_actor_home_visibly(self):
        world = World.build()
        desk = world.rooms["OPS"].desks[0]
        actor = Actor(actor_id="a", name="a", kind="k", home_wing="OPS",
                      desk=desk, position=world.rooms["OPS"].desks[2])
        actor.send_to(world, (0, 0), ActorState.ERRAND)   # a wall
        assert actor.position == desk
        assert actor.state is ActorState.IDLE
