"""Detect which panes are running Claude Code, via a single `ps` call.

Claude Code (the npm/Node install) runs as `node`, so iTerm2's foreground
`jobName` is `node`, not `claude` — a job-name check alone can't spot it. But
the parent `claude` process stays attached to the pane's TTY, so we can find
Claude panes by asking `ps` which TTYs host a `claude`/`claude-code` process and
matching that against each session's `tty`.

One `ps` call covers every pane. Stdlib only, and no `iterm2` import, so the
pure parser is unit-testable in CI (which has no iTerm2 runtime).
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("iterm2_claude_cockpit.claude_detect")

_CLAUDE_COMMS = frozenset({"claude", "claude-code"})


def tty_base(tty: str) -> str:
    """Normalize a tty path to its bare name (`/dev/ttys012` → `ttys012`)."""
    return tty.rsplit("/", 1)[-1] if tty else ""


def parse_claude_ttys(ps_output: str) -> set[str]:
    """Return the tty names with a claude process, from `ps -Ao tty=,comm=` output.

    Each line is `<tty> <comm>`; `tty` is `??` for processes with no controlling
    terminal (skipped). Matches on the basename of `comm`, so path-based installs
    (e.g. `~/.claude/local/claude`) are caught alongside a bare `claude` in PATH.
    """
    ttys: set[str] = set()
    for line in ps_output.splitlines():
        tty, _, comm = line.strip().partition(" ")
        comm = comm.strip()
        if not comm or not tty or tty == "??":
            continue
        if comm.rsplit("/", 1)[-1] in _CLAUDE_COMMS:
            ttys.add(tty)
    return ttys


def claude_ttys() -> set[str]:
    """Set of tty names (e.g. `ttys012`) hosting a claude process.

    One `ps` call; returns an empty set on any failure (never raises).
    """
    try:
        proc = subprocess.run(
            ["ps", "-Ao", "tty=,comm="],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:
        log.debug("ps invocation for claude detection failed", exc_info=True)
        return set()
    return parse_claude_ttys(proc.stdout)
