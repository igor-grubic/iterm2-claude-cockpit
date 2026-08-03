#!/bin/sh
# Claude Code hook — writes a TTY-keyed signal file for the iterm2-pane-tree daemon.
#
# Usage (via ~/.claude/settings.json):
#   notify.sh running    # UserPromptSubmit hook
#   notify.sh idle       # Stop hook
#   notify.sh attention  # Notification hook
#
# The script is intentionally silent on failure — it must never interrupt
# the user's Claude Code session.

set -u

state=${1:-running}
out_dir="/tmp/iterm-pane-tree/claude"

# Whitelist state to keep JSON construction safe and to reject typos in
# settings.json — unknown values are silently dropped.
case "$state" in
    running|idle|attention) ;;
    *) exit 0 ;;
esac

# Read stdin: Claude Code pipes the hook payload here.
stdin_payload=$(cat)

# The Notification hook fires for two situations; only permission_prompt
# means Claude is genuinely blocked and needs user action. idle_prompt fires
# when Claude finishes a turn and sits idle — treat that as idle, not attention.
if [ "$state" = "attention" ]; then
    notif_type=$(printf '%s' "$stdin_payload" | sed 's/.*"notification_type":"\([^"]*\)".*/\1/')
    if [ "$notif_type" = "idle_prompt" ]; then
        state="idle"
    fi
fi

# Claude's own session id, captured from the payload so the cockpit can later
# resume this pane via `claude --resume <id>`. Extracted and validated the same
# defensive way as the fields above — anything unexpected is dropped, never
# written into the JSON unescaped.
claude_session_id=""
raw_csid=$(printf '%s' "$stdin_payload" | sed 's/.*"session_id":"\([^"]*\)".*/\1/')
case "$raw_csid" in
    "") ;;
    *[!0-9A-Fa-f-]*) ;;  # not a UUID charset — reject (also catches no-match: whole payload)
    *) claude_session_id="$raw_csid" ;;
esac

# Build the optional ,"claude_session_id":"…" suffix appended to both JSON branches.
extra=""
if [ -n "$claude_session_id" ]; then
    extra=",\"claude_session_id\":\"$claude_session_id\""
fi

ts=$(date +%s)

# Primary: use ITERM_SESSION_ID if available — works in daemon mode where
# there is no controlling TTY.
session_guid=""
raw_guid=$(printf '%s' "${ITERM_SESSION_ID:-}" | sed 's/.*://')
case "$raw_guid" in
    "") ;;
    *[!0-9A-Za-z-]*) ;;  # contains invalid chars — reject
    *) session_guid="$raw_guid" ;;
esac

if [ -n "$session_guid" ]; then
    mkdir -p "$out_dir"
    json="{\"session\":\"$session_guid\",\"state\":\"$state\",\"ts\":$ts$extra}"
    tmp="$out_dir/$session_guid.json.tmp.$$"
    printf '%s' "$json" > "$tmp" && mv -f "$tmp" "$out_dir/$session_guid.json"
    exit 0
fi

# Fallback: discover controlling TTY (inline / non-daemon mode).
# Stdin is consumed above, so we probe stderr.
tty_path=$(tty <&2 2>/dev/null)
if [ -z "$tty_path" ] || [ "$tty_path" = "not a tty" ]; then
    # Ask ps for the TTY of the parent (the Claude CLI process).
    raw=$(ps -p "$PPID" -o tty= 2>/dev/null | tr -d ' ')
    if [ -n "$raw" ] && [ "$raw" != "??" ]; then
        tty_path="/dev/$raw"
    fi
fi

# Validate the TTY path against the macOS/Linux conventions. Anything else
# (including empty) is rejected so we never write user-controlled bytes into
# the JSON payload unescaped.
case "$tty_path" in
    /dev/ttys[0-9]*|/dev/ttyp[0-9]*|/dev/pts/[0-9]*) ;;
    *) exit 0 ;;
esac

tty_base=$(basename "$tty_path")
mkdir -p "$out_dir"

json="{\"tty\":\"$tty_path\",\"state\":\"$state\",\"ts\":$ts$extra}"

# Atomic write: write to a temp file then rename to avoid partial reads.
tmp="$out_dir/$tty_base.json.tmp.$$"
printf '%s' "$json" > "$tmp" && mv -f "$tmp" "$out_dir/$tty_base.json"
