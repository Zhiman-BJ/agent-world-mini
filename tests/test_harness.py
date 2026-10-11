from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import pytest

from harness.execution import call_environment_tool
from harness.kimi_file_policy import KimiFileAccessPolicy, build_kimi_file_hook_command
from harness.layout import create_task_run_layout
from harness.mcp_tools import expected_mcp_tool_names
from harness.resources import ResourceCatalog


def test_task_layout_requires_the_workspace_contract(tmp_path):
    initial = tmp_path / "initial"
    (initial / "filesystem_scopes/reports").mkdir(parents=True)
    (initial / "filesystem_scopes/reports/public.txt").write_text(
        "VISIBLE", encoding="utf-8"
    )
    sqlite3.connect(initial / "records.sqlite").close()
    environment = {
        "environment_id": "reports",
        "record_sets": [],
        "filesystem_scopes": [{"scope_id": "reports", "access": "read_only"}],
    }

    layout = create_task_run_layout(
        initial, tmp_path / "run", environment=environment,
    )

    assert (layout.model_workspace / "public.txt").read_text() == "VISIBLE"
    assert layout.record_database.parent == layout.private_state
    assert not (layout.model_workspace / "records.sqlite").exists()
    assert not layout.model_workspace.is_symlink()


def test_distill_layout_exposes_business_files_but_keeps_records_private(tmp_path):
    initial = tmp_path / "initial"
    scope = initial / "filesystem_scopes/reports"
    scope.mkdir(parents=True)
    (scope / "daily.txt").write_text("ready", encoding="utf-8")
    sqlite3.connect(initial / "records.sqlite").close()
    environment = {
        "environment_id": "report_review",
        "record_sets": [],
        "filesystem_scopes": [{"scope_id": "reports", "access": "read_only"}],
    }

    layout = create_task_run_layout(
        initial,
        tmp_path / "case",
        environment=environment,
        workspace_name="report_review_task7",
    )

    assert layout.model_workspace.name == "report_review_task7"
    assert (layout.model_workspace / "daily.txt").read_text() == "ready"
    assert not (layout.model_workspace / "records.sqlite").exists()
    assert not (layout.model_workspace / "filesystem_scopes").exists()
    assert layout.record_database == layout.private_state / "records.sqlite"
    assert layout.record_database.is_file()
    assert json.loads(layout.path_map.read_text())["scope_mounts"] == {"reports": "."}


def test_workspace_tool_accepts_cwd_relative_and_absolute_paths(tmp_path):
    initial = tmp_path / "initial"
    scope = initial / "filesystem_scopes/reports"
    scope.mkdir(parents=True)
    (scope / "daily.txt").write_text("ready", encoding="utf-8")
    sqlite3.connect(initial / "records.sqlite").close()
    environment = {
        "schema_version": "2.0",
        "environment_id": "report_review",
        "record_sets": [],
        "filesystem_scopes": [{"scope_id": "reports", "access": "read_only"}],
    }
    layout = create_task_run_layout(
        initial, tmp_path / "case", environment=environment,
        workspace_name="report_review_task7",
    )
    schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string", "x-resource-scope": "reports",
                "x-resource-kind": "file",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    tool = {
        "name": "read_report",
        "usageConditions": {"targetResources": ["reports"], "sideEffects": []},
        "inputSchema": schema,
        "outputSchema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "const": True},
                "data": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string", "x-resource-scope": "reports",
                            "x-resource-kind": "file",
                        },
                        "text": {"type": "string"},
                    },
                    "required": ["path", "text"],
                    "additionalProperties": False,
                },
            },
            "required": ["success", "data"],
            "additionalProperties": False,
        },
        "internal": {"code": (
            "def run(arguments, context):\n"
            "    path = context.scope_root('reports') / arguments['path']\n"
            "    return {'success': True, 'data': {'path': str(path), 'text': path.read_text()}}\n"
        )},
    }
    tools = {"read_report": tool}

    for value in ("daily.txt", str(layout.model_workspace / "daily.txt")):
        record = call_environment_tool(
            "read_report", {"path": value}, tools, layout.model_workspace,
            timeout=10, memory_limit=1024**3, write_limit=1024**2,
            environment=environment, record_database=layout.record_database,
            scope_mounts=layout.scope_mounts,
        )
        assert record["error"] is None
        assert record["result"]["data"] == {"path": "daily.txt", "text": "ready"}

    with pytest.raises(ValueError, match="越出当前工作目录"):
        call_environment_tool(
            "read_report", {"path": str(layout.private_state / "records.sqlite")},
            tools, layout.model_workspace, timeout=10, memory_limit=1024**3,
            write_limit=1024**2, environment=environment,
            record_database=layout.record_database, scope_mounts=layout.scope_mounts,
        )


def test_multi_scope_paths_are_always_workspace_relative(tmp_path):
    workspace = tmp_path / "workspace"
    reports = workspace / "reports"
    reports.mkdir(parents=True)
    (reports / "daily.txt").write_text("ready", encoding="utf-8")
    environment = {
        "filesystem_scopes": [
            {"scope_id": "reports"},
            {"scope_id": "attachments"},
        ]
    }
    catalog = ResourceCatalog(
        environment,
        lambda scope_id: workspace / scope_id,
        workspace_root=workspace,
        scope_mounts={"reports": "reports", "attachments": "attachments"},
    )
    schema = {
        "type": "string",
        "x-resource-scope": "reports",
        "x-resource-kind": "file",
    }

    assert catalog.normalize_arguments(
        "reports/daily.txt", schema=schema, allowed_scopes={"reports"},
    ) == "daily.txt"
    assert catalog.externalize_result(
        "daily.txt", schema=schema, allowed_scopes={"reports"},
    ) == "reports/daily.txt"
    assert catalog.externalize_result(
        str(reports / "daily.txt"), schema=schema, allowed_scopes={"reports"},
    ) == "reports/daily.txt"
    with pytest.raises(ValueError, match="当前工作目录"):
        catalog.normalize_arguments(
            "daily.txt", schema=schema, allowed_scopes={"reports"},
        )


def test_container_runtime_maps_model_visible_absolute_workspace_path(tmp_path):
    public_workspace = tmp_path / "host/task-workspace"
    runtime_workspace = tmp_path / "container/external/0/task-workspace"
    reports = runtime_workspace / "reports"
    reports.mkdir(parents=True)
    (reports / "daily.txt").write_text("ready", encoding="utf-8")
    environment = {"filesystem_scopes": [{"scope_id": "reports"}]}
    catalog = ResourceCatalog(
        environment,
        lambda _scope_id: reports,
        workspace_root=runtime_workspace,
        public_workspace_root=public_workspace,
        scope_mounts={"reports": "reports"},
    )
    schema = {
        "type": "string",
        "x-resource-scope": "reports",
        "x-resource-kind": "file",
    }

    assert catalog.normalize_arguments(
        str(public_workspace / "reports/daily.txt"),
        schema=schema,
        allowed_scopes={"reports"},
    ) == "daily.txt"


def test_workspace_direct_tool_does_not_apply_uid_wide_process_limit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "private-state/records.sqlite"
    database.parent.mkdir()
    sqlite3.connect(database).close()
    environment = {
        "schema_version": "2.0",
        "environment_id": "threaded_tool",
        "record_sets": [],
        "filesystem_scopes": [],
    }
    tool = {
        "name": "run_thread",
        "usageConditions": {"targetResources": [], "sideEffects": []},
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "const": True},
                "thread_completed": {"type": "boolean", "const": True},
            },
            "required": ["success", "thread_completed"],
            "additionalProperties": False,
        },
        "internal": {"code": (
            "def run(arguments, context):\n"
            "    import threading\n"
            "    completed = []\n"
            "    worker = threading.Thread(target=lambda: completed.append(True))\n"
            "    worker.start()\n"
            "    worker.join()\n"
            "    return {'success': True, 'thread_completed': completed == [True]}\n"
        )},
    }

    record = call_environment_tool(
        "run_thread", {}, {"run_thread": tool}, workspace,
        timeout=10, memory_limit=1024**3, write_limit=1024**2,
        process_limit=1, environment=environment, record_database=database,
        scope_mounts={},
    )

    assert record["error"] is None
    assert record["result"] == {"success": True, "thread_completed": True}


def test_workspace_direct_tool_injects_profile_software_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "private-state/records.sqlite"
    database.parent.mkdir()
    sqlite3.connect(database).close()
    software_root = tmp_path / "software-profile"
    software_root.mkdir()
    (software_root / "marker.txt").write_text("profile-ready", encoding="utf-8")
    environment = {
        "schema_version": "2.0",
        "environment_id": "profile_tool",
        "record_sets": [],
        "filesystem_scopes": [],
    }
    tool = {
        "name": "inspect_profile",
        "usageConditions": {"targetResources": [], "sideEffects": []},
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "const": True},
                "value": {"type": "string"},
            },
            "required": ["success", "value"],
            "additionalProperties": False,
        },
        "internal": {"code": (
            "def run(arguments, context):\n"
            "    value = (context.software_root / 'marker.txt').read_text()\n"
            "    return {'success': True, 'value': value}\n"
        )},
    }

    record = call_environment_tool(
        "inspect_profile", {}, {"inspect_profile": tool}, workspace,
        timeout=10, memory_limit=1024**3, write_limit=1024**2,
        environment=environment, record_database=database, scope_mounts={},
        software={"root": str(software_root), "python": sys.executable},
    )

    assert record["error"] is None
    assert record["result"] == {"success": True, "value": "profile-ready"}


def test_workspace_direct_read_only_tool_uses_landlock_without_copy(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "marker.txt"
    marker.write_text("original", encoding="utf-8")
    database = tmp_path / "private-state/records.sqlite"
    database.parent.mkdir()
    sqlite3.connect(database).close()
    environment = {
        "schema_version": "2.0",
        "environment_id": "read_only_direct_tool",
        "record_sets": [],
        "filesystem_scopes": [
            {"scope_id": "reports", "access": "read_only"},
        ],
    }
    tool = {
        "name": "attempt_write",
        "usageConditions": {"targetResources": ["reports"], "sideEffects": []},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "const": True},
                "write_blocked": {"type": "boolean", "const": True},
                "temporary_write": {"type": "boolean", "const": True},
            },
            "required": ["success", "write_blocked", "temporary_write"],
            "additionalProperties": False,
        },
        "internal": {"code": (
            "def run(arguments, context):\n"
            "    import tempfile\n"
            "    blocked = False\n"
            "    try:\n"
            "        (context.workspace_root / 'marker.txt').write_text('changed')\n"
            "    except OSError:\n"
            "        blocked = True\n"
            "    with tempfile.NamedTemporaryFile() as stream:\n"
            "        stream.write(b'ok')\n"
            "        stream.flush()\n"
            "    from pathlib import Path\n"
            "    Path('hx_temp.pt').write_bytes(b'model chunks')\n"
            "    assert Path('hx_temp.pt').read_bytes() == b'model chunks'\n"
            "    with open('/dev/null', 'wb') as sink:\n"
            "        sink.write(b'library diagnostics')\n"
            "    return {'success': True, 'write_blocked': blocked, 'temporary_write': True}\n"
        )},
    }

    with patch("harness.execution.shutil.copytree", side_effect=AssertionError("copied")):
        record = call_environment_tool(
            "attempt_write", {}, {"attempt_write": tool}, workspace,
            timeout=10, memory_limit=1024**3, write_limit=1024**2,
            environment=environment, record_database=database,
            scope_mounts={"reports": "."},
        )

    assert record["error"] is None, record
    assert record["result"] == {
        "success": True,
        "write_blocked": True,
        "temporary_write": True,
    }
    assert marker.read_text(encoding="utf-8") == "original"
    assert not (workspace / "hx_temp.pt").exists()


def test_task_layout_rejects_symlinked_initial_state(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("hidden", encoding="utf-8")
    (initial / "link").symlink_to(outside)

    with pytest.raises(ValueError, match="symbolic links"):
        create_task_run_layout(initial, tmp_path / "run", environment={})


def test_task_layout_rejects_symlink_as_initial_state(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    initial = tmp_path / "initial"
    initial.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic links"):
        create_task_run_layout(initial, tmp_path / "run", environment={})


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

    def fixture_call(_code, _arguments, candidate, *_args, **_kwargs):
        (candidate / "filesystem_scopes/reports/daily.json").write_text(
            "ready", encoding="utf-8"
        )
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


def test_task_mcp_exposes_tools_without_resource_uri_protocol(tmp_path):
    from harness.mcp_protocol import RpcError
    from task_gen.task_eval_mcp import TaskEvalMcpServer

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = TaskEvalMcpServer({
        "workspace_root": str(workspace),
        "trace": str(tmp_path / "trace.jsonl"),
        "max_tool_calls": 10,
        "timeout": 10,
        "memory_limit": 1024 * 1024,
        "write_limit": 1024 * 1024,
        "tools": [],
        "environment": {"filesystem_scopes": []},
    })
    initialized = server.handle({"method": "initialize", "id": 1})
    assert initialized["capabilities"] == {"tools": {"listChanged": False}}
    assert "instructions" not in initialized
    with pytest.raises(RpcError, match="不支持"):
        server.handle({"method": "resources/list", "params": {}, "id": 2})
