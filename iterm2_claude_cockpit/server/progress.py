"""Per-pane "working" state, mirrored from iTerm2's progress bar.

Agents such as Claude Code and Copilot CLI report that they're busy with the OSC 9;4
progress sequence, which iTerm2 draws as the glowing outline on a tab. iTerm2 doesn't
expose that state to the Python API, so the daemon adds two session-local "Progress
Bar Changed" event triggers to every pane. They mirror the bar into the user variable
`user.cockpitProgress` (`"true"` while shown, `"false"` once withdrawn), which
tree.py reads on each refresh.

Event triggers arrived in iTerm2 3.7. Older versions don't know the match type, so the
daemon only installs them on 3.7+.

Stdlib only, with no `iterm2` import, so the helpers are unit-testable in CI.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path
from typing import Any

VAR_NAME = "cockpitProgress"
USER_VAR = f"user.{VAR_NAME}"

MIN_ITERM2_VERSION = (3, 7)

# Encoded the way iTerm2 stores triggers (see iterm2.triggers): match type 112 is
# EVENT_PROGRESS_BAR_CHANGED, and a Set User Variable parameter is `<name>\x01<json value>`.
_PROGRESS_BAR_CHANGED = 112
_SET_USER_VARIABLE = "iTermSetUserVariableTrigger"


def _trigger(progress_bar_filter: str, value: str) -> dict[str, Any]:
    return {
        "regex": "",
        "action": _SET_USER_VARIABLE,
        "parameter": f"{VAR_NAME}\x01{value}",
        "partial": False,
        "disabled": False,
        "matchType": _PROGRESS_BAR_CHANGED,
        "eventParams": {"progressBarFilter": progress_bar_filter},
    }


TRIGGERS: tuple[dict[str, Any], ...] = (_trigger("appeared", "true"), _trigger("disappeared", "false"))

# A bare shell in the foreground means nothing is running. An agent killed mid-task never
# withdraws its progress bar, so this keeps a stale "true" off an idle prompt.
_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh", "nu", "login"})


def _identity(trigger: Any) -> tuple[Any, ...] | None:
    # Ignores `disabled`, so a cockpit trigger the user switched off counts as present
    # and is never re-enabled behind their back.
    if not isinstance(trigger, dict):
        return None
    params = trigger.get("eventParams") or {}
    return (trigger.get("matchType"), trigger.get("action"), trigger.get("parameter"), params.get("progressBarFilter"))


def merge_triggers(existing: list[Any] | None) -> list[Any] | None:
    """Return `existing` plus any missing cockpit triggers, or None when nothing is missing."""
    current = list(existing or [])
    present = {_identity(t) for t in current}
    missing = [dict(t) for t in TRIGGERS if _identity(t) not in present]
    return current + missing if missing else None


def is_busy(value: str | None, job: str) -> bool:
    """Whether a pane is working, given its `user.cockpitProgress` value and foreground job."""
    return value == "true" and job.lstrip("-") not in _SHELLS


def parse_version(version: str) -> tuple[int, ...]:
    """`"3.7.6"` → `(3, 7, 6)`. Ignores suffixes like `beta2`; returns `()` if unparsable."""
    match = re.match(r"\d+(?:\.\d+)*", version.strip())
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def supports_triggers(version: str) -> bool:
    """Whether this iTerm2 version understands the progress-bar event trigger."""
    return parse_version(version) >= MIN_ITERM2_VERSION


def app_version(bundle_path: str) -> str:
    """iTerm2's version from its bundle's Info.plist, or `""` if it can't be read."""
    try:
        with (Path(bundle_path) / "Contents" / "Info.plist").open("rb") as f:
            return str(plistlib.load(f).get("CFBundleShortVersionString", ""))
    except (OSError, plistlib.InvalidFileException, ValueError):
        return ""
