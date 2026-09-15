"""Step 4：为已验证任务生成评分准则、答案 Verifier 和状态 Verifier。"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..utils.schema import Draft202012Validator

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.io import read_json, read_records, write_json, write_jsonl
from ..utils.verifier import state_verifier_smoke_test, verifier_smoke_test


class ScoringCriteriaAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step4Result:
    output_path: Path
    total: int
    ready: int


def build_scoring_prompt(policy: ProgramGenerationPolicy, attempt: int) -> str:
    specific = policy.total_rubric_score - policy.general_rubric_score
    return f"""# 任务：生成可执行的任务得分准则

这是第 {attempt} 次提交。读取 `scoring_request.json`、`scoring_criteria.schema.json`
和 `validation_feedback.json`。你要同时生成三部分：

1. `rubric_items`：逐项说明任务完成条件；
2. `answer_verifier_code`：验证最终结构化答案；
3. `state_verifier_code/state_verification`：验证环境中的业务效果。

Rubric 总分必须为 {policy.total_rubric_score}；general 合计
{policy.general_rubric_score}，task_specific 合计 {specific}。每项只能为 1、2、3 分，
ID 使用 G1/G2 或 T1/T2。每项是一个可独立二元判断的最小条件，明确证据来自
candidate_answer、final_state 或 tool_trace。不要评价语气、特定工具名、固定调用顺序
或任务没有要求的状态差异。不得把参考执行中的偶然值扩写成用户要求。

答案验证器必须定义：

```python
def verify(candidate_answer, ground_truth_answer):
    return score
```

状态验证器必须定义：

```python
def verify_state(candidate_state, ground_truth_state, initial_state):
    return score
```

两者返回 0..1。不得 import、读取文件、调用工具或动态执行代码。只有完整满足时才能
返回 1.0。当前状态验收固定使用 `mode=exact_final_state`：完整比较逻辑 Ground Truth
最终状态；required_effects/forbidden_effects 用自然语言解释应有和不应有的状态变化。
任务作者应在 Step 3 给出确定的选择规则，使正确执行收敛到同一逻辑最终状态。

最终只写 `scoring.json`，格式必须严格满足 scoring_criteria.schema.json。模型声明的
分数不会被直接相信，Python 会重新计算并运行正负例测试。
"""


def _schema_errors(payload: Any, schema: dict[str, Any]) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(payload)
    ]


def _rubric_errors(items: list[dict[str, Any]], policy: ProgramGenerationPolicy) -> list[str]:
    errors: list[str] = []
    identifiers: set[str] = set()
    general = specific = 0
    for index, item in enumerate(items):
        identifier = str(item.get("id") or "")
        section = item.get("section")
        if identifier in identifiers:
            errors.append(f"rubric_items[{index}].id 重复：{identifier}")
        identifiers.add(identifier)
        if section == "general":
            general += int(item.get("points", 0))
            if not identifier.startswith("G"):
                errors.append(f"general 项必须使用 G 前缀：{identifier}")
        elif section == "task_specific":
            specific += int(item.get("points", 0))
            if not identifier.startswith("T"):
                errors.append(f"task_specific 项必须使用 T 前缀：{identifier}")
    expected_specific = policy.total_rubric_score - policy.general_rubric_score
    if general != policy.general_rubric_score:
        errors.append(f"general 合计 {general}，应为 {policy.general_rubric_score}")
    if specific != expected_specific:
        errors.append(f"task_specific 合计 {specific}，应为 {expected_specific}")
    return errors


def validate_scoring_payload(
    payload: Any,
    *,
    schema: dict[str, Any],
    task: dict[str, Any],
    policy: ProgramGenerationPolicy,
) -> list[str]:
    errors = _schema_errors(payload, schema)
    if errors or not isinstance(payload, dict):
        return errors
    errors.extend(_rubric_errors(payload["rubric_items"], policy))
    ground_truth = task[TaskFields.GROUND_TRUTH]
    answer_ok, answer_errors = verifier_smoke_test(
        payload["answer_verifier_code"], ground_truth["candidate_answer"]
    )
    state_ok, state_errors = state_verifier_smoke_test(
        payload["state_verifier_code"],
        ground_truth["init_state"],
        ground_truth["final_state"],
    )
    if not answer_ok:
        errors.extend(answer_errors)
    if not state_ok:
        errors.extend(state_errors)
    return errors


def _fixture_for_task(payload: Any, task_id: str, task_count: int) -> Any:
    if task_count == 1 and isinstance(payload, dict) and "rubric_items" in payload:
        return payload
    if isinstance(payload, dict):
        return payload.get(task_id)
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("task_id") == task_id:
                return item.get("scoring", item)
    return None


def process_single_task(
    task: dict[str, Any],
    *,
    policy: ProgramGenerationPolicy,
    schema: dict[str, Any],
    agent: ScoringCriteriaAgent | None,
    debug_root: Path,
    fixture: Any = None,
) -> dict[str, Any]:
    item = deepcopy(task)
    audit_dir = debug_root / str(task[TaskFields.TASK_ID])
    if audit_dir.exists():
        shutil.rmtree(audit_dir)
    attempts: list[dict[str, Any]] = []
    accepted: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(prefix="agent-world-program-scoring-") as temporary:
        task_dir = Path(temporary)
        write_json(task_dir / "scoring_request.json", {
            "task_public": task[TaskFields.TASK_PUBLIC],
            "output_schema": task[TaskFields.OUTPUT_SCHEMA],
            "task_archetype": task.get("task_archetype"),
            "ground_truth": task[TaskFields.GROUND_TRUTH],
            "solution_trace": task[TaskFields.SOLUTION_TRACE],
        })
        write_json(task_dir / "scoring_criteria.schema.json", schema)
        max_attempts = 1 if fixture is not None else 3
        for attempt in range(1, max_attempts + 1):
            write_json(task_dir / "validation_feedback.json", {"attempts": attempts})
            prompt = build_scoring_prompt(policy, attempt)
            (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            try:
                if fixture is not None:
                    candidate = fixture
                    write_json(task_dir / "scoring.json", candidate)
                else:
                    if agent is None:
                        raise ValueError("没有提供 ScoringCriteriaAgent")
                    agent.run(prompt, working_directory=task_dir)
                    candidate = read_json(task_dir / "scoring.json")
                errors = validate_scoring_payload(
                    candidate, schema=schema, task=task, policy=policy
                )
            except Exception as error:
                errors = [f"{type(error).__name__}: {error}"]
                candidate = None
            attempts.append({"attempt": attempt, "errors": errors})
            if not errors:
                accepted = candidate
                break
        shutil.copytree(task_dir, audit_dir)
    item[TaskFields.SCORING_VALIDATION] = {"attempts": attempts, "success": accepted is not None}
    item[TaskFields.SCORING_READY] = accepted is not None
    if accepted is None:
        item["scoring_error"] = attempts[-1]["errors"] if attempts else ["unknown"]
        return item
    item[TaskFields.RUBRIC_ITEMS] = deepcopy(accepted["rubric_items"])
    item[TaskFields.RUBRIC_COUNT] = len(accepted["rubric_items"])
    item[TaskFields.RUBRIC_TOTAL_SCORE] = sum(
        rubric["points"] for rubric in accepted["rubric_items"]
    )
    item[TaskFields.GENERAL_RUBRIC_SCORE] = policy.general_rubric_score
    item[TaskFields.TASK_SPECIFIC_RUBRIC_SCORE] = (
        policy.total_rubric_score - policy.general_rubric_score
    )
    item[TaskFields.VERIFIER_CODE] = accepted["answer_verifier_code"]
    item[TaskFields.STATE_VERIFIER_CODE] = accepted["state_verifier_code"]
    item["state_verification"] = deepcopy(accepted["state_verification"])
    item[TaskFields.RUBRIC_EXPLANATION] = accepted["explanation"]
    return item


def run_step4(
    *,
    step3_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    agent: ScoringCriteriaAgent | None,
    scoring_fixture_path: Path | None = None,
) -> Step4Result:
    policy.validate()
    tasks = read_records(step3_path)
    schema = read_json(Path(__file__).resolve().parents[1] / "schemas" / "scoring_criteria.schema.json")
    fixture_payload = (
        read_json(scoring_fixture_path.resolve())
        if scoring_fixture_path is not None
        else None
    )
    records = [
        process_single_task(
            task,
            policy=policy,
            schema=schema,
            agent=agent,
            debug_root=output_dir.resolve() / "step4_scoring",
            fixture=(
                _fixture_for_task(fixture_payload, str(task[TaskFields.TASK_ID]), len(tasks))
                if fixture_payload is not None
                else None
            ),
        )
        for task in tasks
    ]
    output_path = output_dir.resolve() / "step4_scoring_criteria.jsonl"
    write_jsonl(output_path, records)
    ready = sum(item.get(TaskFields.SCORING_READY) is True for item in records)
    if tasks and ready == 0:
        raise RuntimeError("Step 4 没有任何任务生成有效得分准则")
    return Step4Result(output_path, len(records), ready)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step3-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scoring-fixture", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--total-score", type=int, default=14)
    parser.add_argument("--general-score", type=int, default=6)
    arguments = parser.parse_args()
    agent = None
    if arguments.scoring_fixture is None:
        from utils.search_agent.codex import CodexAgentClient

        agent = CodexAgentClient(
            model=arguments.model,
            sandbox="workspace-write",
            network_access=False,
            reasoning_effort="high",
        )
    result = run_step4(
        step3_path=arguments.step3_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(
            total_rubric_score=arguments.total_score,
            general_rubric_score=arguments.general_score,
        ),
        agent=agent,
        scoring_fixture_path=arguments.scoring_fixture,
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
