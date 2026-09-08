"""Step 5：为 Ground Truth 生成并自测结构化答案 Verifier。

输入：``step4_ground_truth.jsonl``。
输出：``step5_verifier_code.jsonl``。
"""

from __future__ import annotations

import argparse
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from ..utils.contracts import TaskFields
from ..utils.io import read_json, read_records, write_json, write_jsonl
from ..utils.verifier import build_deterministic_verifier, verifier_smoke_test


class VerifierGenerationAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step5Result:
    output_path: Path
    total: int
    generated: int
    fallback_count: int


def build_verifier_prompt() -> str:
    return """你正在执行 Program-form TaskGen Step 5。

读取 `verifier_request.json`，生成：

```python
def verify(candidate_answer, ground_truth_answer):
    ...
    return score
```

约束：

1. 只比较结构化答案，不读取环境、不调用工具。
2. score 必须是 0..1；只有全部 required 字段正确且没有额外字段时才返回 1.0。
3. 字段按业务重要性分配 1/2/3 分，每个字段只能得满分或 0 分。
4. 严格检查字段缺失、额外字段、值错误和类型错误。
5. 不导入模块，不使用动态执行。

最终只写 `verifier.json`：

```json
{"verifier_code": "完整 Python 代码"}
```
"""


def process_single_task(
    task: dict[str, Any],
    *,
    agent: VerifierGenerationAgent | None,
    debug_root: Path,
) -> tuple[dict[str, Any], bool]:
    item = deepcopy(task)
    ground_truth = task.get(TaskFields.GROUND_TRUTH)
    if not isinstance(ground_truth, dict):
        item[TaskFields.VERIFIER_CODE] = ""
        item["verifier_generation_error"] = "No ground truth available"
        return item, False
    answer = ground_truth.get("candidate_answer")
    schema = task.get(TaskFields.OUTPUT_SCHEMA)
    if not isinstance(answer, dict) or not isinstance(schema, dict):
        item[TaskFields.VERIFIER_CODE] = ""
        item["verifier_generation_error"] = "Missing candidate_answer or output_schema"
        return item, False
    schema_errors = list(Draft202012Validator(schema).iter_errors(answer))
    if schema_errors:
        item[TaskFields.VERIFIER_CODE] = ""
        item["verifier_generation_error"] = schema_errors[0].message
        return item, False

    fallback = False
    code = ""
    generation_errors: list[str] = []
    if agent is not None:
        task_dir = debug_root / str(task[TaskFields.TASK_ID])
        if task_dir.exists():
            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True)
        write_json(task_dir / "verifier_request.json", {
            "task_internal": task.get(TaskFields.TASK_INTERNAL_FINETUNED)
            or task.get(TaskFields.TASK_INTERNAL),
            "output_schema": schema,
            "ground_truth_answer": answer,
        })
        prompt = build_verifier_prompt()
        (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        for attempt in range(1, 4):
            try:
                agent.run(prompt, working_directory=task_dir)
                payload = read_json(task_dir / "verifier.json")
                candidate = str(payload.get("verifier_code") or "")
                passed, errors = verifier_smoke_test(candidate, answer)
                if passed:
                    code = candidate
                    break
                generation_errors.append(f"attempt {attempt}: {' | '.join(errors)}")
            except Exception as error:
                generation_errors.append(
                    f"attempt {attempt}: {type(error).__name__}: {error}"
                )
    if not code:
        code = build_deterministic_verifier(answer)
        passed, errors = verifier_smoke_test(code, answer)
        if not passed:
            raise RuntimeError("本地 Verifier fallback 自测失败：" + " | ".join(errors))
        fallback = True
    item[TaskFields.VERIFIER_CODE] = code
    item["verifier_generation"] = {
        "fallback": fallback,
        "model_errors": generation_errors,
        "smoke_test_passed": True,
    }
    return item, fallback


def run_step5(
    *,
    step4_path: Path,
    output_dir: Path,
    agent: VerifierGenerationAgent | None = None,
) -> Step5Result:
    tasks = read_records(step4_path)
    debug_root = output_dir.resolve() / "step5_debug"
    processed = [
        process_single_task(task, agent=agent, debug_root=debug_root)
        for task in tasks
    ]
    records = [item for item, _fallback in processed]
    output_path = output_dir.resolve() / "step5_verifier_code.jsonl"
    write_jsonl(output_path, records)
    return Step5Result(
        output_path,
        len(records),
        sum(bool(item.get(TaskFields.VERIFIER_CODE)) for item in records),
        sum(fallback for _item, fallback in processed),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step4-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--no-model", action="store_true")
    arguments = parser.parse_args()
    agent = None
    if not arguments.no_model:
        from utils.search_agent.codex import CodexAgentClient
        agent = CodexAgentClient(model=arguments.model, sandbox="workspace-write")
    print(run_step5(
        step4_path=arguments.step4_path,
        output_dir=arguments.output_dir,
        agent=agent,
    ).output_path)


if __name__ == "__main__":
    main()
