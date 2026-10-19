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


def test_author_prompt_explains_problem_goal_and_author_freedom():
    assert draft.PROFILE == "single_agent_tool_list_draft_decision_design"
    prompt = draft.AUTHOR_PROMPT
    goal = prompt.split("# 任务与目标\n", 1)[1].split("\n# 环境与参考材料", 1)[0]
    principles = prompt.split("# 设计原则\n", 1)[1].split("\n# 参考解与提交", 1)[0]
    assert "评测任务执行能力的真实业务任务" in goal
    for requirement in ("领域知识", "选择合适工具", "编排后续工作", "不是必须继承的题目"):
        assert requirement in goal
    for requirement in ("先实际查看代表性工具返回和数据", "可做必要的领域调研",
                        "最省事的合法路径", "调研和求解过程留在作者材料中"):
        assert requirement in prompt
    for requirement in ("利用领域知识选择工具", "组织参数、解释返回并编排后续工作",
                        "有证据支撑的结论、决策、修改或产物"):
        assert requirement in principles
    for requirement in ("不能由一次明显调用直接完成", "至少两处关键业务判断",
                        "允许多条满足共同业务验收标准的完成路径", "整合多个阶段的证据"):
        assert requirement in principles
    for obsolete in ("本会话公开 MCP 与 Read/Grep/Glob 是实际交互入口",
                     "工具接口、公开数据和任务初态沿用原环境", "没有 Python"):
        assert obsolete not in prompt


def test_author_prompt_keeps_file_exploration_out_of_the_business_reference():
    prompt = draft.AUTHOR_PROMPT
    reference = prompt.split("# 参考解与提交\n", 1)[1].split("\n# 完成与提交", 1)[0]
    for requirement in ("参考解用 `solution_calls`", "业务 MCP 调用", "只用于读取必读材料",
                        "参数和答案证据必须可追溯"):
        assert requirement in reference
    assert "Read/Grep/Glob\n只用于读取必读材料或必要的公开参数" in reference
    assert "有效业务调用次数不少于 15 次" in reference
    assert "solution_format.json" in prompt
    assert "validate_solution_calls" in prompt
    assert '最终只返回 `{"validation_id":"成功ID"}`' in prompt
    assert "hardening_audit" not in prompt


def test_author_prompt_reframes_the_main_outcome_without_fixing_a_decision_template():
    prompt = draft.AUTHOR_PROMPT
    design = prompt.split("# 设计原则\n", 1)[1].split("\n# 参考解与提交", 1)[0]
    for requirement in ("题文只说明用户要达成什么", "可以重新选择业务目标、对象范围和交付形式",
                        "关键事实和专业判断必须有当前公开数据、工具能力或可靠调研支撑", "参数和答案证据必须可追溯"):
        assert requirement in prompt
    for requirement in ("围绕一个真实、明确的业务成果展开", "真实可解"):
        assert requirement in design
    assert "至少两个决定" not in prompt
    assert "固定两个分支" not in prompt
    for requirement in ("对象名称和 ID、文件名、坐标、单位、阈值、标准",
                        "关键事实和专业判断必须有当前公开数据、工具能力或可靠调研支撑",
                        "而不是来自题文很长、答案很长、重复查询、无关复核、格式负担或繁琐手算"):
        assert requirement in prompt


def test_author_prompt_checks_leakage_without_fixing_a_solution_route():
    leakage = draft.AUTHOR_PROMPT.split("# 设计原则\n", 1)[1].split("\n# 参考解与提交", 1)[0]
    for requirement in ("方法泄露是指", "调查起点、分析方法、工具顺序、中间对象或分支动作", "参数试验序列",
                        "没有工具名的分阶段筛选、排名或试验清单", "先 A、再 B、最后 C", "什么条件算完成",
                        "把如何调查环境、选择工具和组织调用留给执行者",
                        "题文不得出现具体工具名、MCP 接口名"):
        assert requirement in leakage
    assert "对象名称和 ID、文件名、坐标" in draft.AUTHOR_PROMPT
    assert "不能为参考路线编造这些规则再包装成业务要求" in leakage
    assert "已指定的中间决策不计为自主编排难点" in leakage
    assert len(draft.AUTHOR_PROMPT) <= 2355


def test_prompt_distinguishes_decision_depth_strategy_and_call_count():
    prompt = draft.AUTHOR_PROMPT
    for requirement in ("实质改变后续的信息需求", "不单独构成编排难度",
                        "而不只是把同一组检查换序", "15 次是验收下限，不是设计起点",
                        "已被指定的方法不能再计为自主选择的难点",
                        "不自动证明难度或无方法泄露"):
        assert requirement in prompt
    assert "处置或结论" not in prompt
    assert "real_need_preserved" not in draft.SELF_CHECKS
    assert "meaningful_hardening" not in draft.SELF_CHECKS


def test_prompt_starts_from_capabilities_and_does_not_count_tool_automated_decisions():
    prompt = draft.AUTHOR_PROMPT
    materials = prompt.split('# 环境与参考材料\n', 1)[1].split('\n# 设计原则', 1)[0]
    assert materials.index('public_tools.json') < materials.index('original_task.json')
    assert '工具已经完成的筛选、评分和结论不计为执行者的判断' in prompt
    assert '固定清单逐项调用后汇总也不算多阶段编排' in prompt
    assert '用实际返回说明两处后续工作如何改变' in prompt
    assert len(prompt) <= 2355


def test_prompt_keeps_intermediate_answers_out_of_names_and_checks_actual_content():
    assert "含在输出命名中绑定首选" in draft.AUTHOR_PROMPT
    assert "逐项对照题文核对答案" in draft.AUTHOR_PROMPT
    assert "实际产物内容；缺项或空产物" in draft.AUTHOR_PROMPT


def test_author_format_guide_matches_the_existing_submission_contract():
    from task_gen.program.author_submission import PROPOSAL_FIELDS, check_proposal_fields

    guide = read_json(Path(draft.__file__).with_name("AUTHOR_SOLUTION_FORMAT.json"))
    example = guide["proposal_example"]
    check_proposal_fields(example)
    assert set(example) == PROPOSAL_FIELDS
    assert example["self_check"] == dict.fromkeys(draft.SELF_CHECKS, True)
    assert set(example["solution_calls"][0]) == {"tool", "arguments", "argument_sources", "purpose"}
    assert {item["source"]["kind"] for item in guide["source_examples"]} == {
        "task", "schema", "file", "protocol", "call", "derived", "new_identifier",
        "schema_choice", "agent_choice", "authored_text"}


def test_author_format_source_examples_are_accepted_by_the_real_source_checker(protocol, tmp_path):
    guide = read_json(Path(draft.__file__).with_name("AUTHOR_SOLUTION_FORMAT.json"))
    public = tmp_path / "public.txt"
    public.write_text("实际原文", encoding="utf-8")
    expected = ["W01", 100_000_000, "fast", "wavelength_span", "实际原文", 80000,
                "W01", 8, "新输出名", True, "待查主题", "根据实际材料新编写的内容"]
    examples = guide["source_examples"]
    assert len(examples) == len(expected)
    for item, value in zip(examples, expected):
        source = item["source"]
        schema = {"properties": {"mode": {"default": "fast"},
                                 "metrics": {"description": "支持 wavelength_span"}}}
        if source["kind"] == "schema_choice":
            schema = {"type": "boolean"}
        elif source["kind"] in {"agent_choice", "authored_text"}:
            schema = {"type": "string"}
        actual = sources.source_value(source,
            task={"task_text": "处理W01，预算10，已有2，频率100 MHz。"}, protocol=protocol, schema=schema,
            preceding=[{"visible_output": {"data": {"id": "W01"}}, "completed_event_sequence": 1}],
            current_start=2, public_files={"公开工作区相对路径": public},
            argument_path="/content" if source["kind"] == "authored_text" else "/query")
        assert type(actual) is type(value)
        assert actual == value


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


def test_complete_proposal_feedback_identifies_missing_and_obsolete_fields(solution, protocol):
    proposal, original, observed, schemas = solution
    proposal["observed_calls"] = proposal.pop("solution_calls")
    with pytest.raises(ValueError) as caught:
        draft.check_solution(proposal, original, observed, protocol, schemas)
    message = str(caught.value)
    assert "missing=['solution_calls']" in message
    assert "unexpected=['observed_calls']" in message
    assert "reference_answer" in message
    assert "self_check_notes" in message


@pytest.mark.parametrize("uses_validation", [False, True])
@pytest.mark.parametrize("mutation", ["missing_purpose", "unexpected_result"])
def test_reference_call_feedback_identifies_step_and_invalid_fields(solution, protocol, uses_validation, mutation):
    proposal, original, observed, schemas = solution
    validation = None
    if uses_validation:
        for call in proposal["solution_calls"]:
            call.pop("observed_call")
        validation = {
            "request": {"calls": [{"tool": call["tool"], "arguments": call["arguments"]}
                                   for call in proposal["solution_calls"]]},
            "result": {"calls": [
                {"index": position, "tool": call["tool"], "result": actual["visible_output"], "error": None}
                for position, (call, actual) in enumerate(zip(proposal["solution_calls"], observed), 1)]}}
    call = proposal["solution_calls"][1]
    if mutation == "missing_purpose":
        call.pop("purpose")
    else:
        call["result"] = {}
    with pytest.raises(ValueError) as caught:
        draft.check_solution(proposal, original, observed, protocol, schemas, validation=validation)
    message = str(caught.value)
    assert "reference call 2 has invalid fields" in message
    if mutation == "missing_purpose":
        assert "missing=['purpose']; unexpected=[]" in message
    else:
        assert "missing=[]; unexpected=['result']" in message
    assert ("observed_call" in message) is not uses_validation


def test_code_checks_actual_calls_public_sources_and_basic_arithmetic(solution, protocol):
    indices, report = check(solution, protocol)
    assert indices == [1, 2]
    assert report["call_count"] == 2
    assert report["parameter_sources_match"] is True
    assert report["semantic_correctness_proved"] is False
    assert report["author_self_check_only"] is True
    assert report["new_identifier_declarations"] == [
        {"call": 2, "argument": "/name", "reason": "新建分析输出"}]


@pytest.fixture
def authored_text_source():
    return {"kind": "authored_text", "value": "New content based on observed evidence.",
            "reason": "Produce the required revision rather than quote an unchanged input.",
            "basis": [{"kind": "call", "call": 1, "pointer": "/output/value"}]}


@pytest.mark.parametrize("path", ["/body", "/comment", "/content", "/description", "/message",
                                  "/new_text", "/notes", "/rationale", "/title", "/changes/0/new_text"])
def test_authored_text_accepts_open_content_with_actual_evidence(protocol, authored_text_source, path):
    result = sources.source_value(authored_text_source, task={"task_text": "Task"}, protocol=protocol,
        schema={"type": "string"}, preceding=[{"visible_output": {"output": {"value": 4}},
                                              "completed_event_sequence": 1}],
        current_start=2, argument_path=path)
    assert result == authored_text_source["value"]


@pytest.mark.parametrize("path,schema", [
    ("/asset_id", {"type": "string"}), ("/input_file", {"type": "string"}),
    ("/threshold", {"type": "string"}), ("/measurement/value", {"type": "string"}),
    ("/notes", {"type": "number"}), ("/notes", {"type": "object"}),
    ("/notes", {"type": "string", "enum": ["New content based on observed evidence."]}),
    ("", {"type": "string"}), ("/changes/0", {"type": "string"}),
])
def test_authored_text_does_not_replace_business_parameter_sources(protocol, authored_text_source, path, schema):
    with pytest.raises(ValueError, match="limited to open text-content"):
        sources.source_value(authored_text_source, task={"task_text": "Task"}, protocol=protocol,
            schema=schema, preceding=[], current_start=2, argument_path=path)


@pytest.mark.parametrize("mutation", ["no_basis", "task_basis", "future", "late", "missing_pointer",
                                      "non_string", "no_reason", "extra_field", "unquoted_file", "wrong_quote",
                                      "private_file"])
def test_authored_text_preserves_actual_evidence_checks(protocol, authored_text_source, tmp_path, mutation):
    public = tmp_path / "public.txt"
    public.write_text("Observed public material.")
    preceding = [{"visible_output": {"output": {"value": 4}}, "completed_event_sequence": 1}]
    if mutation == "no_basis":
        authored_text_source["basis"] = []
    elif mutation == "task_basis":
        authored_text_source["basis"] = [{"kind": "task", "quote": "Task"}]
    elif mutation == "future":
        authored_text_source["basis"][0]["call"] = 2
    elif mutation == "late":
        preceding[0]["completed_event_sequence"] = 2
    elif mutation == "missing_pointer":
        authored_text_source["basis"][0]["pointer"] = "/missing"
    elif mutation == "non_string":
        authored_text_source["value"] = 4
    elif mutation == "no_reason":
        authored_text_source["reason"] = " "
    elif mutation == "extra_field":
        authored_text_source["verified"] = True
    else:
        authored_text_source["basis"] = [{"kind": "file", "path": "public.txt"}]
        if mutation == "wrong_quote":
            authored_text_source["basis"][0]["quote"] = "Not present."
        elif mutation == "private_file":
            authored_text_source["basis"][0].update(path="private.txt", quote="Observed")
    with pytest.raises((ValueError, KeyError)):
        sources.source_value(authored_text_source, task={"task_text": "Task"}, protocol=protocol,
            schema={"type": "string"}, preceding=preceding, current_start=2,
            public_files={"public.txt": public}, argument_path="/content")


def test_authored_text_accepts_actual_public_file_quote(protocol, authored_text_source, tmp_path):
    public = tmp_path / "public.txt"
    public.write_text("Observed public material.")
    authored_text_source["basis"] = [{"kind": "file", "path": "public.txt", "quote": "Observed"}]
    assert sources.source_value(authored_text_source, task={"task_text": "Task"}, protocol=protocol,
        schema={"type": "string"}, preceding=[], current_start=None,
        public_files={"public.txt": public}, argument_path="/content") == authored_text_source["value"]


@pytest.mark.parametrize("wrong_value", [False, True])
def test_complete_solution_records_authored_content_without_claiming_correctness(
        solution, protocol, authored_text_source, wrong_value):
    proposal, _, observed, schemas = solution
    text = authored_text_source["value"]
    proposal["solution_calls"][1]["arguments"]["changes"] = [{"new_text": text}]
    observed[1]["arguments"]["changes"] = [{"new_text": text}]
    schemas["measure"]["properties"]["changes"] = {
        "type": "array", "items": {"type": "object", "properties": {"new_text": {"type": "string"}}}}
    proposal["solution_calls"][1]["argument_sources"]["/changes/0/new_text"] = authored_text_source
    if wrong_value:
        authored_text_source["value"] = "Different content."
        with pytest.raises(ValueError, match="differs from its declared"):
            check(solution, protocol)
    else:
        _, report = check(solution, protocol)
        assert report["semantic_correctness_proved"] is False
        assert report["authored_text_declarations"] == [{"call": 2, "argument": "/changes/0/new_text",
            "reason": authored_text_source["reason"], "basis": authored_text_source["basis"]}]


def test_complete_solution_resolves_public_object_option_schema_only_once(solution, protocol):
    proposal, _, observed, schemas = solution
    proposal["solution_calls"][1]["arguments"]["catalog_filters"] = {}
    observed[1]["arguments"]["catalog_filters"] = {}
    proposal["solution_calls"][1]["argument_sources"]["/catalog_filters"] = {
        "kind": "schema_choice", "value": {}, "reason": "Use the public unfiltered query."}
    schemas["measure"]["properties"]["catalog_filters"] = {
        "type": "object", "properties": {"id": {"type": "string"}}, "required": [],
        "additionalProperties": False}
    _, report = check(solution, protocol)
    assert report["parameter_sources_match"] is True


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
    report = check(solution, protocol)[1]
    assert report["original_call_count"] == 1
    assert report["call_count"] == 2


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


@pytest.mark.parametrize('quote', [None, 'fabricated content'])
def test_existing_public_directory_can_supply_a_path_but_not_content(protocol, tmp_path, quote):
    public = tmp_path / 'fixture'
    public.mkdir()
    declaration = {'kind': 'file', 'path': 'fixture'}
    options = dict(task={'task_text': 'task'}, protocol=protocol, schema={},
                   preceding=[], current_start=None, public_files={'fixture': public})
    if quote is None:
        assert sources.source_value(declaration, **options) == 'fixture'
    else:
        declaration['quote'] = quote
        with pytest.raises(ValueError, match='directory sources provide a path'):
            sources.source_value(declaration, **options)


@pytest.mark.parametrize('directory_parameter', [False, True])
def test_public_path_sources_support_scope_relative_file_and_directory_arguments(protocol, tmp_path, directory_parameter):
    scope = tmp_path / 'workflow_materials'
    fixture = scope / 'fixture'
    fixture.mkdir(parents=True)
    public = fixture / 'INCAR'
    public.write_text('ENCUT = 500')
    paths = {'workflow_materials': scope, 'workflow_materials/fixture': fixture,
             'workflow_materials/fixture/INCAR': public}
    expected = 'fixture' if directory_parameter else 'fixture/INCAR'
    declaration = {'kind': 'file', 'path': 'workflow_materials/' + expected,
                   'relative_to': 'workflow_materials'}
    assert sources.source_value(declaration, task={'task_text': 'task'}, protocol=protocol,
        schema={}, preceding=[], current_start=None, public_files=paths) == expected


@pytest.mark.parametrize('base,quote', [('../private', None), ('missing', None),
    ('other', None), ('workflow_materials/INCAR', None), ('workflow_materials', 'ENCUT')])
def test_relative_public_path_sources_reject_unknown_outside_and_content_bases(protocol, tmp_path, base, quote):
    scope = tmp_path / 'workflow_materials'
    scope.mkdir()
    public = scope / 'INCAR'
    public.write_text('ENCUT = 500')
    other = tmp_path / 'other'
    other.mkdir()
    declaration = {'kind': 'file', 'path': 'workflow_materials/INCAR', 'relative_to': base}
    if quote is not None:
        declaration['quote'] = quote
    with pytest.raises(ValueError):
        sources.source_value(declaration, task={'task_text': 'task'}, protocol=protocol,
            schema={}, preceding=[], current_start=None,
            public_files={'workflow_materials': scope, 'workflow_materials/INCAR': public, 'other': other})


@pytest.mark.parametrize("path", ["/limit", "/page/limit", "/frame_selection/start"])
def test_derived_sources_preserve_context_for_nested_parameter_choices(protocol, path):
    schema = {"type": "integer", "enum": [1, 2, 3]}
    for part in reversed(path.split("/")[1:]):
        schema = {"type": "object", "properties": {part: schema}}
    declaration = {"kind": "derived", "operation": "add", "inputs": [
        {"kind": "schema_choice", "value": 1, "reason": "Choose a documented control."},
        {"kind": "agent_choice", "value": 2, "reason": "Advance the selected control."}]}
    assert sources.source_value(declaration, task={"task_text": "task"}, protocol=protocol,
        schema=schema, preceding=[], current_start=None, argument_path=path) == 3


def test_derived_sources_keep_root_schema_for_explicit_pointers(protocol):
    schema = {"properties": {"page": {"properties": {"limit": {"type": "integer", "default": 1}}}}}
    declaration = {"kind": "derived", "operation": "add", "inputs": [
        {"kind": "schema", "pointer": "/properties/page/properties/limit/default"},
        {"kind": "agent_choice", "value": 2, "reason": "Advance the page limit."}]}
    assert sources.source_value(declaration, task={"task_text": "task"}, protocol=protocol,
        schema=schema, preceding=[], current_start=None, argument_path="/page/limit") == 3


def test_derived_choices_do_not_waive_business_parameter_sources(protocol):
    declaration = {"kind": "derived", "operation": "add", "inputs": [
        {"kind": "agent_choice", "value": 1, "reason": "Guess an existing asset."},
        {"kind": "schema_choice", "value": 2, "reason": "Choose an option."}]}
    schema = {"properties": {"asset_id": {"type": "integer", "enum": [1, 2, 3]}}}
    with pytest.raises(ValueError, match="agent choices are limited"):
        sources.source_value(declaration, task={"task_text": "task"}, protocol=protocol,
            schema=schema, preceding=[], current_start=None, argument_path="/asset_id")


def test_documented_schema_options_do_not_need_to_be_added_to_the_task(solution, protocol):
    proposal, original, observed, schemas = solution
    metric = "wavelength_span"
    assert metric not in proposal["task_text"]
    schemas["measure"]["properties"]["mode"] = {
        "type": "string", "description": "支持 wavelength_span 或 sampling_density。"}
    proposal["solution_calls"][1]["arguments"]["mode"] = metric
    observed[1]["arguments"]["mode"] = metric
    proposal["solution_calls"][1]["argument_sources"]["/mode"] = {
        "kind": "schema", "pointer": "/properties/mode/description", "quote": metric}
    assert sources.check_solution(proposal, original, observed, protocol, schemas)[1]["parameter_sources_match"]


@pytest.mark.parametrize("source", [
    {"kind": "schema", "pointer": "/properties/mode/description", "quote": "invented"},
    {"kind": "schema", "pointer": "/private/description", "quote": "wavelength_span"},
    {"kind": "schema", "pointer": "/properties/mode/description", "quote": "wavelength_span", "value": "other"},
    {"kind": "protocol", "pointer": "/api_key", "quote": "secret"},
])
def test_schema_quotes_still_require_exact_public_evidence(protocol, source):
    with pytest.raises((ValueError, KeyError)):
        sources.source_value(source, task={"task_text": "task"}, protocol=protocol,
            schema={"properties": {"mode": {"description": "支持 wavelength_span。"}}},
            preceding=[], current_start=None)


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


@pytest.mark.parametrize("old_count", [1, 2, 17, 18, 40])
def test_new_task_reference_has_no_implicit_growth_requirement(solution, protocol, old_count):
    original = solution[1]
    original["reference"]["tool_calls"] *= old_count
    assert check(solution, protocol)[1]["original_call_count"] == old_count


@pytest.mark.parametrize("count,accepted", [(0, False), (14, False), (15, True), (65, True), (66, False)])
def test_design_reference_minimum_and_actual_budget_are_both_checked(solution, protocol, count, accepted):
    proposal, original, observed, schemas = solution
    first_call, first_observation = proposal["solution_calls"][0], observed[0]
    proposal["solution_calls"] = [{**deepcopy(first_call), "observed_call": position}
                                  for position in range(1, count + 1)]
    observed = [{**deepcopy(first_observation), "call_id": f"query-{position}",
                 "started_event_sequence": 2 * position - 1, "completed_event_sequence": 2 * position}
                for position in range(1, count + 1)]
    proposal["answer_sources"] = [{"kind": "call", "call": 1, "pointer": "/output/id"}]
    if not accepted:
        with pytest.raises(ValueError, match="at least 15 business MCP calls"):
            sources.check_solution(proposal, original, observed, protocol, schemas, minimum_reference_calls=15)
    else:
        report = sources.check_solution(proposal, original, observed, protocol, schemas,
                                        minimum_reference_calls=15)[1]
        assert report["call_count"] == count
        assert report["minimum_reference_calls"] == 15
        assert report["semantic_correctness_proved"] is False


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
    captured = {"sessions": []}

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

    monkeypatch.setattr(draft, "run_harness", harness)
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
    assert captured["sessions"] == []
    materials = read_json(output / "author_materials.json")
    assert materials["solution_format.json"] == read_json(
        Path(draft.__file__).with_name("AUTHOR_SOLUTION_FORMAT.json"))
    assert result["author_materials_sha256"] == sha256(output / "author_materials.json")


def test_execution_is_one_author_then_receipt_binding_then_one_blind_solver(pipeline):
    case, protocol, output, captured = pipeline
    result = draft.run_case(case, protocol, output, execute=True, api_key="SECRET_KEY")
    assert result["status"] == "gpt_completed_pending_answer_review"
    assert len(captured["sessions"]) == 2


    assert set(captured["author_materials"]) == {"original_task.json", "original_calls.json",
        "public_tools.json", "protocol.json", "workspace_files.json", "solution_format.json"}
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


def test_prior_evaluation_is_frozen_author_only_input(pipeline):
    case, protocol, output, captured = pipeline
    feedback = {"source_case": f"{case.source_run.name}__{case.task['task_id']}",
                "official_results": [{"model": "qwen", "outcome": "pass", "summary": "PRIVATE_FEEDBACK"}]}
    prepared = draft.run_case(case, protocol, output, prior_evaluation=feedback)
    assert prepared["prior_evaluation_supplied"] is True
    assert prepared["author_materials_sha256"] == sha256(output / "author_materials.json")
    materials = read_json(output / "author_materials.json")
    assert materials["previous_evaluation.json"] == feedback
    assert (output / "author_prompt.txt").read_text().startswith(draft.AUTHOR_PROMPT)
    assert "previous_evaluation.json" in (output / "author_prompt.txt").read_text()
    assert "PRIVATE_FEEDBACK" not in (output / "author_prompt.txt").read_text()
    result = draft.run_case(case, protocol, output, execute=True, api_key="SECRET", prior_evaluation=feedback)
    assert result["status"] == "gpt_completed_pending_answer_review"
    assert captured["author_materials"]["previous_evaluation.json"] == feedback
    assert len(captured["sessions"]) == 2
    assert "PRIVATE_FEEDBACK" not in json.dumps(captured["sessions"])
    assert "previous_evaluation" not in json.dumps(captured["sessions"][1])
    assert "PRIVATE_FEEDBACK" not in json.dumps(read_json(output / "candidate.json"))
    feedback["official_results"][0]["summary"] = "CHANGED"
    assert materials["previous_evaluation.json"]["official_results"][0]["summary"] == "PRIVATE_FEEDBACK"


def test_default_author_materials_and_prompt_remain_unchanged(pipeline):
    case, protocol, output, _ = pipeline
    prepared = draft.run_case(case, protocol, output)
    assert prepared["prior_evaluation_supplied"] is False
    assert "previous_evaluation.json" not in read_json(output / "author_materials.json")
    assert (output / "author_prompt.txt").read_text() == draft.AUTHOR_PROMPT


def test_one_revision_preserves_the_proposal_instead_of_redesigning_it(pipeline):
    case, protocol, output, captured = pipeline
    feedback = {"source_case": f"{case.source_run.name}__{case.task['task_id']}",
                "revision_opportunities": 1, "previous_proposal": {"task_text": "PRIVATE_PRIOR_TASK"}}
    draft.run_case(case, protocol, output, prior_evaluation=feedback)
    prompt = (output / "author_prompt.txt").read_text()
    revision = prompt.split("# 本轮是原方案修订\n", 1)[1]
    assert "保留业务目标、对象范围和实质难度" in revision
    assert "不另换委托、不扩张交付" in revision
    assert "不能只修被指出的一项" in revision
    assert "再自主设计本轮委托" not in prompt
    assert "PRIVATE_PRIOR_TASK" not in prompt
    assert read_json(output / "author_materials.json")["previous_evaluation.json"] == feedback
    assert captured["sessions"] == []


@pytest.mark.parametrize("feedback", [{}, {"source_case": "another_source__task"}, [], "not an object"])
def test_prior_evaluation_rejects_wrong_source_before_preparing(pipeline, feedback):
    case, protocol, output, captured = pipeline
    with pytest.raises(ValueError, match="same original source case"):
        draft.run_case(case, protocol, output, prior_evaluation=feedback)
    assert not output.exists()
    assert captured["sessions"] == []


def test_prepared_prior_evaluation_cannot_be_replaced(pipeline):
    case, protocol, output, captured = pipeline
    feedback = {"source_case": f"{case.source_run.name}__{case.task['task_id']}", "summary": "original"}
    draft.run_case(case, protocol, output, prior_evaluation=feedback)
    with pytest.raises(ValueError, match="frozen prepared author materials"):
        draft.run_case(case, protocol, output, execute=True, api_key="SECRET",
                       prior_evaluation={**feedback, "summary": "replacement"})
    assert captured["sessions"] == []


def test_prior_evaluation_can_be_omitted_when_executing_frozen_preparation(pipeline):
    case, protocol, output, captured = pipeline
    feedback = {"source_case": f"{case.source_run.name}__{case.task['task_id']}", "summary": "PRIVATE_FEEDBACK"}
    draft.run_case(case, protocol, output, prior_evaluation=feedback)
    result = draft.run_case(case, protocol, output, execute=True, api_key="SECRET", solve_independently=False)
    assert result["status"] == "reference_validated_pending_semantic_verification"
    assert captured["author_materials"]["previous_evaluation.json"] == feedback


def test_reference_only_mode_waits_for_semantic_verification_before_blind_solving(pipeline):
    case, protocol, output, captured = pipeline
    result = draft.run_case(case, protocol, output, execute=True, api_key="SECRET_KEY", solve_independently=False)
    assert result["status"] == "reference_validated_pending_semantic_verification"
    assert result["independent_solver_started"] is result["correctness_verified"] is False
    assert len(captured["sessions"]) == 1


def test_receipt_submission_binds_the_same_complete_proposal_without_another_session(pipeline, monkeypatch):
    case, protocol, output, captured = pipeline
    author = draft.run_harness

    def receipt_harness(*args, **kwargs):
        trajectory, observed = author(*args, **kwargs)
        trajectory["outcome"]["final_answer"] = '{"validation_id":"validated-final"}'
        return trajectory, observed

    monkeypatch.setattr(draft, "run_harness", receipt_harness)

    result = draft.run_case(case, protocol, output, execute=True, api_key="SECRET", solve_independently=False)

    assert result["status"] == "reference_validated_pending_semantic_verification"
    assert result["author_submission"]["kind"] == "validation_receipt"
    assert len(captured["sessions"]) == 1
    validation = json.loads((output / "author/execution/raw/solution_validations.jsonl").read_text())
    assert read_json(output / "proposal.json") == {
        **validation["request"]["proposal"], "validation_id": "validated-final"}
    assert read_json(output / "source_check.json")["parameter_sources_match"] is True
    assert result["correctness_verified"] is False


def test_fragmented_final_output_is_not_mistaken_for_an_inner_tool_object(pipeline, monkeypatch):
    case, protocol, output, captured = pipeline
    author = draft.run_harness

    def fragmented_harness(*args, **kwargs):
        trajectory, observed = author(*args, **kwargs)
        trajectory["outcome"]["final_answer"] = '}, {"tool":"measure","arguments":{}}'
        return trajectory, observed

    monkeypatch.setattr(draft, "run_harness", fragmented_harness)

    result = draft.run_case(case, protocol, output, execute=True, api_key="SECRET", solve_independently=False)

    assert result["status"] == "stage_error"
    assert "JSONDecodeError" in result["error"]
    assert not (output / "proposal.json").exists()
    assert len(captured["sessions"]) == 1


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


@pytest.mark.parametrize("changed", ["prompt", "solution_format", "source", "task_id", "protocol"])
def test_changed_prepared_inputs_cannot_be_executed(pipeline, changed):
    case, protocol, output, captured = pipeline
    draft.run_case(case, protocol, output)
    if changed == "prompt":
        (output / "author_prompt.txt").write_text("Changed policy")
    elif changed == "solution_format":
        materials = read_json(output / "author_materials.json")
        materials["solution_format.json"]["proposal_example"]["task_text"] = "Changed format"
        write_json(output / "author_materials.json", materials)
    elif changed == "source":
        (case.initial_state / "public.txt").write_text("Changed state")
    elif changed == "task_id":
        case.task["task_id"] = "another"
    else:
        write_json(protocol, {**read_json(protocol), "model": "another"})
    with pytest.raises(ValueError):
        draft.run_case(case, protocol, output, execute=True, api_key="SECRET_KEY")
    assert captured["sessions"] == []


def test_invalid_author_reference_stops_without_retry_or_other_agents(pipeline, solution):
    case, protocol, output, captured = pipeline
    solution[0]["solution_calls"][1]["argument_sources"]["/id"]["call"] = 99
    result = draft.run_case(case, protocol, output, execute=True, api_key="key")
    assert result["status"] == "stage_error"
    assert len(captured["sessions"]) == 1


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
        "submission_gate": True,
        "read_default_chars": 80000, "read_max_chars": 128000,
        "source_check": {"protocol": protocol, "minimum_reference_calls": 15,
                         "original_task_text": "Original", "original_call_count": 1}}
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
    sections = ["任务与目标", "环境与参考材料", "设计原则", "参考解与提交", "完成与提交"]
    positions = [prompt.index("# " + section + "\n") for section in sections]
    assert positions == sorted(positions)
    assert len(prompt) < 2800
    for material in ("original_task.json", "original_calls.json", "public_tools.json", "protocol.json",
                     "workspace_files.json", "solution_format.json"):
        assert material in prompt.split("# 环境与参考材料\n", 1)[1].split("\n# 设计原则", 1)[0]
    assert "validate_solution_calls" in prompt
    assert "没有 Python" not in prompt
    assert "不是Python程序" not in prompt
    for requirement in ("完整方案", "同一会话修改", "source_check_passed", "字段和交付均闭合",
                        "submit_validated_solution", "accepted=true", "正式 verifier"):
        assert requirement in prompt


def test_task_rules_separate_business_conditions_from_solution_routes():
    design = draft.AUTHOR_PROMPT.split("# 设计原则\n", 1)[1].split("\n# 参考解与提交", 1)[0]
    for phrase in ("目标、范围、交付内容和验收标准必须明确",
                   "关键事实和专业判断必须有当前公开数据、工具能力或可靠调研支撑",
                   "题文只说明用户要达成什么", "什么条件算完成",
                   "把如何调查环境、选择工具和组织调用留给执行者"):
        assert phrase in design


def test_validation_reference_rejects_changed_task_or_missing_source_check(solution):
    proposal = solution[0]
    for call in proposal["solution_calls"]:
        call.pop("observed_call")
    report = {"request": {"calls": [{"tool": call["tool"], "arguments": call["arguments"]}
              for call in proposal["solution_calls"]],
              "proposal": {key: value for key, value in proposal.items() if key != "validation_id"}},
              "result": {"validation_id": proposal["validation_id"], "execution_passed": True,
                         "source_check_passed": False, "calls": [{"index": position, "tool": call["tool"], "arguments": call["arguments"], "error": None}
                                   for position, call in enumerate(proposal["solution_calls"], 1)]}}
    with pytest.raises(ValueError, match="source-checked"):
        draft.validated_reference(proposal, [report])
    report["result"]["source_check_passed"] = True
    assert draft.validated_reference(proposal, [report])[0] == report
    proposal["task_text"] = "Changed after validation."
    with pytest.raises(ValueError, match="source-checked"):
        draft.validated_reference(proposal, [report])


def test_validation_reference_never_bypasses_missing_author_source_check(solution):
    proposal = solution[0]
    for call in proposal["solution_calls"]:
        call.pop("observed_call")
    report = {"request": {"calls": [{"tool": call["tool"], "arguments": call["arguments"]}
              for call in proposal["solution_calls"]]},
              "result": {"validation_id": proposal["validation_id"], "execution_passed": True,
                         "source_check_passed": False,
                         "calls": [{"index": position, "tool": call["tool"], "arguments": call["arguments"], "error": None}
                                   for position, call in enumerate(proposal["solution_calls"], 1)]}}
    with pytest.raises(ValueError, match="source-checked"):
        draft.validated_reference(proposal, [report])


@pytest.mark.parametrize("mutation", [None, "wrong_file", "wrong_business_value", "missing_call",
                                     "missing_request", "wrong_tool", "wrong_order", "missing_layout"])
def test_validation_binds_runtime_normalized_paths_and_execution_identity(solution, tmp_path, mutation):
    from harness.resources import ResourceCatalog

    proposal, original, observed, schemas = solution
    for call in proposal["solution_calls"]:
        call.pop("observed_call")
    proposal["solution_calls"][1]["arguments"]["path"] = "public/data.txt"
    proposal["solution_calls"][1]["argument_sources"]["/path"] = {"kind": "task", "quote": "public/data.txt"}
    request_calls = [{"tool": call["tool"], "arguments": call["arguments"]}
                     for call in proposal["solution_calls"]]
    runtime_calls = deepcopy(request_calls)
    runtime_calls[1]["arguments"]["path"] = "data.txt"
    report = {"request": {"calls": request_calls,
                           "proposal": {key: value for key, value in proposal.items() if key != "validation_id"}},
              "result": {"validation_id": proposal["validation_id"], "execution_passed": True,
                         "source_check_passed": True,
                         "calls": [{"index": index, "tool": call["tool"], "arguments": runtime_calls[index - 1]["arguments"],
                                    "result": {}, "error": None}
                                   for index, call in enumerate(request_calls, 1)]}}
    schemas["measure"]["properties"]["path"] = {"type": "string", "x-resource-scope": "files"}
    environment = {"filesystem_scopes": [{"scope_id": "files"}],
        "tools": [{"name": name, "inputSchema": schema,
                   "usageConditions": {"targetResources": ["files"]}} for name, schema in schemas.items()]}
    resources = ResourceCatalog(environment, lambda scope_id: tmp_path / "public",
        workspace_root=tmp_path, scope_mounts={"files": "public"})
    if mutation == "wrong_file":
        report["result"]["calls"][1]["arguments"]["path"] = "other.txt"
    elif mutation == "wrong_business_value":
        report["result"]["calls"][1]["arguments"]["id"] = "OTHER"
    elif mutation == "missing_call":
        report["result"]["calls"].pop()
    elif mutation == "missing_request":
        report["request"]["calls"] = report["request"]["calls"][:-1]
    elif mutation == "wrong_tool":
        report["result"]["calls"][1]["tool"] = "query"
    elif mutation == "wrong_order":
        report["result"]["calls"][1]["index"] = 1
    elif mutation == "missing_layout":
        resources = None
    if mutation:
        with pytest.raises(ValueError):
            draft.validated_reference(proposal, [report], resources=resources)
    else:
        assert draft.validated_reference(proposal, [report], resources=resources)[0] == report


def test_schema_choices_are_public_enums_defaults_or_booleans_only(protocol):
    options = dict(task={"task_text": "Task"}, protocol=protocol, preceding=[], current_start=None)
    source = {"kind": "schema_choice", "value": 1, "reason": "Start pagination at its public default."}
    assert draft.source_value(source, schema={"type": "integer", "default": 1}, **options) == 1
    with pytest.raises(ValueError, match="actual public"):
        draft.source_value(source, schema={"type": "string"}, **options)
    with pytest.raises(ValueError, match="actual public"):
        draft.source_value(source, schema={"type": "boolean"}, **options)


@pytest.mark.parametrize("value,schema,accepted", [
    (None, {"type": ["string", "null"]}, True),
    (None, {"type": "null"}, True),
    (None, {"type": "string"}, False),
    (True, {"type": ["boolean", "null"]}, True),
    (1, {"type": ["string", "null"]}, False),
])
def test_nullable_schema_choices_follow_actual_public_types(protocol, value, schema, accepted):
    declaration = {"kind": "schema_choice", "value": value, "reason": "Select an explicitly allowed public option."}
    options = dict(task={"task_text": "Task"}, protocol=protocol, preceding=[], current_start=None, schema=schema)
    if accepted:
        assert sources.source_value(declaration, **options) is value
    else:
        with pytest.raises(ValueError):
            sources.source_value(declaration, **options)


@pytest.mark.parametrize("path,value,schema,accepted", [
    ("/search_query", "laser overheating", {"type": "string"}, True),
    ("/page", 2, {"type": "integer"}, True),
    ("/fields", ["title", "status"], {"type": "array", "items": {"type": "string"}}, True),
    ("/selected_fields", ["date", "model"], {"type": "array", "items": {"type": "string"}}, True),
    ("/comparison_fields", ["grid", "pixel_size"], {"type": "array", "items": {"type": "string"}}, True),
    ("/page_number", 2, {"type": "integer"}, True),
    ("/preview_rows", 5, {"type": "integer"}, True),
    ("/max_matches", 3, {"type": "integer"}, True),
    ("/max_matches_per_source", 3, {"type": "integer"}, True),
    ("/max_matches_per_page", 3, {"type": "integer"}, True),
    ("/display/max_matches", 3, {"type": "integer"}, True),
    ("/max_depth", 3, {"type": "integer", "minimum": 0}, True),
    ("/extension", "txt", {"type": "string"}, True),
    ("/filters/limit", 20, {"type": "integer"}, True),
    ("/filters/text_contains", "focus topic", {"type": "string"}, True),
    ("/pagination/page_number", 2, {"type": "integer"}, True),
    ("/page/limit", 100, {"type": "integer"}, True),
    ("/page/offset", 0, {"type": "integer"}, True),
    ("/group_by/0", "design_id", {"type": "string"}, True),
    ("/comparison_fields/1", "grid", {"type": "string"}, True),
    ("/frame_selection/start", 0, {"type": "integer"}, True),
    ("/frame_selection/stop", 1, {"type": "integer"}, True),
    ("/frame_selection/step", 1, {"type": "integer"}, True),
    ("/sort/direction", "ascending", {"type": "string"}, True),
    ("/page", 2, {"type": ["integer", "null"]}, True),
    ("/fields", ["title", "status"], {"type": ["array", "null"], "items": {"type": "string"}}, True),
    ("/fields", ["title", 2], {"type": "array", "items": {"type": ["string", "integer"]}}, True),
    ("/record_id", "invented-record", {"type": "string"}, False),
    ("/input_file", "missing.csv", {"type": "string"}, False),
    ("/value", 37, {"type": "number"}, False),
    ("/filters/record_id", "invented-record", {"type": "string"}, False),
    ("/display/input_file", "missing.csv", {"type": "string"}, False),
    ("/measurement/limit", 12, {"type": "integer"}, False),
    ("/measurement/max_depth", 3, {"type": "integer"}, False),
    ("/measurement/max_matches", 3, {"type": "integer"}, False),
    ("/filters/nested/limit", 12, {"type": "integer"}, False),
    ("/energy_tolerance", 0.2, {"type": "number"}, False),
    ("/object_ids/0", "invented", {"type": "string"}, False),
    ("/frame_selection/input_file", "missing.gif", {"type": "string"}, False),
    ("/frame_selection/start", True, {"type": "integer"}, False),
    ("/page", True, {"type": "integer"}, False),
    ("/max_matches", True, {"type": "integer"}, False),
])
def test_agent_choices_allow_controls_but_require_evidence_for_business_values(protocol, path, value, schema, accepted):
    declaration = {"kind": "agent_choice", "value": value, "reason": "Select a search or display control."}
    options = dict(task={"task_text": "Task"}, protocol=protocol, preceding=[], current_start=None,
                   schema=schema, argument_path=path)
    if accepted:
        assert sources.source_value(declaration, **options) == value
    else:
        with pytest.raises(ValueError):
            sources.source_value(declaration, **options)


@pytest.mark.parametrize("changed_field", ["task_text", "reference_answer", "argument_sources", "validation_id"])
def test_submissions_changed_after_successful_validation_stop_without_host_reexecution(pipeline, monkeypatch, changed_field):
    from task_gen.program import single_agent_hardening

    case, protocol, output, captured = pipeline
    author = single_agent_hardening.run_harness

    def changed_submission(*args, **kwargs):
        trajectory, observed = author(*args, **kwargs)
        proposal = json.loads(trajectory["outcome"]["final_answer"])
        if changed_field == "argument_sources":
            proposal["solution_calls"][1]["argument_sources"]["/id"] = {"kind": "task", "quote": "W01"}
        else:
            proposal[changed_field] += " changed"
        trajectory["outcome"]["final_answer"] = json.dumps(proposal)
        return trajectory, observed

    monkeypatch.setattr(single_agent_hardening, "run_harness", changed_submission)
    result = single_agent_hardening.run_case(case, protocol, output, execute=True, api_key="key")
    assert result["status"] == "stage_error"
    assert len(captured["sessions"]) == 1
    assert not (output / "author/final-validation").exists()


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
