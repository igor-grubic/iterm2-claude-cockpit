# iterm2-claude-cockpit

Live tree of every iTerm2 window, tab, and pane — purpose-built for orchestrating many Claude Code sessions side-by-side.

[![iTerm2 3.5+](https://img.shields.io/badge/iTerm2-3.5%2B-blue)](https://iterm2.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://iterm2.com/python-api/)

![iterm2-claude-cockpit panel showing the window/tab/pane tree alongside Claude Code](docs/demo.gif)

## Features

- Live hierarchical tree: window → tab → pane, updated in real time
- Two selectable panel themes (Settings panel → Theme): **Modern**, the default, and **Classic**, a JetBrains Mono terminal-styled alternative. The choice persists across restarts
- Per-tab group color coding: click a tab's swatch to cycle through 6 colors, which tints the tab's block and every pane in it; click a color chip in the header to filter the panel down to that color. Colors and collapsed state persist across restarts
- Claude panes stand out at a glance with a yellow pane title — detected via the pane's TTY, so it works for the Node-based install too
- Auto-discovered link chips on group headers — every URL in a workspace status file becomes a labeled chip that opens in your browser (arrays open a popup of all their links); see [Group link chips](#group-link-chips) below
- Click any pane to focus it immediately; its close (×) button is always visible; on the active pane, click its path to copy the working directory to the clipboard. Hover a pane for a tooltip with its job, working directory, and last output line
- Rename tabs inline (✎ button next to the name) or programmatically via `POST /api/rename-tab`; custom names persist until the tab or window is closed
- Create new tabs and windows from the panel
- Session restore (⟲ button) — recreate your windows, tabs, and panes in their saved working directories after closing or updating iTerm2
- Claude Code cheatsheet (✦ button) — a built-in quick reference of slash commands and keyboard shortcuts
- Settings panel (⚙ button) — switch the panel theme and see the plugin version at a glance
- Zero external dependencies — stdlib only, beyond the `iterm2` library bundled with iTerm2
- Runs as an AutoLaunch daemon; starts automatically with iTerm2

## Requirements

- macOS with iTerm2 3.5 or later
- iTerm2's Python API enabled (see step 2 below)

No separate Python installation needed — iTerm2 bundles its own runtime.

## Installation

### 1. Clone and install

```bash
git clone https://github.com/igor-grubic/iterm2-claude-cockpit.git ~/code/iterm2_claude_cockpit
cd ~/code/iterm2_claude_cockpit
bash install.sh
```

`install.sh` validates iTerm2 + its bundled Python, removes any stale AutoLaunch entry from a previous install (with consent), and creates a single file symlink at:

```
~/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit.py
```

iTerm2 runs this as a Basic script using its own bundled Python (which already has the `iterm2` library). No virtual environment to create, no "Full Environment" setup.

You can clone the repo anywhere — the symlink takes care of the AutoLaunch wiring.

### 2. Enable the Python API

`iTerm2 → Settings → General → Magic → ☑ Enable Python API`

### 3. Restart iTerm2

Cmd-Q, then reopen. Click **Allow** on the first-run API permission prompt. The daemon auto-launches on every subsequent iTerm2 start.

### 4. Show the panel

`View → Toolbelt → Show Toolbelt`, then right-click the toolbelt and tick **Claude Cockpit**.

iTerm2 remembers both settings, so this is a one-time step.

### Auto-open the panel in every new window (optional)

`Settings → Profiles → [your profile] → Window → ☑ Open toolbelt`

This is per-profile — repeat for any profile you use. Takes effect on the next new window.

### Updating

```bash
cd ~/code/iterm2_claude_cockpit && git pull
```

Then restart iTerm2. The symlink picks up your latest code automatically; no re-install needed.

### Uninstalling

```bash
bash ~/code/iterm2_claude_cockpit/uninstall.sh
# optionally also remove the repo:
rm -rf ~/code/iterm2_claude_cockpit
```

`uninstall.sh` removes only the AutoLaunch symlink; the repo is left untouched.

### Migrating from a previous install

If you installed an earlier version (folder-based Full Environment install), `install.sh` detects and offers to clean up:

- the old folder symlink at `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit`
- a leftover `iterm_workflow` entry from the pre-rename name

Both must be removed for AutoLaunch to find the new file symlink at startup. If you decline the prompt, iTerm2 will keep showing a "malformed script" warning for the old folder.

Also, if you see an old **Worktree** entry in the toolbelt menu, untick it and re-tick **Claude Cockpit**.

## Session restore

Closing iTerm2 kills every pane and its processes, so in-progress work is normally lost — which makes quitting or updating iTerm2 risky. Session restore brings your workspace layout back.

As you work, the daemon saves a snapshot of your layout (windows → tabs → panes, each pane's working directory) under `~/.config/iterm2-claude-cockpit/`.

Two files are kept: `state.json` mirrors your *current* layout, while `restore.json` holds the *last-good* layout that Restore recreates. The split matters — when iTerm2 relaunches it usually comes back with a single window, and keeping that degraded layout out of `restore.json` is what lets Restore still bring back your full previous workspace.

Click the **⟲ Restore** button in the footer (it shows a summary — how many windows, tabs, and panes — and confirms first) to recreate the saved windows, tabs, and panes. Each pane comes back as a plain shell `cd`'d into its saved directory. This works after both quitting/updating iTerm2 **and** a full machine reboot, because it reads from disk rather than keeping processes alive. To pick a Claude conversation back up, run `claude --continue` (or `claude --resume`) in the restored pane.

What it does **not** restore: running processes (Claude sessions, dev servers, builds — panes come back as a plain shell), terminal scrollback, and exact split sizes (panes come back as a simple vertical split). Custom tab names, group colors, and collapsed state are persisted in the same file and also survive restarts.

## Group link chips

Each group header can show small link chips that open a URL in your browser — handy for jumping from a pane straight to its pull request or ticket. The daemon is deliberately agnostic to what the links mean: it doesn't hardcode "PR" or "Jira", it just turns whatever links your status file carries into chips.

How it works:

1. In the working directories of a group's panes, it looks for a **status file** (default `.cockpit.json`).
2. Every **URL-valued property** of that file becomes a chip, **labeled with the property name**.
3. A chip with a single URL opens it on click; a chip whose property is a **list of URLs** opens a small popup listing them all.

Non-URL properties (e.g. a `branch` string) are ignored, and a group with no status file shows no chips.

**The status file.** Drop a `.cockpit.json` in a pane's working directory:

```json
{
  "jira_url": "https://jira.example.com/browse/PROJ-456",
  "pr_urls": [
    "https://github.com/org/frontend/pull/12",
    "https://github.com/org/backend/pull/34"
  ],
  "branch": "PROJ-456-do-the-thing"
}
```

This renders a **`jira_url`** chip (opens the ticket) and a **`pr_urls`** chip (opens a popup of both PRs). `branch` is ignored. The chip label is exactly the property name, so name your properties how you want them to read.

**Configuration.** The only setting is *which file* to read. To use a different filename, create `~/.config/iterm2-claude-cockpit/links.json`:

```json
{ "file": ".cockpit.json" }
```

Edits take effect on the next refresh — no restart needed. See `specs/http-api.md` for the full reference.

## Troubleshooting

**Check the console first:** `Scripts → Manage → Console` — Python tracebacks appear here.

**Verify the daemon is running:** `curl -s http://127.0.0.1:9876/` should return the panel HTML.

---

### The panel is blank / shows "connecting…"

The daemon isn't running. Check `Scripts → Manage → Console` for Python tracebacks. If the symlink is intact and the API is enabled, restart iTerm2.

---

### AutoLaunch never started the script — no permission prompt appeared

Check the symlink exists and points at this repo:

```bash
ls -la "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit.py"
```

If it's missing or broken, re-run `bash install.sh` from the repo. Also confirm `Settings → General → Magic → Enable Python API` is on.

If you previously had a *folder* at the same name (from an older install), AutoLaunch will show a one-time "Cannot Run Script — malformed" warning for it. Remove it:

```bash
rm -rf "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit"
```

---

### The Scripts menu shows nothing / the entry doesn't appear

`Scripts → AutoLaunch` should list `iterm2_claude_cockpit.py` as a single entry. If it's missing, the symlink wasn't placed — run `bash install.sh` again.

---

### `ModuleNotFoundError` in the console

The daemon's entry script adds its own directory to `sys.path` before importing — if you see a `ModuleNotFoundError`, your symlink probably points at a wrong file. The correct target is `<repo>/iterm2_claude_cockpit/iterm2_claude_cockpit.py` (the entry **inside** the inner package). Re-run `bash install.sh` to fix it.

---

### Toolbelt shows the panel but it disappears on new windows

This is a per-profile setting. Enable it in `Settings → Profiles → [your profile] → Window → ☑ Open toolbelt` for every profile you use.

---

### The `defaults write com.googlecode.iterm2 OpenToolbelt -bool true` command doesn't work

In iTerm2 3.6+ the global `OpenToolbelt` defaults key is overridden by per-profile settings. Use the profile setting above instead.

---

### Log paths show `~/.config/iterm2/AppSupport/Scripts/...` even though I used `~/Library/Application Support/...`

iTerm2 3.6+ moved its support directory to an XDG-style path. The legacy `~/Library/Application Support/iTerm2/` location symlinks transparently to the new one — both work, the different path in logs is expected.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT]
