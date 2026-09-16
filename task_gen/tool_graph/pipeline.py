"""Tool Graph 流水线的唯一编排入口。

每阶段固定执行：run_io 提取 Input → 阶段计算 Output → run_io 合并并存档。
阶段函数不能直接读取 AppendOnlyBundle，也不能直接读写运行产物。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
from dataclasses import asdict
from pathlib import Path
import time
import signal
from typing import Any

from . import llm, run_io
from .contracts import AppendOnlyBundle, Config, PipelineStep, RunResult
from .step_0_environment_load import load_environment
from .step_1_graph_build import build_graph
from .step_2_chain_sample import sample_chains
from .step_3_chain_execute import execute_chains
from .step_4_task_compose import compose_tasks
from .step_5_task_validate import validate_tasks


def run(
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
    *,
    resume: Path | None = None,
    stop_after: int | None = None,
    should_stop: Any = lambda: False,
) -> RunResult | None:
    """阶段级恢复；同一目录同时只允许一个执行进程。暂停返回 None。"""
    if stop_after is not None and stop_after not in range(6):
        raise ValueError("stop_after 必须为 0 到 5")
    if resume is not None:
        if config_path is not None or any(v is not None for v in (overrides or {}).values()):
            raise ValueError("恢复使用 run.json 中的原配置，不能同时覆盖配置")
        run_dir = resume.expanduser().resolve()
        if not (run_dir / "run.json").is_file():
            raise ValueError("恢复目录缺少 run.json")
    else:
        config = run_io.load_config(config_path or Path("config/tool_graph.yaml"), overrides)
        run_dir = run_io.create_run_dir(config)
        run_io.save_run_meta(run_dir, config)
    with (run_dir / ".pipeline.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("该运行目录仍有 pipeline 进程执行，请先等待它停止") from error
        meta = {}
        if resume is not None:
            meta = json.loads((run_dir / "run.json").read_text())
            values = dict(meta["config"])
            for key in ("environment_dir", "schema_dir", "output_root"):
                values[key] = Path(values[key])
            config = Config(**values)
        return _run_stages(config, run_dir, stop_after, should_stop, meta)


def _run_stages(config, run_dir, stop_after, should_stop, meta):
    """按 Step 0→1→2→3→4→5 运行：环境包 → 已验证任务。"""
    checkpoint = run_io.load_latest_bundle(run_dir)
    completed, bundle = checkpoint if checkpoint else (-1, {})
    if checkpoint and bundle.get("_step") != list(PipelineStep)[completed].value:
        raise ValueError("检查点文件名与 _step 不一致")
    if stop_after is not None and stop_after < completed:
        raise ValueError("停止位置早于已有检查点；恢复不会回退已完成阶段")
    timings = dict(meta.get("stage_timings_seconds", {}))
    active_step: PipelineStep | None = None
    run_io.update_run_meta(run_dir, {"status": "running", "error": None, "failed_step": None})
    print(f"Run: {run_dir}; resume after step {completed}", flush=True)

    def stage(step: PipelineStep, producer: Any) -> None:
        nonlocal active_step, completed
        number = list(PipelineStep).index(step)
        if number <= completed:
            return
        if should_stop() or (stop_after is not None and completed >= stop_after):
            raise _Paused()
        active_step = step
        # 未完成的执行阶段重新使用干净初态，旧产物保留供调查。
        if step == PipelineStep.CHAIN_EXECUTE and (run_dir / "tasks").exists() and any((run_dir / "tasks").iterdir()):
            (run_dir / "tasks").rename(run_dir / f"tasks_interrupted_{time.time_ns()}")
            (run_dir / "tasks").mkdir()
        started = time.perf_counter()
        with llm.capture_calls(
            step.value,
            lambda record: run_io.append_llm_call(run_dir, record),
        ):
            output = producer()
        run_io.merge_output(bundle, output, step)
        run_io.save_bundle(run_dir, bundle)
        completed = number
        timings[step.value] = round(time.perf_counter() - started, 3)
        run_io.update_run_meta(run_dir, {"stage_timings_seconds": timings})
        active_step = None

    try:
        stage(PipelineStep.ENVIRONMENT_LOAD, lambda: load_environment(run_io.to_environment_load_input(config)))
        stage(PipelineStep.GRAPH_BUILD, lambda: build_graph(
            run_io.to_build_graph_input(bundle, config),
            checkpoint_dir=run_dir / "intermediate" / "step_1_targets",
        ))
        stage(PipelineStep.CHAIN_SAMPLE, lambda: sample_chains(run_io.to_sample_chains_input(bundle, config)))
        stage(PipelineStep.CHAIN_EXECUTE, lambda: execute_chains(run_io.to_execute_chains_input(bundle, config, run_dir)))
        stage(PipelineStep.TASK_COMPOSE, lambda: compose_tasks(run_io.to_compose_tasks_input(bundle, config)))
        stage(PipelineStep.TASK_VALIDATE, lambda: validate_tasks(run_io.to_validate_tasks_input(bundle, config, run_dir)))
        if stop_after == 5 or should_stop():
            raise _Paused()
        return run_io.finish_run(run_dir, bundle)
    except (_Paused, KeyboardInterrupt):
        run_io.update_run_meta(run_dir, {"status": "paused", "last_completed_step": completed,
                                       "interrupted_step": active_step.value if active_step else None,
                                       "stage_timings_seconds": timings})
        return None
    except Exception as error:
        run_io.update_run_meta(run_dir, {
            "status": "failed",
            "failed_step": active_step.value if active_step else None,
            "error": f"{type(error).__name__}: {error}",
            "stage_timings_seconds": timings,
        })
        raise


class _Paused(Exception):
    pass


@contextmanager
def _stop_signals():
    """CLI 收到 Ctrl+C/SIGTERM 后完成当前阶段并暂停；强杀后也可恢复旧检查点。"""
    requested = False
    def request_stop(signum, frame):
        nonlocal requested
        requested = True
        print("已请求停止：当前阶段保存检查点后退出。", flush=True)
    previous = {s: signal.signal(s, request_stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield lambda: requested
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def build_parser() -> argparse.ArgumentParser:
    """创建命令行解析器；未提供的参数不覆盖配置文件。"""
    parser = argparse.ArgumentParser(description="Tool Graph task generation pipeline")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--resume", type=Path, help="从运行目录最后完成的阶段恢复，使用原配置")
    parser.add_argument("--stop-after", type=int, choices=range(6), help="保存指定阶段检查点后停止")
    parser.add_argument("--environment-dir", type=Path)
    parser.add_argument("--schema-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--backend")
    return parser


def main() -> None:
    """命令行入口。"""
    arguments = build_parser().parse_args()
    overrides = {
        "environment_dir": arguments.environment_dir,
        "schema_dir": arguments.schema_dir,
        "output_root": arguments.output_root,
        "model": arguments.model,
        "backend": arguments.backend,
    }
    with _stop_signals() as should_stop:
        result = run(arguments.config, overrides, resume=arguments.resume,
                     stop_after=arguments.stop_after, should_stop=should_stop)
    print(json.dumps(asdict(result) if result else {"status": "paused"}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
