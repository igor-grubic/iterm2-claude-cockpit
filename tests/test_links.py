"""Unit tests for auto-discovered link chips (no iTerm2 runtime needed).

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from iterm2_claude_cockpit.server import links


class LinksFromTextTest(unittest.TestCase):
    def test_scalar_url_becomes_one_chip(self) -> None:
        text = json.dumps({"jira_url": "https://jira/PROJ-1"})
        (link,) = links.links_from_text(text)
        self.assertEqual(link["id"], "jira_url")
        self.assertEqual(link["label"], "jira_url")
        self.assertEqual(link["urls"], ["https://jira/PROJ-1"])
        self.assertTrue(link["color"].startswith("#"))

    def test_array_of_urls_becomes_one_chip_with_all(self) -> None:
        text = json.dumps({"pr_urls": ["https://gh/a/pull/1", "https://gh/b/pull/2"]})
        (link,) = links.links_from_text(text)
        self.assertEqual(link["label"], "pr_urls")
        self.assertEqual(link["urls"], ["https://gh/a/pull/1", "https://gh/b/pull/2"])

    def test_non_url_property_is_skipped(self) -> None:
        # A plain branch string is not a URL, so it produces no chip.
        text = json.dumps({"branch": "PROJ-1-do-the-thing", "pr_url": "https://gh/pr/1"})
        self.assertEqual([link["id"] for link in links.links_from_text(text)], ["pr_url"])

    def test_array_filters_non_url_entries(self) -> None:
        text = json.dumps({"prs": ["https://gh/pr/1", "not-a-url", 42, "http://x/y"]})
        (link,) = links.links_from_text(text)
        self.assertEqual(link["urls"], ["https://gh/pr/1", "http://x/y"])

    def test_empty_or_urlless_array_skipped(self) -> None:
        self.assertEqual(links.links_from_text(json.dumps({"a": [], "b": ["nope"]})), [])

    def test_property_order_is_preserved(self) -> None:
        text = json.dumps({"jira_url": "https://j/1", "pr_url": "https://p/1", "pr_urls": ["https://p/1"]})
        self.assertEqual([link["id"] for link in links.links_from_text(text)], ["jira_url", "pr_url", "pr_urls"])

    def test_whitespace_and_non_http_are_not_urls(self) -> None:
        text = json.dumps({"a": "   ", "b": "ftp://x/y", "c": "www.example.com"})
        self.assertEqual(links.links_from_text(text), [])

    def test_bad_or_non_object_json(self) -> None:
        self.assertEqual(links.links_from_text("not json"), [])
        self.assertEqual(links.links_from_text(json.dumps(["https://x/y"])), [])


class ColorForTest(unittest.TestCase):
    def test_deterministic_and_in_palette(self) -> None:
        self.assertEqual(links._color_for("pr_url"), links._color_for("pr_url"))
        self.assertIn(links._color_for("jira_url"), links._CHIP_PALETTE)


class FindStatusFileTest(unittest.TestCase):
    def test_found_in_own_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text("{}", encoding="utf-8")
            self.assertEqual(links.find_status_file(str(root), ".cockpit.json"), (root / ".cockpit.json").resolve())

    def test_does_not_walk_up_into_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text("{}", encoding="utf-8")
            deep = root / "repo" / "src"
            deep.mkdir(parents=True)
            self.assertIsNone(links.find_status_file(str(deep), ".cockpit.json"))

    def test_not_found_and_empty_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(links.find_status_file(str(Path(d) / "sub"), ".cockpit.json"))
        self.assertIsNone(links.find_status_file("", ".cockpit.json"))
        self.assertIsNone(links.find_status_file("/tmp", ""))


class ResolveTabLinksTest(unittest.TestCase):
    def setUp(self) -> None:
        links._FILE_CACHE.clear()

    def test_root_pane_lights_chips_for_the_group(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text(
                json.dumps({"pr_url": "https://gh/pr/1", "jira_url": "https://jira/PROJ-1"}),
                encoding="utf-8",
            )
            sub = root / "repo"
            sub.mkdir()
            # Group has a pane at the workspace root (where the file is) and one in a
            # subfolder; the root pane lights the chips (subfolder pane alone would not).
            out = links.resolve_tab_links([str(root), str(sub)], ".cockpit.json")
            self.assertEqual([link["id"] for link in out], ["pr_url", "jira_url"])

    def test_subfolder_only_pane_yields_no_chips(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text(json.dumps({"pr_url": "https://gh/pr/1"}), encoding="utf-8")
            sub = root / "repo"
            sub.mkdir()
            self.assertEqual(links.resolve_tab_links([str(sub)], ".cockpit.json"), [])

    def test_no_file_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(links.resolve_tab_links([str(Path(d))], ".cockpit.json"), [])

    def test_urlless_file_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text(json.dumps({"branch": "x"}), encoding="utf-8")
            self.assertEqual(links.resolve_tab_links([str(root)], ".cockpit.json"), [])


class StatusFilenameTest(unittest.TestCase):
    def setUp(self) -> None:
        links._CONFIG_CACHE = None

    def tearDown(self) -> None:
        links._CONFIG_CACHE = None

    def test_missing_config_uses_default(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(links.status_filename(Path(d) / "nope.json"), links.DEFAULT_STATUS_FILE)

    def test_config_overrides_filename(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "links.json"
            cfg.write_text(json.dumps({"file": "status.json"}), encoding="utf-8")
            self.assertEqual(links.status_filename(cfg), "status.json")

    def test_config_without_file_key_uses_default(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "links.json"
            cfg.write_text(json.dumps({"other": 1}), encoding="utf-8")
            self.assertEqual(links.status_filename(cfg), links.DEFAULT_STATUS_FILE)

    def test_malformed_config_falls_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "links.json"
            cfg.write_text("{ not json", encoding="utf-8")
            with self.assertLogs(links.log, level="ERROR"):
                self.assertEqual(links.status_filename(cfg), links.DEFAULT_STATUS_FILE)


if __name__ == "__main__":
    unittest.main()
