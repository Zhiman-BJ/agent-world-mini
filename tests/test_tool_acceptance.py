from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from env_gen.tool_gen.compiler import ToolGenerator, _contains, _normalize_tests
from env_gen.tool_gen.test_contract import TestBaselines, baseline_changes, check_numbers, validate_tests
from tests import test_tool_gen as fixtures


def positive():
    return {"calls": [{"tool": "get_ticket", "arguments": {"ticket_id": "ticket-1"}}],
            "expected_data": {"ticket_id": "ticket-1", "status": "open"}}


class TestContractTests(unittest.TestCase):
    def test_list_subsets_keep_each_record_coherent(self):
        actual = [{"id": "a", "value": 10, "extra": 1}, {"id": "b", "value": 20}]
        self.assertTrue(_contains(actual, [{"id": "a", "value": 10}]))
        for expected in ([{"id": "a", "value": 20}], [{"id": "missing"}], [{"id": "a", "value": 100}]):
            self.assertFalse(_contains(actual, expected))

    def test_error_alias_and_flat_normalization(self):
        for field, value in (("expected_error", {"code": "not_found"}), ("expected_error_code", "not_found")):
            case = {"tool": "get_ticket", "arguments": {"ticket_id": "missing"},
                    "expect_success": False, field: value}
            result = _normalize_tests([case])
            self.assertEqual(result[0]["expected_error"], {"code": "not_found"})
            self.assertEqual(validate_tests(result), [])
            self.assertIn(field, case)

    def test_malformed_tests_are_visible(self):
        for value in (None, {}, [None], [1], ["test"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _normalize_tests(value)
        test = positive()
        test["expected_datta"] = {"status": "open"}
        self.assertTrue(validate_tests([test]))
        for value in ("false", 0, None):
            test = positive()
            test["expect_success"] = value
            self.assertTrue(validate_tests([test]))

    def test_flat_intermediate_assertions_are_not_discarded(self):
        with self.assertRaisesRegex(ValueError, "intermediate_assertion"):
            _normalize_tests([{"tool": "get_ticket", "expected_data": {"status": "open"}}, {"tool": "resolve_ticket"}])

    def test_invalid_calls_and_assertions(self):
        cases = [
            {"calls": [{"tool": "get_ticket", "ticket_id": "ticket-1"}]},
            {"calls": [{"tool": "get_ticket", "arguments": []}]},
            {"expected_records": [{"record_set_id": "tickets", "key": {}, "values": {}}]},
            {"expected_error": {"unexpected": "ignored"}},
            {"data_checks": [{"path": "/v", "op": "close", "value": 1, "atol": -1}]},
            {"data_checks": [{"path": "/v", "op": "range", "min": 2, "max": 1}]},
            {"data_checks": [{"path": "/v", "op": "range"}]},
            {"data_checks": [{"path": "/v", "op": "close", "value": float("nan")}]},
            {"expected_files": [{"scope_id": "docs", "path": "out.json"}]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                case = positive()
                case.update(changes)
                self.assertTrue(validate_tests([case]))

    def test_null_data_is_explicitly_checked(self):
        case = {**positive(), "expected_data": None, "expect_success": False}
        self.assertEqual(validate_tests([case]), [])
        from contextlib import nullcontext
        fake = SimpleNamespace(tools=[{"name": "get_ticket", "usageConditions": {"targetResources": [], "sideEffects": []}}])
        good = positive()
        runtime = SimpleNamespace(call=lambda *args: {"success": False, "data": {"unexpected": 1}})
        with patch("env_gen.tool_gen.compiler.ToolRuntime", return_value=nullcontext(runtime)):
            self.assertIn("test_1:returned_data_does_not_match_expectation", ToolGenerator._run_tests(fake, "get_ticket", [good, case]))

    def test_numeric_oracles_accept_valid_and_reject_mutations(self):
        close = [{"path": "/metrics/error", "op": "close", "value": 0.3, "atol": 1e-12}]
        self.assertEqual(check_numbers({"metrics": {"error": 0.1 + 0.2}}, close), [])
        for value in (30, "0.3", True, float("nan"), float("inf")):
            self.assertTrue(check_numbers({"metrics": {"error": value}}, close))
        self.assertTrue(check_numbers({}, close))
        self.assertEqual(check_numbers({"a/b": [{"~v": 3}]}, [{"path": "/a~1b/0/~0v", "op": "range", "min": 2, "max": 3}]), [])
        self.assertTrue(check_numbers({"v": 4}, [{"path": "/v", "op": "range", "max": 3}]))

    def test_baseline_preserves_parameters_flags_and_each_assertion(self):
        original = positive()
        self.assertFalse(baseline_changes([original], [deepcopy(original), positive()]))
        explicit = {**original, "expect_success": True, "expect_changed": False, "reference": "source"}
        self.assertFalse(baseline_changes([original], [explicit]))
        for key, value in (("calls", [{"tool": "get_ticket", "arguments": {"ticket_id": "missing"}}]),
                           ("expected_data", {"status": "closed"}), ("expect_success", False), ("expect_changed", True)):
            changed = {**original, key: value}
            self.assertTrue(baseline_changes([original], [changed]))
        self.assertTrue(baseline_changes([original, original], [original]))
        self.assertTrue(baseline_changes([original], []))

    def test_baseline_persists_across_resume_and_is_cached_during_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TestBaselines(Path(directory))
            store.capture("get_ticket", tests=[positive()])
            changed = positive()
            changed["expected_data"]["status"] = "closed"
            store.capture("get_ticket", tests=[changed])
            resumed = TestBaselines(Path(directory))
            self.assertEqual(resumed.entries["get_ticket"]["tests"], [positive()])
            store.path.write_text("{}", encoding="utf-8")
            self.assertEqual(store.entries["get_ticket"]["tests"], [positive()])


class ExecutableAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = fixtures.ToolGenV2Tests().make_package(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def package(self):
        from env_gen.tool_gen.runtime import ToolPackage
        ToolGenerator(fixtures.FakeAgent(), max_repairs=0).generate(self.root)
        return ToolPackage.load(self.root)

    def test_actual_business_error_code_and_path(self):
        package = self.package()
        negative = {"calls": [{"tool": "get_ticket", "arguments": {"ticket_id": "missing"}}],
                    "expect_success": False, "expected_error": {"code": "not_found", "path": "$.ticket_id"}}
        self.assertEqual(ToolGenerator._run_tests(package, "get_ticket", [positive(), negative]), [])
        for field, value in (("code", "invalid_state"), ("path", "$.other"), ("retryable", True)):
            wrong = deepcopy(negative)
            wrong["expected_error"][field] = value
            self.assertIn("test_1:returned_error_does_not_match_expectation", ToolGenerator._run_tests(package, "get_ticket", [positive(), wrong]))

    def test_expected_files_verify_content_and_scope_boundary(self):
        # Isolate result comparison while using actual temporary files.
        scope = self.root / "docs"
        scope.mkdir()
        (scope / "out.json").write_text('{"count":2,"name":"report"}', encoding="utf-8")
        runtime = SimpleNamespace(context=SimpleNamespace(scope_root=lambda name: scope),
                                  call=lambda *args: {"success": True, "data": {"path": "out.json"}})
        fake = SimpleNamespace(tools=[{"name": "export", "usageConditions": {"targetResources": ["docs"], "sideEffects": []}}])
        from contextlib import nullcontext
        case = {"calls": [{"tool": "export"}], "expected_files": [{"scope_id": "docs", "path": "out.json", "expected_json": {"count": 2}, "contains_text": "report"}]}
        with patch("env_gen.tool_gen.compiler.ToolRuntime", return_value=nullcontext(runtime)):
            self.assertEqual(ToolGenerator._run_tests(fake, "export", [case]), [])
            for change in ({"expected_json": {"count": 200}}, {"path": "missing.json"}, {"path": "../out.json"}, {"scope_id": "other"}, {"contains_text": "wrong"}):
                mutated = deepcopy(case)
                mutated["expected_files"][0].update(change)
                self.assertTrue(ToolGenerator._run_tests(fake, "export", [mutated]))

    def test_file_assertions_execute_in_real_sandbox(self):
        package = self.package()
        definition = deepcopy(package.tools[0])
        definition["usageConditions"]["targetResources"].append("reports")
        definition["usageConditions"]["sideEffects"] = ["Writes ticket.json report"]
        definition["internal"]["code"] = fixtures.GET_CODE.replace(
            '    return {"success": True,',
            '    import json\n    (context.scope_root("reports") / "ticket.json").write_text(json.dumps({"status": record["status"]}))\n    return {"success": True,',
        )
        from env_gen.tool_gen.runtime import ToolPackage
        actual = ToolPackage.load(self.root, tools=[definition])
        case = positive()
        case["expect_changed"] = True
        case["expected_files"] = [{"scope_id": "reports", "path": "ticket.json", "expected_json": {"status": "open"}}]
        self.assertEqual(ToolGenerator._run_tests(actual, "get_ticket", [case]), [])
        case["expected_files"][0]["expected_json"]["status"] = "closed"
        self.assertIn("test_0:file_json_does_not_match_expectation:ticket.json", ToolGenerator._run_tests(actual, "get_ticket", [case]))

    def test_new_plan_requires_acceptance_cases(self):
        class Agent(fixtures.FakeAgent):
            def run(agent, prompt, *, working_directory):
                result = super(Agent, agent).run(prompt, working_directory=working_directory)
                if "安排工具动作" in prompt:
                    path = working_directory / "action_plan.json"
                    plan = json.loads(path.read_text())
                    for action in plan["actions"]:
                        action.pop("verification_tests")
                    path.write_text(json.dumps(plan), encoding="utf-8")
                return result
        from env_gen.tool_gen.compiler import ToolGenerationError
        with self.assertRaisesRegex(ToolGenerationError, "verification_tests"):
            ToolGenerator(Agent(), max_repairs=0).generate(self.root)
        # A saved failed plan retains the acceptance requirement on resume.
        with self.assertRaisesRegex(ToolGenerationError, "verification_tests"):
            ToolGenerator(Agent(), max_repairs=0).generate(self.root)

    def test_legacy_saved_plan_remains_usable(self):
        result = ToolGenerator(fixtures.FakeAgent(), max_repairs=0).generate(self.root)
        path = result.action_plan_path
        plan = json.loads(path.read_text())
        plan.pop("verification_version")
        for action in plan["actions"]:
            action.pop("verification_tests")
        path.write_text(json.dumps(plan), encoding="utf-8")
        baseline = path.parent / "verification_baselines.json"
        baseline.rename(path.parent / "verification_baselines.initial.json")
        result = ToolGenerator(fixtures.FakeAgent(), max_repairs=0).generate(self.root)
        self.assertEqual(set(result.tool_names), {"get_ticket", "resolve_ticket"})
        reports = json.loads(result.validation_path.read_text())["reports"]
        self.assertTrue(all(r["verification_origin"] == "draft" for r in reports))

    def test_repair_must_fix_implementation_and_retain_original_tests(self):
        class Agent(fixtures.FakeAgent):
            change_only_tests = True

            def run(agent, prompt, *, working_directory):
                result = super(Agent, agent).run(prompt, working_directory=working_directory)
                path = working_directory / "drafts/get_ticket.json"
                if "这一组相关工具" in prompt or "工具修复" in prompt:
                    draft = json.loads(path.read_text())
                    if "这一组相关工具" in prompt:
                        draft["tool"]["internal"]["code"] = fixtures.GET_CODE.replace('record["status"]', '"closed"')
                    elif agent.change_only_tests:
                        draft["tests"][0]["expected_data"]["status"] = "closed"
                    else:
                        draft["tool"]["internal"]["code"] = fixtures.GET_CODE
                    path.write_text(json.dumps(draft), encoding="utf-8")
                return result

        agent = Agent()
        result = ToolGenerator(agent, max_repairs=1).generate(self.root)
        reports = json.loads(result.validation_path.read_text())["reports"]
        rejected = next(r for r in reports if r["tool"] == "get_ticket")
        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse(rejected["test_baseline_preserved"])
        self.assertIn("test_baseline_changed_requires_review", str(rejected["failures"]))
        # Restore the saved baseline tests, then fix code through normal repair.
        path = self.root / "tool_generation/drafts/get_ticket.json"
        draft = json.loads(path.read_text())
        draft["tests"][0]["expected_data"]["status"] = "open"
        path.write_text(json.dumps(draft), encoding="utf-8")
        agent.change_only_tests = False
        result = ToolGenerator(agent, max_repairs=1).generate(self.root)
        self.assertIn("get_ticket", result.tool_names)

    def test_plan_cases_run_even_when_draft_omits_them(self):
        class Agent(fixtures.FakeAgent):
            def run(agent, prompt, *, working_directory):
                result = super(Agent, agent).run(prompt, working_directory=working_directory)
                if "这一组相关工具" in prompt:
                    path = working_directory / "drafts/get_ticket.json"
                    draft = json.loads(path.read_text())
                    draft["tool"]["internal"]["code"] = fixtures.GET_CODE.replace('record["status"]', '"closed"')
                    draft["tests"][0]["expected_data"] = {"ticket_id": "ticket-1"}
                    path.write_text(json.dumps(draft), encoding="utf-8")
                return result
        result = ToolGenerator(Agent(), max_repairs=0).generate(self.root)
        report = next(r for r in json.loads(result.validation_path.read_text())["reports"] if r["tool"] == "get_ticket")
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["verification_origin"], "action_plan")
        self.assertIn("test_1:returned_data_does_not_match_expectation", report["failures"])


if __name__ == "__main__":
    unittest.main()
