from __future__ import annotations

import json
import subprocess
import sys

import pytest

from harness.execution import call_environment_tool
from harness.kimi_file_policy import KimiFileAccessPolicy, build_kimi_file_hook_command
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


def test_expected_tool_names_exclude_protocol_resources_and_native_tools():
    names = expected_mcp_tool_names(
        [{"name": "inspect_design"}],
        {"filesystem_scopes": [{"scope_id": "designs"}]},
        server_name="agent_world_distill",
    )

    assert names == {
        "mcp__agent_world_distill__inspect_design",
    }


def test_task_call_keeps_annotated_output_as_scope_relative_path(tmp_path):
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
    assert record["result"]["data"]["report"] == "daily.json"
    assert str(tmp_path) not in json.dumps(record)


def test_scope_less_environment_still_rejects_aw_as_plain_business_value(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    tool = {
        "name": "echo",
        "inputSchema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        "outputSchema": {"type": "object"},
        "internal": {"code": "unused by fixture"},
    }

    with pytest.raises(ValueError, match="未标注"):
        call_environment_tool(
            "echo",
            {"value": "aw://reports/daily.json"},
            {"echo": tool},
            state,
            timeout=10,
            memory_limit=1024 * 1024,
            write_limit=1024 * 1024,
            environment={"filesystem_scopes": []},
            call_tool_fn=lambda *_args, **_kwargs: {
                "kind": None,
                "result": {"success": True},
                "error": None,
            },
        )


def test_kimi_native_file_policy_bounds_workspace_and_current_session(tmp_path):
    workspace = tmp_path / "model-workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("alpha\nbeta", encoding="utf-8")
    outside = tmp_path / "execution-state"
    outside.mkdir()
    (outside / "secret.txt").write_text("hidden", encoding="utf-8")
    home = tmp_path / "kimi-home"
    current = home / "sessions/workspace/current-session/agents/main"
    results = current / "tool-results"
    results.mkdir(parents=True)
    (results / "large.txt").write_text("long result", encoding="utf-8")
    (current / "wire.jsonl").write_text("{}\n", encoding="utf-8")
    other = home / "sessions/workspace/other-session/agents/main/tool-results"
    other.mkdir(parents=True)
    (other / "private.txt").write_text("other", encoding="utf-8")
    link = workspace / "escape.txt"
    link.symlink_to(outside / "secret.txt")
    policy = KimiFileAccessPolicy(workspace, home, tmp_path / "audit.jsonl")

    def check(tool, path=None, session="current-session"):
        arguments = {"pattern": "x"}
        if path is not None:
            arguments["path"] = str(path)
        return policy.check({
            "tool_name": tool,
            "tool_input": arguments,
            "tool_call_id": "call-1",
            "session_id": session,
        })

    assert check("Read", "notes.txt")["reason"] == "model_workspace"
    assert check("Grep")["reason"] == "model_workspace"
    assert check("Glob")["reason"] == "model_workspace"
    assert check("Read", results / "large.txt")["reason"] == "session_tool_results"
    assert check("Read", current / "wire.jsonl")["reason"] == "session_wire"
    assert check("Read", "kimi-file://attachment/current")["reason"] == "current_session_attachment"
    assert not check("Read", outside / "secret.txt")["allowed"]
    assert not check("Read", "~/secret.txt")["allowed"]
    assert not check("Read", link)["allowed"]
    assert not check("Read", other / "private.txt")["allowed"]
    assert not check("Glob", current / "wire.jsonl")["allowed"]


def test_kimi_native_file_hook_exits_two_and_audits_denial(tmp_path):
    workspace = tmp_path / "model-workspace"
    workspace.mkdir()
    home = tmp_path / "kimi-home"
    home.mkdir()
    secret = tmp_path / "execution-state/secret.txt"
    secret.parent.mkdir()
    secret.write_text("hidden", encoding="utf-8")
    audit = tmp_path / "raw/native_file_access.jsonl"
    command = build_kimi_file_hook_command(workspace, home, audit, python=sys.executable)

    result = subprocess.run(
        command,
        shell=True,
        input=json.dumps({
            "tool_name": "Read",
            "tool_input": {"path": str(secret)},
            "tool_call_id": "call-denied",
            "session_id": "session-1",
        }),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Native file access denied" in result.stderr
    record = json.loads(audit.read_text(encoding="utf-8"))
    assert record["allowed"] is False
    assert record["requested_path"] == str(secret)


def test_resource_protocol_uses_standard_list_and_read_methods(tmp_path):
    from task_gen.task_eval_mcp import TaskEvalMcpServer

    state = tmp_path / "state/filesystem_scopes/reports"
    state.mkdir(parents=True)
    (state / "daily.txt").write_text("ready", encoding="utf-8")
    server = TaskEvalMcpServer({
        "state_root": str(tmp_path / "state"),
        "trace": str(tmp_path / "trace.jsonl"),
        "max_tool_calls": 10,
        "timeout": 10,
        "memory_limit": 1024 * 1024,
        "write_limit": 1024 * 1024,
        "tools": [],
        "environment": {"filesystem_scopes": [{"scope_id": "reports", "access": "read_only"}]},
    })
    listed = server.handle({"method": "resources/list", "params": {}})
    assert listed["resources"][0]["uri"] == "aw://reports/daily.txt"
    assert listed["resources"][0]["scope_id"] == "reports"
    assert listed["resources"][0]["relative_path"] == "daily.txt"
    read = server.handle({"method": "resources/read", "params": {"uri": "aw://reports/daily.txt"}})
    assert read["contents"][0]["text"] == "ready"
