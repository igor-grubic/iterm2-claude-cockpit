"""Unit tests for the pane progress (working-spinner) helpers (no iTerm2 runtime needed).

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from iterm2_claude_cockpit.server import progress

# A trigger the user set up themselves, which merging must leave alone.
_USER_TRIGGER = {"regex": "ERROR", "action": "iTermBellTrigger", "parameter": "", "matchType": 0}


class MergeTriggersTest(unittest.TestCase):
    def test_adds_both_triggers_and_keeps_user_triggers(self) -> None:
        merged = progress.merge_triggers([_USER_TRIGGER])
        assert merged is not None
        self.assertEqual(merged[0], _USER_TRIGGER)
        self.assertEqual(merged[1:], list(progress.TRIGGERS))

    def test_profile_without_triggers(self) -> None:
        self.assertEqual(progress.merge_triggers(None), list(progress.TRIGGERS))

    def test_noop_when_present_as_iterm2_returns_them(self) -> None:
        # iTerm2 echoes the triggers back with extra keys; a user may also have disabled one.
        echoed = [{**t, "contentregex": ""} for t in progress.TRIGGERS]
        echoed[1]["disabled"] = True
        self.assertIsNone(progress.merge_triggers([_USER_TRIGGER, *echoed]))

    def test_adds_only_the_missing_one(self) -> None:
        self.assertEqual(progress.merge_triggers([progress.TRIGGERS[0]]), list(progress.TRIGGERS))


class IsBusyTest(unittest.TestCase):
    def test_agent_with_progress_bar_is_busy(self) -> None:
        self.assertTrue(progress.is_busy("true", "copilot"))
        self.assertTrue(progress.is_busy("true", "node"))

    def test_withdrawn_or_unknown_progress_is_idle(self) -> None:
        self.assertFalse(progress.is_busy("false", "copilot"))
        self.assertFalse(progress.is_busy(None, "copilot"))

    def test_stale_flag_on_a_shell_prompt_is_idle(self) -> None:
        self.assertFalse(progress.is_busy("true", "zsh"))
        self.assertFalse(progress.is_busy("true", "-zsh"))


class VersionTest(unittest.TestCase):
    def test_supports_triggers(self) -> None:
        for version, expected in [
            ("3.7.6", True),
            ("3.7.0beta2", True),
            ("3.10", True),
            ("3.6.6", False),
            ("3.5", False),
            ("", False),
        ]:
            with self.subTest(version=version):
                self.assertIs(progress.supports_triggers(version), expected)

    def test_app_version_reads_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as bundle:
            contents = Path(bundle) / "Contents"
            contents.mkdir()
            with (contents / "Info.plist").open("wb") as f:
                plistlib.dump({"CFBundleShortVersionString": "3.7.6"}, f)
            self.assertEqual(progress.app_version(bundle), "3.7.6")

    def test_app_version_missing_bundle(self) -> None:
        self.assertEqual(progress.app_version("/nonexistent/iTerm.app"), "")


if __name__ == "__main__":
    unittest.main()
