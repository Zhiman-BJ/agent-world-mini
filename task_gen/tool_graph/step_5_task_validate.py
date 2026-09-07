"""Step 5: assemble and independently review every candidate.

Execution or composition failures retain their root cause without derivative
missing-field errors. Otherwise, verify required fields and chain/call order,
then review task completion, answer coverage, and usability from public contracts,
review guidance, bounded initial observations, and actual calls. Final task text
is the sole requirements baseline. Unknown state is not evidence of absence.

Semantic review and exported tasks see all available public tools. No workspace
replay or byte-level state auditing is performed.
Candidates preserve their original order. Passing tasks satisfy the task schema;
run_io exports them and retains complete rejected candidates separately.
"""
from __future__ import annotations

from copy import deepcopy
import json
import warnings
from pathlib import Path
from typing import Any

from .contracts import ValidateTasksInput, ValidateTasksOutput
from .llm import BatchInferenceError, infer, parse_json_object
from .initial_state_probe import report_context
from .prompt_principles import REVIEW_GUIDANCE, TASK_STATE_CHAIN

from jsonschema import validators


def validate_tasks(stage_input: ValidateTasksInput) -> ValidateTasksOutput:
    """组装正式任务，并用一次 LLM 审查任务文本与真实调用链的语义一致性。"""
    environment = stage_input["environment"]
    public_tools = [_public_tool(tool) for tool in environment.get("tools", []) if isinstance(tool, dict)]
    schema = _load_schema(stage_input["config"].schema_dir / "validation" / "task.schema.json")
    output: list[dict[str, Any]] = []
    review_items: list[tuple[int, str]] = []
    for source in stage_input["tasks"]:
        candidate = deepcopy(source)
        task = _assemble_task(candidate, environment, public_tools)
        errors = _basic_errors(candidate, task)
        candidate["task"] = task
        candidate["validation"] = {
            "passed": not errors,
            "execution_matches_task": False,
            "answer_matches_task": False,
            "task_is_usable": False,
            "errors": errors,
        }
        output.append(candidate)
        if not errors:
            review_items.append((len(output) - 1, _build_review_prompt(
                environment, public_tools, candidate, stage_input.get("initial_state_report"),
            )))

    if review_items:
        try:
            results = infer([prompt for _index, prompt in review_items], llm_config=stage_input["config"].llm)
            if len(results) != len(review_items):
                raise ValueError("LLM 返回数量不一致")
        except BatchInferenceError as error:
            results = list(error.outcomes)
        except Exception as error:
            for index, _prompt in review_items:
                output[index]["validation"]["errors"].append(f"LLM 语义审查失败：{error}")
                output[index]["validation"]["passed"] = False
            results = []
        for (index, _prompt), result in zip(review_items, results):
            if isinstance(result, Exception):
                output[index]["validation"]["errors"].append(f"LLM 语义审查失败：{result}")
                output[index]["validation"]["passed"] = False
                continue
            try:
                review = _parse_review(parse_json_object(result.text))
            except Exception as error:
                output[index]["validation"]["errors"].append(f"LLM 审查结果无效：{error}")
                output[index]["validation"]["passed"] = False
                continue
            validation = output[index]["validation"]
            validation.update({
                "execution_matches_task": review["execution_matches_task"],
                "answer_matches_task": review["answer_matches_task"],
                "task_is_usable": review["task_is_usable"],
            })
            validation["errors"].extend(review["errors"])
            validation["passed"] = (
                review["execution_matches_task"]
                and review["answer_matches_task"]
                and review["task_is_usable"]
                and not validation["errors"]
            )

    for candidate in output:
        validation = candidate["validation"]
        if validation["passed"]:
            schema_errors = [f"task schema：{error.message}" for error in schema.iter_errors(candidate["task"])]
            validation["errors"].extend(schema_errors)
            validation["passed"] = not validation["errors"]
    return {"tasks": output}


def _assemble_task(candidate: dict[str, Any], environment: dict[str, Any], public_tools: list[dict[str, Any]]) -> dict[str, Any]:
    execution = candidate.get("execution") if isinstance(candidate.get("execution"), dict) else {}
    calls = execution.get("tool_calls") if isinstance(execution.get("tool_calls"), list) else []
    return {
        "schema_version": "1.0",
        "task_id": candidate.get("task_id") if isinstance(candidate.get("task_id"), str) else "",
        "environment_id": environment.get("environment_id") if isinstance(environment.get("environment_id"), str) else "",
        "task_text": candidate.get("task_text") if isinstance(candidate.get("task_text"), str) else None,
        "difficulty": {"tool_calls": len(calls)},
        "initial_state": execution.get("initial_state"),
        "available_tools": public_tools,
        "reference": {
            "tool_calls": [
                {"tool": call.get("tool"), "arguments": call.get("arguments")}
                for call in calls if isinstance(call, dict)
            ],
            "answer": candidate.get("reference_answer") if isinstance(candidate.get("reference_answer"), str) else None,
            "final_state": execution.get("final_state"),
        },
    }


def _basic_errors(candidate: dict[str, Any], task: dict[str, Any]) -> list[str]:
    execution = candidate.get("execution")
    if not isinstance(execution, dict) or execution.get("success") is not True:
        detail = execution.get("error") if isinstance(execution, dict) else "缺少 execution"
        return [f"execution 未成功：{detail or '未提供原因'}"]
    if candidate.get("compose_error") is not None:
        return [f"compose_error：{candidate['compose_error']}"]
    errors: list[str] = []
    if not {"task_text", "reference_answer", "compose_error"} <= set(candidate):
        errors.append("缺少 Step 4 中间字段")
    if not isinstance(task["task_text"], str) or not task["task_text"].strip():
        errors.append("task_text 必须是非空文本")
    if not isinstance(task["reference"]["answer"], str) or not task["reference"]["answer"].strip():
        errors.append("reference_answer 必须是非空文本")
    calls = task["reference"]["tool_calls"]
    if not calls:
        errors.append("execution.tool_calls 必须非空")
    if len(calls) < 6:
        errors.append("最终调用链至少 6 次工具调用")
    if [call.get("tool") for call in calls] != candidate.get("chain"):
        errors.append("execution.tool_calls 顺序与 chain 不一致")
    return errors


def _build_review_prompt(environment: dict[str, Any], public_tools: list[dict[str, Any]], candidate: dict[str, Any], initial_report: dict[str, Any] | None = None) -> str:
    execution = candidate["execution"]
    context = {
        "environment": {key: environment.get(key) for key in ("name", "description", "resources", "rules")},
        "tools": public_tools,
        "initial_state_report": report_context(initial_report),
        "review_guidance": (candidate.get("llm_review") or {}).get("reason"),
        "task_text": candidate.get("task_text"),
        "reference_answer": candidate.get("reference_answer"),
        "chain": candidate.get("chain"),
        "tool_calls": execution.get("tool_calls"),
    }
    instruction = """对最终任务产物作独立质量判断，不修改任务、答案或执行记录。
task_text 是唯一需求基准；review 和初态报告用于理解证据与路径，不能增加任务未要求的义务。
execution_matches_task：在给定初态下，真实执行及已有状态是否满足任务的全部适用要求。
answer_matches_task：参考答案是否准确、完整地回答这些要求，结论范围与业务含义是否有证据支持，是否遗漏重要结果。
task_is_usable：任务是否自然、逻辑清楚、结果导向且信息充分，能否仅凭用户可辨认的业务信息和公开环境独立理解。
任务可以包含多项独立子任务和未触发分支，不要求逐一对应工具调用；成功响应本身不证明业务目标完成。
tools 是环境全部公开能力，chain 只是本次执行路径；不能因本次没有使用某个工具而认定环境不具备该能力。
初态摘要不保证完整或准确；结合 review 引用的观察依据和实际查询判断，区分查询范围、已有事实和推断。
分别给出三个判断。失败时，每条 errors 说明任务的具体要求、当前适用依据、实际证据与完成缺口，
或明确指出无法判断的证据缺口；不能只以缺少某种调用为拒绝理由。
严格只返回 JSON：{"execution_matches_task":true,"answer_matches_task":true,"task_is_usable":true,"errors":[]}。"""
    return "\n".join((instruction, TASK_STATE_CHAIN, REVIEW_GUIDANCE,
                      "\n【待分析数据】", json.dumps(context, ensure_ascii=False)))


def _parse_review(payload: dict[str, Any]) -> dict[str, Any]:
    keys = ("execution_matches_task", "answer_matches_task", "task_is_usable")
    if set(payload) != {*keys, "errors"}:
        raise ValueError("语义审查字段集合无效")
    for key in keys:
        if type(payload.get(key)) is not bool:
            raise ValueError(f"{key} 必须是 bool")
    errors = payload.get("errors")
    if not isinstance(errors, list) or any(not isinstance(item, str) or not item.strip() for item in errors):
        raise ValueError("errors 必须是字符串数组")
    return {
        key: payload[key]
        for key in keys
    } | {
        "errors": [item.strip() for item in errors],
    }


def _load_schema(path: Path):
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            validator_class = validators.validator_for(schema)
            validator_class.check_schema(schema)
        return validator_class(schema)
    except Exception as error:
        raise ValueError(f"无法加载 task.schema.json：{path}: {error}") from error


def _public_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {key: tool.get(key) for key in ("name", "description", "inputSchema", "outputSchema")}
