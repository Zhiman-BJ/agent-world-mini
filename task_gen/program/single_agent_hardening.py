"""Draft: one author/solver session, code checks, then an independent GPT attempt."""

from __future__ import annotations

import argparse
from copy import deepcopy
import os
from pathlib import Path

from harness.resources import ResourceCatalog

from .author_submission import resolve_submission
from .utils.io import read_json, read_jsonl, write_json
from .proposal_sources import (
    EXPLORATION_TOOLS, PUBLIC_PROTOCOL_FIELDS, SELF_CHECKS, UNIT_FACTORS, _value_pointers,
    argument_leaves, argument_schema, check_solution, parameter_source_values,
    pointer, same_json, same_source_value, source_value,
)


PROFILE = "single_agent_tool_list_draft_decision_design"
PROVIDER_HEADERS = {"User-Agent": "codex_cli_rs/0.115.0", "originator": "codex_cli_rs",
                    "OpenAI-Beta": "responses=experimental"}
AUTHOR_PROMPT = """# 任务与目标
本轮目标是生成一条用于评测任务执行能力的真实业务任务，以及一条从干净初态可执行的参考工具调用列表。
任务重点考查执行者能否利用自身领域知识理解用户需求，判断需要什么信息，选择合适工具，解释真实返回，并据此编排后续工作。
原任务、原调用记录和历史评测只用于了解环境能力、发现泄题和识别失败模式，不是必须继承的题目、路线或答案。
可以重新选择业务目标、对象范围和交付形式，但新任务必须立足当前公开数据和工具并真实可解。

# 环境与参考材料
先读 `../.task-authoring/` 下的 `public_tools.json`、`workspace_files.json`、`protocol.json`、`solution_format.json`，
再参考 `original_task.json` 和 `original_calls.json`。若存在 `previous_evaluation.json`，阅读失败、泄题
和不通过案例。

先实际查看代表性工具返回和数据，再选择环境能够支持的业务成果；可做必要的领域调研。
从成果倒推求解者必须自行作出的决定，并试查最省事的合法路径：工具若已直接给出结论，
或逐项查询汇总即可完成，应调整目标，而不是增加审查栏目、附件或引用规格。调研和求解过程留在作者材料中。

# 设计原则
所有设计须围绕一个真实、明确的业务成果展开。

1. **成果与能力**：围绕有实际用途的完整成果设计需求，让执行者利用领域知识选择工具、组织参数、解释返回并编排后续工作，
交付有证据支撑的结论、决策、修改或产物；不能退化为独立查询后汇总。
2. **困难任务的构成**：任务不能由一次明显调用直接完成。至少两处关键业务判断必须结合领域知识和真实工具返回，实质改变后续的信息需求、
工具选择、处理对象、参数范围或行动方案；最终成果还须整合多个阶段的证据和状态。仅改变最后结论、机械传递标识、调换独立查询顺序或执行固定格式转换，
不单独构成编排难度。困难应来自领域理解、条件判断和结果驱动的后续工作，而不是来自题文很长、答案很长、重复查询、无关复核、格式负担或繁琐手算。
工具已经完成的筛选、评分和结论不计为执行者的判断，固定清单逐项调用后汇总也不算多阶段编排。
3. **真实可解与路径多样**：目标、范围、交付内容和验收标准必须明确，关键事实和专业判断必须有当前公开数据、工具能力或可靠调研支撑。
在目标和验收标准固定的情况下，应允许多条满足共同业务验收标准的完成路径：执行者能权衡不同的信息获取、取证策略或工具组合，而不只是把同一组检查换序。
参考解只展示一条路线；业务允许不同可行方案时，不要求候选、参数或中间值相同。若目标无法自然形成上述依赖，应重新选择业务目标、范围或交付形式。
专业计算使用现有领域工具，模型负责理解、筛选、条件判断、简单比较、四则运算和结果传递。
4. **方法不泄露**：题文只说明用户要达成什么、处理哪些对象、已有何种背景和输入、什么条件算完成，把如何调查环境、选择工具和组织调用留给执行者。
方法泄露是指题文替执行者决定调查起点、分析方法、工具顺序、中间对象或分支动作；没有工具名的分阶段筛选、排名或试验清单同样会泄露路线。
不能为参考路线编造这些规则再包装成业务要求；已指定的中间决策不计为自主编排难点。
题文不得出现具体工具名、MCP 接口名、工具调用示例或工具返回字段；不得预告中间答案（含在输出命名中绑定首选），或把参数试验序列及“先 A、再 B、最后 C”路线写成业务要求。
可以写入对象名称和 ID、文件名、坐标、单位、阈值、标准、已知背景、已有输入和验收条件。真实业务确需指定的标准或方法可以保留，
但已被指定的方法不能再计为自主选择的难点；最终产物规格也不等于中间操作路线。

在 `self_check_notes` 用实际返回说明两处后续工作如何改变、哪些判断由执行者完成，以及合理路径、可解依据与限制。

# 参考解与提交
先设计有真实决策依赖的完整成果，再检查参考解是否自然满足有效业务调用次数不少于 15 次。参考解用 `solution_calls` 表示按干净初态可执行的顺序业务 MCP 调用。
15 次是验收下限，不是设计起点；不能靠追加无关检查、扩大为全套资料盘点、重复查询或拆分结果凑数，调用数达标不抵消其他原则的失败。
参考解只含公开业务 MCP 调用；Read/Grep/Glob
只用于读取必读材料或必要的公开参数，查得参数直接填入接口，文件查看不计入新旧业务调用数。参数和答案证据必须可追溯，
最终答案必须覆盖题文要求。

# 完成与提交
完成顺序：
1. 读材料、设计任务并实际求解，在 `self_check_notes` 记录方法泄露、可解性和必要结果依赖。
2. 按 `solution_format.json` 构造完整方案，调用 `validate_solution_calls`，以 `solution_calls` 为唯一执行列表。
3. 逐项对照题文核对答案，并回读 `state.public_workspace` 中实际产物内容；缺项或空产物就在同一会话修改并重新验证。
4. 确认 `execution_passed`、`source_check_passed`、字段和交付均闭合后，调用 `submit_validated_solution`。
`accepted=true` 后最终只返回 `{"validation_id":"成功ID"}`；后续独立盲解及正式 verifier 检查任务完成情况，不自动证明难度或无方法泄露。
"""


def run_harness(case, task: dict, protocol: dict, root: Path, api_key: str,
                *, author_materials: dict | None = None) -> tuple[dict, list[dict]]:
    from distill.runner import make_server_config, run_k3_distillation

    from .reference_evidence import check_upstream_requests, ordered_interactions

    config = make_server_config(case.environment, max_tool_calls=protocol["max_total_tool_calls"], runtime=case.runtime)
    if author_materials is not None:
        config["author_materials"] = author_materials
        config["solution_validation"] = {
            "submission_gate": True,
            "read_default_chars": protocol["read_default_chars"], "read_max_chars": protocol["read_max_chars"],
            "source_check": {"protocol": protocol,
                "minimum_reference_calls": 15,
                "original_task_text": author_materials["original_task.json"]["task_text"],
                "original_call_count": author_materials["original_task.json"]["original_call_count"]}}
        if "task_history.json" in author_materials:
            config["solution_validation"]["task_history"] = author_materials["task_history.json"]
            if protocol.get("task_novelty_review"):
                config["solution_validation"]["task_novelty_review"] = protocol["task_novelty_review"]
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
        trajectory, read_jsonl(root / "raw/environment_tool_calls.jsonl"), task, protocol,
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


def validation_resources(case, execution_root: Path) -> ResourceCatalog | None:
    from distill.taskgen_verifier import _verifier_environment

    path_map_path = execution_root / "private-state/path-map.json"
    if not path_map_path.is_file():
        return None
    path_map = read_json(path_map_path)
    workspace = Path(path_map["workspace_root"])
    mounts = path_map["scope_mounts"]
    return ResourceCatalog(_verifier_environment(case),
        lambda scope_id: workspace / mounts[scope_id] if scope_id in mounts
        else workspace / "filesystem_scopes" / scope_id,
        workspace_root=workspace, scope_mounts=mounts)


def validated_reference(proposal: dict, reports: list[dict], *,
                        resources: ResourceCatalog | None = None) -> tuple[dict, dict]:
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
    if len(records) != len(proposal["solution_calls"]) or any(
            record.get("error") or record.get("index") != position
            or record.get("tool") != requested["tool"]
            for position, (record, requested) in enumerate(zip(records, validation["request"]["calls"]), 1)):
        raise ValueError("successful validation is missing its complete actual execution evidence")
    tools = {tool["name"]: tool for tool in resources.environment.get("tools", [])} if resources else {}
    scopes = {str(scope["scope_id"]) for scope in resources.environment.get("filesystem_scopes", [])} if resources else set()
    for position, (record, requested) in enumerate(zip(records, validation["request"]["calls"]), 1):
        arguments = requested["arguments"]
        if resources is not None:
            tool = tools[requested["tool"]]
            declared = {str(scope) for scope in tool.get("usageConditions", {}).get("targetResources", [])}
            arguments = resources.normalize_arguments(arguments, schema=tool["inputSchema"],
                                                      allowed_scopes=scopes & declared)
        if not same_json(record.get("arguments"), arguments):
            raise ValueError(f"validation execution arguments differ at call {position} ({requested['tool']})")
    checked["state"] = validation["result"].get("state")
    return validation, checked


def prepare_case(case, protocol: dict, output: Path, *, prior_evaluation: dict | None = None,
                 task_history: dict | None = None) -> dict:
    from distill.taskgen_verifier import _verifier_environment
    from harness.mcp_protocol import public_tools

    from .reference_evidence import _atomic_json, check_protocol, public_task, sha256, source_fingerprint

    check_protocol(protocol)
    source_case = f"{case.source_run.name}__{case.task['task_id']}"
    if prior_evaluation is not None and (
        not isinstance(prior_evaluation, dict) or prior_evaluation.get("source_case") != source_case
    ):
        raise ValueError("prior evaluation must identify the same original source case")
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
        "solution_format.json": read_json(Path(__file__).with_name("AUTHOR_SOLUTION_FORMAT.json")),
        "workspace_files.json": {"initial_state_files": workspace_files,
            "filesystem_scopes": case.environment.get("filesystem_scopes", []),
            "path_note": "文件在当前公开工作区按scope挂载；单scope挂载根目录，多scope分别挂载子目录。"}}
    prompt = AUTHOR_PROMPT
    if task_history is not None:
        from .task_history import HISTORY_PROMPT, validate_history

        validate_history(task_history, case.task["environment_id"])
        materials["task_history.json"] = deepcopy(task_history)
        prompt += HISTORY_PROMPT
        if "fixed_task_text" in task_history:
            materials["fixed_task.json"] = {"task_text": task_history["fixed_task_text"]}
            prompt = (
                "# 恢复已发布任务的参考解\n"
                "本轮任务题文已冻结在 fixed_task.json，不是重新出题。读取公开工具、工作区、solution_format.json"
                "和 task_history.json，根据真实工具返回，恢复该题的完整参考工具调用列表和交付答案。"
                "proposal.task_text 必须逐字等于 fixed_task.json 的 task_text；保持所有业务条件和验收要求。"
                "参考列表记录至少15次公开业务MCP调用，参数和答案证据使用实际观察来源。"
                "通过 validate_solution_calls 校验完整proposal，收到错误后在同一会话修正参考解并重验。"
                "调用 submit_validated_solution，accepted=true 后最终只返回"
                '{"validation_id":"成功ID"}。其他历史题用于去重核查，不是本题的替代需求。\n'
            )
    if prior_evaluation is not None:
        materials["previous_evaluation.json"] = deepcopy(prior_evaluation)
        if (prior_evaluation.get("revision_opportunities") == 1
                and isinstance(prior_evaluation.get("previous_proposal"), dict)):
            prompt += (
                "\n# 本轮是原方案修订\n"
                "先读 previous_evaluation.json，以 previous_proposal 为待修订方案，保留业务目标、对象范围和实质难度。"
                "仅修复实际错误、歧义、能力不匹配或方法提示；已通过且无上述问题时保留方案，不另换委托、不扩张交付。"
                "逐项核对修订后题文、参考答案和实际产物，不能只修被指出的一项；不为GPT自身失误降低标准或补解题步骤。"
                "判定原因须用实际返回核实，参数、事实与结论仍以公开环境及本次实测为依据。\n"
            )
        else:
            prompt += (
                "\n# 上一轮实际反馈\n"
                "先读 previous_evaluation.json，结合上一轮正式判定与实际调用，理解旧设计哪些地方"
                "仍容易完成、哪些失败来自交互故障或答案错误，再自主设计本轮委托。"
                "判定中的原因分析应结合实际工具返回核实。"
                "这份材料只供作者改进设计；本轮参数、事实和结论仍以公开环境及本次实际返回为依据。\n"
            )
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "author_input.json", author_input)
    write_json(output / "author_materials.json", materials)
    write_json(output / "protocol.json", protocol)
    write_json(output / "source_fingerprint.json", source_fingerprint(case))
    (output / "author_prompt.txt").write_text(prompt, encoding="utf-8")
    report = {"profile": PROFILE, "status": "awaiting_user_confirmation", "model": protocol["model"],
        "source_case": source_case, "prior_evaluation_supplied": prior_evaluation is not None,
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
             execute: bool = False, solve_independently: bool = True,
             prior_evaluation: dict | None = None, task_history: dict | None = None) -> dict:
    from .reference_evidence import _atomic_json, check_protocol, public_task, sha256, source_fingerprint

    protocol = read_json(protocol_path)
    check_protocol(protocol)
    report = read_json(output / "result.json") if output.exists() else prepare_case(
        case, protocol, output, prior_evaluation=prior_evaluation, task_history=task_history)
    if task_history != read_json(output / "author_materials.json").get("task_history.json"):
        raise ValueError("task history differs from the frozen prepared author materials")
    if prior_evaluation is not None and read_json(output / "author_materials.json").get("previous_evaluation.json") != prior_evaluation:
        raise ValueError("prior evaluation differs from the frozen prepared author materials")
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
        reports = read_jsonl(output / "author/execution/raw/solution_validations.jsonl")
        proposal, submission = resolve_submission(trajectory["outcome"]["final_answer"], reports)
        write_json(output / "proposal.json", proposal)
        report["author_submission"] = submission
        validation, clean = validated_reference(proposal, reports,
            resources=validation_resources(case, output / "author/execution"))
        checked = deepcopy(validation["result"]["source_check"])
        if checked.get("parameter_sources_match") is not True:
            raise ValueError("final source validation did not confirm public parameter sources")
        checked["clean_state_validation"] = clean
        if task_history is not None:
            from .task_history import history_digest

            novelty = validation["result"].get("task_history_check", {})
            if novelty.get("passed") is not True or novelty.get("history_sha256") != history_digest(task_history):
                raise ValueError("final author validation did not check the frozen task history")
            semantic_review = novelty.get("semantic_review")
            if (protocol.get("task_novelty_review") and novelty.get("compared_tasks", 0)
                    and (not isinstance(semantic_review, dict) or not semantic_review.get("novel"))):
                raise ValueError("final author validation is missing its successful semantic novelty review")
            checked["task_history_check"] = novelty
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
    parser.add_argument("--prior-evaluation", type=Path, help="Private previous-round diagnostic feedback for this source")
    parser.add_argument("--task-history", type=Path, help="Frozen previous tasks from the same environment")
    options = parser.parse_args()
    case = select_case(options.input_root, options.case)
    if options.execute:
        os.environ.update(evaluation_environment())
    report = run_case(case, options.protocol, options.output_root.resolve(), execute=options.execute,
                      api_key=os.environ.get(options.api_key_env) if options.execute else None,
                      prior_evaluation=read_json(options.prior_evaluation) if options.prior_evaluation else None,
                      task_history=read_json(options.task_history) if options.task_history else None)
    print({key: report[key] for key in ("profile", "status", "author_started", "release_ready")})


if __name__ == "__main__":
    main()
