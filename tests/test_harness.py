from __future__ import annotations

import json

import pytest

from harness.execution import call_environment_tool
from harness.layout import create_task_run_layout
from harness.mcp_tools import expected_mcp_tool_names


def test_task_layout_separates_execution_state_from_model_workspace(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    (initial / "private.txt").write_text("STATE_ONLY", encoding="utf-8")

    layout = create_task_run_layout(initial, tmp_path / "run")

    assert (layout.execution_state / "private.txt").read_text() == "STATE_ONLY"
    assert list(layout.model_workspace.iterdir()) == []
    assert layout.execution_state != layout.model_workspace
    assert not layout.execution_state.is_symlink()
    assert not layout.model_workspace.is_symlink()


def test_task_layout_rejects_symlinked_initial_state(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("hidden", encoding="utf-8")
    (initial / "link").symlink_to(outside)

    with pytest.raises(ValueError, match="symbolic links"):
        create_task_run_layout(initial, tmp_path / "run")


def test_task_layout_rejects_symlink_as_initial_state(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    initial = tmp_path / "initial"
    initial.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic links"):
        create_task_run_layout(initial, tmp_path / "run")


def test_expected_tool_names_include_conditional_resource_tools():
    names = expected_mcp_tool_names(
        [{"name": "inspect_design"}],
        {"filesystem_scopes": [{"scope_id": "designs"}]},
        server_name="agent_world_distill",
        support_tools=("read_tool_result",),
    )

    assert names == {
        "mcp__agent_world_distill__inspect_design",
        "mcp__agent_world_distill__get_environment_overview",
        "mcp__agent_world_distill__list_environment_resources",
        "mcp__agent_world_distill__inspect_environment_resource",
        "mcp__agent_world_distill__read_tool_result",
    }


def test_task_call_externalizes_annotated_output_as_aw_reference(tmp_path):
    state = tmp_path / "state"
    (state / "filesystem_scopes" / "reports").mkdir(parents=True)
    environment = {
        "schema_version": "2.0",
        "record_sets": [],
        "filesystem_scopes": [
            {"scope_id": "reports", "access": "copy_on_write"}
        ],
    }
    output_schema = {
        "type": "object",
        "properties": {
            "success": {"type": "boolean", "const": True},
            "data": {
                "type": "object",
                "properties": {
                    "report": {
                        "type": "string",
                        "pattern": "^aw://",
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
    }
    tool = {
        "name": "create_report",
        "usageConditions": {"targetResources": ["reports"]},
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": output_schema,
        "internal": {"code": "unused by fixture"},
    }

    def fixture_call(*_args, **_kwargs):
        return {
            "kind": None,
            "result": {"success": True, "data": {"report": "daily.json"}},
            "error": None,
        }

    record = call_environment_tool(
        "create_report",
        {},
        {"create_report": tool},
        state,
        timeout=10,
        memory_limit=1024 * 1024,
        write_limit=1024 * 1024,
        environment=environment,
        call_tool_fn=fixture_call,
    )

    assert record["error"] is None
    assert record["result"]["data"]["report"] == "aw://reports/daily.json"
    assert str(tmp_path) not in json.dumps(record)
