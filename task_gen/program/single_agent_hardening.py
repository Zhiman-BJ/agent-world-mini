"""Draft: one author/solver session, code checks, then an independent GPT attempt."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

from .utils.io import read_json, write_json
from .proposal_sources import (
    EXPLORATION_TOOLS, PUBLIC_PROTOCOL_FIELDS, SELF_CHECKS, UNIT_FACTORS, _value_pointers,
    argument_leaves, argument_schema, check_solution, parameter_source_values,
    pointer, same_json, same_source_value, source_value,
)


PROFILE = "single_agent_tool_list_draft_v10_closed_loop"
PROVIDER_HEADERS = {"User-Agent": "codex_cli_rs/0.115.0", "originator": "codex_cli_rs",
                    "OpenAI-Beta": "responses=experimental"}
AUTHOR_PROMPT = """# 你的工作与最终目标
你负责把一道已有任务改写成更有挑战、真实且可解的任务，并在同一个会话中完成参考解。
交付物是中文新题、完整参考答案、顺序工具调用列表，以及通过快速验证的记录。
新题服务原任务相关的真实用户需求；参考解能从同一干净初态执行，独立模型能依据公开材料求解。

# 先读哪些材料
专用只读目录 ../.task-authoring/ 中的以下文件是本任务的必读材料：
1. original_task.json：原任务、原参考答案、原参考调用数，理解用户实际需要的成果。
2. original_calls.json：原参考工具名和参数，理解已有工作及调用链。
3. public_tools.json：工具说明、输入和输出格式；结合本会话工具定义理解可完成的操作。
4. protocol.json：上下文、读取长度、工具次数和推理配置。
5. workspace_files.json：公开工作区文件目录；根据任务范围读取相关数据、说明和业务规则文件。
业务探索优先使用MCP工具；Read/Grep/Glob可用于作者读材料、查找公开参数，仅在必要时使用，保留在探索轨迹中，不进入参考解调用列表。

# 怎样增难
从原题的现实用途出发，增加一项会改变最终成果的必要决定，例如选择处理对象、确定方案或配置；具体决定由场景确定。
使这个决定依赖不同工具返回的信息，并影响后续工作，由求解者自行选择和编排工具。
用最短合理解法检查：原解法不能直接完成新目标，新增工作对最终成果必需，保留原工作的实质难度。
题文只呈现用户需求和验收标准；发现过程、关键决定的真实结果依赖写在参考解与real_need。

# 必要规则
需求真实且可解，允许改写原目标、范围和交付。
题文交代业务目标、对象范围、必要判据和验收标准；工具选择、查询顺序、中间参数与回退路线写在参考解。
已知输入路径与业务规则可以公开；探索得到的候选、证据位置、中间标识和配置组合留在参考解，参数来源引用探索结果。
验收条件描述成果应满足什么，不逐项告诉求解者怎样得到；同一成果接受等效合法路径。
任务形态由具体业务决定，主成果应可直接使用，保留原工作的实质难度。
使用已有工具和数据，参数与结论有公开依据，区分事实、假设和待实施事项。
参考解调用列表只包含公开业务MCP工具；作者查得参数后直接填入对应接口，文件查看不计入参考调用数。
文件或状态交付须在验证结果中实际存在；只返回内容的工具对应内容交付，实际落盘能力才对应文件交付。
专业计算使用现有领域工具，模型负责判断、结果传递、筛选和简单四则运算。
原参考少于18次时增加必要调用；达到18次时保留实质难度，调用数按需求安排。

# 工作顺序：改题、求解、验证、修正
1. 读完必读材料并探索相关公开工具和数据，确定值得扩展的现实需求。
2. 同时设计新题和解题路径，探索实际能力，整理完成新成果所需的工具列表。
3. 将完整新题、答案、参数来源与调用列表一起传给 validate_solution_calls：
   {"calls":[{"tool":"公开工具短名","arguments":{}}],"proposal":下述完整JSON（省略validation_id）}。
   calls与proposal.solution_calls的工具和参数保持一致；返回validation_id、execution_passed、每步结果、状态变化、source_check_passed及错误。
   验证直接复用正式MCP业务接口，不重新启动完整Kimi会话；每次从独立的干净初态副本执行。
4. 根据实际结果、状态与全部错误修改完整方案后再次校验。修改题文、答案或来源而调用列表相同时，
   自动复用该列表的成功执行结果；修改列表则从干净初态重新执行。校验修正受会话总预算约束，不另设3次上限。
5. execution_passed与source_check_passed均为true且成果确实可交付后，
   返回同一proposal，只补上成功validation_id；如需改题或改答案，先再校验。
执行与来源通过之后，仍要核对新题的成果和业务结论确实成立。
最终列表保留为解题服务的调用；探索和验证工具本身单独留在会话记录中。

# 参考解格式和参数来源
solution_calls 按顺序记录最后一次成功验证实际执行的工具名、参数、来源和用途。
调用身份由 validation_id 和列表序号确定，宿主从真实验证记录提取，不手填会话调用索引。
tool 只填写公开业务MCP短名；arguments填入作者实际查得的参数，不把查文件的动作加入列表。
argument_sources 用参数对象的JSON Pointer记录来源，如/id；同源数组或对象可整体引用，如/ids，每个叶子恰好覆盖一次。
- {"kind":"task","quote":"新题中出现的参数值"}：引用完整连续原文。
- {"kind":"task","quote":"100 MHz","as":"frequency_hz"}：将题文明确给出的频率单位换算为工具所需的Hz数值。
- {"kind":"schema","pointer":"/properties/mode/enum/0"}：引用该工具公开inputSchema。
- {"kind":"file","path":"公开工作区相对路径","quote":"文件中的实际参数"}：引用冻结公开文件；数值可加"as":"json"，仅引用文件路径时省略quote。
- {"kind":"protocol","pointer":"/read_default_chars"}：引用protocol.json中的常量。
- {"kind":"call","call":1,"pointer":"/data/id"}：
  call 是最终列表中的前序序号，pointer 指向成功验证返回calls中该步的result，不含CLI文本包装。
  省略quote即可引用字段原有的JSON值；截取字符串时填写返回内容的连续原文。
  仅在quote本身为JSON文本时加"as":"json"，将其解析为数值或容器。
- {"kind":"derived","operation":"subtract","inputs":[来源1,来源2]}：
  两个数字来源的add/subtract/multiply/divide运算。
- {"kind":"new_identifier","value":"新输出名","reason":"新建标识的业务用途"}：声明新输出标识。
- {"kind":"schema_choice","value":1,"reason":"分页从第一页开始"}：选择公开Schema允许的枚举、布尔或默认值。
  数组可整体选择items.enum中的元素，满足长度与去重约束；可选参数可省略以使用工具默认值。
  Schema中没有枚举或默认值的业务对象标识仍引用题文或前序结果。
- {"kind":"agent_choice","value":"待查主题","reason":"根据任务目标选择检索词"}：声明检索词、分页、排序、投影或展示参数的自主选择。
  自主选择仍须符合公开Schema；业务对象标识、输入文件与实测值引用题文、公开文件或前序结果。
参数来源直接填写；完整校验的错误在本会话内修正，宿主只绑定最终提交与成功记录。
answer_sources 用最终列表的call、pointer和可选quote指向实际读到的答案证据。
validation_id 填最后一次通过验证且调用列表与提交列表一致的真实ID。
最终回复为以下JSON对象：
{"task_text":"完整中文新题","real_need":"原成果、新成果、现实用途、原解法为何不足及新增必要判断和操作",
 "reference_answer":"基于本次实测的完整答案",
 "solution_calls":[{"tool":"公开工具短名","arguments":{},
 "argument_sources":{},"purpose":"该调用如何服务主要成果"}],
 "answer_sources":[{"kind":"call","call":1,"pointer":"/data"}],
 "validation_id":"最后一次成功验证的ID",
 "self_check":{"real_need_preserved":true,"meaningful_hardening":true,
 "public_rules_complete":true,"computation_boundary_respected":true,
 "no_padding":true,"answer_supported":true,"solver_plans_route":true,
 "deliverables_supported":true},"self_check_notes":["新增关键决定的真实依据、题文保留的业务条件、成果实际状态和仍有的限制"]}。
宿主只核对最终提交与成功的完整校验记录一致，复用该记录的实测结果与状态，不再执行参考列表。
最后使用Kimi Code和相同协议
让独立GPT会话只看新题求解，保存答案和轨迹供对照检查。
"""


def run_harness(case, task: dict, protocol: dict, root: Path, api_key: str,
                *, author_materials: dict | None = None) -> tuple[dict, list[dict]]:
    from distill.runner import make_server_config, run_k3_distillation

    from .reference_evidence import check_upstream_requests, ordered_interactions
    from .reference_replay import _jsonl

    config = make_server_config(case.environment, max_tool_calls=protocol["max_total_tool_calls"], runtime=case.runtime)
    if author_materials is not None:
        config["author_materials"] = author_materials
        config["solution_validation"] = {
            "read_default_chars": protocol["read_default_chars"], "read_max_chars": protocol["read_max_chars"],
            "source_check": {"protocol": protocol,
                "original_task_text": author_materials["original_task.json"]["task_text"],
                "original_call_count": author_materials["original_task.json"]["original_call_count"]}}
    trajectory = run_k3_distillation(task["task_text"], case.initial_state, config, root,
        kimi_bin=os.environ.get("KIMI_CODE_BIN", "kimi"), base_url=protocol["provider_base_url"],
        api_key=api_key, model_id=protocol["model"], provider_type=protocol["provider_type"],
        reasoning_effort=protocol["reasoning_effort"], max_context_size=protocol["context_cap"],
        max_output_size=protocol["max_output_tokens"], read_default_max_chars=protocol["read_default_chars"],
        read_max_chars=protocol["read_max_chars"], compaction_trigger_ratio=protocol["compaction_trigger_ratio"],
        max_environment_errors=None if author_materials is not None else 1, max_upstream_retries=4, task=task,
        provider_headers=PROVIDER_HEADERS,
        evaluation={"kind": PROFILE, "formal_accuracy_evaluation": False})
    run = read_json(root / "raw/run_result.json")
    if (run.get("cli_returncode") != 0 or run.get("error")
            or any(run.get(field) for field in ("environment_stop", "tool_budget_stop", "upstream_stop"))
            or read_json(root / "raw/mcp_preflight.json").get("status") != "passed"):
        raise ValueError("harness stopped or MCP preflight failed; this is not a scored content failure")
    check_upstream_requests(root, protocol)
    observed = [] if author_materials is not None else ordered_interactions(
        trajectory, _jsonl(root / "raw/environment_tool_calls.jsonl"), task, protocol,
        read_json(root / "raw/harness_policy.json"))
    return trajectory, observed


def check_validation(proposal: dict, reports: list[dict]) -> dict:
    if any(call.get("tool") in EXPLORATION_TOOLS for call in proposal["solution_calls"]):
        raise ValueError("reference lists contain business MCP tools only; file tools are exploration-only")
    identifier = proposal["validation_id"]
    matches = [report for report in reports if report["result"].get("validation_id") == identifier]
    if not isinstance(identifier, str) or not identifier or len(matches) != 1:
        raise ValueError("final solution must cite a real, unique validation record")
    validation = matches[0]
    calls = [{"tool": call["tool"], "arguments": call["arguments"]} for call in proposal["solution_calls"]]
    if (validation["result"].get("execution_passed") is not True
            or not same_json(validation["request"].get("calls"), calls)):
        raise ValueError("final solution differs from its successful clean-state validation")
    return {"validation_id": identifier, "execution_passed": True, "semantic_correctness_proved": False}


def validated_reference(proposal: dict, reports: list[dict]) -> tuple[dict, dict]:
    checked = check_validation(proposal, reports)
    validation = next(report for report in reports if report["result"].get("validation_id") == proposal["validation_id"])
    submitted = validation["request"].get("proposal")
    expected = {key: value for key, value in proposal.items() if key != "validation_id"}
    source_checked = validation["result"].get("source_check_passed") is True
    submitted_matches = isinstance(submitted, dict) and same_json(
        {key: value for key, value in submitted.items() if key != "validation_id"}, expected)
    if not source_checked or not submitted_matches:
        raise ValueError("final proposal differs from its source-checked validation; validate the complete final proposal")
    records = validation["result"].get("calls", [])
    if len(records) != len(proposal["solution_calls"]) or any(record.get("error") for record in records):
        raise ValueError("successful validation is missing its complete actual execution evidence")
    checked["state"] = validation["result"].get("state")
    return validation, checked


def prepare_case(case, protocol: dict, output: Path) -> dict:
    from distill.taskgen_verifier import _verifier_environment
    from harness.mcp_protocol import public_tools

    from .reference_evidence import _atomic_json, check_protocol, public_task, sha256, source_fingerprint

    check_protocol(protocol)
    if not case.reference_calls:
        raise ValueError("the original observed call baseline must not be empty")
    if output.resolve().is_relative_to(case.source_run.resolve()) or case.source_run.resolve().is_relative_to(output.resolve()):
        raise ValueError("draft output must not overlap the frozen original environment")
    original = {**public_task(case.task), "reference_answer": case.task["reference"]["answer"],
                "original_call_count": sum(call["tool"] not in EXPLORATION_TOOLS for call in case.reference_calls)}
    author_input = {"original": original, "protocol": {field: protocol[field] for field in PUBLIC_PROTOCOL_FIELDS},
                    "input_scope": "Existing task; public tools are exposed by the unchanged harness."}
    workspace_files = [path.relative_to(case.initial_state).as_posix()
                       for path in sorted((case.initial_state / "filesystem_scopes").rglob("*")) if path.is_file()]
    materials = {"original_task.json": original,
        "original_calls.json": [{"tool": call["tool"], "arguments": call.get("arguments", {})}
                                 for call in case.reference_calls],
        "public_tools.json": public_tools(_verifier_environment(case)["tools"]),
        "protocol.json": author_input["protocol"],
        "workspace_files.json": {"initial_state_files": workspace_files,
            "filesystem_scopes": case.environment.get("filesystem_scopes", []),
            "path_note": "文件在当前公开工作区按scope挂载；单scope挂载根目录，多scope分别挂载子目录。"}}
    prompt = AUTHOR_PROMPT
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "author_input.json", author_input)
    write_json(output / "author_materials.json", materials)
    write_json(output / "protocol.json", protocol)
    write_json(output / "source_fingerprint.json", source_fingerprint(case))
    (output / "author_prompt.txt").write_text(prompt, encoding="utf-8")
    report = {"profile": PROFILE, "status": "awaiting_user_confirmation", "model": protocol["model"],
        "source_case": f"{case.source_run.name}__{case.task['task_id']}",
        "implementation_sha256": sha256(Path(__file__)), "author_prompt_sha256": sha256(output / "author_prompt.txt"),
        "author_input_sha256": sha256(output / "author_input.json"), "protocol_sha256": sha256(output / "protocol.json"),
        "author_materials_sha256": sha256(output / "author_materials.json"),
        "source_fingerprint_sha256": sha256(output / "source_fingerprint.json"),
        "provider_headers": PROVIDER_HEADERS,
        "author_session_limit": 1, "independent_solver_session_limit": 1,
        "checklist_agents": 0, "business_review_agents": 0, "verifier_agents": 0,
        "author_started": False, "independent_solver_started": False,
        "correctness_verified": False, "formal_accuracy_evaluation": False, "release_ready": False}
    _atomic_json(output / "result.json", report)
    return report


def run_case(case, protocol_path: Path, output: Path, *, api_key: str | None = None,
             execute: bool = False, solve_independently: bool = True) -> dict:
    from utils.io import extract_json_object

    from .reference_evidence import _atomic_json, check_protocol, public_task, sha256, source_fingerprint
    from .reference_replay import _jsonl

    protocol = read_json(protocol_path)
    check_protocol(protocol)
    report = read_json(output / "result.json") if output.exists() else prepare_case(case, protocol, output)
    if not execute:
        return report
    if report.get("status") != "awaiting_user_confirmation" or not api_key:
        raise ValueError("execution requires a fresh prepared draft and an API key; no automatic agent retry")
    for name, field in (("author_prompt.txt", "author_prompt_sha256"), ("author_input.json", "author_input_sha256"),
                        ("author_materials.json", "author_materials_sha256"),
                        ("protocol.json", "protocol_sha256"), ("source_fingerprint.json", "source_fingerprint_sha256")):
        if sha256(output / name) != report.get(field):
            raise ValueError("prepared inputs changed after the proposed flow was frozen")
    if (sha256(Path(__file__)) != report["implementation_sha256"]
            or read_json(output / "protocol.json") != protocol
            or report["source_case"] != f"{case.source_run.name}__{case.task['task_id']}"
            or read_json(output / "source_fingerprint.json") != source_fingerprint(case)):
        raise ValueError("prepared implementation, protocol, or original source changed")
    try:
        report.update(status="authoring_and_solving", author_started=True)
        _atomic_json(output / "result.json", report)
        author_task = {"task_id": case.task["task_id"] + "_author", "environment_id": case.task["environment_id"],
                       "task_text": (output / "author_prompt.txt").read_text(encoding="utf-8")
                       + "\n本次必读材料的绝对目录：" + str((output / "author/execution/.task-authoring").resolve())
                       + "。使用该目录的绝对路径读取材料。\n"}
        trajectory, observed = run_harness(case, author_task, protocol, output / "author/execution", api_key,
                                           author_materials=read_json(output / "author_materials.json"))
        if read_json(output / "source_fingerprint.json") != source_fingerprint(case):
            raise ValueError("the author run changed the frozen original source")
        proposal = extract_json_object(trajectory["outcome"]["final_answer"])
        write_json(output / "proposal.json", proposal)
        reports = _jsonl(output / "author/execution/raw/solution_validations.jsonl")
        validation, clean = validated_reference(proposal, reports)
        checked = deepcopy(validation["result"]["source_check"])
        if checked.get("parameter_sources_match") is not True:
            raise ValueError("final source validation did not confirm public parameter sources")
        checked["clean_state_validation"] = clean
        write_json(output / "source_check.json", checked)
        calls = [{key: deepcopy(record[key]) for key in ("tool", "arguments", "result", "error")}
                 for record in validation["result"]["calls"]]
        task = {**deepcopy(case.task), "task_id": case.task["task_id"] + "_single_agent_hardening",
                "task_text": proposal["task_text"], "hardening_profile": PROFILE,
                "reference": {"answer": proposal["reference_answer"], "tool_calls": calls,
                              "kind": "observed_tool_call_list"}}
        task.pop("task_text_sha256", None)
        write_json(output / "candidate.json", task)
        write_json(output / "reference_tool_calls.json", calls)
        receipt = {"validation_id": proposal["validation_id"], "call_count": len(calls),
                   "execution_passed": True, "source_check_passed": True,
                   "backend": validation["result"].get("backend"), "model_inference": False,
                   "evidence": "author/execution/raw/solution_validations.jsonl"}
        write_json(output / "reference_validation.json", receipt)
        report.update(reference_call_count=len(calls), reference_validation=receipt)
        if not solve_independently:
            report.update(status="reference_validated_pending_semantic_verification")
        else:
            report.update(status="independent_gpt_solving", independent_solver_started=True)
            _atomic_json(output / "result.json", report)
            solved, _ = run_harness(case, {**public_task(task), "environment_id": task["environment_id"]}, protocol,
                                     output / "independent-gpt/execution", api_key)
            (output / "independent-gpt/answer.txt").write_text(solved["outcome"]["final_answer"], encoding="utf-8")
            report.update(status="gpt_completed_pending_answer_review",
                          independent_answer="independent-gpt/answer.txt")
    except Exception as error:
        report.update(failed_stage=report["status"], status="stage_error", error=f"{type(error).__name__}: {error}")
    if read_json(output / "source_fingerprint.json") != source_fingerprint(case):
        report.update(status="frozen_sources_changed")
    _atomic_json(output / "result.json", report)
    return report


def main() -> None:
    from .reference_evidence import evaluation_environment, select_case

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Run only after the draft flow has been approved")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    options = parser.parse_args()
    case = select_case(options.input_root, options.case)
    if options.execute:
        os.environ.update(evaluation_environment())
    report = run_case(case, options.protocol, options.output_root.resolve(), execute=options.execute,
                      api_key=os.environ.get(options.api_key_env) if options.execute else None)
    print({key: report[key] for key in ("profile", "status", "author_started", "release_ready")})


if __name__ == "__main__":
    main()
