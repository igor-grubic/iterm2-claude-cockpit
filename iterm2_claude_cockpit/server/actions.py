"""Action handlers invoked from the webview via HTTP POST."""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import iterm2

from . import directories

if TYPE_CHECKING:
    from .http import State


def open_url(url: str) -> dict:
    """Open an http(s) URL in the user's default browser via macOS `open`.

    Synchronous (no iterm2 loop needed). Only http/https is allowed so the
    endpoint can't be coaxed into launching arbitrary local handlers.
    """
    if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
        return {"ok": False, "error": "only http(s) urls are allowed"}
    try:
        subprocess.run(["open", url], check=True, capture_output=True, timeout=5.0)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


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


def _restore_profile(pane: dict, location: str, errors: list[str]) -> iterm2.LocalWriteOnlyProfile | None:
    try:
        cwd = directories.restore_cwd(pane.get("cwd"))
    except ValueError as exc:
        errors.append(f"{location}: {exc}; opened in the profile's default directory")
        return None
    profile = iterm2.LocalWriteOnlyProfile()
    profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
    profile.set_custom_directory(cwd)
    return profile


async def restore_workspace(
    connection: iterm2.Connection,
    app: iterm2.App,
    state_data: dict,
    state: State,
) -> dict:
    """Recreate persisted windows/tabs/panes, each as a shell in its saved cwd.

    Always creates fresh windows — it never touches existing ones — so the user
    controls when to bring a workspace back.
    """
    windows_data = state_data.get("windows", []) if isinstance(state_data, dict) else []
    if not windows_data:
        return {"ok": False, "error": "no saved workspace"}

    restored = 0  # panes recreated
    errors: list[str] = []

    for win_idx, win in enumerate(windows_data):
        tabs = win.get("tabs", [])
        if not tabs:
            continue
        # Isolate per-window failures: a transient error on one window must not
        # abort the whole restore (and discard already-restored windows + counts,
        # which would also invite a duplicate-creating re-click).
        try:
            first_panes = tabs[0].get("panes") or [{}]
            first_profile = _restore_profile(first_panes[0], f"window {win_idx + 1}, tab 1, pane 1", errors)
            window = await iterm2.Window.async_create(connection, profile_customizations=first_profile)
            if window is None or not window.tabs:
                errors.append(f"window {win_idx + 1}: could not create")
                continue

            for tab_idx, tab_data in enumerate(tabs):
                panes = tab_data.get("panes") or [{}]
                location = f"window {win_idx + 1}, tab {tab_idx + 1}"
                profile = first_profile if tab_idx == 0 else _restore_profile(panes[0], f"{location}, pane 1", errors)
                tab = window.tabs[0] if tab_idx == 0 else await window.async_create_tab(profile_customizations=profile)
                if tab is None or not tab.sessions:
                    errors.append(f"{location}: could not create tab")
                    continue
                first_session = tab.sessions[0]

                for pane_idx, pane in enumerate(panes):
                    session = (
                        first_session
                        if pane_idx == 0
                        else await first_session.async_split_pane(
                            vertical=True,
                            profile_customizations=_restore_profile(pane, f"{location}, pane {pane_idx + 1}", errors),
                        )
                    )
                    if session is None:
                        errors.append(f"{location}, pane {pane_idx + 1}: could not create pane")
                        continue
                    restored += 1

                tab_id = str(tab.tab_id)
                name = tab_data.get("name")
                color = tab_data.get("color")
                collapsed = tab_data.get("collapsed")
                sleeping = tab_data.get("sleeping", False)
                with state.lock:
                    if name:
                        state.tab_names[tab_id] = name
                    if color is not None:
                        state.tab_colors[tab_id] = int(color)
                    if collapsed or sleeping:
                        state.tab_collapsed[tab_id] = True
                    if sleeping:
                        state.tab_sleeping[tab_id] = True
        except Exception as exc:
            errors.append(f"window {win_idx + 1}: {exc}")
            continue

    result: dict = {"ok": True, "restored": restored}
    if errors:
        result["errors"] = errors
    return result
