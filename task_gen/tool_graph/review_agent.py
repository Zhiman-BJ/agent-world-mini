"""Codex chain review with read-only access to a disposable initial-state copy."""

from datetime import datetime
from pathlib import Path
import shutil
import tempfile
import time

from .llm import CodexAgentClient, InferenceResult, _TRACE_CONTEXT, _record_call, _run_batch
from .step_3_chain_execute import _workspace_signature, _workspace_usage


def review_with_initial_state(prompts, *, llm_config, initial_workspace):
    if not prompts:
        return []
    source = Path(initial_workspace).resolve()
    if not source.is_dir():
        raise ValueError("review 初态 workspace 不存在")
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
                workspace = root / "workspace"
                shutil.copytree(source, workspace)
                before = _workspace_signature(workspace)
                client = CodexAgentClient(
                    model=llm_config.get("model"),
                    timeout_seconds=int(llm_config.get("timeout_seconds", 1800)),
                    sandbox="read-only",
                    log_directory=root / "logs",
                )
                try:
                    text = client.run(prompt, working_directory=workspace)
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
