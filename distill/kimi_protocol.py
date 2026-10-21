"""Audit the recorded Kimi Code Chat requests without changing model output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _evidence(execution: Path, relative: str) -> Path:
    path = (execution / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(execution.resolve()):
        raise ValueError("Kimi protocol evidence must remain inside the execution")
    return path


def _assistant(path: Path) -> tuple[dict, str | None, bool]:
    reasoning, content, tools = [], [], {}
    finish, done = None, False
    for line in path.read_text().splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            done = True
            continue
        for choice in json.loads(payload).get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            finish = choice.get("finish_reason") or finish
            delta = choice.get("delta", {})
            reasoning.append(delta.get("reasoning_content") or "")
            content.append(delta.get("content") or "")
            for fragment in delta.get("tool_calls", []):
                tool = tools.setdefault(fragment["index"], {
                    "id": "", "type": "function", "function": {"name": "", "arguments": ""},
                })
                tool["id"] += fragment.get("id") or ""
                for field in ("name", "arguments"):
                    tool["function"][field] += fragment.get("function", {}).get(field) or ""
    message = {"role": "assistant", "content": "".join(content),
               "reasoning_content": "".join(reasoning)}
    if tools:
        message["tool_calls"] = [tools[index] for index in sorted(tools)]
    return message, finish, done


def _text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(part["text"] for part in content if part.get("type") == "text")


def _tool_ids(messages: list[dict], tools: list[dict]) -> dict[str, str]:
    raw_ids = list(dict.fromkeys([
        tool["id"] for message in messages for tool in message.get("tool_calls", [])
    ] + [tool["id"] for tool in tools]))
    mapped = {identifier: identifier for identifier in raw_ids
              if identifier and len(identifier) <= 64 and re.fullmatch(r"[a-zA-Z0-9_-]+", identifier)}
    used = set(mapped.values())
    for identifier in raw_ids:
        if identifier in mapped:
            continue
        base = re.sub(r"[^a-zA-Z0-9_-]", "_", identifier)[:64] or "tool_call"
        candidate, suffix = base, 2
        while candidate in used:
            ending = f"_{suffix}"
            candidate = base[:64 - len(ending)] + ending
            suffix += 1
        mapped[identifier] = candidate
        used.add(candidate)
    return mapped


def _same_tools(expected: list[dict], retained: list[dict], history: list[dict]) -> tuple[int, int]:
    if len(expected) != len(retained):
        raise ValueError("Kimi changed the number of tool calls")
    mapped = _tool_ids(history, expected)
    normalized_ids, reformatted_args = 0, 0
    for before, after in zip(expected, retained):
        if (after.get("id") != mapped[before["id"]] or after.get("type") != before["type"]
                or after.get("function", {}).get("name") != before["function"]["name"]
                or json.dumps(json.loads(after["function"]["arguments"]), sort_keys=True)
                != json.dumps(json.loads(before["function"]["arguments"]), sort_keys=True)):
            raise ValueError("Kimi changed a tool name, argument value, or call identifier mapping")
        normalized_ids += before["id"] != after["id"]
        reformatted_args += before["function"]["arguments"] != after["function"]["arguments"]
    return normalized_ids, reformatted_args


def check_kimi_chat_protocol(execution: Path, protocol: dict) -> dict:
    requests = _records(execution / "raw/model_requests.jsonl")
    responses = {row["request_id"]: row for row in _records(execution / "raw/model_responses.jsonl")}
    if not requests or protocol["provider_type"] != "kimi":
        raise ValueError("Expected recorded native Kimi Chat requests")
    history_pairs, reasoning_pairs, tool_pairs = 0, 0, 0
    normalized_ids, reformatted_args = 0, 0
    incomplete, truncated, compaction_boundaries = [], [], []
    previous = None
    for record in requests:
        request = record["request"]
        evidence = _evidence(execution, record["raw_path"])
        if (hashlib.sha256(evidence.read_bytes()).hexdigest() != record["sha256"]
                or json.loads(evidence.read_text()) != request):
            raise ValueError("Recorded Kimi request differs from raw evidence")
        if (not record["path"].endswith("/chat/completions")
                or request.get("model") != protocol["model"]
                or request.get("stream") is not True
                or request.get("reasoning_effort") != protocol["reasoning_effort"]
                or type(request.get("max_completion_tokens")) is not int
                or not 0 < request["max_completion_tokens"] <= protocol["max_output_tokens"]
                or any(field in request for field in ("thinking", "max_tokens", "max_output_tokens"))):
            raise ValueError("Kimi request differs from the native K3 protocol")
        headers = record.get("protocol_headers")
        if (not isinstance(headers, dict) or not headers.get("user-agent")
                or "codex" in headers["user-agent"].lower()
                or "originator" in headers or "openai-beta" in headers):
            raise ValueError("Native Kimi request has missing or unexpected client headers")
        if previous is not None:
            previous_record, assistant = previous
            if record.get("kind") == "compaction" or previous_record.get("kind") == "compaction":
                compaction_boundaries.append(record["request_id"])
            else:
                messages = request["messages"]
                if messages[:len(previous_record["request"]["messages"])] != previous_record["request"]["messages"]:
                    raise ValueError("Kimi changed retained history outside a recorded compaction")
                appended = messages[len(previous_record["request"]["messages"]):]
                retained = next((message for message in appended if message.get("role") == "assistant"), None)
                if (retained is None or _text(retained.get("content")) != assistant["content"]
                        or retained.get("reasoning_content", "") != assistant["reasoning_content"]):
                    raise ValueError("Kimi did not retain the complete previous assistant message")
                changed_ids, changed_args = _same_tools(
                    assistant.get("tool_calls", []), retained.get("tool_calls", []),
                    previous_record["request"]["messages"])
                normalized_ids += changed_ids
                reformatted_args += changed_args
                history_pairs += 1
                reasoning_pairs += bool(assistant["reasoning_content"])
                for tool in retained.get("tool_calls", []):
                    results = [message for message in appended if message.get("role") == "tool"
                               and message.get("tool_call_id") == tool["id"]]
                    if len(results) != 1:
                        raise ValueError("Kimi tool result is missing or duplicated in the next request")
                    tool_pairs += 1
        previous = None
        response = responses.get(record["request_id"], {})
        if response.get("status") != 200 or response.get("error") or not response.get("body_path"):
            incomplete.append(record["request_id"])
            continue
        assistant, finish, done = _assistant(_evidence(execution, response["body_path"]))
        if finish == "length":
            truncated.append(record["request_id"])
        if not done or finish not in {"stop", "tool_calls"}:
            incomplete.append(record["request_id"])
            continue
        previous = record, assistant
    return {"request_count": len(requests), "history_pairs_checked": history_pairs,
            "reasoning_pairs_checked": reasoning_pairs, "tool_result_pairs_checked": tool_pairs,
            "cli_normalized_tool_ids": normalized_ids, "cli_reformatted_json_arguments": reformatted_args,
            "compaction_boundaries_not_compared": compaction_boundaries,
            "incomplete_response_ids": incomplete, "truncated_response_ids": truncated,
            "user_agents": sorted({record["protocol_headers"]["user-agent"] for record in requests}),
            "model_requests_sha256": hashlib.sha256(
                (execution / "raw/model_requests.jsonl").read_bytes()).hexdigest()}
