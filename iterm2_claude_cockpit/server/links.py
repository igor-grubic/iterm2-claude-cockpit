"""Config-driven per-workspace link buttons (PR, Jira, …).

A *link provider* turns a pane's working directory into a button: it names a
status file to find by walking up from the cwd, a value to pull out of that
file, and a URL template to build from the value. The cockpit itself knows
nothing about GitHub or Jira — providers are plain data loaded from
`~/.config/iterm2-claude-cockpit/links.json`, with a sensible built-in default
when that file is absent.

A provider is a dict::

    {
      "id": "pr",                       # stable key, used by the webview
      "label": "PR",                    # chip text
      "color": "#8ab4f8",               # optional chip color (hex)
      "file": ".cockpit.json",          # found by walking up from a pane cwd
      "extract": {"json": "pr_url"},    # or {"regex": "- PR:\\s*(\\S+)"}
      "href": "{value}"                 # {value} is replaced with the extracted value
    }

The value-extraction and URL-building helpers are pure (no filesystem, no
`iterm2`), so they are unit-testable in CI. Only `resolve_tab_links` and
`load_providers` touch disk; file contents and the parsed config are cached by
mtime so repeated tree builds don't re-read unchanged files.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from . import persistence

log = logging.getLogger("iterm2_claude_cockpit.links")

# Lives alongside the other daemon config (state.json, settings.json, …).
LINKS_PATH = persistence.STATE_DIR / "links.json"

# How far up from a pane's cwd we look for a status file before giving up.
_MAX_WALK_DEPTH = 40

# Shipped default: two chips reading full URLs from a `.cockpit.json` at (or above)
# the pane's cwd. Full URLs (not ids) keep this default free of any org-specific
# domain — the `href` template and `regex` extractor are there for custom configs.
DEFAULT_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "pr",
        "label": "PR",
        "color": "#8ab4f8",
        "file": ".cockpit.json",
        "extract": {"json": "pr_url"},
        "href": "{value}",
    },
    {
        "id": "jira",
        "label": "JIRA",
        "color": "#d8a0e6",
        "file": ".cockpit.json",
        "extract": {"json": "jira_url"},
        "href": "{value}",
    },
]

# path str -> (mtime, text); invalidated on mtime change (e.g. /pr rewrites the file).
_FILE_CACHE: dict[str, tuple[float, str]] = {}
# (config mtime | None, providers); None mtime means the file was absent.
_PROVIDERS_CACHE: tuple[float | None, list[dict[str, Any]]] | None = None


def _valid_provider(provider: Any) -> bool:
    """A provider must at least name an id, a file to read, and how to extract."""
    return (
        isinstance(provider, dict)
        and bool(provider.get("id"))
        and bool(provider.get("file"))
        and isinstance(provider.get("extract"), dict)
    )


def load_providers(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Return the configured link providers, or the built-in defaults.

    Cached by the config file's mtime so an edit to `links.json` takes effect on
    the next tree build without a daemon restart, and an unchanged file is parsed
    only once. A missing or malformed file falls back to `DEFAULT_PROVIDERS`.
    """
    global _PROVIDERS_CACHE
    p = Path(path) if path is not None else LINKS_PATH
    try:
        mtime: float | None = p.stat().st_mtime
    except OSError:
        mtime = None  # file absent → use defaults

    if _PROVIDERS_CACHE is not None and _PROVIDERS_CACHE[0] == mtime:
        return _PROVIDERS_CACHE[1]

    providers = DEFAULT_PROVIDERS
    if mtime is not None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            raw = data.get("providers") if isinstance(data, dict) else None
            if isinstance(raw, list):
                providers = [pr for pr in raw if _valid_provider(pr)]
        except Exception:
            log.exception("failed to read links config %s; using defaults", p)
            providers = DEFAULT_PROVIDERS

    _PROVIDERS_CACHE = (mtime, providers)
    return providers


def find_status_file(start: str, filename: str, max_depth: int = _MAX_WALK_DEPTH) -> Path | None:
    """Walk up from `start` looking for `filename`; return the nearest match or None.

    Walking up means a pane that has `cd`-ed into a repo subfolder still finds the
    status file at its workspace root.
    """
    if not start or not filename:
        return None
    try:
        cur = Path(start).resolve()
    except Exception:
        return None
    for _ in range(max_depth):
        candidate = cur / filename
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            pass
        if cur.parent == cur:  # reached the filesystem root
            break
        cur = cur.parent
    return None


def _dig(data: Any, dotted: str) -> Any:
    """Follow a dotted key path (`a.b.c`) through nested dicts, or None if absent."""
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def extract_value(text: str, extract: dict[str, Any]) -> str | None:
    """Pull a single string value out of `text` per an `extract` spec.

    `{"json": "a.b"}` parses `text` as JSON and follows the dotted key path.
    `{"regex": "..."}` searches `text` and returns capture group 1. Returns None
    when the value is missing, empty, or the file/pattern can't be parsed.
    """
    if not isinstance(extract, dict):
        return None
    if "json" in extract:
        try:
            data = json.loads(text)
        except Exception:
            return None
        value = _dig(data, str(extract["json"]))
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return None
    if "regex" in extract:
        try:
            match = re.search(str(extract["regex"]), text)
        except re.error:
            log.warning("invalid link-provider regex: %r", extract["regex"])
            return None
        if match and match.groups():
            return (match.group(1) or "").strip() or None
    return None


def build_href(template: str, value: str) -> str:
    """Substitute `{value}` in a provider's href template with the extracted value."""
    return (template or "{value}").replace("{value}", value)


def link_from_text(provider: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Build one link dict from a provider and the text of its status file.

    Pure (no filesystem): returns `{"id","label","href","color"}` or None when the
    provider's value isn't present in `text`.
    """
    value = extract_value(text, provider.get("extract") or {})
    if not value:
        return None
    return {
        "id": str(provider.get("id", "")),
        "label": str(provider.get("label", provider.get("id", ""))),
        "href": build_href(str(provider.get("href", "{value}")), value),
        "color": provider.get("color"),
    }


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


def resolve_tab_links(cwds: list[str], providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve the link chips for a tab, given its panes' working directories.

    For each provider, the first cwd whose status file yields the provider's value
    wins — so a workspace shared by several panes produces one chip, not one per
    pane. Providers with no match are simply omitted.
    """
    out: list[dict[str, Any]] = []
    for provider in providers:
        filename = str(provider.get("file") or "")
        if not filename:
            continue
        for cwd in cwds:
            path = find_status_file(cwd, filename)
            if path is None:
                continue
            text = _read_cached(path)
            if text is None:
                continue
            link = link_from_text(provider, text)
            if link is not None:
                out.append(link)
                break
    return out
