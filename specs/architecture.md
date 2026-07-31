# Architecture

## Overview

`iterm2-claude-cockpit` is an iTerm2 AutoLaunch daemon. It's installed as a **Basic script** — a single `.py` file symlinked into `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/` — and runs inside iTerm2's bundled Python (which ships with the `iterm2` library). A lightweight HTTP server on `127.0.0.1:9876` serves a browser-based side panel displayed in the iTerm2 toolbelt.

## Install layout

```
~/Library/Application Support/iTerm2/Scripts/AutoLaunch/
└── iterm2_claude_cockpit.py  ← symlink → <repo>/iterm2_claude_cockpit/iterm2_claude_cockpit.py
```

`install.sh` (at the repo root) places the symlink; `uninstall.sh` removes it. The repo can live anywhere.

## Startup sequence

1. iTerm2 boots, iterates direct children of `Scripts/AutoLaunch/`, and runs each `.py` it finds with its bundled Python.
2. The entry script does `sys.path.insert(0, str(Path(__file__).resolve().parent))` — `.resolve()` follows the symlink, so the inner package directory ends up on `sys.path`.
3. The daemon connects to iTerm2 via the Python API.
4. Enabled extensions are loaded from `extensions.json` via `extensions/_loader.py`.
5. The HTTP server starts on port 9876.
6. iTerm2 update hooks are registered (window/tab/session change events).
7. The panel HTML is loaded in the toolbelt webview; the JS opens a polling connection to `/api/tree`.

## Data flow

```
iTerm2 runtime
    │  update events
    ▼
iterm2_claude_cockpit.py  ──────────────────────────────────────────┐
    │                                                         │
    │  on change: invalidate tree cache                       │
    ▼                                                         │
server/tree.py                                                │
    │  build_tree(app, registry, tab_names)                   │
    │  → walks App → Window → Tab → Session                   │
    │  → calls session enrichers from Registry                │
    │  → returns JSON-serializable dict                       │
    ▼                                                         │
server/http.py                                                │
    │  GET /api/tree → returns snapshot JSON                  │
    │  POST /api/focus|close|create → delegates to            │
    │    server/actions.py                                     │
    │  GET /api/ext/<name>/<path> → delegates to              │
    │    Registry.routes                                      │
    ▼                                                         │
webview/index.html + app.js                                   │
    │  polls /api/tree every ~500ms                           │
    │  renders tree, handles click events                     │
    └─────────────────────────────────────────────────────────┘
```

## Module responsibilities

### `iterm2_claude_cockpit.py`
Entry point. Connects to iTerm2, bootstraps extensions, starts the HTTP server, registers `async_monitor` hooks for window/tab/session changes.

### `server/tree.py`
Builds the snapshot. Reads iTerm2 state (windows, tabs, sessions), calls `_session_status` to get job/cwd/screen content, runs registered session enrichers, and returns a pure dict with no iTerm2 objects.

### `server/http.py`
Minimal HTTP server (no framework). Routes:
- `GET /` → panel HTML
- `GET /api/tree` → snapshot JSON
- `POST /api/*` → action dispatch
- `GET /static/*` → bundled webview assets
- `GET /static/ext/<name>/*` → extension static assets

### `server/actions.py`
Handles user-initiated actions (focus window/tab/pane, create tab/window, close session, restore workspace). Returns `{"ok": true}` or `{"error": "..."}`. `restore_workspace` recreates persisted windows/tabs/panes and resumes Claude panes via `claude --resume`.

### `server/persistence.py`
Durable workspace state. Serializes the layout (windows → tabs → panes, each pane's cwd + Claude session id) plus `tab_names` to two files under `~/.config/iterm2-claude-cockpit/`:

- `state.json` — a rolling mirror of the *current* layout, rewritten on every `State.refresh()` (debounced, on change, ≥5s apart) via the executor.
- `restore.json` — the *last-good* layout `POST /api/restore` rebuilds. The rolling save never writes it; `should_update_restore` only adopts the live layout when it is non-empty and either grows the saved one or the daemon has been up past `RESTORE_FREEZE_SECONDS`. This keeps the single-window layout iTerm2 relaunches with from overwriting the workspace before the user restores it.

`State.__init__` loads `restore.json` (falling back to `state.json` for installs predating the split) into an in-memory `restore_snapshot`, seeds `tab_names` from it, and serves it to Restore — so Restore always recreates the *previous* session, not the current one as it evolves.

### `extensions/_api.py`
Defines `ExtensionAPI` (the v1 contract exposed to extensions) and `Registry` (the shared mutable container the core reads at request time). See `specs/extension-api.md`.

### `extensions/_loader.py`
Reads `extensions.json`, imports enabled extension packages, calls `register(api)` on each. Failures are logged and skipped — a bad extension must not crash the daemon.

### `webview/`
Static browser assets. `app.js` polls `/api/tree`, diffs the response, and updates the DOM. Exposes `window.PaneTreeExt` for extension JS hooks.

## Threading model

The daemon runs on the iTerm2 asyncio event loop. The HTTP server runs on the same loop (using `asyncio.start_server`). All iTerm2 API calls must be awaited on this loop. `ps` is run in a thread pool executor to avoid blocking the loop.

## Port

Hard-coded to `127.0.0.1:9876`. Not configurable in v1.
