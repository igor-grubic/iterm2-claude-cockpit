# CLAUDE.md — Agent Guide for iterm2-claude-cockpit

This file is the authoritative guide for AI agents (Claude Code and others) working in this repo. Read it before making any changes.

---

## Project in one sentence

`iterm2-claude-cockpit` is a zero-dependency iTerm2 AutoLaunch daemon (Basic-script .py symlinked into `Scripts/AutoLaunch/`) that serves a live window/tab/pane tree as a toolbelt cockpit, purpose-built for managing parallel Claude Code panes.

---

## Install model

The daemon ships as an iTerm2 **AutoLaunch Basic script** — installed via a single file symlink:

```
~/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit.py
   → <repo>/iterm2_claude_cockpit/iterm2_claude_cockpit.py
```

iTerm2 runs the symlinked `.py` with its bundled Python (which already has the `iterm2` library). The entry script does `sys.path.insert(0, str(Path(__file__).resolve().parent))` so imports resolve through the symlink to the inner package.

`install.sh` and `uninstall.sh` at the repo root manage the symlink. The repo can live anywhere — it doesn't have to be inside `AutoLaunch/`.

The two-level layout (`iterm2_claude_cockpit/iterm2_claude_cockpit/iterm2_claude_cockpit.py`) is just a normal Python package — outer folder is the repo, inner folder is the package. It is **not** required by iTerm2; you can refactor it for normal Python reasons. If you do, update the path in `install.sh` and the symlink target in any active install.

---

## Architecture

See `specs/architecture.md` for the full picture. Quick map:

| Module | Role |
|--------|------|
| `iterm2_claude_cockpit.py` | Daemon entry point; registers iTerm2 update hooks |
| `server/tree.py` | Builds the JSON snapshot (window → tab → pane) |
| `server/http.py` | Serves the panel HTML and `/api/*` routes |
| `server/actions.py` | Handles user actions: focus, create, close, restore |
| `server/persistence.py` | Saves/loads the workspace layout for session restore |
| `webview/` | The browser-side panel (HTML/CSS/JS), incl. the Claude cheatsheet |
| `projects/` | User-defined YAML project layouts |

---

## Code style

- Formatter and linter: `ruff` (config in `pyproject.toml`, line length 120)
- Type checker: `mypy` with `ignore_missing_imports = true` (iterm2 has no stubs)
- **No external runtime dependencies** — stdlib + the `iterm2` library only
- Run before every commit:
  ```bash
  ruff check iterm2_claude_cockpit/
  ruff format iterm2_claude_cockpit/
  mypy iterm2_claude_cockpit/
  ```

---

## Changelog — always update

The changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.

**Rules:**
- Every user-visible change goes under `## [Unreleased]` before a release
- Use subsections: `### Added`, `### Changed`, `### Fixed`, `### Removed`
- One bullet per change, written for a user, not a developer ("Added X" not "Implement X")
- Do not touch dated version sections unless doing a release

**When to update:** Any time you add a feature, fix a bug visible to users, or change user-facing behavior. Internal refactors with no observable effect don't need a changelog entry.

---

## README — keep in sync

The README is the public face. Keep these sections up to date:

- **Features list** — add a bullet if you add a user-visible capability
- **Troubleshooting** — add an entry for any new failure mode with a known fix
- **Badges** — do not change the badge URLs; they point to the live CI and shields.io

The Python version badge must reflect `requires-python` in `pyproject.toml`. Both are currently `3.10+`.

---

## Specs folder

`specs/` contains agentic-readable specifications. These are the source of truth for contracts and formats — consult them before changing any interface.

| File | What it covers |
|------|----------------|
| `specs/architecture.md` | System architecture, data flow, module responsibilities |
| `specs/snapshot-format.md` | The JSON tree snapshot schema (all fields, types, invariants) |
| `specs/http-api.md` | All HTTP endpoints, request/response shapes |

**When to update specs:** Whenever the contract described in a spec file changes. A spec going out of sync with the code is a bug. If you add an HTTP route, update `specs/http-api.md`. If you change the snapshot shape, update `specs/snapshot-format.md`.

---

## Testing

There are no automated integration tests — iTerm2's runtime environment cannot run in CI. The CI pipeline runs:

1. `ruff check` + `ruff format --check` (lint/format)
2. `mypy` (type checking)
3. `python -m unittest discover -s tests` (stdlib unit tests — iterm2-free logic only)
4. YAML validation for `iterm2_claude_cockpit/projects/*.yaml`

Manual testing: install via symlink (see CONTRIBUTING.md), run the daemon, exercise the feature in iTerm2.

When adding logic that doesn't depend on the iTerm2 runtime (e.g., parsing, data transformation, path manipulation), write it in a way that can be tested in isolation and add a case under `tests/`. Keep tests stdlib-only and import-light — the import chain must not pull in `iterm2` (unavailable in CI), so test pure helpers in `server/persistence.py`-style modules, not anything importing `http.py`.

---

## Commits and PRs

- Branch names: `<type>/<short-description>` (e.g., `feat/session-restore`, `fix/tab-rename`)
- Commit messages: imperative mood, present tense ("Add X", not "Added X")
- PR checklist (also in `.github/pull_request_template.md`):
  - `ruff check` passes
  - `ruff format --check` passes
  - `mypy` passes
  - CHANGELOG updated under `[Unreleased]`
  - README updated if user-facing behavior changed
  - Relevant spec file updated if a contract changed

---

## What NOT to do

- Do not add external runtime dependencies (anything beyond `iterm2` and stdlib) — the script runs with iTerm2's bundled Python (no per-script venv)
- Do not commit `iterm2env/` or anything under `iterm2env-*/` (gitignored for a reason)
- Do not push directly to `main` — open a PR
- Do not let this file exceed 200 lines — keep it scannable; move detail into `specs/`
