"""Adapt TaskGen calls to the upstream Harness without changing its contracts."""
from functools import partial
from pathlib import Path
from types import SimpleNamespace
import subprocess

from harness.execution import call_environment_tool as _execute
from harness.sandbox import call_tool as _sandbox_call


def _docker_callback(runtime, binding_path):
    if not binding_path:
        raise ValueError('Docker 工具执行需要 binding_path')
    from task_gen.program.utils.tool_runtime import _docker_call_tool
    package = SimpleNamespace(delivery={'binding_path': str(binding_path)}, runtime=runtime)
    return partial(_docker_call_tool, package)


def call_environment_tool(name, arguments, tools, state_root, *, runtime=None,
                          binding_path=None, **options):
    try:
        if runtime and runtime.get('backend') == 'docker':
            if options.get('call_tool_fn') is not None:
                raise ValueError('Docker 后端不能同时指定另一工具执行器')
            options['call_tool_fn'] = _docker_callback(runtime, binding_path)
            options.pop('software', None)
            options['software_root'] = Path(runtime['software_root'])
        return _execute(name, arguments, tools, state_root, **options)
    except subprocess.TimeoutExpired as error:
        return {'tool': name, 'arguments': arguments, 'result': None,
                'error': f'工具调用超过 {error.timeout} 秒', 'failure_kind': 'timeout'}
    except (ValueError, OSError) as error:
        # MCP/ReAct need an observation to correct the call; Harness raises.
        return {'tool': name, 'arguments': arguments, 'result': None,
                'error': str(error), 'failure_kind': 'invalid_arguments'}


def call_tool(code, arguments, state_root, timeout, memory_limit, write_limit,
              environment=None, *, runtime=None, binding_path=None, **options):
    if runtime and runtime.get('backend') == 'docker':
        return _docker_callback(runtime, binding_path)(
            code, arguments, state_root, timeout, memory_limit, write_limit,
            environment, software_root=Path(runtime['software_root']),
        )
    return _sandbox_call(code, arguments, state_root, timeout, memory_limit,
                         write_limit, environment, **options)
