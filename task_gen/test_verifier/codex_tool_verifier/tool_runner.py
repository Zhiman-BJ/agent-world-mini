"""CLI-to-host tool bridge; only the host can write authoritative call records."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import uuid

from jsonschema import Draft202012Validator

from task_gen.task_eval_verifier import sandbox_command
from ..program_verifier.application.runner import _absolute_request
from ..program_verifier.generation.pipeline import write_json
from .contracts import require, validate_package

REPOSITORY = Path(__file__).resolve().parents[3]
MAX_MESSAGE = 2 * 1024 * 1024


def kill_process(process):
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def read_message(stream):
    raw = stream.readline(MAX_MESSAGE + 1)
    require(len(raw) <= MAX_MESSAGE, 'tool message too large; write bulk facts to scratch and return paths')
    return json.loads(raw)


class ToolServer:
    def __init__(self, verifier, evidence, output, config):
        self.verifier, self.evidence, self.output = (Path(p).resolve() for p in (verifier, evidence, output))
        self.config = config
        self.plan = validate_package(self.verifier)
        self.tools = {t['id']: t for t in self.plan['tools']}
        self.request = _absolute_request(json.loads((self.evidence/'program_request.json').read_text()), self.evidence)
        self.output.mkdir(parents=True, exist_ok=True)
        self.logs, self.scratch = self.output/'tools', self.output/'scratch'
        self.logs.mkdir(exist_ok=True)
        self.scratch.mkdir(exist_ok=True)
        (self.output/'evidence').mkdir(exist_ok=True)
        write_json(self.output/'request.json', self.request)
        (self.output/'prompt.txt').touch(exist_ok=True)
        self.failed, self.cancelled = threading.Event(), threading.Event()
        self.error = None
        self.lock = threading.RLock()
        self.processes = set()
        self.limit = threading.Semaphore(int(config.get('tool_concurrency', 2)))
        require(int(config.get('tool_concurrency', 2)) > 0, 'tool_concurrency must be positive')
        require(float(config.get('tool_timeout_seconds', 120)) > 0, 'tool timeout must be positive')
        self.temporary = tempfile.TemporaryDirectory(prefix='cvt-')
        self.socket_dir = Path(self.temporary.name)
        self.session_path = self.output/'session.json'
        write_json(self.session_path, {'socket': str(self.socket_dir/'s'), 'scratch': str(self.scratch),
                                     'verifier': str(self.verifier), 'evidence': str(self.evidence)})
        owner = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    self.connection.settimeout(5)
                    request = read_message(self.rfile)
                    value = owner.call(request['tool'], request.get('inputs'), input_path=request.get('input_path'))
                except Exception as error:
                    owner.fail(f'Invalid tool request: {error}')
                    value = {'result': {'status': 'verification_error', 'error': str(error)}}
                try:
                    self.wfile.write(json.dumps(value, allow_nan=False).encode() + b'\n')
                except (BrokenPipeError, ConnectionResetError):
                    pass
        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = False
        self.server = Server(str(self.socket_dir/'s'), Handler)

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.cancelled.set()
        with self.lock:
            for process in list(self.processes):
                kill_process(process)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def fail(self, message):
        with self.lock:
            if self.error is None:
                self.error = message
            self.failed.set()
            self.cancelled.set()
            for process in list(self.processes):
                kill_process(process)

    @staticmethod
    def write_record(directory, record):
        temporary = directory/'record.tmp'
        write_json(temporary, record)
        temporary.replace(directory/'record.json')

    def records(self):
        return [json.loads(p.read_text()) for p in sorted(self.logs.glob('C*/record.json'))]

    def call(self, tool_id, inputs=None, *, input_path=None):
        call_id = 'C' + uuid.uuid4().hex[:16]
        directory = self.logs/call_id
        directory.mkdir()
        started = time.monotonic()
        record = {'kind': 'tool', 'call_id': call_id, 'tool_id': tool_id, 'inputs': inputs,
                  'status': 'running', 'started_monotonic': started}
        self.write_record(directory, record)
        process = scratch = None
        try:
            with self.limit:
                require(not self.cancelled.is_set(), 'verification session stopped')
                require(tool_id in self.tools, 'unknown tool ID')
                tool = self.tools[tool_id]
                if input_path is not None:
                    path = Path(input_path).resolve()
                    require(path.is_relative_to(self.scratch) and path.is_file(), 'input JSON must be in session scratch')
                    require(path.stat().st_size <= MAX_MESSAGE, 'input JSON too large')
                    inputs = json.loads(path.read_text())
                    record['inputs'] = inputs
                try:
                    Draft202012Validator(tool['input_schema']).validate(inputs)
                except Exception as e:
                    raise ValueError(f'input schema: {e}') from e
                scratch = self.scratch/call_id
                scratch.mkdir()
                record['scratch_dir'] = str(scratch)
                write_json(directory/'input.json', inputs)
                write_json(directory/'context.json', {'root': str(self.evidence), 'request': self.request, 'scratch_dir': str(scratch)})
                with tempfile.TemporaryDirectory(prefix='cvt-tool-') as auth:
                    runtimes = [REPOSITORY, self.verifier, self.evidence, Path(sys.prefix), Path(sys.base_prefix)]
                    runtimes += [Path(p) for p in self.config.get('runtime_paths', [])]
                    command = list(sandbox_command(self.output, Path(auth), runtime_paths=runtimes))
                    command += ['--unshare-net', '--ro-bind', str(self.output), str(self.output),
                                '--bind', str(scratch), str(scratch), '--setenv', 'PYTHONPATH', str(REPOSITORY),
                                '--setenv', 'PYTHONDONTWRITEBYTECODE', '1', sys.executable, '-m',
                                'task_gen.test_verifier.codex_tool_verifier.tool_runner', '--worker',
                                str(self.verifier/tool['file']), tool['function'], str(directory)]
                    with (directory/'stdout.log').open('w') as out, (directory/'stderr.log').open('w') as err:
                        with self.lock:
                            require(not self.cancelled.is_set(), 'verification session stopped')
                            process = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True)
                            self.processes.add(process)
                        try:
                            process.wait(timeout=float(self.config.get('tool_timeout_seconds', 120)))
                        except subprocess.TimeoutExpired:
                            raise TimeoutError('static tool exceeded its time budget')
                    require(not self.cancelled.is_set(), 'verification session stopped')
                    require(process.returncode == 0, 'tool process failed: ' + (directory/'stderr.log').read_text(errors='replace')[-3000:])
                    require((directory/'stdout.log').stat().st_size <= MAX_MESSAGE, 'tool output too large')
                    value = json.loads((directory/'stdout.log').read_text())
                    json.dumps(value, allow_nan=False)
                    try:
                        Draft202012Validator(tool['output_schema']).validate(value)
                    except Exception as e:
                        raise ValueError(f'output schema: {e}') from e
                    require(value.get('status') != 'verification_error', 'tool reported verification_error: ' + json.dumps(value))
                    record.update(status='completed', result=value)
        except Exception as error:
            record.update(status='abandoned' if self.cancelled.is_set() and not self.failed.is_set() else 'error',
                          error=f'{type(error).__name__}: {error}', result={'status': 'verification_error', 'error': str(error)})
            self.fail(record['error'])
        finally:
            kill_process(process)
            with self.lock:
                self.processes.discard(process)
            record['seconds'] = time.monotonic() - started
            self.write_record(directory, record)
        return {'call_id': call_id, 'tool_id': tool_id,
                'scratch_dir': str(scratch) if scratch is not None else None, 'result': record['result']}


def make_context(request, root, scratch):
    context = SimpleNamespace(root=Path(root), request=request, scratch_dir=Path(scratch),
                              task_id=request['task_id'], task_text=request['task_text'], initial_state=Path(request['initial_state']),
                              actual={k: Path(v) for k, v in request['actual'].items()}, reference={k: Path(v) for k, v in request['reference'].items()})
    context.error = lambda code, message, *args: RuntimeError(f'{code}: {message}')
    return context


def worker(path, name, directory):
    saved = json.loads((directory/'context.json').read_text())
    context = make_context(saved['request'], saved['root'], saved['scratch_dir'])
    spec = importlib.util.spec_from_file_location('_static_tool', path)
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)
        value = getattr(module, name)(context, json.loads((directory/'input.json').read_text()))
    print(json.dumps(value, allow_nan=False))


def main():
    if len(sys.argv) == 5 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--tool', required=True)
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    try:
        session = json.loads(args.session.read_text())
        path = args.input.resolve()
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(session['socket'])
            connection.sendall(json.dumps({'tool': args.tool, 'input_path': str(path)}, allow_nan=False).encode() + b'\n')
            value = read_message(connection.makefile('rb'))
    except Exception as error:
        value = {'result': {'status': 'verification_error', 'error': str(error)}}
    print(json.dumps(value, allow_nan=False))
    if value['result']['status'] == 'verification_error':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
