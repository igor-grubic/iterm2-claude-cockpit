"""Unit tests for the persistence layer's pure logic (no iTerm2 runtime needed).

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from iterm2_claude_cockpit.server import persistence


def _state(*pane_counts: int) -> dict:
    """Build a minimal state dict: one window, one tab per given pane count."""
    return {"windows": [{"tabs": [{"name": None, "panes": [{"cwd": "/x"} for _ in range(n)]}]} for n in pane_counts]}


def _snapshot(*tab_ids: str) -> dict:
    """Build a minimal live-snapshot dict (tree.build_tree's shape): one window, one tab per id."""
    return {"windows": [{"tabs": [{"id": tid, "panes": [{"cwd": "/x"}]} for tid in tab_ids]}]}


class CountPanesTest(unittest.TestCase):
    def test_none_and_empty(self) -> None:
        self.assertEqual(persistence.count_panes(None), 0)
        self.assertEqual(persistence.count_panes({}), 0)
        self.assertEqual(persistence.count_panes({"windows": []}), 0)

    def test_sums_across_windows_and_tabs(self) -> None:
        # two windows: one with 3 panes, one with 2 panes
        self.assertEqual(persistence.count_panes(_state(3, 2)), 5)


class BuildStateTest(unittest.TestCase):
    def test_defaults_to_empty_dicts(self) -> None:
        data = persistence.build_state(_snapshot("t1"), {"t1": "custom"})
        self.assertEqual(data["tab_colors"], {})
        self.assertEqual(data["tab_collapsed"], {})

    def test_prunes_dead_tab_ids(self) -> None:
        # t1 is live, t2 no longer exists in the snapshot — must be dropped from all three.
        data = persistence.build_state(
            _snapshot("t1"),
            tab_names={"t1": "keep", "t2": "gone"},
            tab_colors={"t1": 2, "t2": 5},
            tab_collapsed={"t1": True, "t2": False},
        )
        self.assertEqual(data["tab_names"], {"t1": "keep"})
        self.assertEqual(data["tab_colors"], {"t1": 2})
        self.assertEqual(data["tab_collapsed"], {"t1": True})

    def test_embeds_color_and_collapsed_per_tab(self) -> None:
        # restore_workspace reads color/collapsed off each tab entry directly (not the
        # top-level dicts, since old tab ids die on a real restart) to re-apply them to
        # the freshly created tab — so they must round-trip through the per-tab dict too.
        data = persistence.build_state(
            _snapshot("t1"),
            tab_names={},
            tab_colors={"t1": 3},
            tab_collapsed={"t1": True},
        )
        tab = data["windows"][0]["tabs"][0]
        self.assertEqual(tab["color"], 3)
        self.assertTrue(tab["collapsed"])


class NormalizeThemeTest(unittest.TestCase):
    def test_valid_passthrough(self) -> None:
        self.assertEqual(persistence.normalize_theme("1a"), "1a")
        self.assertEqual(persistence.normalize_theme("2a"), "2a")

    def test_invalid_or_missing_defaults_to_2a(self) -> None:
        self.assertEqual(persistence.normalize_theme(None), "2a")
        self.assertEqual(persistence.normalize_theme("bogus"), "2a")
        self.assertEqual(persistence.normalize_theme(""), "2a")


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


if __name__ == "__main__":
    unittest.main()
