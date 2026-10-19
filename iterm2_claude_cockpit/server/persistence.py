"""Durable, on-disk persistence of the workspace layout for session restore.

The daemon holds everything in memory and dies when iTerm2 closes. This module
serializes a minimal layout snapshot (windows → tabs → panes, each pane's cwd)
plus the in-memory `tab_names`/`tab_colors`/`tab_collapsed`/`tab_sleeping` to disk, so a later
"Restore workspace" action can recreate the layout with each pane back in its
saved working directory, with custom names/colors/collapsed/sleeping state intact.

Two files are kept, side by side, plus bounded startup backups:

- `state.json`   — a rolling mirror of the *current* live layout, rewritten on
  every refresh. After a relaunch iTerm2 typically comes back with a single
  window, so within ~1s this file reflects that degraded layout.
- `restore.json` — the last-good layout the Restore action recreates. The rolling
  save never writes it; it is only updated through `should_update_restore`, which
  refuses to let the degraded post-relaunch layout overwrite the workspace before
  the user has had a chance to restore it.
- `restore-backups/` — the last ten startup or pre-loss restore sources.

Stdlib only. All functions swallow their own errors — persistence must never crash
the daemon or interrupt a refresh.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

log = logging.getLogger("iterm2_claude_cockpit.persistence")

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
STATE_DIR = _CONFIG_HOME / "iterm2-claude-cockpit"
STATE_PATH = STATE_DIR / "state.json"  # rolling mirror of the current live layout
RESTORE_PATH = STATE_DIR / "restore.json"  # last-good layout the Restore action recreates
SETTINGS_PATH = STATE_DIR / "settings.json"  # plain UI preferences (e.g. panel theme)
RESTORE_BACKUP_DIR = STATE_DIR / "restore-backups"
RESTORE_BACKUP_LIMIT = 10

# Panel visual themes selectable in Settings. "1a" is the classic terminal-styled
# look (default); "2a" is the modern redesign. See webview/app.js's THEMES table.
VALID_THEMES = ("2a", "1a")
DEFAULT_THEME = "1a"

# Bump when the on-disk shape changes incompatibly; load_state tolerates older
# files by simply returning whatever parsed (restore is defensive about fields).
STATE_VERSION = 1

# A shrink in the live layout is only adopted into restore.json once the daemon has
# been up this long, so the single-window layout iTerm2 relaunches with cannot
# overwrite the saved workspace before the user gets a chance to restore it. Growth
# with surviving session identities is adopted immediately; new sessions and
# metadata loss are also held off during this grace window.
RESTORE_FREEZE_SECONDS = 600.0


def build_state(
    snapshot: dict[str, Any],
    tab_names: dict[str, str],
    tab_colors: dict[str, int] | None = None,
    tab_collapsed: dict[str, bool] | None = None,
    tab_sleeping: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build the persistable layout dict from the live snapshot (no timestamp).

    Derives entirely from the already-built snapshot — never re-walks iTerm2.

    The result is deterministic, so callers can diff it to debounce writes.
    """
    tab_colors = tab_colors or {}
    tab_collapsed = tab_collapsed or {}
    tab_sleeping = tab_sleeping or {}
    windows: list[dict] = []
    live_tab_ids: set[str] = set()
    for window in snapshot.get("windows", []):
        tabs: list[dict] = []
        for tab in window.get("tabs", []):
            live_tab_ids.add(str(tab.get("id", "")))
            panes: list[dict] = []
            for pane in tab.get("panes", []):
                panes.append({"id": pane.get("id", ""), "cwd": pane.get("cwd", "")})
            # Persist metadata keyed by the live tab id, which dies on
            # restart — these are what restore re-applies to the freshly created tab.
            tab_id = tab.get("id", "")
            tabs.append(
                {
                    "id": tab_id,
                    "name": tab_names.get(tab_id),
                    "color": tab_colors.get(tab_id),
                    "collapsed": tab_sleeping.get(tab_id, False) or tab_collapsed.get(tab_id, False),
                    "sleeping": tab_sleeping.get(tab_id, False),
                    "panes": panes,
                }
            )
        windows.append({"tabs": tabs})

    # Prune metadata to ids still present in the live
    # snapshot, so none of them can accumulate dead-tab keys forever across restarts.
    return {
        "version": STATE_VERSION,
        "windows": windows,
        "tab_names": {k: v for k, v in tab_names.items() if str(k) in live_tab_ids},
        "tab_colors": {k: v for k, v in tab_colors.items() if str(k) in live_tab_ids},
        "tab_collapsed": {k: v for k, v in tab_collapsed.items() if str(k) in live_tab_ids},
        "tab_sleeping": {k: v for k, v in tab_sleeping.items() if str(k) in live_tab_ids},
    }


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Atomically write `data` (stamped with saved_at) to `path` via tmp + replace."""
    data = {**data, "saved_at": int(time.time())}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f"{path.name}.tmp.", dir=path.parent, delete=False
        ) as file:
            tmp = Path(file.name)
            json.dump(data, file, indent=2)
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


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
        previous = load_restore()
        if previous and has_layout_loss(data, previous):
            backup_restore(previous)
        _write_json_atomic(RESTORE_PATH, data)
    except Exception:
        log.exception("failed to persist restore snapshot")


def load_restore() -> dict[str, Any] | None:
    """Read the restore snapshot (restore.json), or None if missing/unreadable."""
    return _read_json(RESTORE_PATH)


def backup_restore(data: dict[str, Any] | None) -> None:
    """Preserve a restore source before startup or layout/metadata loss."""
    if not data or not count_panes(data):
        return
    try:
        path = RESTORE_BACKUP_DIR / f"restore-{time.time_ns()}.json"
        _write_json_atomic(path, {**data, "source_saved_at": data.get("saved_at")})
        path.chmod(0o600)
        for old in sorted(RESTORE_BACKUP_DIR.glob("restore-*.json"))[:-RESTORE_BACKUP_LIMIT]:
            old.unlink()
    except OSError:
        log.exception("failed to back up restore snapshot")


def pane_ids(data: dict[str, Any] | None) -> set[str]:
    """Session identities distinguish a daemon reload from new iTerm2 panes."""
    return {
        str(pane["id"])
        for window in (data or {}).get("windows", [])
        for tab in window.get("tabs", [])
        for pane in tab.get("panes", [])
        if pane.get("id")
    }


def live_tab_metadata(data: dict[str, Any] | None, live_tabs: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    """Attach saved metadata to surviving sessions, not reused native tab numbers."""
    fields = {"tab_names": "name", "tab_colors": "color", "tab_collapsed": "collapsed", "tab_sleeping": "sleeping"}
    result: dict[str, dict[str, Any]] = {field: {} for field in fields}
    if not data:
        return result
    if not pane_ids(data):
        # Legacy files have no session identities; retain their old tab-id behavior.
        return {
            field: {str(k): v for k, v in (data.get(field) or {}).items() if str(k) in live_tabs} for field in fields
        }
    for window in data.get("windows", []):
        for tab in window.get("tabs", []):
            saved_ids = {str(p["id"]) for p in tab.get("panes", []) if p.get("id")}
            matches = [tab_id for tab_id, ids in live_tabs.items() if saved_ids & ids]
            if len(matches) != 1:
                continue
            for field, key in fields.items():
                value = tab.get(key)
                if value is not None:
                    result[field][matches[0]] = value
    return result


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


def has_layout_loss(live: dict[str, Any], saved: dict[str, Any]) -> bool:
    """Whether adopting live would lose panes or known names/directories."""

    def counts(data: dict[str, Any]) -> tuple[int, int, int]:
        tabs = [t for w in data.get("windows", []) for t in w.get("tabs", [])]
        panes = [p for t in tabs for p in t.get("panes", [])]
        return len(panes), sum(bool(p.get("cwd")) for p in panes), sum(bool(t.get("name")) for t in tabs)

    return any(current < previous for current, previous in zip(counts(live), counts(saved), strict=True))


def should_update_restore(
    live_panes: int,
    restore_panes: int,
    uptime_seconds: float,
    freeze_seconds: float = RESTORE_FREEZE_SECONDS,
    *,
    live_state: dict[str, Any] | None = None,
    restore_state: dict[str, Any] | None = None,
) -> bool:
    """Decide whether the current live layout may overwrite the restore snapshot.

    The restore snapshot must outlive the degraded layout iTerm2 relaunches with
    (usually a single window) long enough for the user to click Restore, so:

    - An empty layout never becomes the restore source.
    - During startup, unrelated session identities or fewer known names/cwds are
      protected even if the pane count is unchanged or larger.
    - Otherwise, growth is adopted immediately.
    - A shrink is adopted only once the daemon has been up past `freeze_seconds`,
      so the post-relaunch transient can't clobber the snapshot, while a genuinely
      reduced workspace is still tracked (for recency) after the grace period.
    """
    if live_panes == 0:
        return False
    if uptime_seconds < freeze_seconds and restore_state:
        saved_ids = pane_ids(restore_state)
        current_ids = pane_ids(live_state)
        if saved_ids and current_ids and not saved_ids & current_ids:
            return False

        # Legacy snapshots can still reveal lost metadata without session IDs.
        if live_state is not None and has_layout_loss(live_state, restore_state):
            return False
    if live_panes >= restore_panes:
        return True
    return uptime_seconds >= freeze_seconds
