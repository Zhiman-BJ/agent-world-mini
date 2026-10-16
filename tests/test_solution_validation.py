import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from distill.export import _tool_kind
from task_gen.program import solution_validation as validation
from task_gen.task_eval_mcp import TaskEvalMcpServer


def test_validation_tool_description_requires_business_mcp_references():
    description = validation.TOOL["description"]
    assert "calls 只记录公开业务MCP工具短名和实际参数" in description
    assert "Read/Grep/Glob只用于作者探索，不进入参考列表" in description
    assert "无需启动新的Kimi会话" in description


@pytest.fixture
def config(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "counter.txt").write_text("0")
    database = tmp_path / "records.sqlite"
    database.write_bytes(b"original records")
    tools = [{"name": name, "description": name, "inputSchema": {"type": "object"},
              "outputSchema": {"type": "object"}} for name in ("increment", "inspect", "business_failure")]

    def execute(name, arguments, tools, root, **options):
        counter = root / "counter.txt"
        if name == "increment":
            counter.write_text(str(int(counter.read_text()) + 1))
            options["record_database"].write_bytes(b"updated records")
        result = {"success": name != "business_failure", "counter": int(counter.read_text())}
        return {"tool": name, "arguments": arguments, "result": result, "error": None}

    monkeypatch.setattr(validation, "call_environment_tool", execute)
    return {"workspace_root": str(workspace), "record_database": str(database), "tools": tools,
            "scope_mounts": {"public": "."}, "trace": str(tmp_path / "raw/environment_tool_calls.jsonl"),
            "max_tool_calls": 65, "timeout": 10, "memory_limit": 2147483648, "write_limit": 268435456,
            "solution_validation": {"read_default_chars": 80000, "read_max_chars": 128000}}


def request(*names):
    return {"calls": [{"tool": name, "arguments": {}} for name in names], "proposal": {}}


def test_every_attempt_starts_clean_and_preserves_the_live_workspace_and_database(config):
    validator = validation.SolutionValidator(config)
    Path(config["workspace_root"], "counter.txt").write_text("99")
    first = validator.validate(request("increment", "inspect"))
    second = validator.validate(request("inspect"))
    assert first["execution_passed"] is True
    assert [call["result"]["counter"] for call in first["calls"]] == [1, 1]
    assert first["state"]["changed_files"] == ["records.sqlite", "workspace/counter.txt"]
    assert first["semantic_correctness_proved"] is False
    assert second["calls"][0]["result"]["counter"] == 0
    assert second["state"]["changed"] is False
    assert first["validation_id"] != second["validation_id"]
    assert Path(config["workspace_root"], "counter.txt").read_text() == "99"
    assert Path(config["record_database"]).read_bytes() == b"original records"
    reports = [json.loads(line) for line in Path(config["trace"]).with_name("solution_validations.jsonl").read_text().splitlines()]
    assert reports[0]["request"] == request("increment", "inspect")
    assert reports[0]["result"] == first


@pytest.mark.parametrize("name", ["missing", "business_failure", "validate_solution_calls"])
def test_failure_returns_the_exact_step_and_stops_following_calls(config, name):
    result = validation.SolutionValidator(config).validate(request("inspect", name, "increment"))
    assert result["execution_passed"] is False
    assert result["failed_call"] == 2
    assert result["call_count"] == 2
    assert result["state"]["changed"] is False


def test_final_proposal_gets_live_source_feedback_and_references_actual_results(config):
    from task_gen.program.single_agent_hardening import SELF_CHECKS, validated_reference

    protocol = {"read_default_chars": 80000, "max_total_tool_calls": 65}
    config["solution_validation"]["source_check"] = {
        "protocol": protocol, "original_task_text": "Original", "original_call_count": 1}
    proposal = {"task_text": "Inspect and increment the public counter.", "real_need": "Deliver the updated counter.",
        "reference_answer": "The new counter is 1.", "solution_calls": [
            {"tool": "inspect", "arguments": {}, "argument_sources": {}, "purpose": "Get the original counter."},
            {"tool": "increment", "arguments": {}, "argument_sources": {}, "purpose": "Update the counter."}],
        "answer_sources": [{"kind": "call", "call": 2, "pointer": "/counter"}],
        "self_check": dict.fromkeys(SELF_CHECKS, True), "self_check_notes": ["Actual state checked."]}
    arguments = {**request("inspect", "increment"), "proposal": proposal}
    validator = validation.SolutionValidator(config)
    result = validator.validate(arguments)
    assert result["execution_passed"] is result["source_check_passed"] is True, result
    report = {"request": arguments, "result": result}
    final = {**proposal, "validation_id": result["validation_id"]}
    assert validated_reference(final, [report])[0] == report
    proposal["answer_sources"][0]["pointer"] = "/data/private_or_missing"
    proposal["answer_sources"].append({"kind": "call", "call": 2, "pointer": "/another_missing_value"})
    result = validator.validate(arguments)
    assert result["execution_passed"] is True
    assert result["source_check_passed"] is False
    assert len(result["source_check_errors"]) == 2
    assert "answer source 1" in result["source_check_errors"][0]
    assert "answer source 2" in result["source_check_errors"][1]


def test_validation_repairs_are_not_capped_at_three_but_call_budget_is_enforced(config):
    validator = validation.SolutionValidator(config)
    for _ in range(5):
        validator.validate(request("inspect"))
    assert validator.attempts == 5
    config["max_tool_calls"] = 1
    with pytest.raises(ValueError, match="budget"):
        validation.SolutionValidator(config).validate(request("inspect", "increment"))


def test_list_only_requests_are_rejected_before_execution(config, monkeypatch):
    validator = validation.SolutionValidator(config)
    monkeypatch.setattr(validator, "execute", lambda *args: pytest.fail("full proposal required before execution"))
    with pytest.raises(ValueError, match="proposal"):
        validator.validate({"calls": request("inspect")["calls"]})


def test_annotation_repairs_reuse_execution_but_changed_lists_execute_from_clean_state(config, monkeypatch):
    from task_gen.program.proposal_sources import SELF_CHECKS

    config["solution_validation"]["source_check"] = {
        "protocol": {"max_total_tool_calls": 65}, "original_task_text": "Original", "original_call_count": 0}
    validator = validation.SolutionValidator(config)
    proposal = {"task_text": "Increment the counter.", "real_need": "Update the counter.",
        "reference_answer": "Counter is 1.", "solution_calls": [
            {"tool": "increment", "arguments": {}, "argument_sources": {}, "purpose": "Update counter."}],
        "answer_sources": [{"kind": "call", "call": 1, "pointer": "/missing"}],
        "self_check": dict.fromkeys(SELF_CHECKS, True), "self_check_notes": ["Result checked."]}
    arguments = {**request("increment"), "proposal": proposal}
    first = validator.validate(arguments)
    assert first["source_check_passed"] is False
    execute = validator.execute
    monkeypatch.setattr(validator, "execute", lambda *args: pytest.fail("same calls must reuse successful execution"))
    for _ in range(4):
        validator.validate(arguments)
    proposal["answer_sources"][0]["pointer"] = "/counter"
    repaired = validator.validate(arguments)
    assert repaired["source_check_passed"] is True
    assert repaired["execution_reused"] is True
    assert repaired["execution_validation_id"] == first["validation_id"]
    assert repaired["validation_id"] != first["validation_id"]
    assert repaired["calls"][0]["result"]["counter"] == 1
    repaired["calls"][0]["result"]["counter"] = 99
    assert validator.validate(arguments)["calls"][0]["result"]["counter"] == 1
    monkeypatch.setattr(validator, "execute", execute)
    changed = validator.validate(request("inspect"))
    assert changed["execution_reused"] is False
    assert changed["calls"][0]["result"]["counter"] == 0


def test_failed_execution_is_not_reused(config, monkeypatch):
    validator = validation.SolutionValidator(config)
    first = validator.validate(request("business_failure"))
    assert first["execution_passed"] is False
    monkeypatch.setattr(validator, "execute", lambda *args: pytest.fail("failed execution must be retried"))
    with pytest.raises(pytest.fail.Exception, match="must be retried"):
        validator.validate(request("business_failure"))


@pytest.mark.parametrize("name", ["Read", "Grep", "Glob"])
def test_file_tools_are_rejected_before_reference_execution_or_snapshot(config, monkeypatch, name):
    validator = validation.SolutionValidator(config)
    monkeypatch.setattr(validator, "ensure_baseline", lambda: pytest.fail("file calls must not start validation"))
    with pytest.raises(ValueError, match="business MCP tools only"):
        validator.validate(request(name))
    assert validator.attempts == 0


def test_public_file_evidence_uses_frozen_workspace_not_private_or_live_files(config):
    validator = validation.SolutionValidator(config)
    Path(config["workspace_root"], "counter.txt").write_text("99")
    Path(config["workspace_root"], "author_created.txt").write_text("not frozen")
    files = validator.public_files()
    assert sorted(files) == ["counter.txt"]
    assert files["counter.txt"].read_text() == "0"
    assert "../records.sqlite" not in files


def test_file_derived_parameters_validate_through_mcp_without_file_calls_or_kimi(config, monkeypatch):
    from distill import runner
    from task_gen.program.proposal_sources import SELF_CHECKS

    monkeypatch.setattr(runner, "run_k3_distillation",
                        lambda *args, **kwargs: pytest.fail("reference validation must not start Kimi"))
    Path(config["workspace_root"], "parameters.json").write_text('{"target":"W01"}')
    config["solution_validation"]["source_check"] = {
        "protocol": {"max_total_tool_calls": 65}, "original_task_text": "Original", "original_call_count": 1}
    calls = [{"tool": "inspect", "arguments": {}}, {"tool": "increment", "arguments": {"target": "W01"}}]
    proposal = {"task_text": "Find the public target and increment its counter.", "real_need": "Update the selected target.",
        "reference_answer": "Counter is 1.", "solution_calls": [
            {**calls[0], "argument_sources": {}, "purpose": "Inspect current state."},
            {**calls[1], "argument_sources": {"/target": {
                "kind": "file", "path": "parameters.json", "quote": "W01"}}, "purpose": "Update target."}],
        "answer_sources": [{"kind": "call", "call": 2, "pointer": "/counter"}],
        "self_check": dict.fromkeys(SELF_CHECKS, True), "self_check_notes": ["Result checked."]}
    validator = validation.SolutionValidator(config)
    Path(config["workspace_root"], "parameters.json").write_text('{"target":"OTHER"}')
    result = validator.validate({"calls": calls, "proposal": proposal})
    assert result["execution_passed"] is result["source_check_passed"] is True, result
    assert result["call_count"] == 2
    assert [call["tool"] for call in result["calls"]] == ["inspect", "increment"]
    assert result["backend"] == "shared_mcp_executor"
    assert result["model_inference"] is False
    proposal["solution_calls"][1]["argument_sources"]["/target"]["quote"] = "OTHER"
    result = validator.validate({"calls": calls, "proposal": proposal})
    assert result["execution_passed"] is True
    assert result["source_check_passed"] is False


def test_validation_tool_is_opt_in_and_its_results_are_separate_from_domain_trace(config):
    server = TaskEvalMcpServer(config)
    tools = server.handle({"method": "tools/list"})["tools"]
    assert validation.TOOL_NAME in {tool["name"] for tool in tools}
    result = server.handle({"method": "tools/call", "params": {
        "name": validation.TOOL_NAME, "arguments": request("inspect")}})
    assert result["isError"] is False
    assert result["structuredContent"]["execution_passed"] is True
    assert not Path(config["trace"]).exists()
    assert _tool_kind("mcp__agent_world_distill__validate_solution_calls") == "harness_support"
    config.pop("solution_validation")
    normal = TaskEvalMcpServer(config)
    assert validation.TOOL_NAME not in {tool["name"] for tool in normal.handle({"method": "tools/list"})["tools"]}


def test_mcp_preflight_does_not_snapshot_the_workspace(config, monkeypatch):
    def unexpected_copy(*arguments, **options):
        pytest.fail("MCP preflight must not copy the task workspace")

    monkeypatch.setattr(validation.shutil, "copytree", unexpected_copy)
    server = TaskEvalMcpServer(config)
    server.handle({"method": "initialize"})
    server.handle({"method": "tools/list"})
    server.handle({"method": "ping"})
    assert not server.solution_validator.baseline.exists()


def test_first_business_call_snapshots_before_mutating_live_state(config, monkeypatch):
    from task_gen import task_eval_mcp

    monkeypatch.setattr(task_eval_mcp, "call_environment_tool", validation.call_environment_tool)
    server = TaskEvalMcpServer(config)
    result = server.handle({"method": "tools/call", "params": {"name": "increment", "arguments": {}}})
    assert result["structuredContent"]["counter"] == 1
    assert Path(config["record_database"]).read_bytes() == b"updated records"
    assert (server.solution_validator.baseline / "records.sqlite").read_bytes() == b"original records"
    for _ in range(2):
        result = server.handle({"method": "tools/call", "params": {
            "name": validation.TOOL_NAME, "arguments": request("inspect")}})
        assert result["structuredContent"]["calls"][0]["result"]["counter"] == 0
    assert Path(config["workspace_root"], "counter.txt").read_text() == "1"


def test_failed_deferred_snapshot_can_be_retried_without_partial_state(config, monkeypatch):
    validator = validation.SolutionValidator(config, defer_baseline=True)
    copytree = validation.shutil.copytree
    attempts = []

    def interrupted_copy(source, destination, *arguments, **options):
        attempts.append(source)
        if len(attempts) == 1:
            Path(destination).mkdir(parents=True)
            raise OSError("interrupted snapshot")
        return copytree(source, destination, *arguments, **options)

    monkeypatch.setattr(validation.shutil, "copytree", interrupted_copy)
    with pytest.raises(OSError, match="interrupted snapshot"):
        validator.ensure_baseline()
    assert not validator.baseline.exists()
    validator.ensure_baseline()
    validator.ensure_baseline()
    assert len(attempts) == 2
    assert (validator.baseline / "workspace/counter.txt").read_text() == "0"


def test_bound_kimi_adapter_loads_the_author_validation_configuration(config, tmp_path):
    from task_gen.task_eval_kimi_mcp import _arguments, _server_config

    path = tmp_path / "validation.json"
    path.write_text(json.dumps(config["solution_validation"]))
    parsed = _arguments(["--binding", str(tmp_path / "binding.json"),
        "--workspace-root", config["workspace_root"], "--trace", config["trace"],
        "--max-tool-calls", "65", "--timeout", "10", "--memory-limit", "2147483648",
        "--write-limit", "268435456", "--solution-validation", str(path)])
    assert _server_config(parsed)["solution_validation"] == config["solution_validation"]


def test_validator_executes_a_real_tool_with_the_shared_runtime(config, monkeypatch):
    from harness.execution import call_environment_tool

    monkeypatch.setattr(validation, "call_environment_tool", call_environment_tool)
    config["tools"] = [{"name": "read_value", "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"},
        "internal": {"code": "def run(arguments, context):\n return {'success': True, 'value': 37}"}}]
    result = validation.SolutionValidator(config).validate(request("read_value"))
    assert result["execution_passed"] is True, result
    assert result["calls"][0]["result"] == {"success": True, "value": 37}


def test_author_materials_are_isolated_from_business_data_and_opt_in_for_file_reads(config, tmp_path):
    from distill.runner import _write_author_materials
    from harness.kimi_file_policy import KimiFileAccessPolicy, build_kimi_file_hook_command

    workspace = Path(config["workspace_root"])
    paths = SimpleNamespace(output=tmp_path, model_workspace=workspace)
    root = _write_author_materials(paths, {"original_task.json": {"answer": "author only"},
                                         "workspace_files.json": {}})
    assert root == tmp_path / ".task-authoring"
    assert not root.is_relative_to(workspace)
    assert sorted(path.name for path in workspace.iterdir()) == ["counter.txt"]
    assert json.loads((root / "workspace_files.json").read_text())["public_files"] == ["counter.txt"]
    assert sorted(validation.SolutionValidator(config).public_files()) == ["counter.txt"]
    payload = {"tool_name": "Read", "tool_input": {"path": "../.task-authoring/original_task.json"}}
    normal = KimiFileAccessPolicy(workspace, tmp_path / "home", tmp_path / "audit.jsonl")
    author = KimiFileAccessPolicy(workspace, tmp_path / "home", tmp_path / "audit.jsonl", read_roots=(root,))
    assert normal.check(payload)["allowed"] is False
    assert author.check(payload)["allowed"] is True
    payload["tool_input"]["path"] = "../records.sqlite"
    assert author.check(payload)["allowed"] is False
    command = build_kimi_file_hook_command(workspace, tmp_path / "home", tmp_path / "audit.jsonl", read_roots=(root,))
    assert "--read-root" in command
    assert str(root) in command
