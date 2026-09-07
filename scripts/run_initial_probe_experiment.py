#!/usr/bin/env python3
"""Run Step 2-5 against a recorded graph, with ordinary checkpoints and tracing."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from task_gen.tool_graph import llm, run_io
from task_gen.tool_graph.contracts import PipelineStep
from task_gen.tool_graph.step_0_environment_load import load_environment
from task_gen.tool_graph.step_2_chain_sample import sample_chains
from task_gen.tool_graph.step_3_chain_execute import execute_chains
from task_gen.tool_graph.step_4_task_compose import compose_tasks
from task_gen.tool_graph.step_5_task_validate import validate_tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--graph", type=Path)
    source.add_argument("--from-step2", type=Path, help="Replay frozen Step 2 candidates in fresh workspaces")
    parser.add_argument("--config", type=Path, default=ROOT / "config/tool_graph.yaml")
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs/initial_probe")
    args = parser.parse_args()
    source_path = args.graph or args.from_step2
    graph_bytes = source_path.read_bytes()
    graph = json.loads(graph_bytes)
    edges = graph.get("tool_graph", graph.get("edges")) if isinstance(graph, dict) else graph
    config = run_io.load_config(args.config, {"output_root": args.output_root})
    run_dir = run_io.create_run_dir(config)
    run_io.save_run_meta(run_dir, config)
    run_io.update_run_meta(run_dir, {
        "source_checkpoint": str(source_path.resolve()),
        "reused_step_1_graph_sha256": hashlib.sha256(graph_bytes).hexdigest(),
        "reused_step_1_edge_count": len(edges),
        "graph_projection": "edges only; Step 2 does not consume prerequisites",
    })
    print(f"Run: {run_dir}", flush=True)
    # Checkpoints retain the validated environment even when its original manifest has moved.
    bundle = {"environment": graph["environment"]} if isinstance(graph, dict) and "environment" in graph else load_environment({"config": config})
    run_io.merge_output(bundle, {"tool_graph": edges}, PipelineStep.GRAPH_BUILD)
    if args.from_step2:
        if graph.get("_step") != PipelineStep.CHAIN_SAMPLE.value:
            raise ValueError("--from-step2 必须是 Step 2 检查点")
        run_io.merge_output(bundle, {key: graph[key] for key in ("tasks", "sampling_report", "initial_state_report")}, PipelineStep.CHAIN_SAMPLE)
        run_io.update_run_meta(run_dir, {"resumed_from_step_2": str(source_path.resolve()), "diagnostic": graph["sampling_report"].get("diagnostic", False)})
    run_io.save_bundle(run_dir, bundle)
    timings = {}
    stages = [
        (PipelineStep.CHAIN_SAMPLE, sample_chains, lambda: run_io.to_sample_chains_input(bundle, config)),
        (PipelineStep.CHAIN_EXECUTE, execute_chains, lambda: run_io.to_execute_chains_input(bundle, config, run_dir)),
        (PipelineStep.TASK_COMPOSE, compose_tasks, lambda: run_io.to_compose_tasks_input(bundle, config)),
        (PipelineStep.TASK_VALIDATE, validate_tasks, lambda: run_io.to_validate_tasks_input(bundle, config, run_dir)),
    ]
    if args.from_step2:
        stages = stages[1:]
    for step, producer, stage_input in stages:
        started = time.monotonic()
        print(f"Starting {step.value}", flush=True)
        try:
            with llm.capture_calls(step.value, lambda record: run_io.append_llm_call(run_dir, record)):
                output = producer(stage_input())
            run_io.merge_output(bundle, output, step)
            run_io.save_bundle(run_dir, bundle)
        except Exception as error:
            run_io.update_run_meta(run_dir, {"status": "failed", "failed_step": step.value, "error": str(error)})
            raise
        timings[step.value] = round(time.monotonic() - started, 3)
        run_io.update_run_meta(run_dir, {"stage_timings_seconds": timings})
        print(f"Finished {step.value}: {len(bundle['tasks'])} candidates, {timings[step.value]}s", flush=True)
    print(json.dumps(asdict(run_io.finish_run(run_dir, bundle)), ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
