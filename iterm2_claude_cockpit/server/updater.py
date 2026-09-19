"""In-place self-update over the repo's own git checkout.

The install is a `git clone` plus a single symlink into iTerm2's AutoLaunch
folder (see install.sh), so the checkout *is* the running app. That makes
updating a fast-forward of the checkout followed by a restart of the daemon —
no download/unpack step, and nothing to install afterwards (the project has no
runtime dependencies beyond the iterm2 library iTerm2 itself ships).

Two phases, deliberately split:

- `check()` runs `git fetch`, which downloads the new commits *and their file
  contents* into `.git` without touching the working tree or HEAD. By the time
  the user is looking at "3 updates available", the bytes are already on disk.
- `apply(sha)` is then purely local: a fast-forward is moving a ref and writing
  out files. It takes the sha `check()` reported rather than re-pulling, so the
  user installs exactly the commits whose subjects they were shown, even if
  someone pushed again in between.

Everything here shells out to git with a timeout and a hostile environment
(no credential prompts, no SSH passphrase prompts) — a blocked `git fetch`
would otherwise hang an HTTP worker thread forever.

Stdlib only. The git-output parsing is pure and unit-tested in
tests/test_updater.py; nothing in this module imports iterm2.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("iterm2_claude_cockpit.updater")

# The branch the updater tracks. Releases are cut from main; there are no tags
# yet. Switching to tags later means changing this to a tag-resolving ref.
TRACKED_BRANCH = "main"
TRACKED_REMOTE = "origin"

# git calls are network-bound at worst; keep every one of them bounded so a
# handler thread can't be parked indefinitely.
_FETCH_TIMEOUT = 20.0
_LOCAL_TIMEOUT = 10.0

# Files whose change means the symlink/install layout may have moved, so a
# plain fast-forward isn't enough — the user has to re-run the installer.
_INSTALLER_FILES = ("install.sh", "uninstall.sh")

# Cap the commit list handed to the panel; a user 200 commits behind does not
# need 200 rows in a toolbelt-width modal.
_MAX_COMMITS = 20


class UpdateError(Exception):
    """A git step failed in a way the panel should report verbatim."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def repo_root() -> Path:
    """The checkout this daemon is running from.

    `__file__` is resolved, so this holds when iTerm2 launches the entry script
    through the AutoLaunch symlink (server/ → package → repo).
    """
    return Path(__file__).resolve().parent.parent.parent


def _git_env() -> dict[str, str]:
    """Environment that makes git fail fast instead of prompting.

    Without this an SSH clone with a passphrase-protected key, or an HTTPS clone
    with expired credentials, blocks on a prompt that has no terminal to appear
    on — parking the HTTP worker thread until the daemon is killed.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    return env


def _git(*args: str, timeout: float = _LOCAL_TIMEOUT, root: Path | None = None) -> str:
    """Run a git command in the checkout and return its stripped stdout."""
    cwd = root or repo_root()
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
            check=False,
        )
    except FileNotFoundError as exc:
        raise UpdateError("git_missing", "the `git` command is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("timeout", f"git {args[0]} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        raise UpdateError("git_failed", (proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


def parse_commits(raw: str, limit: int = _MAX_COMMITS) -> list[dict[str, str]]:
    """Parse `git log --format=%h\\x1f%s` output into commit dicts.

    Split on a unit separator rather than whitespace so commit subjects
    containing anything at all survive intact.
    """
    commits: list[dict[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\x1f")
        commits.append({"sha": sha.strip(), "subject": subject.strip()})
        if len(commits) >= limit:
            break
    return commits


def is_clean(status_porcelain: str) -> bool:
    """True when `git status --porcelain` reports no local modifications.

    Untracked files count as dirty: a fast-forward that wants to create a path
    the user already has untracked fails, and it is better to say so up front
    than to half-apply.
    """
    return not status_porcelain.strip()


def blocking_reason(branch: str, upstream: str, status_porcelain: str) -> str | None:
    """The reason an automatic update can't proceed, or None when it can.

    Kept pure and separate from the git calls so the precondition policy can be
    tested without a repo. Order matters: report the most fundamental problem
    first, since a detached HEAD also looks like "wrong branch".
    """
    if branch == "HEAD" or not branch:
        return "detached_head"
    if branch != TRACKED_BRANCH:
        return "wrong_branch"
    if upstream != f"{TRACKED_REMOTE}/{TRACKED_BRANCH}":
        return "no_upstream"
    if not is_clean(status_porcelain):
        return "dirty_tree"
    return None


def _describe_head(root: Path) -> dict[str, str]:
    return {
        "current": _git("rev-parse", "--short", "HEAD", root=root),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD", root=root),
    }


def _upstream(root: Path) -> str:
    try:
        return _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", root=root)
    except UpdateError:
        return ""


def check(root: Path | None = None) -> dict[str, Any]:
    """Fetch and report whether the checkout is behind the tracked branch.

    Never raises: the panel gets `{"ok": False, "reason": ...}` for every
    failure mode, because "couldn't check" is a normal state (offline laptop,
    zip download instead of a clone) and must not surface as a 500.
    """
    try:
        root = root or repo_root()
        if not (root / ".git").exists():
            return {"ok": False, "reason": "not_a_git_checkout", "detail": str(root)}

        head = _describe_head(root)
        upstream = _upstream(root)
        status = _git("status", "--porcelain", root=root)
        blocked = blocking_reason(head["branch"], upstream, status)

        remote_ref = f"{TRACKED_REMOTE}/{TRACKED_BRANCH}"
        _git("fetch", "--quiet", TRACKED_REMOTE, TRACKED_BRANCH, timeout=_FETCH_TIMEOUT, root=root)

        behind = int(_git("rev-list", "--count", f"HEAD..{remote_ref}", root=root) or "0")
        latest = _git("rev-parse", "--short", remote_ref, root=root)
        latest_date = _git("log", "-1", "--format=%cI", remote_ref, root=root)

        result: dict[str, Any] = {
            "ok": True,
            "update_available": behind > 0,
            "behind": behind,
            "current": head["current"],
            "latest": latest,
            "latest_date": latest_date,
            "commits": [],
            "installer_changed": False,
        }
        if behind > 0:
            result["commits"] = parse_commits(_git("log", "--format=%h\x1f%s", f"HEAD..{remote_ref}", root=root))
            changed = _git("diff", "--name-only", "HEAD", remote_ref, "--", *_INSTALLER_FILES, root=root)
            result["installer_changed"] = bool(changed.strip())
        # Report the blocker alongside the count: the user still deserves to know
        # an update exists even when this daemon can't be the one to apply it.
        if blocked:
            result["blocked"] = blocked
        return result
    except UpdateError as exc:
        log.warning("update check failed (%s): %s", exc.reason, exc.detail)
        return {"ok": False, "reason": exc.reason, "detail": exc.detail}
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("update check crashed")
        return {"ok": False, "reason": "internal_error", "detail": str(exc)}


def apply(sha: str, root: Path | None = None) -> dict[str, Any]:
    """Fast-forward the checkout to `sha`.

    Re-verifies every precondition rather than trusting the preceding check:
    minutes may have passed with the modal open, and the tree can have gone
    dirty in between.
    """
    try:
        root = root or repo_root()
        if not sha or not sha.replace("-", "").isalnum():
            return {"ok": False, "reason": "bad_sha", "detail": sha}

        head = _describe_head(root)
        blocked = blocking_reason(head["branch"], _upstream(root), _git("status", "--porcelain", root=root))
        if blocked:
            return {"ok": False, "reason": blocked}

        # Only ever move forward, and only onto something already fetched. Without
        # this an arbitrary sha posted to the endpoint could check out any commit
        # in the object database.
        try:
            _git("merge-base", "--is-ancestor", "HEAD", sha, root=root)
        except UpdateError as exc:
            return {"ok": False, "reason": "not_a_fast_forward", "detail": exc.detail}

        _git("merge", "--ff-only", sha, root=root)
        new_head = _git("rev-parse", "--short", "HEAD", root=root)
        log.info("updated %s → %s", head["current"], new_head)
        return {"ok": True, "previous": head["current"], "current": new_head}
    except UpdateError as exc:
        log.warning("update apply failed (%s): %s", exc.reason, exc.detail)
        return {"ok": False, "reason": exc.reason, "detail": exc.detail}
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("update apply crashed")
        return {"ok": False, "reason": "internal_error", "detail": str(exc)}


def schedule_restart(flush: Callable[[], None], delay: float = 0.5) -> None:
    """Re-exec the daemon in place, shortly, so the caller can answer first.

    `os.execv` replaces the process image while keeping the pid, so iTerm2 never
    sees its AutoLaunch script exit — the websocket fd closes on exec and the
    new image reconnects and re-registers the toolbelt. The HTTP socket closes
    with it, and ThreadingHTTPServer sets SO_REUSEADDR, so port 9876 rebinds
    immediately. The panel's poll loop rides out the ~1s gap on its own
    ("disconnected — retrying") and recovers without user action.

    The delay exists so the HTTP response for /api/update/apply is flushed to
    the webview before this thread's process stops existing.
    """

    def _restart() -> None:
        try:
            flush()  # the workspace layout may be inside its 5s persist debounce
        except Exception:
            log.exception("pre-restart flush failed; restarting anyway")
        log.info("re-exec: %s %s", sys.executable, " ".join(sys.argv))
        # Buffered output is discarded by exec, and these are the last lines that
        # explain the restart in the iTerm2 script console.
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except Exception:
            log.exception("re-exec failed — daemon still running the pre-update code")

    threading.Timer(delay, _restart).start()
