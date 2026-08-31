from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from task_gen.program_form import (
    CompleteEnvironmentPackage,
    CompleteEnvironmentRuntime,
    ProgramGenerationPolicy,
    ProgramTaskGenerator,
    execute_reference_program,
)
from task_gen.program_form.executor import validate_reference_program
from task_gen.program_form.prompts import build_program_generation_prompt


def object_schema(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def output_schema(data_properties, data_required):
    return {
        "oneOf": [
            object_schema(
                {
                    "success": {"type": "boolean", "const": True},
                    "data": object_schema(data_properties, data_required),
                },
                ["success", "data"],
            ),
            object_schema(
                {
                    "success": {"type": "boolean", "const": False},
                    "error": object_schema(
                        {
                            "code": {"type": "string", "enum": ["not_found"]},
                            "path": {"type": "string"},
                            "message": {"type": "string", "minLength": 1},
                            "retryable": {"type": "boolean"},
                        },
                        ["code", "path", "message", "retryable"],
                    ),
                },
                ["success", "error"],
            ),
        ]
    }


TOOL_SOURCE = '''
def run(arguments, context):
    import json
    path = context.workspace_root / "entities" / "items.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    operation = "__OPERATION__"
    if operation == "list_items":
        return {"success": True, "data": {"items": items}}
    if operation == "get_item":
        item = next((row for row in items if row["item_id"] == arguments["item_id"]), None)
        if item is None:
            return {"success": False, "error": {"code": "not_found", "path": "$.item_id", "message": "Item not found", "retryable": False}}
        return {"success": True, "data": {"item": item}}
    item = next((row for row in items if row["item_id"] == arguments["item_id"]), None)
    if item is None:
        return {"success": False, "error": {"code": "not_found", "path": "$.item_id", "message": "Item not found", "retryable": False}}
    item["status"] = arguments["status"]
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "data": {"item_id": item["item_id"], "status": item["status"]}}
'''


class ProgramFormTests(unittest.TestCase):
    def make_package(self, root: Path) -> Path:
        package = root / "environment"
        workspace = package / "workspace" / "entities"
        workspace.mkdir(parents=True)
        (workspace / "items.json").write_text(
            json.dumps(
                [
                    {"item_id": "a", "score": 4, "eligible": True, "status": "open"},
                    {"item_id": "b", "score": 9, "eligible": False, "status": "open"},
                    {"item_id": "c", "score": 7, "eligible": True, "status": "open"},
                ]
            ),
            encoding="utf-8",
        )
        item = object_schema(
            {
                "item_id": {"type": "string"},
                "score": {"type": "integer"},
                "eligible": {"type": "boolean"},
                "status": {"type": "string"},
            },
            ["item_id", "score", "eligible", "status"],
        )
        tools = [
            {
                "name": "list_items",
                "description": "List all review candidates and their current evidence.",
                "inputSchema": object_schema({}, []),
                "outputSchema": output_schema(
                    {"items": {"type": "array", "items": item}}, ["items"]
                ),
                "internal": {"code": TOOL_SOURCE.replace("__OPERATION__", "list_items")},
            },
            {
                "name": "get_item",
                "description": "Inspect one candidate by its stable identifier.",
                "inputSchema": object_schema({"item_id": {"type": "string"}}, ["item_id"]),
                "outputSchema": output_schema({"item": item}, ["item"]),
                "internal": {"code": TOOL_SOURCE.replace("__OPERATION__", "get_item")},
            },
            {
                "name": "update_item_status",
                "description": "Change the workflow status of one candidate.",
                "inputSchema": object_schema(
                    {
                        "item_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["open", "selected"]},
                    },
                    ["item_id", "status"],
                ),
                "outputSchema": output_schema(
                    {"item_id": {"type": "string"}, "status": {"type": "string"}},
                    ["item_id", "status"],
                ),
                "internal": {"code": TOOL_SOURCE.replace("__OPERATION__", "update")},
            },
        ]
        environment = {
            "schema_version": "1.0",
            "environment_id": "candidate_review",
            "name": "Candidate review",
            "description": "Select the highest-scoring eligible candidate and record the decision.",
            "resources": [
                {
                    "resource_id": "items",
                    "name": "Items",
                    "description": "Review candidates.",
                    "data_type": "raw",
                    "storage_type": "file",
                    "path": "entities/items.json",
                    "format": "json",
                    "writable": True,
                }
            ],
            "rules": [],
            "tools": tools,
        }
        (package / "environment.json").write_text(
            json.dumps(environment), encoding="utf-8"
        )
        return package

    @staticmethod
    def solution_code() -> str:
        return '''
listed = call_tool("list_items", {})
eligible = []
for row in listed["data"]["items"]:
    detail = call_tool("get_item", {"item_id": row["item_id"]})
    if detail["data"]["item"]["eligible"]:
        eligible.append(detail["data"]["item"])
eligible = sorted(eligible, key=lambda row: row["score"], reverse=True)
winner = eligible[0]
updated = call_tool("update_item_status", {"item_id": winner["item_id"], "status": "selected"})
final_answer = {"selected_item_id": winner["item_id"], "selected_score": winner["score"], "status": updated["data"]["status"]}
'''.strip()

    @staticmethod
    def answer_schema():
        return object_schema(
            {
                "selected_item_id": {"type": "string"},
                "selected_score": {"type": "integer"},
                "status": {"type": "string"},
            },
            ["selected_item_id", "selected_score", "status"],
        )

    def test_public_projection_removes_internal_tool_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = CompleteEnvironmentPackage.load(self.make_package(Path(temporary)))
            public = package.public_environment()
            self.assertNotIn("internal", public["tools"][0])
            self.assertIn("inputSchema", public["tools"][0])

    def test_reference_program_executes_tools_and_changes_isolated_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = CompleteEnvironmentPackage.load(self.make_package(Path(temporary)))
            result = execute_reference_program(package, self.solution_code(), self.answer_schema())
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.answer["selected_item_id"], "c")
            self.assertEqual(len(result.trace), 5)
            self.assertEqual({item["tool"] for item in result.trace}, {"list_items", "get_item", "update_item_status"})
            self.assertEqual(result.state_diff["modified"], ["entities/items.json"])
            baseline = json.loads((package.workspace_root / "entities/items.json").read_text())
            self.assertTrue(all(item["status"] == "open" for item in baseline))

    def test_runtime_rejects_arguments_outside_tool_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = CompleteEnvironmentPackage.load(self.make_package(Path(temporary)))
            with CompleteEnvironmentRuntime(package) as runtime:
                with self.assertRaisesRegex(ValueError, "输入不符合 Schema"):
                    runtime.call("get_item", {"item_id": "a", "unexpected": True})

    def test_reference_program_rejects_direct_file_access(self):
        errors = validate_reference_program(
            'value = open("workspace/file.json").read()\nfinal_answer = {"value": value}'
        )
        self.assertTrue(any("open" in error for error in errors))

    def test_offline_candidate_becomes_deterministic_task_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = self.make_package(root)
            candidates = root / "candidates.json"
            candidates.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "task": "请审查当前全部候选记录，排除不符合资格的对象，从剩余对象中选择评分最高者，并把最终选择正式记录为已选中，同时返回所选对象、评分和最新状态。",
                                "output_schema": self.answer_schema(),
                                "solution_code": self.solution_code(),
                                "design": {
                                    "business_goal": "从当前评审范围内选出最高评分的合格对象并正式记录选择结果。",
                                    "evidence_sources": [
                                        "候选对象的资格状态",
                                        "候选对象的业务评分与当前流程状态",
                                    ],
                                    "exclusion_basis": "资格状态为不合格的候选即使评分更高也必须排除。",
                                    "decision_rule": "在合格候选中按评分降序选择唯一最高者。",
                                    "tool_plan": [
                                        {"tool": "list_items", "purpose": "确定本次评审的完整候选范围。"},
                                        {"tool": "get_item", "purpose": "逐项核实候选资格、评分和当前状态。"},
                                        {"tool": "update_item_status", "purpose": "正式记录最终选中的候选对象。"},
                                    ],
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = root / "tasks"
            result = ProgramTaskGenerator(
                None,
                policy=ProgramGenerationPolicy(
                    task_count=1,
                    min_tool_calls=5,
                    min_distinct_tools=3,
                    clean_replays=2,
                    require_state_change=True,
                ),
            ).generate(
                environment_package=package_path,
                output_dir=output,
                candidates_path=candidates,
            )
            self.assertEqual(result.task_count, 1)
            payload = json.loads(result.tasks_path.read_text(encoding="utf-8"))
            task = payload["tasks"][0]
            self.assertEqual(task["reference"]["answer"]["selected_item_id"], "c")
            self.assertEqual(task["validation"]["clean_replays"], 2)
            self.assertNotIn("internal", payload["public_environment"]["tools"][0])

    def test_generation_prompt_defines_business_quality_before_code_contract(self):
        prompt = build_program_generation_prompt(
            round_index=1,
            policy=ProgramGenerationPolicy(
                task_count=2,
                min_tool_calls=6,
                min_distinct_tools=3,
                require_state_change=True,
            ),
        )
        self.assertLess(prompt.index("# 合格任务的核心定义"), prompt.index("# 隐藏参考程序"))
        self.assertIn("至少两类语义不同", prompt)
        self.assertIn("表面相关但", prompt)
        self.assertIn("不得通过重复查询", prompt)
        self.assertIn("每个任务都必须完成一项", prompt)
        self.assertIn("validation_feedback.json", prompt)


if __name__ == "__main__":
    unittest.main()
