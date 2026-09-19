"""Unit tests for the self-updater (no iTerm2 runtime needed).

Two layers:

- the pure parsing/policy helpers, exercised directly;
- the real `check`/`apply` flow, driven against throwaway git repositories
  built in a temp dir. That second layer is what actually pins the behaviour
  that matters (a fast-forward happens, a dirty tree blocks it, a sideways
  commit is refused), and it needs nothing but stdlib + the `git` binary.

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from iterm2_claude_cockpit.server import updater

HAVE_GIT = shutil.which("git") is not None


class ParseCommitsTest(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(updater.parse_commits(""), [])
        self.assertEqual(updater.parse_commits("\n\n"), [])

    def test_splits_sha_and_subject(self) -> None:
        raw = "79161b2\x1ffeat: make Classic the default panel theme (#14)\n887aada\x1ffeat: adds theme switcher (#11)"
        self.assertEqual(
            updater.parse_commits(raw),
            [
                {"sha": "79161b2", "subject": "feat: make Classic the default panel theme (#14)"},
                {"sha": "887aada", "subject": "feat: adds theme switcher (#11)"},
            ],
        )

    def test_subject_containing_separator_like_text(self) -> None:
        # Subjects routinely contain colons, spaces and parens; only the unit
        # separator delimits, so the subject must survive whole.
        raw = "abc1234\x1ffix: handle a: b (#9) — weird  spacing"
        self.assertEqual(updater.parse_commits(raw)[0]["subject"], "fix: handle a: b (#9) — weird  spacing")

    def test_respects_limit(self) -> None:
        raw = "\n".join(f"sha{i}\x1fsubject {i}" for i in range(50))
        self.assertEqual(len(updater.parse_commits(raw, limit=3)), 3)
        self.assertEqual(len(updater.parse_commits(raw)), updater._MAX_COMMITS)


class IsCleanTest(unittest.TestCase):
    def test_clean(self) -> None:
        self.assertTrue(updater.is_clean(""))
        self.assertTrue(updater.is_clean("   \n  "))

    def test_dirty(self) -> None:
        self.assertFalse(updater.is_clean(" M iterm2_claude_cockpit/server/http.py"))
        self.assertFalse(updater.is_clean("?? untracked.py"))


class BlockingReasonTest(unittest.TestCase):
    def test_happy_path(self) -> None:
        self.assertIsNone(updater.blocking_reason("main", "origin/main", ""))

    def test_detached_head_wins_over_wrong_branch(self) -> None:
        # A detached HEAD also fails the branch-name test; the more precise
        # reason is the useful one to show.
        self.assertEqual(updater.blocking_reason("HEAD", "", ""), "detached_head")
        self.assertEqual(updater.blocking_reason("", "", ""), "detached_head")

    def test_wrong_branch(self) -> None:
        self.assertEqual(updater.blocking_reason("feat/x", "origin/feat/x", ""), "wrong_branch")

    def test_no_upstream(self) -> None:
        self.assertEqual(updater.blocking_reason("main", "", ""), "no_upstream")
        self.assertEqual(updater.blocking_reason("main", "fork/main", ""), "no_upstream")

    def test_dirty_tree(self) -> None:
        self.assertEqual(updater.blocking_reason("main", "origin/main", " M a.py"), "dirty_tree")


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@unittest.skipUnless(HAVE_GIT, "git not available")
class UpdateFlowTest(unittest.TestCase):
    """Drive check/apply against a real origin + clone pair."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin"
        self.origin.mkdir()
        _git(self.origin, "init", "--quiet", "--initial-branch=main")
        (self.origin / "app.py").write_text("v1\n")
        _git(self.origin, "add", ".")
        _git(self.origin, "commit", "--quiet", "-m", "initial")
        self.clone = self.tmp / "clone"
        _git(self.tmp, "clone", "--quiet", str(self.origin), str(self.clone))

    def _push(self, message: str, files: dict[str, str]) -> str:
        for name, content in files.items():
            (self.origin / name).write_text(content)
        _git(self.origin, "add", ".")
        _git(self.origin, "commit", "--quiet", "-m", message)
        return _git(self.origin, "rev-parse", "--short", "HEAD")

    def test_up_to_date(self) -> None:
        result = updater.check(root=self.clone)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["update_available"])
        self.assertEqual(result["behind"], 0)
        self.assertNotIn("blocked", result)

    def test_detects_and_applies_update(self) -> None:
        self._push("feat: add a thing", {"app.py": "v2\n"})
        self._push("fix: a second thing", {"app.py": "v3\n"})

        result = updater.check(root=self.clone)
        self.assertTrue(result["update_available"])
        self.assertEqual(result["behind"], 2)
        self.assertEqual(
            [c["subject"] for c in result["commits"]],
            ["fix: a second thing", "feat: add a thing"],  # git log order: newest first
        )
        self.assertFalse(result["installer_changed"])

        applied = updater.apply(result["latest"], root=self.clone)
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["current"], result["latest"])
        # The fast-forward actually wrote the new file contents to disk.
        self.assertEqual((self.clone / "app.py").read_text(), "v3\n")
        self.assertFalse(updater.check(root=self.clone)["update_available"])

    def test_flags_installer_change(self) -> None:
        self._push("chore: move the symlink", {"install.sh": "#!/usr/bin/env bash\necho new\n"})
        result = updater.check(root=self.clone)
        self.assertTrue(result["installer_changed"])

    def test_dirty_tree_blocks_apply(self) -> None:
        latest = self._push("feat: something", {"app.py": "v2\n"})
        (self.clone / "app.py").write_text("local edit\n")

        result = updater.check(root=self.clone)
        # The update is still reported — the user deserves to know it exists —
        # but flagged as un-appliable by this daemon.
        self.assertTrue(result["update_available"])
        self.assertEqual(result["blocked"], "dirty_tree")

        applied = updater.apply(latest, root=self.clone)
        self.assertFalse(applied["ok"])
        self.assertEqual(applied["reason"], "dirty_tree")
        self.assertEqual((self.clone / "app.py").read_text(), "local edit\n")

    def test_refuses_non_fast_forward(self) -> None:
        """A sha that isn't a descendant of HEAD must never be checked out."""
        self._push("feat: upstream work", {"app.py": "v2\n"})
        updater.check(root=self.clone)  # fetch it into the clone's object db
        # Commit locally so HEAD diverges from origin/main.
        (self.clone / "local.py").write_text("local\n")
        _git(self.clone, "add", ".")
        _git(self.clone, "commit", "--quiet", "-m", "local work")
        head_before = _git(self.clone, "rev-parse", "HEAD")

        applied = updater.apply(_git(self.origin, "rev-parse", "--short", "HEAD"), root=self.clone)
        self.assertFalse(applied["ok"])
        self.assertEqual(applied["reason"], "not_a_fast_forward")
        self.assertEqual(_git(self.clone, "rev-parse", "HEAD"), head_before)

    def test_rejects_malformed_sha(self) -> None:
        applied = updater.apply("; rm -rf /", root=self.clone)
        self.assertFalse(applied["ok"])
        self.assertEqual(applied["reason"], "bad_sha")

    def test_not_a_git_checkout(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        result = updater.check(root=plain)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_a_git_checkout")


if __name__ == "__main__":
    unittest.main()
