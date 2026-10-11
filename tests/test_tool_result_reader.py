import json

from harness.mcp_protocol import tool_call_result


def test_kimi_adapter_keeps_long_result_and_does_not_register_reader():
    from task_gen.task_eval_kimi_mcp import KimiMcpAdapter

    payload = {"success": True, "data": "x" * 100_000}

    class Server:
        def handle(self, request):
            if request["method"] == "tools/list":
                return {"tools": [{
                    "name": "inspect",
                    "description": "Inspect",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object"},
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
