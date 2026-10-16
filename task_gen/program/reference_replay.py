"""Replay observed reference calls from a clean state without model inference."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any

from distill.runner import _stop_process_group, _task_mcp_launch, make_server_config
from distill.taskgen_verifier import _verifier_environment
from harness.layout import create_task_run_layout
from harness.mcp_protocol import PROTOCOL_VERSION
from harness.resources import ResourceCatalog
from task_gen.eval_cases import EvalCase

from .utils.io import write_json


def same_json_evidence(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(same_json_evidence(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same_json_evidence(first, second) for first, second in zip(left, right))
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    return type(left) is type(right) and left == right


def same_native_file_evidence(expected: Any, observed: Any) -> bool:
    """Compare file-tool text while ignoring CLI presentation wrappers."""
    if isinstance(expected, dict):
        expected = expected.get("output")
    if isinstance(observed, dict):
        observed = observed.get("output")
    if isinstance(expected, list) and all(isinstance(item, str) for item in expected):
        expected_lines = expected
        observed_lines = observed.splitlines() if isinstance(observed, str) else None
        if observed_lines is not None:
            if expected_lines == observed_lines:
                return True
            if len(expected_lines) == len(observed_lines) and all(
                expected_item.endswith("/" + observed_item)
                for expected_item, observed_item in zip(expected_lines, observed_lines)
            ):
                return True
        expected = "\n".join(expected_lines)
    if not isinstance(expected, str) or not isinstance(observed, str):
        return same_json_evidence(expected, observed)

    def normalize(value: str) -> str:
        lines = value.replace("\r\n", "\n").split("\n")
        lines = [line for line in lines if not line.startswith("<system>")]
        nonempty = [line for line in lines if line]
        if nonempty and all(line.split("\t", 1)[0].isdigit() and "\t" in line for line in nonempty):
            lines = [line.split("\t", 1)[1] for line in lines]
        return "\n".join(lines).rstrip("\n")

    return normalize(expected) == normalize(observed)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_replay(calls: list[dict[str, Any]], actual: list[dict[str, Any]],
                 responses: list[dict[str, Any]]) -> dict[str, Any]:
    expected_ids = {1, 2, *range(3, len(calls) + 3)}
    observed_ids = [response.get("id") for response in responses]
    if len(observed_ids) != len(expected_ids) or set(observed_ids) != expected_ids:
        raise ValueError("reference replay has missing or duplicate MCP responses")
    if any("error" in response for response in responses):
        errors = [{"id": response.get("id"), "error": response["error"]}
                  for response in responses if "error" in response]
        raise ValueError(f"reference replay returned a JSON-RPC error: {errors}")
    return check_call_results(calls, actual)


def check_call_results(calls: list[dict[str, Any]], actual: list[dict[str, Any]]) -> dict[str, Any]:
    if len(actual) != len(calls):
        raise ValueError(f"reference replay recorded {len(actual)} of {len(calls)} calls")
    differences = []
    negative_results = []
    for index, (expected, observed) in enumerate(zip(calls, actual), 1):
        if expected["tool"] != observed.get("tool") or not same_json_evidence(expected.get("arguments", {}), observed.get("arguments", {})):
            raise ValueError(f"reference replay changed tool or arguments at call {index}")
        result_matches = same_json_evidence(observed.get("result"), expected.get("result"))
        expected_negative = (isinstance(expected.get("result"), dict)
                             and expected["result"].get("success") is False and result_matches)
        legacy_annotation = (expected_negative and expected.get("error") is None
                             and observed.get("error") == "工具返回值必须包含 success=true")
        if observed.get("error") != expected.get("error") and not legacy_annotation:
            raise ValueError(f"reference replay changed execution error at call {index}")
        if expected_negative:
            negative_results.append(index)
        if not result_matches:
            differences.append(index)
    return {"call_count": len(actual), "recorded_result_difference_indices": differences,
            "recorded_results_identical": not differences, "expected_negative_result_indices": negative_results}


def reference_public_arguments(catalog: ResourceCatalog, value: Any, *, schema: Any,
                               allowed_scopes: set[str]) -> Any:
    if isinstance(value, dict):
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        return {key: reference_public_arguments(catalog, item, schema=properties.get(key),
                                                allowed_scopes=allowed_scopes) for key, item in value.items()}
    if isinstance(value, list):
        item_schema = schema.get("items") if isinstance(schema, dict) else None
        return [reference_public_arguments(catalog, item, schema=item_schema, allowed_scopes=allowed_scopes) for item in value]
    if isinstance(value, str) and isinstance(schema, dict) and schema.get("x-resource-scope"):
        scope = schema["x-resource-scope"]
        projected = catalog.externalize_result(value, schema=schema, must_exist=False, allowed_scopes=allowed_scopes)
        try:
            normalized = catalog.normalize_arguments(value, schema=schema, allowed_scopes=allowed_scopes)
        except ValueError:
            return projected
        if Path(value).is_absolute() or value == normalized:
            return catalog.externalize_result(normalized, schema=schema, must_exist=False, allowed_scopes=allowed_scopes)
        internal_target = catalog.resolve_relative(scope, value, must_exist=False)
        public_target = catalog.resolve_relative(scope, normalized, must_exist=False)
        if internal_target.exists() and public_target.exists():
            raise ValueError("reference path is ambiguous between public and Scope-relative files")
        return projected if internal_target.exists() else value
    return value


def replay_reference(case: EvalCase, output_root: Path, *, tool_timeout: int = 300) -> dict[str, Any]:
    calls = case.reference_calls
    if not calls:
        raise ValueError("reference replay requires existing reference calls")
    if any(call["tool"] in {"Read", "Grep", "Glob"} for call in calls):
        from .scripted_reference import replay_with_official_cli

        return replay_with_official_cli(case, output_root, tool_timeout=tool_timeout)
    paths = create_task_run_layout(
        case.initial_state, output_root / "execution", environment=case.environment,
        workspace_name=f"{case.task['environment_id']}_{case.task['task_id']}_reference",
    )
    config = make_server_config(case.environment, max_tool_calls=len(calls),
                                timeout=tool_timeout, runtime=case.runtime)
    config.update(workspace_root=str(paths.model_workspace), record_database=str(paths.record_database),
                  scope_mounts=paths.scope_mounts, trace=str(paths.raw / "environment_tool_calls.jsonl"),
                  budget_stop_marker=str(paths.raw / "tool_budget_stop.json"),
                  tool_budget_counter=str(paths.raw / "tool_budget_counter.json"))
    private_server = paths.private_state / "server.json"
    write_json(private_server, config)
    launch = _task_mcp_launch(config, private_server, paths)
    catalog = ResourceCatalog(case.environment,
                              lambda scope: paths.model_workspace / paths.scope_mounts[scope],
                              workspace_root=paths.model_workspace, scope_mounts=paths.scope_mounts)
    tools = {tool["name"]: tool for tool in _verifier_environment(case)["tools"]}
    public_arguments = [reference_public_arguments(
        catalog, call.get("arguments", {}), schema=tools[call["tool"]]["inputSchema"],
        allowed_scopes=set(paths.scope_mounts) & set(tools[call["tool"]].get("usageConditions", {}).get("targetResources", [])),
    ) for call in calls]
    expected_runtime_calls = deepcopy(calls)
    for call, arguments in zip(expected_runtime_calls, public_arguments):
        call["arguments"] = catalog.normalize_arguments(arguments, schema=tools[call["tool"]]["inputSchema"],
                                                        allowed_scopes=set(paths.scope_mounts) & set(tools[call["tool"]].get("usageConditions", {}).get("targetResources", [])))
    write_json(output_root / "reference_argument_projection.json", [
        {"tool": call["tool"], "original_arguments": call.get("arguments", {}),
         "public_arguments": public, "runtime_arguments": runtime["arguments"]}
        for call, public, runtime in zip(calls, public_arguments, expected_runtime_calls)
    ])
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "reference-replay", "version": "1.0"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        *[{"jsonrpc": "2.0", "id": index, "method": "tools/call",
           "params": {"name": call["tool"], "arguments": arguments}}
          for index, (call, arguments) in enumerate(zip(calls, public_arguments), 3)],
    ]
    payload = "".join(json.dumps(request, ensure_ascii=False) + "\n" for request in requests).encode()
    write_json(output_root / "replay_requests.json", requests)
    stdout_path = paths.raw / "reference_replay.stdout.jsonl"
    with stdout_path.open("wb") as stdout, (paths.raw / "reference_replay.stderr.log").open("wb") as stderr:
        process = subprocess.Popen([str(launch.command), *launch.arguments], cwd=paths.model_workspace,
                                   env={**os.environ, **launch.environment}, stdin=subprocess.PIPE,
                                   stdout=stdout, stderr=stderr, start_new_session=os.name == "posix")
        try:
            process.communicate(input=payload, timeout=120 + tool_timeout * len(calls))
        except BaseException:
            _stop_process_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _stop_process_group(process, signal.SIGKILL)
                process.wait()
            raise
    if process.returncode != 0:
        raise RuntimeError(f"reference MCP process exited {process.returncode}; see {paths.raw}")
    actual = _jsonl(paths.raw / "environment_tool_calls.jsonl")
    replay = check_replay(expected_runtime_calls, actual, _jsonl(stdout_path))
    replay.update(backend="direct_mcp_reference", model_inference=False)
    write_json(output_root / "replay.json", replay)
    return replay
