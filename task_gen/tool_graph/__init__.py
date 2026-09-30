"""Tool Graph：从环境 JSON 生成任务 JSON 的流水线（从头设计版）。"""

def __getattr__(name):
    if name == 'run':
        from .pipeline import run

        return run
    raise AttributeError(name)

__all__ = ["run"]
