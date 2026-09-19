"""Kimi SDK adapter; environment execution stays in the existing MCP gateway."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

from .tool_graph.llm import _client
from .tool_result_reader import ResultReader
from .task_eval_mcp import bind_delivery


SYSTEM_PROMPT = '${base_prompt}'


def run_kimi_agent(
    prompt: str, workspace: Path, server_config: Path, trace: Path,
    llm_config: dict[str, Any],
) -> str:
    options = dict(llm_config.get("kimi", {}))
    unknown = options.keys() - {
        'sdk_path', 'node', 'provider_type', 'max_context_size', 'max_output_size',
        'timeout_seconds', 'max_steps_per_turn', 'max_attempts_per_step',
        'reserved_context_size', 'compaction_trigger_ratio', 'compaction_max_attempts',
        'system_prompt', 'tool_result_page_chars', 'binding_path', 'responses_stream',
    }
    if unknown:
        raise ValueError('未知 llm.kimi 配置：' + ', '.join(sorted(unknown)))
    if options.get("provider_type", "openai") not in {"openai", "openai_responses"}:
        raise ValueError("当前 Kimi 接入支持 OpenAI-compatible Chat Completions 和 Responses 后端")
    if type(options.get('responses_stream', True)) is not bool:
        raise ValueError('responses_stream 必须是布尔值')
    sdk_value = options.get("sdk_path") or os.environ.get("KIMI_CODE_SDK")
    if not sdk_value or not Path(sdk_value).expanduser().is_file():
        raise ValueError("设置 llm.kimi.sdk_path 或 KIMI_CODE_SDK，指向编译后的官方 SDK dist/index.mjs")
    client = _client({**llm_config, "backend": "api"})
    config = json.loads(server_config.read_text(encoding="utf-8"))
    if options.get('binding_path'):
        config['binding_path'] = str(Path(options['binding_path']).expanduser().resolve())
    config = bind_delivery(config)
    page_chars = options.get('tool_result_page_chars', 6000)
    reader = ResultReader(page_chars)  # Validate before starting the SDK.
    if any(tool['name'] == reader.name for tool in config['tools']):
        raise ValueError('read_tool_result 工具名称冲突')
    budget = config["max_tool_calls"]
    if type(budget) is not int or budget < 1:
        raise ValueError("max_tool_calls 必须是正整数")
    if type(options.get("max_context_size")) is not int or options["max_context_size"] < 1:
        raise ValueError("llm.kimi.max_context_size 必须明确配置为后端支持的上下文长度")
    timeout = options.get("timeout_seconds")
    if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("Kimi timeout_seconds 必须大于 0")
    integer_options = {}
    for name in ('max_output_size', 'reserved_context_size', 'compaction_max_attempts',
                 'max_steps_per_turn', 'max_attempts_per_step'):
        if name in options:
            integer_options[name] = options[name]
    for name, value in integer_options.items():
        minimum = 0 if name == 'reserved_context_size' else 1
        if type(value) is not int or value < minimum:
            raise ValueError(f'llm.kimi.{name} 必须是 >= {minimum} 的整数')
    if any(integer_options.get(name, 0) >= options['max_context_size']
           for name in ('max_output_size', 'reserved_context_size')):
        raise ValueError('max_output_size、reserved_context_size 必须小于 max_context_size')
    ratio = options.get('compaction_trigger_ratio')
    if 'compaction_trigger_ratio' in options and (type(ratio) not in (int, float) or not 0.5 <= ratio <= 0.99):
        raise ValueError('compaction_trigger_ratio 必须在 0.5 到 0.99 之间')
    temperature = llm_config.get('temperature', 0)
    if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
        raise ValueError('llm.temperature 必须在 0 到 2 之间')
    system_prompt = options.get('system_prompt', SYSTEM_PROMPT)
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError('system_prompt 必须是非空字符串')
    log_dir = workspace.parent / (workspace.name + ".agent")
    log_dir.mkdir(parents=True, exist_ok=True)
    # Each invocation has its own artifacts, including failed/retried attempts.
    if any(log_dir.iterdir()):
        log_dir = Path(tempfile.mkdtemp(prefix="kimi-attempt-", dir=log_dir))
    trace = trace.resolve()
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.touch()
    repository = Path(__file__).resolve().parents[1]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agent-world-kimi-") as temporary:
        root = Path(temporary)
        work_dir = root / "work"
        work_dir.mkdir()
        result_read_trace = log_dir / 'result_reads.jsonl'
        result_read_trace.touch()
        config.update(workspace=str(workspace.resolve()), trace=str(trace),
                      tool_result_page_chars=page_chars, result_read_trace=str(result_read_trace.resolve()))
        private_server = root / "server.json"
        private_server.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        profile = root / "agent.md"
        profile_text = (
            '---\nname: agent\noverride: true\ndescription: Environment task solver\n'
            'tools: ["mcp__agent_world_eval__*"]\ndisallowedTools: ["select_tools"]\n'
            'subagents: []\n---\n' + system_prompt.strip() + '\n'
        )
        profile.write_text(profile_text, encoding="utf-8")
        (log_dir / "agent.md").write_text(profile_text, encoding="utf-8")
        payload = {
            "sdk_path": str(Path(sdk_value).expanduser().resolve()),
            "home_dir": str(root / "home"), "work_dir": str(work_dir),
            "profile": str(profile), "log_dir": str(log_dir.resolve()), "prompt": prompt,
            "tool_count": len(config["tools"]) + 1,
            "model": client.model, "base_url": client.base_url, "api_key": client.api_key,
            "provider_type": options.get("provider_type", "openai"),
            "responses_stream": options.get("responses_stream", True),
            "max_context_size": options["max_context_size"],
            **({'max_output_size': integer_options['max_output_size']}
               if 'max_output_size' in integer_options else {}),
            "temperature": temperature,
            "loop_control": {
                **({'maxStepsPerTurn': integer_options['max_steps_per_turn']}
                   if 'max_steps_per_turn' in integer_options else {}),
                **({'maxAttemptsPerStep': integer_options['max_attempts_per_step']}
                   if 'max_attempts_per_step' in integer_options else {}),
                **({'reservedContextSize': integer_options['reserved_context_size']}
                   if 'reserved_context_size' in integer_options else {}),
                **({'compactionTriggerRatio': ratio} if ratio is not None else {}),
                **({'compactionMaxAttempts': integer_options['compaction_max_attempts']}
                   if 'compaction_max_attempts' in integer_options else {}),
            },
            "mcp": {"name": "agent_world_eval", "transport": "stdio", "command": sys.executable,
                    "args": [str(repository / "task_gen/task_eval_kimi_mcp.py"), str(private_server)],
                    "cwd": str(work_dir), "deferred": False,
                    "toolTimeoutMs": int(config.get("timeout", 300)) * 1000},
        }
        # Do not inherit another Kimi session's settings or model credentials.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("KIMI_", "OPENAI_", "ANTHROPIC_"))}
        env.pop("NODE_OPTIONS", None)
        process = subprocess.Popen(
            [options.get("node", "node"), str(Path(__file__).with_suffix(".mjs"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=work_dir, env=env, start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(json.dumps(payload), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()  # SDK cancels the turn and exports partial context first.
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
        finally:
            shutil.copyfile(trace, log_dir / "tool_calls.jsonl")
        for name, value in (("stdout.txt", stdout), ("stderr.txt", stderr)):
            if client.api_key:
                value = value.replace(client.api_key, "[REDACTED]")
            (log_dir / name).write_text(value, encoding="utf-8")
        (log_dir / "timing.json").write_text(json.dumps({
            "duration_seconds": time.monotonic() - started, "timed_out": timed_out,
            "returncode": process.returncode,
        }), encoding="utf-8")
        if timed_out:
            raise RuntimeError(f"Kimi 执行超时（{timeout}s）；日志：{log_dir}")
        result_path = log_dir / "result.json"
        if not result_path.is_file():
            raise RuntimeError(f"Kimi 未生成结果，退出码 {process.returncode}；日志：{log_dir}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if process.returncode != 0 or result.get("error") or result.get("reason") != "completed":
            raise RuntimeError(f"Kimi 执行失败：{result.get('error') or result.get('reason')}；日志：{log_dir}")
        answer = result.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError(f"Kimi 未提交最终答案；日志：{log_dir}")
        return answer.strip()
