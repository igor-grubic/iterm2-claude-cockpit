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

## Install model

The daemon ships as an iTerm2 **AutoLaunch Basic script** — a single file symlink at `Scripts/AutoLaunch/iterm2_claude_cockpit.py` pointing at the entry script inside this repo. iTerm2 runs it with its bundled Python, which ships with the `iterm2` library. The two-level layout (`iterm2_claude_cockpit/iterm2_claude_cockpit/iterm2_claude_cockpit.py`) is just a normal Python package — outer dir is the repo, inner dir is the package. It is **not** required by iTerm2; refactor it for normal Python reasons if you need to, and update the symlink target in `install.sh` if you move the entry file.

## Writing an extension

User-facing behavior that isn't strictly part of "manage iTerm2 windows/tabs/panes" should live in an extension under `iterm2_claude_cockpit/extensions/<name>/`, not in core. Each extension is a folder with `__init__.py` exposing `register(api)`. See the `claude` extension for a worked example and the README's [Extensions](README.md#extensions) section for the v1 API surface.

Use the `ext.<name>.<field>` namespace when adding fields to session nodes. Don't write top-level snapshot keys from an extension — collisions become a future problem.

## Submitting a pull request

1. Fork the repo and create a branch
2. Make changes and run `ruff check` + `mypy`
3. Open a PR describing what changed and why
