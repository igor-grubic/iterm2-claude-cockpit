# Snapshot Format

The `/api/tree` endpoint returns a JSON object describing the full iTerm2 session hierarchy.

## Top-level object

```json
{
  "windows": [ <window>, ... ]
}
```

## Window node

```json
{
  "kind": "window",
  "id": "<string>",
  "title": "Window 1 · 3 tabs",
  "active": true,
  "tabs": [ <tab>, ... ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `"window"` | Node discriminator |
| `id` | string | iTerm2 window ID |
| `title` | string | Display label, format: `"Window N · M tab(s)"` |
| `active` | boolean | Whether this is the current focused window |
| `tabs` | array | Ordered list of tab nodes |

## Tab node

```json
{
  "kind": "tab",
  "id": "<string>",
  "title": "Tab 2",
  "active": false,
  "panes": [ <session>, ... ],
  "color": 2,
  "collapsed": false,
  "links": [ { "id": "pr", "label": "PR", "href": "https://github.com/org/repo/pull/12", "color": "#8ab4f8" } ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `"tab"` | Node discriminator |
| `id` | string | iTerm2 tab ID (stringified) |
| `title` | string | Display label: custom name if one is set via `POST /api/rename-tab`, otherwise `"Tab N"` (1-indexed) |
| `active` | boolean | Whether this is the frontmost tab in its window |
| `panes` | array | Ordered list of session nodes |
| `color` | int \| null | Group color index (0-5) set via `POST /api/set-tab-color`, or `null` if uncolored |
| `collapsed` | boolean | Whether the group's panes are collapsed in the panel, set via `POST /api/set-tab-collapsed` |
| `links` | array | Config-driven link chips for this group (see below). Always present, may be empty (`[]`) |

### Link node (`tab.links[]`)

A clickable chip the panel renders on the group header — e.g. a PR or Jira link. Chips are produced by **link providers** (a config file, or a built-in default) that resolve a value out of a *status file* read from a pane's working directory. See `specs/http-api.md` → `POST /api/open-url` and the README for the provider format and the default `.cockpit.json` status file.

```json
{ "id": "pr", "label": "PR", "href": "https://github.com/org/repo/pull/12", "color": "#8ab4f8" }
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Provider id (stable key, e.g. `"pr"`, `"jira"`) |
| `label` | string | Chip text |
| `href` | string | URL the chip opens (via `POST /api/open-url`) |
| `color` | string \| null | Chip color (hex), or `null` to use the panel default |

The links are resolved per group from the working directories of its panes: for each provider, the first pane cwd whose status file yields the provider's value wins, so a workspace shared by several panes produces one chip set rather than one per pane. A group with no matching status file has `links: []`.

## Session node (pane)

```json
{
  "kind": "session",
  "id": "<string>",
  "title": "myrepo",
  "session_name": "igor@mac: ~/code/myrepo",
  "active": false,
  "job": "nvim",
  "last_line": "-- INSERT --",
  "cwd": "/Users/igor/code/myrepo",
  "tty": "/dev/ttys003",
  "claude": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `"session"` | Node discriminator |
| `id` | string | iTerm2 session ID |
| `title` | string | Short label derived from `cwd` basename (max 10 chars + `…`) |
| `session_name` | string | Full iTerm2 session name (autoName variable) |
| `active` | boolean | Whether this is the currently focused pane |
| `job` | string | Foreground process name (`jobName` variable), empty if unknown |
| `last_line` | string | Last non-empty visible terminal line (max 120 chars), empty if unavailable |
| `cwd` | string | Current working directory, empty if unknown |
| `tty` | string | Controlling TTY path (e.g. `/dev/ttys003`), empty if unknown |
| `claude` | boolean | Whether a `claude`/`claude-code` process is attached to this pane's TTY (detected via `ps`). `true` even when the foreground `job` is `node`, since the npm/Node install runs Claude under Node. `false` if undetectable |

## Invariants

- Every node has a `kind` field; the webview uses it as a discriminator
- `windows` is always present, may be empty (`[]`)
- `tabs` within a window is always present, may be empty
- `panes` within a tab always contains at least one session (the tab's visible sessions)
- `links` within a tab is always present, may be empty (`[]`)
- `active` is mutually exclusive within a level: at most one window, one tab per window, and one session per tab is `active: true`
- `id` values are stable for the lifetime of the session; they are reused by iTerm2 only after a restart
