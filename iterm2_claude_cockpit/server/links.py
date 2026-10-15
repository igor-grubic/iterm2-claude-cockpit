"""Per-workspace link chips, auto-discovered from a status file.

The cockpit reads a small status file (default `.cockpit.json`) from a group's
panes' working directories and turns each **URL-valued property** into a chip:
the property name is the label, and its value(s) are what the chip opens. The
cockpit hardcodes nothing about GitHub or Jira — whatever URL properties the file
carries become chips, in file order.

- A property whose value is a URL string → a chip that opens that URL.
- A property whose value is a list of URLs → a chip that opens a popup of them all
  (the webview opens directly when there's just one).
- Non-URL properties (e.g. a plain `branch` string) are ignored.

Example `.cockpit.json`::

    {
      "jira_url": "https://jira.example.com/browse/PROJ-1",
      "pr_urls": ["https://github.com/org/a/pull/1", "https://github.com/org/b/pull/2"],
      "branch": "PROJ-1-do-the-thing"          # ignored: not a URL
    }

→ a `jira_url` chip (opens the ticket) and a `pr_urls` chip (popup of both PRs).

The only configurable knob is *which file* to read, via
`~/.config/iterm2-claude-cockpit/links.json` → `{"file": "..."}`. The
text-parsing helpers are pure (no filesystem, no `iterm2`), so they're
unit-testable in CI; file contents and the config are cached by mtime.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from . import persistence

log = logging.getLogger("iterm2_claude_cockpit.links")

# Lives alongside the other daemon config (state.json, settings.json, …).
LINKS_PATH = persistence.STATE_DIR / "links.json"

# The status file read from each pane's working directory, unless overridden.
DEFAULT_STATUS_FILE = ".cockpit.json"

# Stable per-property chip colors: a property name always maps to the same hue, so
# e.g. a `pr_url` chip looks the same across every group. Chosen to read well on the
# dark panel; the webview applies them as the chip's text/border/fill.
_CHIP_PALETTE = ["#8ab4f8", "#d8a0e6", "#7dd3a8", "#e8c26a", "#e2685f", "#b58a63"]

# path str -> (mtime, text); invalidated on mtime change (e.g. /pr rewrites the file).
_FILE_CACHE: dict[str, tuple[float, str]] = {}
# (config mtime | None, filename); None mtime means the config file was absent.
_CONFIG_CACHE: tuple[float | None, str] | None = None


def status_filename(path: str | Path | None = None) -> str:
    """Return the status-file name to read, from config or the built-in default.

    Reads `{"file": "..."}` from `links.json` when present; otherwise
    `DEFAULT_STATUS_FILE`. Cached by the config file's mtime so an edit takes effect
    on the next tree build without a restart, and an unchanged file is parsed once.
    """
    global _CONFIG_CACHE
    p = Path(path) if path is not None else LINKS_PATH
    try:
        mtime: float | None = p.stat().st_mtime
    except OSError:
        mtime = None  # config absent → default

    if _CONFIG_CACHE is not None and _CONFIG_CACHE[0] == mtime:
        return _CONFIG_CACHE[1]

    filename = DEFAULT_STATUS_FILE
    if mtime is not None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("file"), str) and data["file"].strip():
                filename = data["file"].strip()
        except Exception:
            log.exception("failed to read links config %s; using default status file", p)
            filename = DEFAULT_STATUS_FILE

    _CONFIG_CACHE = (mtime, filename)
    return filename


def find_status_file(start: str, filename: str) -> Path | None:
    """Return `<start>/<filename>` if it exists, else None.

    The lookup is scoped to the pane's own working directory — it does not walk up
    into ancestor directories — so a status file only affects panes actually sitting
    in its folder, never unrelated panes elsewhere on the tree.
    """
    if not start or not filename:
        return None
    try:
        candidate = Path(start).resolve() / filename
    except Exception:
        return None
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def _is_url(value: Any) -> bool:
    """True when `value` is an http(s) URL string (what a chip can open)."""
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def _urls_of(value: Any) -> list[str]:
    """The URL(s) carried by a property value: a URL string → one; a list → its URLs."""
    if _is_url(value):
        return [value.strip()]
    if isinstance(value, list):
        return [v.strip() for v in value if _is_url(v)]
    return []


def _color_for(name: str) -> str:
    """Deterministic chip color for a property name (stable across runs/groups)."""
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return _CHIP_PALETTE[int(digest, 16) % len(_CHIP_PALETTE)]


def links_from_text(text: str) -> list[dict[str, Any]]:
    """Turn a status file's text into link chips, one per URL-valued property.

    Pure (no filesystem). Each chip is `{"id","label","urls","color"}`, in the
    file's property order. `urls` always has at least one entry. Non-URL properties
    are skipped; malformed or non-object JSON yields no chips.
    """
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for key, value in data.items():
        urls = _urls_of(value)
        if not urls:
            continue
        name = str(key)
        out.append({"id": name, "label": name, "urls": urls, "color": _color_for(name)})
    return out


def _read_cached(path: Path) -> str | None:
    """Read a status file's text, cached by mtime; None if it can't be read."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _FILE_CACHE.pop(key, None)
        return None
    cached = _FILE_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    _FILE_CACHE[key] = (mtime, text)
    return text


def resolve_tab_links(cwds: list[str], filename: str) -> list[dict[str, Any]]:
    """Resolve a tab's link chips from its panes' working directories.

    The first cwd that has the status file (and yields at least one chip) wins — so a
    workspace shared by several panes produces one chip set, not one per pane. Returns
    an empty list when no pane's folder holds a usable status file.
    """
    for cwd in cwds:
        path = find_status_file(cwd, filename)
        if path is None:
            continue
        text = _read_cached(path)
        if text is None:
            continue
        links = links_from_text(text)
        if links:
            return links
    return []
