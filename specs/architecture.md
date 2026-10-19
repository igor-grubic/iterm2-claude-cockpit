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
    │    set-tab-collapsed|set-tab-sleeping|open-url|           │
    │    settings|restore →                                  │
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
Auto-discovered group link chips. For each tab, reads a *status file* (default `.cockpit.json`) from its panes' working directories and turns every URL-valued property into a `tab.links[]` entry — the property name is the label, its value(s) are what the chip opens (a list of URLs becomes a multi-link chip). Nothing about PRs or Jira is hardcoded. The status filename is configurable via `~/.config/iterm2-claude-cockpit/links.json` (`{"file": …}`); the config and status files are cached by mtime. The parsing helpers are pure and `iterm2`-free, so they're unit-tested in CI (see `tests/test_links.py`). Chips open via `POST /api/open-url` (`server/actions.py:open_url`, which shells out to macOS `open`, http/https only).

### `server/http.py`
Minimal HTTP server (no framework). Routes:
- `GET /` → panel HTML
- `GET /api/tree` → snapshot JSON
- `POST /api/*` → action dispatch
- `GET /static/*` → bundled webview assets

### `server/actions.py`
Handles user-initiated actions (focus window/tab/pane, create tab/window, split pane, close session, restore workspace). Returns `{"ok": true}` or `{"error": "..."}`. `restore_workspace` recreates persisted windows/tabs/panes, launching each shell in its saved directory via per-session profile customizations, and re-applies each tab's saved name/color/collapsed/sleeping state onto the freshly created tab id. Missing/unavailable directories and partial creation failures are returned in `errors` and shown by the panel. Structural refreshes are suppressed until restore finishes so partial layouts do not get saved mid-operation.

### `server/directories.py`
Stdlib-only directory recovery and validation. When iTerm2's `path` variable is missing, `tree.build_tree` collects the panes' foreground `jobPid` values and resolves their working directories in one bounded `lsof` query off the event loop. Output is read as bytes; hexadecimal, C-style, and backslash filename escapes are decoded once before filesystem decoding, preserving both raw UTF-8 and literal escape-looking names regardless of locale. Malformed or ambiguous caret-form records and unavailable directories are logged and ignored. If unavailable, the tree retains the previous directory only for the same session ID. The parser and saved-directory validation are independently unit-tested.

### `server/persistence.py`
Durable workspace state. Serializes the layout (windows → tabs → panes, each pane's cwd) plus `tab_names`/`tab_colors`/`tab_collapsed`/`tab_sleeping` to two files under `~/.config/iterm2-claude-cockpit/`:

- `state.json` — a rolling mirror of the *current* layout, rewritten on every `State.refresh()` (debounced, on change, ≥5s apart) via the executor.
- `restore.json` — the *last-good* layout `POST /api/restore` rebuilds. `should_update_restore` never adopts an empty layout. During `RESTORE_FREEZE_SECONDS`, it rejects shrinks, unrelated session identities, and losses of known names/directories, including equal-size or larger layouts. The policy is re-evaluated after the grace period even if the live layout has not changed.
- `restore-backups/` — before the first refresh, the startup restore source is copied to a timestamped private JSON file. `save_restore` also backs up the current saved layout before adopting fewer panes or known names/directories, including after the startup grace period. The ten most recent backups are retained. `source_saved_at` retains the original source timestamp while `saved_at` records the backup write time.

`State.flush_now()` first drains previously queued saves, then persists the latest state synchronously — no debounce or executor hand-off for the final write. `iterm2_claude_cockpit.py`'s entry point calls it from `SIGTERM`/`SIGINT` handlers (then exits via `os._exit`) and registers it with `atexit` as a fallback, so a change made moments before a daemon restart isn't lost or overwritten by an older pending save. Atomic writes use distinct private temporary files, including when callers write concurrently.

`State.__init__` loads `restore.json` (falling back to `state.json` for installs predating the split) into an in-memory `restore_snapshot` and serves it to Restore — so Restore always recreates the *previous* session, not the current one as it evolves. Live tab metadata instead comes from the latest rolling `state.json` when present, matching saved pane session IDs to surviving tabs rather than blindly reusing native tab numbers. Saved tabs and panes now carry their `id` alongside the existing fields; this is an additive, version-1-compatible change. Legacy files without pane IDs retain their old tab-id metadata lookup. Each tab's `name`/`color`/`collapsed`/`sleeping` remain embedded directly on its entry for explicit restores onto fresh tab IDs. Older files without sleep metadata load as awake.

Sleep is an independent boolean, not a special color index: `tab_colors` retains the original color, while the panel renders a grey override and hides panes. `POST /api/set-tab-sleeping` updates sleep and collapse together under the state lock; wake always expands. Color and collapse endpoints reject changes while sleeping. This is presentation only, with no iTerm2 process or native color changes.

A third, independent file, `settings.json`, holds plain UI preferences unrelated to the window/tab/pane layout — currently just the selected panel theme (`GET`/`POST /api/settings`). It has no freeze-window logic like `restore.json`; `save_settings`/`load_settings` are thin wrappers around the same atomic-write/read helpers.

### `webview/`
Static browser assets. `app.js` polls `/api/tree`, diffs the response, and updates the DOM. `index.html` also wires the iTerm2 and Claude Code cheatsheet buttons, which fetch static HTML fragments (`iterm_cheatsheet.html`, `claude_cheatsheet.html`).

Two selectable visual themes render the same tab/pane data: `"1a"` (default, "Classic", JetBrains Mono/terminal-styled) and `"2a"` ("Modern"). `app.js`'s `THEMES` table holds each theme's color palette and neutral/label/title colors; `currentTheme` (fetched once from `/api/settings` at startup, changed via the Settings panel) selects which of two structurally distinct render paths runs — `renderGroup2a`/`renderPaneRow2a` vs `renderGroup1a`/`renderPaneRow1a` — since the two designs differ in DOM shape (rounded tinted block vs flat accent bar, a colored dot vs a text glyph mark), not just color. Both share the same state/interaction layer (`cycleColor`, `toggleCollapse`, `toggleSleep`, `startGroupEdit`, `wireGroupDrag`, `postAction`) and the folder button beside each pane's close control (full cwd on hover, click to copy without focusing). Sleep/wake is optimistic, blocks duplicate requests, reconciles on a confirming poll, and rolls back with an error toast if the request fails. Chrome that doesn't structurally differ between themes (footer, buttons, bottom nav, popups) is re-skinned purely via CSS custom-property overrides scoped under `body[data-theme="1a"]`.

## Threading model

The daemon runs on the iTerm2 asyncio event loop. The HTTP server runs on a separate daemon thread (`ThreadingHTTPServer`); its handlers read the cached snapshot directly and schedule action coroutines back onto the iTerm2 loop via `asyncio.run_coroutine_threadsafe`. All iTerm2 API calls must be awaited on that loop. Disk writes for workspace persistence run in a dedicated single-worker executor, preserving save order without blocking the loop. Process/working-directory queries use the default executor.

## Port

Hard-coded to `127.0.0.1:9876`. Not configurable in v1.
