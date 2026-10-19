"""Working-directory helpers that do not require the iTerm2 runtime."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger("iterm2_claude_cockpit.directories")

_LSOF_ESCAPES = re.compile(rb"\\(x[0-9a-fA-F]{2}|.?)|\^[@-_]")
_LSOF_SIMPLE_ESCAPES = {b"\\": b"\\", b"b": b"\b", b"f": b"\f", b"n": b"\n", b"r": b"\r", b"t": b"\t"}


def _decode_lsof_name(value: bytes) -> str:
    def unescape(match: re.Match[bytes]) -> bytes:
        escape = match[1]
        if escape is None:
            # lsof prints a literal "^A" and the control byte identically.
            raise ValueError("ambiguous lsof control-character escape")
        if escape.startswith(b"x") and len(escape) == 3:
            return bytes([int(escape[1:], 16)])
        try:
            return _LSOF_SIMPLE_ESCAPES[escape]
        except KeyError:
            raise ValueError("invalid lsof filename escape") from None

    decoded = _LSOF_ESCAPES.sub(unescape, value)
    if b"\0" in decoded:
        raise ValueError("NUL in lsof directory name")
    return os.fsdecode(decoded)


def parse_process_cwds(output: bytes) -> dict[int, str]:
    """Parse lsof's machine-readable PID/name records for cwd-only queries."""
    result: dict[int, str] = {}
    pid: int | None = None
    for line in output.split(b"\n"):
        if line.startswith(b"p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith(b"n/") and pid is not None:
            try:
                result[pid] = _decode_lsof_name(line[1:])
            except ValueError:
                log.warning("ignoring invalid lsof directory record for PID %s", pid, exc_info=True)
    return result


def process_cwds(pids: set[int]) -> dict[int, str]:
    """Read missing pane directories in one bounded, off-loop lsof call."""
    if not pids:
        return {}
    try:
        proc = subprocess.run(
            ["/usr/sbin/lsof", "-a", "-d", "cwd", "-p", ",".join(str(pid) for pid in sorted(pids)), "-Fpn"],
            capture_output=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.warning("could not read pane working directories with lsof", exc_info=True)
        return {}
    # lsof may return 1 when a queried process exits; other PID records remain usable.
    if proc.returncode not in (0, 1):
        log.warning("lsof failed reading pane directories: %s", proc.stderr.decode(errors="replace").strip())
    result: dict[int, str] = {}
    for pid, cwd in parse_process_cwds(proc.stdout).items():
        try:
            result[pid] = restore_cwd(cwd)
        except (OSError, ValueError):
            log.warning("ignoring unavailable lsof directory for PID %s", pid, exc_info=True)
    return result


def restore_cwd(value: object) -> str:
    """Validate a saved directory before using it in an iTerm2 launch profile."""
    if not isinstance(value, str) or not value:
        raise ValueError("no saved working directory")
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"saved working directory is unavailable: {value}")
    return value
