# iterm2-claude-cockpit

Live tree of every iTerm2 window, tab, and pane — purpose-built for orchestrating many Claude Code sessions side-by-side.

[![iTerm2 3.5+](https://img.shields.io/badge/iTerm2-3.5%2B-blue)](https://iterm2.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://iterm2.com/python-api/)

![iterm2-claude-cockpit panel showing the window/tab/pane tree alongside Claude Code](docs/demo.gif)

## Features

- Live hierarchical tree: window → tab → pane, updated in real time
- Click any node to focus it immediately
- Rename tabs inline (hover → ✎) or programmatically via `POST /api/rename-tab`; custom names persist until the tab or window is closed
- Per-pane status popup: current job, working directory, recent terminal output
- Click the folder pill to focus the pane; on the active pane, hover reveals "copy" and clicking copies its working directory to the clipboard
- Create new tabs and windows from the panel
- Bury and unbury sessions (hide a running pane without closing it)
- Session restore (⟲ button) — recreate your windows/tabs/panes after closing or updating iTerm2 and resume each Claude Code session via `claude --resume`
- YAML project layouts — define a named set of tabs and open them with one click
- Settings panel (⚙ button) — shows the plugin version and installed extensions at a glance
- Optional extensions — opt-in modules can enrich the snapshot, inject CSS/JS into the panel, and add HTTP routes (see [Extensions](#extensions))
- Zero external dependencies — stdlib only, beyond the `iterm2` library bundled with iTerm2
- Runs as an AutoLaunch daemon; starts automatically with iTerm2

## Requirements

- macOS with iTerm2 3.5 or later
- Python API enabled (see step 1 below)

No separate Python installation needed — iTerm2 bundles its own runtime.

## Installation

### 1. Enable the Python API

`iTerm2 → Settings → General → Magic → ☑ Enable Python API`

### 2. Clone

**Option A — recommended for most users.** Clone directly into the AutoLaunch directory:

```bash
git clone https://github.com/igor-grubic/iterm2-claude-cockpit.git \
  "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit"
```

**Option B — recommended for developers.** Clone anywhere and symlink:

```bash
git clone https://github.com/igor-grubic/iterm2-claude-cockpit.git ~/code/iterm2_claude_cockpit
ln -s "$HOME/code/iterm2_claude_cockpit" \
  "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit"
```

> **Why `iterm2_claude_cockpit`?** iTerm2's script loader requires the folder name, inner package name, and entry script name to all match — and they must be valid Python identifiers (underscores, not hyphens). The distribution name is `iterm2-claude-cockpit` (hyphens); the install folder is `iterm2_claude_cockpit` (underscores).

### 3. Run once from the Scripts menu

`Scripts → AutoLaunch → iterm2_claude_cockpit → iterm2_claude_cockpit.py`

iTerm2 will install dependencies and create an `iterm2env/` virtual environment inside the script folder. **This step is required — autolaunch will not work until it has been completed at least once.** It only needs to happen once per installation.

### 4. Allow the API permission prompt

Click **Allow** when iTerm2 asks for Python API access. The daemon starts immediately and will auto-launch on every subsequent iTerm2 start.

### 5. Set up Claude Code status tracking

The Claude extension is enabled by default. Run its interactive installer to wire up accurate `running` / `idle` / `attention` states via Claude Code hooks:

**Option A:**
```bash
python3 "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit/iterm2_claude_cockpit/extensions/claude/hooks/install.py"
```

**Option B:**
```bash
python3 ~/code/iterm2_claude_cockpit/iterm2_claude_cockpit/extensions/claude/hooks/install.py
```

The installer explains every change, shows a before/after diff, and asks for confirmation before touching anything. It backs up your existing `~/.claude/settings.json` first.

### 6. Show the panel

`View → Toolbelt → Show Toolbelt`, then right-click the toolbelt and tick **Claude Cockpit**.

iTerm2 remembers both settings, so this is a one-time step.

### Auto-open the panel in every new window (optional)

`Settings → Profiles → [your profile] → Window → ☑ Open toolbelt`

This is per-profile — repeat for any profile you use. Takes effect on the next new window.

### Updating

**Option A:**
```bash
cd "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit" && git pull
```

**Option B:**
```bash
cd ~/code/iterm2_claude_cockpit && git pull
```

Then re-run from `Scripts → AutoLaunch → iterm2_claude_cockpit → iterm2_claude_cockpit.py`, or restart iTerm2.

### Uninstalling

**Option A (cloned directly):**
```bash
rm -rf "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit"
```

**Option B (symlinked):**
```bash
rm "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit"
# optionally also remove the repo:
rm -rf ~/code/iterm2_claude_cockpit
```

### Migrating from `iterm_workflow`

If you installed before this rename, remove the old entry and re-clone:

```bash
rm -rf "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm_workflow"
# then follow the installation steps above with the new iterm2_claude_cockpit name
```

Also remove the old toolbelt entry from iTerm2's Toolbelt menu: right-click the toolbelt and untick **Worktree** (if present), then re-enable **Claude Cockpit** after re-running step 3.

## Session restore

Closing iTerm2 kills every pane and its processes, so in-progress work is normally lost — which makes quitting or updating iTerm2 risky. Session restore brings your workspace back.

As you work, the daemon saves a snapshot of your layout (windows → tabs → panes, each pane's working directory and — for Claude panes — its Claude Code session id) to `~/.config/iterm2-claude-cockpit/state.json`. The Claude session id is captured from the same hook used for status tracking, so [Claude Code integration](#claude-code-integration) must be set up for resume to work.

Click the **⟲ Restore** button in the footer (it confirms first) to recreate the saved windows, tabs, and panes. Each pane `cd`s back to its directory; Claude panes whose transcript still exists are resumed with `claude --resume <session-id>`. This works after both quitting/updating iTerm2 **and** a full machine reboot, because it reads from disk rather than keeping processes alive.

> **Requires the `claude` extension for resume.** Identifying Claude panes and capturing their session id is done by the bundled [`claude` extension](#extensions), which is enabled by default. With it disabled (`ext disable claude`), Restore still recreates your full window/tab/pane layout in the right working directories, but treats every pane as a plain shell — it won't resume any Claude session. Restoring the layout (without resume) does not require the extension.

What it does **not** restore: any turn that was mid-execution when iTerm2 closed (the resumed session picks up from the last completed turn), terminal scrollback, non-Claude process state (dev servers, builds), and exact split sizes (panes come back as a simple vertical split). Custom tab names and buried-pane positions are persisted in the same file and also survive restarts.

## Project layouts

Define a named set of tabs in `iterm2_claude_cockpit/projects/example.yaml` and open them from the panel. See [`iterm2_claude_cockpit/projects/example.yaml`](iterm2_claude_cockpit/projects/example.yaml) for the format.

## Extensions

The core panel is a generic pane manager. Anything Claude-specific (or other "goodies") lives in opt-in extensions under `iterm2_claude_cockpit/extensions/`.

```bash
# from the repo / install root:
python3 -m iterm2_claude_cockpit ext list
python3 -m iterm2_claude_cockpit ext enable claude
python3 -m iterm2_claude_cockpit ext disable claude
```

After enabling or disabling, restart iTerm2 — the toolbelt webview is loaded once on startup and does not hot-reload.

### Bundled extensions

- **claude** — detects Claude Code panes via a `ps -t <tty>` process check, tags them in the snapshot as `ext.claude.{active,state,action_needed}`, and decorates them in the panel (accent color, ❗ attention badge, blue plan-mode tint). Status is driven by Claude Code hook signal files; see [Claude Code integration](#claude-code-integration) for setup.

#### Claude Code integration

For accurate `running` / `idle` / `attention` states, run the interactive installer once:

```bash
python3 /path/to/iterm2_claude_cockpit/extensions/claude/hooks/install.py
```

The installer explains every change it will make, shows before/after for each hook entry, and asks for confirmation before touching anything. It writes a `.bak` of your existing `~/.claude/settings.json` before modifying it.

<details>
<summary>What the installer adds (manual alternative)</summary>

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "/path/to/iterm2_claude_cockpit/extensions/claude/hooks/notify.sh running"}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "/path/to/iterm2_claude_cockpit/extensions/claude/hooks/notify.sh idle"}]}
    ],
    "Notification": [
      {"hooks": [{"type": "command", "command": "/path/to/iterm2_claude_cockpit/extensions/claude/hooks/notify.sh attention"}]}
    ]
  }
}
```
</details>

To remove the hooks later:

```bash
python3 /path/to/iterm2_claude_cockpit/extensions/claude/hooks/uninstall.py
```

Without hooks, panes where `claude` is the foreground process still show as active (amber) but state will default to `running` throughout the session.

### Authoring an extension

Create `iterm2_claude_cockpit/extensions/<name>/__init__.py` exposing `register(api)`. The API (v1) lets you:

- `api.add_session_enricher(fn)` — add fields to each session node before serialization. `fn(session, node, ps_output, screen_lines, signals=None)` may be sync or async; return a dict to merge or mutate `node` in place. Use the `ext.<name>.<field>` namespace for new keys.
- `api.add_signal_dir_source(name, directory)` — register a directory of TTY-keyed JSON signal files written by in-pane hook scripts; the parsed payloads are passed to enrichers via the `signals` kwarg.
- `api.add_static_dir(path)` — serve a directory at `/static/ext/<name>/...`.
- `api.add_webview_asset("css"|"js", relpath)` — inject a `<link>` or `<script>` tag into the panel HTML.
- `api.add_route("GET"|"POST", path, handler)` — handle `/api/ext/<name>/<path>`. Async handlers run on the iTerm2 event loop.
- `api.add_action(name, handler)` — sugar for `add_route("POST", name, ...)`.

The webview exposes a small registry on `window.PaneTreeExt`:

- `paneRowDecorators: ((row, node) => void)[]`
- `paneTitleDecorators: ((label, node) => void)[]`
- `shouldShowJob: ((node) => boolean)[]` — return false to hide the job badge for a pane

Extension JS is loaded after `app.js`, so the registry exists when your script runs. See `iterm2_claude_cockpit/extensions/claude/` for a complete example.

## Troubleshooting

**Check the console first:** `Scripts → Manage → Console` — Python tracebacks appear here.

**Verify the daemon is running:** `curl -s http://127.0.0.1:9876/` should return the panel HTML.

---

### The panel is blank / shows "connecting…"

The daemon isn't running. Check the console for errors, then re-run the script from `Scripts → AutoLaunch → iterm2_claude_cockpit → iterm2_claude_cockpit.py`.

---

### AutoLaunch never started the script — no permission prompt appeared

The `iterm2env/` virtual environment inside the script folder hasn't been created yet. iTerm2 requires it before it will autolaunch.

Run the script once manually:

`Scripts → AutoLaunch → iterm2_claude_cockpit → iterm2_claude_cockpit.py`

iTerm2 installs dependencies and creates `iterm2env/` on this first run. After that, autolaunch works on every subsequent iTerm2 start.

To verify the environment was created, check that this directory exists:

```bash
ls "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit/iterm2env"
# or for Option B:
ls ~/code/iterm2_claude_cockpit/iterm2env
```

If it doesn't exist after running manually, check the console (`Scripts → Manage → Console`) for errors.

---

### The Scripts menu shows nothing / the folder doesn't appear

The folder name must match the `.py` filename exactly, and both must be valid Python identifiers (underscores, not hyphens). If you renamed the folder, rename it back to `iterm2_claude_cockpit`.

---

### `ModuleNotFoundError` in the console

The script is running as a Basic (shared) environment rather than Full Environment. This happens when the two-level folder structure or `setup.cfg` is missing. Make sure the layout is:

```
iterm2_claude_cockpit/          ← the cloned/symlinked folder
├── setup.cfg
└── iterm2_claude_cockpit/
    └── iterm2_claude_cockpit.py
```

---

### Toolbelt shows the panel but it disappears on new windows

This is a per-profile setting. Enable it in `Settings → Profiles → [your profile] → Window → ☑ Open toolbelt` for every profile you use.

---

### The `defaults write com.googlecode.iterm2 OpenToolbelt -bool true` command doesn't work

In iTerm2 3.6+ the global `OpenToolbelt` defaults key is overridden by per-profile settings. Use the profile setting above instead.

---

### Log paths show `~/.config/iterm2/AppSupport/Scripts/...` even though I used `~/Library/Application Support/...`

iTerm2 3.6+ moved its support directory to an XDG-style path. The legacy `~/Library/Application Support/iTerm2/` location symlinks transparently to the new one — both work, the different path in logs is expected.

---

### Restore opened the panes but Claude didn't resume

Restore recreates every pane's directory, but it only runs `claude --resume` when it can find the session. Common causes:

- **The `claude` extension is disabled.** Capturing Claude session ids is done by the bundled `claude` extension; with it disabled (`ext disable claude`) no pane is recognized as Claude, so Restore recreates the layout + directories only. Re-enable it (`ext enable claude`) and restart iTerm2.
- **No saved session id.** The Claude session id is captured by the status hook — if [Claude Code integration](#claude-code-integration) isn't installed, restore has nothing to resume and brings panes back as a plain shell. Set up the hook and re-run a turn so the session is recorded.
- **Transcript missing.** Sessions older than your Claude `cleanupPeriodDays` setting (default 30) are deleted, and so is the ability to resume them. Restore falls back to just `cd`-ing into the directory (counted as `skipped`).
- **`claude` not on `PATH` / not logged in.** The pane runs `claude --resume …` in your shell; if `claude` isn't found or you're logged out, you'll see the shell error or a login prompt in the right directory.
- **In-flight turn not continued.** A turn that was mid-execution when iTerm2 closed is gone; the resumed session starts from the last completed turn.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT]
