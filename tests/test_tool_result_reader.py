import json

from harness.mcp_protocol import tool_call_result


def test_kimi_adapter_keeps_small_result_and_does_not_register_reader():
    from task_gen.task_eval_kimi_mcp import KimiMcpAdapter

    payload = {"success": True, "data": "x" * 1_000}

    class Server:
        def handle(self, request):
            if request["method"] == "tools/list":
                return {"tools": [{
                    "name": "inspect",
                    "description": "Inspect",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object"},
                "usageConditions": {"sideEffects": []},
                }]}
            return {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": payload,
                "isError": False,
            }

    adapter = KimiMcpAdapter(Server())
    listed = adapter.handle({"method": "tools/list"})["tools"]
    assert [tool["name"] for tool in listed] == ["inspect"]
    assert listed[0]["description"] == "Inspect"
    assert listed[0]["outputSchema"] == {"type": "object"}
    result = adapter.handle({"method": "tools/call", "params": {"name": "inspect", "arguments": {}}})
    assert result["structuredContent"] == payload


def test_kimi_adapter_rejects_oversized_read_only_result_before_model_context():
    from task_gen.task_eval_kimi_mcp import KimiMcpAdapter

    payload = {"success": True, "data": "x" * 100}

    class Server:
        config = {"max_visible_result_chars": 64}
        tools = {"inspect": {"usageConditions": {"sideEffects": []}}}

        def handle(self, request):
            return tool_call_result(payload, is_error=False)

    result = KimiMcpAdapter(Server()).handle({
        "id": 1,
        "method": "tools/call",
        "params": {"name": "inspect", "arguments": {}},
    })
    assert result["isError"] is True
    assert result["content"][0]["type"] == "text"
    error = json.loads(result["content"][0]["text"])["error"]
    assert error["code"] == "result_too_large_for_context"
    assert error["serialized_chars"] > 64


def test_kimi_adapter_does_not_hide_oversized_mutating_result():
    from task_gen.task_eval_kimi_mcp import KimiMcpAdapter

    payload = {"success": True, "data": "x" * 100}

    class Server:
        config = {"max_visible_result_chars": 64}
        tools = {"mutate": {"usageConditions": {"sideEffects": ["writes"]}}}

        def handle(self, request):
            return tool_call_result(payload, is_error=False)

    result = KimiMcpAdapter(Server()).handle({
        "id": 1,
        "method": "tools/call",
        "params": {"name": "mutate", "arguments": {}},
    })
    assert result["structuredContent"] == payload


def test_tool_result_uses_only_structured_content_for_kimi():
    payload = {"success": True, "data": "x" * 10_000}
    result = tool_call_result(payload, is_error=False)
    assert result["content"] == []
    assert result["structuredContent"] == payload
    assert result["isError"] is False


def test_error_tool_result_uses_only_structured_content_for_kimi():
    payload = {"success": False, "error": {"code": "not_found"}}
    result = tool_call_result(payload, is_error=True)
    assert result == {
        "content": [],
        "structuredContent": payload,
        "isError": True,
    }


def test_kimi_adapter_records_actual_mcp_result(tmp_path):
    from task_gen.task_eval_kimi_mcp import KimiMcpAdapter

    expected = tool_call_result({"success": True, "data": {"value": 17}}, is_error=False)

    class Server:
        trace = tmp_path / "environment_tool_calls.jsonl"

        def handle(self, request):
            return expected

    request = {"id": 23, "method": "tools/call", "params": {"name": "inspect", "arguments": {}}}
    assert KimiMcpAdapter(Server()).handle(request) is expected
    evidence = json.loads((tmp_path / "mcp_responses.jsonl").read_text())
    assert evidence == {"request_id": 23, "tool": "inspect", "result": expected}
