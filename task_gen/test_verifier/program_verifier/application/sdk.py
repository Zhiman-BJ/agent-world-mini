"""The fixed SDK injected into generated programs; no task-specific checks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
import json
import threading
import time
import tomllib
import uuid

from ..contracts import RubricVerificationError, aggregate_results, validate_evidence, resolve_evidence_path

_ACTIVE = ContextVar('verifier_function', default=(None, None))


class Context(SimpleNamespace):
    def error(self, code, message, rubric_id=None):
        return RubricVerificationError(code, message, rubric_id)

    def resolve_evidence_path(self, path):
        return resolve_evidence_path(path, self.root, self.scratch_dir)


def make_context(request, root, scratch_dir, sdk):
    return Context(request=request, root=Path(root), task_id=request['task_id'],
        task_text=request['task_text'], initial_state=Path(request['initial_state']),
        actual={k: Path(v) for k, v in request['actual'].items()},
        reference={k: Path(v) for k, v in request.get('reference', {}).items()},
        scratch_dir=Path(scratch_dir), sdk=sdk)


class SDK:
    def __init__(self, design, log_dir, scratch_dir, config=None):
        self.design, self.config = design, dict(config or {})
        self.log_dir, self.scratch_dir = Path(log_dir), Path(scratch_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self.deadline = time.monotonic() + float(self.config.get('program_timeout_seconds', self.config.get('timeout_seconds', 900)))
        self.node_concurrency = int(self.config.get('node_concurrency', self.config.get('max_concurrency', 4)))
        model_concurrency = int(self.config.get('model_concurrency', 2))
        codex_cfg = dict(self.config.get('codex', {}))
        self.codex_model = codex_cfg.get('model') or self.config.get('model')
        isolated_home = self.config.get('isolated_codex_home')
        if not self.codex_model and isolated_home and (Path(isolated_home)/'config.toml').is_file():
            self.codex_model = tomllib.loads((Path(isolated_home)/'config.toml').read_text()).get('model')
        if self.node_concurrency < 1 or model_concurrency < 1 or self.deadline <= time.monotonic():
            raise ValueError('budgets and concurrency must be positive')
        self.models = threading.BoundedSemaphore(model_concurrency)
        self.cancelled = threading.Event()
        self.failure = None
        self.results = []
        self.lock = threading.Lock()

    def error(self, code, message, rubric_id=None):
        return RubricVerificationError(code, message, rubric_id)

    def _stop(self, error):
        with self.lock:
            if self.failure is None:
                self.failure = error
            self.cancelled.set()

    def _check(self):
        if self.cancelled.is_set():
            raise self.error('cancelled', 'program verification stopped')
        if time.monotonic() >= self.deadline:
            self.cancelled.set()
            raise self.error('timeout', 'program budget exhausted')

    def _write(self, ident, record):
        target = self.log_dir / f'{ident}.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, default=str))
        temporary.replace(target)

    def execute(self, fn, context, inputs, rubric_id=None):
        self._check()
        name = f'{fn.__module__}.{fn.__name__}'
        declarations = [(r['function_contract'], r['id']) for r in self.design['rubrics']]
        declarations += [(h, None) for h in self.design.get('helpers', [])]
        declarations += [({'file': m['file'], 'name': 'build_prompt'}, None)
                         for m in self.design.get('model_calls', [])]
        module_file = Path(fn.__code__.co_filename).resolve()
        matched = [(c, rid) for c, rid in declarations if c['name'] == fn.__name__ and tuple(module_file.parts[-2:]) == tuple(Path(c['file']).parts)]
        if not matched:
            error = self.error('undeclared_function', f'function does not match a generated contract: {name}', rubric_id)
            self._stop(error)
            raise error
        declared_rubric = matched[0][1]
        if rubric_id is not None and rubric_id != declared_rubric:
            error = self.error('invalid_rubric_id', f'{name} is declared for {declared_rubric}', rubric_id)
            self._stop(error)
            raise error
        rubric_id = declared_rubric
        ident, started = 'function_' + uuid.uuid4().hex, time.monotonic()
        record = {'kind': 'function', 'function': name, 'rubric_id': rubric_id, 'status': 'running', 'started_monotonic': started}
        self._write(ident, record)
        token = _ACTIVE.set((name, rubric_id))
        try:
            value = fn(context, inputs)
            self._check()
            if rubric_id is not None:
                # Validate early so malformed results stop other work immediately.
                rubric = next(r for r in self.design['rubrics'] if r['id'] == rubric_id)
                aggregate_results([rubric], [value])
                validate_evidence([value], context.root, context.scratch_dir)
                with self.lock:
                    self.results.append(value)
            record.update(status='completed', result=value)
            return value
        except BaseException as error:
            self._stop(error)
            record.update(status='error', error={'type': type(error).__name__, 'code': getattr(error, 'code', 'function_error'), 'message': str(error)})
            raise
        finally:
            _ACTIVE.reset(token)
            record['seconds'] = time.monotonic() - started
            self._write(ident, record)

    run_check = execute

    def parallel(self, functions):
        self._check()
        executor = ThreadPoolExecutor(max_workers=self.node_concurrency)
        futures = []
        try:
            for fn in functions:
                self._check()
                futures.append(executor.submit(self._invoke, fn))
            done, pending = wait(futures, timeout=max(0, self.deadline-time.monotonic()), return_when=FIRST_EXCEPTION)
            for future in done:
                if future.exception() is not None:
                    raise future.exception()
            if pending:
                raise self.error('timeout', 'parallel work exceeded program budget')
            return [f.result() for f in futures]
        except BaseException as error:
            self._stop(error)
            for future in futures:
                future.cancel()
            raise self.failure
        finally:
            # The runner exits the PID namespace on failure, killing active descendants.
            executor.shutdown(wait=False, cancel_futures=True)

    def _invoke(self, fn):
        self._check()
        try:
            return fn()
        except BaseException as error:
            self._stop(error)
            raise

    def aggregate(self, results):
        self._check()
        return aggregate_results(self.design['rubrics'], results)

    def call_llm(self, prompt, *, call_file, function_name=None, rubric_id=None):
        return self._model('llm', prompt, call_file, function_name, rubric_id)

    def call_codex(self, prompt, *, call_file, function_name=None, rubric_id=None):
        return self._model('codex', prompt, call_file, function_name, rubric_id)

    def _model(self, method, prompt, call_file, function_name, rubric_id):
        self._check()
        declared = {m['file']: m for m in self.design.get('model_calls', [])}
        if call_file not in declared or declared[call_file]['method'] != method:
            error = self.error('undeclared_model_call', f'{method}: {call_file}', rubric_id)
            self._stop(error)
            raise error
        if not isinstance(prompt, str) or not prompt.strip():
            error = self.error('invalid_prompt', 'model prompt must be nonempty', rubric_id)
            self._stop(error)
            raise error
        while not self.models.acquire(timeout=min(0.1, max(0.001, self.deadline-time.monotonic()))):
            self._check()
        ident, started = 'model_' + uuid.uuid4().hex, time.monotonic()
        active_name, active_rubric = _ACTIVE.get()
        record = {'kind': 'model', 'call_id': ident, 'method': method,
            'call_file': call_file, 'function': function_name or active_name,
            'rubric_id': rubric_id if rubric_id is not None else active_rubric,
            'model': (self.codex_model if method == 'codex' else self.config.get('llm', {}).get('model')) or 'unknown',
            'usage': 'unknown', 'prompt': prompt, 'status': 'running', 'started_monotonic': started}
        self._write(ident, record)
        try:
            self._check()
            remaining = max(1, int(self.deadline-time.monotonic()))
            if method == 'llm':
                from task_gen.tool_graph.llm import infer
                cfg = dict(self.config.get('llm', {}))
                if cfg.get('backend') == 'codex':
                    raise self.error('invalid_config', 'call_llm requires the API backend')
                if declared[call_file].get('response_format') == 'json_object':
                    cfg.setdefault('response_format', {'type': 'json_object'})
                record['response_format'] = cfg.get('response_format')
                cfg['timeout_seconds'] = min(int(cfg.get('timeout_seconds', remaining)), remaining)
                result = infer(prompt, llm_config=cfg)
                raw = result.text
                record.update(model=result.model, usage=dict(result.usage))
            else:
                from task_gen.task_eval_verifier import _VerifierClient
                cfg = dict(self.config.get('codex', {}))
                cfg.setdefault('model', self.config.get('model'))
                allowed = ('model', 'base_url', 'api_key', 'executable', 'reasoning_effort')
                auth_config = Path(self.config['isolated_codex_home'])/'config.toml'
                settings = tomllib.loads(auth_config.read_text())
                cfg['model'] = cfg.get('model') or settings.get('model')
                record['model'] = cfg.get('model') or 'unknown'
                client = _VerifierClient(**{k: v for k, v in cfg.items() if k in allowed},
                    codex_home=self.config['isolated_codex_home'],
                    timeout_seconds=min(int(cfg.get('timeout_seconds', remaining)), remaining),
                    bypass_approvals_and_sandbox=True, log_directory=self.log_dir / ident)
                working = self.scratch_dir / ident
                working.mkdir()
                raw = client.run(prompt, working_directory=working)
            record['response'] = raw
            self._check()
            record['status'] = 'completed'
            return raw
        except BaseException as error:
            self._stop(error)
            record.update(status='error', error={'type': type(error).__name__, 'message': str(error)})
            raise
        finally:
            if method == 'codex' and 'client' in locals() and client.last_log_directory is not None:
                events_path = client.last_log_directory/'stdout.log'
                usage = []
                if events_path.is_file():
                    for line in events_path.read_text().splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(event, dict) and 'usage' in event:
                            usage.append(event['usage'])
                if usage:
                    record['usage'] = usage
            record['seconds'] = time.monotonic()-started
            self._write(ident, record)
            self.models.release()
