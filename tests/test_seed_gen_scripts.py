import unittest
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from seed_gen.catalog import _seed_from_detail, select_reference_tools, _read_server_detail, retry_empty_smithery_tools
from seed_gen.scripts.fetch_smithery_servers import build_seed_records, fetch_all_servers


class SmitherySeedExportTests(unittest.TestCase):
    def test_transient_errors_retry_and_respect_shared_rate_limit(self):
        audit = {}
        responses = [HTTPError("url", 429, "Limited", {"Retry-After": "2"}, None),
                     OSError("temporary network failure"),
                     {"tools": [{"name": "search", "description": "Search"}]}]
        with patch("seed_gen.catalog._get_json", side_effect=responses), patch("seed_gen.catalog.time.sleep") as sleep, patch("seed_gen.catalog._defer_requests") as defer:
            result = _read_server_detail({"qualifiedName": "demo/server"}, audit=audit)
        defer.assert_called_once_with(2.0)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 2])
        self.assertEqual(result["tools"][0]["name"], "search")
        self.assertEqual(audit["events"][1]["error_type"], "OSError")

    def test_long_cooldown_is_recorded_without_immediate_retry(self):
        audit = {}
        with patch("seed_gen.catalog._get_json", side_effect=HTTPError("url", 429, "Limited", {"Retry-After": "120"}, None)) as get, patch("seed_gen.catalog._defer_requests"):
            self.assertIsNone(_read_server_detail({"qualifiedName": "demo/server"}, audit=audit))
        self.assertEqual(get.call_count, 1)
        self.assertEqual(audit["events"][0]["retry_after_seconds"], 120)

    def test_wrong_server_response_is_not_attached_to_seed(self):
        audit = {}
        with patch("seed_gen.catalog._get_json", return_value={"qualifiedName": "someone/else", "tools": [{"name": "wrong", "description": "Wrong"}]}):
            self.assertIsNone(_read_server_detail({"qualifiedName": "demo/server"}, 2, 0, audit))
        self.assertEqual(len(audit["events"]), 2)

    def test_empty_success_responses_retry_until_valid_tools(self):
        audit = {}
        responses = [{"tools": []}, {"tools": [{"name": "bad"}]},
                     {"tools": [{"name": "search", "description": "Search"}]}]
        with patch("seed_gen.catalog._get_json", side_effect=responses) as get, patch("seed_gen.catalog.time.sleep") as sleep:
            detail = _read_server_detail({"qualifiedName": "demo/server"}, audit=audit)
        self.assertEqual(get.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(detail["tools"][0]["name"], "search")
        self.assertEqual([e["status"] for e in audit["events"]], ["empty_tools", "invalid_tools", "tools_found"])

    def test_retry_exhaustion_and_permanent_errors_are_recorded(self):
        audit = {}
        with patch("seed_gen.catalog._get_json", return_value={"tools": []}) as get:
            self.assertEqual(_read_server_detail({"qualifiedName": "a"}, 3, 0, audit)["tools"], [])
        self.assertEqual(get.call_count, 3)
        with patch("seed_gen.catalog._get_json", side_effect=HTTPError("url", 404, "Missing", {}, None)) as get:
            self.assertIsNone(_read_server_detail({"qualifiedName": "a"}, 5, 0, {}))
        self.assertEqual(get.call_count, 1)

    def test_recovery_preserves_nonempty_and_failed_seeds_and_identity(self):
        populated = _seed_from_detail({"qualifiedName": "existing", "description": "Old", "tools": [{"name": "old", "description": "Old"}]}, 1)
        empty = _seed_from_detail({"qualifiedName": "empty", "description": "Keep this"}, 7)
        failed = _seed_from_detail({"qualifiedName": "failed", "description": "Keep failed"}, 9)
        original = [populated, empty, failed]
        def fetch(server, attempts, retry_delay, audit):
            audit.update(name=server["qualifiedName"], events=[{"status": "test"}])
            if server["qualifiedName"] == "failed":
                return None
            return {**server, "description": "New description", "tools": [{"name": "new", "description": "New"}]}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "seeds.json"
            target.write_text(json.dumps(original), encoding="utf-8")
            with patch("seed_gen.catalog._read_server_detail", side_effect=fetch) as get:
                report = retry_empty_smithery_tools(target, workers=1, report_dir=root / "reports")
            actual = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(get.call_count, 2)
            self.assertEqual(actual[0], populated)
            self.assertEqual(actual[2], failed)
            self.assertEqual(actual[1]["environment"]["description"], "Keep this")
            self.assertEqual(actual[1]["global_id"], empty["global_id"])
            self.assertEqual(actual[1]["environment"]["nums"]["function"], 1)
            self.assertEqual(report["recovered"], 1)
            self.assertEqual(report["remaining_empty"], 1)
            self.assertEqual(json.loads(Path(report["backup"]).read_text()), original)

    def test_default_cap_preserves_first_100_tools_and_updates_counts(self):
        tools = [{"name": f"t{i}", "description": f"Tool {i}", "inputSchema": {
            "properties": {"OriginalKey": {"type": "string"}}}} for i in range(105)]
        detail = {"qualifiedName": "demo/server", "description": "Demo", "useCount": 999, "tools": tools}
        original = deepcopy(detail)
        seed = _seed_from_detail(detail, 1)
        self.assertEqual([t["name"] for t in seed["init_ref_tools"]], [f"t{i}" for i in range(100)])
        self.assertEqual(seed["environment"]["nums"]["function"], 100)
        self.assertEqual(seed["environment"]["nums"]["all_func"], 100)
        self.assertEqual(seed["others"]["tool_selection"]["strategy"], "source_order")
        self.assertEqual(seed["others"]["tool_selection"]["removed_count"], 5)
        self.assertIn("OriginalKey", seed["init_ref_tools"][0]["input"])
        self.assertEqual(detail, original)

    def test_tool_usage_ranking_is_stable_and_does_not_mutate_tools(self):
        tools = [{"name": "a", "useCount": 1}, {"name": "b", "useCount": 3},
                 {"name": "c", "useCount": 3}]
        original = deepcopy(tools)
        selected, audit = select_reference_tools(tools, 2)
        self.assertEqual([t["name"] for t in selected], ["b", "c"])
        self.assertIs(selected[0], tools[1])
        self.assertEqual(audit["ranking_field"], "useCount")
        self.assertEqual(tools, original)
        self.assertEqual(select_reference_tools(tools, 3)[0], tools)

    def test_unreliable_usage_falls_back_to_source_order(self):
        for invalid in (None, -1, True, "50", float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                selected, audit = select_reference_tools([
                    {"name": "first", "useCount": invalid},
                    {"name": "second", "useCount": 50},
                ], 1)
                self.assertEqual(selected[0]["name"], "first")
                self.assertEqual(audit["strategy"], "source_order")
        selected, audit = select_reference_tools([
            {"name": "a", "use_count": 0}, {"name": "b", "use_count": 2}], 1)
        self.assertEqual(selected[0]["name"], "b")
        self.assertEqual(audit["ranking_field"], "use_count")

    def test_invalid_tools_are_filtered_before_cap(self):
        seed = _seed_from_detail({"qualifiedName": "demo/server", "description": "Demo", "tools": [
            {"name": "invalid"}, {"name": "valid", "description": "Valid"},
            {"name": "also_valid", "description": "Valid too"}]}, 1, 1)
        self.assertEqual(seed["init_ref_tools"][0]["name"], "valid")
        self.assertEqual(seed["others"]["tool_selection"]["invalid_count"], 1)
        self.assertEqual(seed["others"]["tool_selection"]["eligible_count"], 2)
        for invalid in (0, 101, -1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                select_reference_tools([], invalid)

    def test_catalog_detail_uses_environment_seed_v11(self):
        seed = _seed_from_detail(
            {
                "qualifiedName": "demo/server",
                "description": "A demo Smithery server.",
                "tools": [
                    {
                        "name": "search",
                        "description": "Search records.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                    }
                ],
            },
            3,
        )

        self.assertEqual(seed["schema_version"], "1.1")
        self.assertEqual(seed["environment"]["basic_info"]["url"], ["https://smithery.ai/servers/demo/server"])
        self.assertEqual(seed["environment"]["basic_info"]["version"], "2026-09-02")
        self.assertEqual(
            seed["init_ref_tools"][0],
            {
                "name": "search",
                "type": "function",
                "module": None,
                "description": "Search records.",
                "input": {"query": {"type": "string"}},
                "output": {},
            },
        )
        self.assertEqual(
            seed["environment"]["nums"],
            {"class": 0, "function": 1, "class_func": 0, "all_func": 1},
        )
        self.assertNotIn("tool_count", seed["others"])
        self.assertNotIn("data_directions", seed["others"])
        self.assertNotIn("organization_status", seed["others"])

    def test_records_are_sorted_and_preserve_catalog_fields(self):
        result = build_seed_records([
            {"qualifiedName": "z/server", "useCount": 2, "description": "Z", "verified": False, "score": 1},
            {"qualifiedName": "b/server", "useCount": 5, "description": "B", "verified": True, "score": 2},
            {"qualifiedName": "a/server", "useCount": 5, "description": "A", "verified": False, "score": 3},
        ])

        self.assertEqual([item["qualifiedName"] for item in result], ["a/server", "b/server", "z/server"])
        self.assertEqual([item["score"] for item in result], [3, 2, 1])
        self.assertEqual([item["verified"] for item in result], [False, True, False])

    def test_fetch_uses_seeded_remote_deep_pagination(self):
        def fake_get(url, _api_key, _retries):
            query = parse_qs(urlsplit(url).query)
            page = int(query["page"][0])
            self.assertEqual(query["seed"], ["9"])
            self.assertNotIn("fields", query)
            remote = query["remote"][0] == "true"
            if remote:
                names = ["a", "b"] if page == 1 else ["c"]
                total_pages = 2
                total_count = 3
            else:
                names = ["local"]
                total_pages = 1
                total_count = 1
            return {
                "servers": [
                    {
                        "qualifiedName": name,
                        "description": name.upper(),
                        "useCount": 1,
                        "verified": name == "b",
                        "remote": remote,
                    }
                    for name in names
                ],
                "pagination": {
                    "currentPage": page,
                    "pageSize": 2,
                    "totalPages": total_pages,
                    "totalCount": total_count,
                },
            }

        with patch("seed_gen.scripts.fetch_smithery_servers._get_json", side_effect=fake_get):
            servers, pagination = fetch_all_servers("secret", page_size=2, workers=1, seed=9)

        self.assertEqual([server["qualifiedName"] for server in servers], ["a", "b", "c", "local"])
        self.assertEqual(pagination["total_pages"], 3)
        self.assertEqual(pagination["total_count"], 4)
        self.assertEqual(pagination["groups"]["remote_true"]["total_count"], 3)
        self.assertEqual(pagination["groups"]["remote_false"]["total_count"], 1)

    def test_fetch_rejects_duplicate_names(self):
        payload = {
            "servers": [
                {
                    "qualifiedName": "same",
                    "description": "A",
                    "useCount": 1,
                    "verified": True,
                    "remote": True,
                },
                {
                    "qualifiedName": "same",
                    "description": "B",
                    "useCount": 0,
                    "verified": False,
                    "remote": False,
                },
            ],
            "pagination": {"currentPage": 1, "pageSize": 2, "totalPages": 1, "totalCount": 2},
        }
        with patch("seed_gen.scripts.fetch_smithery_servers._get_json", return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "duplicate server"):
                fetch_all_servers("secret", page_size=2, workers=1)

    def test_fetch_rejects_missing_verified_flag(self):
        payload = {
            "servers": [
                {"qualifiedName": "server", "description": "A", "useCount": 1, "remote": True}
            ],
            "pagination": {"currentPage": 1, "pageSize": 1, "totalPages": 1, "totalCount": 1},
        }
        with patch("seed_gen.scripts.fetch_smithery_servers._get_json", return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "invalid verified value"):
                fetch_all_servers("secret", page_size=1, workers=1)


if __name__ == "__main__":
    unittest.main()
