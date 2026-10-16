from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from task_gen.eval_cases import EvalCase
from task_gen.program import single_agent_hardening as draft
from task_gen.program import proposal_sources as sources
from task_gen.program.reference_evidence import sha256
from task_gen.program.utils.io import read_json, write_json


ORIGINAL_HARNESS = draft.run_harness


def test_author_prompt_separates_hardening_method_from_constraints():
    assert draft.PROFILE == "single_agent_tool_list_draft_v9_mcp_only"
    method = draft.AUTHOR_PROMPT.split("# 怎样增难\n", 1)[1].split("\n# 必要规则", 1)[0].strip()
    rules = draft.AUTHOR_PROMPT.split("# 必要规则\n", 1)[1].split("\n# 工作顺序", 1)[0].strip()
    assert len(method.splitlines()) == 5
    assert len(method) < 350
    for requirement in ("现实用途", "关键决定", "自行选择和编排工具", "不同工具返回",
                        "最短合理解法", "原解法不能直接完成新目标",
                        "真实结果依赖", "题文只呈现用户需求"):
        assert requirement in method
    assert len(rules.splitlines()) <= 10
    assert len(rules) < 650
    for requirement in ("需求真实且可解", "允许改写原目标、范围和交付",
                        "必要判据和验收标准", "回退路线写在参考解", "等效合法路径",
                        "使用已有工具和数据", "参数与结论有公开依据", "区分事实、假设和待实施事项",
                        "专业计算使用现有领域工具", "简单四则运算",
                        "原参考少于18次时增加必要调用", "达到18次时保留实质难度"):
        assert requirement in rules
    assert "18次" not in method
    assert "专业计算" not in method
    assert "本会话公开 MCP 与 Read/Grep/Glob 是实际交互入口" not in draft.AUTHOR_PROMPT
    assert "工具接口、公开数据和任务初态沿用原环境" not in draft.AUTHOR_PROMPT
    assert "选用真实需要的多对象处理、取舍、状态分支" not in draft.AUTHOR_PROMPT


def test_author_prompt_keeps_file_exploration_out_of_the_business_reference():
    prompt = draft.AUTHOR_PROMPT
    rules = prompt.split("# 必要规则\n", 1)[1].split("\n# 工作顺序", 1)[0]
    for requirement in ("参考解调用列表只包含公开业务MCP工具", "作者查得参数后直接填入对应接口",
                        "文件查看不计入参考调用数"):
        assert requirement in rules
    assert "Read/Grep/Glob可用于作者读材料、查找公开参数" in prompt
    assert "tool 只填写公开业务MCP短名" in prompt
    assert "验证直接复用正式MCP业务接口，不重新启动完整Kimi会话" in prompt
    assert "tool 使用公开MCP短名或Read/Grep/Glob" not in prompt


@pytest.fixture
def protocol():
    return {"model": "test-model", "provider_type": "openai_responses",
        "provider_base_url": "http://localhost:8000/v1", "reasoning_effort": "high",
        "context_cap": 262144, "max_output_tokens": 131072, "read_default_chars": 80000,
        "read_max_chars": 128000, "max_total_tool_calls": 65, "compaction_trigger_ratio": 0.8,
        "reference_answer_supplied_to_solver": False}


@pytest.fixture
def solution():
    schemas = {"query": {}, "measure": {"properties": {"mode": {"default": "fast"}}}}
    observed = [
        {"call_id": "first", "name": "mcp__agent_world_distill__query", "tool_kind": "environment_mcp",
         "arguments": {}, "visible_output": {"output": {"id": "W01", "value": 4}},
         "started_event_sequence": 1, "completed_event_sequence": 2, "is_error": False},
        {"call_id": "second", "name": "mcp__agent_world_distill__measure", "tool_kind": "environment_mcp",
         "arguments": {"id": "W01", "threshold": 8, "mode": "fast", "name": "analysis"},
         "visible_output": {"output": {"answer": 7}},
         "started_event_sequence": 3, "completed_event_sequence": 4, "is_error": False}]
    proposal = {"task_text": "处理W01，预算10，已有2，生成分析结果。", "real_need": "扩展原任务的实际分析范围。",
        "reference_answer": "实际结果是7。", "validation_id": "validated-final", "solution_calls": [
            {"observed_call": 1, "tool": "query", "arguments": {}, "argument_sources": {}, "purpose": "发现对象"},
            {"observed_call": 2, "tool": "measure", "arguments": deepcopy(observed[1]["arguments"]),
             "argument_sources": {
                "/id": {"kind": "call", "call": 1, "pointer": "/output/id"},
                "/threshold": {"kind": "derived", "operation": "subtract", "inputs": [
                    {"kind": "task", "quote": "10", "as": "json"},
                    {"kind": "task", "quote": "2", "as": "json"}]},
                "/mode": {"kind": "schema", "pointer": "/properties/mode/default"},
                "/name": {"kind": "new_identifier", "value": "analysis", "reason": "新建分析输出"}},
             "purpose": "根据发现的对象计算业务结果"}],
        "answer_sources": [{"kind": "call", "call": 2, "pointer": "/output/answer"}],
        "self_check": dict.fromkeys(draft.SELF_CHECKS, True), "self_check_notes": ["实际结果支持交付，自查不是独立证明。"]}
    original = {"task_id": "task", "task_text": "Original public task.",
                "reference": {"answer": "PRIVATE_OLD_ANSWER", "tool_calls": [{"tool": "query", "arguments": {}}]}}
    return proposal, original, observed, schemas


def check(solution, protocol):
    proposal, original, observed, schemas = solution
    return draft.check_solution(proposal, original, observed, protocol, schemas)


def test_code_checks_actual_calls_public_sources_and_basic_arithmetic(solution, protocol):
    indices, report = check(solution, protocol)
    assert indices == [1, 2]
    assert report["call_count"] == 2
    assert report["parameter_sources_match"] is True
    assert report["semantic_correctness_proved"] is False
    assert report["author_self_check_only"] is True
    assert report["new_identifier_declarations"] == [
        {"call": 2, "argument": "/name", "reason": "新建分析输出"}]


@pytest.mark.parametrize("name", ["Read", "Grep", "Glob"])
def test_reference_rejects_file_tools_even_if_they_appear_in_a_schema(solution, protocol, name):
    proposal, _, observed, schemas = solution
    proposal["solution_calls"][0]["tool"] = name
    observed[0]["name"] = name
    schemas[name] = {}
    with pytest.raises(ValueError, match="business MCP tools only"):
        check(solution, protocol)


def test_public_file_parameters_do_not_require_a_file_call_in_the_reference(solution, protocol, tmp_path):
    proposal, original, observed, schemas = solution
    path = tmp_path / "public.json"
    path.write_text('{"id":"W01","budget":10,"used":2}')
    sources_map = proposal["solution_calls"][1]["argument_sources"]
    sources_map["/id"] = {"kind": "file", "path": "public.json", "quote": "W01"}
    sources_map["/threshold"] = {"kind": "derived", "operation": "subtract", "inputs": [
        {"kind": "file", "path": "public.json", "quote": "10", "as": "json"},
        {"kind": "file", "path": "public.json", "quote": "2", "as": "json"}]}
    _, report = sources.check_solution(proposal, original, observed, protocol, schemas,
                                       public_files={"public.json": path})
    assert report["call_count"] == 2
    assert report["parameter_sources_match"] is True


def test_old_file_calls_cannot_inflate_the_business_call_count(solution, protocol):
    proposal, original, _, _ = solution
    original["reference"]["tool_calls"].extend([{"tool": "Read", "arguments": {}}] * 20)
    proposal["solution_calls"] = proposal["solution_calls"][:1]
    with pytest.raises(ValueError, match="eighteen-call threshold"):
        check(solution, protocol)


@pytest.mark.parametrize("path,quote", [("../records.sqlite", "W01"),
    (".task-authoring/original_task.json", "W01"), ("missing.txt", "W01"), ("public.txt", "invented")])
def test_file_sources_cannot_use_private_missing_or_fabricated_evidence(protocol, tmp_path, path, quote):
    public = tmp_path / "public.txt"
    public.write_text("W01")
    with pytest.raises(ValueError):
        sources.source_value({"kind": "file", "path": path, "quote": quote},
            task={"task_text": "task"}, protocol=protocol, schema={}, preceding=[], current_start=None,
            public_files={"public.txt": public})


def test_existing_public_file_path_can_be_passed_directly_as_a_parameter(protocol, tmp_path):
    public = tmp_path / "public.txt"
    public.write_text("data")
    assert sources.source_value({"kind": "file", "path": "public.txt"},
        task={"task_text": "task"}, protocol=protocol, schema={}, preceding=[], current_start=None,
        public_files={"public.txt": public}) == "public.txt"


@pytest.mark.parametrize("module", [draft.__name__, sources.__name__])
def test_source_checks_run_without_orchestration_dependencies(solution, protocol, module):
    payload = json.dumps({"solution": solution, "protocol": protocol}, ensure_ascii=False)
    result = subprocess.run([sys.executable, "-S", "-c",
        "import json, sys; "
        f"from {module} import check_solution; "
        "payload = json.load(sys.stdin); proposal, original, observed, schemas = payload['solution']; "
        "assert check_solution(proposal, original, observed, payload['protocol'], schemas)[1]['parameter_sources_match']; "
        "assert not {'tomlkit', 'openai', 'yaml', 'distill.runner'} & set(sys.modules)"],
        input=payload, capture_output=True, text=True, timeout=30, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("schema,value,accepted", [
    ({"type": "array", "items": {"enum": ["source_row", "source_file"]}, "uniqueItems": True},
     ["source_row", "source_file"], True),
    ({"type": "array", "items": {"enum": ["source_row"]}}, ["unknown"], False),
    ({"type": "array", "items": {"enum": ["source_row"]}, "uniqueItems": True},
     ["source_row", "source_row"], False),
    ({"type": "array", "items": {"enum": [1]}, "uniqueItems": True}, [1, 1.0], False),
    ({"type": "array", "items": {"enum": [1]}, "maxItems": 1}, [1, 1], False),
    ({"type": "array", "items": {"enum": [1]}, "minItems": 1}, [], False),
    ({"type": "array", "minItems": 0}, [], True),
    ({"type": "array", "items": {"type": "string"}}, ["private-object-id"], False),
    ({"type": "array", "default": ["public-default-id"]}, ["public-default-id"], True),
    ({"type": "array", "items": {"type": "array", "items": {"enum": [1]}}}, [[1]], True),
    ({"type": "array", "items": {"type": "boolean"}}, [1], False),
    ({"enum": [1]}, 1.0, True),
    ({"enum": [1]}, "1", False),
])
def test_container_schema_choices_only_combine_actual_public_options(protocol, schema, value, accepted):
    declaration = {"kind": "schema_choice", "value": value, "reason": "选择公开工具选项"}
    options = dict(task={"task_text": "task"}, protocol=protocol, schema=schema,
                   preceding=[], current_start=None)
    if accepted:
        assert sources.same_source_value(sources.source_value(declaration, **options), value)
    else:
        with pytest.raises(ValueError, match="actual public"):
            sources.source_value(declaration, **options)


def test_whole_enum_array_provenance_matches_equivalent_per_item_choices(solution, protocol):
    proposal, original, observed, schemas = solution
    value = ["source_row", "source_file"]
    proposal["solution_calls"][1]["arguments"]["group_by"] = deepcopy(value)
    observed[1]["arguments"]["group_by"] = deepcopy(value)
    schemas["measure"]["properties"]["group_by"] = {
        "type": "array", "items": {"enum": value}, "uniqueItems": True}
    declarations = proposal["solution_calls"][1]["argument_sources"]
    declarations["/group_by"] = {"kind": "schema_choice", "value": value, "reason": "选择分组字段"}
    assert sources.check_solution(proposal, original, observed, protocol, schemas)[1]["parameter_sources_match"]
    declarations.pop("/group_by")
    for index, item in enumerate(value):
        declarations[f"/group_by/{index}"] = {"kind": "schema_choice", "value": item, "reason": "选择分组字段"}
    assert sources.check_solution(proposal, original, observed, protocol, schemas)[1]["parameter_sources_match"]


def test_source_errors_report_all_bad_parameters_and_answer_evidence_in_one_pass(solution, protocol):
    proposal, original, observed, schemas = solution
    declarations = proposal["solution_calls"][1]["argument_sources"]
    declarations["/id"]["call"] = 99
    declarations["/threshold"]["operation"] = "add"
    declarations["/mode"]["pointer"] = "/missing"
    proposal["answer_sources"][0]["call"] = 99
    with pytest.raises(sources.SourceCheckError) as caught:
        sources.check_solution(proposal, original, observed, protocol, schemas)
    assert len(caught.value.errors) == 4
    for label in ("call 2 parameter /id", "call 2 parameter /threshold",
                  "call 2 parameter /mode", "answer source 1"):
        assert any(label in message for message in caught.value.errors)


def test_source_coverage_reports_all_missing_fields_and_extra_paths():
    with pytest.raises(sources.SourceCheckError) as caught:
        sources.parameter_source_values({"id": "one", "value": 2}, {"/unknown": {}})
    assert any("extra source path '/unknown'" in message for message in caught.value.errors)
    assert any("missing source paths: /id, /value" in message for message in caught.value.errors)


@pytest.mark.parametrize("value", [{"ids": ["one", "two"]}, [1, 2], 4, 3.5, True, None])
def test_json_annotation_on_an_already_typed_result_is_idempotent(protocol, value):
    declaration = {"kind": "call", "call": 1, "pointer": "/value", "as": "json"}
    actual = sources.source_value(declaration, task={"task_text": "task"}, protocol=protocol,
        schema={}, preceding=[{"visible_output": {"value": value}}], current_start=None)
    assert sources.same_source_value(actual, value)


@pytest.mark.parametrize("value", ["12", "{}", "\"private-object\""])
def test_json_annotation_does_not_implicitly_decode_strings_without_a_quote(protocol, value):
    with pytest.raises(ValueError, match="actual quote"):
        sources.source_value({"kind": "call", "call": 1, "pointer": "/value", "as": "json"},
            task={"task_text": "task"}, protocol=protocol, schema={},
            preceding=[{"visible_output": {"value": value}}], current_start=None)


@pytest.mark.parametrize("expected,observed,accepted", [
    (8, 8.0, True), (8.0, 8, True), (8, 8.01, False), (1, True, False), ("8", 8, False),
])
def test_numeric_sources_preserve_values_not_integer_float_spelling(solution, protocol, expected, observed, accepted):
    proposal, _, interactions, _ = solution
    proposal["solution_calls"][1]["arguments"]["threshold"] = expected
    interactions[1]["arguments"]["threshold"] = expected
    interactions[0]["visible_output"]["output"]["value"] = observed
    proposal["solution_calls"][1]["argument_sources"]["/threshold"] = {
        "kind": "call", "call": 1, "pointer": "/output/value"}
    if accepted:
        assert check(solution, protocol)[1]["parameter_sources_match"] is True
    else:
        with pytest.raises(ValueError, match="differs from its declared public source"):
            check(solution, protocol)


def test_numeric_source_lookup_does_not_confuse_boolean_with_number():
    assert draft._value_pointers({"frequency": 100_000_000.0, "enabled": True}, 100_000_000) == ["/frequency"]
    assert draft._value_pointers({"enabled": True}, 1) == []
    assert draft._value_pointers(8, 8) == [""]


@pytest.mark.parametrize("mutation", [None, "overlap", "missing", "extra", "changed_value", "future"])
def test_container_sources_cover_every_leaf_once_without_weakening_provenance(solution, protocol, mutation):
    proposal, _, observed, schemas = solution
    selection = {"ids": ["W01", "W02"], "window": {"start": 4}}
    observed[0]["visible_output"]["output"]["selection"] = deepcopy(selection)
    arguments = {"selection": selection, "enabled": True}
    observed[1]["arguments"] = deepcopy(arguments)
    call = proposal["solution_calls"][1]
    call["arguments"] = deepcopy(arguments)
    call["argument_sources"] = {
        "/selection": {"kind": "call", "call": 1, "pointer": "/output/selection"},
        "/enabled": {"kind": "schema_choice", "value": True, "reason": "选择公开布尔选项"}}
    schemas["measure"] = {"properties": {"enabled": {"type": "boolean"}}}
    if mutation == "overlap":
        call["argument_sources"]["/selection/ids/0"] = {
            "kind": "call", "call": 1, "pointer": "/output/id"}
    elif mutation == "missing":
        call["argument_sources"].pop("/selection")
    elif mutation == "extra":
        call["argument_sources"]["/invented"] = {"kind": "task", "quote": "W01"}
    elif mutation == "changed_value":
        observed[0]["visible_output"]["output"]["selection"]["ids"][1] = "DIFFERENT"
    elif mutation == "future":
        call["argument_sources"]["/selection"]["call"] = 2
    if mutation is None:
        assert check(solution, protocol)[1]["parameter_sources_match"] is True
    else:
        with pytest.raises(ValueError):
            check(solution, protocol)


def test_source_normalization_preserves_valid_schema_and_container_sources(solution, protocol):
    proposal, original, observed, schemas = solution
    observed[0]["visible_output"]["output"]["ids"] = ["W01", "W02"]
    observed[1]["arguments"]["ids"] = ["W01", "W02"]
    call = proposal["solution_calls"][1]
    call["arguments"]["ids"] = ["W01", "W02"]
    call["argument_sources"]["/ids"] = {"kind": "call", "call": 1, "pointer": "/output/ids"}
    before = deepcopy(call["argument_sources"])
    report = {"request": {"calls": [{"tool": item["tool"], "arguments": item["arguments"]}
                                   for item in proposal["solution_calls"]]},
              "result": {"validation_id": proposal["validation_id"], "execution_passed": True,
                         "source_check_passed": False,
                         "calls": [{"tool": item["name"].removeprefix("mcp__agent_world_distill__"),
                                    "arguments": item["arguments"], "result": item["visible_output"]}
                                   for item in observed]}}
    result = draft.normalize_proposal_sources(proposal, [report], task=original, protocol=protocol, schemas=schemas)
    assert result["inferred_parameter_sources"] == 0
    assert call["argument_sources"] == before
    assert check(solution, protocol)[1]["parameter_sources_match"] is True


def test_parameter_source_coverage_handles_escaped_empty_and_root_paths():
    arguments = {"a/b": {"~key": [7]}, "": [8], "empty": []}
    assert draft.parameter_source_values(arguments, {"/a~1b": {}, "/": {}, "/empty": {}}) == {
        "/a~1b": {"~key": [7]}, "/": [8], "/empty": []}
    assert draft.parameter_source_values(arguments, {"": {}}) == {"": arguments}
    for sources in ({"/a~1b": {}, "/a~1b/~0key/0": {}, "/": {}, "/empty": {}},
                    {"/a~2b": {}}, {"/empty/0": {}}, {"/unknown": {}}, {1: {}}):
        with pytest.raises(ValueError):
            draft.parameter_source_values(arguments, sources)
    with pytest.raises(ValueError):
        draft.parameter_source_values({}, {"": {}})


@pytest.mark.parametrize("mutation", ["invented_call", "wrong_arguments", "duplicate_observation", "missing_source",
    "extra_source", "future_source", "parallel_source", "private_source", "false_quote", "false_self_check",
    "numeric_self_check", "no_answer_source", "unsupported_program", "wrong_value", "bad_schema", "answer_future"])
@pytest.mark.parametrize("implementation", [draft, sources])
def test_unsupported_or_fabricated_reference_is_rejected(solution, protocol, mutation, implementation):
    proposal, original, observed, schemas = solution
    call = proposal["solution_calls"][1]
    source = call["argument_sources"]["/id"]
    if mutation == "invented_call":
        call["observed_call"] = 3
    elif mutation == "wrong_arguments":
        call["arguments"]["id"] = "FAKE"
    elif mutation == "duplicate_observation":
        call["observed_call"] = 1
    elif mutation == "missing_source":
        call["argument_sources"].pop("/id")
    elif mutation == "extra_source":
        call["argument_sources"]["/invented"] = deepcopy(source)
    elif mutation == "future_source":
        source["call"] = 2
    elif mutation == "parallel_source":
        observed[0]["completed_event_sequence"] = 4
    elif mutation == "private_source":
        source["pointer"] = "/environment_record/result/data/id"
    elif mutation == "false_quote":
        call["argument_sources"]["/id"] = {"kind": "task", "quote": "not in public task"}
    elif mutation == "false_self_check":
        proposal["self_check"]["no_padding"] = False
    elif mutation == "numeric_self_check":
        proposal["self_check"]["no_padding"] = 1
    elif mutation == "no_answer_source":
        proposal["answer_sources"] = []
    elif mutation == "unsupported_program":
        call["argument_sources"]["/threshold"]["operation"] = "scipy.optimize"
    elif mutation == "wrong_value":
        call["argument_sources"]["/threshold"]["operation"] = "add"
    elif mutation == "bad_schema":
        call["argument_sources"]["/mode"]["pointer"] = "/missing"
    else:
        proposal["answer_sources"][0]["call"] = 3
    with pytest.raises((ValueError, KeyError, IndexError)):
        implementation.check_solution(proposal, original, observed, protocol, schemas)


def test_sub_eighteen_reference_requires_real_growth_but_eighteen_is_exempt(solution, protocol):
    proposal, original, observed, schemas = solution
    original["reference"]["tool_calls"] *= 2
    with pytest.raises(ValueError, match="eighteen"):
        check(solution, protocol)
    original["reference"]["tool_calls"] *= 9
    assert check(solution, protocol)[1]["original_call_count"] == 18


def test_source_quote_preserves_numeric_string_unless_json_is_explicit(protocol):
    arguments = dict(task={"task_text": "对象1001，阈值2。"}, protocol=protocol, schema={}, preceding=[], current_start=1)
    assert draft.source_value({"kind": "task", "quote": "1001"}, **arguments) == "1001"
    assert draft.source_value({"kind": "task", "quote": "2", "as": "json"}, **arguments) == 2
    frequency_arguments = dict(task={"task_text": "覆盖100 MHz至14 GHz。"}, protocol=protocol,
                               schema={}, preceding=[], current_start=1)
    assert draft.source_value({"kind": "task", "quote": "100 MHz", "as": "frequency_hz"},
                              **frequency_arguments) == 100_000_000
    with pytest.raises(ValueError):
        draft.source_value({"kind": "task", "quote": "2", "as": "python"}, **arguments)


def test_sources_do_not_reach_unread_private_returns_or_credentials(protocol):
    protocol["api_key"] = "PRIVATE_KEY"
    arguments = dict(task={"task_text": "task"}, protocol=protocol, schema={}, preceding=[], current_start=1)
    with pytest.raises(KeyError):
        draft.source_value({"kind": "protocol", "pointer": "/api_key"}, **arguments)


def test_pointer_escaping_and_nested_parameter_coverage():
    assert draft.pointer({"a/b": {"~key": [7]}}, "/a~1b/~0key/0") == 7
    assert draft.argument_leaves({"a/b": [{"~key": 7}], "empty": []}) == {"/a~1b/0/~0key": 7, "/empty": []}
    for path in ("no-leading-slash", "/~2", "/00", "/-1"):
        with pytest.raises((ValueError, KeyError)):
            draft.pointer([7], path)


@pytest.fixture
def pipeline(tmp_path, protocol, solution, monkeypatch):
    proposal, original, observed, schemas = solution
    source = tmp_path / "source"
    initial = source / "initial"
    initial.mkdir(parents=True)
    (initial / "public.txt").write_text("unchanged")
    task = {**original, "environment_id": "environment", "initial_state": "initial"}
    write_json(source / "tasks.json", [task])
    write_json(source / "intermediate/step_5_bundle.json", {"environment": {}})
    write_json(source / "tools.json", {"tools": []})
    write_json(source / "binding.json", {})
    case = EvalCase(source, task, {}, initial, None, original["reference"]["tool_calls"],
                   {"tools_path": str(source / "tools.json"), "binding_path": str(source / "binding.json")})
    protocol_path = tmp_path / "protocol.json"
    write_json(protocol_path, protocol)
    captured = {"sessions": [], "replays": []}

    def harness(case, task, actual_protocol, root, api_key, *, author_materials=None):
        captured["sessions"].append(deepcopy(task))
        if len(captured["sessions"]) == 1:
            captured["author_materials"] = author_materials
            write_json(root / "raw/placeholder.json", {})
            submitted = deepcopy(proposal)
            for call in submitted["solution_calls"]:
                call.pop("observed_call")
            validation = {"request": {"calls": [{"tool": call["tool"], "arguments": call["arguments"]}
                for call in submitted["solution_calls"]],
                "proposal": {key: value for key, value in submitted.items() if key != "validation_id"}}, "result": {
                    "validation_id": "validated-final", "execution_passed": True, "source_check_passed": True,
                    "calls": [{"index": index, "tool": item["name"].removeprefix("mcp__agent_world_distill__"),
                               "arguments": deepcopy(item["arguments"]), "result": item["visible_output"], "error": None}
                              for index, item in enumerate((item for item in observed if item["tool_kind"] != "harness_support"), 1)]}}
            try:
                _, checked = draft.check_solution(submitted, original, [], actual_protocol, schemas,
                                                   validation=validation)
                validation["result"]["source_check"] = checked
            except ValueError as error:
                validation["result"].update(source_check_passed=False, source_check_errors=[str(error)])
            (root / "raw/solution_validations.jsonl").write_text(json.dumps(validation) + "\n")
            calls = []
            for item in observed:
                name = item["name"].removeprefix("mcp__agent_world_distill__")
                calls.append({**deepcopy(item), "output": item["visible_output"],
                    "environment_record": {"tool": name, "arguments": item["arguments"],
                                           "result": item["visible_output"], "error": None}})
            return {"outcome": {"final_answer": json.dumps(submitted)}, "steps": [{"tool_calls": calls}]}, observed
        root.mkdir(parents=True)
        return {"outcome": {"final_answer": "INDEPENDENT_ANSWER"}}, []

    def replay(case, root):
        captured["replays"].append(case)
        return {"call_count": len(case.reference_calls), "recorded_results_identical": True}

    monkeypatch.setattr(draft, "run_harness", harness)
    monkeypatch.setattr("task_gen.program.reference_replay.replay_reference", replay)
    monkeypatch.setattr("task_gen.program.scripted_reference.replay_with_official_cli",
                        lambda *args, **kwargs: pytest.fail("MCP-only references must not start a Kimi CLI replay"))
    monkeypatch.setattr("distill.taskgen_verifier._verifier_environment", lambda case: {
        "tools": [{"name": name, "description": "Public tool", "inputSchema": schema,
                   "outputSchema": {"type": "object"}} for name, schema in schemas.items()]})
    return case, protocol_path, tmp_path / "output", captured


def test_default_only_prepares_the_flow_and_never_starts_an_agent(pipeline):
    case, protocol, output, captured = pipeline
    result = draft.run_case(case, protocol, output)
    assert result["status"] == "awaiting_user_confirmation"
    assert result["author_session_limit"] == result["independent_solver_session_limit"] == 1
    assert result["checklist_agents"] == result["business_review_agents"] == result["verifier_agents"] == 0
    assert result["author_started"] is False
    assert captured["sessions"] == captured["replays"] == []


def test_execution_is_one_author_then_code_replay_then_one_blind_solver(pipeline):
    case, protocol, output, captured = pipeline
    result = draft.run_case(case, protocol, output, execute=True, api_key="SECRET_KEY")
    assert result["status"] == "gpt_completed_pending_answer_review"
    assert len(captured["sessions"]) == 2


    assert len(captured["replays"]) == 1
    assert set(captured["author_materials"]) == {"original_task.json", "original_calls.json",
        "public_tools.json", "protocol.json", "workspace_files.json"}
    assert captured["author_materials"]["original_task.json"]["reference_answer"] == "PRIVATE_OLD_ANSWER"
    assert "PRIVATE_OLD_ANSWER" not in captured["sessions"][0]["task_text"]
    assert captured["sessions"][1] == {
        "task_id": "task_single_agent_hardening", "task_text": "处理W01，预算10，已有2，生成分析结果。",
        "environment_id": "environment"}
    assert (output / "independent-gpt/answer.txt").read_text() == "INDEPENDENT_ANSWER"
    assert result["correctness_verified"] is result["formal_accuracy_evaluation"] is result["release_ready"] is False
    assert "SECRET_KEY" not in json.dumps(read_json(output / "result.json"))
    assert "solution_code" not in read_json(output / "candidate.json")["reference"]
    assert [call["tool"] for call in read_json(output / "reference_tool_calls.json")] == ["query", "measure"]
    with pytest.raises(ValueError, match="no automatic agent retry"):
        draft.run_case(case, protocol, output, execute=True, api_key="SECRET_KEY")
    assert len(captured["sessions"]) == 2


def test_reference_only_mode_waits_for_semantic_verification_before_blind_solving(pipeline):
    case, protocol, output, captured = pipeline
    result = draft.run_case(case, protocol, output, execute=True, api_key="SECRET_KEY", solve_independently=False)
    assert result["status"] == "reference_replayed_pending_semantic_verification"
    assert result["independent_solver_started"] is result["correctness_verified"] is False
    assert len(captured["sessions"]) == len(captured["replays"]) == 1


def test_support_call_between_business_calls_does_not_shift_the_selected_reference(pipeline, solution):
    observed = solution[2]
    observed[1].update(started_event_sequence=5, completed_event_sequence=6)
    observed.insert(1, {"call_id": "validation-support", "name": "mcp__agent_world_distill__validate_solution_calls",
        "tool_kind": "harness_support", "arguments": {"calls": []}, "visible_output": {},
        "started_event_sequence": 3, "completed_event_sequence": 4, "is_error": False})
    solution[0]["solution_calls"][1]["observed_call"] = 3
    case, protocol, output, captured = pipeline
    result = draft.run_case(case, protocol, output, execute=True, api_key="key")
    assert result["status"] == "gpt_completed_pending_answer_review"
    assert [call["tool"] for call in read_json(output / "reference_tool_calls.json")] == ["query", "measure"]


@pytest.mark.parametrize("changed", ["prompt", "source", "task_id", "protocol"])
def test_changed_prepared_inputs_cannot_be_executed(pipeline, changed):
    case, protocol, output, captured = pipeline
    draft.run_case(case, protocol, output)
    if changed == "prompt":
        (output / "author_prompt.txt").write_text("Changed policy")
    elif changed == "source":
        (case.initial_state / "public.txt").write_text("Changed state")
    elif changed == "task_id":
        case.task["task_id"] = "another"
    else:
        write_json(protocol, {**read_json(protocol), "model": "another"})
    with pytest.raises(ValueError):
        draft.run_case(case, protocol, output, execute=True, api_key="SECRET_KEY")
    assert captured["sessions"] == []


def test_replay_difference_is_not_silently_accepted_or_counted_as_model_failure(pipeline, monkeypatch):
    case, protocol, output, captured = pipeline
    monkeypatch.setattr("task_gen.program.reference_replay.replay_reference",
                        lambda *args: {"call_count": 2, "recorded_results_identical": False})
    result = draft.run_case(case, protocol, output, execute=True, api_key="key")
    assert result["status"] == "reference_replay_needs_review"
    assert len(captured["sessions"]) == 1
    assert result["independent_solver_started"] is False
    assert result["correctness_verified"] is False


def test_invalid_author_reference_stops_without_retry_or_other_agents(pipeline, solution):
    case, protocol, output, captured = pipeline
    solution[0]["solution_calls"][1]["argument_sources"]["/id"]["call"] = 99
    result = draft.run_case(case, protocol, output, execute=True, api_key="key")
    assert result["status"] == "stage_error"
    assert len(captured["sessions"]) == 1
    assert captured["replays"] == []


def test_draft_output_cannot_change_the_original_environment(pipeline):
    case, protocol, output, captured = pipeline
    with pytest.raises(ValueError, match="overlap"):
        draft.run_case(case, protocol, case.initial_state / "draft")
    assert not (case.initial_state / "draft").exists()


@pytest.fixture
def actual_harness(tmp_path, protocol, solution, pipeline, monkeypatch):
    monkeypatch.setattr(draft, "run_harness", ORIGINAL_HARNESS)
    observed = solution[2]
    task = {"task_id": "public", "task_text": "Solve the public task."}
    captured = {}
    policy = {"tools_enabled": ["mcp__agent_world_distill__*", "Read", "Grep", "Glob"],
        "tools_disabled": ["Write", "Edit", "Bash", "Agent", "AgentSwarm", "WebSearch",
                           "FetchURL", "AskUserQuestion", "select_tools"],
        "tool_call_budget": {"limit": 65, "scope": "environment_mcp_plus_native_file_tools"},
        "result_reader": "native_kimi_tool_results", "native_read": {"default_max_chars": 80000, "max_chars": 128000},
        "compaction_trigger_ratio": 0.8}
    state = {"run": {"cli_returncode": 0}, "policy": policy, "model": protocol["model"]}

    def server(environment, **config):
        captured["server"] = config
        return {"runtime": "test"}

    def run(prompt, initial, config, root, **kwargs):
        captured["run"] = kwargs
        captured["server_config"] = deepcopy(config)
        calls, raw = [], []
        for item in observed:
            record = {"tool": item["name"].removeprefix("mcp__agent_world_distill__"),
                      "arguments": item["arguments"], "result": item["visible_output"], "error": None}
            raw.append(record)
            calls.append({**item, "output": item["visible_output"], "environment_record": record})
        trajectory = {"task": deepcopy(kwargs["task"]),
            "model": {"upstream_id": protocol["model"], "provider": protocol["provider_type"],
                      "reasoning_effort": "high", "max_context_size": 262144, "max_output_size": 131072},
            "outcome": {"status": "completed", "final_answer": "answer"}, "steps": [{"tool_calls": calls}]}
        write_json(root / "trajectory.json", trajectory)
        write_json(root / "raw/run_result.json", state["run"])
        write_json(root / "raw/mcp_preflight.json", {"status": "passed"})
        write_json(root / "raw/harness_policy.json", state["policy"])
        request = {"model": state["model"], "stream": True, "reasoning": {"effort": "high"},
                   "max_output_tokens": 131072}
        write_json(root / "raw/request.json", request)
        (root / "raw/model_requests.jsonl").write_text(json.dumps({"request": request,
            "raw_path": "raw/request.json", "sha256": sha256(root / "raw/request.json")}) + "\n")
        (root / "raw/environment_tool_calls.jsonl").write_text("".join(json.dumps(item) + "\n" for item in raw))
        return trajectory

    monkeypatch.setattr("distill.runner.make_server_config", server)
    monkeypatch.setattr("distill.runner.run_k3_distillation", run)
    return task, state, captured


def test_actual_harness_wires_the_real_protocol_and_validates_requests(pipeline, actual_harness, protocol, tmp_path):
    case = pipeline[0]
    task, state, captured = actual_harness
    trajectory, observed = draft.run_harness(case, task, protocol, tmp_path / "harness", "SECRET")
    assert len(observed) == 2
    assert captured["server"]["max_tool_calls"] == 65
    run = captured["run"]
    assert run["model_id"] == protocol["model"]
    assert run["provider_type"] == "openai_responses"
    assert run["reasoning_effort"] == "high"
    assert run["max_context_size"] == 262144
    assert run["max_output_size"] == 131072
    assert run["read_default_max_chars"] == 80000
    assert run["read_max_chars"] == 128000
    assert run["compaction_trigger_ratio"] == 0.8
    assert run["provider_headers"] == draft.PROVIDER_HEADERS
    assert run["task"] == task
    assert "solution_validation" not in captured["server_config"]


def test_author_harness_alone_exposes_materials_and_validation(pipeline, actual_harness, protocol, tmp_path):
    task, state, captured = actual_harness
    materials = {"original_task.json": {"task_text": "Original", "original_call_count": 1}}
    draft.run_harness(pipeline[0], task, protocol, tmp_path / "author-harness", "SECRET", author_materials=materials)
    assert captured["server_config"]["author_materials"] == materials
    assert captured["server_config"]["solution_validation"] == {
        "max_attempts": 3, "read_default_chars": 80000, "read_max_chars": 128000,
        "source_check": {"protocol": protocol, "original_task_text": "Original", "original_call_count": 1}}
    assert captured["run"]["max_environment_errors"] is None


@pytest.mark.parametrize("mutation", ["missing", "failed", "wrong_calls", "duplicate"])
def test_final_solution_requires_its_exact_successful_validation(solution, mutation):
    proposal = solution[0]
    report = {"request": {"calls": [{"tool": call["tool"], "arguments": call["arguments"]}
              for call in proposal["solution_calls"]]},
              "result": {"validation_id": "validated-final", "execution_passed": True}}
    reports = [report]
    if mutation == "missing":
        reports.clear()
    elif mutation == "failed":
        report["result"]["execution_passed"] = False
    elif mutation == "wrong_calls":
        report["request"]["calls"] = []
    else:
        reports.append(deepcopy(report))
    with pytest.raises(ValueError):
        draft.check_validation(proposal, reports)


def test_prompt_explains_materials_goal_rules_and_validation_in_order():
    prompt = draft.AUTHOR_PROMPT
    assert prompt.index("最终目标") < prompt.index("先读哪些材料") < prompt.index("怎样增难")
    assert prompt.index("怎样增难") < prompt.index("必要规则") < prompt.index("工作顺序")
    assert prompt.index("工作顺序") < prompt.index("参考解格式")
    assert "validate_solution_calls" in prompt
    assert "没有 Python" not in prompt
    assert "不是Python程序" not in prompt


def test_task_rules_separate_business_conditions_from_solution_routes():
    rules = draft.AUTHOR_PROMPT.split("# 必要规则\n", 1)[1].split("\n# 工作顺序", 1)[0]
    for phrase in ("工具选择、查询顺序、中间参数与回退路线写在参考解", "已知输入路径与业务规则可以公开",
                   "验收条件描述成果应满足什么", "任务形态由具体业务决定", "实际落盘能力"):
        assert phrase in rules


def test_validation_reference_rejects_changed_task_or_missing_source_check(solution):
    proposal = solution[0]
    for call in proposal["solution_calls"]:
        call.pop("observed_call")
    report = {"request": {"calls": [{"tool": call["tool"], "arguments": call["arguments"]}
              for call in proposal["solution_calls"]],
              "proposal": {key: value for key, value in proposal.items() if key != "validation_id"}},
              "result": {"validation_id": proposal["validation_id"], "execution_passed": True,
                         "source_check_passed": False, "calls": [{"error": None}, {"error": None}]}}
    with pytest.raises(ValueError, match="source-checked"):
        draft.validated_reference(proposal, [report])
    report["result"]["source_check_passed"] = True
    assert draft.validated_reference(proposal, [report])[0] == report
    proposal["task_text"] = "Changed after validation."
    with pytest.raises(ValueError, match="source-checked"):
        draft.validated_reference(proposal, [report])


def test_validation_reference_can_use_host_final_source_check(solution):
    proposal = solution[0]
    for call in proposal["solution_calls"]:
        call.pop("observed_call")
    report = {"request": {"calls": [{"tool": call["tool"], "arguments": call["arguments"]}
              for call in proposal["solution_calls"]]},
              "result": {"validation_id": proposal["validation_id"], "execution_passed": True,
                         "source_check_passed": False,
                         "calls": [{"error": None}, {"error": None}]}}
    assert draft.validated_reference(proposal, [report], host_source_check=True)[0] == report


def test_schema_choices_are_public_enums_defaults_or_booleans_only(protocol):
    options = dict(task={"task_text": "Task"}, protocol=protocol, preceding=[], current_start=None)
    source = {"kind": "schema_choice", "value": 1, "reason": "Start pagination at its public default."}
    assert draft.source_value(source, schema={"type": "integer", "default": 1}, **options) == 1
    with pytest.raises(ValueError, match="actual public"):
        draft.source_value(source, schema={"type": "string"}, **options)
    with pytest.raises(ValueError, match="actual public"):
        draft.source_value(source, schema={"type": "boolean"}, **options)


@pytest.mark.parametrize("mutation", ["budget_stop", "upstream_stop", "request_model", "read_limits", "extra_tool"])
def test_actual_harness_stops_on_runtime_or_protocol_errors(pipeline, actual_harness, protocol, tmp_path, mutation):
    task, state, captured = actual_harness
    if mutation == "budget_stop":
        state["run"]["tool_budget_stop"] = True
    elif mutation == "upstream_stop":
        state["run"]["upstream_stop"] = True
    elif mutation == "request_model":
        state["model"] = "different-model"
    elif mutation == "read_limits":
        state["policy"]["native_read"]["max_chars"] = 500000
    else:
        state["policy"]["tools_enabled"].append("Bash")
    with pytest.raises(ValueError):
        draft.run_harness(pipeline[0], task, protocol, tmp_path / "harness", "SECRET")
