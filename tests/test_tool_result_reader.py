import json

from env_gen.tool_gen.mcp_protocol import tool_call_result


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
    assert [tool["name"] for tool in adapter.handle({"method": "tools/list"})["tools"]] == ["inspect"]
    result = adapter.handle({"method": "tools/call", "params": {"name": "inspect", "arguments": {}}})
    assert result["structuredContent"] == payload


def test_tool_result_does_not_repeat_full_structured_content_in_text():
    payload = {"success": True, "data": "x" * 10_000}
    result = tool_call_result(payload, is_error=False)
    text = result["content"][0]["text"]
    assert text.startswith("Tool result: success.")
    assert len(text) < 500
    assert "x" * 1000 not in text
    assert result["structuredContent"] == payload
