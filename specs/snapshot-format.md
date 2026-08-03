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
  "panes": [ <session>, ... ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `"tab"` | Node discriminator |
| `id` | string | iTerm2 tab ID (stringified) |
| `title` | string | Display label: custom name if one is set via `POST /api/rename-tab`, otherwise `"Tab N"` (1-indexed) |
| `active` | boolean | Whether this is the frontmost tab in its window |
| `panes` | array | Ordered list of session nodes |

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
  "tty": "/dev/ttys003"
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

## Invariants

- Every node has a `kind` field; the webview uses it as a discriminator
- `windows` is always present, may be empty (`[]`)
- `tabs` within a window is always present, may be empty
- `panes` within a tab always contains at least one session (the tab's visible sessions)
- `active` is mutually exclusive within a level: at most one window, one tab per window, and one session per tab is `active: true`
- `id` values are stable for the lifetime of the session; they are reused by iTerm2 only after a restart
