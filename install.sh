#!/usr/bin/env bash
# Install iterm2-claude-cockpit as an iTerm2 AutoLaunch Basic-script daemon.
#
# Places a single .py file symlink at:
#   ~/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit.py
# pointing at the daemon entry script in this repo.
#
# Re-running is safe. Pass --force to overwrite a divergent existing symlink.

set -eu

SCRIPT_NAME="iterm2_claude_cockpit.py"
PACKAGE_DIR="iterm2_claude_cockpit"
AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
GLOBAL_PYENV="$HOME/Library/Application Support/iTerm2/iterm2env"
ITERM_APP="/Applications/iTerm.app"
TARGET_REL="iterm2_claude_cockpit/iterm2_claude_cockpit.py"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FORCE=0
DRY_RUN=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

runcmd() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

usage() {
  cat <<'EOF'
Install iterm2-claude-cockpit as an iTerm2 AutoLaunch daemon.

USAGE:
  bash install.sh [--force] [--dry-run] [--help]

OPTIONS:
  --force     Overwrite any divergent existing symlink at the AutoLaunch target,
              and remove old install entries without prompting.
  --dry-run   Print what would be done; make no changes.
  --help      Show this message.

WHAT IT DOES:
  1. Verifies iTerm2 is installed and its bundled Python has the iterm2 module.
  2. Removes any stale AutoLaunch entry from a previous install (with consent).
  3. Creates a single file symlink at:
       ~/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2_claude_cockpit.py
     pointing at the entry script in this repo.

After install: restart iTerm2 and tick Claude Cockpit in the toolbelt.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --force)    FORCE=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    -h|--help)  usage; exit 0 ;;
    *)          red "Unknown argument: $1"; echo; usage; exit 2 ;;
  esac
  shift
done

# ─── 1. Repo sanity ──────────────────────────────────────────────────────────

TARGET="$REPO/$TARGET_REL"
if [ ! -f "$REPO/setup.cfg" ] || [ ! -f "$TARGET" ]; then
  red "✗ This doesn't look like an iterm2-claude-cockpit clone."
  red "  Expected: $REPO/setup.cfg and $TARGET"
  exit 1
fi
green "✓ Repo: $REPO"

# ─── 2. iTerm2 sanity ────────────────────────────────────────────────────────

if [ ! -d "$ITERM_APP" ]; then
  red "✗ iTerm2 not found at $ITERM_APP"
  yellow "  Install from https://iterm2.com first, then re-run."
  exit 1
fi
green "✓ iTerm2 installed"

PYTHON=""
shopt -s nullglob
for bin_dir in "$GLOBAL_PYENV"/versions/*/bin; do
  candidate="$bin_dir/python3"
  if [ -x "$candidate" ] && "$candidate" -c "import iterm2" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
shopt -u nullglob

if [ -z "$PYTHON" ]; then
  red "✗ iTerm2's bundled Python with the 'iterm2' module wasn't found."
  red "  Looked under: $GLOBAL_PYENV/versions/*/bin/python3"
  echo
  yellow "  Fix: launch iTerm2 once so it self-installs its Python runtime,"
  yellow "  then re-run this script. If iTerm2 has never bootstrapped its"
  yellow "  runtime, open Scripts → New Python Script, pick 'Full Environment',"
  yellow "  and follow the prompts once."
  exit 1
fi
green "✓ Bundled Python with iterm2 module: $PYTHON"

# ─── 3. Migration: clean up previous install ─────────────────────────────────

mkdir -p "$AUTOLAUNCH"

OLD_FOLDER="$AUTOLAUNCH/$PACKAGE_DIR"
OLD_WORKFLOW="$AUTOLAUNCH/iterm_workflow"
LINK="$AUTOLAUNCH/$SCRIPT_NAME"

prompt_remove() {
  local path="$1"
  local what="$2"
  local answer
  if [ "$FORCE" = 1 ]; then
    runcmd rm -rf "$path"
    green "  removed $what"
    return
  fi
  printf "Remove %s at %s? [Y/n] " "$what" "$path"
  answer=y
  read -r answer || true
  case "${answer:-y}" in
    [yY]*|"")
      runcmd rm -rf "$path"
      green "  removed"
      ;;
    *)
      yellow "  kept — iTerm2 may still show a 'malformed script' warning at startup for this entry"
      ;;
  esac
}

if [ -e "$OLD_FOLDER" ] || [ -L "$OLD_FOLDER" ]; then
  if [ -L "$OLD_FOLDER" ]; then
    yellow "Found old folder symlink: $OLD_FOLDER → $(readlink "$OLD_FOLDER")"
  else
    yellow "Found old folder: $OLD_FOLDER"
  fi
  prompt_remove "$OLD_FOLDER" "old Full-Environment AutoLaunch entry"
fi

if [ -e "$OLD_WORKFLOW" ] || [ -L "$OLD_WORKFLOW" ]; then
  yellow "Found leftover entry from a prior rename: $OLD_WORKFLOW"
  prompt_remove "$OLD_WORKFLOW" "old iterm_workflow entry"
fi

# ─── 4. Place the symlink ────────────────────────────────────────────────────

SKIP_LINK=0
if [ -L "$LINK" ]; then
  EXISTING="$(readlink "$LINK")"
  if [ "$EXISTING" = "$TARGET" ]; then
    green "✓ Symlink already in place: $LINK"
    SKIP_LINK=1
  elif [ "$FORCE" = 1 ]; then
    yellow "Replacing existing symlink (was → $EXISTING)"
    runcmd rm "$LINK"
  else
    red "✗ A different symlink already exists at $LINK"
    red "  Currently points to: $EXISTING"
    red "  Re-run with --force to replace it."
    exit 1
  fi
elif [ -e "$LINK" ]; then
  red "✗ A file (not a symlink) already exists at $LINK"
  red "  Move or delete it first, then re-run."
  exit 1
fi

if [ "$SKIP_LINK" = 0 ]; then
  runcmd ln -s "$TARGET" "$LINK"
  green "✓ Symlink created:"
  green "    $LINK"
  green "      → $TARGET"
fi

# ─── 5. Next steps ───────────────────────────────────────────────────────────

echo
bold "Install complete."
cat <<EOF

Next steps:
  1. If not already on, enable iTerm2's Python API:
       iTerm2 → Settings → General → Magic → ☑ Enable Python API
  2. Restart iTerm2 (Cmd-Q, then reopen).
  3. Click "Allow" on the first-run API permission prompt.
  4. Show the panel:
       View → Toolbelt → Show Toolbelt
       Right-click the toolbelt → tick Claude Cockpit
  5. (Optional) Wire up Claude Code status hooks:
       python3 "$REPO/$PACKAGE_DIR/extensions/claude/hooks/install.py"

Verify the daemon is running after restart:
  curl -s http://127.0.0.1:9876/ | head -3

Python tracebacks (if anything fails):
  Inside iTerm2: Scripts → Manage → Console
EOF
