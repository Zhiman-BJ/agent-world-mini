"""Run generated verifiers in a read-only sandbox, or fall back for the whole task."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib

from task_gen.task_eval_verifier import sandbox_command, verify_execution
from ..contracts import RubricVerificationError, aggregate_results, validate_design, validate_evidence
from .sdk import SDK, make_context


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _isolated_auth(config, target):
    """Adapted from task_eval_verifier: retain provider/auth, never plugins/hooks."""
    home = Path(config.get('codex', {}).get('codex_home') or config.get('codex_home') or Path.home()/'.codex').expanduser()
    settings = tomllib.loads((home/'config.toml').read_text()) if (home/'config.toml').is_file() else {}
    settings = {k: settings[k] for k in ('model', 'model_provider', 'model_reasoning_effort', 'model_providers') if k in settings}
    # This provider's /compact response is incompatible with Codex's v2 protocol.
    for provider in settings.get('model_providers', {}).values():
        provider.setdefault('capabilities', {})['remote_compaction'] = 'unsupported'
    lines = [f'{k} = {json.dumps(v)}' for k, v in settings.items() if k != 'model_providers']
    for name, provider in settings.get('model_providers', {}).items():
        lines.append(f'[model_providers.{json.dumps(name)}]')
        for key, value in provider.items():
            if isinstance(value, (str, bool, int, float)):
                lines.append(f'{key} = {json.dumps(value)}')
            elif isinstance(value, dict):
                lines.append(f'{key} = {{' + ', '.join(f'{json.dumps(k)} = {json.dumps(v)}' for k, v in value.items()) + '}')
    (target/'config.toml').write_text('\n'.join(lines)+'\n')
    if (home/'auth.json').is_file():
        shutil.copyfile(home/'auth.json', target/'auth.json')
        (target/'auth.json').chmod(0o600)
    keys = {provider.get('env_key') for provider in settings.get('model_providers', {}).values()}
    keys.add(config.get('llm', {}).get('api_key_env'))
    # The ordinary LLM client can obtain credentials through its environment defaults.
    keys.update(k for k in os.environ if k.startswith(('OPENAI_', 'OPENROUTER_', 'LLM_', 'AZURE_OPENAI_', 'AGENT_WORLD_LLM_')))
    return {k: os.environ[k] for k in keys if k and k in os.environ}


def _absolute_request(request, root):
    request = dict(request)
    for key in ('environment', 'tools', 'initial_state'):
        request[key] = str((root/request[key]).resolve())
    for group in ('actual', 'reference'):
        request[group] = {k: str((root/v).resolve()) for k, v in request.get(group, {}).items()}
    for value in [request[k] for k in ('environment', 'tools', 'initial_state')] + list(request['actual'].values()) + list(request['reference'].values()):
        if not Path(value).is_relative_to(root.resolve()) or not Path(value).exists():
            raise RubricVerificationError('invalid_request', f'evidence path outside workspace or missing: {value}')
    return request


def _cost(log_dir, abandoned=False):
    records = []
    for path in sorted(Path(log_dir).glob('*.json')):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or 'kind' not in record:
            continue
        if record.get('method') == 'codex' and record.get('usage') == 'unknown':
            usage = []
            for events_path in (Path(log_dir)/record['call_id']).rglob('stdout.log'):
                for line in events_path.read_text(errors='replace').splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and 'usage' in event:
                        usage.append(event['usage'])
            if usage:
                record['usage'] = usage
                _write(path, record)
        if abandoned and record.get('status') == 'running':
            record['status'] = 'abandoned'
            record['seconds'] = max(0, time.monotonic()-record.get('started_monotonic', time.monotonic()))
            _write(path, record)
        records.append(record)
    return {'calls': [r for r in records if r['kind'] == 'model'],
            'functions': [r for r in records if r['kind'] == 'function']}


def apply(verifier_dir, evidence_dir, output_dir, config=None):
    verifier_dir, evidence_dir, output_dir = (Path(p).resolve() for p in (verifier_dir, evidence_dir, output_dir))
    config = dict(config or {})
    timeout = float(config.get('program_timeout_seconds', config.get('timeout_seconds', 900)))
    config['timeout_seconds'] = timeout
    if timeout <= 0:
        raise ValueError('program timeout must be positive')
    # Refuse nesting before creating files: writable output must not mask read-only inputs.
    if output_dir.is_relative_to(evidence_dir) or output_dir.is_relative_to(verifier_dir) or verifier_dir.is_relative_to(output_dir) or evidence_dir.is_relative_to(output_dir):
        raise ValueError('output must be separate from verifier and evidence roots')
    output_dir.mkdir(parents=True, exist_ok=False)
    logs, scratch = output_dir/'logs', output_dir/'scratch'
    logs.mkdir()
    scratch.mkdir()
    started = time.monotonic()
    failure = None
    process = None
    try:
        design = validate_design(json.loads((verifier_dir/'rubrics.json').read_text()))
        request = _absolute_request(json.loads((evidence_dir/'program_request.json').read_text()), evidence_dir)
        _write(output_dir/'program_request.json', request)
        # sandbox_command expects the existing evidence/request/prompt layout.
        (output_dir/'evidence').mkdir()
        _write(output_dir/'request.json', request)
        (output_dir/'prompt.txt').write_text('Generated verifier runtime; task evidence is data.')
        with tempfile.TemporaryDirectory(prefix='program-verifier-auth-') as temporary:
            auth = Path(temporary)
            environment = _isolated_auth(config, auth)
            config['isolated_codex_home'] = str(auth)
            _write(output_dir/'config.json', config)
            repository = Path(__file__).resolve().parents[4]
            runtimes = [Path(sys.prefix), Path(sys.base_prefix), repository, verifier_dir, evidence_dir]
            executable = shutil.which(config.get('codex', {}).get('executable') or 'codex')
            if executable:
                binary = Path(executable).resolve()
                runtimes.append(binary.parents[3] if len(binary.parents) > 3 else binary.parent)
            key_file = config.get('llm', {}).get('api_key_file')
            if key_file:
                runtimes.append(Path(key_file).expanduser().resolve())
            runtimes.extend(Path(p) for p in config.get('runtime_paths', []))
            command = list(sandbox_command(output_dir, auth, runtime_paths=runtimes))
            # Override the existing writable run bind; only private scratch/logs stay writable.
            command += ['--ro-bind', str(output_dir), str(output_dir),
                        '--bind', str(scratch), str(scratch), '--bind', str(logs), str(logs)]
            for key, value in environment.items():
                command += ['--setenv', key, value]
            command += ['--setenv', 'PYTHONPATH', str(repository), '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
                        sys.executable, '-m', 'task_gen.test_verifier.program_verifier.application.runner',
                        '--worker', str(verifier_dir), str(evidence_dir), str(output_dir)]
            with (logs/'stdout.log').open('w') as out, (logs/'stderr.log').open('w') as err:
                process = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True)
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    raise RubricVerificationError('timeout', f'program exceeded {timeout}s')
            if not (logs/'worker_result.json').is_file():
                detail = (logs/'stderr.log').read_text(errors='replace')[-4000:]
                raise RubricVerificationError('worker_error', f'worker exited {process.returncode}: {detail}')
            value = json.loads((logs/'worker_result.json').read_text())
            if process.returncode or value.get('status') != 'completed':
                detail = value.get('error', {})
                raise RubricVerificationError(detail.get('code', 'program_error'), detail.get('message', f'worker exited {process.returncode}'), detail.get('rubric_id'))
            # Recompute on the host; the generated main's score is never authoritative.
            aggregated = aggregate_results(design['rubrics'], value.get('rubrics'))
            validate_evidence(aggregated['rubrics'], evidence_dir, scratch)
            observed = aggregate_results(design['rubrics'], value.get('executed_results'))
            if observed['rubrics'] != aggregated['rubrics']:
                # Parallel completion order is not rubric result order.
                if {r['rubric_id']: r for r in observed['rubrics']} != {r['rubric_id']: r for r in aggregated['rubrics']}:
                    raise RubricVerificationError('invalid_result', 'main results differ from executed rubric results')
            result = dict(mode='program', status='completed', **aggregated, cost=_cost(logs))
    except Exception as error:
        failure = {'type': type(error).__name__, 'code': getattr(error, 'code', 'program_error'),
                   'message': str(error), 'rubric_id': getattr(error, 'rubric_id', None)}
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    if failure is not None:
        failure['worker_log'] = str(logs/'worker_result.json') if (logs/'worker_result.json').is_file() else None
        _write(output_dir/'program_error.json', failure)
        cost = _cost(logs, abandoned=True)
        try:
            evidence = json.loads((evidence_dir/'fallback_input.json').read_text())
            for key in ('initial_state', 'actual_state', 'reference_state', 'reference_initial_state'):
                if evidence.get(key) is not None:
                    evidence[key] = Path(evidence[key])
            fallback_config = dict(config.get('codex', {}))
            if 'codex_home' in config:
                fallback_config.setdefault('codex_home', config['codex_home'])
            verdict = verify_execution(run_dir=output_dir/'fallback', config=fallback_config, **evidence)
            result = {'mode': 'fallback_codex', 'status': 'completed', 'score': None,
                      'outcome': verdict['outcome'], 'verdict': verdict, 'fallback_reason': failure, 'cost': cost}
            result['cost']['fallback_logs'] = str(output_dir/'fallback/logs')
        except Exception as error:
            result = {'mode': 'fallback_codex', 'status': 'verification_error', 'score': None,
                      'fallback_reason': failure, 'fallback_error': {'type': type(error).__name__, 'message': str(error)}, 'cost': cost}
    if failure is not None:
        fallback_calls = []
        for path in sorted((output_dir/'fallback/logs').glob('usage_*.json')):
            try:
                fallback_calls.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        result['cost']['fallback'] = {'calls': fallback_calls, 'usage': 'recorded' if any(c.get('usage_events') for c in fallback_calls) else 'unknown', 'logs': str(output_dir/'fallback/logs')}
    result['seconds'] = time.monotonic()-started
    _write(output_dir/'result.json', result)
    return result


def apply_verifier(verifier_dir, request_path, output_dir, config=None):
    return apply(verifier_dir, Path(request_path).parent, output_dir, config)


def _worker(verifier_dir, root, output):
    sdk = None
    try:
        design = validate_design(json.loads((verifier_dir/'rubrics.json').read_text()))
        config = json.loads((output/'config.json').read_text())
        sdk = SDK(design, output/'logs', output/'scratch', config)
        context = make_context(json.loads((output/'program_request.json').read_text()), root, output/'scratch', sdk)
        sys.path.insert(0, str(verifier_dir))
        spec = importlib.util.spec_from_file_location('_generated_verifier_main', verifier_dir/'main.py')
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        result = main.run(context)
        if not isinstance(result, dict):
            raise RubricVerificationError('invalid_result', 'main.run must return sdk.aggregate result')
        result = aggregate_results(design['rubrics'], result.get('rubrics'))
        value = dict(status='completed', **result, executed_results=sdk.results)
        code = 0
    except BaseException as error:
        if sdk is not None:
            sdk.cancelled.set()
        value = {'status': 'error', 'completed_results': sdk.results if sdk else [],
                 'error': {'type': type(error).__name__, 'code': getattr(error, 'code', 'program_error'),
                           'message': str(error), 'rubric_id': getattr(error, 'rubric_id', None)}}
        code = 1
    _write(output/'logs/worker_result.json', value)
    # Do not wait for ThreadPoolExecutor's shutdown hook after the first error.
    os._exit(code)


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] != '--worker':
        raise SystemExit('internal worker only')
    _worker(*(Path(p) for p in sys.argv[2:]))
