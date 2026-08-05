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
4. The HTTP server starts on port 9876.
5. iTerm2 update hooks are registered (window/tab/session change events).
6. The panel HTML is loaded in the toolbelt webview; the JS opens a polling connection to `/api/tree`.

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
    │  build_tree(app, tab_names)                             │
    │  → walks App → Window → Tab → Session                   │
    │  → returns JSON-serializable dict                       │
    ▼                                                         │
server/http.py                                                │
    │  GET /api/tree|settings → returns snapshot/settings JSON │
    │  POST /api/focus|new-tab|new-window|split-pane|          │
    │    close-session|move-tab|rename-tab|set-tab-color|      │
    │    set-tab-collapsed|open-url|settings|restore →         │
    │    delegates to server/actions.py                        │
    ▼                                                         │
webview/index.html + app.js                                   │
    │  polls /api/tree every ~500ms                           │
    │  renders tree, handles click events                     │
    └─────────────────────────────────────────────────────────┘
```

## Module responsibilities

### `iterm2_claude_cockpit.py`
Entry point. Connects to iTerm2, starts the HTTP server, registers `async_monitor` hooks for window/tab/session changes.

### `server/tree.py`
Builds the snapshot. Reads iTerm2 state (windows, tabs, sessions), calls `_session_status` to get job and the last visible line, resolves each group's link chips via `server/links.py`, and returns a pure dict with no iTerm2 objects.

### `server/links.py`
Config-driven group link chips (PR, Jira, …). For each tab, walks up from its panes' working directories to find a *status file* (default `.cockpit.json`), extracts a value (JSON key path or regex), and builds a URL — producing the `tab.links[]` entries. Providers are loaded from `~/.config/iterm2-claude-cockpit/links.json` (or a built-in default), and both the config and the status files are cached by mtime. The value-extraction/URL-building helpers are pure and `iterm2`-free, so they're unit-tested in CI (see `tests/test_links.py`). Chips open via `POST /api/open-url` (`server/actions.py:open_url`, which shells out to macOS `open`, http/https only).

### `server/http.py`
Minimal HTTP server (no framework). Routes:
- `GET /` → panel HTML
- `GET /api/tree` → snapshot JSON
- `POST /api/*` → action dispatch
- `GET /static/*` → bundled webview assets

### `server/actions.py`
Handles user-initiated actions (focus window/tab/pane, create tab/window, split pane, close session, restore workspace). Returns `{"ok": true}` or `{"error": "..."}`. `restore_workspace` recreates persisted windows/tabs/panes, each as a plain shell `cd`'d into its saved directory, and re-applies each tab's saved name/color/collapsed state onto the freshly created tab id.

### `server/persistence.py`
Durable workspace state. Serializes the layout (windows → tabs → panes, each pane's cwd) plus `tab_names`/`tab_colors`/`tab_collapsed` to two files under `~/.config/iterm2-claude-cockpit/`:

- `state.json` — a rolling mirror of the *current* layout, rewritten on every `State.refresh()` (debounced, on change, ≥5s apart) via the executor.
- `restore.json` — the *last-good* layout `POST /api/restore` rebuilds. The rolling save never writes it; `should_update_restore` only adopts the live layout when it is non-empty and either grows the saved one or the daemon has been up past `RESTORE_FREEZE_SECONDS`. This keeps the single-window layout iTerm2 relaunches with from overwriting the workspace before the user restores it.

`State.flush_now()` persists synchronously — no debounce, no executor hand-off — and is the only path guaranteed to complete before the process exits. `iterm2_claude_cockpit.py`'s entry point calls it from `SIGTERM`/`SIGINT` handlers (then exits via `os._exit`) and registers it with `atexit` as a fallback, so a change made moments before a daemon restart (still inside the 5s debounce window, or queued on the executor) isn't silently lost.

`State.__init__` loads `restore.json` (falling back to `state.json` for installs predating the split) into an in-memory `restore_snapshot`, seeds `tab_names`/`tab_colors`/`tab_collapsed` from it, and serves it to Restore — so Restore always recreates the *previous* session, not the current one as it evolves. Each tab's `name`/`color`/`collapsed` are also embedded directly on its entry in the persisted `windows` list (not just the top-level dicts), since `actions.restore_workspace` re-applies them to the *freshly created* tab id — the old id is gone once iTerm2 itself has restarted.

A third, independent file, `settings.json`, holds plain UI preferences unrelated to the window/tab/pane layout — currently just the selected panel theme (`GET`/`POST /api/settings`). It has no freeze-window logic like `restore.json`; `save_settings`/`load_settings` are thin wrappers around the same atomic-write/read helpers.

### `webview/`
Static browser assets. `app.js` polls `/api/tree`, diffs the response, and updates the DOM. `index.html` also wires the iTerm2 and Claude Code cheatsheet buttons, which fetch static HTML fragments (`iterm_cheatsheet.html`, `claude_cheatsheet.html`).

Two selectable visual themes render the same tab/pane data: `"2a"` (default, "Modern") and `"1a"` ("Classic", JetBrains Mono/terminal-styled). `app.js`'s `THEMES` table holds each theme's color palette and neutral/label/title colors; `currentTheme` (fetched once from `/api/settings` at startup, changed via the Settings panel) selects which of two structurally distinct render paths runs — `renderGroup2a`/`renderPaneRow2a` vs `renderGroup1a`/`renderPaneRow1a` — since the two designs differ in DOM shape (rounded tinted block vs flat accent bar, a colored dot vs a text glyph mark), not just color. Both share the same state/interaction layer (`cycleColor`, `toggleCollapse`, `startGroupEdit`, `wireGroupDrag`, `postAction`). Chrome that doesn't structurally differ between themes (footer, buttons, bottom nav, popups) is re-skinned purely via CSS custom-property overrides scoped under `body[data-theme="1a"]`.

## Threading model

The daemon runs on the iTerm2 asyncio event loop. The HTTP server runs on a separate daemon thread (`ThreadingHTTPServer`); its handlers read the cached snapshot directly and schedule action coroutines back onto the iTerm2 loop via `asyncio.run_coroutine_threadsafe`. All iTerm2 API calls must be awaited on that loop. Disk writes for workspace persistence run in the default thread-pool executor so they never block the loop.

## Port

Hard-coded to `127.0.0.1:9876`. Not configurable in v1.
