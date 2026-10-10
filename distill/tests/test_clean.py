import json


def _trajectory():
    return {
        "schema_version": "agent_world.kimi_trajectory.v1",
        "trajectory_id": "trajectory-1",
        "created_at": "2026-10-02T00:00:00+00:00",
        "model": {},
        "task": {
            "task_id": "task1",
            "task_text": "核查器件结果。",
            "prompt": json.dumps({
                "role": "Use tools.",
                "task": "核查器件结果。",
                "environment": {"record_sets": [{"many": "fields"}]},
            }, ensure_ascii=False),
        },
        "environment": {
            "environment_id": "environment-1",
            "name": "器件环境",
            "summary": "用于器件核查。",
            "description": "包含仿真记录，但不代表制造签核。",
            "record_sets": [{"many": "fields"}],
            "relationships": [{"many": "joins"}],
            "filesystem_scopes": [{"many": "files"}],
            "tool_names": ["inspect", "generate_solution_driven_background_mesh"],
        },
        "steps": [{
            "index": 0, "turn_id": 0, "step": 1,
            "reasoning": {"text": "Call mcp__agent_world_distill__inspect.", "event_sequences": []},
            "assistant_text": "",
            "tool_calls": [{
                "call_id": "mcp__agent_world_distill__inspect_0",
                "name": "mcp__agent_world_distill__inspect",
                "tool_kind": "environment_mcp", "arguments": {},
                "output": {"output": (
                    "Tool result: success. structuredContent fields: data, success.\n"
                    "<mcp-result-extras>\n"
                    '{"structuredContent":{"success":true,"data":{"value":17}}}\n'
                    "</mcp-result-extras>"
                )},
                "is_error": False,
            }, {
                "call_id": "long_0",
                "name": "mcp__agent_world_distill__generate_solution_driven_back_280aead6",
                "tool_kind": "environment_mcp", "arguments": {}, "output": {}, "is_error": False,
            }, {
                "call_id": "support_0",
                "name": "mcp__agent_world_distill__read_tool_result",
                "tool_kind": "harness_support", "arguments": {}, "output": {}, "is_error": False,
            }],
            "model_exchanges": [], "retries": [], "finish": None,
        }],
        "compactions": [],
        "outcome": {"status": "completed", "final_answer": "done", "usage": None, "evaluation": None},
        "reasoning_preservation": {
            "thinking_deltas": True, "raw_model_responses": True,
            "model_input_contexts": True, "context_snapshots": False, "session_export": True,
        },
        "artifacts": [],
    }


def test_clean_trajectory_compacts_environment_and_canonicalizes_tools():
    from distill.clean import clean_trajectory

    trajectory = _trajectory()
    record = {
        "request_id": 1,
        "request": {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Native `Read`, `Grep`, and `Glob` are only for files in the empty model workspace,\n"
                        "the current session's saved long tool results, and the current session's wire log.\n"
                        "Do not use them to search the environment Scope, `execution-state`, delivery\n"
                        "directories, host paths, or another task. Use the environment MCP tools to inspect\n"
                        "and modify business state; a long MCP result can be recovered from the path Kimi\n"
                        "provides in the current session.\n\n"
                        "OFFICIAL BASE\nThe current working directory is "
                        "`/data1/agent_world/aw-data/run/task/model-workspace`; treat it as the project root."
                    ),
                },
                {"role": "user", "content": trajectory["task"]["prompt"]},
            ],
            "tools": [
                {"type": "function", "function": {
                    "name": "mcp__agent_world_distill__inspect",
                    "description": "Inspect.\nTarget resources: records\nOutput contract: {\"type\":\"object\"}",
                }},
                {"type": "function", "function": {"name": "mcp__agent_world_distill__generate_solution_driven_back_280aead6"}},
            ],
        },
    }
    cleaned, model_input = clean_trajectory(
        trajectory,
        record,
        source="/source/trajectory.json",
        environment_context="这是面向器件结果核查的受控仿真环境，包含可追溯的模型、运行记录与派生产物；环境不代表真实制造签核。",
    )

    assert set(cleaned["environment"]) == {
        "environment_id", "name", "description", "tool_names", "support_tool_names",
    }
    assert "record_sets" not in cleaned["task"]["prompt"]
    assert "核查器件结果" in cleaned["task"]["prompt"]
    assert not cleaned["task"]["prompt"].lstrip().startswith("{")
    assert [call["name"] for call in cleaned["steps"][0]["tool_calls"]] == [
        "inspect", "generate_solution_driven_background_mesh", "read_tool_result",
    ]
    assert cleaned["environment"]["support_tool_names"] == ["read_tool_result"]
    assert "mcp__agent_world_distill__" not in json.dumps(cleaned, ensure_ascii=False)
    assert [tool["function"]["name"] for tool in model_input["request"]["tools"]] == [
        "inspect", "generate_solution_driven_background_mesh",
    ]
    assert cleaned["steps"][0]["tool_calls"][0]["call_id"] == "inspect_0"
    assert cleaned["steps"][0]["tool_calls"][0]["output"] == {
        "success": True, "data": {"value": 17},
    }
    assert model_input["request"]["tools"][0]["function"]["description"] == "Inspect."
    system = model_input["request"]["messages"][0]["content"]
    assert system.startswith("OFFICIAL BASE")
    assert "The current working directory is `/workspace`" in system
    assert "/data1/agent_world/aw-data" not in system


def test_clean_string_normalizes_kimi_session_root_without_losing_result_path():
    from distill.clean import _clean_string

    source = (
        "/tmp/agent-world-kimi-home-abc123/sessions/wd_model-workspace_deadbeef/"
        "session_01234567-89ab-cdef-0123-456789abcdef/agents/main/tool-results/result.txt"
    )
    assert _clean_string(source, {}) == "/session/agents/main/tool-results/result.txt"


def test_cleaning_uses_case_scoped_workspace_and_places_context_before_task():
    from distill.clean import _clean_string, cleaned_prompt

    runtime = "/data1/agent_world/runs/environment__task2/model-workspace"
    portable = "/environment__task2/model-workspace"
    value = (
        f"The current working directory is `{runtime}`. "
        f"Read `{runtime}/notes.txt`."
    )
    assert _clean_string(value, {}, (runtime,), portable) == (
        f"The current working directory is `{portable}`. "
        f"Read `{portable}/notes.txt`."
    )
    task_root = runtime.removesuffix("/model-workspace")
    assert _clean_string(task_root + "/wire.jsonl", {}, (runtime,), portable) == (
        "/environment__task2/wire.jsonl"
    )
    prompt = cleaned_prompt(
        {"task_text": "请核查结果。"},
        "工作区保存了完成核查所需的原始记录和派生结果，来源与限制均可复核。",
    )
    assert prompt.startswith("工作区保存")
    assert prompt.endswith("请核查结果。")


def test_cleaning_can_use_original_task_text_without_generated_context():
    from distill.clean import cleaned_prompt

    assert cleaned_prompt({"task_text": "请核查结果。"}) == "请核查结果。"


def test_cleaning_injects_repaired_environment_tool_descriptions():
    from distill.clean import clean_trajectory

    trajectory = _trajectory()
    request = {
        "request_id": 1,
        "request": {
            "messages": [
                {"role": "system", "content": "OFFICIAL BASE"},
                {"role": "user", "content": trajectory["task"]["prompt"]},
            ],
            "tools": [
                {"type": "function", "function": {
                    "name": "mcp__agent_world_distill__inspect",
                    "description": "Inspect.\nOutput contract: oversized",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "object", "properties": {
                                "ids": {"type": "array", "items": {"type": "string"}},
                            }},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                }},
                {"type": "function", "function": {
                    "name": "mcp__agent_world_distill__generate_solution_driven_back_280aead6",
                    "description": "Generate mesh.",
                    "parameters": {"type": "object", "properties": {}},
                }},
                {"type": "function", "function": {
                    "name": "Read", "description": "Native read.",
                    "parameters": {"type": "object", "properties": {}},
                }},
            ],
        },
    }
    repaired = [
        {
            "name": "inspect",
            "description": "按查询条件检查器件记录并返回可复核结果。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "object",
                        "description": "用于缩小检查范围的查询条件。",
                        "properties": {
                            "ids": {
                                "type": "array",
                                "description": "需要检查的记录标识列表。",
                                "items": {
                                    "type": "string",
                                    "description": "单个记录的稳定标识。",
                                },
                            },
                        },
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "generate_solution_driven_background_mesh",
            "description": "根据解场生成背景网格。",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]

    _, cleaned_input = clean_trajectory(
        trajectory,
        request,
        source="/source/trajectory.json",
        environment_context="这是完成器件核查所需的受控环境说明，包含可复核的原始记录、运行状态和派生结果边界。",
        tool_definitions=repaired,
    )

    tools = {
        item["function"]["name"]: item["function"]
        for item in cleaned_input["request"]["tools"]
    }
    assert tools["inspect"]["description"] == repaired[0]["description"]
    assert tools["inspect"]["parameters"] == repaired[0]["inputSchema"]
    assert tools["inspect"]["parameters"]["properties"]["query"]["description"]
    assert tools["inspect"]["parameters"]["properties"]["query"]["properties"]["ids"]["items"]["description"]
    assert tools["Read"]["description"] == "Native read."


def test_repaired_descriptions_do_not_replace_historical_schema_constraints():
    from distill.clean import apply_repaired_tool_definitions

    request = {"tools": [{"type": "function", "function": {
        "name": "inspect",
        "description": "Old.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string", "minLength": 2}},
            "required": ["value"],
        },
    }}]}
    repaired = [{
        "name": "inspect",
        "description": "检查指定值并返回结果。",
        "inputSchema": {
            "type": "object",
            "properties": {"value": {"description": "需要检查的原始值。"}},
            "required": ["value"],
        },
    }]

    apply_repaired_tool_definitions(request, repaired, expected_names={"inspect"})

    value = request["tools"][0]["function"]["parameters"]["properties"]["value"]
    assert value == {
        "type": "string",
        "minLength": 2,
        "description": "需要检查的原始值。",
    }


def test_visualizer_prefers_cleaned_input(tmp_path):
    from distill.visualize import render_trajectory_html

    trajectory = _trajectory()
    source = tmp_path / "trajectory.json"
    source.write_text(json.dumps(trajectory), encoding="utf-8")
    (tmp_path / "input.json").write_text(json.dumps({
        "request": {"messages": [{"role": "user", "content": "CLEANED INPUT"}]}
    }), encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "model_requests.jsonl").write_text(json.dumps({
        "request": {"messages": [{"role": "user", "content": "RAW INPUT"}]}
    }) + "\n", encoding="utf-8")

    html = render_trajectory_html(source, tmp_path / "trajectory.html").read_text()
    assert "CLEANED INPUT" in html
    assert "RAW INPUT" not in html


def test_clean_trajectory_normalizes_mcp_error_output():
    from distill.clean import _normalize_mcp_output

    assert _normalize_mcp_output({
        "output": '{"success":false,"error":{"code":"not_found"}}',
        "isError": True,
    }) == {"success": False, "error": {"code": "not_found"}}


def test_native_read_output_removes_mcp_wrapper_lines():
    from distill.clean import _normalize_tool_call_output

    output = _normalize_tool_call_output({
        "tool_kind": "native_file_tool",
        "output": {
            "output": (
                "1\tTool result: success. success=true.\n"
                "2\t<mcp-result-extras>\n"
                '3\t{"structuredContent":{"success":true}}\n'
                "4\t</mcp-result-extras>"
            ),
            "note": "four lines read",
        },
    })
    assert output == {
        "output": '3\t{"structuredContent":{"success":true}}',
        "note": "four lines read",
    }


def test_clean_trajectory_preserves_compaction_evidence():
    from distill.clean import clean_trajectory

    trajectory = _trajectory()
    trajectory["compactions"] = [{
        "index": 0,
        "trigger": "auto",
        "started_event_sequence": 10,
        "completed_event_sequence": 12,
        "status": "completed",
        "summary": "保留的压缩摘要",
        "before_context": {
            "path": "raw/model_requests.jsonl", "line": 2, "sha256": "a" * 64,
        },
        "after_context": {
            "path": "raw/model_requests.jsonl", "line": 3, "sha256": "b" * 64,
        },
        "model_exchanges": [{
            "request_id": 2,
            "kind": "compaction",
            "request": {
                "path": "raw/model_requests.jsonl", "line": 2, "sha256": "a" * 64,
            },
            "response": {
                "path": "raw/model_responses.jsonl", "line": 2, "sha256": "c" * 64,
            },
        }],
    }]
    expected = json.loads(json.dumps(trajectory["compactions"]))
    record = {
        "request_id": 1,
        "request": {
            "messages": [
                {"role": "system", "content": "OFFICIAL BASE"},
                {"role": "user", "content": trajectory["task"]["prompt"]},
            ],
            "tools": [],
        },
    }

    cleaned, _ = clean_trajectory(
        trajectory,
        record,
        source="/source/trajectory.json",
        environment_context="这是完成器件核查所需的受控环境说明，包含可复核的原始记录、运行状态和派生结果边界。",
    )

    assert cleaned["compactions"] == expected


def test_clean_trajectory_preserves_model_exchange_and_artifact_evidence():
    from distill.clean import clean_trajectory

    trajectory = _trajectory()
    trajectory["steps"][0]["model_exchanges"] = [{
        "request_id": 3,
        "kind": "agent_step",
        "request": {"path": "raw/model_requests.jsonl", "line": 3, "sha256": "a" * 64},
        "response": {"path": "raw/model_responses.jsonl", "line": 3, "sha256": "b" * 64},
    }]
    trajectory["artifacts"] = [{
        "path": "raw/model_responses.jsonl", "sha256": "b" * 64, "bytes": 123,
    }]
    expected_exchanges = json.loads(json.dumps(trajectory["steps"][0]["model_exchanges"]))
    expected_artifacts = json.loads(json.dumps(trajectory["artifacts"]))
    expected_preservation = json.loads(json.dumps(trajectory["reasoning_preservation"]))
    request = {
        "request_id": 1,
        "request": {
            "messages": [
                {"role": "system", "content": "OFFICIAL BASE"},
                {"role": "user", "content": trajectory["task"]["prompt"]},
            ],
            "tools": [],
        },
    }

    cleaned, _ = clean_trajectory(
        trajectory,
        request,
        source="/source/trajectory.json",
    )

    assert cleaned["steps"][0]["model_exchanges"] == expected_exchanges
    assert cleaned["artifacts"] == expected_artifacts
    assert cleaned["reasoning_preservation"] == expected_preservation


def test_first_request_record_falls_back_to_legacy_visualization(tmp_path):
    from distill.clean import first_request_record_from_source

    expected = {"request_id": 7, "request": {"messages": [], "tools": []}}
    (tmp_path / "trajectory.html").write_text(
        '<script id="initial-request-data" type="application/json">'
        + json.dumps(expected)
        + "</script>",
        encoding="utf-8",
    )

    assert first_request_record_from_source(tmp_path) == expected


def test_cleaning_preserves_historical_unavailable_tool_attempts():
    from distill.clean import clean_trajectory

    trajectory = _trajectory()
    trajectory["steps"][0]["tool_calls"].append({
        "call_id": "bash_0",
        "name": "Bash",
        "tool_kind": "environment_mcp",
        "arguments": {"command": "pwd"},
        "output": {"output": "/workspace"},
        "is_error": False,
    })
    request = {
        "request_id": 1,
        "request": {
            "messages": [
                {"role": "system", "content": "OFFICIAL BASE"},
                {"role": "user", "content": trajectory["task"]["prompt"]},
            ],
            "tools": [],
        },
    }

    cleaned, cleaned_input = clean_trajectory(
        trajectory,
        request,
        source="/source/trajectory.json",
    )

    assert cleaned["steps"][0]["tool_calls"][-1]["name"] == "Bash"
    assert cleaned_input["request"]["tools"] == []
