"""Adapt TaskGen calls to the upstream Harness without changing its contracts."""
import subprocess

from harness.execution import call_environment_tool as _execute
from harness.sandbox import call_tool as _sandbox_call


def call_environment_tool(name, arguments, tools, state_root, *, runtime=None,
                          binding_path=None, **options):
    try:
        return _execute(name, arguments, tools, state_root, runtime=runtime, **options)
    except subprocess.TimeoutExpired as error:
        return {'tool': name, 'arguments': arguments, 'result': None,
                'error': f'工具调用超过 {error.timeout} 秒', 'failure_kind': 'timeout'}
    except (ValueError, OSError) as error:
        # MCP/ReAct need an observation to correct the call; Harness raises.
        return {'tool': name, 'arguments': arguments, 'result': None,
                'error': str(error), 'failure_kind': 'invalid_arguments'}


def call_tool(code, arguments, state_root, timeout, memory_limit, write_limit,
              environment=None, *, runtime=None, binding_path=None, **options):
    return _sandbox_call(code, arguments, state_root, timeout, memory_limit,
                         write_limit, environment, runtime=runtime, **options)
