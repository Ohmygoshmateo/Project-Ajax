"""The browser view: the same simulation, drawn on a canvas.

One simulation, two renderers. The server owns a :class:`Simulation`, ticks it
on a background thread, and serves its state as JSON; the page draws that state
and interpolates between frames so movement looks continuous at a poll rate that
is not. Nothing about the world lives in the browser — reload it and you rejoin
the same office rather than starting a new one.

Bound to ``127.0.0.1`` for the same reason the dashboard is: the state includes
what each agent is doing, taken from transcripts. Not configurable.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ajax_hq.game.events import ACTIVITY_LABEL
from ajax_hq.game.sim import Simulation
from ajax_hq.game.tui import STATE_LABEL
from ajax_hq.game.world import Tile

log = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
DEFAULT_GAME_PORT = 8788
TICK_SECONDS = 1 / 15

TILE_CODE = {Tile.WALL: 0, Tile.FLOOR: 1, Tile.DOOR: 2, Tile.DESK: 3}


def world_payload(sim: Simulation) -> dict[str, Any]:
    """The static half: sent once, on page load."""
    world = sim.world
    return {
        "width": world.width,
        "height": world.height,
        "tiles": [[TILE_CODE[t] for t in row] for row in world.tiles],
        "rooms": [
            {
                "code": room.code,
                "name": room.name,
                "korean": room.korean,
                "x": room.x,
                "y": room.y,
                "w": room.width,
                "h": room.height,
            }
            for room in world.rooms.values()
        ],
    }


def state_payload(sim: Simulation) -> dict[str, Any]:
    """The moving half: polled."""
    return {
        "live": sim.live,
        "events": sim.stats.events_applied,
        "errands": sim.stats.errands,
        "remaining": max(0, len(sim.pending) - sim.replay_index) if not sim.live else 0,
        "last_event": sim.stats.last_event,
        # Newest first: the feed is read from the top.
        "feed": [
            {
                "actor": item.actor,
                "activity": item.activity,
                "label": item.label,
                "detail": item.detail,
                "wing": item.wing,
                "clock": item.clock,
            }
            for item in reversed(sim.feed)
        ],
        "actors": [
            {
                "id": actor.actor_id,
                "name": actor.name,
                "kind": actor.kind,
                "wing": actor.home_wing,
                # Whole-cell position for anything that reasons about the world,
                # fractional for drawing — a character mid-stride is between
                # tiles on screen and on exactly one tile in the simulation.
                "x": actor.position[0],
                "y": actor.position[1],
                "fx": round(actor.visual_position[0], 3),
                "fy": round(actor.visual_position[1], 3),
                "facing": actor.facing,
                "moving": actor.moving,
                "at_desk": actor.at_desk,
                "state": actor.state.value,
                "state_label": STATE_LABEL[actor.state],
                "activity": actor.activity,
                "activity_label": ACTIVITY_LABEL.get(actor.activity, actor.activity),
                "real": actor.state.is_real_activity,
                "principal": actor.principal,
                "walked_in": actor.walked_in,
                "events": actor.events_seen,
                "detail": actor.last_detail,
            }
            for actor in sorted(sim.floor.actors.values(), key=lambda a: a.actor_id)
        ],
    }


class SimulationRunner:
    """Ticks the simulation on its own thread so the HTTP handler never blocks."""

    def __init__(self, sim: Simulation) -> None:
        self.sim = sim
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            time.sleep(TICK_SECONDS)
            with self._lock:
                self.sim.tick(TICK_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def snapshot_state(self) -> dict[str, Any]:
        with self._lock:
            return state_payload(self.sim)

    def snapshot_world(self) -> dict[str, Any]:
        with self._lock:
            return world_payload(self.sim)


def _handler_factory(runner: SimulationRunner, page: str):  # noqa: ANN202
    class Handler(BaseHTTPRequestHandler):
        server_version = "AjaxHQ-Floor/0.1"

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict[str, Any]) -> None:
            self._send(json.dumps(payload).encode("utf-8"), "application/json")

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/":
                self._send(page.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/world":
                self._json(runner.snapshot_world())
            elif path == "/api/state":
                self._json(runner.snapshot_state())
            elif path == "/healthz":
                self._send(b"ok", "text/plain; charset=utf-8")
            else:
                self._send(b"not found", "text/plain; charset=utf-8", status=404)

        def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def build_server(sim: Simulation, port: int = DEFAULT_GAME_PORT) -> tuple[
    ThreadingHTTPServer, SimulationRunner
]:
    """Construct the game server. Always bound to loopback."""
    from ajax_hq.game.page import PAGE

    runner = SimulationRunner(sim)
    server = ThreadingHTTPServer((LOOPBACK, port), _handler_factory(runner, PAGE))
    return server, runner


def serve(
    snapshot,  # noqa: ANN001 - Snapshot, imported by the caller
    claude_home: Path,
    *,
    port: int = DEFAULT_GAME_PORT,
    live: bool = True,
) -> None:
    """Block, running the floor in a browser on localhost."""
    sim = Simulation.create(snapshot, claude_home, live=live)
    server, runner = build_server(sim, port)
    runner.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()
        server.server_close()
