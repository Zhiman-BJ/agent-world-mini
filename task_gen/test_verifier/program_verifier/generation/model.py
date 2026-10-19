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
from ..environment_runtime import runtime_mounts, runtime_instructions
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
    optional_design_files = {'README.md'}
    check_module = 'task_gen.test_verifier.program_verifier.generation.check'
    isolate_experiments = False

    def isolate_prefix(self, prefix, repository, call):
        if not self.isolate_experiments:
            return prefix
        source = repository/'task_gen/test_verifier'
        hidden = [source/'runs', source/'manual_s59'] + list(source.glob('*/runs'))
        for path in hidden:
            if path.is_dir():
                prefix += ['--tmpfs', str(path)]
        # Reopen only this task and this call after hiding all experiment packages.
        prefix += ['--ro-bind', str(self.evidence_dir), str(self.evidence_dir),
                   '--bind', str(call), str(call)]
        for path in (call/'evidence', call/'request.json', call/'prompt.txt'):
            prefix += ['--ro-bind', str(path), str(path)]
        return prefix

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
        prompt += runtime_instructions(self.evidence_dir, call, self.config)
        repository = Path(__file__).resolve().parents[4]
        check_command = [sys.executable, '-m', self.check_module,
                         '--workspace', str(workspace), '--evidence', str(self.evidence_dir),
                         '--stage', stage, '--self-test', str(call/'scratch/self_test.py')]
        if stage == 'final':
            check_command += ['--method', self.final_method, '--config', str(call/'final_config.json')]
        checkpoint = call/'scratch/acceptance_ready.json'
        delivery_command = check_command + ['--completion-file', str(checkpoint)]
        prompt += (f'\n工作目录：{workspace}。在这里直接编辑交付文件，测试脚本放在 {call/"scratch/self_test.py"}。'
                   f'\n可用 Python：{sys.executable}。验收命令：{shlex.join(delivery_command)}'
                   '\n请实际运行该命令，阅读报错并在本次会话内修正，直到通过。最终回复简述文件和测试结果；不要在回复里返回代码JSON。'
                   '\n该命令成功表示准备好宿主复验，本会话会结束。交付以宿主复验成功为准；若失败将把诊断交回同一工作目录继续修复，共享原定预算。不要自行创建验收标记。'
                   '\n宿主会独立复跑验收。自测只检查本阶段可证明的性质；还未实现的依赖可按契约构造测试替身，不把替身结果当作真实任务得分。')
        (call/'prompt.txt').write_text(prompt)
        started=time.monotonic()
        cfg=dict(self.config.get('codex',{}))
        timeout = float(self.config.get('generation_call_timeout_seconds',cfg.get('timeout_seconds',900)))
        reserve = min(120, timeout/10)
        if stage == 'final':
            native_timeout = float(self.config.get('program_timeout_seconds' if self.final_method == 'program'
                                                  else 'application_timeout_seconds', cfg.get('timeout_seconds',900)))
            timeout = float(self.config.get('final_review_timeout_seconds', max(3600, 2*native_timeout+600)))
            reserve = native_timeout+60
            if timeout <= reserve:
                raise ValueError('final_review_timeout_seconds must leave time for repair and host native test')
        record={'stage':stage,'status':'running','method':'codex','model':cfg.get('model'),'usage':'unknown',
                'workspace':str(workspace),'attempts':[]}
        if stage == 'final':
            files = [repository/'task_gen/test_verifier/final_review.py', Path(__file__),
                     repository/'task_gen/test_verifier/program_verifier/application/runner.py',
                     repository/'task_gen/test_verifier/program_verifier/application/sdk.py',
                     repository/'task_gen/test_verifier/codex_tool_verifier/application.py']
            record['host_files'] = {str(p.relative_to(repository)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
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
                runtimes += runtime_mounts(self.evidence_dir, self.config)
                runtimes += [Path(p) for p in self.config.get('runtime_paths', [])]
                key_file = self.config.get('llm',{}).get('api_key_file')
                if key_file:
                    runtimes.append(Path(key_file).expanduser().resolve())
                prefix=list(sandbox_command(call,auth,runtime_paths=runtimes))
                prefix=self.isolate_prefix(prefix,repository,call)
                if stage == 'final':
                    # Earlier failed workspaces/logs remain readable outside repository mounts.
                    prefix += ['--ro-bind', str(self.logs), str(self.logs),
                               '--bind', str(call), str(call)]
                    for path in (call/'evidence', call/'request.json', call/'prompt.txt'):
                        prefix += ['--ro-bind', str(path), str(path)]
                prefix += ['--setenv','PYTHONPATH',str(repository),'--setenv','PYTHONDONTWRITEBYTECODE','1']
                for k,v in environment.items(): prefix+=['--setenv',k,v]
                if stage == 'final':
                    native_config = dict(self.config, codex_home=str(auth))
                    native_config['codex'] = dict(cfg, codex_home=str(auth))
                    write_json(call/'final_config.json', native_config)
                selected={k:v for k,v in cfg.items() if k in ['model','base_url','api_key','reasoning_effort']}
                repair_prompt = prompt
                while True:
                    remaining = timeout-(time.monotonic()-started)
                    if remaining <= reserve:
                        last_error = record['attempts'][-1].get('error', '') if record['attempts'] else ''
                        raise TimeoutError('Generation budget exhausted before repair/acceptance: '+last_error)
                    attempt = len(record['attempts'])+1
                    attempt_dir = call/f'acceptance_{attempt:02d}'
                    attempt_dir.mkdir()
                    checkpoint.unlink(missing_ok=True)
                    attempt_started = time.monotonic()
                    attempt_before = snapshot(workspace)
                    row = {'attempt':attempt, 'status':'running'}
                    record['attempts'].append(row)
                    (attempt_dir/'prompt.txt').write_text(repair_prompt)
                    client=_VerifierClient(**selected,codex_home=auth,executable=str(executable),
                        record_usage=cfg.get('record_usage',False),
                        timeout_seconds=max(1, int(remaining-reserve)),
                        bypass_approvals_and_sandbox=True,log_directory=call/'logs'/f'attempt_{attempt:02d}',
                        command_prefix=tuple(prefix))
                    host_check = False
                    try:
                        text=client.run_until_files(repair_prompt,working_directory=call,required_paths=(checkpoint,))
                        (call/'response.txt').write_text(text)
                        (attempt_dir/'response.txt').write_text(text)
                        after = snapshot(workspace)
                        if stage == 'design':
                            if not self.design_files <= set(after) <= self.design_files | self.optional_design_files:
                                raise ValueError(f'Designer must deliver {sorted(self.design_files)} and optional {sorted(self.optional_design_files)} in verifier/')
                        elif stage != 'final' and {k:v for k,v in after.items() if k!=stage} != {k:v for k,v in before.items() if k!=stage}:
                            raise ValueError('Implementation changed files outside its target')
                        remaining = timeout-(time.monotonic()-started)
                        if remaining <= 0:
                            raise TimeoutError('Generation budget exhausted before acceptance')
                        if stage == 'final':
                            write_json(call/'final_config.json', native_config)
                        host_check = True
                        # Recheck immutable delivery files; tests may write only to private scratch.
                        replay = prefix + ['--ro-bind', str(workspace), str(workspace)] + check_command
                        with (attempt_dir/'stdout.log').open('w') as stdout, (attempt_dir/'stderr.log').open('w') as stderr:
                            result = subprocess.run(replay,stdout=stdout,stderr=stderr,
                                                    timeout=remaining if stage == 'final' else min(120,remaining))
                        for source, target in [('stdout.log','acceptance.stdout.log'),('stderr.log','acceptance.stderr.log')]:
                            shutil.copyfile(attempt_dir/source, call/target)
                        acceptance = {'exit_code':result.returncode,'command':check_command,'attempt':attempt}
                        write_json(attempt_dir/'result.json', acceptance)
                        write_json(call/'acceptance.json', acceptance)
                        if result.returncode:
                            raise RuntimeError('Workspace acceptance/self-test failed: '+(attempt_dir/'stderr.log').read_text()[-6000:])
                        if snapshot(workspace) != after:
                            raise ValueError('Self-test changed delivered code')
                        row['status']='completed'
                        record['status']='completed'
                        return workspace
                    except Exception as error:
                        row.update(status='error',error=f'{type(error).__name__}: {error}')
                        checkpoint.unlink(missing_ok=True)
                        if not host_check:
                            raise
                        repair_prompt = (prompt+'\n宿主独立复验失败。继续修复当前同一 verifier 工作目录；不要重抽任务或改评分标准。'
                            +'\n本轮诊断：'+row['error']+'\n完整 stdout/stderr 与真实 native_runs 结果：'
                            +str(attempt_dir)+'，'+str(call/'acceptance.stderr.log')+'，'+str(call/'scratch/native_runs'))
                    finally:
                        attempt_after = snapshot(workspace)
                        row['changed_files'] = sorted(k for k in attempt_before.keys() | attempt_after.keys()
                                                      if attempt_before.get(k) != attempt_after.get(k))
                        row['seconds'] = time.monotonic()-attempt_started
                        write_json(call/'call.json',record)
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
