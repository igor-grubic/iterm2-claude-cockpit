"""Directory recovery and validation without an iTerm2 runtime."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from iterm2_claude_cockpit.server import directories


class ProcessCwdsTest(unittest.TestCase):
    def test_parses_multiple_processes_and_spaces(self) -> None:
        output = b"p123\nn/Users/test/a project\np456\nn/tmp/other\n"
        self.assertEqual(directories.parse_process_cwds(output), {123: "/Users/test/a project", 456: "/tmp/other"})

    def test_decodes_escaped_utf8_directory_names(self) -> None:
        output = b"p123\n" + rb"n/tmp/t\xc3\xa9st" + b"\n"
        self.assertEqual(directories.parse_process_cwds(output), {123: "/tmp/t\u00e9st"})

    def test_decodes_names_once_without_corrupting_raw_utf8_or_literal_escapes(self) -> None:
        cases = [
            (rb"/tmp/\xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e", "/tmp/\u65e5\u672c\u8a9e"),
            ("/tmp/t\u00e9st".encode(), "/tmp/t\u00e9st"),
            ("/tmp/\u65e5\u672c\u8a9e".encode(), "/tmp/\u65e5\u672c\u8a9e"),
            (rb"/tmp/\\xc3\\xa9", r"/tmp/\xc3\xa9"),
            (rb"/tmp/\\n", r"/tmp/\n"),
            (rb"/tmp/trailing\\", "/tmp/trailing\\"),
            (rb"/tmp/line\nbreak\t\b\f\r", "/tmp/line\nbreak\t\b\f\r"),
            (rb"/tmp/delete-\x7f", "/tmp/delete-\x7f"),
            ("/tmp/t\u00e9st".encode() + rb"\\literal", "/tmp/t\u00e9st\\literal"),
            (rb"/tmp/t\xc3\xa9st\\literal\\xc3\\xa9", "/tmp/t\u00e9st" + r"\literal\xc3\xa9"),
            (rb"/tmp/caret-^?", "/tmp/caret-^?"),
        ]
        for encoded, expected in cases:
            with self.subTest(encoded=encoded):
                self.assertEqual(directories.parse_process_cwds(b"p123\nn" + encoded + b"\n"), {123: expected})

    def test_rejects_invalid_or_ambiguous_names_without_losing_other_pids(self) -> None:
        names = [rb"/tmp/\xZZ", rb"/tmp/\x1", rb"/tmp/\q", b"/tmp/trailing\\", rb"/tmp/\x00", b"/tmp/control-^A"]
        for name in names:
            with self.subTest(name=name), self.assertLogs(directories.log, level="WARNING"):
                output = b"p123\nn" + name + b"\np456\nn/tmp/other\n"
                self.assertEqual(directories.parse_process_cwds(output), {456: "/tmp/other"})

    def test_ignores_malformed_or_nonabsolute_records(self) -> None:
        output = b"n/orphan\npwrong\nn/unknown\np123\nnrelative\n"
        self.assertEqual(directories.parse_process_cwds(output), {})

    @patch.object(directories.subprocess, "run")
    def test_no_processes_needs_no_command(self, run) -> None:
        self.assertEqual(directories.process_cwds(set()), {})
        run.assert_not_called()

    @patch.object(directories.subprocess, "run")
    def test_batches_pids_and_keeps_partial_results(self, run) -> None:
        with TemporaryDirectory() as root:
            run.return_value = subprocess.CompletedProcess([], 1, b"p123\nn" + os.fsencode(root) + b"\n", b"")
            self.assertEqual(directories.process_cwds({456, 123}), {123: root})
        self.assertEqual(run.call_args.args[0], ["/usr/sbin/lsof", "-a", "-d", "cwd", "-p", "123,456", "-Fpn"])
        self.assertEqual(run.call_args.kwargs["timeout"], 2.0)
        self.assertFalse(run.call_args.kwargs.get("text", False))

    @patch.object(directories.subprocess, "run")
    def test_resolves_escaped_unicode_to_an_existing_directory(self, run) -> None:
        with TemporaryDirectory() as root:
            path = Path(root) / "t\u00e9st"
            path.mkdir()
            output = b"p123\nn" + os.fsencode(root) + rb"/t\xc3\xa9st" + b"\n"
            run.return_value = subprocess.CompletedProcess([], 0, output, b"")
            self.assertEqual(directories.process_cwds({123}), {123: str(path)})

    @patch.object(directories.subprocess, "run")
    def test_rejects_missing_directories_and_files_but_keeps_valid_results(self, run) -> None:
        with TemporaryDirectory() as root:
            file = Path(root) / "file"
            file.touch()
            output = (
                b"p123\nn"
                + os.fsencode(Path(root) / "gone")
                + b"\np456\nn"
                + os.fsencode(root)
                + b"\np789\nn"
                + os.fsencode(file)
                + b"\n"
            )
            run.return_value = subprocess.CompletedProcess([], 0, output, b"")
            with self.assertLogs(directories.log, level="WARNING"):
                self.assertEqual(directories.process_cwds({123, 456, 789}), {456: root})

    @patch.object(directories.Path, "is_dir", side_effect=PermissionError("permission denied"))
    @patch.object(directories.subprocess, "run")
    def test_directory_validation_errors_are_logged(self, run, is_dir) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, b"p123\nn/tmp/project\n", b"")
        with self.assertLogs(directories.log, level="WARNING"):
            self.assertEqual(directories.process_cwds({123}), {})

    @patch.object(directories.subprocess, "run", side_effect=subprocess.TimeoutExpired("lsof", 2))
    def test_timeout_is_logged(self, run) -> None:
        with self.assertLogs(directories.log, level="WARNING"):
            self.assertEqual(directories.process_cwds({123}), {})

    @patch.object(directories.subprocess, "run")
    def test_command_failure_is_logged(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 2, b"", b"permission denied: \xff")
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
