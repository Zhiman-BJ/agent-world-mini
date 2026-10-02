"""Tool Graph：从环境 JSON 生成任务 JSON 的流水线（从头设计版）。"""

__all__ = ["run"]


def run(*args, **kwargs):
    """Load the model-facing pipeline only when callers actually run it.

    Runtime-only imports such as ``task_gen.tool_graph.state_runtime`` execute
    inside delivery containers and must not pull the LLM client dependency into
    the tool sandbox.
    """
    from .pipeline import run as run_pipeline

    return run_pipeline(*args, **kwargs)
