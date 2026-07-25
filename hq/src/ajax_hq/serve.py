"""Local server for the live view.

Binds ``127.0.0.1`` and nothing else. The page renders full agent prompts and
transcripts, so exposing it on a routable interface would publish everything
those agents were ever asked to do. The bind address is not configurable, and a
test asserts it.

Snapshots are rebuilt per request, rate-limited by a short cache so a browser
polling every twenty seconds does not re-walk the workspace each time.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ajax_hq.collect import collect
from ajax_hq.render import render
from ajax_hq.timeutil import now

log = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8787
MIN_REBUILD_INTERVAL = timedelta(seconds=8)


class SnapshotCache:
    """Rebuilds at most every :data:`MIN_REBUILD_INTERVAL`."""

    def __init__(self, claude_home: Path | None, workspace: Path | None,
                 history_dir: Path | None) -> None:
        self.claude_home = claude_home
        self.workspace = workspace
        self.history_dir = history_dir
        self._lock = threading.Lock()
        self._built_at: datetime | None = None
        self._html: str | None = None
        self._generated: str = ""

    def get(self) -> tuple[str, str]:
        """Return ``(html, generated_stamp)``."""
        with self._lock:
            fresh = self._built_at is None or (now() - self._built_at) > MIN_REBUILD_INTERVAL
            if fresh or self._html is None:
                snapshot = collect(claude_home=self.claude_home, workspace=self.workspace)
                if self.history_dir is not None:
                    from ajax_hq import snapshot as snapshot_mod

                    snapshot_mod.merge_history(snapshot, self.history_dir)
                self._html = render(snapshot, include_text=True, live=True)
                self._generated = snapshot.generated_at.isoformat(timespec="seconds")
                self._built_at = now()
            return self._html, self._generated


def _handler_factory(cache: SnapshotCache):  # noqa: ANN202 - closure over cache
    class Handler(BaseHTTPRequestHandler):
        server_version = "AjaxHQ/0.1"

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # The page embeds transcript text; keep it out of any referrer.
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = self.path.split("?", 1)[0].rstrip("/") or "/"

            if path == "/":
                html, _ = cache.get()
                self._send(html.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/generated":
                _, generated = cache.get()
                self._send(generated.encode("utf-8"), "text/plain; charset=utf-8")
            elif path == "/healthz":
                self._send(b"ok", "text/plain; charset=utf-8")
            else:
                self._send(b"not found", "text/plain; charset=utf-8", status=404)

        def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def build_server(
    port: int = DEFAULT_PORT,
    *,
    claude_home: Path | None = None,
    workspace: Path | None = None,
    history_dir: Path | None = None,
) -> ThreadingHTTPServer:
    """Construct the server. Always bound to loopback."""
    cache = SnapshotCache(claude_home, workspace, history_dir)
    return ThreadingHTTPServer((LOOPBACK, port), _handler_factory(cache))


def serve(
    port: int = DEFAULT_PORT,
    *,
    claude_home: Path | None = None,
    workspace: Path | None = None,
    history_dir: Path | None = None,
) -> None:
    """Block, serving the dashboard on localhost."""
    server = build_server(
        port, claude_home=claude_home, workspace=workspace, history_dir=history_dir
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
