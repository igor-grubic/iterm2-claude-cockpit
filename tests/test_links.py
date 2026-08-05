"""Unit tests for config-driven link providers (no iTerm2 runtime needed).

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from iterm2_claude_cockpit.server import links


class ExtractValueTest(unittest.TestCase):
    def test_json_key(self) -> None:
        text = json.dumps({"pr_url": "https://gh/pr/1", "n": 3})
        self.assertEqual(links.extract_value(text, {"json": "pr_url"}), "https://gh/pr/1")

    def test_json_dotted_path(self) -> None:
        text = json.dumps({"links": {"jira": "PROJ-9"}})
        self.assertEqual(links.extract_value(text, {"json": "links.jira"}), "PROJ-9")

    def test_json_number_becomes_string(self) -> None:
        self.assertEqual(links.extract_value(json.dumps({"pr": 42}), {"json": "pr"}), "42")

    def test_json_bool_is_not_a_value(self) -> None:
        # A boolean is not a usable link value (avoid "True"/"False" chips).
        self.assertIsNone(links.extract_value(json.dumps({"pr": True}), {"json": "pr"}))

    def test_json_missing_key_and_bad_json(self) -> None:
        self.assertIsNone(links.extract_value(json.dumps({"a": 1}), {"json": "pr_url"}))
        self.assertIsNone(links.extract_value("not json", {"json": "pr_url"}))

    def test_json_empty_string_is_none(self) -> None:
        self.assertIsNone(links.extract_value(json.dumps({"pr_url": "  "}), {"json": "pr_url"}))

    def test_regex_capture(self) -> None:
        text = "## Links\n- PR: https://gh/pr/7\n- Jira: PROJ-1\n"
        self.assertEqual(links.extract_value(text, {"regex": r"- PR:\s*(\S+)"}), "https://gh/pr/7")

    def test_regex_no_match_and_bad_pattern(self) -> None:
        self.assertIsNone(links.extract_value("nothing here", {"regex": r"- PR:\s*(\S+)"}))
        # An invalid pattern logs a warning (captured here) and yields no value.
        with self.assertLogs(links.log, level="WARNING"):
            self.assertIsNone(links.extract_value("x", {"regex": r"("}))

    def test_unknown_extract_spec(self) -> None:
        self.assertIsNone(links.extract_value("x", {}))
        self.assertIsNone(links.extract_value("x", {"nope": "y"}))


class BuildHrefTest(unittest.TestCase):
    def test_passthrough_url(self) -> None:
        self.assertEqual(links.build_href("{value}", "https://x/1"), "https://x/1")

    def test_id_into_template(self) -> None:
        self.assertEqual(
            links.build_href("https://jira/browse/{value}", "PROJ-3"),
            "https://jira/browse/PROJ-3",
        )

    def test_empty_template_defaults_to_value(self) -> None:
        self.assertEqual(links.build_href("", "https://x/1"), "https://x/1")


class LinkFromTextTest(unittest.TestCase):
    def test_builds_link(self) -> None:
        provider = {"id": "pr", "label": "PR", "color": "#8ab4f8", "extract": {"json": "pr_url"}, "href": "{value}"}
        link = links.link_from_text(provider, json.dumps({"pr_url": "https://gh/pr/1"}))
        self.assertEqual(link, {"id": "pr", "label": "PR", "href": "https://gh/pr/1", "color": "#8ab4f8"})

    def test_label_defaults_to_id(self) -> None:
        provider = {"id": "pr", "extract": {"json": "pr_url"}, "href": "{value}"}
        link = links.link_from_text(provider, json.dumps({"pr_url": "https://gh/pr/1"}))
        assert link is not None
        self.assertEqual(link["label"], "pr")

    def test_missing_value_yields_none(self) -> None:
        provider = {"id": "pr", "extract": {"json": "pr_url"}, "href": "{value}"}
        self.assertIsNone(links.link_from_text(provider, json.dumps({"other": 1})))


class FindStatusFileTest(unittest.TestCase):
    def test_found_in_own_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text("{}", encoding="utf-8")
            self.assertEqual(links.find_status_file(str(root), ".cockpit.json"), (root / ".cockpit.json").resolve())

    def test_does_not_walk_up_into_ancestors(self) -> None:
        # A file in an ancestor must NOT be picked up from a subfolder — the lookup is
        # scoped to the pane's own folder, so unrelated panes never inherit a distant file.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text("{}", encoding="utf-8")
            deep = root / "repo" / "src" / "pkg"
            deep.mkdir(parents=True)
            self.assertIsNone(links.find_status_file(str(deep), ".cockpit.json"))

    def test_not_found_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(links.find_status_file(str(Path(d) / "sub"), ".cockpit.json"))

    def test_empty_inputs(self) -> None:
        self.assertIsNone(links.find_status_file("", ".cockpit.json"))
        self.assertIsNone(links.find_status_file("/tmp", ""))


class ResolveTabLinksTest(unittest.TestCase):
    def setUp(self) -> None:
        links._FILE_CACHE.clear()

    def test_shared_workspace_yields_one_chip_per_provider(self) -> None:
        providers = [
            {"id": "pr", "label": "PR", "file": ".cockpit.json", "extract": {"json": "pr_url"}, "href": "{value}"},
            {
                "id": "jira",
                "label": "JIRA",
                "file": ".cockpit.json",
                "extract": {"json": "jira_url"},
                "href": "{value}",
            },
        ]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text(
                json.dumps({"pr_url": "https://gh/pr/1", "jira_url": "https://jira/PROJ-1"}),
                encoding="utf-8",
            )
            sub = root / "repo"
            sub.mkdir()
            # Group has a pane at the workspace root (where the file is) and one in a
            # subfolder; the root pane lights the chips (the subfolder pane alone would not),
            # and the shared file yields one chip per provider — not one per pane.
            out = links.resolve_tab_links([str(root), str(sub)], providers)
            self.assertEqual([link["id"] for link in out], ["pr", "jira"])
            self.assertEqual(out[0]["href"], "https://gh/pr/1")

    def test_subfolder_only_pane_yields_no_chips(self) -> None:
        # If a group's only pane sits below the status file, no chips (no walk-up).
        providers = [{"id": "pr", "file": ".cockpit.json", "extract": {"json": "pr_url"}, "href": "{value}"}]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text(json.dumps({"pr_url": "https://gh/pr/1"}), encoding="utf-8")
            sub = root / "repo"
            sub.mkdir()
            self.assertEqual(links.resolve_tab_links([str(sub)], providers), [])

    def test_absent_value_omits_that_chip(self) -> None:
        providers = [
            {"id": "pr", "label": "PR", "file": ".cockpit.json", "extract": {"json": "pr_url"}, "href": "{value}"},
            {
                "id": "jira",
                "label": "JIRA",
                "file": ".cockpit.json",
                "extract": {"json": "jira_url"},
                "href": "{value}",
            },
        ]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".cockpit.json").write_text(json.dumps({"jira_url": "https://jira/PROJ-1"}), encoding="utf-8")
            out = links.resolve_tab_links([str(root)], providers)
            self.assertEqual([link["id"] for link in out], ["jira"])

    def test_no_status_file_yields_empty(self) -> None:
        providers = [{"id": "pr", "file": ".cockpit.json", "extract": {"json": "pr_url"}, "href": "{value}"}]
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(links.resolve_tab_links([str(Path(d))], providers), [])


class LoadProvidersTest(unittest.TestCase):
    def setUp(self) -> None:
        links._PROVIDERS_CACHE = None

    def tearDown(self) -> None:
        links._PROVIDERS_CACHE = None

    def test_missing_file_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            got = links.load_providers(Path(d) / "nope.json")
            self.assertEqual(got, links.DEFAULT_PROVIDERS)

    def test_reads_and_filters_invalid_providers(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "links.json"
            cfg.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "pr", "file": ".cockpit.json", "extract": {"json": "pr_url"}},
                            {"label": "no id"},  # dropped: no id
                            {"id": "x", "file": "f"},  # dropped: no extract
                        ]
                    }
                ),
                encoding="utf-8",
            )
            got = links.load_providers(cfg)
            self.assertEqual([p["id"] for p in got], ["pr"])

    def test_malformed_json_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "links.json"
            cfg.write_text("{ not json", encoding="utf-8")
            # The parse error is logged (captured here so it stays out of test output).
            with self.assertLogs(links.log, level="ERROR"):
                self.assertEqual(links.load_providers(cfg), links.DEFAULT_PROVIDERS)


if __name__ == "__main__":
    unittest.main()
