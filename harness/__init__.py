"""Stable execution interfaces shared by ToolGen, TaskGen, and distillation.

Imports are resolved lazily so low-level modules such as ``harness.resources``
do not initialize delivery/runtime adapters and create dependency cycles.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "DeliveryPackage",
    "KimiFileAccessPolicy",
    "TaskRunLayout",
    "ToolRuntime",
    "call_environment_tool",
    "build_kimi_file_hook_command",
    "create_task_run_layout",
    "expected_mcp_tool_names",
    "load_delivery",
]


_EXPORTS = {
    "DeliveryPackage": ("harness.delivery", "DeliveryPackage"),
    "KimiFileAccessPolicy": ("harness.kimi_file_policy", "KimiFileAccessPolicy"),
    "TaskRunLayout": ("harness.layout", "TaskRunLayout"),
    "ToolRuntime": ("harness.runtime", "ToolRuntime"),
    "call_environment_tool": ("harness.execution", "call_environment_tool"),
    "build_kimi_file_hook_command": (
        "harness.kimi_file_policy",
        "build_kimi_file_hook_command",
    ),
    "create_task_run_layout": ("harness.layout", "create_task_run_layout"),
    "expected_mcp_tool_names": ("harness.mcp_tools", "expected_mcp_tool_names"),
    "load_delivery": ("harness.delivery", "load_delivery"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
