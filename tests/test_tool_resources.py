from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from env_gen.tool_gen.resources import ResourceCatalog, parse_resource_ref, resource_ref
from env_gen.tool_gen.runtime import ToolPackage, ToolRuntime
from env_gen.tool_gen.compiler import (
    ToolGenerationError,
    _validate_resource_annotations,
)
from task_gen.program.utils.environment import CompleteEnvironmentPackage
from task_gen.program.utils.tool_runtime import CompleteEnvironmentRuntime


class ResourceCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        reports = self.root / "reports"
        designs = self.root / "designs"
        reports.mkdir()
        designs.mkdir()
        (designs / "subdirectory").mkdir()
        (reports / "daily.json").write_text('{"status":"ok"}', encoding="utf-8")
        (designs / "main.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
        environment = {
            "environment_id": "resource_test",
            "name": "Resource test",
            "summary": "Resource lookup test environment.",
            "record_sets": [],
            "filesystem_scopes": [
                {
                    "scope_id": "reports",
                    "name": "Reports",
                    "description": "Generated reports.",
                    "access": "copy_on_write",
                },
                {
                    "scope_id": "designs",
                    "name": "Designs",
                    "description": "KiCad design files.",
                    "access": "read_only",
                },
            ],
        }
        self.catalog = ResourceCatalog(environment, lambda scope: self.root / scope)

    def test_lists_logical_references_without_physical_paths(self) -> None:
        resources = self.catalog.list(scope_id="designs", query="main")
        self.assertEqual(resources[0]["ref"], "aw://designs/main.kicad_sch")
        self.assertNotIn(str(self.root), str(resources))

    def test_inspects_text_resource(self) -> None:
        resource = self.catalog.inspect("aw://reports/daily.json")
        self.assertEqual(resource["relative_path"], "daily.json")
        self.assertIn('"status"', resource["text_preview"])

    def test_validates_scope_relative_paths_for_resource_fields(self) -> None:
        arguments = {
            "source": "main.kicad_sch",
            "outputs": ["render/main.svg"],
        }
        normalized = self.catalog.normalize_arguments(
            arguments,
            schema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "x-resource-scope": "designs",
                        "x-resource-kind": "file",
                    },
                    "outputs": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "x-resource-scope": "designs",
                            "x-resource-kind": "file",
                        },
                    },
                },
            },
            allowed_scopes={"designs"},
        )
        self.assertEqual(normalized["source"], "main.kicad_sch")
        self.assertEqual(normalized["outputs"], ["render/main.svg"])

    def test_rejects_undeclared_scope_and_parent_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "未声明"):
            self.catalog.normalize_arguments(
                "daily.json",
                schema={
                    "type": "string",
                    "x-resource-scope": "reports",
                    "x-resource-kind": "file",
                },
                allowed_scopes={"designs"},
            )
        with self.assertRaises(ValueError):
            parse_resource_ref("aw://reports/../secret.txt")

    def test_rejects_resource_uri_in_unannotated_string(self) -> None:
        with self.assertRaisesRegex(ValueError, "未标注"):
            self.catalog.normalize_arguments(
                {"ticket_id": "aw://reports/daily.json"},
                schema={
                    "type": "object",
                    "properties": {"ticket_id": {"type": "string"}},
                },
            )
        with self.assertRaisesRegex(ValueError, "相对路径"):
            self.catalog.normalize_arguments(
                {"path": "aw://reports/daily.json"},
                schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "x-resource-scope": "reports",
                            "x-resource-kind": "file",
                        }
                    },
                },
            )

    def test_rejects_unannotated_resource_uri_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "业务工具结果"):
            self.catalog.externalize_result(
                {"value": "aw://reports/daily.json"},
                schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            )

    def test_schema_metadata_checks_scope_and_existing_resource_kind(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "x-resource-scope": "designs",
                    "x-resource-kind": "file",
                }
            },
        }
        normalized = self.catalog.normalize_arguments(
            {"source": "main.kicad_sch"},
            schema=schema,
            allowed_scopes={"designs", "reports"},
        )
        self.assertEqual(normalized, {"source": "main.kicad_sch"})
        with self.assertRaisesRegex(ValueError, "未声明"):
            self.catalog.normalize_arguments(
                {"source": "main.kicad_sch"},
                schema=schema,
                allowed_scopes={"reports"},
            )
        with self.assertRaisesRegex(ValueError, "要求文件"):
            self.catalog.normalize_arguments(
                {"source": "subdirectory"},
                schema=schema,
                allowed_scopes={"designs", "reports"},
            )

    def test_resource_ref_encodes_names(self) -> None:
        ref = resource_ref("reports", "daily report.json")
        self.assertEqual(ref, "aw://reports/daily%20report.json")
        self.assertEqual(parse_resource_ref(ref)[1].as_posix(), "daily report.json")

    def test_validates_annotated_output_path_inside_success_branch(self) -> None:
        schema = {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "success": {"const": True},
                        "data": {
                            "type": "object",
                            "properties": {
                                "report": {
                                    "type": "string",
                                    "x-resource-scope": "reports",
                                    "x-resource-kind": "file",
                                }
                            },
                        },
                    },
                    "required": ["success", "data"],
                },
                {
                    "type": "object",
                    "properties": {
                        "success": {"const": False},
                        "error": {"type": "object"},
                    },
                    "required": ["success", "error"],
                },
            ]
        }
        result = self.catalog.externalize_result(
            {"success": True, "data": {"report": "daily.json"}},
            schema=schema,
            allowed_scopes={"reports"},
        )
        self.assertEqual(result["data"]["report"], "daily.json")

    def test_toolgen_checks_resource_annotations_against_environment(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "x-resource-scope": "designs",
                    "x-resource-kind": "file",
                }
            },
        }
        _validate_resource_annotations(
            "render_design", schema, {"designs"}, {"designs"}
        )
        with self.assertRaisesRegex(ToolGenerationError, "未声明的目标资源"):
            _validate_resource_annotations(
                "render_design", schema, {"designs"}, {"reports"}
            )
        schema["properties"]["source"]["x-resource-kind"] = "path"
        with self.assertRaisesRegex(ToolGenerationError, "file 或 directory"):
            _validate_resource_annotations(
                "render_design", schema, {"designs"}, {"designs"}
            )
        with self.assertRaisesRegex(ToolGenerationError, "outputSchema"):
            _validate_resource_annotations(
                "render_design",
                {
                    "type": "string",
                    "x-resource-scope": "missing",
                    "x-resource-kind": "file",
                },
                {"designs"},
                {"designs"},
                schema_name="outputSchema",
            )

    def test_runtime_accepts_scope_relative_path_for_resource_tool(self) -> None:
        package_root = self.root / "package"
        scope_root = package_root / "state/filesystem_scopes/reports"
        scope_root.mkdir(parents=True)
        (scope_root / "daily.json").write_text('{"status":"ok"}', encoding="utf-8")
        environment = {
            "environment_id": "resource_runtime",
            "record_sets": [],
            "filesystem_scopes": [
                {"scope_id": "reports", "access": "read_only"}
            ],
        }
        (package_root / "environment.json").write_text(
            json.dumps(environment), encoding="utf-8"
        )
        tool = {
            "name": "read_report",
            "usageConditions": {"targetResources": ["reports"]},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "x-resource-scope": "reports",
                        "x-resource-kind": "file",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "const": True},
                    "data": {"type": "object"},
                },
                "required": ["success", "data"],
                "additionalProperties": False,
            },
            "internal": {
                "code": (
                    "def run(arguments, context):\n"
                    "    path = context.scope_root('reports') / arguments['path']\n"
                    "    return {'success': True, 'data': {'text': path.read_text()}}\n"
                )
            },
        }
        package = ToolPackage(package_root, environment, (tool,))
        with ToolRuntime(package) as runtime:
            result = runtime.call(
                "read_report", {"path": "daily.json"}
            )
        self.assertEqual(result["data"]["text"], '{"status":"ok"}')

    def test_runtime_returns_annotated_paths_as_scope_relative_paths(self) -> None:
        package_root = self.root / "output-package"
        scope_root = package_root / "state/filesystem_scopes/reports"
        scope_root.mkdir(parents=True)
        (scope_root / "daily.json").write_text('{"status":"ok"}', encoding="utf-8")
        environment = {
            "environment_id": "resource_output_runtime",
            "record_sets": [],
            "filesystem_scopes": [
                {"scope_id": "reports", "access": "read_only"}
            ],
        }
        (package_root / "environment.json").write_text(
            json.dumps(environment), encoding="utf-8"
        )
        tool = {
            "name": "locate_report",
            "usageConditions": {"targetResources": ["reports"]},
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "const": True},
                    "data": {
                        "type": "object",
                        "properties": {
                            "report": {
                                "type": "string",
                                "x-resource-scope": "reports",
                                "x-resource-kind": "file",
                            }
                        },
                        "required": ["report"],
                        "additionalProperties": False,
                    },
                },
                "required": ["success", "data"],
                "additionalProperties": False,
            },
            "internal": {
                "code": (
                    "def run(arguments, context):\n"
                    "    return {'success': True, 'data': {'report': 'daily.json'}}\n"
                )
            },
        }
        package = ToolPackage(package_root, environment, (tool,))
        with ToolRuntime(package) as runtime:
            result = runtime.call("locate_report", {})
        self.assertEqual(result["data"]["report"], "daily.json")

    def test_program_runtime_uses_the_same_scope_relative_path_contract(self) -> None:
        package_root = self.root / "program-package"
        scope_root = package_root / "state/filesystem_scopes/reports"
        scope_root.mkdir(parents=True)
        (scope_root / "daily.json").write_text('{"status":"ok"}', encoding="utf-8")
        environment = {
            "schema_version": "2.0",
            "environment_id": "program_resource_runtime",
            "record_sets": [],
            "filesystem_scopes": [
                {"scope_id": "reports", "access": "read_only"}
            ],
        }
        tool = {
            "name": "read_report",
            "usageConditions": {"targetResources": ["reports"]},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "x-resource-scope": "reports",
                        "x-resource-kind": "file",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "const": True},
                    "data": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "x-resource-scope": "reports",
                                "x-resource-kind": "file",
                            },
                            "text": {"type": "string"},
                        },
                        "required": ["source", "text"],
                        "additionalProperties": False,
                    },
                },
                "required": ["success", "data"],
                "additionalProperties": False,
            },
            "internal": {
                "code": (
                    "def run(arguments, context):\n"
                    "    path = context.scope_root('reports') / arguments['path']\n"
                    "    return {'success': True, 'data': "
                    "{'source': arguments['path'], 'text': path.read_text()}}\n"
                )
            },
        }
        package = CompleteEnvironmentPackage(
            package_root=package_root,
            environment_path=package_root / "environment.json",
            state_root=package_root / "state",
            environment=environment,
            tools=(tool,),
            tools_path=None,
            package_format="v2",
        )

        with CompleteEnvironmentRuntime(package) as runtime:
            result = runtime.call("read_report", {"path": "daily.json"})
            self.assertEqual(result["data"]["source"], "daily.json")
            self.assertEqual(result["data"]["text"], '{"status":"ok"}')
            with self.assertRaisesRegex(ValueError, "相对路径"):
                runtime.call("read_report", {"path": str(scope_root / "daily.json")})
            with self.assertRaisesRegex(ValueError, "相对路径"):
                runtime.call("read_report", {"path": "aw://reports/daily.json"})


if __name__ == "__main__":
    unittest.main()
