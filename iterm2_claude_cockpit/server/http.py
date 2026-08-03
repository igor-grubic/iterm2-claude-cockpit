"""HTTP server using Python stdlib only (no external deps).

The webview polls /api/tree for the current snapshot. The snapshot is kept
fresh by iTerm2 notification callbacks running on the main asyncio loop;
HTTP handlers (running in worker threads) just read the cached dict.

Action endpoints (focus, new tab, etc.) call into the iterm2 async API by
scheduling coroutines onto the captured asyncio loop via
asyncio.run_coroutine_threadsafe and waiting for the result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import iterm2

from . import actions, persistence, tree

log = logging.getLogger("iterm2_claude_cockpit.http")

WEBVIEW_DIR = Path(__file__).resolve().parent.parent / "webview"
_PLUGIN_VERSION = "0.1.0"  # keep in sync with __init__.py and pyproject.toml

# Minimum seconds between workspace-state writes (layout changes are debounced).
_PERSIST_DEBOUNCE_SECONDS = 5.0

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class State:
    """Shared mutable state passed to the HTTP handler via the server instance."""

    def __init__(
        self,
        connection: iterm2.Connection,
        app: iterm2.App,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.connection = connection
        self.app = app
        self.loop = loop
        self.snapshot: dict[str, Any] = {"windows": []}
        self.tab_names: dict[str, str] = {}  # tab_id → custom name
        self.lock = threading.Lock()
        # Set True while a structural write (e.g. move-tab) is in flight to prevent
        # layout-change notifications from flooding iTerm2 with concurrent reads.
        self.suppress_refresh: bool = False
        # Workspace-state persistence: debounce bookkeeping (see refresh()).
        self._last_persist_ts: float = 0.0
        self._last_persisted_sig: str = ""
        self._start_monotonic: float = time.monotonic()
        # Two files on disk (see persistence.py): state.json mirrors the *current*
        # layout (overwritten ~1s after launch with the degraded post-relaunch one),
        # while restore.json holds the last-good layout the rolling save never
        # touches. Restore reads this in-memory copy of restore.json, frozen at
        # startup, so it always brings back the *previous* session, not this one as
        # it evolves. Fall back to state.json for installs predating the split.
        restore = persistence.load_restore()
        if restore is None:
            restore = persistence.load_state()
        self.restore_snapshot: dict[str, Any] | None = restore
        self._restore_pane_count: int = persistence.count_panes(restore)
        # Seed in-memory metadata from it so custom tab names survive a daemon
        # restart (correct when iTerm2 stayed up; harmlessly ignored when its ids
        # are stale after a full iTerm2 restart).
        if self.restore_snapshot:
            self.tab_names = {str(k): str(v) for k, v in (self.restore_snapshot.get("tab_names") or {}).items()}

    async def refresh(self) -> None:
        with self.lock:
            tab_names = dict(self.tab_names)
        try:
            snap = await tree.build_tree(self.app, tab_names)
        except Exception as exc:
            log.exception("tree build failed: %s", exc)
            return
        with self.lock:
            self.snapshot = snap
        self._maybe_persist(snap, tab_names)

    def _maybe_persist(self, snap: dict[str, Any], tab_names: dict[str, str]) -> None:
        """Persist workspace state to disk, debounced and only when it changed.

        Runs the disk write on the default executor so it never blocks the loop.
        """
        now = time.monotonic()
        if now - self._last_persist_ts < _PERSIST_DEBOUNCE_SECONDS:
            return
        try:
            data = persistence.build_state(snap, tab_names)
            sig = json.dumps(data, sort_keys=True)
        except Exception:
            log.exception("building persist state failed")
            return
        self._last_persist_ts = now
        if sig == self._last_persisted_sig:
            return
        self._last_persisted_sig = sig
        # Always mirror the current layout to state.json.
        self.loop.run_in_executor(None, persistence.save_state, data)
        # Update the separate restore snapshot only when safe (never empty; shrinks
        # held off until past the startup grace window), so the single-window layout
        # iTerm2 relaunches with can't wipe the workspace before Restore is used.
        live_panes = persistence.count_panes(data)
        uptime = now - self._start_monotonic
        if persistence.should_update_restore(live_panes, self._restore_pane_count, uptime):
            self._restore_pane_count = live_panes
            self.loop.run_in_executor(None, persistence.save_restore, data)

    def get_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return self.snapshot

    def call_async(self, coro_factory: Callable[[], Any], timeout: float = 5.0) -> Any:
        """Schedule an async callable onto the iterm2 loop and wait for the result."""
        future = asyncio.run_coroutine_threadsafe(coro_factory(), self.loop)
        return future.result(timeout=timeout)


class _Handler(BaseHTTPRequestHandler):
    server_version = "iterm-workflow/0.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log.debug("%s - %s", self.address_string(), format % args)

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404, f"not found: {path.name}")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self) -> None:
        self._send_file(WEBVIEW_DIR / "index.html")

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_index()
            return
        if path == "/static/app.js":
            self._send_file(WEBVIEW_DIR / "app.js")
            return
        if path == "/static/styles.css":
            self._send_file(WEBVIEW_DIR / "styles.css")
            return
        if path == "/static/iterm_cheatsheet.html":
            self._send_file(WEBVIEW_DIR / "iterm_cheatsheet.html")
            return
        if path == "/static/claude_cheatsheet.html":
            self._send_file(WEBVIEW_DIR / "claude_cheatsheet.html")
            return
        if path == "/api/tree":
            self._send_json(self.state.get_snapshot())
            return
        if path == "/api/about":
            self._send_json({"version": _PLUGIN_VERSION})
            return
        if path == "/api/restore-preview":
            snap = self.state.restore_snapshot
            if not snap:
                self._send_json({"ok": False, "error": "no saved workspace"})
                return
            windows = snap.get("windows", [])
            all_tabs = [t for w in windows for t in w.get("tabs", [])]
            all_panes = [p for t in all_tabs for p in t.get("panes", [])]
            self._send_json(
                {
                    "ok": True,
                    "windows": len(windows),
                    "tabs": len(all_tabs),
                    "panes": len(all_panes),
                }
            )
            return
        if path == "/api/session-lines":
            qs = parse_qs(urlparse(self.path).query)
            session_id = (qs.get("id") or [""])[0]
            try:
                result = self.state.call_async(lambda: actions.get_session_lines(self.state.app, session_id))
                self._send_json(result)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        body = self._read_json_body()
        try:
            if path == "/api/focus":
                kind = body.get("kind") or ""
                node_id = body.get("id") or ""
                result = self.state.call_async(lambda: actions.focus_node(self.state.app, kind, node_id))
                self._send_json(result)
                return
            if path == "/api/new-tab":
                result = self.state.call_async(lambda: actions.new_tab(self.state.app, body.get("window_id")))
                self._send_json(result)
                return
            if path == "/api/new-window":
                result = self.state.call_async(lambda: actions.new_window(self.state.connection))
                self._send_json(result)
                return
            if path == "/api/restore":
                data = self.state.restore_snapshot
                if not data:
                    self._send_json({"ok": False, "error": "no saved workspace"})
                    return
                result = self.state.call_async(
                    lambda: actions.restore_workspace(self.state.connection, self.state.app, data, self.state),
                    timeout=60.0,
                )
                self._send_json(result)
                return
            if path == "/api/split-pane":
                sid = body.get("id", "")
                vertical = bool(body.get("vertical", True))
                result = self.state.call_async(lambda: actions.split_pane(self.state.app, sid, vertical))
                self._send_json(result)
                return
            if path == "/api/close-session":
                sid = body.get("id", "")
                result = self.state.call_async(lambda: actions.close_session(self.state.app, sid))
                self._send_json(result)
                return
            if path == "/api/move-tab":
                tab_id = body.get("tab_id", "")
                window_id = body.get("window_id", "")
                position = int(body.get("position", 0))

                async def _move() -> dict:
                    self.state.suppress_refresh = True
                    try:
                        return await actions.move_tab(self.state.app, window_id, tab_id, position)
                    finally:
                        self.state.suppress_refresh = False
                        await self.state.refresh()

                result = self.state.call_async(_move, timeout=8.0)
                self._send_json(result)
                return
            if path == "/api/rename-tab":
                tab_id = body.get("id", "")
                name = (body.get("name") or "").strip()
                if not tab_id:
                    for w in self.state.get_snapshot().get("windows", []):
                        if w.get("active"):
                            for t in w.get("tabs", []):
                                if t.get("active"):
                                    tab_id = t["id"]
                                    break
                if not tab_id:
                    self._send_json({"ok": False, "error": "no tab id"})
                    return
                with self.state.lock:
                    if name:
                        self.state.tab_names[tab_id] = name
                    else:
                        self.state.tab_names.pop(tab_id, None)
                self._send_json({"ok": True})
                return
        except Exception as exc:
            log.exception("action failed: %s", exc)
            self._send_json({"ok": False, "error": str(exc)}, status=500)
            return
        self.send_error(404)


def start_server_thread(state: State, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, name="iterm-workflow-http", daemon=True)
    thread.start()
    log.info("serving on http://%s:%d/", host, port)
    return server
