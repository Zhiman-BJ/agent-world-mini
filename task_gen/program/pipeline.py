"""Single orchestration entry point for the Program task pipeline."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import fields
import fcntl
import json
from pathlib import Path
import signal
from typing import Any

from . import llm, run_io
from .contracts import Config, ProgramPipelineStep, RunResult

def load_environment(input: dict[str, Any]) -> dict[str, Any]:
    from .step_0_environment_load import load_environment as implementation
    return implementation(input)


def research_tasks(input: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    from .step_1_task_research import research_tasks as implementation
    return implementation(input, **kwargs)


def generate_solutions(input: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    from .step_2_solution_generate import generate_solutions as implementation
    return implementation(input, **kwargs)

def _config_from_meta(values: dict[str, Any]) -> Config:
    policy_values = values.get("policy") or {}
    # Keep this explicit: serialized policy also contains derived candidate_count.
    from .utils.contracts import ProgramGenerationPolicy

    policy = ProgramGenerationPolicy(**{name: policy_values[name] for name in ProgramGenerationPolicy.__dataclass_fields__ if name in policy_values})
    values = dict(values)
    if values.get("environment_package"):
        values["environment_package"] = Path(values["environment_package"])
    values["output_root"] = Path(values["output_root"])
    for name in ("tools_path", "delivery_root", "binding_path", "scenario_research_path", "research_fixture_path", "candidates_path"):
        if values.get(name):
            values[name] = Path(values[name])
    values["policy"] = policy
    return Config(**{field.name: values[field.name] for field in fields(Config) if field.name in values})


def _client(config: Config, *, web_research: bool = False):
    from utils.search_agent.codex import CodexAgentClient

    return CodexAgentClient(
        model=config.model,
        timeout_seconds=config.agent_timeout_seconds,
        sandbox="workspace-write",
        enable_web_search=web_research,
        network_access=web_research,
        reasoning_effort="high",
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )


class _Paused(Exception):
    pass


def run(
    config: Config | None = None,
    *,
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
    resume: Path | None = None,
    stop_after: int | None = None,
    should_stop: Any = lambda: False,
) -> RunResult | None:
    if stop_after is not None and stop_after not in range(3):
        raise ValueError("stop_after 必须为 0 到 2")
    if resume is not None:
        run_dir = resume.expanduser().resolve()
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        config = _config_from_meta(meta["config"])
    else:
        if config is None:
            config = run_io.load_config(config_path, overrides or {})
        run_dir = run_io.create_run_dir(config)
        run_io.save_run_meta(run_dir, config)
        meta = {}
    assert config is not None
    with (run_dir / ".pipeline.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("该运行目录仍有 pipeline 进程执行") from error
        return _run_stages(config, run_dir, stop_after, should_stop, meta)


def _run_stages(config: Config, run_dir: Path, stop_after: int | None, should_stop: Any, meta: dict[str, Any]) -> RunResult | None:
    checkpoint = run_io.load_latest_bundle(run_dir)
    completed, bundle = checkpoint if checkpoint else (-1, {})
    clients: dict[str, Any] = {}
    run_io.update_run_meta(run_dir, {"status": "running", "last_completed_step": completed})

    def agent(name: str, *, web_research: bool = False) -> Any:
        if name not in clients:
            clients[name] = _client(config, web_research=web_research)
        return clients[name]

    def stage(step: ProgramPipelineStep, producer: Any) -> None:
        nonlocal completed, bundle
        index = list(ProgramPipelineStep).index(step)
        if index <= completed:
            return
        if should_stop() or (stop_after is not None and completed >= stop_after):
            raise _Paused()
        with llm.capture_calls(step.value, lambda record: run_io.append_llm_call(run_dir, record)):
            output = producer()
        run_io.merge_output(bundle, output, step)
        run_io.save_bundle(run_dir, bundle)
        completed = index

    try:
        stage(ProgramPipelineStep.ENVIRONMENT_LOAD, lambda: load_environment({"config": config, "run_dir": run_dir}))
        stage(ProgramPipelineStep.TASK_RESEARCH, lambda: research_tasks({**bundle, "config": config, "run_dir": run_dir}, agent=None if config.research_fixture_path else agent("research", web_research=True)))
        stage(ProgramPipelineStep.SOLUTION_GENERATE, lambda: generate_solutions({**bundle, "config": config, "run_dir": run_dir}, generation_agent=None if config.candidates_path else agent("workflow"), review_agent=agent("workflow")))
        if should_stop():
            raise _Paused()
        return run_io.finish_run(run_dir, bundle)
    except (_Paused, KeyboardInterrupt):
        run_io.update_run_meta(run_dir, {"status": "paused", "last_completed_step": completed})
        return None
    except Exception as error:
        run_io.update_run_meta(run_dir, {"status": "failed", "error": f"{type(error).__name__}: {error}"})
        raise


@contextmanager
def _stop_signals():
    requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal requested
        requested = True

    previous = {s: signal.signal(s, request_stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield lambda: requested
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--stop-after", type=int, choices=range(3))
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--delivery-root", type=Path)
    parser.add_argument("--package-id")
    parser.add_argument("--environment-package", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--model")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    overrides = {
        "binding_path": args.binding,
        "delivery_root": args.delivery_root,
        "package_id": args.package_id,
        "environment_package": args.environment_package,
        "output_root": args.output_root,
        "model": args.model,
    }
    with _stop_signals() as should_stop:
        result = run(config_path=args.config, overrides=overrides, resume=args.resume, stop_after=args.stop_after, should_stop=should_stop)
    print(json.dumps(result.__dict__ if result else {"status": "paused"}, default=str, ensure_ascii=False, indent=2))
