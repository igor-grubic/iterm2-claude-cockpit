#!/usr/bin/env bash
# Uninstall iterm2-claude-cockpit's AutoLaunch entry.
# Removes only the symlink — the repo is left untouched.

set -eu

AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
LINK="$AUTOLAUNCH/iterm2_claude_cockpit.py"

if [ -L "$LINK" ] || [ -e "$LINK" ]; then
  rm -f "$LINK"
  printf '\033[32m✓ Removed %s\033[0m\n' "$LINK"
else
  printf '\033[33mNo symlink found at %s — nothing to do.\033[0m\n' "$LINK"
fi

echo
echo "Restart iTerm2 to stop the daemon."
echo "The repo and your Claude Code hook config (~/.claude/settings.json) are untouched."
echo "To remove Claude Code hooks too, run:"
echo "  python3 \"\$REPO/iterm2_claude_cockpit/extensions/claude/hooks/uninstall.py\""
