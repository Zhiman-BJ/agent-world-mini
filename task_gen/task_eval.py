"""Run generated tasks with ReAct, then independently investigate their delivery."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import shutil
import tempfile
import time
from typing import Any, Callable
from harness.mcp_protocol import public_tools

from .eval_cases import EvalCase, load_cases
from .task_eval_react import run_react_agent
from .tool_graph.llm import capture_calls
from .tool_graph.run_io import append_llm_call, load_config
from .tool_graph.step_3_chain_execute import (
    _public_environment,
    _tools,
    _workspace_signature,
)
from eval.verifier import verify_execution, VerificationError


DEFAULT_INPUT_ROOT = Path(__file__).resolve().parents[1] / "runs/taskgen"
KIMI_MAX_TOOL_CALLS = 65
AgentRunFn = Callable[[str, Path, Path, Path], str]


def evaluate_case(
    case: EvalCase,
    workspace: Path,
    llm_config: dict[str, Any],
    *,
    max_tool_calls: int | None = None,
    agent_attempts: int = 3,
    tool_timeout_seconds: int = 300,
    tool_max_memory_bytes: int = 2 * 1024 * 1024 * 1024,
    tool_max_write_bytes: int = 256 * 1024 * 1024,
    agent_run_fn: AgentRunFn | None = None,
    verifier_run_fn: Callable[..., dict[str, Any]] = verify_execution,
    verifier_output: Path | None = None,
) -> dict[str, Any]:
    """Let the configured agent solve a task with environment tools, then judge it."""
    if max_tool_calls is None:
        max_tool_calls = KIMI_MAX_TOOL_CALLS if llm_config.get('agent_backend') == 'kimi' else 50
    if max_tool_calls < 1:
        raise ValueError("max_tool_calls 必须大于 0")
    if llm_config.get('agent_backend') == 'kimi' and max_tool_calls > KIMI_MAX_TOOL_CALLS:
        raise ValueError(f"Kimi max_tool_calls 不能超过 {KIMI_MAX_TOOL_CALLS}")
    if agent_attempts < 1:
        raise ValueError("agent_attempts 必须大于 0")
    workspace = workspace.expanduser().resolve()
    if workspace.exists():
        raise ValueError(f"评测 workspace 已存在：{workspace}")
    source_signature = _workspace_signature(case.initial_state)
    shutil.copytree(case.initial_state, workspace)
    tools = _tools(case.environment)
    expected_tools = public_tools(tools.values())
    legacy_tools = [
        {key: tool[key] for key in ("name", "description", "inputSchema", "outputSchema", "usageConditions") if key in tool}
        for tool in tools.values()
    ]
    if case.task.get("available_tools") not in (expected_tools, legacy_tools):
        raise ValueError("task.available_tools 与环境公开工具契约不一致")
    verifier_config = dict(llm_config.get("verifier", {}))
    # Verifier defaults come from .codex, independent of the solver API model.
    answer, calls, agent_error = "", [], None
    run_agent = agent_run_fn or (lambda prompt, cwd, config, call_trace: _run_agent(
        prompt, cwd, config, call_trace, llm_config,
    ))
    for agent_attempt in range(1, agent_attempts + 1):
        with tempfile.TemporaryDirectory(prefix="task-eval-mcp-") as temporary:
            server_config = Path(temporary) / "server.json"
            trace = Path(temporary) / "calls.jsonl"
            server_config.write_text(json.dumps({
                "state_root": str(workspace),
                "trace": str(trace),
                "max_tool_calls": max_tool_calls,
                "timeout": tool_timeout_seconds,
                "memory_limit": tool_max_memory_bytes,
                "write_limit": tool_max_write_bytes,
                "tools": list(tools.values()),
                "environment": case.environment,
                **({"binding_path": case.runtime['binding_path']} if case.runtime.get('binding_path') else {}),
                **({"tools_path": case.runtime['tools_path']} if case.runtime.get('tools_path') else {}),
                **({"software": case.runtime['software']} if case.runtime.get('software') else {}),
                **({"runtime": case.runtime['runtime']} if case.runtime.get('runtime') else {}),
                "record_database": str(workspace / "records.sqlite"),
                "scope_mounts": {
                    str(item["scope_id"]): f"filesystem_scopes/{item['scope_id']}"
                    for item in case.environment.get("filesystem_scopes", [])
                },
            }, ensure_ascii=False), encoding="utf-8")
            try:
                answer = run_agent(_agent_prompt(case, max_tool_calls), workspace, server_config, trace).strip()
                if not answer:
                    raise ValueError("评测 Agent 未提交最终答案")
            except Exception as error:
                agent_error = f"{type(error).__name__}: {error}"
            finally:
                calls = _read_trace(trace)
        if agent_error:
            break
        unavailable = (
            not calls
            and _workspace_signature(workspace) == source_signature
            and "503" in answer
            and any(token in answer.lower() for token in (
                "service", "approval", "unavailable", "服务", "审批", "不可用",
            ))
        )
        if not unavailable:
            break
        if agent_attempt == agent_attempts:
            agent_error = f"评测 Agent 基础设施连续 {agent_attempts} 次返回 503 且未执行工具调用"

    if _workspace_signature(case.initial_state) != source_signature:
        raise ValueError("来源初态在评测期间被修改")
    changes = _workspace_changes(source_signature, _workspace_signature(workspace))
    state_error = None
    if case.environment.get('schema_version') == '2.0':
        from .tool_graph.state_runtime import snapshot_state, state_diff
        try:
            changes = state_diff(snapshot_state(case.initial_state, case.environment), snapshot_state(workspace, case.environment))
        except Exception as error:
            # A corrupted final database is evidence to investigate, not a reason to skip judging.
            state_error = f"{type(error).__name__}: {error}"
    verifier_dir = (verifier_output.with_suffix("") if verifier_output else workspace.parent / (workspace.name + ".verifier"))
    verifier_error = None
    try:
        evaluation = verifier_run_fn(
            run_dir=verifier_dir, config=verifier_config,
            task=case.task, environment=case.environment,
            initial_state=case.initial_state, actual_state=workspace,
            calls=calls, answer=answer,
            execution={"error": agent_error, "attempts": agent_attempt, "state_inspection_error": state_error},
            reference_state=case.reference_state, reference_calls=case.reference_calls,
            reference_answer=case.task.get("reference", {}).get("answer", ""),
        )
    except VerificationError as error:
        verifier_error = str(error)
        evaluation = {"outcome": "verification_error", "summary": str(error), "requirements": []}
    if _workspace_signature(case.initial_state) != source_signature:
        raise ValueError("来源初态在验证期间被修改")
    return {
        "source_run": case.source_run.name, "task_id": case.task.get("task_id"),
        "environment_id": case.task.get("environment_id"), "task_text": case.task.get("task_text"),
        "workspace": str(workspace), "agent_response": answer, "agent_answer": answer,
        "agent_attempts": agent_attempt, "agent_error": agent_error, "tool_calls": calls,
        "workspace_changes": changes, "evaluation": evaluation,
        "outcome": evaluation["outcome"], "verifier_directory": str(verifier_dir),
        "error": verifier_error,
    }


def _agent_prompt(case: EvalCase, max_tool_calls: int) -> str:
    del max_tool_calls
    task_text = case.task.get("task_text")
    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be non-empty")
    return task_text.strip()


def _run_agent(
    prompt: str,
    workspace: Path,
    server_config: Path,
    trace: Path,
    llm_config: dict[str, Any],
) -> str:
    agent_backend = llm_config.get("agent_backend", "react")
    if agent_backend == "kimi":
        from .task_eval_kimi import run_kimi_agent
        return run_kimi_agent(prompt, workspace, server_config, trace, llm_config)
    if agent_backend != "react":
        raise ValueError(f"未知 agent_backend：{agent_backend}")
    return run_react_agent(prompt, workspace, server_config, trace, llm_config)


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]




def _workspace_changes(
    before: tuple[tuple[str, int, str], ...],
    after: tuple[tuple[str, int, str], ...],
) -> list[dict[str, str]]:
    old = {path: (mode, digest) for path, mode, digest in before}
    new = {path: (mode, digest) for path, mode, digest in after}
    return [
        {
            "path": path,
            "change": "added" if path not in old else "deleted" if path not in new else "modified",
        }
        for path in sorted(old.keys() | new.keys())
        if old.get(path) != new.get(path)
    ]




def run_evaluation(
    input_root: Path,
    output_root: Path,
    llm_config: dict[str, Any],
    execution_config: dict[str, Any],
    *,
    limit: int | None = None,
    sample_size: int | None = None,
    sample_seed: int = 20261005,
    environment_id: str | None = None,
    max_tool_calls: int | None = None,
    max_concurrency: int = 1,
) -> Path:
    cases = load_cases(input_root)
    if environment_id:
        cases = [case for case in cases if case.task.get("environment_id") == environment_id]
    population_size = len(cases)
    if sample_size is not None:
        if sample_size > population_size:
            raise ValueError(f"抽样数 {sample_size} 超过可评测任务数 {population_size}")
        cases = random.Random(sample_seed).sample(cases, sample_size)
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        raise ValueError("没有找到可评测任务")
    run_dir = output_root.expanduser().resolve() / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True)
    verifier_dir = run_dir / "verifiers"
    verifier_dir.mkdir()
    task_result_dir = run_dir / "task_results"
    task_result_dir.mkdir()
    selection = {
        "input_root": str(input_root.expanduser().resolve()),
        "population_size": population_size,
        "sample_size": sample_size,
        "sample_seed": sample_seed if sample_size is not None else None,
        "selected_count": len(cases),
        "cases": [{
            "case_name": f"{case.source_run.name}__{case.task['task_id']}",
            "source_run": str(case.source_run),
            "task_id": case.task["task_id"],
            "environment_id": case.task.get("environment_id"),
            "task_text_sha256": hashlib.sha256(
                str(case.task.get("task_text", "")).encode("utf-8")
            ).hexdigest(),
            "initial_state": str(case.initial_state),
            "reference_state": str(case.reference_state) if case.reference_state else None,
        } for case in cases],
    }
    (run_dir / "selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    def run(case: EvalCase) -> dict[str, Any]:
        filename = f"{case.source_run.name}__{case.task['task_id']}"
        workspace = run_dir / "workspaces" / filename
        solver_log_dir = run_dir / "solver_llm_logs" / filename
        solver_log_dir.mkdir(parents=True)
        started = time.perf_counter()
        try:
            with capture_calls("task_eval.execution", lambda record: append_llm_call(solver_log_dir, record)):
                result = evaluate_case(
                    case,
                    workspace,
                    llm_config,
                    max_tool_calls=max_tool_calls,
                    agent_attempts=int(execution_config.get("retry_count", 3)),
                    tool_timeout_seconds=int(execution_config.get("tool_timeout_seconds", 300)),
                    tool_max_memory_bytes=int(execution_config.get("tool_max_memory_bytes", 2 * 1024 * 1024 * 1024)),
                    tool_max_write_bytes=int(execution_config.get("tool_max_write_bytes", 256 * 1024 * 1024)),
                    verifier_output=verifier_dir / f"{filename}.json",
                )
        except Exception as error:
            verifier_error = isinstance(error, VerificationError)
            result = {
                "source_run": case.source_run.name,
                "task_id": case.task.get("task_id"),
                "environment_id": case.task.get("environment_id"),
                "task_text": case.task.get("task_text"),
                "outcome": "verification_error" if verifier_error else "infrastructure_error",
                "attribution": "verifier" if verifier_error else "infrastructure",
                "error": f"{type(error).__name__}: {error}",
            }
        result["duration_seconds"] = time.perf_counter() - started
        (task_result_dir / f"{filename}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return result

    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        results = list(executor.map(run, cases))
    counts = _result_counts(results)
    payload = {
        "input_root": str(input_root.expanduser().resolve()),
        "model": llm_config.get("model"),
        "agent_backend": "kimi" if llm_config.get("agent_backend") == "kimi" else "react-api",
        "task_count": len(results),
        **counts,
        "results": results,
    }
    (run_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def _result_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    outcomes = [item.get("outcome") for item in results]
    return {
        "passed_count": sum(outcome == "pass" for outcome in outcomes),
        "failed_count": sum(outcome == "fail" for outcome in outcomes),
        "indeterminate_count": sum(outcome == "indeterminate" for outcome in outcomes),
        "verification_error_count": sum(outcome == "verification_error" for outcome in outcomes),
        "infrastructure_error_count": sum(outcome == "infrastructure_error" for outcome in outcomes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="让模型执行已生成任务，再由 Codex 按任务文本独立验收")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output-root", type=Path, default=Path("runs/task_eval"))
    parser.add_argument("--config", type=Path, default=Path("config/tool_graph.yaml"))
    parser.add_argument("--model")
    parser.add_argument("--backend")
    parser.add_argument("--agent-backend", choices=["react", "kimi"])
    parser.add_argument("--environment-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--sample-seed", type=int, default=20261005)
    parser.add_argument("--max-tool-calls", type=int)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--llm-timeout-seconds", type=int)
    arguments = parser.parse_args()
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit 必须大于 0")
    if arguments.sample_size is not None and arguments.sample_size < 1:
        parser.error("--sample-size 必须大于 0")
    if arguments.limit is not None and arguments.sample_size is not None:
        parser.error("--limit 和 --sample-size 不能同时使用")
    if arguments.max_tool_calls is not None and arguments.max_tool_calls < 1:
        parser.error("--max-tool-calls 必须大于 0")
    if arguments.max_concurrency is not None and arguments.max_concurrency < 1:
        parser.error("--max-concurrency 必须大于 0")
    if arguments.llm_timeout_seconds is not None and arguments.llm_timeout_seconds < 1:
        parser.error("--llm-timeout-seconds 必须大于 0")
    config = load_config(arguments.config, {"model": arguments.model, "backend": arguments.backend})
    llm_config = dict(config.llm)
    if arguments.agent_backend is not None:
        llm_config['agent_backend'] = arguments.agent_backend
    max_tool_calls = arguments.max_tool_calls if arguments.max_tool_calls is not None else config.execution.get(
        'evaluation_max_tool_calls', KIMI_MAX_TOOL_CALLS if llm_config.get('agent_backend') == 'kimi' else 50)
    max_concurrency = arguments.max_concurrency if arguments.max_concurrency is not None else config.execution.get('evaluation_max_concurrency', 1)
    for name, value in (('evaluation_max_tool_calls', max_tool_calls), ('evaluation_max_concurrency', max_concurrency)):
        if type(value) is not int or value < 1:
            parser.error(f'{name} 必须是正整数')
    if llm_config.get('agent_backend') == 'kimi' and max_tool_calls > KIMI_MAX_TOOL_CALLS:
        parser.error(f'evaluation_max_tool_calls 不能超过 {KIMI_MAX_TOOL_CALLS}')
    if arguments.llm_timeout_seconds is not None:
        llm_config["timeout_seconds"] = arguments.llm_timeout_seconds
        if llm_config.get('agent_backend') == 'kimi':
            llm_config['kimi'] = {**llm_config.get('kimi', {}), 'timeout_seconds': arguments.llm_timeout_seconds}
    run_dir = run_evaluation(
        arguments.input_root,
        arguments.output_root,
        llm_config,
        config.execution,
        limit=arguments.limit,
        sample_size=arguments.sample_size,
        sample_seed=arguments.sample_seed,
        environment_id=arguments.environment_id,
        max_tool_calls=max_tool_calls,
        max_concurrency=max_concurrency,
    )
    print(run_dir)


if __name__ == "__main__":
    main()
