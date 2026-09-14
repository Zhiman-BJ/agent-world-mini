from __future__ import annotations

import copy
import unittest

from seed_gen.scripts.select_python_ref_tools import SelectionError, _canonical_sha256, select_seed


def raw_payload():
    return [{
        "global_id": "pypi_demo_1",
        "schema_version": "1.1",
        "environment": {
            "basic_info": {
                "source": "pypi",
                "url": ["https://example.com/demo"],
                "name": "demo",
                "version": "v1",
                "index": 1,
            },
            "description": "Demo package.",
            "domain": {"level1": "general", "level2": None, "level3": None},
        },
        "init_ref_tools": [
            {
                "name": "Record",
                "type": "class",
                "module": "demo.core",
                "input": "object",
                "description": "A public record.",
                "function": [
                    {"name": "load", "description": "Load it.", "input": {}, "output": {}},
                    {"name": "save", "description": "Save it.", "input": {}, "output": {}},
                ],
            },
            {
                "name": "normalize",
                "type": "function",
                "module": "demo.core",
                "description": "Normalize a record.",
                "input": {},
                "output": {},
            },
            {
                "name": "DemoError",
                "type": "class",
                "module": "demo.errors",
                "input": "Exception",
                "description": "Demo failure.",
                "function": [],
            },
        ],
        "init_ref_tasks": [],
        "others": {},
    }]


def profile_for(payload):
    return {
        "profile_id": "demo_core_v1",
        "package": "demo",
        "version": "v1",
        "source_sha256": _canonical_sha256(payload),
        "boundary": "Local record operations.",
        "capabilities": [{
            "id": "record_lifecycle",
            "description": "Load and normalize records.",
            "selection_reason": "Representative documented operations.",
            "verification_tier": "local",
            "evidence": ["public_api", "core_capability", "deterministic_local"],
            "symbols": [
                {"module": "demo.core", "name": "Record", "type": "class", "methods": ["load"]},
                {"module": "demo.core", "name": "normalize", "type": "function"},
            ],
        }],
    }


class PythonRefToolSelectionTests(unittest.TestCase):
    def test_selects_exact_symbols_and_methods_without_mutating_raw(self):
        raw = raw_payload()
        original = copy.deepcopy(raw)

        selected, report = select_seed(raw, profile_for(raw), input_label="raw.json", profile_label="profile.json")

        self.assertEqual(raw, original)
        self.assertEqual([tool["name"] for tool in selected[0]["init_ref_tools"]], ["Record", "normalize"])
        self.assertEqual([method["name"] for method in selected[0]["init_ref_tools"][0]["function"]], ["load"])
        self.assertEqual(selected[0]["environment"]["nums"], {
            "class": 1,
            "function": 1,
            "class_func": 1,
            "all_func": 2,
        })
        self.assertNotIn("tool_count", selected[0]["others"])
        self.assertEqual(report["summary"]["selected_class_count"], 1)
        self.assertEqual(report["summary"]["selected_function_count"], 1)
        self.assertEqual(report["summary"]["selected_method_count"], 1)
        self.assertEqual(report["exclusion_counts"], {"support_type": 1})

    def test_rejects_source_hash_drift(self):
        raw = raw_payload()
        profile = profile_for(raw)
        profile["source_sha256"] = "0" * 64

        with self.assertRaisesRegex(SelectionError, "source_sha256 mismatch"):
            select_seed(raw, profile, input_label="raw.json", profile_label="profile.json")

    def test_rejects_missing_method(self):
        raw = raw_payload()
        profile = profile_for(raw)
        profile["capabilities"][0]["symbols"][0]["methods"] = ["missing"]

        with self.assertRaisesRegex(SelectionError, "Missing methods"):
            select_seed(raw, profile, input_label="raw.json", profile_label="profile.json")

    def test_missing_description_exception_is_explicit_and_preserves_source(self):
        raw = raw_payload()
        raw[0]["init_ref_tools"][0]["description"] = ""
        raw[0]["init_ref_tools"][0]["function"][0]["description"] = ""
        profile = profile_for(raw)
        with self.assertRaisesRegex(SelectionError, "empty description"):
            select_seed(raw, profile, input_label="raw.json", profile_label="profile.json")
        profile["capabilities"][0]["symbols"][0]["missing_description_reason"] = "Core fixture constructor; source has no docstring."
        original = copy.deepcopy(raw)
        selected, report = select_seed(raw, profile, input_label="raw.json", profile_label="profile.json")
        self.assertEqual(raw, original)
        self.assertEqual(selected[0]["init_ref_tools"][0]["description"], "")
        self.assertEqual(selected[0]["init_ref_tools"][0]["function"][0]["description"], "")
        self.assertEqual(report["summary"]["missing_description_count"], 2)
        self.assertEqual(report["selected_symbols"][0]["excluded_methods"][0]["name"], "save")

    def test_undocumented_method_requires_its_own_class_exception(self):
        raw = raw_payload()
        raw[0]["init_ref_tools"][0]["function"][0]["description"] = ""
        profile = profile_for(raw)
        with self.assertRaisesRegex(SelectionError, "Selected methods have empty descriptions"):
            select_seed(raw, profile, input_label="raw.json", profile_label="profile.json")

    def test_budget_counts_functions_and_methods_without_classes(self):
        raw = raw_payload()
        profile = profile_for(raw)
        profile["target_all_func"] = {"min": 2, "max": 2}
        _, report = select_seed(raw, profile, input_label="raw.json", profile_label="profile.json")
        self.assertEqual(report["summary"]["selected_all_func"], 2)
        profile["target_all_func"] = {"min": 3, "max": 4}
        with self.assertRaisesRegex(SelectionError, "outside target"):
            select_seed(raw, profile, input_label="raw.json", profile_label="profile.json")


if __name__ == "__main__":
    unittest.main()
