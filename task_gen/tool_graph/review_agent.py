"""Codex chain review with read-only access to a disposable initial-state copy."""

from datetime import datetime
from pathlib import Path
import json
import shutil
import sys
import tempfile
import time

from .llm import CodexAgentClient, InferenceResult, _TRACE_CONTEXT, _record_call, _run_batch
from .step_3_chain_execute import _workspace_signature, _workspace_usage


class _ReviewClient(CodexAgentClient):
    def __init__(self, *, server_config, **options):
        super().__init__(**options)
        self.server_config = server_config

    def _llm_arguments(self, environment):
        arguments = super()._llm_arguments(environment)
        server = Path(__file__).resolve().parents[1] / 'task_eval_mcp.py'
        arguments.extend(['--config', 'features.shell_tool=false', '--config',
                          'features.unified_exec=false', '--config',
                          'features.view_image=false', '--config', 'mcp_servers={}', '--config',
                          'mcp_servers.environment.command=' + json.dumps(sys.executable),
                          '--config', 'mcp_servers.environment.default_tools_approval_mode="approve"',
                          '--config', 'mcp_servers.environment.args=' + json.dumps([str(server), str(self.server_config)])])
        return arguments


def review_with_initial_state(prompts, *, llm_config, initial_workspace, environment=None):
    if not prompts:
        return []
    source = Path(initial_workspace).resolve()
    if not source.is_dir():
        raise ValueError("review 初态 state 不存在")
    _, _, error = _workspace_usage(source)
    if error:
        raise ValueError(error)
    source_signature = _workspace_signature(source)
    concurrency = llm_config.get("max_concurrency", 1)
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("llm.max_concurrency 必须是正整数")
    trace = _TRACE_CONTEXT.get()

    def run(index, prompt):
        started_at = datetime.now().astimezone().isoformat()
        started = time.perf_counter()
        result, error, agent_log = None, None, {}
        try:
            with tempfile.TemporaryDirectory(prefix="tool-graph-review-") as temporary:
                root = Path(temporary)
                workspace = root / "state"
                shutil.copytree(source, workspace)
                before = _workspace_signature(workspace)
                working_directory = root / 'review'
                working_directory.mkdir()
                server_config = root / 'server.json'
                declaration = environment or {}
                readonly_environment = {**declaration,
                    'record_sets': [{**d, 'access': 'read_only'} for d in declaration.get('record_sets', [])],
                    'filesystem_scopes': [{**d, 'access': 'read_only'} for d in declaration.get('filesystem_scopes', [])]}
                server_config.write_text(json.dumps({
                    'environment': readonly_environment,
                    'tools': [t for t in declaration.get('tools', []) if not t.get('usageConditions', {}).get('sideEffects')],
                    'workspace': str(workspace), 'trace': str(root / 'tool_calls.jsonl'),
                    'max_tool_calls': 100, 'timeout': 300, 'memory_limit': 2 * 1024**3,
                    'write_limit': 256 * 1024**2,
                }, ensure_ascii=False), encoding='utf-8')
                client = _ReviewClient(
                    server_config=server_config,
                    model=llm_config.get("model"),
                    codex_home=llm_config.get("codex_home"),
                    timeout_seconds=int(llm_config.get("timeout_seconds", 1800)),
                    sandbox="read-only",
                    log_directory=root / "logs",
                )
                try:
                    text = client.run(prompt, working_directory=working_directory)
                    if _workspace_signature(workspace) != before:
                        raise ValueError("review 改变了初态副本，已丢弃结论")
                    if _workspace_signature(source) != source_signature:
                        raise ValueError("review 期间源初态被修改，已丢弃结论")
                    result = InferenceResult(text, {}, client.model or "codex-default")
                finally:
                    for name in ("stdout", "stderr"):
                        path = root / "logs" / "run_01" / f"{name}.log"
                        if path.is_file():
                            agent_log[name] = path.read_text(encoding="utf-8", errors="replace")
                    tool_trace = root / 'tool_calls.jsonl'
                    if tool_trace.is_file():
                        agent_log['tool_calls'] = [json.loads(line) for line in tool_trace.read_text(encoding='utf-8').splitlines()]
        except Exception as failure:
            error = failure
            raise
        finally:
            _record_call(
                trace, index=index, prompt=prompt, system_prompt=None, history=(),
                backend="codex-review", model=llm_config.get("model") or "codex-default",
                started_at=started_at, started=started, result=result, error=error,
                agent_log=agent_log,
            )
        return result

    return _run_batch(run, prompts, concurrency)
