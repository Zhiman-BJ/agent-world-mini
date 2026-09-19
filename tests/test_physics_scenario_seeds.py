"""Regression checks for scenario table mapping and lossless package API merging."""

import copy
import unittest

from seed_gen.scripts.build_physics_scenario_seeds import (
    counts,
    merge_tools,
    parse_scenarios,
    scenario_validator,
    select_package,
)


def function(module="package_a.api", name="solve", description="Original docs"):
    return {"module": module, "name": name, "type": "function",
            "description": description, "input": {}, "output": None}


def klass(method_names):
    return {"module": "package_a.api", "name": "Model", "type": "class",
            "description": "Model docs", "input": None,
            "function": [{"name": name, "description": name, "input": {}, "output": None}
                         for name in method_names]}


class PhysicsScenarioSeedsTests(unittest.TestCase):
    def test_table_preserves_text_and_fills_only_l2(self):
        table = """| L2 | L3 | 应用场景 | 场景具体描述 | 包 / 包组合 | 关系 | 可合成任务 |
| :---: | --- | --- | --- | --- | --- | --- |
| 02.01 TCAD | 02.01.01 Poisson | 应用一 | 描述一 | A / B | 可替代 | 扫描；验证 |
| | 02.01.02 Quantum | 应用二 | 描述二 | C → solver | 集成 | 读取结果 |

| 包 | 版本 |
| A | v1 |
"""
        rows = parse_scenarios(table)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["l2"], "02.01 TCAD")
        self.assertEqual(rows[0]["possible_task"], "扫描；验证")
        self.assertEqual(rows[1]["table_packages"], "C → solver")
        with self.assertRaisesRegex(ValueError, "L2/L3 mismatch"):
            parse_scenarios(table.replace("02.01.02 Quantum", "02.02.02 Quantum"))

    def test_same_short_name_in_different_packages_survives(self):
        tools = merge_tools([[function()], [function(module="package_b.api")]])
        self.assertEqual(counts(tools)["all_func"], 2)

    def test_duplicate_functions_deduplicate_but_conflicts_fail(self):
        self.assertEqual(len(merge_tools([[function()], [function()]])), 1)
        with self.assertRaisesRegex(ValueError, "Conflicting function"):
            merge_tools([[function()], [function(description="Changed docs")]])

    def test_class_merge_unions_methods_without_mutating_input(self):
        left, right = klass(["__init__", "solve"]), klass(["__init__", "read"])
        before = copy.deepcopy([left, right])
        result = merge_tools([[left], [right]])
        self.assertEqual([m["name"] for m in result[0]["function"]], ["__init__", "solve", "read"])
        self.assertEqual([left, right], before)
        self.assertEqual(counts(result), {"class": 1, "function": 0, "class_func": 3, "all_func": 3})
        right["function"][0]["output"] = "conflicting return"
        with self.assertRaisesRegex(ValueError, "Conflicting method"):
            merge_tools([[left], [right]])

    def selection_fixture(self):
        seed = {"init_ref_tools": [klass(["__init__", "solve", "plot"]), function(name="unused")]}
        profile = {"capabilities": [{"id": "solver", "symbols": [
            {"module": "package_a.api", "name": "Model", "type": "class"}]}]}
        rule = {"package": "package_a", "capabilities": ["solver"], "reason": "solve task",
                "methods": {"Model": ["__init__", "solve"]}}
        return seed, profile, rule

    def test_selection_preserves_docs_and_records_excluded_methods(self):
        seed, profile, rule = self.selection_fixture()
        chosen, decisions = select_package(seed, profile, rule)
        self.assertEqual(chosen[0]["function"], seed["init_ref_tools"][0]["function"][:2])
        self.assertEqual(decisions[0]["excluded_methods"], ["plot"])
        self.assertFalse(decisions[1]["selected"])
        self.assertEqual(len(seed["init_ref_tools"][0]["function"]), 3)

    def test_stale_selectors_and_missing_constructors_fail(self):
        for change, expected in [({"exclude": ["typo"]}, "Unmatched exclude"),
                                 ({"methods": {"Model": ["solve"]}}, "Cannot remove available constructor"),
                                 ({"methods": {"Model": ["__init__", "missing"]}}, "Unavailable methods")]:
            with self.subTest(expected=expected):
                seed, profile, rule = self.selection_fixture()
                rule.update(change)
                with self.assertRaisesRegex(ValueError, expected):
                    select_package(seed, profile, rule)

    def test_profile_cannot_reintroduce_tools_missing_from_selected_input(self):
        seed, profile, rule = self.selection_fixture()
        seed["init_ref_tools"] = []
        with self.assertRaisesRegex(ValueError, "absent from selected input"):
            select_package(seed, profile, rule)

    def test_new_envelope_does_not_relax_tool_description_schema(self):
        validator = scenario_validator()
        tool_schema = validator.schema["$defs"]["referenceTool"]
        self.assertEqual(tool_schema["properties"]["description"]["minLength"], 1)
        self.assertEqual(validator.schema["$defs"]["seedEnvironment"]["properties"]["basic_info"]["type"], "array")


if __name__ == "__main__":
    unittest.main()
