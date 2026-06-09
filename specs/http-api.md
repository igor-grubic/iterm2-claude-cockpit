# HTTP API

The daemon listens on `127.0.0.1:9876`. All endpoints are local-only.

## Core endpoints

### `GET /`

Returns the panel HTML (`webview/index.html` with extension assets injected).

**Response:** `200 text/html`

---

### `GET /api/about`

Returns the plugin version and the list of extensions (both enabled and available).

**Response:** `200 application/json`

```json
{
  "version": "0.1.0",
  "extensions": {
    "enabled": ["claude"],
    "available": ["claude"]
  }
}
```

`enabled` — extensions currently active (from `extensions.json`).  
`available` — all extensions found on disk (superset of `enabled`).

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

### `POST /api/bury`

Bury (hide without closing) or unbury a session.

**Request body:** `application/json`

```json
{ "id": "<session id>", "bury": true | false }
```

**Response:** `200 application/json` — `{ "ok": true }` or `{ "error": "..." }`

---

### `POST /api/project`

Open a named project layout from `iterm2_claude_cockpit/projects/<name>.yaml`.

**Request body:** `application/json`

```json
{ "name": "<project name>" }
```

**Response:** `200 application/json` — `{ "ok": true }` or `{ "error": "..." }`

---

### `POST /api/restore`

Recreate the last saved workspace (see "Session restore" in the README). Reads the persisted layout from `~/.config/iterm2-claude-cockpit/state.json` and creates fresh windows/tabs/panes, sending `cd <cwd>` into each pane and `claude --resume <session-id>` into Claude panes whose transcript still exists and that aren't already open. Always creates new windows; it never modifies existing ones. Takes no request body.

**Response:** `200 application/json`

```json
{ "ok": true, "restored": 5, "resumed": 3, "skipped": 1 }
```

`restored` — panes recreated. `resumed` — Claude sessions resumed via `claude --resume`. `skipped` — Claude panes recreated as a plain shell because the session was already open or its transcript was gone. If one or more windows fail to recreate, restore continues with the rest and includes an `errors` array (e.g. `"errors": ["window 2: ..."]`) alongside `"ok": true` with the counts that succeeded. Returns `{ "ok": false, "error": "no saved workspace" }` when nothing has been saved yet.

The restored workspace is the layout as it was when the daemon last shut down (loaded into memory at startup), not the freshly relaunched layout.

---

## Static assets

### `GET /static/<path>`

Serves files from `iterm2_claude_cockpit/webview/`. Used by the panel for `app.js`, `styles.css`, etc.

### `GET /static/ext/<name>/<path>`

Serves files from a registered extension static directory. Only available if the extension called `api.add_static_dir(...)`.

---

## Extension routes

### `GET|POST /api/ext/<name>/<path>`

Handled by the extension that registered the route via `api.add_route(...)` or `api.add_action(...)`.

**Request body:** raw bytes passed to the handler.

**Response:** The handler's return value serialized as JSON, `200 application/json`.

---

## Error handling

All endpoints return `200` with `{ "error": "<message>" }` on handled errors. Unhandled exceptions produce a `500` with a plain-text body (logged to the iTerm2 console). The webview shows a toast notification on `{ "error": ... }` responses.

## Versioning

The API is unversioned in v1. There is no `/v1/` prefix. Breaking changes will be coordinated with the webview JS, which is bundled and not a public API.
