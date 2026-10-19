"""Use the task's original Harness runtime against disposable evidence copies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import time
import uuid

DEFAULT_DELIVERY_ROOT = Path('/data1/agent_world/toolgen_environments/delivery')
REPOSITORY = Path(__file__).resolve().parents[3]


def _read(path):
    return json.loads(Path(path).read_text())


def _digest(tools):
    return hashlib.sha256(json.dumps(tools, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _inside(root, relative):
    path = (root/relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'path outside evidence/delivery root: {relative}')
    return path


def prepare_runtime(case, out):
    """Persist identities, not the source machine's absolute installation paths."""
    runtime = getattr(case, 'runtime', None) or {}
    if not runtime.get('binding_path'):
        return
    from harness.delivery import load_delivery
    package = load_delivery(Path(runtime['binding_path']))
    tools = _read(Path(out)/'evidence/tools.json')
    selected = Path(runtime.get('tools_path') or package.delivery_root/package.binding['tools_path'])
    selected_tools = _read(selected)
    if isinstance(selected_tools, dict):
        selected_tools = selected_tools['tools']
    if {t['name']:t['internal'] for t in tools} != {t['name']:t['internal'] for t in selected_tools}:
        raise ValueError('Selected original tool implementations differ from task evidence')
    # Task export trims schema descriptions; retain the original full tool contracts separately.
    (Path(out)/'evidence/runtime_tools.json').write_text(json.dumps(selected_tools, ensure_ascii=False)+'\n')
    metadata = {'binding':str(package.binding_path.relative_to(package.delivery_root)),
                'tools':str(selected.relative_to(package.delivery_root)),
                'tools_sha256':_digest(selected_tools), 'task_tools_sha256':_digest(tools),
                'backend':package.runtime['backend'],
                'profile_id':package.runtime.get('profile_id')}
    if package.software_root and (package.software_root/'requirements.txt').is_file():
        metadata['packages'] = (package.software_root/'requirements.txt').read_text().splitlines()
    (Path(out)/'evidence/runtime.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n')


def _delivery(evidence_dir, config=None):
    metadata = Path(evidence_dir)/'evidence/runtime.json'
    if not metadata.is_file():
        return None
    from harness.delivery import load_delivery
    spec = _read(metadata)
    root = Path((config or {}).get('delivery_root') or DEFAULT_DELIVERY_ROOT).expanduser().resolve()
    package = load_delivery(_inside(root, spec['binding']))
    if package.runtime['backend'] != spec['backend'] or package.runtime.get('profile_id') != spec.get('profile_id'):
        raise ValueError('Original runtime binding changed')
    tools = _read(Path(evidence_dir)/'evidence/tools.json')
    # Execute exactly the copied task tools, including repaired versions.
    if _digest(tools) != spec['task_tools_sha256'] or _digest(_read(Path(evidence_dir)/'evidence/runtime_tools.json')) != spec['tools_sha256']:
        raise ValueError('Evidence original tools changed')
    return package


def runtime_mounts(evidence_dir, config=None):
    mounts = [REPOSITORY]
    package = _delivery(evidence_dir, config)
    if package:
        mounts.append(package.delivery_root)
        if package.software_root:
            mounts.append(package.software_root)
        if package.python_path:
            mounts.append(package.python_path.resolve().parent.parent)
            venv = package.python_path.parent.parent
            cfg = venv/'pyvenv.cfg'
            if cfg.is_file():
                for line in cfg.read_text().splitlines():
                    key, sep, value = line.partition('=')
                    if sep and key.strip() == 'home':
                        mounts.append(Path(value.strip()).parent)
        if package.runtime['backend'] == 'docker':
            socket = Path('/var/run/docker.sock')
            if socket.exists():
                mounts.append(socket)
    return list(dict.fromkeys(mounts))


def prepare_runtime_view(working_dir, evidence_dir, config=None):
    working, evidence = Path(working_dir), Path(evidence_dir).resolve()
    working.mkdir(parents=True, exist_ok=True)
    alias = working/'evidence_bundle'
    if not alias.exists() and not alias.is_symlink():
        alias.symlink_to(evidence, target_is_directory=True)
    if alias.resolve() != evidence:
        raise ValueError('evidence_bundle alias points to a different task')
    # Only installation routing/limits are persisted here; never API credentials.
    routing = {k:v for k,v in (config or {}).items() if k in (
        'delivery_root', 'environment_tool_timeout_seconds', 'environment_tool_memory_bytes',
        'environment_tool_write_bytes')}
    (working/'runtime_config.json').write_text(json.dumps(routing)+'\n')
    launcher = working/'environment_tool'
    launcher.write_text('#!/bin/sh\nexport PYTHONPATH='+shlex.quote(str(REPOSITORY))+'\nexec '+shlex.quote(sys.executable)+
                        ' -m task_gen.test_verifier.program_verifier.environment_runtime "$@"\n')
    launcher.chmod(0o700)


def runtime_instructions(evidence_dir, scratch_dir, config=None):
    prepare_runtime_view(scratch_dir, evidence_dir, config)
    metadata = Path(evidence_dir)/'evidence/runtime.json'
    binding = _read(metadata) if metadata.is_file() else {'backend':'host_python'}
    return ('\n原任务工具清单位于 evidence_bundle/evidence/tools.json；有原绑定时完整原工具契约位于 evidence_bundle/evidence/runtime_tools.json。原运行绑定：'+json.dumps(binding, ensure_ascii=False)+
            '\n先检查原工具及软件能力，适合则复用，不能因当前 Python 缺包就向公共环境补包。'
            '\n在本工作目录运行 ./environment_tool --evidence evidence_bundle --scratch . --config runtime_config.json '
            '--tool TOOL_NAME --arguments \'{"key":"value"}\' --state actual。state 可为 initial/actual/reference。'
            '\n也可用 --code helpers/check.py --arguments \'{}\' 在原软件解释器中运行该文件的 run(arguments, context) 函数。'
            'context 是原工具的文件/记录访问接口。JSON 输入输出，不拼接两套 Python 的包目录。'
            '\n原工具仅操作 scratch 中的独立证据副本；返回 success=false 是检查发现，需按 rubric 判断，不能一律视为基础设施错误。'
            '工具成功也不代表任务完成。工具执行异常必须报告，不能改为假通过。'
            '\n返回证据路径使用 evidence/...（相对 evidence_bundle）或 scratch/...（相对当前 scratch）；不要输出宿主绝对路径。')


def load_fallback(evidence_dir):
    root = Path(evidence_dir).resolve()
    data = _read(root/'fallback_input.json')
    for key in ('initial_state', 'actual_state', 'reference_state', 'reference_initial_state'):
        if data.get(key) is not None:
            value = Path(data[key])
            # Legacy bundles used absolute source-state paths; new bundles are portable.
            data[key] = value if value.is_absolute() else _inside(root, value)
    return data


def _execute(evidence_dir, scratch_dir, arguments, state, config, *, name=None, code=None):
    evidence, scratch = Path(evidence_dir).resolve(), Path(scratch_dir).resolve()
    config = config or {}
    request = _read(evidence/'program_request.json')
    if state not in ('initial', 'actual', 'reference'):
        raise ValueError('state must be initial, actual or reference')
    relative = request['initial_state'] if state == 'initial' else request[state]['final_state']
    source = _inside(evidence, relative)
    package = _delivery(evidence, config)
    options = {'timeout':int(config.get('environment_tool_timeout_seconds',120)),
               'memory_limit':int(config.get('environment_tool_memory_bytes',2*1024**3)),
               'write_limit':int(config.get('environment_tool_write_bytes',64*1024**2)),
               'environment':_read(evidence/request['environment']) if 'environment' in request else _read(evidence/'evidence/environment.json')}
    if package:
        options['runtime'] = package.runtime
        if package.python_path:
            options['software'] = {'python':str(package.python_path), 'root':str(package.software_root)}
            options['software_root'] = package.software_root
    scratch.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    row = {'kind':'environment_tool', 'tool':name or 'generated_helper', 'state':state, 'status':'running'}
    try:
        with tempfile.TemporaryDirectory(prefix='environment-state-', dir=scratch) as temporary:
            copied = Path(temporary)/'state'
            shutil.copytree(source, copied)  # Normal copies: writable tools must not mutate hardlinked evidence.
            if code is None:
                from harness.execution import call_environment_tool as call
                tools_file = evidence/('evidence/runtime_tools.json' if package else 'evidence/tools.json')
                tools = {t['name']:t for t in _read(tools_file)}
                record = call(name, arguments, tools, copied, **options)
                if record.get('failure_kind') or not isinstance(record.get('result'), dict):
                    raise RuntimeError(record.get('error') or 'Original tool execution failed')
                if record.get('error') and record['result'].get('success') is not False:
                    raise RuntimeError(record['error'])
            else:
                from harness.sandbox import call_tool
                record = call_tool(code, arguments, copied, **options)
                if record.get('kind') or record.get('error') or not isinstance(record.get('result'), dict):
                    raise RuntimeError(record.get('error') or 'Original runtime helper failed')
            value = record['result']
            row.update(status='completed', result=value)
            return value
    except Exception as error:
        row.update(status='error', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        row['seconds'] = time.monotonic()-started
        (scratch/f'environment_call_{uuid.uuid4().hex}.json').write_text(json.dumps(row, ensure_ascii=False)+'\n')


def call_environment_tool(evidence_dir, scratch_dir, name, arguments, *, state='actual', config=None):
    return _execute(evidence_dir, scratch_dir, arguments, state, config, name=name)


def run_environment_code(evidence_dir, scratch_dir, code, arguments, *, state='actual', config=None):
    return _execute(evidence_dir, scratch_dir, arguments, state, config, code=code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--tool')
    choice.add_argument('--code', type=Path)
    parser.add_argument('--arguments', default='{}')
    parser.add_argument('--state', choices=('initial','actual','reference'), default='actual')
    args = parser.parse_args()
    config = _read(args.config) if args.config else {}
    arguments = json.loads(args.arguments)
    if not isinstance(arguments, dict):
        parser.error('--arguments must be a JSON object')
    value = (_execute(args.evidence, args.scratch, arguments, args.state, config,
                      code=args.code.read_text()) if args.code else
             call_environment_tool(args.evidence, args.scratch, args.tool, arguments, state=args.state, config=config))
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
