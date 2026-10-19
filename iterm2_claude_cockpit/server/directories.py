"""Working-directory helpers that do not require the iTerm2 runtime."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger("iterm2_claude_cockpit.directories")


def parse_process_cwds(output: str) -> dict[int, str]:
    """Parse lsof's machine-readable PID/name records for cwd-only queries."""
    result: dict[int, str] = {}
    pid: int | None = None
    for line in output.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n/") and pid is not None:
            result[pid] = line[1:]
    return result


def process_cwds(pids: set[int]) -> dict[int, str]:
    """Read missing pane directories in one bounded, off-loop lsof call."""
    if not pids:
        return {}
    try:
        proc = subprocess.run(
            ["/usr/sbin/lsof", "-a", "-d", "cwd", "-p", ",".join(str(pid) for pid in sorted(pids)), "-Fpn"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.warning("could not read pane working directories with lsof", exc_info=True)
        return {}
    # lsof may return 1 when a queried process exits; other PID records remain usable.
    if proc.returncode not in (0, 1):
        log.warning("lsof failed reading pane directories: %s", proc.stderr.strip())
    return parse_process_cwds(proc.stdout)


def restore_cwd(value: object) -> str:
    """Validate a saved directory before using it in an iTerm2 launch profile."""
    if not isinstance(value, str) or not value:
        raise ValueError("no saved working directory")
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"saved working directory is unavailable: {value}")
    return value
