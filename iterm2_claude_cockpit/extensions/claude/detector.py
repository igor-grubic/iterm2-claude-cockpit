"""Detect Claude-driven panes via TTY process check and hook signal files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_CLAUDE_JOBS = {"claude", "claude-code"}

# Narrow set of markers used only to promote state → "plan" when the plan-mode
# banner is visible. Screen-scraping is intentionally limited to this one check.
_PLAN_MARKERS = (
    "plan mode on",
    "Claude has written up a plan",
    "shift+tab to approve",
)


def _claude_attached_to_tty(tty_path: str, ps_output: str) -> bool:
    """True if a claude/claude-code process is attached to this pane's TTY.

    ps_output must be from `ps -A -o pid=,ppid=,tty=,comm=` (4 columns).
    The TTY column in ps output uses the basename (e.g. ttys003), not /dev/.
    """
    if not tty_path or not ps_output:
        return False
    tty_base = Path(tty_path).name  # "/dev/ttys003" → "ttys003"
    for line in ps_output.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        _, _, tty_col, comm = parts
        if tty_col.strip() == tty_base and comm.strip() in _CLAUDE_JOBS:
            return True
    return False


def _plan_banner_visible(screen_lines: list[str]) -> bool:
    """True if the plan-mode banner is visible in recent screen content."""
    return any(any(m in line for m in _PLAN_MARKERS) for line in screen_lines)


async def detect(
    session: Any,
    node: dict,
    ps_output: str,
    screen_lines: list[str],
    signals: dict | None = None,
) -> dict:
    """Session enricher: classify Claude state from hook signals or TTY process check.

    Primary path (Claude Code daemon mode): looks for a signal file keyed by
    the iTerm2 session GUID (node["id"]), written by notify.sh using the
    ITERM_SESSION_ID env var that the daemon inherits from the launching shell.

    Fallback (inline / older Claude Code): checks ps output for a claude/claude-code
    process attached to the pane's TTY, then reads any TTY-keyed signal file.
    """
    tty = node.get("tty") or ""
    session_id = node.get("id", "")
    all_signals: dict = (signals or {}).get("claude", {})

    # Primary: session-GUID signal (works when Claude runs as a background daemon).
    sig = all_signals.get(session_id, {})
    if sig:
        # The Stop hook writes state="idle" when Claude finishes a turn, but the
        # REPL is still alive at its prompt and may have a foreground child (e.g.
        # `docker-compose`). Don't demote such a pane to "not Claude" on every
        # turn boundary while a claude process is still attached to its TTY —
        # that unmasks the child's job badge and strips the Claude styling. Only
        # a genuinely-exited pane (no claude on the TTY) becomes inactive.
        active = sig.get("state") != "idle" or _claude_attached_to_tty(tty, ps_output)
    else:
        # Fallback: ps-based TTY check (inline mode where Claude attaches to TTY).
        active = _claude_attached_to_tty(tty, ps_output)
        if active:
            tty_base = Path(tty).name if tty else ""
            sig = all_signals.get(tty_base, {})

    if not active:
        return {
            "ext.claude.active": False,
            "ext.claude.state": "idle",
            "ext.claude.action_needed": False,
            "ext.claude.session_id": "",
        }

    state = sig.get("state", "running")  # default: active but no signal yet → running

    # Narrow screen-scrape fallback for plan-mode (hooks don't cover it).
    if state != "plan" and _plan_banner_visible(screen_lines):
        state = "plan"

    return {
        "ext.claude.active": True,
        "ext.claude.state": state,
        "ext.claude.action_needed": state == "attention",
        # Claude's own session UUID (from notify.sh), used to resume this pane via
        # `claude --resume <id>` after a restart. Empty until the first hook fires.
        "ext.claude.session_id": sig.get("claude_session_id", ""),
    }
