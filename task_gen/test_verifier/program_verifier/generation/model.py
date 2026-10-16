"""Sandboxed Codex calls for design and individual implementation files."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import tomllib

from task_gen.task_eval_verifier import sandbox_command, _VerifierClient
from ..application.runner import _isolated_auth
from .pipeline import write_json


def snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'Symlink in generated workspace: {path}')
        # Explicit py_compile writes bytecode even with PYTHONDONTWRITEBYTECODE.
        if '__pycache__' in path.relative_to(root).parts and path.suffix == '.pyc':
            continue
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class GenerationModel:
    design_files = {'main.py', 'rubrics.json'}
    check_module = 'task_gen.test_verifier.program_verifier.generation.check'

    def __init__(self, evidence_dir: Path, config: dict, logs: Path):
        self.evidence_dir, self.config, self.logs = evidence_dir, config, logs
        self.counter = 0
        self.lock = threading.Lock()

    def __call__(self, prompt: str, *, stage: str, output_dir: Path) -> Path:
        with self.lock:
            self.counter += 1
            counter = self.counter
        call = self.logs / f'{counter:02d}_{stage.replace("/","_").replace(".py","")}'
        call.mkdir()
        (call/'evidence').mkdir()
        shutil.copyfile(self.evidence_dir/'request.json',call/'request.json')
        (call/'scratch').mkdir()
        workspace = call/'scratch/verifier'
        if stage == 'design':
            workspace.mkdir()
        else:
            shutil.copytree(output_dir/'verifier', workspace)
        before = snapshot(workspace)
        repository = Path(__file__).resolve().parents[4]
        check_command = [sys.executable, '-m', self.check_module,
                         '--workspace', str(workspace), '--evidence', str(self.evidence_dir),
                         '--stage', stage, '--self-test', str(call/'scratch/self_test.py')]
        checkpoint = call/'scratch/acceptance_ready.json'
        delivery_command = check_command + ['--completion-file', str(checkpoint)]
        prompt += (f'\n工作目录：{workspace}。在这里直接编辑交付文件，测试脚本放在 {call/"scratch/self_test.py"}。'
                   f'\n可用 Python：{sys.executable}。验收命令：{shlex.join(delivery_command)}'
                   '\n请实际运行该命令，阅读报错并在本次会话内修正，直到通过。最终回复简述文件和测试结果；不要在回复里返回代码JSON。'
                   '\n该命令成功即交付，宿主会立即结束本会话；请把必要复核放在运行验收之前，通过后不要再扩展工作。不要自行创建验收标记。'
                   '\n宿主会独立复跑验收。自测只检查本阶段可证明的性质；还未实现的依赖可按契约构造测试替身，不把替身结果当作真实任务得分。')
        (call/'prompt.txt').write_text(prompt)
        started=time.monotonic()
        cfg=dict(self.config.get('codex',{}))
        timeout = float(self.config.get('generation_call_timeout_seconds',cfg.get('timeout_seconds',900)))
        reserve = min(120, timeout/10)
        record={'stage':stage,'status':'running','method':'codex','model':cfg.get('model'),'usage':'unknown'}
        write_json(call/'call.json',record)
        try:
            with tempfile.TemporaryDirectory(prefix='verifier-generation-auth-') as temporary:
                auth=Path(temporary)
                environment=_isolated_auth(self.config,auth)
                if record['model'] is None and (auth/'config.toml').exists():
                    record['model']=tomllib.loads((auth/'config.toml').read_text()).get('model')
                executable=Path(shutil.which(cfg.get('executable','codex')) or '').resolve()
                if not executable.is_file(): raise ValueError('Codex CLI not found')
                runtimes=[repository,self.evidence_dir,Path(sys.prefix),Path(sys.base_prefix),executable.parents[3]]
                prefix=list(sandbox_command(call,auth,runtime_paths=runtimes))
                prefix += ['--setenv','PYTHONPATH',str(repository),'--setenv','PYTHONDONTWRITEBYTECODE','1']
                for k,v in environment.items(): prefix+=['--setenv',k,v]
                selected={k:v for k,v in cfg.items() if k in ['model','base_url','api_key','reasoning_effort']}
                client=_VerifierClient(**selected,codex_home=auth,executable=str(executable),
                    record_usage=cfg.get('record_usage',False),
                    timeout_seconds=max(1, int(timeout-reserve)),
                    bypass_approvals_and_sandbox=True,log_directory=call/'logs',command_prefix=tuple(prefix))
                text=client.run_until_files(prompt,working_directory=call,required_paths=(checkpoint,))
                (call/'response.txt').write_text(text)
                after = snapshot(workspace)
                if stage == 'design':
                    if set(after) != self.design_files:
                        raise ValueError(f'Designer must deliver only {sorted(self.design_files)} in verifier/')
                elif {k:v for k,v in after.items() if k!=stage} != {k:v for k,v in before.items() if k!=stage}:
                    raise ValueError('Implementation changed files outside its target')
                remaining = timeout-(time.monotonic()-started)
                if remaining <= 0:
                    raise TimeoutError('Generation budget exhausted before acceptance')
                # Recheck immutable delivery files; tests may write only to private scratch.
                replay = prefix + ['--ro-bind', str(workspace), str(workspace)] + check_command
                with (call/'acceptance.stdout.log').open('w') as stdout, (call/'acceptance.stderr.log').open('w') as stderr:
                    result = subprocess.run(replay,stdout=stdout,stderr=stderr,timeout=min(120,remaining))
                write_json(call/'acceptance.json',{'exit_code':result.returncode,'command':check_command})
                if result.returncode:
                    raise RuntimeError('Workspace acceptance/self-test failed: '+(call/'acceptance.stderr.log').read_text()[-3000:])
                if snapshot(workspace) != after:
                    raise ValueError('Self-test changed delivered code')
                record['status']='completed'
                record['workspace']=str(workspace)
                return workspace
        except Exception as e:
            record.update(status='error',error=f'{type(e).__name__}: {e}')
            raise
        finally:
            events=[]
            for p in (call/'logs').rglob('stdout.log'):
                for line in p.read_text(errors='replace').splitlines():
                    try: e=json.loads(line)
                    except ValueError: continue
                    if isinstance(e,dict) and 'usage' in e: events.append(e['usage'])
            if events: record['usage']=events
            record['seconds']=time.monotonic()-started
            write_json(call/'call.json',record)
