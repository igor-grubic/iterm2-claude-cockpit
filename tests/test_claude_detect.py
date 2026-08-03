"""Unit tests for Claude-pane detection parsing (no iTerm2 runtime needed).

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from iterm2_claude_cockpit.server import claude_detect

# Representative `ps -Ao tty=,comm=` output: a bare `claude` in PATH, a
# path-based install, a claude-code binary, a plain node, a shell, and two
# processes with no controlling terminal (`??`).
_PS_SAMPLE = """\
ttys012  claude
ttys007  /Users/igor/.claude/local/claude
ttys009  claude-code
ttys012  node
ttys003  -zsh
??       /usr/bin/python3
??       claude
"""


class ParseClaudeTtysTest(unittest.TestCase):
    def test_matches_bare_path_and_claude_code(self) -> None:
        self.assertEqual(
            claude_detect.parse_claude_ttys(_PS_SAMPLE),
            {"ttys012", "ttys007", "ttys009"},
        )

    def test_ignores_processes_without_a_tty(self) -> None:
        # A `claude` on `??` (no controlling terminal) must not add anything.
        self.assertEqual(claude_detect.parse_claude_ttys("??       claude\n"), set())

    def test_no_substring_false_positive(self) -> None:
        # A path merely containing "claude" is not a claude process.
        out = "ttys004  node\nttys004  /Users/igor/code/iterm2-claude-cockpit/run.sh\n"
        self.assertEqual(claude_detect.parse_claude_ttys(out), set())

    def test_empty_and_blank(self) -> None:
        self.assertEqual(claude_detect.parse_claude_ttys(""), set())
        self.assertEqual(claude_detect.parse_claude_ttys("\n  \n"), set())


class TtyBaseTest(unittest.TestCase):
    def test_strips_dev_prefix(self) -> None:
        self.assertEqual(claude_detect.tty_base("/dev/ttys012"), "ttys012")

    def test_bare_name_and_empty(self) -> None:
        self.assertEqual(claude_detect.tty_base("ttys012"), "ttys012")
        self.assertEqual(claude_detect.tty_base(""), "")


if __name__ == "__main__":
    unittest.main()
