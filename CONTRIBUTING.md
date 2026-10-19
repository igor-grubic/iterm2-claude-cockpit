# Contributing

Bug reports and pull requests are welcome.

## Reporting bugs

Open an issue and include:
- iTerm2 version (`Help → About iTerm2`)
- macOS version
- The full error from `Scripts → Manage → Console`
- Steps to reproduce

## Suggesting features

Open an issue with the label `enhancement`. Describe the use case, not just the feature.

## Development setup

```bash
git clone https://github.com/igorgrubic/iterm2-claude-cockpit.git ~/code/iterm2_claude_cockpit
cd ~/code/iterm2_claude_cockpit
bash install.sh
```

`install.sh` creates a single file symlink at `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit.py` pointing at the entry script. `git pull` updates the code — restart iTerm2 to pick up changes.

Install dev tools (these run on your local Python, not inside iTerm2's env):

```bash
pip install ruff mypy
```

Lint and format:

```bash
ruff check iterm2_claude_cockpit/
ruff format iterm2_claude_cockpit/
```

Type check:

```bash
mypy iterm2_claude_cockpit/
```

## Code style

- `ruff` enforces formatting and lint (see `pyproject.toml`)
- No external runtime dependencies — stdlib + `iterm2` only
- The `iterm2` library has no type stubs; `ignore_missing_imports = true` is set in mypy config

## README demo

The README animation is a captioned slideshow of browser states captured with Playwright, not a live terminal recording. The fixture serves the **real** `webview/` assets next to a synthetic terminal preview. All sessions, paths, and output are examples; the fixture never connects to iTerm2, starts shells, or launches Claude.

Preview it with Node.js 20+:

```bash
npm run demo
```

Open `http://127.0.0.1:9877/demo`. Focus, color, collapse, sleep/wake, rename, theme switching, pane closing, and workspace restore work against in-memory sample data. Restore creates a new sample window with plain shell panes in the saved directories, leaving existing windows unchanged; it does not resume running commands. Other actions return an explicit demo-only error. Stop the server with Ctrl-C; no state is persisted. Use `npm run demo -- --port 0` for an available port.

To regenerate the media, install the development-only tooling and FFmpeg (`brew install ffmpeg` on macOS):

```bash
npm ci
npx playwright install chromium
npm run demo:record -- --update-readme
```

The recorder starts its own isolated server and browser, walks `scripts/demo/scenes.json`, and produces a looping GIF plus an MP4 under `.playwright-mcp/demo/video/`. Captions are rendered in the browser; no image library is needed. `--update-readme` explicitly copies the GIF to `docs/demo.gif`; without it, the checked-in media is left untouched. No production daemon code or runtime dependencies are changed.

The walkthrough stays in Classic and covers pane focus, group colors, collapse/expand, copying an inactive pane's path, sleep/wake with the moon icon, and a before-and-after restore demonstration. It closes a sample pane, shows the missing pane beside the saved-layout counts, holds the restore confirmation, and recreates the saved workspace as shells in a new sample window. The before/after callout is part of the demo framing, not the production panel. Update the scenes when the UI changes. Captures use a fixed 1000×760 viewport and synthetic clipboard writes so recording does not overwrite your clipboard.

To encode existing Playwright MCP screenshots instead of recapturing:

```bash
npm run demo:record -- --encode-only --out .playwright-mcp/docs_playwright-demo/video --update-readme
```

Use the scene filenames and the same viewport, and call the demo page's `setDemoCaption(caption, step)` before each screenshot so the frames already include their captions. Intermediate frames and videos are gitignored; only the refreshed README GIF is checked in.

`npm run test:demo` exercises the fixture's API and scene definitions without iTerm2 or a browser. CI runs it alongside the Python checks. Node.js, Playwright, and FFmpeg are **only** needed for this development/demo workflow, not to install or run the cockpit.

## Install model

The daemon ships as an iTerm2 **AutoLaunch Basic script** — a single file symlink at `Scripts/AutoLaunch/iterm2_claude_cockpit.py` pointing at the entry script inside this repo. iTerm2 runs it with its bundled Python, which ships with the `iterm2` library. The two-level layout (`iterm2_claude_cockpit/iterm2_claude_cockpit/iterm2_claude_cockpit.py`) is just a normal Python package — outer dir is the repo, inner dir is the package. It is **not** required by iTerm2; refactor it for normal Python reasons if you need to, and update the symlink target in `install.sh` if you move the entry file.

## Project scope

The daemon is intentionally small and non-extensible: a live window/tab/pane tree with focus/create/split/rename/restore controls and a built-in Claude Code cheatsheet. Keep new work aligned with that scope — no plugin framework, no external runtime dependencies (stdlib + `iterm2` only), and no per-pane process polling. See `specs/` for the snapshot, HTTP, and architecture contracts before changing an interface.

## Submitting a pull request

1. Fork the repo and create a branch
2. Make changes and run `ruff check` + `mypy`
3. Open a PR describing what changed and why
