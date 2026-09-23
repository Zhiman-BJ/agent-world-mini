import json


def _jsonl(path, records):
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_parse_responses_stream_preserves_reasoning_and_text(tmp_path):
    from distill.export import _parse_sse

    stream = tmp_path / "response.sse"
    stream.write_text(
        'event: response.reasoning_summary_text.delta\n'
        'data: {"type":"response.reasoning_summary_text.delta","delta":"inspect"}\n\n'
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":"done"}\n\n'
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{"status":"completed"}}\n\n',
        encoding="utf-8",
    )

    assert _parse_sse(stream) == ("inspect", "done")


def test_compaction_links_before_and_after_model_contexts(tmp_path):
    from distill.export import export_trajectory, validate_trajectory

    raw = tmp_path / "raw"
    io = raw / "model_io"
    io.mkdir(parents=True)
    _jsonl(raw / "wire.jsonl", [
        {"type": "compaction.started", "trigger": "auto"},
        {"type": "llm.request", "kind": "compaction", "turnStep": "0.2"},
        {"type": "context.apply_compaction", "summary": "retained facts", "compactedCount": 4},
        {"type": "compaction.completed", "result": {"summary": "retained facts", "tokensBefore": 900, "tokensAfter": 100, "compactedCount": 4}},
        {"type": "context.append_loop_event", "event": {"type": "step.begin", "uuid": "s3", "turnId": "0", "step": 3}},
        {"type": "llm.request", "kind": "loop", "turnStep": "0.3"},
        {"type": "context.append_loop_event", "event": {"type": "content.part", "uuid": "p3", "turnId": "0", "stepUuid": "s3", "part": {"type": "text", "text": "done"}}},
        {"type": "context.append_loop_event", "event": {"type": "step.end", "uuid": "s3", "turnId": "0", "step": 3, "finishReason": "end_turn"}},
    ])
    _jsonl(raw / "model_requests.jsonl", [
        {"request_id": 1, "kind": "compaction", "turn_id": 0, "step": 2, "wire_line": 2, "request": {"model": "kimi-k3", "messages": [{"role": "assistant", "content": "full history"}], "stream": True}},
        {"request_id": 2, "kind": "agent_step", "turn_id": 0, "step": 3, "wire_line": 6, "request": {"model": "kimi-k3", "messages": [{"role": "user", "content": "retained facts"}], "stream": True}},
    ])
    (io / "000001.response.sse").write_text(
        'data: {"choices":[{"delta":{"content":"retained facts"}}]}\n\ndata: [DONE]\n\n',
        encoding="utf-8",
    )
    (io / "000002.response.sse").write_text(
        'data: {"choices":[{"delta":{"reasoning_content":"continue","content":"done"}}]}\n\ndata: [DONE]\n\n',
        encoding="utf-8",
    )
    _jsonl(raw / "model_responses.jsonl", [
        {"request_id": 1, "status": 200, "body_path": "raw/model_io/000001.response.sse"},
        {"request_id": 2, "status": 200, "body_path": "raw/model_io/000002.response.sse"},
    ])
    _jsonl(raw / "environment_tool_calls.jsonl", [])
    _jsonl(raw / "result_reads.jsonl", [])
    (raw / "kimi_session.zip").write_bytes(b"session")
    (raw / "run_result.json").write_text(json.dumps({
        "reason": "completed", "answer": "done", "usage": {},
        "model": {
            "alias": "evaluation",
            "upstream_id": "kimi-k3",
            "reasoning_effort": "high",
        },
    }), encoding="utf-8")

    trajectory = export_trajectory(
        raw,
        tmp_path / "trajectory.json",
        task={"prompt": "task"},
        environment={"tool_names": []},
    )
    validate_trajectory(trajectory)
    assert trajectory["model"]["reasoning_effort"] == "high"
    compaction = trajectory["compactions"][0]
    assert compaction["summary"] == "retained facts"
    assert compaction["tokens_before"] == 900
    assert compaction["before_context"]["path"] == "raw/model_requests.jsonl"
    assert compaction["after_context"]["line"] == 2
    assert compaction["model_exchanges"][0]["request_id"] == 1
    assert trajectory["reasoning_preservation"]["context_snapshots"] is True
