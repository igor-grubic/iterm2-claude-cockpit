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
{ "theme": "1a" }
```

`theme` — the selected panel visual theme: `"1a"` (default, "Classic") or `"2a"` ("Modern").

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

### `GET /api/session-lines`

Return the last non-empty visible lines of a session, most recent last. Used by the panel's per-pane status popup.

**Query parameters:**

- `id` — the session id to read.

**Response:** `200 application/json`

```json
{ "ok": true, "lines": ["...", "..."] }
```

Up to 10 lines are returned. Returns `{ "ok": false, "error": "..." }` (status `500` on an unexpected failure) when the session is missing or its contents can't be read.

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

### `POST /api/close-session`

Close a session (pane).

**Request body:** `application/json`

```json
{ "id": "<session id>" }
```

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/new-tab`

Create a new tab. Added to `window_id` when supplied, otherwise the current terminal window.

**Request body:** `application/json`

```json
{ "window_id": "<window id>" }
```

`window_id` is optional.

**Response:** `200 application/json` — `{ "ok": true, "tab_id": "<tab id>" }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/new-window`

Create a new window. Takes no request body.

**Response:** `200 application/json` — `{ "ok": true, "window_id": "<window id>" }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/split-pane`

Split a session into two panes.

**Request body:** `application/json`

```json
{ "id": "<session id>", "vertical": true }
```

`vertical` is optional and defaults to `true` (side-by-side split); `false` stacks the new pane below.

**Response:** `200 application/json` — `{ "ok": true, "session_id": "<new session id>" }` or `{ "ok": false, "error": "..." }`

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

### `POST /api/open-url`

Open a URL in the user's default browser (macOS `open`). Used by the group-header link chips (`tab.links[]`), since the panel runs in a WKWebView where `window.open` on an external URL is unreliable.

**Request body:** `application/json`

```json
{ "url": "https://github.com/org/repo/pull/12" }
```

Only `http`/`https` URLs are accepted; anything else returns `{ "ok": false, "error": "only http(s) urls are allowed" }` without launching anything.

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

#### Where the chips come from (status file)

The chips in `tab.links[]` are **auto-discovered** from a status file (default `.cockpit.json`) read from a group's panes' working directories. Every **URL-valued property** of that JSON object becomes a chip, labeled with the property name:

- a property whose value is an `http(s)` URL string → a chip that opens that URL;
- a property whose value is a list of URLs → a chip that opens a popup listing them all (the panel opens it directly when the list has just one);
- any other property (e.g. a plain `branch` string) is ignored.

```json
{
  "jira_url": "https://jira.example.com/browse/PROJ-1",
  "pr_urls": ["https://github.com/org/a/pull/1", "https://github.com/org/b/pull/2"],
  "branch": "PROJ-1-do-the-thing"
}
```

→ a `jira_url` chip (opens the ticket) and a `pr_urls` chip (popup of both PRs); `branch` is ignored.

The daemon is agnostic to what the properties mean — it just turns URL-valued keys into chips, in file order, with a color derived deterministically from each key.

The only configurable knob is **which file** to read, via `~/.config/iterm2-claude-cockpit/links.json`:

```json
{ "file": ".cockpit.json" }
```

An absent or malformed config falls back to `.cockpit.json`. The config (and the status files) are cached by mtime, so edits take effect on the next tree build without a daemon restart.

---

### `POST /api/set-tab-color`

Set or clear a tab's group color. Persists across restarts the same way `tab_names` does.

**Request body:** `application/json`

```json
{ "id": "<tab id>", "color": 0-5 | null }
```

Set `color` to `null` to clear it (uncolored).

Sleeping tabs reject color changes with `{ "ok": false, "error": "tab is sleeping; wake it first" }`, keeping their original color intact.

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/set-tab-collapsed`

Set a tab group's collapsed state in the panel. Persists across restarts the same way `tab_names` does.

**Request body:** `application/json`

```json
{ "id": "<tab id>", "collapsed": true | false }
```

Sleeping tabs reject collapse/expand changes with `{ "ok": false, "error": "tab is sleeping; wake it first" }`. Only the sleep toggle wakes a sleeping tab.

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

---

### `POST /api/set-tab-sleeping`

Set a tab group's visual-only sleep state. Persists across daemon restarts and workspace restore.

**Request body:** `application/json`

```json
{ "id": "<tab id>", "sleeping": true | false }
```

`id` must be a non-empty string identifying a tab in the current snapshot, and `sleeping` must be a JSON boolean. Missing/invalid values or an unknown tab return `{ "ok": false, "error": "..." }` without changing state.

Sleeping atomically sets `sleeping: true` and `collapsed: true`. Waking clears both, always expanding the panes, even if the tab was collapsed before sleeping. The original `color` is never modified: both panel themes render sleeping tabs in grey and restore their original color (including uncolored) on wake. Color filters continue matching the original assigned color.

The panel's name/caret (and pane count in Modern) and color swatch are disabled while asleep; only the **moon** (sleep) button wakes the tab. Rename, link chips, and drag-to-reorder remain available. No terminal processes are paused or stopped, and iTerm2's native tab color is not changed.

**Response:** `200 application/json` — `{ "ok": true }` or `{ "ok": false, "error": "..." }`

---

### `GET /api/restore-preview`

Summarize what `POST /api/restore` would recreate, read from the in-memory saved snapshot. Used by the panel to show counts in the Restore confirmation before the user commits.

**Response:** `200 application/json`

```json
{ "ok": true, "windows": 3, "tabs": 8, "panes": 14, "missing_cwds": 2 }
```

`windows`/`tabs`/`panes` — totals that would be recreated. Returns `{ "ok": false, "error": "no saved workspace" }` when nothing has been saved yet.

`missing_cwds` — panes with no saved working directory. The confirmation warns that these will use the profile's default directory. This count does not check whether non-empty paths still exist; restore reports unavailable directories in its `errors` array.

---

### `POST /api/restore`

Recreate the last saved workspace (see "Session restore" in the README). Reads the persisted layout from `~/.config/iterm2-claude-cockpit/restore.json` — the last-good snapshot, kept separate from the rolling `state.json` mirror — and creates fresh windows/tabs/panes. Each shell starts in its saved directory through per-session profile customizations, without sending commands into a shell that may not be ready. Always creates new windows; it never modifies existing ones. Takes no request body.

**Response:** `200 application/json`

```json
{ "ok": true, "restored": 5 }
```

`restored` — panes recreated. If windows/tabs/panes fail to recreate, restore continues where possible and includes an `errors` array (e.g. `"errors": ["window 2: ..."]`) alongside `"ok": true` with the count that succeeded. Missing or unavailable saved directories also produce a location-specific warning in `errors`; those panes open in the profile's default directory. The UI displays these warnings rather than an unqualified success. Returns `{ "ok": false, "error": "no saved workspace" }` when nothing has been saved yet.

The restored workspace is the layout as it was during the *previous* daemon session (loaded into memory from `restore.json` at startup), not the freshly relaunched layout. For `RESTORE_FREEZE_SECONDS` after launch, `restore.json` rejects shrinks, unrelated session identities, and losses of known names/directories. After that grace period a non-empty layout may be adopted even without a further layout change. The startup source, and the latest saved layout before any subsequent pane/metadata loss, are backed up in `restore-backups/`, with the ten most recent backups retained. Refresh notifications are suppressed during restore, followed by one complete refresh with the restored metadata applied.

---

## Self-update

The install is a `git clone` plus a symlink (see `install.sh`), so the checkout *is* the
running app. Updating is therefore a fast-forward of that checkout followed by an in-place
re-exec of the daemon. Implemented in `server/updater.py`; the tracked ref is
`origin/main`.

### `GET /api/update/check`

Runs `git fetch origin main` in the checkout, then reports how far behind it is. This is
the only network call the daemon ever makes, and it only happens when the user clicks
"Check for updates" in Settings — there is no background polling.

`git fetch` downloads the new commits *and their contents* into `.git` without touching
the working tree, so by the time this returns, everything `POST /api/update/apply` needs is
already on disk.

**Response:** `200 application/json`

```json
{
  "ok": true,
  "update_available": true,
  "behind": 3,
  "current": "5deafb2",
  "latest": "79161b2",
  "latest_date": "2026-09-19T11:22:34+02:00",
  "commits": [
    { "sha": "79161b2", "subject": "feat: make Classic the default panel theme (#14)" }
  ],
  "installer_changed": false
}
```

- `behind` — commits on `origin/main` not in `HEAD`. `0` means up to date, and
  `update_available` is `false`.
- `commits` — newest first, capped at 20.
- `installer_changed` — true when the update touches `install.sh` / `uninstall.sh`. A
  fast-forward does not re-run the installer, so the panel tells the user to run it.
- `blocked` — present when an update exists but this daemon can't apply it. One of
  `dirty_tree`, `wrong_branch`, `detached_head`, `no_upstream`. The update is still
  reported; only the apply button is withheld.

**Never returns a non-200 for a failed check.** "Couldn't check" is an ordinary state
(offline, zip download instead of a clone), so failures come back as:

```json
{ "ok": false, "reason": "not_a_git_checkout", "detail": "..." }
```

`reason` is one of `not_a_git_checkout`, `git_missing`, `timeout`, `git_failed`,
`internal_error`.

---

### `POST /api/update/apply`

Fast-forwards the checkout to `sha`, then re-execs the daemon.

**Request body:** `application/json`

```json
{ "sha": "79161b2" }
```

`sha` is the `latest` value from a preceding check, **not** a re-resolved ref: the user
installs exactly the commits whose subjects they were shown, even if someone pushed again
while the modal sat open.

**Response:** `200 application/json`

```json
{ "ok": true, "previous": "5deafb2", "current": "79161b2", "restarting": true }
```

The response is sent *before* the restart; the daemon then re-execs ~0.5s later. The panel
polls `GET /api/about` until it answers and reloads itself.

Preconditions are re-verified here rather than trusted from the check (the tree may have
gone dirty in between), and `sha` must be a descendant of `HEAD`. On any failure nothing is
changed and the response is `{ "ok": false, "reason": "..." }` with one of the `blocked`
reasons above, plus `bad_sha`, `not_a_fast_forward`, or `already_updating`.

Because `git merge --ff-only` refuses rather than merging, a failed apply never leaves a
partial state or a conflict to resolve.

---

## Static assets

### `GET /static/<path>`

Serves files from `iterm2_claude_cockpit/webview/`. Used by the panel for `app.js`, `styles.css`, `iterm_cheatsheet.html`, and `claude_cheatsheet.html`.

### `GET /static/fonts/<name>`

Serves the bundled webfonts (`webview/fonts/`) referenced by `styles.css`'s `@font-face` rules: `space-grotesk-variable.woff2`, `ibm-plex-mono-400.woff2`, `ibm-plex-mono-500.woff2`. An explicit filename whitelist, not a generic directory listing.

---

## Cross-origin requests

All `POST` endpoints reject requests carrying an `Origin` header that isn't the panel's own
(`http://127.0.0.1:9876` / `http://localhost:9876`), with `403` and
`{ "ok": false, "error": "cross-origin request rejected" }`.

Binding to loopback never stopped another page in the user's browser from POSTing here, and
these endpoints act on the request. A missing `Origin` is allowed: that means a non-browser
client (curl, a local script), which already has the run of the machine.

## Error handling

All endpoints return `200` with `{ "error": "<message>" }` on handled errors. Unhandled exceptions produce a `500` with a plain-text body (logged to the iTerm2 console). The webview shows a toast notification on `{ "error": ... }` responses.

## Versioning

The API is unversioned in v1. There is no `/v1/` prefix. Breaking changes will be coordinated with the webview JS, which is bundled and not a public API.
