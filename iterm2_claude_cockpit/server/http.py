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

from extensions import _loader as ext_loader
from extensions._api import Registry

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
        registry: Registry | None = None,
    ) -> None:
        self.connection = connection
        self.app = app
        self.loop = loop
        self.registry: Registry = registry if registry is not None else Registry()
        self.snapshot: dict[str, Any] = {"windows": []}
        self.buried_positions: dict[str, str] = {}  # session_id → tab_id
        self.tab_names: dict[str, str] = {}  # tab_id → custom name
        self.lock = threading.Lock()
        # Set True while a structural write (e.g. move-tab) is in flight to prevent
        # layout-change notifications from flooding iTerm2 with concurrent reads.
        self.suppress_refresh: bool = False
        # Workspace-state persistence: debounce bookkeeping (see refresh()).
        self._last_persist_ts: float = 0.0
        self._last_persisted_sig: str = ""
        # Snapshot of the last saved workspace, loaded once at startup. Restore reads
        # THIS in-memory copy, not the file: the rolling auto-save in refresh()
        # overwrites the file with the current (post-restart, Claude-less) layout
        # within ~1s of launch, so reading the file at restore time would return the
        # degraded layout instead of the pre-close one we want to bring back.
        self.restore_snapshot: dict[str, Any] | None = persistence.load_state()
        # Seed in-memory metadata from it so custom tab names and buried positions
        # survive a daemon restart (correct when iTerm2 stayed up; harmlessly ignored
        # when its ids are stale after a full iTerm2 restart).
        if self.restore_snapshot:
            self.tab_names = {str(k): str(v) for k, v in (self.restore_snapshot.get("tab_names") or {}).items()}
            self.buried_positions = {
                str(k): str(v) for k, v in (self.restore_snapshot.get("buried_positions") or {}).items()
            }

    async def refresh(self) -> None:
        with self.lock:
            buried_pos = dict(self.buried_positions)
            tab_names = dict(self.tab_names)
        try:
            snap = await tree.build_tree(self.app, buried_pos, self.registry, tab_names)
        except Exception as exc:
            log.exception("tree build failed: %s", exc)
            return
        with self.lock:
            self.snapshot = snap
        self._maybe_persist(snap, tab_names, buried_pos)

    def _maybe_persist(self, snap: dict[str, Any], tab_names: dict[str, str], buried_pos: dict[str, str]) -> None:
        """Persist workspace state to disk, debounced and only when it changed.

        Runs the disk write on the default executor so it never blocks the loop.
        """
        now = time.monotonic()
        if now - self._last_persist_ts < _PERSIST_DEBOUNCE_SECONDS:
            return
        try:
            data = persistence.build_state(snap, tab_names, buried_pos)
            sig = json.dumps(data, sort_keys=True)
        except Exception:
            log.exception("building persist state failed")
            return
        self._last_persist_ts = now
        if sig == self._last_persisted_sig:
            return
        self._last_persisted_sig = sig
        self.loop.run_in_executor(None, persistence.save_state, data)

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
        html_path = WEBVIEW_DIR / "index.html"
        if not html_path.is_file():
            self.send_error(404, "index.html missing")
            return
        html = html_path.read_text(encoding="utf-8")
        css_tags: list[str] = []
        js_tags: list[str] = []
        for name, kind, relpath in self.state.registry.webview_assets:
            url = f"/static/ext/{name}/{relpath}"
            if kind == "css":
                css_tags.append(f'<link rel="stylesheet" href="{url}" />')
            elif kind == "js":
                js_tags.append(f'<script src="{url}"></script>')
        html = html.replace("<!-- ext:css -->", "\n  ".join(css_tags))
        html = html.replace("<!-- ext:js -->", "\n  ".join(js_tags))
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES[".html"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_ext_static(self, path: str) -> None:
        rest = path[len("/static/ext/") :]
        if "/" not in rest:
            self.send_error(404)
            return
        name, rel = rest.split("/", 1)
        base = self.state.registry.static_dirs.get(name)
        if base is None or not rel:
            self.send_error(404)
            return
        target = (base / rel).resolve()
        try:
            if not target.is_relative_to(base):
                self.send_error(404)
                return
        except ValueError:
            self.send_error(404)
            return
        self._send_file(target)

    def _dispatch_ext_route(self, method: str, path: str, body: dict | None) -> bool:
        handler = self.state.registry.routes.get((method, path))
        if handler is None:
            return False
        query = parse_qs(urlparse(self.path).query)
        try:

            async def _invoke() -> Any:
                result = handler(self.state, body or {}, query)
                if asyncio.iscoroutine(result):
                    result = await result
                return result

            result = self.state.call_async(_invoke)
            if not isinstance(result, dict):
                result = {"ok": False, "error": "handler did not return dict"}
            self._send_json(result)
        except Exception as exc:
            log.exception("ext route %s %s failed", method, path)
            self._send_json({"ok": False, "error": str(exc)}, status=500)
        return True

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
        if path.startswith("/static/ext/"):
            self._serve_ext_static(path)
            return
        if path == "/api/tree":
            self._send_json(self.state.get_snapshot())
            return
        if path == "/api/about":
            self._send_json(
                {
                    "version": _PLUGIN_VERSION,
                    "extensions": {
                        "enabled": ext_loader.list_enabled(),
                        "available": ext_loader.list_available(),
                    },
                }
            )
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
                    "claude": sum(1 for p in all_panes if p.get("claude")),
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
        if path.startswith("/api/ext/"):
            if self._dispatch_ext_route("GET", path, None):
                return
            self.send_error(404)
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
            if path == "/api/bury-session":
                sid = body.get("id", "")
                tab_id = body.get("tab_id", "")
                result = self.state.call_async(lambda: actions.bury_session(self.state.connection, self.state.app, sid))
                if result.get("ok"):
                    with self.state.lock:
                        self.state.buried_positions[sid] = tab_id
                self._send_json(result)
                return
            if path == "/api/unbury-session":
                sid = body.get("id", "")
                result = self.state.call_async(
                    lambda: actions.unbury_session(self.state.connection, self.state.app, sid)
                )
                if result.get("ok"):
                    with self.state.lock:
                        self.state.buried_positions.pop(sid, None)
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
            if path.startswith("/api/ext/"):
                if self._dispatch_ext_route("POST", path, body):
                    return
                self.send_error(404)
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
