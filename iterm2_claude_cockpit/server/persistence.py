"""Durable, on-disk persistence of the workspace layout for session restore.

The daemon holds everything in memory and dies when iTerm2 closes. This module
serializes a minimal layout snapshot (windows → tabs → panes, each pane's cwd and
Claude session id) plus the in-memory `tab_names`/`buried_positions` to a durable
per-user file, so a later "Restore workspace" action can recreate the layout and
resume each Claude pane via `claude --resume <session-id>`.

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
STATE_PATH = STATE_DIR / "state.json"

# Bump when the on-disk shape changes incompatibly; load_state tolerates older
# files by simply returning whatever parsed (restore is defensive about fields).
STATE_VERSION = 1


def build_state(
    snapshot: dict[str, Any],
    tab_names: dict[str, str],
    buried_positions: dict[str, str],
) -> dict[str, Any]:
    """Build the persistable layout dict from the live snapshot (no timestamp).

    Derives entirely from the already-built snapshot — never re-walks iTerm2.
    Buried panes are skipped in the windows tree (they aren't part of a restorable
    live layout); `buried_positions` is persisted separately and verbatim.

    The result is deterministic, so callers can diff it to debounce writes.
    """
    windows: list[dict] = []
    live_tab_ids: set[str] = set()
    live_pane_ids: set[str] = set()
    for window in snapshot.get("windows", []):
        tabs: list[dict] = []
        for tab in window.get("tabs", []):
            live_tab_ids.add(str(tab.get("id", "")))
            panes: list[dict] = []
            for pane in tab.get("panes", []):
                live_pane_ids.add(str(pane.get("id", "")))
                if pane.get("buried"):
                    continue
                panes.append(
                    {
                        "cwd": pane.get("cwd", ""),
                        "claude": bool(pane.get("ext.claude.active")),
                        "session_id": pane.get("ext.claude.session_id", ""),
                    }
                )
            # Persist the custom name only (not the auto-generated "Tab N"), keyed
            # by the live tab id which dies on restart — the name is what restore
            # re-applies to the freshly created tab.
            tabs.append({"name": tab_names.get(tab.get("id", "")), "panes": panes})
        windows.append({"tabs": tabs})

    # Prune metadata to ids still present in the live snapshot, so these dicts
    # can't accumulate dead-tab/session keys forever across restarts.
    return {
        "version": STATE_VERSION,
        "windows": windows,
        "tab_names": {k: v for k, v in tab_names.items() if str(k) in live_tab_ids},
        "buried_positions": {k: v for k, v in buried_positions.items() if str(k) in live_pane_ids},
    }


def save_state(data: dict[str, Any]) -> None:
    """Atomically write a pre-built state dict (from build_state) to disk."""
    try:
        data = {**data, "saved_at": int(time.time())}
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_name(f"{STATE_PATH.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except Exception:
        log.exception("failed to persist workspace state")


def load_state() -> dict[str, Any] | None:
    """Read the persisted state, or None if missing/unreadable."""
    try:
        if not STATE_PATH.is_file():
            return None
        loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        log.exception("failed to load workspace state")
        return None
