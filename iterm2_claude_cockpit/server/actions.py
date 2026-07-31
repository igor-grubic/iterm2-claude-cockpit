"""Action handlers invoked from the webview via HTTP POST."""

from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

import iterm2

if TYPE_CHECKING:
    from .http import State


async def focus_node(app: iterm2.App, kind: str, node_id: str) -> dict:
    if kind == "session":
        session = app.get_session_by_id(node_id)
        if session is None:
            return {"ok": False, "error": f"no session {node_id}"}
        await session.async_activate(select_tab=True, order_window_front=True)
        return {"ok": True}

    if kind == "tab":
        tab = app.get_tab_by_id(node_id)
        if tab is None:
            return {"ok": False, "error": f"no tab {node_id}"}
        await tab.async_select(order_window_front=True)
        return {"ok": True}

    if kind == "window":
        window = app.get_window_by_id(node_id)
        if window is None:
            return {"ok": False, "error": f"no window {node_id}"}
        await window.async_activate()
        return {"ok": True}

    return {"ok": False, "error": f"unknown kind {kind!r}"}


async def new_tab(app: iterm2.App, window_id: str | None = None) -> dict:
    window = app.get_window_by_id(window_id) if window_id else app.current_terminal_window
    if window is None:
        return {"ok": False, "error": "no target window"}
    tab = await window.async_create_tab()
    return {"ok": True, "tab_id": str(tab.tab_id)}


async def new_window(connection: iterm2.Connection) -> dict:
    window = await iterm2.Window.async_create(connection)
    return {"ok": True, "window_id": window.window_id if window else None}


async def get_session_lines(app: iterm2.App, session_id: str, count: int = 10) -> dict:
    session = app.get_session_by_id(session_id)
    if session is None:
        return {"ok": False, "error": f"no session {session_id}"}
    try:
        contents = await session.async_get_screen_contents()
        lines = []
        for i in range(contents.number_of_lines - 1, -1, -1):
            line = contents.line(i).string.strip()
            if line:
                lines.append(line)
            if len(lines) >= count:
                break
        lines.reverse()
        return {"ok": True, "lines": lines}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def split_pane(app: iterm2.App, session_id: str, vertical: bool) -> dict:
    session = app.get_session_by_id(session_id)
    if session is None:
        return {"ok": False, "error": f"no session {session_id}"}
    try:
        new_session = await session.async_split_pane(vertical=vertical)
        return {"ok": True, "session_id": new_session.session_id if new_session else None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def close_session(app: iterm2.App, session_id: str) -> dict:
    session = app.get_session_by_id(session_id)
    if session is None:
        return {"ok": False, "error": f"no session {session_id}"}
    await session.async_close(force=True)
    return {"ok": True}


async def move_tab(app: iterm2.App, window_id: str, tab_id: str, position: int) -> dict:
    window = app.get_window_by_id(window_id)
    if window is None:
        return {"ok": False, "error": f"no window {window_id}"}
    tabs = list(window.tabs)
    tab = next((t for t in tabs if str(t.tab_id) == tab_id), None)
    if tab is None:
        return {"ok": False, "error": f"no tab {tab_id}"}
    tabs.remove(tab)
    tabs.insert(max(0, min(position, len(tabs))), tab)
    await asyncio.wait_for(window.async_set_tabs(tabs), timeout=3.0)
    return {"ok": True}


def _transcript_exists(cwd: str, session_id: str) -> bool:
    """True if Claude Code has a saved transcript for this cwd + session id.

    Claude stores transcripts at ~/.claude/projects/<encoded-cwd>/<id>.jsonl,
    where the cwd is encoded by replacing every non-alphanumeric character with
    "-" (no collapsing of runs — so "/a/.b" → "-a--b").
    """
    if not cwd or not session_id:
        return False
    encoded = re.sub(r"[^a-zA-Z0-9]", "-", cwd)
    return (Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl").is_file()


def build_launch_line(cwd: str, session_id: str, resume: bool) -> str:
    """The shell line to send into a restored pane (empty if nothing to do).

    Resumes Claude only when `resume` is set; otherwise just restores the cwd so
    non-Claude panes (and Claude panes whose transcript is gone) come up as a
    plain shell in the right directory.
    """
    parts: list[str] = []
    if cwd:
        parts.append(f"cd {shlex.quote(cwd)}")
    if resume and session_id:
        parts.append(f"claude --resume {session_id}")
    return (" && ".join(parts) + "\n") if parts else ""


def _live_claude_session_ids(snapshot: dict) -> set[str]:
    """Claude session ids currently open, so restore never double-resumes one."""
    ids: set[str] = set()
    for window in snapshot.get("windows", []):
        for tab in window.get("tabs", []):
            for pane in tab.get("panes", []):
                sid = pane.get("ext.claude.session_id")
                if sid:
                    ids.add(sid)
    return ids


async def restore_workspace(
    connection: iterm2.Connection,
    app: iterm2.App,
    state_data: dict,
    state: State,
) -> dict:
    """Recreate persisted windows/tabs/panes and resume each Claude session.

    Always creates fresh windows — it never touches existing ones — so the user
    controls when to bring a workspace back. Each Claude pane whose transcript
    still exists (and isn't already open) is resumed via `claude --resume`; every
    other pane is recreated as a shell in its saved cwd.
    """
    windows_data = state_data.get("windows", []) if isinstance(state_data, dict) else []
    if not windows_data:
        return {"ok": False, "error": "no saved workspace"}

    live_ids = _live_claude_session_ids(state.get_snapshot())
    restored = 0  # panes recreated
    resumed = 0  # Claude sessions resumed
    skipped = 0  # Claude panes recreated cwd-only (already open or transcript gone)
    errors: list[str] = []

    for win_idx, win in enumerate(windows_data):
        tabs = win.get("tabs", [])
        if not tabs:
            continue
        # Isolate per-window failures: a transient error on one window must not
        # abort the whole restore (and discard already-restored windows + counts,
        # which would also invite a duplicate-creating re-click).
        try:
            window = await iterm2.Window.async_create(connection)
            if window is None or not window.tabs:
                errors.append(f"window {win_idx + 1}: could not create")
                continue

            for tab_idx, tab_data in enumerate(tabs):
                tab = window.tabs[0] if tab_idx == 0 else await window.async_create_tab()
                if tab is None or not tab.sessions:
                    continue
                first_session = tab.sessions[0]

                for pane_idx, pane in enumerate(tab_data.get("panes", [])):
                    session = first_session if pane_idx == 0 else await first_session.async_split_pane(vertical=True)
                    if session is None:
                        continue
                    cwd = pane.get("cwd", "")
                    sid = pane.get("session_id", "")
                    is_claude = bool(pane.get("claude"))
                    resume = is_claude and _transcript_exists(cwd, sid) and sid not in live_ids
                    if is_claude and not resume:
                        skipped += 1
                    line = build_launch_line(cwd, sid, resume)
                    if line:
                        await asyncio.sleep(0.15)  # let the shell prompt appear before typing
                        await session.async_send_text(line)
                    if resume:
                        resumed += 1
                    restored += 1

                name = tab_data.get("name")
                if name:
                    with state.lock:
                        state.tab_names[str(tab.tab_id)] = name
        except Exception as exc:
            errors.append(f"window {win_idx + 1}: {exc}")
            continue

    result: dict = {"ok": True, "restored": restored, "resumed": resumed, "skipped": skipped}
    if errors:
        result["errors"] = errors
    return result
