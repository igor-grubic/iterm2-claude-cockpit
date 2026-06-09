"""Extension API and shared registry.

An extension is a subpackage of `iterm2_claude_cockpit.extensions` exposing a
`register(api)` callable. The loader instantiates an `ExtensionAPI` bound to
the extension's name and passes it in. The API methods append to the shared
`Registry`, which the core reads at request time.

Stable in v1: every public method on `ExtensionAPI`. Anything else is an
implementation detail and may change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from extensions._signals import SignalSource

SessionEnricher = Callable[..., Any]
RouteHandler = Callable[..., Any]


@dataclass
class Registry:
    session_enrichers: list[SessionEnricher] = field(default_factory=list)
    static_dirs: dict[str, Path] = field(default_factory=dict)
    webview_assets: list[tuple[str, str, str]] = field(default_factory=list)
    routes: dict[tuple[str, str], RouteHandler] = field(default_factory=dict)
    signal_sources: list[SignalSource] = field(default_factory=list)


class ExtensionAPI:
    version = 1

    def __init__(self, name: str, ext_dir: Path, registry: Registry) -> None:
        self._name = name
        self._dir = ext_dir.resolve()
        self._registry = registry

    @property
    def name(self) -> str:
        return self._name

    @property
    def dir(self) -> Path:
        return self._dir

    def add_session_enricher(self, fn: SessionEnricher) -> None:
        self._registry.session_enrichers.append(fn)

    def add_static_dir(self, path: Path | str) -> None:
        resolved = Path(path).resolve()
        if not resolved.is_dir():
            raise ValueError(f"static dir does not exist: {resolved}")
        self._registry.static_dirs[self._name] = resolved

    def add_webview_asset(self, kind: str, relpath: str) -> None:
        if kind not in ("js", "css"):
            raise ValueError(f"unknown asset kind: {kind!r} (expected 'js' or 'css')")
        self._registry.webview_assets.append((self._name, kind, relpath.lstrip("/")))

    def add_route(self, method: str, path: str, handler: RouteHandler) -> None:
        m = method.upper()
        if m not in ("GET", "POST"):
            raise ValueError(f"unsupported method: {method!r}")
        full = f"/api/ext/{self._name}/{path.lstrip('/')}"
        self._registry.routes[(m, full)] = handler

    def add_action(self, name: str, handler: RouteHandler) -> None:
        self.add_route("POST", name, handler)

    def add_signal_dir_source(
        self,
        name: str,
        directory: str | Path,
        ttl_seconds: float | None = None,
    ) -> None:
        """Register a directory of TTY-keyed JSON signal files.

        Hook scripts write <tty-basename>.json into directory; the daemon reads
        them once per tick and passes the parsed payloads to enrichers that
        declare a `signals` kwarg.

        ttl_seconds defaults to None — signals represent persistent state and
        live until the next hook overwrites them or the daemon restarts. Pass
        a value only if signals are events that should expire (e.g. a 5s
        heartbeat). Stale files left over across daemon restarts are cleaned
        at startup regardless.
        """
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        self._registry.signal_sources.append(SignalSource(name=name, directory=d, ttl_seconds=ttl_seconds))
