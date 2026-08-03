"""Build a JSON-serializable snapshot of the iTerm2 window/tab/pane tree."""

from __future__ import annotations

import logging
from pathlib import Path

import iterm2

log = logging.getLogger("iterm2_claude_cockpit.tree")


async def _get_var(obj, name: str) -> str | None:
    try:
        val = await obj.async_get_variable(name)
    except Exception:
        return None
    if val and isinstance(val, str) and val.strip():
        return val
    return None


async def _session_title(session: iterm2.Session) -> str:
    for name in ("autoName", "autoNameFormat", "name"):
        v = await _get_var(session, name)
        if v:
            return v
    return session.session_id


async def _session_status(session: iterm2.Session) -> tuple[str, str]:
    """Return (job, last_line) for a session.

    last_line is the last non-empty visible line (max 120 chars). Both values
    default to empty on failure.
    """
    job = ""
    last_line = ""
    try:
        job = (await session.async_get_variable("jobName")) or ""
    except Exception:
        pass
    try:
        contents = await session.async_get_screen_contents()
        for i in range(contents.number_of_lines - 1, -1, -1):
            # iTerm2 pads empty cells with NUL bytes; treat them as spaces so
            # the line matches what the user visually sees.
            line = contents.line(i).string.replace("\x00", " ").strip()
            if line:
                last_line = line[:120]
                break
    except Exception:
        pass
    return job, last_line


def _path_label(cwd: str) -> str:
    name = Path(cwd).name if cwd else ""
    return (name[:10] + "…") if len(name) > 10 else name


async def _session_node(session: iterm2.Session, active_session_id: str | None) -> dict:
    job, last_line = await _session_status(session)
    cwd = await _get_var(session, "path") or ""
    tty = await _get_var(session, "tty") or ""
    iterm_title = await _session_title(session)
    title = _path_label(cwd)

    return {
        "kind": "session",
        "id": session.session_id,
        "title": title,
        "session_name": iterm_title,
        "active": session.session_id == active_session_id,
        "job": job,
        "last_line": last_line,
        "cwd": cwd,
        "tty": tty,
    }


async def _tab_node(
    tab: iterm2.Tab,
    tab_idx: int,
    active_tab_id,
    active_session_id: str | None,
    tab_names: dict[str, str] | None = None,
) -> dict:
    panes: list[dict] = []
    for session in tab.sessions:
        panes.append(await _session_node(session, active_session_id))

    title = (tab_names or {}).get(str(tab.tab_id)) or f"Tab {tab_idx + 1}"

    return {
        "kind": "tab",
        "id": str(tab.tab_id),
        "title": title,
        "active": str(tab.tab_id) == str(active_tab_id) if active_tab_id is not None else False,
        "panes": panes,
    }


async def build_tree(app: iterm2.App, tab_names: dict[str, str] | None = None) -> dict:
    """Walk the App → Window → Tab → Session tree and return a JSON snapshot."""
    active_window = app.current_terminal_window
    active_window_id = active_window.window_id if active_window else None

    active_tab = active_window.current_tab if active_window else None
    active_tab_id = active_tab.tab_id if active_tab else None

    active_session = active_tab.current_session if active_tab else None
    active_session_id = active_session.session_id if active_session else None

    windows: list[dict] = []
    for win_idx, window in enumerate(app.terminal_windows):
        tabs: list[dict] = []
        for tab_idx, tab in enumerate(window.tabs):
            tabs.append(await _tab_node(tab, tab_idx, active_tab_id, active_session_id, tab_names))

        tab_count = len(tabs)
        suffix = "1 tab" if tab_count == 1 else f"{tab_count} tabs"
        title = f"Window {win_idx + 1} · {suffix}"

        windows.append(
            {
                "kind": "window",
                "id": window.window_id,
                "title": title,
                "active": window.window_id == active_window_id,
                "tabs": tabs,
            }
        )
    return {"windows": windows}
