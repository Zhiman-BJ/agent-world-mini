from copy import deepcopy
import unittest

from seed_gen.catalog import _seed_from_detail
from seed_gen.scripts.analyze_mcp_seeds import analyze, profile_seed, score_review, WEIGHTS


def seed(name, tools):
    return _seed_from_detail({"qualifiedName": name, "description": "Test", "tools": tools}, 1)


class McpSeedAnalysisTests(unittest.TestCase):
    def test_empty_toolsets_are_missing_evidence_and_not_duplicate_groups(self):
        result = analyze([seed("a", []), seed("b", [])])
        self.assertEqual(result["summary"]["routes"], {"needs_tool_evidence": 2})
        self.assertEqual(result["identical_toolset_groups"], [])
        self.assertTrue(all(r["suitability_score"] is None for r in result["records"]))

    def test_analysis_previews_cap_without_changing_source(self):
        source = seed("a", [{"name": "read", "description": "Read"}])
        source["init_ref_tools"] *= 101
        source["environment"]["nums"].update(function=101, all_func=101)
        original = deepcopy(source)
        record = profile_seed(source)
        self.assertEqual(record["tool_selection_preview"]["retained_count"], 100)
        self.assertIn("duplicate_tool_names", record["flags"])
        self.assertNotIn("source_count_mismatch", record["flags"])
        self.assertEqual(record["nonempty_input_count"], 0)
        self.assertIsNone(record["suitability_score"])
        self.assertEqual(source, original)

    def test_identical_definitions_are_only_duplicate_leads(self):
        tools = [{"name": "read", "description": "Read records"}]
        result = analyze([seed("a", tools), seed("b", tools)])
        self.assertEqual(result["identical_toolset_groups"], [["a", "b"]])
        self.assertEqual(len(result["records"]), 2)

    def test_review_cannot_cite_unknown_tools_or_claim_runtime_success(self):
        review = {"name": "a", "dimensions": {key: {"score": 3, "reason": "Evidence"} for key in WEIGHTS},
                  "evidence_tools": ["read"], "proposed_task_chain": ["read"]}
        self.assertEqual(score_review(review), 60)
        result = analyze([seed("a", [{"name": "read", "description": "Read"}])], [review])
        self.assertFalse(result["records"][0]["runtime_verified"])
        review["proposed_task_chain"] = ["invented_tool"]
        with self.assertRaisesRegex(ValueError, "outside the retained snapshot"):
            analyze([seed("a", [])], [review])


if __name__ == "__main__":
    unittest.main()
