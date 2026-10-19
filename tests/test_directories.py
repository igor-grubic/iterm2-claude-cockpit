"""Directory recovery and validation without an iTerm2 runtime."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from iterm2_claude_cockpit.server import directories


class ProcessCwdsTest(unittest.TestCase):
    def test_parses_multiple_processes_and_spaces(self) -> None:
        output = "p123\nn/Users/test/a project\np456\nn/tmp/other\n"
        self.assertEqual(directories.parse_process_cwds(output), {123: "/Users/test/a project", 456: "/tmp/other"})

    def test_ignores_malformed_or_nonabsolute_records(self) -> None:
        output = "n/orphan\npwrong\nn/unknown\np123\nnrelative\n"
        self.assertEqual(directories.parse_process_cwds(output), {})

    @patch.object(directories.subprocess, "run")
    def test_no_processes_needs_no_command(self, run) -> None:
        self.assertEqual(directories.process_cwds(set()), {})
        run.assert_not_called()

    @patch.object(directories.subprocess, "run")
    def test_batches_pids_and_keeps_partial_results(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, "p123\nn/tmp/project\n", "")
        self.assertEqual(directories.process_cwds({456, 123}), {123: "/tmp/project"})
        self.assertEqual(run.call_args.args[0], ["/usr/sbin/lsof", "-a", "-d", "cwd", "-p", "123,456", "-Fpn"])
        self.assertEqual(run.call_args.kwargs["timeout"], 2.0)

    @patch.object(directories.subprocess, "run", side_effect=subprocess.TimeoutExpired("lsof", 2))
    def test_timeout_is_logged(self, run) -> None:
        with self.assertLogs(directories.log, level="WARNING"):
            self.assertEqual(directories.process_cwds({123}), {})

    @patch.object(directories.subprocess, "run")
    def test_command_failure_is_logged(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 2, "", "permission denied")
        with self.assertLogs(directories.log, level="WARNING"):
            self.assertEqual(directories.process_cwds({123}), {})


class RestoreCwdTest(unittest.TestCase):
    def test_existing_directory_with_shell_metacharacters(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root) / "spaces & 'quotes' $(not-a-command)"
            path.mkdir()
            self.assertEqual(directories.restore_cwd(str(path)), str(path))

    def test_missing_invalid_or_unavailable_directory_is_explicit(self) -> None:
        with TemporaryDirectory() as root:
            file = Path(root) / "file"
            file.touch()
            for value in (None, "", 123, [], "relative", str(file), str(Path(root) / "gone")):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    directories.restore_cwd(value)


if __name__ == "__main__":
    unittest.main()
