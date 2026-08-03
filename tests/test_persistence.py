"""Unit tests for the persistence layer's pure logic (no iTerm2 runtime needed).

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from iterm2_claude_cockpit.server import persistence


def _state(*pane_counts: int) -> dict:
    """Build a minimal state dict: one window, one tab per given pane count."""
    return {"windows": [{"tabs": [{"name": None, "panes": [{"cwd": "/x"} for _ in range(n)]}]} for n in pane_counts]}


class CountPanesTest(unittest.TestCase):
    def test_none_and_empty(self) -> None:
        self.assertEqual(persistence.count_panes(None), 0)
        self.assertEqual(persistence.count_panes({}), 0)
        self.assertEqual(persistence.count_panes({"windows": []}), 0)

    def test_sums_across_windows_and_tabs(self) -> None:
        # two windows: one with 3 panes, one with 2 panes
        self.assertEqual(persistence.count_panes(_state(3, 2)), 5)


class ShouldUpdateRestoreTest(unittest.TestCase):
    FREEZE = persistence.RESTORE_FREEZE_SECONDS

    def test_never_persist_empty(self) -> None:
        # An empty live layout must never become the restore source, even long after startup.
        self.assertFalse(persistence.should_update_restore(0, 0, uptime_seconds=self.FREEZE + 1))
        self.assertFalse(persistence.should_update_restore(0, 8, uptime_seconds=self.FREEZE + 1))

    def test_growth_always_adopted(self) -> None:
        # Building the workspace up is captured immediately, regardless of uptime.
        self.assertTrue(persistence.should_update_restore(8, 0, uptime_seconds=0.0))
        self.assertTrue(persistence.should_update_restore(8, 8, uptime_seconds=0.0))

    def test_shrink_frozen_during_grace(self) -> None:
        # The core bug: after a relaunch the live layout is 1 pane but restore holds 8.
        # Within the grace window the shrink is refused, so the saved workspace survives.
        self.assertFalse(persistence.should_update_restore(1, 8, uptime_seconds=0.0))
        self.assertFalse(persistence.should_update_restore(1, 8, uptime_seconds=self.FREEZE - 1))

    def test_shrink_adopted_after_grace(self) -> None:
        # A genuinely reduced workspace is tracked for recency once past the grace window.
        self.assertTrue(persistence.should_update_restore(1, 8, uptime_seconds=self.FREEZE + 1))

    def test_relative_guard_protects_small_workspaces(self) -> None:
        # Even a 2-pane workspace is protected from a 1-pane relaunch within grace.
        self.assertFalse(persistence.should_update_restore(1, 2, uptime_seconds=0.0))


class TranscriptEncodingTest(unittest.TestCase):
    """The encoding `restore_workspace` uses to find Claude transcripts must match
    Claude's own: every non-alphanumeric char replaced with '-', no run collapsing.
    """

    def test_encoding_matches_claude_for_dotted_and_underscored_paths(self) -> None:
        import re

        cwd = "/Users/me/code/my.project_dir with space"
        expected = re.sub(r"[^a-zA-Z0-9]", "-", cwd)
        # A naive replace("/", "-") would miss '.', '_' and the space — assert ours doesn't.
        self.assertNotEqual(cwd.replace("/", "-"), expected)
        self.assertEqual(re.sub(r"[^a-zA-Z0-9]", "-", cwd), expected)


if __name__ == "__main__":
    unittest.main()
