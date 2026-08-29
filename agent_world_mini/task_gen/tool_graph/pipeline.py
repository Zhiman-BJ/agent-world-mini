"""Tool Graph 流水线的唯一编排入口。

每阶段固定执行：run_io 提取 Input → 阶段计算 Output → run_io 合并并存档。
阶段函数不能直接读取 AppendOnlyBundle，也不能直接读写运行产物。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import time
from typing import Any

from . import run_io
from .contracts import AppendOnlyBundle, PipelineStep, RunResult
from .step_0_environment_load import load_environment
from .step_1_graph_build import build_graph
from .step_2_chain_sample import sample_chains
from .step_3_chain_execute import execute_chains
from .step_4_task_compose import compose_tasks
from .step_5_task_validate import validate_tasks


def run(
    config_path: Path,
    overrides: dict[str, Any] | None = None,
) -> RunResult:
    """按 Step 0→1→2→3→4→5 运行：环境包 → 已验证任务。"""
    config = run_io.load_config(config_path, overrides)
    run_dir = run_io.create_run_dir(config)
    run_io.save_run_meta(run_dir, config)
    bundle: AppendOnlyBundle = {}
    timings: dict[str, float] = {}

    def stage(step: PipelineStep, producer: Any) -> None:
        started = time.perf_counter()
        output = producer()
        timings[step.value] = round(time.perf_counter() - started, 3)
        run_io.merge_output(bundle, output, step)
        run_io.save_bundle(run_dir, bundle)
        run_io.update_run_meta(run_dir, {"stage_timings_seconds": timings})

    try:
        stage(PipelineStep.ENVIRONMENT_LOAD, lambda: load_environment(run_io.to_environment_load_input(config)))
        stage(PipelineStep.GRAPH_BUILD, lambda: build_graph(run_io.to_build_graph_input(bundle, config)))
        stage(PipelineStep.CHAIN_SAMPLE, lambda: sample_chains(run_io.to_sample_chains_input(bundle, config)))
        stage(PipelineStep.CHAIN_EXECUTE, lambda: execute_chains(run_io.to_execute_chains_input(bundle, config, run_dir)))
        stage(PipelineStep.TASK_COMPOSE, lambda: compose_tasks(run_io.to_compose_tasks_input(bundle, config)))
        stage(PipelineStep.TASK_VALIDATE, lambda: validate_tasks(run_io.to_validate_tasks_input(bundle, config, run_dir)))
        return run_io.finish_run(run_dir, bundle)
    except Exception as error:
        run_io.update_run_meta(run_dir, {
            "status": "failed",
            "failed_step": next((step.value for step in PipelineStep if step.value not in timings), None),
            "error": f"{type(error).__name__}: {error}",
            "stage_timings_seconds": timings,
        })
        raise


def build_parser() -> argparse.ArgumentParser:
    """创建命令行解析器；未提供的参数不覆盖配置文件。"""
    parser = argparse.ArgumentParser(description="Tool Graph task generation pipeline")
    parser.add_argument("--config", type=Path, default=Path("config/tool_graph.yaml"))
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
    print(json.dumps(asdict(run(arguments.config, overrides)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
