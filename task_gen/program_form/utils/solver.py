"""使用独立 Codex Agent 和临时 MCP 工具会话求解一条任务。"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from utils.search_agent.codex import CodexAgentClient

from .environment import load_frozen_package
from .io import read_json, write_json


_FENCE = re.compile(r"\A\s*```(?:json)?\s*|\s*```\s*\Z", re.IGNORECASE)


class _SolverClient(CodexAgentClient):
    def __init__(self, server: Path, config: Path, **options: Any):
        super().__init__(**options)
        self.server = server
        self.config = config

    def _llm_arguments(self, environment: dict[str, str]) -> list[str]:
        arguments = super()._llm_arguments(environment)
        arguments.extend([
            "--config", "mcp_servers={}",
            "--config", f"mcp_servers.agent_world_program.command={json.dumps(sys.executable)}",
            "--config", "mcp_servers.agent_world_program.args="
            + json.dumps([str(self.server), str(self.config)]),
        ])
        return arguments


@dataclass(frozen=True)
class SolverResult:
    answer: dict[str, Any] | None
    trace: list[dict[str, Any]]
    final_state: dict[str, Any] | None
    raw_response: str
    error: str | None


def _parse_answer(text: str) -> dict[str, Any]:
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("求解 Agent 没有返回 JSON object")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("求解 Agent 最终答案必须是 JSON object")
    return value


def solve_with_codex(
    *,
    step1_path: Path,
    task_text: str,
    output_schema: dict[str, Any],
    model: str,
    run_dir: Path,
    max_tool_calls: int = 100,
    timeout_seconds: int = 1800,
) -> SolverResult:
    package = load_frozen_package(step1_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "tool_calls.jsonl"
    state_path = run_dir / "final_state.json"
    config_path = run_dir / "mcp_config.json"
    write_json(config_path, {
        "step1_path": str(step1_path.resolve()),
        "trace_path": str(trace_path),
        "state_path": str(state_path),
        "max_tool_calls": max_tool_calls,
    })
    server = Path(__file__).with_name("solver_mcp.py").resolve()
    client = _SolverClient(
        server,
        config_path,
        model=model,
        timeout_seconds=timeout_seconds,
        sandbox="read-only",
        network_access=False,
        reasoning_effort="high",
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )
    prompt = json.dumps({
        "role": (
            "完成给定业务任务。只能通过本会话公开的 Agent-World MCP 工具了解和修改"
            "业务状态；不要猜测标识符。完成后只返回符合 output_schema 的 JSON object。"
        ),
        "task": task_text,
        "output_schema": output_schema,
        "environment": package.public_environment(),
    }, ensure_ascii=False)
    try:
        raw = client.run(prompt, working_directory=run_dir)
        answer = _parse_answer(raw)
        errors = list(Draft202012Validator(output_schema).iter_errors(answer))
        if errors:
            raise ValueError(f"Agent 答案不符合 output_schema：{errors[0].message}")
        trace = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ] if trace_path.is_file() else []
        final_state = read_json(state_path) if state_path.is_file() else None
        return SolverResult(answer, trace, final_state, raw, None)
    except Exception as error:
        trace = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ] if trace_path.is_file() else []
        final_state = read_json(state_path) if state_path.is_file() else None
        return SolverResult(None, trace, final_state, "", f"{type(error).__name__}: {error}")
