"""Run the OmniaBench-aligned Program-form TaskGen pipeline."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.search_agent.codex import CodexAgentClient

from .steps.step1_prepare_environment import run_step1
from .steps.step2_gen_task_solution import run_step2
from .steps.step3_debug_solution_jsonl import run_step3
from .steps.step4_ground_truth_jsonl_in_jsonl_out import run_step4
from .steps.step5_verifier_code_jsonl_in_jsonl_out import run_step5
from .steps.step6_debug_verifier_jsonl_in_jsonl_out import run_step6
from .steps.step7_consistency_jsonl_in_jsonl_out import run_step7
from .steps.step8_filter_rewrite_jsonl_in_jsonl_out import run_step8
from .steps.step9_filter_trace_state_jsonl_in_jsonl_out import run_step9
from .steps.step10_gen_task_rubric_jsonl_in_jsonl_out import run_step10
from .steps.step11_difficulty_eval_jsonl_in_jsonl_out import run_step11
from .steps.step12_final_output import run_step12
from .utils.contracts import ProgramGenerationPolicy


@dataclass(frozen=True)
class StepSpec:
    number: int
    name: str
    output: str


STEPS = {
    1: StepSpec(1, "prepare_environment", "step1_environment.json"),
    2: StepSpec(2, "generate_task_solution", "step2_task_solution.json"),
    3: StepSpec(3, "debug_solution", "step3_debug_solution.json"),
    4: StepSpec(4, "record_ground_truth", "step4_ground_truth.jsonl"),
    5: StepSpec(5, "generate_verifier", "step5_verifier_code.jsonl"),
    6: StepSpec(6, "debug_verifier", "step6_debug_verifier.jsonl"),
    7: StepSpec(7, "consistency_check", "step7_consistency.jsonl"),
    8: StepSpec(8, "filter_rewrite", "step8_filter_rewrite.jsonl"),
    9: StepSpec(9, "filter_trace_state", "step9_filter_trace_state.jsonl"),
    10: StepSpec(10, "generate_rubric", "step10_rubric.jsonl"),
    11: StepSpec(11, "evaluate_difficulty", "step11_difficulty.jsonl"),
    12: StepSpec(12, "final_output", "final/task_gen_final_english.json"),
}


STEP_ARTIFACTS = {
    1: ("step1_environment.json", "baseline_environment"),
    2: ("step2_task_solution.json",),
    3: ("step3_debug_solution.json", "step3_debug_solution.jsonl"),
    4: ("step4_ground_truth.jsonl",),
    5: ("step5_verifier_code.jsonl",),
    6: ("step6_debug_verifier.jsonl",),
    7: ("step7_consistency.jsonl",),
    8: ("step8_filter_rewrite.jsonl", "step8_filter_rewrite_kept.jsonl"),
    9: ("step9_filter_trace_state.jsonl", "step9_filter_trace_state_kept.jsonl"),
    10: ("step10_rubric.jsonl",),
    11: ("step11_difficulty.jsonl",),
    12: ("final/task_gen_final_english.json",),
}


def parse_step_range(value: str) -> list[int]:
    if value == "all":
        return list(STEPS)
    selected: set[int] = set()
    for raw in value.split(","):
        part = raw.strip()
        if "-" in part:
            start, end = (int(item) for item in part.split("-", 1))
            selected.update(range(start, end + 1))
        else:
            selected.add(int(part))
    unknown = selected - set(STEPS)
    if unknown:
        raise ValueError(f"未知步骤：{sorted(unknown)}")
    return sorted(selected)


def _agent(model: str, timeout: int) -> CodexAgentClient:
    return CodexAgentClient(
        model=model,
        timeout_seconds=timeout,
        sandbox="workspace-write",
        enable_web_search=False,
        network_access=False,
        reasoning_effort="high",
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )


def run_selected_steps(
    *,
    steps: list[int],
    environment_package: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    model: str,
    tools_path: Path | None = None,
    candidates_path: Path | None = None,
    overwrite: bool = False,
    agent_timeout_seconds: int = 1800,
) -> dict[int, Any]:
    """只连接各 Step 的固定输入输出，不实现步骤内部业务。"""
    policy.validate()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    writer_agent = _agent(model, agent_timeout_seconds)
    paths = {number: output_dir / spec.output for number, spec in STEPS.items()}
    results: dict[int, Any] = {}
    for number in steps:
        artifact_paths = tuple(output_dir / relative for relative in STEP_ARTIFACTS[number])
        existing = [path.exists() for path in artifact_paths]
        if all(existing) and not overwrite:
            results[number] = paths[number]
            continue
        if any(existing) and not overwrite:
            missing = [
                str(path.relative_to(output_dir))
                for path, present in zip(artifact_paths, existing)
                if not present
            ]
            raise RuntimeError(
                f"Step {number} 只有部分产物存在；缺少：{', '.join(missing)}。"
                "请补齐产物或使用 --fresh 重跑该步骤。"
            )
        if number == 1:
            results[number] = run_step1(
                environment_package=environment_package,
                tools_path=tools_path,
                output_dir=output_dir,
                overwrite=overwrite,
            )
        elif number == 2:
            results[number] = run_step2(
                step1_path=paths[1],
                output_dir=output_dir,
                policy=policy,
                agent=None if candidates_path else writer_agent,
                candidates_path=candidates_path,
            )
        elif number == 3:
            results[number] = run_step3(
                step1_path=paths[1],
                step2_path=paths[2],
                output_dir=output_dir,
                policy=policy,
                repair_agent=writer_agent,
            )
        elif number == 4:
            results[number] = run_step4(
                step1_path=paths[1],
                step3_path=output_dir / "step3_debug_solution.jsonl",
                output_dir=output_dir,
                policy=policy,
            )
        elif number == 5:
            results[number] = run_step5(
                step4_path=paths[4], output_dir=output_dir, agent=writer_agent
            )
        elif number == 6:
            results[number] = run_step6(
                step5_path=paths[5], output_dir=output_dir,
                policy=policy, agent=writer_agent,
            )
        elif number == 7:
            results[number] = run_step7(
                step1_path=paths[1], step6_path=paths[6], output_dir=output_dir,
                policy=policy, model=model,
            )
        elif number == 8:
            results[number] = run_step8(
                step1_path=paths[1], step7_path=paths[7],
                output_dir=output_dir, agent=writer_agent,
            )
        elif number == 9:
            results[number] = run_step9(
                step1_path=paths[1],
                step8_kept_path=output_dir / "step8_filter_rewrite_kept.jsonl",
                output_dir=output_dir, policy=policy, agent=writer_agent,
            )
        elif number == 10:
            results[number] = run_step10(
                step9_kept_path=output_dir / "step9_filter_trace_state_kept.jsonl",
                output_dir=output_dir, policy=policy, agent=writer_agent,
            )
        elif number == 11:
            results[number] = run_step11(
                step1_path=paths[1], step10_path=paths[10], output_dir=output_dir,
                policy=policy, model=model, rubric_agent=writer_agent,
            )
        else:
            results[number] = run_step12(
                step1_path=paths[1], step11_path=paths[11], output_dir=output_dir
            )
    return results


def _policy(arguments: argparse.Namespace) -> ProgramGenerationPolicy:
    return ProgramGenerationPolicy(
        task_count=arguments.task_count,
        candidate_multiplier=arguments.candidate_multiplier,
        task_generation_attempts=arguments.task_generation_attempts,
        min_tool_calls=arguments.min_tool_calls,
        min_distinct_tools=arguments.min_distinct_tools,
        require_state_change=arguments.require_state_change,
        max_repair_rounds=arguments.max_debug_rounds,
        execution_timeout_seconds=arguments.execution_timeout_seconds,
        consistency_runs=arguments.consistency_runs,
        consistency_threshold=arguments.consistency_threshold,
        difficulty_eval_runs=arguments.difficulty_runs,
        trace_state_judge_runs=arguments.step9_judge_runs,
        total_rubric_score=arguments.total_rubric_score,
        general_rubric_score=arguments.general_rubric_score,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", default="all", help="all、N、N-M 或逗号分隔步骤")
    parser.add_argument("--environment-package", type=Path, required=True)
    parser.add_argument("--tools-path", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--task-count", type=int, default=1)
    parser.add_argument("--candidate-multiplier", type=int, default=2)
    parser.add_argument("--task-generation-attempts", type=int, default=3)
    parser.add_argument("--min-tool-calls", type=int, default=4)
    parser.add_argument("--min-distinct-tools", type=int, default=2)
    parser.add_argument("--require-state-change", action="store_true")
    parser.add_argument("--max-debug-rounds", type=int, default=10)
    parser.add_argument("--execution-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--consistency-runs", type=int, default=5)
    parser.add_argument("--consistency-threshold", type=int, default=2)
    parser.add_argument("--step9-judge-runs", type=int, default=3)
    parser.add_argument("--difficulty-runs", type=int, default=5)
    parser.add_argument("--total-rubric-score", type=int, default=14)
    parser.add_argument("--general-rubric-score", type=int, default=6)
    parser.add_argument("--agent-timeout-seconds", type=int, default=1800)
    parser.add_argument("--min-final-pass-rate", type=float, default=0.5)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def validate_arguments(arguments: argparse.Namespace) -> None:
    for name in (
        "task_count",
        "candidate_multiplier",
        "task_generation_attempts",
        "min_tool_calls",
        "min_distinct_tools",
        "consistency_runs",
        "consistency_threshold",
        "step9_judge_runs",
        "difficulty_runs",
        "total_rubric_score",
        "agent_timeout_seconds",
    ):
        if getattr(arguments, name) < 1:
            raise ValueError(f"--{name.replace('_', '-')} 必须至少为 1")
    if arguments.max_debug_rounds < 0:
        raise ValueError("--max-debug-rounds 不能小于 0")
    if arguments.execution_timeout_seconds <= 0:
        raise ValueError("--execution-timeout-seconds 必须大于 0")
    if not 0 <= arguments.min_final_pass_rate <= 1:
        raise ValueError("--min-final-pass-rate 必须位于 0..1")
    if not arguments.environment_package.exists():
        raise FileNotFoundError(f"找不到环境包：{arguments.environment_package}")
    if arguments.tools_path is not None and not arguments.tools_path.is_file():
        raise FileNotFoundError(f"找不到工具文件：{arguments.tools_path}")
    if arguments.candidates is not None and not arguments.candidates.is_file():
        raise FileNotFoundError(f"找不到候选任务文件：{arguments.candidates}")
    _policy(arguments).validate()


def main() -> None:
    arguments = build_parser().parse_args()
    validate_arguments(arguments)
    steps = parse_step_range(arguments.step)
    print(f"Model: {arguments.model}")
    print(f"Environment: {arguments.environment_package.resolve()}")
    print(f"Output: {arguments.output_dir.resolve()}")
    print(f"Runner steps: {steps}")
    for number in STEPS:
        path = arguments.output_dir.resolve() / STEPS[number].output
        print(f"  {number:>2}. {STEPS[number].name}: {'done' if path.is_file() else 'pending'} ({path})")
    if arguments.dry_run:
        return
    results = run_selected_steps(
        steps=steps,
        environment_package=arguments.environment_package,
        tools_path=arguments.tools_path,
        output_dir=arguments.output_dir,
        policy=_policy(arguments),
        model=arguments.model,
        candidates_path=arguments.candidates,
        overwrite=arguments.fresh,
        agent_timeout_seconds=arguments.agent_timeout_seconds,
    )
    if 12 in steps:
        from .utils.io import read_records
        generated = len(read_records(arguments.output_dir / STEPS[2].output))
        final = len(read_records(arguments.output_dir / STEPS[12].output))
        rate = final / generated if generated else 0.0
        print(f"Final task pass rate: {final}/{generated} = {rate:.2%}")
        if generated == 0 or rate < arguments.min_final_pass_rate:
            raise RuntimeError(
                f"最终通过率 {rate:.2%} 低于要求 {arguments.min_final_pass_rate:.2%}"
            )
    print(json.dumps({number: str(result) for number, result in results.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
