from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from task_gen.program.utils.io import read_json, write_json


def reference_task():
    text = "Choose one eligible candidate and record the selection in the current review cycle."
    return {"environment_id": "candidate_review_v2", "task_id": "original_001",
            "task_text": text, "task_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "reference": {"answer": "Original answer", "tool_calls": [
                {"tool": "list_candidates", "arguments": {}, "result": {"success": True, "value": 7}}]}}


def test_direct_reference_replay_uses_runtime_and_isolated_state(tmp_path, monkeypatch):
    import sys
    from env_gen.tool_gen.runtime_launch import StdioLaunch
    from task_gen.eval_cases import EvalCase
    from task_gen.program import reference_replay as validation

    initial = tmp_path / "initial"
    initial.mkdir()
    original = reference_task()
    original["reference"]["tool_calls"][0]["error"] = None
    tool = {"name": "list_candidates", "inputSchema": {}, "outputSchema": {},
            "internal": {"code": "def run(arguments, ctx): return {'success': True, 'value': 7}"}}
    case = EvalCase(tmp_path, original, {"environment_id": "env", "tools": [tool]}, initial,
                    None, original["reference"]["tool_calls"])

    def fake_launch(config, private_server, paths):
        assert paths.model_workspace.is_relative_to(tmp_path / "output")
        assert read_json(private_server)["scope_mounts"] == {}
        script = '''import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    if request["method"] == "tools/call":
        record = {"tool": request["params"]["name"], "arguments": request["params"]["arguments"],
                  "result": {"success": True, "value": 7}, "error": None}
        with open(os.environ["TRACE"], "a") as trace:
            trace.write(json.dumps(record) + "\\n")
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}), flush=True)
'''
        return StdioLaunch(Path(sys.executable), ("-c", script), {"TRACE": config["trace"]})

    monkeypatch.setattr(validation, "_task_mcp_launch", fake_launch)
    result = validation.replay_reference(case, tmp_path / "output", tool_timeout=1)
    assert result == {"call_count": 1, "recorded_result_difference_indices": [], "recorded_results_identical": True,
                      "expected_negative_result_indices": [], "backend": "direct_mcp_reference", "model_inference": False}
    assert list(initial.iterdir()) == []
    assert read_json(tmp_path / "output/replay_requests.json")[-1]["params"]["name"] == "list_candidates"


def test_direct_scripted_driver_advances_only_after_actual_tool_acknowledgement(tmp_path, monkeypatch):
    import json
    from task_gen.eval_cases import EvalCase
    from task_gen.program import scripted_reference as scripted

    original = reference_task()
    calls = [{"tool": "Read", "arguments": {"path": "evidence.txt"}},
             {"tool": "Glob", "arguments": {"pattern": "*.txt"}}]
    case = EvalCase(tmp_path, original, {}, tmp_path, None, calls)
    monkeypatch.setattr(scripted, "_verifier_environment", lambda case: {"tools": []})
    request = {"tools": [{"function": {"name": name}} for name in ("Read", "Glob")], "messages": []}
    with scripted.ReferenceDriver(case, tmp_path / "replay") as driver:
        response, finish = driver.reply(request)
        assert finish == "tool_calls"
        call = response["tool_calls"][0]
        assert call["function"]["name"] == "Read"
        assert json.loads(call["function"]["arguments"]) == {"path": "evidence.txt"}
        assert driver.reply(request) == (response, finish)
        assert driver.position == 0
        driver.reply({"messages": [{"role": "tool", "tool_call_id": call["id"]}]})
        assert driver.position == 0
        request["messages"] = [{"role": "tool", "tool_call_id": call["id"]}]
        next_response, finish = driver.reply(request)
        assert next_response["tool_calls"][0]["function"]["name"] == "Glob"
        assert driver.position == 1
        request["messages"].append({"role": "tool", "tool_call_id": next_response["tool_calls"][0]["id"]})
        assert driver.reply(request) == ({"role": "assistant", "content": original["reference"]["answer"]}, "stop")
        assert driver.position == 2


def test_direct_scripted_driver_resolves_actual_exposed_names_and_rejects_missing_tools():
    from task_gen.program.scripted_reference import ReferenceDriver

    request = {"tools": [{"function": {"name": "mcp__agent_world_distill__inspect"}},
                         {"function": {"name": "Read"}}]}
    assert ReferenceDriver._name("inspect", request) == "mcp__agent_world_distill__inspect"
    assert ReferenceDriver._name("Read", request) == "Read"
    with pytest.raises(ValueError, match="absent or ambiguous"):
        ReferenceDriver._name("missing", request)


def test_direct_scripted_legacy_artifact_requires_actual_producer_and_never_uses_recorded_preview(tmp_path, monkeypatch):
    from task_gen.eval_cases import EvalCase
    from task_gen.program import scripted_reference as scripted

    original = reference_task()
    old_path = "/tmp/original/tool-results/inspect-old-id.txt"
    calls = [{"tool": "inspect", "arguments": {}, "result": {
        "externalized": True, "output_path": old_path, "preview": "forged old preview 999"}},
        {"tool": "Read", "arguments": {"path": old_path, "line_offset": 3, "n_lines": 1}}]
    case = EvalCase(tmp_path, original, {}, tmp_path, None, calls)
    monkeypatch.setattr(scripted, "_verifier_environment", lambda case: {"tools": []})
    trace = tmp_path / "replay/execution/raw/environment_tool_calls.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text('{"tool":"inspect","arguments":{},"result":{"success":true,"value":17},"error":null}\n')
    with scripted.ReferenceDriver(case, tmp_path / "replay") as driver:
        monkeypatch.setattr(driver, "_session_root", lambda request: tmp_path / "actual-session")
        public = driver._native_arguments(1, dict(calls[1]["arguments"]), {})
        path = Path(public["path"])
        assert public["line_offset"] == 3 and public["n_lines"] == 1
        assert path.read_text().splitlines()[2] == '{"result":{"success":true,"value":17},"error":null}'
        assert "999" not in path.read_text()
        assert driver.expected_runtime_calls[1]["arguments"] == public
        assert read_json(tmp_path / "replay/native_argument_projection.json")[0]["producer_index"] == 1
    trace.write_text('{"tool":"different","arguments":{},"result":{"success":true},"error":null}\n')
    with scripted.ReferenceDriver(case, tmp_path / "replay") as driver:
        with pytest.raises(ValueError, match="actual producer"):
            driver._native_arguments(1, dict(calls[1]["arguments"]), {})


def test_direct_reference_native_calls_use_official_backend_without_mcp_only_replay(tmp_path, monkeypatch):
    from task_gen.eval_cases import EvalCase
    from task_gen.program import reference_replay as validation, scripted_reference as scripted

    original = reference_task()
    case = EvalCase(tmp_path, original, {}, tmp_path, None, [{"tool": "Glob", "arguments": {"pattern": "*.txt"}}])
    result = {"backend": "official_kimi_cli_scripted_reference", "model_inference": False}
    monkeypatch.setattr(scripted, "replay_with_official_cli", lambda selected, root, **kwargs: result)
    monkeypatch.setattr(validation, "_task_mcp_launch", lambda *args: pytest.fail("native calls entered MCP-only replay"))
    assert validation.replay_reference(case, tmp_path / "replay") == result


def test_direct_scripted_native_workspace_relocation_preserves_file_and_rejects_missing_paths(tmp_path, monkeypatch):
    from task_gen.eval_cases import EvalCase
    from task_gen.program import scripted_reference as scripted

    original = reference_task()
    original["task_id"] += "_direct_hardening"
    old = f"/{original['environment_id']}__original_001/model-workspace/evidence.txt"
    case = EvalCase(tmp_path, original, {}, tmp_path, None, [{"tool": "Read", "arguments": {"path": old}}])
    monkeypatch.setattr(scripted, "_verifier_environment", lambda case: {"tools": []})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "evidence.txt").write_text("actual public evidence\n")
    write_json(tmp_path / "replay/execution/private-state/path-map.json", {"workspace_root": str(workspace)})
    with scripted.ReferenceDriver(case, tmp_path / "replay") as driver:
        projected = driver._native_arguments(0, {"path": old}, {})
        assert Path(projected["path"]).read_text() == "actual public evidence\n"
        assert driver.expected_runtime_calls[0]["arguments"] == projected
        with pytest.raises(ValueError, match="no matching public runtime file"):
            driver._native_arguments(0, {"path": old.replace("evidence.txt", "missing.txt")}, {})
        outside = tmp_path / "outside.txt"
        outside.write_text("not public\n")
        (workspace / "escaped.txt").symlink_to(outside)
        with pytest.raises(ValueError, match="no matching public runtime file"):
            driver._native_arguments(0, {"path": old.replace("evidence.txt", "escaped.txt")}, {})


def test_direct_scripted_legacy_root_glob_only_projects_its_public_workspace(tmp_path, monkeypatch):
    from task_gen.eval_cases import EvalCase
    from task_gen.program import scripted_reference as scripted

    original = reference_task()
    old_root = f"/{original['environment_id']}__{original['task_id']}"
    calls = [{"tool": "Glob", "arguments": {"path": old_root, "pattern": "**/*.txt"}},
             {"tool": "Read", "arguments": {"path": old_root}}]
    case = EvalCase(tmp_path, original, {}, tmp_path, None, calls)
    monkeypatch.setattr(scripted, "_verifier_environment", lambda selected: {"tools": []})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "private.log").write_text("not public\n")
    write_json(tmp_path / "replay/execution/private-state/path-map.json", {"workspace_root": str(workspace)})
    with scripted.ReferenceDriver(case, tmp_path / "replay") as driver:
        projected = driver._native_arguments(0, dict(calls[0]["arguments"]), {})
        assert projected["path"] == str(workspace)
        assert driver.expected_runtime_calls[0]["arguments"] == projected
        assert calls[0]["arguments"]["path"] == old_root
        assert driver._native_arguments(1, {"path": old_root}, {}) == {"path": old_root}
        foreign_root = old_root + "-another-task"
        assert driver._native_arguments(0, {"path": foreign_root}, {}) == {"path": foreign_root}
        projection = read_json(tmp_path / "replay/native_argument_projection.json")
        assert projection[0]["source"] == "existing_public_workspace_root"
        assert projection[0]["runtime_path"] != str(workspace.parent)


def test_direct_official_scripted_replay_executes_mcp_and_all_native_file_tools(tmp_path):
    import os
    from task_gen.eval_cases import EvalCase
    from task_gen.program.reference_replay import replay_reference

    if not os.environ.get("KIMI_CODE_BIN"):
        pytest.skip("Set KIMI_CODE_BIN to the official Kimi CLI")
    initial = tmp_path / "initial"
    initial.mkdir()
    scope = initial / "filesystem_scopes/evidence"
    scope.mkdir(parents=True)
    (scope / "evidence.txt").write_text("reference evidence 17\n", encoding="utf-8")
    tool = {"name": "inspect", "description": "Inspect a deterministic value.",
            "inputSchema": {"type": "object", "additionalProperties": False},
            "outputSchema": {"type": "object", "properties": {"success": {"const": True}, "value": {"type": "integer"}},
                             "required": ["success", "value"], "additionalProperties": False},
            "internal": {"code": "def run(arguments, context): return {'success': True, 'value': 17}"}}
    old_path = "/tmp/old-scripted-reference/tool-results/inspect-old-id.txt"
    calls = [{"tool": "inspect", "arguments": {}, "result": {
                 "externalized": True, "output_path": old_path, "preview": "original recorded preview"}, "error": None},
             {"tool": "Read", "arguments": {"path": "evidence.txt"}, "result": {}},
             {"tool": "Glob", "arguments": {"pattern": "*.txt"}, "result": {}},
             {"tool": "Grep", "arguments": {"pattern": "reference", "path": "evidence.txt"}, "result": {}},
             {"tool": "Glob", "arguments": {"pattern": "*.txt", "path": "/scripted-cli-fixture__reference"}, "result": {}},
             {"tool": "Read", "arguments": {"path": old_path, "line_offset": 3, "n_lines": 1}, "result": {}}]
    task = {"environment_id": "scripted-cli-fixture", "task_id": "reference", "task_text": "Inspect the evidence.",
            "reference": {"answer": "The value is 17.", "tool_calls": calls}}
    case = EvalCase(tmp_path, task, {"environment_id": task["environment_id"], "schema_version": "1.0", "tools": [tool],
                                   "filesystem_scopes": [{"scope_id": "evidence", "name": "Evidence", "access": "copy_on_write"}]},
                    initial, None, calls)
    result = replay_reference(case, tmp_path / "replay", tool_timeout=30)
    assert result["model_inference"] is False
    assert result["call_count"] == 6
    assert result["mcp_call_count"] == 1
    assert result["native_file_call_count"] == 5
    assert result["recorded_result_difference_indices"] == [1, 2, 3, 4, 5, 6]
    actual = read_json(tmp_path / "replay/actual_reference_calls.json")
    assert [call["tool"] for call in actual] == ["inspect", "Read", "Glob", "Grep", "Glob", "Read"]
    assert "reference evidence 17" in actual[1]["result"]["output"]
    assert actual[2]["result"]["output"] == "evidence.txt"
    assert actual[3]["result"]["output"] == "evidence.txt"
    assert actual[4]["result"]["output"].endswith("evidence.txt")
    mapping = read_json(tmp_path / "replay/execution/private-state/path-map.json")
    assert actual[4]["arguments"]["path"] == mapping["workspace_root"]
    assert actual[5]["result"]["output"] == '3\t{"result":{"success":true,"value":17},"error":null}'
    assert actual[5]["arguments"]["path"] != old_path
    assert (scope / "evidence.txt").read_text(encoding="utf-8") == "reference evidence 17\n"


@pytest.mark.parametrize("defect", ["missing", "duplicate", "rpc_error", "call_error", "changed_args"])
def test_direct_reference_replay_rejects_execution_defects(defect):
    from copy import deepcopy
    from task_gen.program.reference_replay import check_replay

    original = reference_task()
    expected = original["reference"]["tool_calls"]
    actual = deepcopy(expected)
    responses = [{"id": index, "result": {}} for index in (1, 2, 3)]
    if defect == "missing":
        responses.pop()
    elif defect == "duplicate":
        responses.append(responses[-1])
    elif defect == "rpc_error":
        responses[-1] = {"id": 3, "error": {"code": -32000}}
    elif defect == "call_error":
        actual[0]["error"] = "Runtime unavailable"
    else:
        actual[0]["arguments"] = {"different": True}
    with pytest.raises(ValueError, match="reference replay"):
        check_replay(expected, actual, responses)


def test_direct_replay_accepts_only_identical_expected_negative_results():
    from copy import deepcopy
    from task_gen.program.reference_replay import check_replay

    expected = [{"tool": "check_wafer", "arguments": {}, "result": {
        "success": False, "error": {"code": "wafer_not_found"}}, "error": None}]
    actual = deepcopy(expected)
    actual[0]["error"] = "工具返回值必须包含 success=true"
    responses = [{"id": index, "result": {}} for index in (1, 2, 3)]
    replay = check_replay(expected, actual, responses)
    assert replay["recorded_results_identical"] and replay["expected_negative_result_indices"] == [1]
    actual[0]["error"] = "Runtime unavailable"
    with pytest.raises(ValueError, match="changed execution error"):
        check_replay(expected, actual, responses)
    actual[0]["error"] = "工具返回值必须包含 success=true"
    actual[0]["result"]["error"]["code"] = "permission_denied"
    with pytest.raises(ValueError, match="changed execution error"):
        check_replay(expected, actual, responses)


def test_direct_replay_does_not_confuse_boolean_and_numeric_arguments():
    from copy import deepcopy
    from task_gen.program.reference_replay import check_replay

    original = reference_task()
    calls = original["reference"]["tool_calls"]
    calls[0]["arguments"] = {"enabled": True}
    actual = deepcopy(calls)
    actual[0]["arguments"]["enabled"] = 1
    with pytest.raises(ValueError, match="changed tool or arguments"):
        check_replay(calls, actual, [{"id": index, "result": {}} for index in (1, 2, 3)])


def test_native_file_evidence_ignores_read_presentation_wrappers():
    from task_gen.program.reference_replay import same_native_file_evidence

    expected = {"output": "1\tfirst\n2\tsecond\n<system>two lines read</system>"}
    observed = "first\nsecond"
    assert same_native_file_evidence(expected, observed)
    assert not same_native_file_evidence({"output": "first\nthird"}, observed)


def test_native_glob_evidence_compares_array_with_cli_text():
    from task_gen.program.reference_replay import same_native_file_evidence

    expected = {"output": ["a.txt", "b.txt"], "truncated": False}
    assert same_native_file_evidence(expected, "a.txt\nb.txt")


def test_direct_reference_path_projection_preserves_scope_and_missing_outputs(tmp_path):
    from harness.resources import ResourceCatalog

    environment = {"filesystem_scopes": [{"scope_id": "measurements"}, {"scope_id": "reports"}]}
    workspace = tmp_path / "workspace"
    (workspace / "measurements").mkdir(parents=True)
    (workspace / "reports").mkdir()
    (workspace / "measurements/data.csv").write_text("actual data")
    catalog = ResourceCatalog(environment, lambda scope: workspace / scope, workspace_root=workspace,
                              scope_mounts={"measurements": "measurements", "reports": "reports"})
    schema = {"type": "object", "properties": {
        "input": {"type": "string", "x-resource-scope": "measurements", "x-resource-kind": "file"},
        "output": {"type": "string", "x-resource-scope": "reports", "x-resource-kind": "file"},
    }}
    original = {"input": "data.csv", "output": "new.csv"}
    projected = catalog.externalize_result(original, schema=schema, must_exist=False,
                                           allowed_scopes={"measurements", "reports"})
    assert projected == {"input": "measurements/data.csv", "output": "reports/new.csv"}
    assert catalog.normalize_arguments(projected, schema=schema) == original
    assert not (workspace / "reports/new.csv").exists()
    with pytest.raises(ValueError, match="输出资源不存在"):
        catalog.externalize_result(original, schema=schema)
    with pytest.raises(ValueError, match="Scope 内的普通相对路径"):
        catalog.externalize_result({"output": "../escape.csv"}, schema=schema, must_exist=False)


def test_direct_reference_projection_accepts_public_and_scope_relative_paths_without_double_prefix(tmp_path):
    from harness.resources import ResourceCatalog
    from task_gen.program.reference_replay import reference_public_arguments

    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs/paper.txt").write_text("real evidence")
    catalog = ResourceCatalog({"filesystem_scopes": [{"scope_id": "docs"}]}, lambda scope: workspace / scope,
                              workspace_root=workspace, scope_mounts={"docs": "docs"})
    schema = {"type": "string", "x-resource-scope": "docs", "x-resource-kind": "file"}
    for original in ("paper.txt", "docs/paper.txt"):
        projected = reference_public_arguments(catalog, original, schema=schema, allowed_scopes={"docs"})
        assert projected == "docs/paper.txt"
        assert catalog.normalize_arguments(projected, schema=schema) == "paper.txt"
    assert reference_public_arguments(catalog, "docs/new.txt", schema=schema, allowed_scopes={"docs"}) == "docs/new.txt"
    (workspace / "docs/docs").mkdir()
    (workspace / "docs/docs/paper.txt").write_text("different evidence")
    with pytest.raises(ValueError, match="ambiguous"):
        reference_public_arguments(catalog, "docs/paper.txt", schema=schema, allowed_scopes={"docs"})
    with pytest.raises(ValueError, match="Scope 内的普通相对路径"):
        reference_public_arguments(catalog, "../escape.txt", schema=schema, allowed_scopes={"docs"})


def test_direct_verifier_state_copy_handles_cross_filesystem(tmp_path, monkeypatch):
    import errno
    import os
    from distill.taskgen_verifier import _copy_tree_links

    source = tmp_path / "source"
    source.mkdir()
    (source / "evidence.txt").write_text("immutable evidence")

    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", cross_device)
    destination = tmp_path / "destination"
    _copy_tree_links(source, destination)
    assert (destination / "evidence.txt").read_text() == "immutable evidence"
    (destination / "evidence.txt").write_text("different copy")
    assert (source / "evidence.txt").read_text() == "immutable evidence"
