"""Run the three-step Program-form task generation pipeline."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.search_agent.codex import CodexAgentClient

from .step_0_environment_load import run_step0
from .step_1_task_research import run_step1
from .step_2_solution_generate import run_step2
from .utils.contracts import ProgramGenerationPolicy


@dataclass(frozen=True)
class StepSpec:
    number: int
    name: str
    artifacts: tuple[str, ...]

    @property
    def primary_output(self) -> str:
        return self.artifacts[0]


STEPS = {
    0: StepSpec(0, "environment_load", ("step0_environment.json", "baseline_environment")),
    1: StepSpec(1, "task_research", ("step1_task_research.json",)),
    2: StepSpec(
        2,
        "solution_generate",
        (
            "step2_task_solution.jsonl",
            "step2_validation.json",
            "tasks.json",
            "rejected.json",
            "intermediate/step_5_bundle.json",
        ),
    ),
}


def parse_step_range(value: str) -> list[int]:
    if value.strip().lower() == "all":
        return list(STEPS)
    selected: set[int] = set()
    for raw in value.split(","):
        part = raw.strip()
        if not part:
            raise ValueError("步骤表达式中包含空项")
        if "-" in part:
            start, end = (int(item) for item in part.split("-", 1))
            if start > end:
                raise ValueError(f"步骤范围起点大于终点：{part}")
            selected.update(range(start, end + 1))
        else:
            selected.add(int(part))
    unknown = selected - set(STEPS)
    if unknown:
        raise ValueError(f"未知步骤：{sorted(unknown)}")
    return sorted(selected)


def _agent(model: str, timeout: int, *, web_research: bool) -> CodexAgentClient:
    return CodexAgentClient(
        model=model,
        timeout_seconds=timeout,
        sandbox="workspace-write",
        enable_web_search=web_research,
        network_access=web_research,
        reasoning_effort="high",
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )


def _require_input(path: Path, step: int) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Step {step} 缺少上游产物：{path}。请先运行前置步骤。")


def run_selected_steps(
    *,
    steps: list[int],
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    model: str,
    binding_path: Path | None = None,
    delivery_root: Path | None = None,
    package_id: str | None = None,
    environment_package: Path | None = None,
    tools_path: Path | None = None,
    scenario_research_path: Path | None = None,
    research_fixture_path: Path | None = None,
    research_attempts: int = 3,
    candidates_path: Path | None = None,
    overwrite: bool = False,
    agent_timeout_seconds: int = 1800,
) -> dict[int, Any]:
    """Run selected generation steps while preserving their file checkpoints."""
    policy.validate()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {number: output_dir / spec.primary_output for number, spec in STEPS.items()}
    results: dict[int, Any] = {}
    research_agent: CodexAgentClient | None = None
    workflow_agent: CodexAgentClient | None = None

    for number in steps:
        spec = STEPS[number]
        artifact_paths = tuple(output_dir / relative for relative in spec.artifacts)
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

        if number == 0:
            results[number] = run_step0(
                binding_path=binding_path,
                delivery_root=delivery_root,
                package_id=package_id,
                environment_package=environment_package,
                tools_path=tools_path,
                scenario_research_path=scenario_research_path,
                output_dir=output_dir,
                overwrite=overwrite,
            )
            continue

        _require_input(paths[number - 1], number)
        if number == 1:
            if research_fixture_path is None and research_agent is None:
                research_agent = _agent(model, agent_timeout_seconds, web_research=True)
            results[number] = run_step1(
                step0_path=paths[0],
                output_dir=output_dir,
                agent=research_agent,
                research_fixture_path=research_fixture_path,
                max_attempts=research_attempts,
            )
            continue

        if workflow_agent is None:
            workflow_agent = _agent(model, agent_timeout_seconds, web_research=False)
        results[number] = run_step2(
            step0_path=paths[0],
            step1_path=paths[1],
            output_dir=output_dir,
            policy=policy,
            generation_agent=workflow_agent,
            review_agent=workflow_agent,
            candidates_path=candidates_path,
        )
    return results


def _policy(arguments: argparse.Namespace) -> ProgramGenerationPolicy:
    return ProgramGenerationPolicy(
        task_count=arguments.task_count,
        candidate_multiplier=arguments.candidate_multiplier,
        task_generation_attempts=arguments.task_generation_attempts,
        clean_replays=arguments.clean_replays,
        require_state_change=arguments.require_state_change,
        max_repair_rounds=arguments.max_repair_rounds,
        execution_timeout_seconds=arguments.execution_timeout_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", default="all", help="all、N、N-M 或逗号分隔步骤")
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--delivery-root", type=Path)
    parser.add_argument("--package-id")
    parser.add_argument("--environment-package", type=Path)
    parser.add_argument("--tools-path", type=Path)
    parser.add_argument("--scenario-research", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--research-fixture", type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--task-count", type=int, default=1)
    parser.add_argument("--candidate-multiplier", type=int, default=2)
    parser.add_argument("--research-attempts", type=int, default=3)
    parser.add_argument("--task-generation-attempts", type=int, default=3)
    parser.add_argument("--clean-replays", type=int, default=2)
    parser.add_argument("--require-state-change", action="store_true")
    parser.add_argument("--max-repair-rounds", type=int, default=10)
    parser.add_argument("--execution-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--agent-timeout-seconds", type=int, default=1800)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def validate_arguments(arguments: argparse.Namespace) -> None:
    for name in (
        "task_count",
        "candidate_multiplier",
        "research_attempts",
        "task_generation_attempts",
        "clean_replays",
        "agent_timeout_seconds",
    ):
        if getattr(arguments, name) < 1:
            raise ValueError(f"--{name.replace('_', '-')} 必须至少为 1")
    if arguments.max_repair_rounds < 0:
        raise ValueError("--max-repair-rounds 不能小于 0")
    if arguments.execution_timeout_seconds <= 0:
        raise ValueError("--execution-timeout-seconds 必须大于 0")

    binding_mode = arguments.binding is not None or arguments.package_id is not None
    legacy_mode = arguments.environment_package is not None
    if binding_mode == legacy_mode:
        raise ValueError("必须且只能选择 binding 模式或 environment-package 模式")
    if arguments.binding is not None and not arguments.binding.is_file():
        raise FileNotFoundError(f"找不到 --binding：{arguments.binding}")
    if arguments.package_id is not None:
        if arguments.delivery_root is None or not arguments.delivery_root.is_dir():
            raise FileNotFoundError("--package-id 需要存在的 --delivery-root")
    if legacy_mode and not arguments.environment_package.exists():
        raise FileNotFoundError(f"找不到环境包：{arguments.environment_package}")
    for name in ("tools_path", "scenario_research", "research_fixture", "candidates"):
        value = getattr(arguments, name)
        if value is not None and not value.is_file():
            raise FileNotFoundError(f"找不到 --{name.replace('_', '-')}：{value}")
    _policy(arguments).validate()


def main() -> None:
    arguments = build_parser().parse_args()
    validate_arguments(arguments)
    steps = parse_step_range(arguments.step)
    output_dir = arguments.output_dir.resolve()
    source = arguments.binding or (
        arguments.delivery_root / "environments" / arguments.package_id / "binding.json"
        if arguments.package_id
        else arguments.environment_package
    )
    print(f"Model: {arguments.model}")
    print(f"Input: {source.resolve() if source else 'missing'}")
    print(f"Output: {output_dir}")
    print(f"Runner steps: {steps}")
    for number, spec in STEPS.items():
        artifact_paths = [output_dir / relative for relative in spec.artifacts]
        status = "done" if all(path.exists() for path in artifact_paths) else (
            "partial" if any(path.exists() for path in artifact_paths) else "pending"
        )
        print(f"  {number}. {spec.name}: {status}")
    if arguments.dry_run:
        return
    results = run_selected_steps(
        steps=steps,
        binding_path=arguments.binding,
        delivery_root=arguments.delivery_root,
        package_id=arguments.package_id,
        environment_package=arguments.environment_package,
        tools_path=arguments.tools_path,
        scenario_research_path=arguments.scenario_research,
        output_dir=output_dir,
        policy=_policy(arguments),
        model=arguments.model,
        research_fixture_path=arguments.research_fixture,
        research_attempts=arguments.research_attempts,
        candidates_path=arguments.candidates,
        overwrite=arguments.fresh,
        agent_timeout_seconds=arguments.agent_timeout_seconds,
    )
    print(json.dumps(
        {number: str(result) for number, result in results.items()},
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
