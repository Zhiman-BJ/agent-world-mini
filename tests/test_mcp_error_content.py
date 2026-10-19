from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from env_gen.tool_gen.mcp_server import ToolMcpServer
from harness.mcp_protocol import tool_call_result
from task_gen.task_eval_mcp import TaskEvalMcpServer


SUCCESS_SCHEMA = {
    "type": "object",
    "properties": {"success": {"const": True}, "value": {"type": "integer"}},
    "required": ["success", "value"],
    "additionalProperties": False,
}
PREFIX_ERROR = "路径必须位于 Scope 挂载目录 reports/ 内：daily.json"


@pytest.fixture(params=["task", "delivery"])
def server_factory(request, tmp_path, monkeypatch):
    def create(record, schema=SUCCESS_SCHEMA):
        tool = {
            "name": "inspect", "description": "Inspect a report.",
            "usageConditions": {"sideEffects": []},
            "inputSchema": {"type": "object"}, "outputSchema": deepcopy(schema),
        }
        trace = tmp_path / "calls.jsonl"
        if request.param == "task":
            monkeypatch.setattr("task_gen.task_eval_mcp.call_environment_tool", lambda *args, **kwargs: deepcopy(record))
            server = TaskEvalMcpServer({
                "tools": [tool], "workspace_root": str(tmp_path), "trace": str(trace),
                "max_tool_calls": 10, "timeout": 10, "memory_limit": 2147483648,
                "write_limit": 268435456,
            })
        else:
            runtime = SimpleNamespace(root=tmp_path, resumed=False)
            monkeypatch.setattr("env_gen.tool_gen.mcp_server.ToolRuntime", lambda *args, **kwargs: runtime)
            monkeypatch.setattr("env_gen.tool_gen.mcp_server.call_environment_tool", lambda *args, **kwargs: deepcopy(record))
            delivery = SimpleNamespace(
                runtime={}, software_root=None, python_path=None,
                package=SimpleNamespace(tools=[tool], environment={}),
                binding={"package_id": "fixture", "environment_id": "fixture"},
            )
            server = ToolMcpServer(delivery, trace_path=trace)
        return server, trace

    return create


def call(server):
    return server.handle({"method": "tools/call", "params": {"name": "inspect", "arguments": {}}})


def test_success_wire_shape_is_unchanged(server_factory):
    payload = {"success": True, "value": 17}
    server, _ = server_factory({"result": payload, "error": None})
    assert call(server) == {"content": [], "structuredContent": payload, "isError": False}


@pytest.mark.parametrize("failure_kind", ["invalid_input", "timeout", "output_schema_error"])
def test_runtime_error_survives_success_only_schema(server_factory, failure_kind):
    record = {"tool": "inspect", "arguments": {}, "result": None,
              "error": PREFIX_ERROR, "failure_kind": failure_kind}
    server, trace = server_factory(record)
    result = call(server)
    assert result["isError"] is True
    assert "structuredContent" not in result
    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "text"
    payload = json.loads(result["content"][0]["text"])
    assert payload["success"] is False
    assert payload["error"]["message"] == PREFIX_ERROR
    recorded = json.loads(trace.read_text().strip())
    assert recorded["result"] is None or recorded["result"]["error"]["message"] == PREFIX_ERROR
    assert (recorded.get("error") or recorded.get("runtime_error")) == PREFIX_ERROR
    if "tool_result" in payload:
        assert payload["tool_result"] is None
        assert payload["error"]["retryable"] is (failure_kind == "timeout")
        assert recorded == record


def test_schema_defined_business_failure_stays_structured(server_factory):
    payload = {"success": False, "error": {"code": "not_found", "message": "Report not found."}}
    schema = {"type": "object", "oneOf": [SUCCESS_SCHEMA, {
        "type": "object", "properties": {"success": {"const": False}, "error": {"type": "object"}},
        "required": ["success", "error"], "additionalProperties": False,
    }]}
    server, _ = server_factory({"result": payload, "error": "工具返回值必须包含 success=true"}, schema)
    assert call(server) == {"content": [], "structuredContent": payload, "isError": True}


def test_permissive_error_schema_keeps_existing_structured_shape(server_factory):
    server, _ = server_factory({"result": None, "error": PREFIX_ERROR}, {"type": "object"})
    result = call(server)
    assert result["isError"] is True
    assert result["content"] == []
    assert result["structuredContent"]["error"]["message"] == PREFIX_ERROR


def test_invalid_business_output_is_visible_without_relaxing_schema(server_factory):
    payload = {"success": False, "unexpected": "wrong shape"}
    server, _ = server_factory({"result": payload, "error": "Invalid business output", "failure_kind": "schema"})
    result = call(server)
    assert result["isError"] is True
    assert "structuredContent" not in result
    assert "wrong shape" in result["content"][0]["text"]


def test_success_cannot_bypass_structured_output():
    with pytest.raises(ValueError, match="Successful tool results"):
        tool_call_result({"success": True}, is_error=False, structured=False)


def test_text_error_keeps_unicode_and_one_payload():
    payload = {"success": False, "error": {"message": PREFIX_ERROR}}
    result = tool_call_result(payload, is_error=True, structured=False)
    assert json.loads(result["content"][0]["text"]) == payload
    assert PREFIX_ERROR in result["content"][0]["text"]
    assert "structuredContent" not in result
