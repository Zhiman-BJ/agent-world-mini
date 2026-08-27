from __future__ import annotations

import json
import re
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .executor import ProgramExecutionResult, execute_reference_program
from .loader import CompleteEnvironmentPackage
from .models import (
    ProgramGenerationPolicy,
    ProgramGenerationResult,
    ProgramTaskCandidate,
)
from .prompts import build_program_generation_prompt


DEFAULT_PROGRAM_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "high"


class ProgramTaskAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} 不是合法 JSON：{error}") from error


def _has_state_change(result: ProgramExecutionResult) -> bool:
    return any(result.state_diff.get(field) for field in ("created", "modified", "deleted"))


def _normalized_task(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


class ProgramTaskGenerator:
    """Generate task text and Python references, then prove them by clean replay."""

    def __init__(
        self,
        agent: ProgramTaskAgent | None,
        *,
        policy: ProgramGenerationPolicy | None = None,
    ):
        self.agent = agent
        self.policy = policy or ProgramGenerationPolicy()
        self.policy.validate()
        root = Path(__file__).resolve().parent
        self.candidate_schema = _read_json(root / "candidate.schema.json")
        self.task_package_schema = _read_json(root / "task_package.schema.json")

    def generate(
        self,
        *,
        environment_package: Path,
        output_dir: Path,
        candidates_path: Path | None = None,
        overwrite: bool = False,
    ) -> ProgramGenerationResult:
        package = CompleteEnvironmentPackage.load(environment_package)
        output_dir = output_dir.resolve()
        if output_dir.exists() and not overwrite:
            raise FileExistsError(f"输出目录已经存在：{output_dir}")
        if candidates_path is None and self.agent is None:
            raise ValueError("没有 candidates_path 时必须提供 ProgramTaskAgent")

        with tempfile.TemporaryDirectory(prefix="agent-world-program-gen-") as temporary:
            staging = Path(temporary)
            shutil.copytree(package.workspace_root, staging / "workspace")
            public_environment = package.public_environment()
            _write_json(staging / "environment.public.json", public_environment)
            _write_json(staging / "candidate.schema.json", self.candidate_schema)
            request = {
                "environment_id": package.environment["environment_id"],
                **self.policy.to_dict(),
                "instructions": {
                    "environment": "environment.public.json",
                    "initial_workspace": "workspace/",
                    "output": "candidates.json",
                    "schema": "candidate.schema.json",
                },
            }
            _write_json(staging / "generation_request.json", request)

            accepted: list[dict[str, Any]] = []
            accepted_tasks: set[str] = set()
            submissions: list[dict[str, Any]] = []
            rejections: list[dict[str, Any]] = []
            repair_rounds = 0
            rounds = 1 if candidates_path is not None else self.policy.max_repair_rounds + 1

            for round_index in range(rounds):
                if len(accepted) >= self.policy.task_count:
                    break
                if candidates_path is not None:
                    payload = _read_json(candidates_path.resolve())
                else:
                    assert self.agent is not None
                    candidate_file = staging / "candidates.json"
                    candidate_file.unlink(missing_ok=True)
                    feedback = {
                        "round": round_index,
                        "remaining_tasks": self.policy.task_count - len(accepted),
                        "accepted_task_texts": [item["task"] for item in accepted],
                        "previous_rejections": rejections[-12:],
                    }
                    _write_json(staging / "validation_feedback.json", feedback)
                    prompt = self._prompt(round_index)
                    (staging / f"prompt_round_{round_index}.txt").write_text(
                        prompt, encoding="utf-8"
                    )
                    self.agent.run(prompt, working_directory=staging)
                    if not candidate_file.is_file():
                        rejections.append({
                            "round": round_index,
                            "candidate": None,
                            "reasons": ["生成 Agent 没有写出 candidates.json"],
                        })
                        repair_rounds = round_index
                        continue
                    payload = _read_json(candidate_file)
                submissions.append({"round": round_index, "payload": deepcopy(payload)})
                structural_errors = self._candidate_payload_errors(payload)
                if structural_errors:
                    rejections.append({
                        "round": round_index,
                        "candidate": None,
                        "reasons": structural_errors,
                    })
                    repair_rounds = round_index
                    continue

                for candidate_index, value in enumerate(payload["candidates"]):
                    candidate = ProgramTaskCandidate.from_dict(value)
                    task_key = _normalized_task(candidate.task)
                    if task_key in accepted_tasks:
                        rejections.append({
                            "round": round_index,
                            "candidate": candidate_index,
                            "task": candidate.task,
                            "reasons": ["任务正文与已接受任务重复"],
                        })
                        continue
                    task, reasons = self._validate_candidate(
                        package,
                        candidate,
                        task_index=len(accepted) + 1,
                    )
                    if reasons:
                        rejections.append({
                            "round": round_index,
                            "candidate": candidate_index,
                            "task": candidate.task,
                            "reasons": reasons,
                        })
                        continue
                    assert task is not None
                    accepted.append(task)
                    accepted_tasks.add(task_key)
                    if len(accepted) >= self.policy.task_count:
                        break
                repair_rounds = round_index

            task_package = {
                "schema_version": "1.0",
                "task_form": "program",
                "environment_id": package.environment["environment_id"],
                "public_environment": public_environment,
                "tasks": accepted,
            }
            package_errors = [
                error.message
                for error in Draft202012Validator(self.task_package_schema).iter_errors(
                    task_package
                )
            ]
            validation = {
                "status": "passed" if len(accepted) >= self.policy.task_count and not package_errors else "failed",
                "policy": self.policy.to_dict(),
                "accepted": len(accepted),
                "requested": self.policy.task_count,
                "submission_rounds": len(submissions),
                "rejections": rejections,
                "task_package_schema_errors": package_errors,
            }

            publish = staging / "publish"
            publish.mkdir()
            _write_json(publish / "tasks.json", task_package)
            _write_json(publish / "validation.json", validation)
            _write_json(publish / "candidates.json", {"submissions": submissions})
            _write_json(publish / "generation_request.json", request)
            for prompt_file in staging.glob("prompt_round_*.txt"):
                shutil.copy2(prompt_file, publish / prompt_file.name)

            if output_dir.exists():
                shutil.rmtree(output_dir)
            output_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(publish, output_dir)

        if validation["status"] != "passed":
            raise RuntimeError(
                f"Program-form 只生成了 {len(accepted)}/{self.policy.task_count} 个有效任务；"
                f"详见 {output_dir / 'validation.json'}"
            )
        return ProgramGenerationResult(
            output_dir=output_dir,
            tasks_path=output_dir / "tasks.json",
            validation_path=output_dir / "validation.json",
            candidates_path=output_dir / "candidates.json",
            task_count=len(accepted),
            repair_rounds=repair_rounds,
        )

    def _candidate_payload_errors(self, payload: Any) -> list[str]:
        return [
            f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in Draft202012Validator(self.candidate_schema).iter_errors(payload)
        ]

    def _validate_candidate(
        self,
        package: CompleteEnvironmentPackage,
        candidate: ProgramTaskCandidate,
        *,
        task_index: int,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        reasons = self._semantic_contract_errors(package, candidate)
        if reasons:
            return None, reasons

        replays = [
            execute_reference_program(
                package,
                candidate.solution_code,
                candidate.output_schema,
                timeout_seconds=self.policy.execution_timeout_seconds,
            )
            for _ in range(self.policy.clean_replays)
        ]
        for replay_index, replay in enumerate(replays):
            if not replay.success:
                reasons.append(
                    f"干净重放 {replay_index + 1} 失败：{replay.error_type}: {replay.error}"
                )
        if reasons:
            return None, reasons
        reference = replays[0]
        call_count = len(reference.trace)
        distinct_tools = {item["tool"] for item in reference.trace}
        planned_tools = {
            str(item["tool"]) for item in candidate.design.get("tool_plan", [])
        }
        if planned_tools != distinct_tools:
            reasons.append(
                "design.tool_plan 与真实调用工具不一致："
                f"计划={sorted(planned_tools)}，实际={sorted(distinct_tools)}"
            )
        if call_count < self.policy.min_tool_calls:
            reasons.append(
                f"实际工具调用 {call_count} 次，少于要求的 {self.policy.min_tool_calls} 次"
            )
        if len(distinct_tools) < self.policy.min_distinct_tools:
            reasons.append(
                f"实际只使用 {len(distinct_tools)} 个不同工具，少于要求的 "
                f"{self.policy.min_distinct_tools} 个"
            )
        state_changed = _has_state_change(reference)
        if self.policy.require_state_change and not state_changed:
            reasons.append("任务要求修改环境，但最终工作区没有真实变化")

        reference_signature = {
            "answer": reference.answer,
            "trace": reference.trace,
            "final_state": reference.final_state,
        }
        for replay_index, replay in enumerate(replays[1:], start=2):
            signature = {
                "answer": replay.answer,
                "trace": replay.trace,
                "final_state": replay.final_state,
            }
            if signature != reference_signature:
                reasons.append(f"干净重放 {replay_index} 与第一次结果不一致")
        if reasons:
            return None, reasons

        environment_id = str(package.environment["environment_id"])
        return {
            "task_id": f"{environment_id}_program_{task_index:03d}",
            "task": candidate.task,
            "output_schema": candidate.output_schema,
            "reference": {
                "solution_code": candidate.solution_code,
                "answer": reference.answer,
                "tool_calls": reference.trace,
                "initial_state": reference.initial_state,
                "final_state": reference.final_state,
                "state_diff": reference.state_diff,
            },
            "validation": {
                "clean_replays": self.policy.clean_replays,
                "tool_call_count": call_count,
                "distinct_tool_count": len(distinct_tools),
                "state_changed": state_changed,
                "deterministic": True,
            },
        }, []

    @staticmethod
    def _semantic_contract_errors(
        package: CompleteEnvironmentPackage,
        candidate: ProgramTaskCandidate,
    ) -> list[str]:
        errors: list[str] = []
        task_lower = candidate.task.lower()
        leaked = [name for name in package.tool_names if name.lower() in task_lower]
        if leaked:
            errors.append(f"任务正文泄露工具名：{', '.join(leaked)}")
        for internal_word in ("call_tool", "solution_code", "output_schema", "final_answer"):
            if internal_word in task_lower:
                errors.append(f"任务正文泄露内部执行概念：{internal_word}")

        schema = candidate.output_schema
        if not isinstance(schema, dict) or schema.get("type") != "object":
            errors.append("output_schema 根节点必须声明 type=object")
            return errors
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not properties:
            errors.append("output_schema.properties 必须是非空 object")
        if not isinstance(required, list) or set(required) != set(properties or {}):
            errors.append("output_schema.required 必须且只能包含全部输出字段")
        if schema.get("additionalProperties") is not False:
            errors.append("output_schema.additionalProperties 必须是 false")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as error:
            errors.append(f"output_schema 本身不合法：{error}")
        known_tools = set(package.tool_names)
        planned_tools = [
            str(item.get("tool") or "")
            for item in candidate.design.get("tool_plan", [])
            if isinstance(item, dict)
        ]
        unknown_tools = sorted(set(planned_tools) - known_tools)
        if unknown_tools:
            errors.append(f"design.tool_plan 引用了未知工具：{', '.join(unknown_tools)}")
        if len(planned_tools) != len(set(planned_tools)):
            errors.append("design.tool_plan 不能重复列出同一个工具")
        return errors

    def _prompt(self, round_index: int) -> str:
        return build_program_generation_prompt(
            round_index=round_index,
            policy=self.policy,
        )
