"""独立评测的 API ReAct 循环；模型只能通过公开的环境工具访问状态。

借鉴 OmniaBench/TaskGen-DAG/TaskSynthesis1.6_en_gpt/react_agent_direct.py
的 action/finish 协议和完整 observation 回传。执行复用本项目的工具沙箱，
不导入 OmniaBench 的动态环境、状态注入或答案评判。
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

from .task_eval_mcp import call_environment_tool
from .tool_graph.llm import capture_calls, infer, parse_json_object
from .tool_graph.run_io import append_llm_call
from .tool_graph.step_5_task_validate import _public_tool


SYSTEM_PROMPT = """Complete the supplied task using only the available environment tools.
You can see the task, public environment/tool descriptions, and results of your tool calls.
There is no shell, direct filesystem access, or access to hidden state or tool implementation.
Choose one action at a time, then use its Observation to decide what to do next.
Return one JSON object in either format:
{"action": {"name": "<available tool name>", "params": {}}}
{"finish": true, "final_answer": "<final user-facing answer>"}
Never include both action and finish. Parameters must be a JSON object.
An Observation is tool feedback, not a new task. Correct invalid calls using that feedback.
Finish when done, or explain what could not be completed and why. Do not claim success
without evidence. The final_answer is ordinary text; use the format the task requests.
"""


def run_react_agent(
    prompt: str,
    workspace: Path,
    server_config: Path,
    trace: Path,
    llm_config: dict[str, Any],
    *,
    resume_request: dict[str, Any] | None = None,
) -> str:
    """只向模型公开工具契约；留存每轮原始模型响应、usage 和工具调用。

    保留旧评测入口的字符串答案和 JSONL trace 接口。即使共享配置选择 codex，
    此执行器也固定使用 API，以免重新引入 CLI 的文件访问能力。
    """
    config = json.loads(server_config.read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in config["tools"]}
    budget = config["max_tool_calls"]
    if type(budget) is not int or budget < 1:
        raise ValueError("max_tool_calls 必须是正整数")
    api_config = {**llm_config, "backend": "api"}
    log_dir = workspace.parent / (workspace.name + ".agent")
    log_dir.mkdir(parents=True, exist_ok=True)
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.touch()
    system = SYSTEM_PROMPT + "\nAvailable tools:\n" + json.dumps(
        [_public_tool(tool) for tool in tools.values()], ensure_ascii=False,
    )
    history: list[dict[str, str]] = []
    message = prompt
    calls = 0
    if resume_request is not None:
        system = resume_request['system_prompt']
        history = list(resume_request['history'])
        message = resume_request['prompt']
        calls = len(trace.read_text().splitlines())
    # 为格式修正留出有界余量；工具预算耗尽后仍允许提交最终答案。
    max_rounds = budget * 2 + 10
    try:
        with capture_calls("task_eval.agent", lambda record: append_llm_call(log_dir, record)):
            for _ in range(max_rounds):
                response = infer(message, system_prompt=system, history=history, llm_config=api_config)
                history.extend([
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": response.text},
                ])
                try:
                    payload = parse_json_object(response.text)
                    if "action" in payload and "finish" in payload:
                        raise ValueError("action 与 finish 不能同时出现")
                    if payload.get("finish") is True:
                        answer = payload.get("final_answer")
                        if not isinstance(answer, str) or not answer.strip():
                            raise ValueError("final_answer 必须是非空字符串")
                        return answer
                    action = payload.get("action")
                    if not isinstance(action, dict):
                        raise ValueError("需要 action 对象或 finish=true")
                    name, arguments = action.get("name"), action.get("params")
                    if not isinstance(name, str) or not isinstance(arguments, dict):
                        raise ValueError("action.name 必须是字符串，params 必须是对象")
                except ValueError as error:
                    message = f"Response format error: {error}. Use the specified action/finish JSON format."
                    continue
                if calls >= budget:
                    message = "Tool call budget exhausted. Submit finish with your final answer and any limitations."
                    continue
                calls += 1
                record = call_environment_tool(
                    name, arguments, tools, workspace,
                    timeout=int(config.get("timeout", 300)),
                    memory_limit=int(config.get("memory_limit", 2 * 1024**3)),
                    write_limit=int(config.get("write_limit", 256 * 1024**2)),
                    environment=config.get("environment", {}),
                )
                with trace.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                observation = record["result"] if record["error"] is None else {
                    "error": record["error"], "tool_result": record["result"],
                }
                message = (
                    "Observation: " + json.dumps(observation, ensure_ascii=False)
                    + f"\nRemaining tool calls: {budget - calls}"
                )
        raise RuntimeError(f"ReAct Agent 在 {max_rounds} 轮内未提交最终答案")
    finally:
        shutil.copyfile(trace, log_dir / "tool_calls.jsonl")
