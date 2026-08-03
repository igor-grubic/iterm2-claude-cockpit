# Extension API v1

This document is the authoritative contract for the extension system. The v1 API is stable — breaking changes require a major version bump and migration path.

## Loading

An extension is a subpackage of `iterm2_claude_cockpit.extensions` with an `__init__.py` that exposes:

```python
def register(api: ExtensionAPI) -> None: ...
```

The loader calls `register(api)` at daemon startup. If `register` raises, the extension is skipped and the error is logged — it must not crash the daemon.

Extensions are opt-in. They are listed in `iterm2_claude_cockpit/extensions.json`:

```json
{
  "extensions": {
    "claude": { "enabled": true, "description": "Detects Claude-driven panes" }
  }
}
```

Enable/disable via CLI (requires daemon restart):

```bash
python -m iterm2_claude_cockpit ext enable <name>
python -m iterm2_claude_cockpit ext disable <name>
python -m iterm2_claude_cockpit ext list
```

## ExtensionAPI

`ExtensionAPI` is instantiated per extension and bound to its name. All registered hooks, routes, and assets are scoped to that name automatically.

### Properties

```python
api.name: str        # the extension name (e.g. "claude")
api.dir: Path        # absolute path to the extension's package directory
```

### `api.add_session_enricher(fn)`

Register a function called for every session node during tree build.

```python
def fn(
    session: iterm2.Session,
    node: dict,
    ps_output: str,
    screen_lines: list[str],
    signals: dict | None = None,   # optional; declare to receive signal data
) -> dict | None: ...
```

- `session` — the iTerm2 Session object (read-only recommended; writes are not tested)
- `node` — the partially-built session dict; may be mutated in place; includes `tty` as of v1.1
- `ps_output` — raw output of `ps -A -o pid=,ppid=,tty=,comm=` (4 columns, shared across all enrichers for this snapshot)
- `screen_lines` — up to 20 recent non-empty visible terminal lines, oldest-first
- `signals` — if the enricher declares this kwarg, the dispatcher passes `{source_name: {tty_basename: payload_dict}}` built from all registered signal-dir sources (see `api.add_signal_dir_source`). Enrichers that do not declare `signals` receive only the four positional args.
- Return a `dict` to merge into `node`, or `None`/mutate in place

The function may be `async`. Errors are caught and logged; a failing enricher does not abort the snapshot.

**Namespace rule:** Only write keys in the `ext.<name>.<field>` namespace. Never write top-level keys — collisions with future core fields or other extensions are silent and hard to debug.

### `api.add_static_dir(path)`

Serve a directory of static files at `/static/ext/<name>/...`.

```python
api.add_static_dir(api.dir / "static")
```

`path` must exist at registration time; raises `ValueError` otherwise.

### `api.add_webview_asset(kind, relpath)`

Inject a `<link>` or `<script>` tag into the panel HTML.

```python
api.add_webview_asset("css", "static/claude.css")
api.add_webview_asset("js", "static/claude.js")
```

- `kind`: `"css"` or `"js"` (raises `ValueError` otherwise)
- `relpath`: path relative to `/static/ext/<name>/`
- CSS is injected in `<head>`, JS before `</body>` and after `app.js`

### `api.add_route(method, path, handler)`

Register an HTTP route at `/api/ext/<name>/<path>`.

```python
async def my_handler(request_body: bytes) -> dict: ...
api.add_route("POST", "action", my_handler)
# → handles POST /api/ext/<name>/action
```

- `method`: `"GET"` or `"POST"` (raises `ValueError` otherwise)
- `path`: relative path (leading `/` is stripped)
- `handler`: sync or async callable; receives the raw request body as `bytes`; should return a JSON-serializable dict

### `api.add_action(name, handler)`

Convenience wrapper for `add_route("POST", name, handler)`.

### `api.add_signal_dir_source(name, directory, ttl_seconds=None)`

Register a directory of TTY-keyed JSON signal files written by in-pane hook scripts.

```python
api.add_signal_dir_source("claude", "/tmp/iterm-pane-tree/claude")
```

- `name` — identifies the source; used as the key in the `signals` dict passed to enrichers
- `directory` — path to the directory; created automatically if it does not exist
- `ttl_seconds` — defaults to `None`, meaning signals represent persistent state and live until the next hook overwrites them or the daemon restarts. Pass a positive value only if signals are events that should expire (e.g. a 5s heartbeat). Stale files left over across daemon restarts are cleaned at startup regardless.

**Signal file format:** `<tty-basename>.json` (e.g. `ttys003.json`), containing at minimum `{"tty": "/dev/ttys003", "state": "<value>", "ts": <epoch>}`. Written atomically by hook scripts via tmpfile + rename. Sources may include extra source-specific fields — e.g. the `claude` source also writes `claude_session_id` (surfaced as `ext.claude.session_id` and used by the workspace-restore feature), and keys files by the iTerm2 session GUID rather than the TTY basename when running in daemon mode.

**In the enricher:** declare `signals` as a kwarg and receive `signals["<name>"][<tty-basename>]` for the current session's TTY. The `node["tty"]` field (e.g. `/dev/ttys003`) gives the full path; `Path(tty).name` gives the basename to look up.

**Shipped hook script:** `iterm2_claude_cockpit/extensions/claude/hooks/notify.sh` is the reference implementation. See the "Claude Code integration" section in the README for the `~/.claude/settings.json` snippet.

## Webview extension points

The panel JS exposes `window.PaneTreeExt` — a registry for decorating the rendered tree. Extension JS runs after `app.js`, so the registry always exists.

```typescript
window.PaneTreeExt = {
  paneRowDecorators:  Array<(row: HTMLElement, node: SessionNode) => void>,
  paneTitleDecorators: Array<(label: HTMLElement, node: SessionNode) => void>,
  shouldShowJob: Array<(node: SessionNode) => boolean>,
}
```

| Hook | Called when | Use case |
|------|-------------|----------|
| `paneRowDecorators` | A pane row is rendered or updated | Add classes, badges, icons |
| `paneTitleDecorators` | The title label is rendered | Modify or replace the title text |
| `shouldShowJob` | Deciding whether to render the job badge | Return `false` to hide it |

`shouldShowJob` is evaluated as OR-suppression: if any registered function returns `false` for a node, the job badge is hidden.

## Example extension skeleton

```python
# iterm2_claude_cockpit/extensions/myext/__init__.py
from __future__ import annotations
from pathlib import Path


def register(api) -> None:
    api.add_session_enricher(_enrich)
    api.add_static_dir(api.dir / "static")
    api.add_webview_asset("js", "static/myext.js")
    api.add_action("ping", _ping)


def _enrich(session, node, ps_output, screen_lines):
    node[f"ext.myext.active"] = "myprocess" in ps_output


async def _ping(body: bytes) -> dict:
    return {"pong": True}
```

```javascript
// iterm2_claude_cockpit/extensions/myext/static/myext.js
window.PaneTreeExt.paneRowDecorators.push((row, node) => {
  if (node["ext.myext.active"]) row.classList.add("myext-active");
});
```

## Versioning

The current API version is **v1**. `ExtensionAPI.version` is `1`.

Any additive change (new method, new webview hook) is a minor change and does not break v1 extensions. Removing or changing the signature of an existing method is a breaking change and requires incrementing the version.
