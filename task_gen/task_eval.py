"""Run generated tasks with an agent, then compare its answer with the reference."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable

from utils.search_agent.codex import CodexAgentClient

from .tool_graph.llm import InferenceResult, infer, parse_json_object
from .tool_graph.run_io import load_config
from .tool_graph.step_3_chain_execute import (
    _bounded_calls,
    _public_environment,
    _tools,
    _workspace_signature,
)


DEFAULT_INPUT_ROOT = Path(__file__).resolve().parents[1] / "runs/taskgen"
InferFn = Callable[..., InferenceResult]
AgentRunFn = Callable[[str, Path, Path, Path], str]


class _TaskEvalCodexClient(CodexAgentClient):
    """Add this evaluation's temporary MCP server to one Codex invocation."""

    def __init__(self, server: Path, server_config: Path, **options: Any):
        super().__init__(**options)
        self.server = server.resolve()
        self.server_config = server_config.resolve()

    def _llm_arguments(self, environment: dict[str, str]) -> list[str]:
        arguments = super()._llm_arguments(environment)
        arguments.extend([
            "--config",
            "mcp_servers={}",
            "--config",
            f"mcp_servers.agent_world_eval.command={json.dumps(sys.executable)}",
            "--config",
            "mcp_servers.agent_world_eval.args="
            + json.dumps([str(self.server), str(self.server_config)]),
        ])
        return arguments


@dataclass(frozen=True)
class EvalCase:
    source_run: Path
    task: dict[str, Any]
    environment: dict[str, Any]
    initial_state: Path


def load_cases(input_root: Path) -> list[EvalCase]:
    """Read tasks from the latest complete run for each environment."""
    latest: dict[str, tuple[Path, Path, dict[str, Any]]] = {}
    for tasks_path in sorted(input_root.expanduser().resolve().glob("*/tasks.json")):
        source_run = tasks_path.parent
        bundle_path = source_run / "intermediate/step_5_bundle.json"
        if not bundle_path.is_file():
            continue
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        environment = bundle.get("environment")
        if not isinstance(environment, dict):
            raise ValueError(f"无效的正式任务 run：{source_run}")
        environment_id = environment.get("environment_id")
        if not isinstance(environment_id, str) or not environment_id:
            raise ValueError(f"环境缺少 environment_id：{source_run}")
        previous = latest.get(environment_id)
        if previous is None or source_run.name > previous[0].name:
            latest[environment_id] = (source_run, tasks_path, environment)

    cases: list[EvalCase] = []
    for environment_id, (source_run, tasks_path, environment) in sorted(latest.items()):
        tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
        if not isinstance(tasks, list):
            raise ValueError(f"无效的正式任务 run：{source_run}")
        for task in tasks:
            if not isinstance(task, dict) or not isinstance(task.get("initial_state"), str):
                raise ValueError(f"无效任务：{source_run}")
            task_id = task.get("task_id")
            if not isinstance(task_id, str) or not task_id or "/" in task_id or task_id in {".", ".."}:
                raise ValueError(f"无效 task_id：{task_id}")
            relative = Path(task["initial_state"])
            initial_state = (source_run / relative).resolve()
            if relative.is_absolute() or not initial_state.is_relative_to(source_run.resolve()):
                raise ValueError(f"初态路径越界：{relative}")
            if not initial_state.is_dir():
                raise ValueError(f"任务初态不存在：{initial_state}")
            if any(path.is_symlink() for path in [initial_state, *initial_state.rglob("*")]):
                raise ValueError(f"初态路径不得包含符号链接：{initial_state}")
            if task.get("environment_id") != environment_id:
                raise ValueError(f"任务 environment_id 与环境不一致：{source_run}")
            cases.append(EvalCase(source_run, task, environment, initial_state))
    return cases


def evaluate_case(
    case: EvalCase,
    workspace: Path,
    llm_config: dict[str, Any],
    *,
    max_tool_calls: int = 20,
    tool_timeout_seconds: int = 300,
    tool_max_memory_bytes: int = 2 * 1024 * 1024 * 1024,
    tool_max_write_bytes: int = 256 * 1024 * 1024,
    tool_result_max_bytes: int = 65536,
    agent_run_fn: AgentRunFn | None = None,
    judge_infer_fn: InferFn = infer,
) -> dict[str, Any]:
    """Let one Codex agent solve a task with environment MCP tools, then judge it."""
    if max_tool_calls < 1:
        raise ValueError("max_tool_calls 必须大于 0")
    workspace = workspace.expanduser().resolve()
    if workspace.exists():
        raise ValueError(f"评测 workspace 已存在：{workspace}")
    source_signature = _workspace_signature(case.initial_state)
    shutil.copytree(case.initial_state, workspace)
    tools = _tools(case.environment)
    expected_tools = [
        {key: tool[key] for key in ("name", "description", "inputSchema", "outputSchema")}
        for tool in tools.values()
    ]
    if case.task.get("available_tools") != expected_tools:
        raise ValueError("task.available_tools 与环境公开工具契约不一致")
    with tempfile.TemporaryDirectory(prefix="task-eval-mcp-") as temporary:
        server_config = Path(temporary) / "server.json"
        trace = Path(temporary) / "calls.jsonl"
        server_config.write_text(json.dumps({
            "workspace": str(workspace),
            "trace": str(trace),
            "max_tool_calls": max_tool_calls,
            "timeout": tool_timeout_seconds,
            "memory_limit": tool_max_memory_bytes,
            "write_limit": tool_max_write_bytes,
            "tools": list(tools.values()),
        }, ensure_ascii=False), encoding="utf-8")
        run_agent = agent_run_fn or (lambda prompt, cwd, config, call_trace: _run_agent(
            prompt, cwd, config, call_trace, llm_config,
        ))
        answer = run_agent(_agent_prompt(case), workspace, server_config, trace).strip()
        if not answer:
            raise ValueError("Codex Agent 未提交最终答案")
        calls = _read_trace(trace)

    if _workspace_signature(case.initial_state) != source_signature:
        raise ValueError("来源初态在评测期间被修改")
    changes = _workspace_changes(source_signature, _workspace_signature(workspace))
    judge_response = judge_infer_fn(
        _judge_prompt(case.task, case.environment, answer, calls, changes, tool_result_max_bytes),
        llm_config=llm_config,
    ).text
    evaluation = parse_json_object(judge_response)
    _validate_evaluation(evaluation)
    return {
        "source_run": case.source_run.name,
        "task_id": case.task.get("task_id"),
        "environment_id": case.task.get("environment_id"),
        "task_text": case.task.get("task_text"),
        "workspace": str(workspace),
        "agent_response": answer,
        "tool_calls": calls,
        "workspace_changes": changes,
        "agent_answer": answer,
        "reference_answer": case.task.get("reference", {}).get("answer"),
        "judge_response": judge_response,
        "evaluation": evaluation,
        "error": None,
    }


def _agent_prompt(case: EvalCase) -> str:
    return json.dumps({
        "role": (
            "You are the agent responsible for completing this task in the provided task workspace. "
            "Use the environment MCP tools exposed in this session to inspect and change environment state. "
            "You may inspect workspace files directly when useful, but all business state changes must go through the "
            "environment MCP tools so they are auditable. Never guess internal identifiers; discover them from list, "
            "search, read, or workspace evidence and use business errors to correct invalid calls. Do not stop at a "
            "plan. Complete the task, verify the result with the environment tools when useful, then return only the "
            "final user-facing answer."
        ),
        "task": case.task.get("task_text"),
        "environment": _public_environment(case.environment),
    }, ensure_ascii=False)


def _run_agent(
    prompt: str,
    workspace: Path,
    server_config: Path,
    _trace: Path,
    llm_config: dict[str, Any],
) -> str:
    server = Path(__file__).with_name("task_eval_mcp.py")
    client = _TaskEvalCodexClient(
        server,
        server_config,
        model=str(llm_config["model"]) if llm_config.get("model") else None,
        sandbox="read-only",
        network_access=False,
    )
    return client.run(prompt, working_directory=workspace)


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _judge_prompt(
    task: dict[str, Any],
    environment: dict[str, Any],
    answer: str,
    calls: list[dict[str, Any]],
    changes: list[dict[str, str]],
    result_limit: int,
) -> str:
    return json.dumps({
        "role": "你是严格的任务结果评审。比较实际回答与参考答案的语义，不要求措辞相同。",
        "criteria": [
            "实际回答是否正确、完整地回应任务目标",
            "实际回答中的事实是否得到真实工具结果支持",
            "实际工具调用是否完成任务要求的状态变化或交付物",
            "用 environment_resources 将资源 ID 映射到路径并核对 workspace_changes：should_modify 必须变化，"
            "must_not_modify 必须不变，can_modify 可以变化，三个列表之外的资源默认必须不变",
            "参考答案是核对依据，不是必须逐字匹配的唯一表述",
        ],
        "task": task.get("task_text"),
        "resource_constraints": task.get("resource_constraints"),
        "environment_resources": environment.get("resources"),
        "workspace_changes": changes,
        "actual_tool_calls": _bounded_calls(calls, result_limit),
        "actual_answer": answer,
        "reference_answer": task.get("reference", {}).get("answer"),
        "response_contract": {
            "passed": "boolean",
            "score": "0 到 100 的整数",
            "analysis": "具体说明一致之处、遗漏或错误",
        },
    }, ensure_ascii=False)


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


def _validate_evaluation(value: dict[str, Any]) -> None:
    if set(value) != {"passed", "score", "analysis"}:
        raise ValueError("评审结果必须只包含 passed、score、analysis")
    if not isinstance(value["passed"], bool):
        raise ValueError("passed 必须是 boolean")
    if type(value["score"]) is not int or not 0 <= value["score"] <= 100:
        raise ValueError("score 必须是 0 到 100 的整数")
    if not isinstance(value["analysis"], str) or not value["analysis"].strip():
        raise ValueError("analysis 必须是非空字符串")


def run_evaluation(
    input_root: Path,
    output_root: Path,
    llm_config: dict[str, Any],
    execution_config: dict[str, Any],
    *,
    limit: int | None = None,
    environment_id: str | None = None,
    max_tool_calls: int = 20,
    max_concurrency: int = 1,
) -> Path:
    cases = load_cases(input_root)
    if environment_id:
        cases = [case for case in cases if case.task.get("environment_id") == environment_id]
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        raise ValueError("没有找到可评测任务")
    run_dir = output_root.expanduser().resolve() / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True)

    def run(case: EvalCase) -> dict[str, Any]:
        workspace = run_dir / "workspaces" / f"{case.source_run.name}__{case.task['task_id']}"
        try:
            return evaluate_case(
                case,
                workspace,
                llm_config,
                max_tool_calls=max_tool_calls,
                tool_timeout_seconds=int(execution_config.get("tool_timeout_seconds", 300)),
                tool_max_memory_bytes=int(execution_config.get("tool_max_memory_bytes", 2 * 1024 * 1024 * 1024)),
                tool_max_write_bytes=int(execution_config.get("tool_max_write_bytes", 256 * 1024 * 1024)),
                tool_result_max_bytes=int(execution_config.get("tool_result_max_bytes", 65536)),
            )
        except Exception as error:
            return {
                "source_run": case.source_run.name,
                "task_id": case.task.get("task_id"),
                "environment_id": case.task.get("environment_id"),
                "task_text": case.task.get("task_text"),
                "error": f"{type(error).__name__}: {error}",
            }

    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        results = list(executor.map(run, cases))
    passed = sum(item.get("evaluation", {}).get("passed") is True for item in results)
    payload = {
        "input_root": str(input_root.expanduser().resolve()),
        "model": llm_config.get("model"),
        "task_count": len(results),
        "passed_count": passed,
        "failed_count": len(results) - passed,
        "results": results,
    }
    (run_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="让模型真实执行已生成任务并与参考答案核对")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output-root", type=Path, default=Path("runs/task_eval"))
    parser.add_argument("--config", type=Path, default=Path("config/tool_graph.yaml"))
    parser.add_argument("--model")
    parser.add_argument("--backend")
    parser.add_argument("--environment-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-tool-calls", type=int, default=20)
    parser.add_argument("--max-concurrency", type=int, default=1)
    arguments = parser.parse_args()
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit 必须大于 0")
    if arguments.max_tool_calls < 1:
        parser.error("--max-tool-calls 必须大于 0")
    if arguments.max_concurrency < 1:
        parser.error("--max-concurrency 必须大于 0")
    config = load_config(arguments.config, {"model": arguments.model, "backend": arguments.backend})
    run_dir = run_evaluation(
        arguments.input_root,
        arguments.output_root,
        config.llm,
        config.execution,
        limit=arguments.limit,
        environment_id=arguments.environment_id,
        max_tool_calls=arguments.max_tool_calls,
        max_concurrency=arguments.max_concurrency,
    )
    print(run_dir)


if __name__ == "__main__":
    main()
