# HTTP API

The daemon listens on `127.0.0.1:9876`. All endpoints are local-only.

## Core endpoints

### `GET /`

Returns the panel HTML (`webview/index.html`).

**Response:** `200 text/html`

---

### `GET /api/about`

Returns the plugin version.

**Response:** `200 application/json`

```json
{
  "version": "0.1.0"
}
```

---

### `GET /api/settings`

Returns the current UI settings.

**Response:** `200 application/json`

```json
{ "theme": "2a" }
```

`theme` — the selected panel visual theme: `"2a"` (default, "Modern") or `"1a"` ("Classic").

---

### `POST /api/settings`

Set a UI setting.

**Request body:** `application/json`

```json
{ "theme": "1a" }
```

Invalid or missing `theme` values fall back to the default (`"2a"`), rather than erroring.

**Response:** `200 application/json` — `{ "ok": true, "theme": "1a" }`

---

### `GET /api/tree`

Returns the current session tree snapshot.

**Response:** `200 application/json`

```json
{
  "windows": [ <window>, ... ]
}
```

See `specs/snapshot-format.md` for the full schema.

---

### `POST /api/focus`

Focus a window, tab, or pane.

**Request body:** `application/json`

```json
{ "id": "<session|tab|window id>", "kind": "session" | "tab" | "window" }
```

**Response:** `200 application/json`

```json
{ "ok": true }
```

On error:

```json
{ "error": "<message>" }
```

---

### `POST /api/close`

Close a session (pane).

**Request body:** `application/json`

```json
{ "id": "<session id>" }
```

**Response:** `200 application/json` — `{ "ok": true }` or `{ "error": "..." }`

---

### `POST /api/create`

Create a new tab or window.

**Request body:** `application/json`

```json
{ "kind": "tab" | "window" }
```

**Response:** `200 application/json` — `{ "ok": true }` or `{ "error": "..." }`

---

### `POST /api/move-tab`

Move a tab to a new position within its window.

**Request body:** `application/json`

```json
{ "tab_id": "<tab id>", "window_id": "<window id>", "position": <int> }
```

`position` is the 0-based target index after the move, clamped to `[0, tab_count - 1]`. Cross-window moves are not supported.

The daemon briefly suppresses layout-change notification callbacks during the call to prevent concurrent reads from deadlocking iTerm2's RPC connection. A single explicit refresh is issued after the operation completes.

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/rename-tab`

Set a custom display name for a tab. The name persists in the daemon's memory until the tab or window is closed.

**Request body:** `application/json`

```json
{ "id": "<tab id>", "name": "<new name>" }
```

`id` is optional — when omitted, the currently active tab is renamed. Set `name` to an empty string to clear a custom name and revert to the auto-generated `"Tab N"` label.

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/set-tab-color`

Set or clear a tab's group color. Persists across restarts the same way `tab_names` does.

**Request body:** `application/json`

```json
{ "id": "<tab id>", "color": 0-5 | null }
```

Set `color` to `null` to clear it (uncolored).

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/set-tab-collapsed`

Set a tab group's collapsed state in the panel. Persists across restarts the same way `tab_names` does.

**Request body:** `application/json`

```json
{ "id": "<tab id>", "collapsed": true | false }
```

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/project`

Open a named project layout from `iterm2_claude_cockpit/projects/<name>.yaml`.

**Request body:** `application/json`

```json
{ "name": "<project name>" }
```

**Response:** `200 application/json` — `{ "ok": true }` or `{ "error": "..." }`

---

### `GET /api/restore-preview`

Summarize what `POST /api/restore` would recreate, read from the in-memory saved snapshot. Used by the panel to show counts in the Restore confirmation before the user commits.

**Response:** `200 application/json`

```json
{ "ok": true, "windows": 3, "tabs": 8, "panes": 14 }
```

`windows`/`tabs`/`panes` — totals that would be recreated. Returns `{ "ok": false, "error": "no saved workspace" }` when nothing has been saved yet.

---

### `POST /api/restore`

Recreate the last saved workspace (see "Session restore" in the README). Reads the persisted layout from `~/.config/iterm2-claude-cockpit/restore.json` — the last-good snapshot, kept separate from the rolling `state.json` mirror so a relaunch can't overwrite it — and creates fresh windows/tabs/panes, sending `cd <cwd>` into each pane so it comes back as a plain shell in its saved directory. Always creates new windows; it never modifies existing ones. Takes no request body.

**Response:** `200 application/json`

```json
{ "ok": true, "restored": 5 }
```

`restored` — panes recreated. If one or more windows fail to recreate, restore continues with the rest and includes an `errors` array (e.g. `"errors": ["window 2: ..."]`) alongside `"ok": true` with the count that succeeded. Returns `{ "ok": false, "error": "no saved workspace" }` when nothing has been saved yet.

The restored workspace is the layout as it was during the *previous* daemon session (loaded into memory from `restore.json` at startup), not the freshly relaunched layout. `restore.json` is shrink-frozen for `RESTORE_FREEZE_SECONDS` after launch, so the single-window layout iTerm2 comes back with cannot wipe the snapshot before Restore is used.

---

## Static assets

### `GET /static/<path>`

Serves files from `iterm2_claude_cockpit/webview/`. Used by the panel for `app.js`, `styles.css`, `iterm_cheatsheet.html`, and `claude_cheatsheet.html`.

### `GET /static/fonts/<name>`

Serves the bundled webfonts (`webview/fonts/`) referenced by `styles.css`'s `@font-face` rules: `space-grotesk-variable.woff2`, `ibm-plex-mono-400.woff2`, `ibm-plex-mono-500.woff2`. An explicit filename whitelist, not a generic directory listing.

---

## Error handling

All endpoints return `200` with `{ "error": "<message>" }` on handled errors. Unhandled exceptions produce a `500` with a plain-text body (logged to the iTerm2 console). The webview shows a toast notification on `{ "error": ... }` responses.

## Versioning

The API is unversioned in v1. There is no `/v1/` prefix. Breaking changes will be coordinated with the webview JS, which is bundled and not a public API.
