"""Unit tests for the persistence layer's pure logic (no iTerm2 runtime needed).

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from iterm2_claude_cockpit.server import persistence


def _state(*pane_counts: int) -> dict:
    """Build a minimal state dict: one window, one tab per given pane count."""
    return {"windows": [{"tabs": [{"name": None, "panes": [{"cwd": "/x"} for _ in range(n)]}]} for n in pane_counts]}


def _snapshot(*tab_ids: str) -> dict:
    """Build a minimal live-snapshot dict (tree.build_tree's shape): one window, one tab per id."""
    return {"windows": [{"tabs": [{"id": tid, "panes": [{"id": f"s-{tid}", "cwd": "/x"}]} for tid in tab_ids]}]}


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
        self.assertEqual(data["tab_sleeping"], {})
        self.assertFalse(data["windows"][0]["tabs"][0]["sleeping"])

    def test_prunes_dead_tab_ids(self) -> None:
        # t1 is live, t2 no longer exists in the snapshot — must be dropped from all metadata.
        data = persistence.build_state(
            _snapshot("t1"),
            tab_names={"t1": "keep", "t2": "gone"},
            tab_colors={"t1": 2, "t2": 5},
            tab_collapsed={"t1": True, "t2": False},
            tab_sleeping={"t1": True, "t2": True},
        )
        self.assertEqual(data["tab_names"], {"t1": "keep"})
        self.assertEqual(data["tab_colors"], {"t1": 2})
        self.assertEqual(data["tab_collapsed"], {"t1": True})
        self.assertEqual(data["tab_sleeping"], {"t1": True})

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

    def test_sleep_preserves_original_color_and_forces_collapsed(self) -> None:
        for color in (None, 0, 5):
            with self.subTest(color=color):
                data = persistence.build_state(
                    _snapshot("t1"),
                    tab_names={},
                    tab_colors={} if color is None else {"t1": color},
                    tab_sleeping={"t1": True},
                )
                tab = data["windows"][0]["tabs"][0]
                self.assertEqual(tab["color"], color)
                self.assertTrue(tab["sleeping"])
                self.assertTrue(tab["collapsed"])
                self.assertEqual(data["tab_sleeping"], {"t1": True})
                self.assertEqual(tab["panes"], [{"id": "s-t1", "cwd": "/x"}])

    def test_awake_tab_keeps_independent_collapsed_state(self) -> None:
        data = persistence.build_state(
            _snapshot("t1", "t2"),
            tab_names={},
            tab_collapsed={"t1": True},
            tab_sleeping={"t1": False},
        )
        first, second = data["windows"][0]["tabs"]
        self.assertFalse(first["sleeping"])
        self.assertTrue(first["collapsed"])
        self.assertFalse(second["sleeping"])
        self.assertFalse(second["collapsed"])

    def test_sleep_metadata_round_trips_through_disk(self) -> None:
        data = persistence.build_state(
            _snapshot("t1"),
            tab_names={"t1": "Paused work"},
            tab_colors={"t1": 0},
            tab_collapsed={"t1": True},
            tab_sleeping={"t1": True},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(persistence, "STATE_DIR", root),
                patch.object(persistence, "STATE_PATH", root / "state.json"),
                patch.object(persistence, "RESTORE_PATH", root / "restore.json"),
            ):
                persistence.save_state(data)
                persistence.save_restore(data)
                for loaded in (persistence.load_state(), persistence.load_restore()):
                    self.assertIsNotNone(loaded)
                    self.assertEqual(loaded["tab_sleeping"], {"t1": True})
                    self.assertEqual(loaded["tab_colors"], {"t1": 0})
                    self.assertEqual(loaded["windows"], data["windows"])

    def test_embeds_name_and_session_identity(self) -> None:
        data = persistence.build_state(_snapshot("t1"), {"t1": "Original project"})
        tab = data["windows"][0]["tabs"][0]
        self.assertEqual(tab["id"], "t1")
        self.assertEqual(tab["name"], "Original project")
        self.assertEqual(persistence.pane_ids(data), {"s-t1"})


class LiveTabMetadataTest(unittest.TestCase):
    def test_metadata_follows_surviving_sessions_even_if_tab_is_renumbered(self) -> None:
        data = persistence.build_state(_snapshot("old"), {"old": "Project"}, {"old": 3}, {"old": True}, {"old": True})
        self.assertEqual(
            persistence.live_tab_metadata(data, {"new": {"s-old"}}),
            {
                "tab_names": {"new": "Project"},
                "tab_colors": {"new": 3},
                "tab_collapsed": {"new": True},
                "tab_sleeping": {"new": True},
            },
        )

    def test_reused_tab_number_does_not_inherit_another_sessions_title(self) -> None:
        data = persistence.build_state(_snapshot("t1"), {"t1": "Old project"})
        self.assertEqual(persistence.live_tab_metadata(data, {"t1": {"different-session"}})["tab_names"], {})

    def test_legacy_tab_metadata_remains_readable(self) -> None:
        data = {"tab_names": {"t1": "Legacy", "gone": "Gone"}, "windows": []}
        self.assertEqual(persistence.live_tab_metadata(data, {"t1": {"s1"}})["tab_names"], {"t1": "Legacy"})

    def test_no_saved_data(self) -> None:
        self.assertEqual(persistence.live_tab_metadata(None, {})["tab_names"], {})


class RestoreBackupsTest(unittest.TestCase):
    def test_backup_keeps_source_timestamp_and_survives_restore_overwrite(self) -> None:
        with TemporaryDirectory() as root:
            with (
                patch.object(persistence, "RESTORE_BACKUP_DIR", Path(root) / "backups"),
                patch.object(persistence, "RESTORE_PATH", Path(root) / "restore.json"),
            ):
                original = {**_state(3), "saved_at": 123}
                persistence.backup_restore(original)
                persistence.save_restore(_state(1))
                paths = list((Path(root) / "backups").glob("restore-*.json"))
                self.assertEqual(len(paths), 1)
                backup = persistence._read_json(paths[0])
                self.assertEqual(backup["source_saved_at"], 123)
                self.assertEqual(persistence.count_panes(backup), 3)
                self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)

    def test_retains_only_bounded_recent_backups(self) -> None:
        with TemporaryDirectory() as root:
            with (
                patch.object(persistence, "RESTORE_BACKUP_DIR", Path(root)),
                patch.object(persistence, "RESTORE_BACKUP_LIMIT", 2),
                patch.object(persistence.time, "time_ns", side_effect=[1, 2, 3]),
            ):
                for _ in range(3):
                    persistence.backup_restore(_state(1))
                self.assertEqual(
                    sorted(path.name for path in Path(root).iterdir()), ["restore-2.json", "restore-3.json"]
                )

    def test_empty_snapshot_creates_no_backup(self) -> None:
        with TemporaryDirectory() as root:
            with patch.object(persistence, "RESTORE_BACKUP_DIR", Path(root)):
                persistence.backup_restore(None)
                persistence.backup_restore({"windows": []})
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_loss_after_startup_backs_up_the_latest_good_layout(self) -> None:
        with TemporaryDirectory() as root:
            with (
                patch.object(persistence, "RESTORE_BACKUP_DIR", Path(root) / "backups"),
                patch.object(persistence, "RESTORE_PATH", Path(root) / "restore.json"),
            ):
                original = persistence.build_state(_snapshot("t1"), {"t1": "Important project"})
                persistence.save_restore(original)
                degraded = persistence.build_state(_snapshot("t1"), {})
                persistence.save_restore(degraded)
                paths = list((Path(root) / "backups").glob("restore-*.json"))
                self.assertEqual(len(paths), 1)
                self.assertEqual(persistence._read_json(paths[0])["windows"], original["windows"])
                self.assertEqual(persistence.load_restore()["windows"], degraded["windows"])


class AtomicWriteTest(unittest.TestCase):
    def test_concurrent_writers_use_distinct_temporary_files(self) -> None:
        barrier = Barrier(2)
        replace = persistence.os.replace

        def synchronized_replace(source, target):
            barrier.wait(timeout=5)
            replace(source, target)

        with TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            with (
                patch.object(persistence.os, "replace", side_effect=synchronized_replace),
                ThreadPoolExecutor(2) as pool,
            ):
                futures = [pool.submit(persistence._write_json_atomic, path, {"value": value}) for value in (1, 2)]
                for future in futures:
                    future.result(timeout=5)
            self.assertIn(persistence._read_json(path)["value"], (1, 2))
            self.assertEqual(list(Path(root).glob("*.tmp.*")), [])


class NormalizeThemeTest(unittest.TestCase):
    def test_valid_passthrough(self) -> None:
        self.assertEqual(persistence.normalize_theme("1a"), "1a")
        self.assertEqual(persistence.normalize_theme("2a"), "2a")

    def test_invalid_or_missing_defaults_to_classic(self) -> None:
        self.assertEqual(persistence.normalize_theme(None), "1a")
        self.assertEqual(persistence.normalize_theme("bogus"), "1a")
        self.assertEqual(persistence.normalize_theme(""), "1a")


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

    def test_same_size_and_larger_new_sessions_are_frozen(self) -> None:
        saved = persistence.build_state(_snapshot("old"), {"old": "Project"})
        for ids in (("new",), ("new", "another")):
            with self.subTest(ids=ids):
                live = persistence.build_state(_snapshot(*ids), {})
                self.assertFalse(
                    persistence.should_update_restore(len(ids), 1, 0, live_state=live, restore_state=saved)
                )

    def test_surviving_sessions_can_update_during_startup(self) -> None:
        data = persistence.build_state(_snapshot("same"), {"same": "Project"})
        self.assertTrue(persistence.should_update_restore(1, 1, 0, live_state=data, restore_state=data))

    def test_legacy_metadata_loss_is_frozen_even_with_equal_counts(self) -> None:
        saved = _state(2)
        saved["windows"][0]["tabs"][0]["name"] = "Project"
        for lose_name, lose_cwd in ((True, False), (False, True)):
            with self.subTest(lose_name=lose_name, lose_cwd=lose_cwd):
                live = _state(2)
                live["windows"][0]["tabs"][0]["name"] = None if lose_name else "Project"
                if lose_cwd:
                    live["windows"][0]["tabs"][0]["panes"][0]["cwd"] = ""
                self.assertFalse(persistence.should_update_restore(2, 2, 0, live_state=live, restore_state=saved))

    def test_new_workspace_can_be_adopted_after_grace(self) -> None:
        saved = persistence.build_state(_snapshot("old"), {"old": "Project"})
        live = persistence.build_state(_snapshot("new"), {})
        self.assertTrue(persistence.should_update_restore(1, 1, self.FREEZE + 1, live_state=live, restore_state=saved))


if __name__ == "__main__":
    unittest.main()
