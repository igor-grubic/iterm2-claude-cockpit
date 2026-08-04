"""Durable, on-disk persistence of the workspace layout for session restore.

The daemon holds everything in memory and dies when iTerm2 closes. This module
serializes a minimal layout snapshot (windows → tabs → panes, each pane's cwd)
plus the in-memory `tab_names`/`tab_colors`/`tab_collapsed` to disk, so a later
"Restore workspace" action can recreate the layout with each pane back in its
saved working directory, with custom names/colors/collapsed state intact.

Two files are kept, side by side:

- `state.json`   — a rolling mirror of the *current* live layout, rewritten on
  every refresh. After a relaunch iTerm2 typically comes back with a single
  window, so within ~1s this file reflects that degraded layout.
- `restore.json` — the last-good layout the Restore action recreates. The rolling
  save never writes it; it is only updated through `should_update_restore`, which
  refuses to let the degraded post-relaunch layout overwrite the workspace before
  the user has had a chance to restore it.

Stdlib only. All functions swallow their own errors — persistence must never crash
the daemon or interrupt a refresh.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("iterm2_claude_cockpit.persistence")

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
STATE_DIR = _CONFIG_HOME / "iterm2-claude-cockpit"
STATE_PATH = STATE_DIR / "state.json"  # rolling mirror of the current live layout
RESTORE_PATH = STATE_DIR / "restore.json"  # last-good layout the Restore action recreates
SETTINGS_PATH = STATE_DIR / "settings.json"  # plain UI preferences (e.g. panel theme)

# Panel visual themes selectable in Settings. "2a" is the modern redesign (default);
# "1a" is the classic terminal-styled alternative. See webview/app.js's THEMES table.
VALID_THEMES = ("2a", "1a")
DEFAULT_THEME = "2a"

# Bump when the on-disk shape changes incompatibly; load_state tolerates older
# files by simply returning whatever parsed (restore is defensive about fields).
STATE_VERSION = 1

# A shrink in the live layout is only adopted into restore.json once the daemon has
# been up this long, so the single-window layout iTerm2 relaunches with cannot
# overwrite the saved workspace before the user gets a chance to restore it. Growth
# is always adopted immediately; this grace window only gates shrinks.
RESTORE_FREEZE_SECONDS = 600.0


def build_state(
    snapshot: dict[str, Any],
    tab_names: dict[str, str],
    tab_colors: dict[str, int] | None = None,
    tab_collapsed: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build the persistable layout dict from the live snapshot (no timestamp).

    Derives entirely from the already-built snapshot — never re-walks iTerm2.

    The result is deterministic, so callers can diff it to debounce writes.
    """
    tab_colors = tab_colors or {}
    tab_collapsed = tab_collapsed or {}
    windows: list[dict] = []
    live_tab_ids: set[str] = set()
    for window in snapshot.get("windows", []):
        tabs: list[dict] = []
        for tab in window.get("tabs", []):
            live_tab_ids.add(str(tab.get("id", "")))
            panes: list[dict] = []
            for pane in tab.get("panes", []):
                panes.append({"cwd": pane.get("cwd", "")})
            # Persist name/color/collapsed keyed by the live tab id, which dies on
            # restart — these are what restore re-applies to the freshly created tab.
            tab_id = tab.get("id", "")
            tabs.append(
                {
                    "name": tab_names.get(tab_id),
                    "color": tab_colors.get(tab_id),
                    "collapsed": tab_collapsed.get(tab_id, False),
                    "panes": panes,
                }
            )
        windows.append({"tabs": tabs})

    # Prune tab_names/tab_colors/tab_collapsed to ids still present in the live
    # snapshot, so none of them can accumulate dead-tab keys forever across restarts.
    return {
        "version": STATE_VERSION,
        "windows": windows,
        "tab_names": {k: v for k, v in tab_names.items() if str(k) in live_tab_ids},
        "tab_colors": {k: v for k, v in tab_colors.items() if str(k) in live_tab_ids},
        "tab_collapsed": {k: v for k, v in tab_collapsed.items() if str(k) in live_tab_ids},
    }


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Atomically write `data` (stamped with saved_at) to `path` via tmp + replace."""
    data = {**data, "saved_at": int(time.time())}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON dict from `path`, or None if missing/unreadable."""
    try:
        if not path.is_file():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        log.exception("failed to read %s", path.name)
        return None


def save_state(data: dict[str, Any]) -> None:
    """Atomically write the rolling live-layout mirror (state.json)."""
    try:
        _write_json_atomic(STATE_PATH, data)
    except Exception:
        log.exception("failed to persist live workspace state")


def load_state() -> dict[str, Any] | None:
    """Read the live-layout mirror (state.json), or None if missing/unreadable."""
    return _read_json(STATE_PATH)


def save_restore(data: dict[str, Any]) -> None:
    """Atomically write the restore snapshot (restore.json).

    This is the layout the Restore action recreates. It is kept separate from the
    rolling live mirror so the degraded layout iTerm2 relaunches with can never
    overwrite it (see should_update_restore for the write policy).
    """
    try:
        _write_json_atomic(RESTORE_PATH, data)
    except Exception:
        log.exception("failed to persist restore snapshot")


def load_restore() -> dict[str, Any] | None:
    """Read the restore snapshot (restore.json), or None if missing/unreadable."""
    return _read_json(RESTORE_PATH)


def normalize_theme(value: Any) -> str:
    """Return `value` if it's a recognized theme id, else the default theme."""
    return value if value in VALID_THEMES else DEFAULT_THEME


def save_settings(data: dict[str, Any]) -> None:
    """Atomically write UI preferences (settings.json), e.g. the selected theme."""
    try:
        _write_json_atomic(SETTINGS_PATH, data)
    except Exception:
        log.exception("failed to persist settings")


def load_settings() -> dict[str, Any] | None:
    """Read UI preferences (settings.json), or None if missing/unreadable."""
    return _read_json(SETTINGS_PATH)


def count_panes(state: dict[str, Any] | None) -> int:
    """Total panes across all tabs/windows in a built state dict (see build_state)."""
    if not state:
        return 0
    return sum(len(tab.get("panes", [])) for win in state.get("windows", []) for tab in win.get("tabs", []))


def should_update_restore(
    live_panes: int,
    restore_panes: int,
    uptime_seconds: float,
    freeze_seconds: float = RESTORE_FREEZE_SECONDS,
) -> bool:
    """Decide whether the current live layout may overwrite the restore snapshot.

    The restore snapshot must outlive the degraded layout iTerm2 relaunches with
    (usually a single window) long enough for the user to click Restore, so:

    - An empty layout never becomes the restore source.
    - Growth — the live layout is at least as large as the saved one — is always
      adopted; this is how restore.json captures the workspace as it is built up.
    - A shrink is adopted only once the daemon has been up past `freeze_seconds`,
      so the post-relaunch transient can't clobber the snapshot, while a genuinely
      reduced workspace is still tracked (for recency) after the grace period.
    """
    if live_panes == 0:
        return False
    if live_panes >= restore_panes:
        return True
    return uptime_seconds >= freeze_seconds
