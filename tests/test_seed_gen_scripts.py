import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from agent_world_mini.seed_gen.scripts.fetch_smithery_servers import build_seed_records, fetch_all_servers


class SmitherySeedExportTests(unittest.TestCase):
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

        with patch("agent_world_mini.seed_gen.scripts.fetch_smithery_servers._get_json", side_effect=fake_get):
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
        with patch("agent_world_mini.seed_gen.scripts.fetch_smithery_servers._get_json", return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "duplicate server"):
                fetch_all_servers("secret", page_size=2, workers=1)

    def test_fetch_rejects_missing_verified_flag(self):
        payload = {
            "servers": [
                {"qualifiedName": "server", "description": "A", "useCount": 1, "remote": True}
            ],
            "pagination": {"currentPage": 1, "pageSize": 1, "totalPages": 1, "totalCount": 1},
        }
        with patch("agent_world_mini.seed_gen.scripts.fetch_smithery_servers._get_json", return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "invalid verified value"):
                fetch_all_servers("secret", page_size=1, workers=1)


if __name__ == "__main__":
    unittest.main()
