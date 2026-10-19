from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from harness.mcp_protocol import public_tool, public_tools
from harness.resources import ResourceCatalog
from task_gen.task_eval_mcp import TaskEvalMcpServer


@pytest.fixture
def tool():
    path_schema = {
        "type": "string", "x-resource-scope": "reports",
        "x-resource-kind": "file", "description": "Scope-relative filename.",
    }
    return {
        "name": "export_report", "description": "Export a report.",
        "inputSchema": {
            "type": "object", "required": ["output_name"], "additionalProperties": False,
            "properties": {"output_name": deepcopy(path_schema), "record_id": {"type": "string"}},
        },
        "outputSchema": {
            "type": "object", "properties": {"path": deepcopy(path_schema)},
        },
    }


def without_descriptions(value):
    if isinstance(value, dict):
        return {key: without_descriptions(child) for key, child in value.items() if key != "description"}
    if isinstance(value, list):
        return [without_descriptions(child) for child in value]
    return value


def test_workspace_projection_only_changes_path_descriptions(tool):
    original = deepcopy(tool)
    legacy = public_tool(tool)
    projected = public_tool(tool, scope_mounts={"reports": "published/reports"})
    for schema_name, field in (("inputSchema", "output_name"), ("outputSchema", "path")):
        description = projected[schema_name]["properties"][field]["description"]
        assert "`published/reports/`" in description
        assert "新建输出" in description
    assert projected["inputSchema"]["properties"]["record_id"] == {"type": "string"}
    assert without_descriptions(projected) == without_descriptions(legacy)
    assert tool == original
    assert public_tool(tool, scope_mounts={}) == legacy
    assert public_tools([tool], scope_mounts={"reports": "published/reports"}) == [projected]
    assert public_tool(projected, scope_mounts={"reports": "published/reports"}) == projected


def test_single_scope_paths_remain_workspace_relative(tool):
    projected = public_tool(tool, scope_mounts={"reports": "."})
    description = projected["inputSchema"]["properties"]["output_name"]["description"]
    assert "挂载在当前工作目录" in description
    assert "以 `reports/` 开头" not in description
    assert without_descriptions(projected) == without_descriptions(public_tool(tool))


def test_unknown_scopes_keep_original_descriptions(tool):
    assert public_tool(tool, scope_mounts={"other": "other"}) == public_tool(tool)


@pytest.mark.parametrize("wrapper", [
    lambda child: {"properties": {"nested": child}},
    lambda child: {"items": child},
    lambda child: {"items": [child]},
    lambda child: {"anyOf": [{"type": "null"}, child]},
    lambda child: {"allOf": [{"oneOf": [child]}]},
    lambda child: {"$defs": {"path": child}},
    lambda child: {"definitions": {"path": child}},
    lambda child: {"additionalProperties": child},
    lambda child: {"dependentSchemas": {"selector": child}},
    lambda child: {"if": {"type": "string"}, "then": child},
    lambda child: {"prefixItems": [child]},
])
def test_nested_schema_paths_are_described_without_editing_examples(tool, wrapper):
    child = {"type": "string", "x-resource-scope": "reports"}
    example = {"x-resource-scope": "reports", "description": "Example data, not a schema."}
    tool["inputSchema"] = {**wrapper(child), "examples": [example], "default": example}
    projected = public_tool(tool, scope_mounts={"reports": "reports"})
    assert "`reports/`" in json.dumps(projected["inputSchema"], ensure_ascii=False)
    assert projected["inputSchema"]["examples"] == [example]
    assert projected["inputSchema"]["default"] == example
    assert without_descriptions(projected) == without_descriptions(public_tool(tool))
    assert "description" not in child


def test_parameter_acceptance_and_scope_boundary_are_unchanged(tool, tmp_path):
    workspace = tmp_path / "workspace"
    reports = workspace / "reports"
    reports.mkdir(parents=True)
    environment = {"filesystem_scopes": [{"scope_id": "reports"}]}
    catalog = ResourceCatalog(
        environment, lambda scope_id: reports, workspace_root=workspace,
        scope_mounts={"reports": "reports"},
    )
    schemas = [tool["inputSchema"], public_tool(tool, scope_mounts={"reports": "reports"})["inputSchema"]]
    for schema in schemas:
        assert catalog.normalize_arguments(
            {"output_name": "reports/new.csv"}, schema=schema,
        ) == {"output_name": "new.csv"}
        for invalid_path in ("new.csv", "reports/../secret.csv", "/outside/new.csv"):
            with pytest.raises(ValueError):
                catalog.normalize_arguments({"output_name": invalid_path}, schema=schema)
    assert not (reports / "new.csv").exists()


def test_mcp_lists_runtime_paths_but_keeps_business_tools_unchanged(tool, tmp_path):
    server = TaskEvalMcpServer({
        "tools": [tool], "workspace_root": str(tmp_path), "trace": str(tmp_path / "trace.jsonl"),
        "scope_mounts": {"reports": "reports"},
    })
    listed = server.handle({"method": "tools/list"})["tools"]
    assert listed == public_tools([tool], scope_mounts={"reports": "reports"})
    assert server.tools["export_report"] == tool
    legacy = TaskEvalMcpServer({
        "tools": [tool], "workspace_root": str(tmp_path), "trace": str(tmp_path / "trace.jsonl"),
    })
    assert legacy.handle({"method": "tools/list"})["tools"] == public_tools([tool])


def test_author_materials_use_the_same_path_descriptions_as_mcp(tool, tmp_path):
    from distill.runner import _write_author_materials

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = SimpleNamespace(output=tmp_path, model_workspace=workspace, scope_mounts={"reports": "reports"})
    materials = {"public_tools.json": [public_tool(tool)], "original_calls.json": [{"tool": "export_report"}]}
    original = deepcopy(materials)
    root = _write_author_materials(paths, materials)
    assert json.loads((root / "public_tools.json").read_text()) == public_tools([tool], scope_mounts=paths.scope_mounts)
    assert json.loads((root / "original_calls.json").read_text()) == original["original_calls.json"]
    assert materials == original
