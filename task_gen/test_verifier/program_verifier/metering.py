"""Transparent loopback API metering; no prompt changes, retries or auth logging."""
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import select
import socket
import threading
import time
from urllib.parse import urlsplit
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(path)


def token_counts(usage):
    u = usage if isinstance(usage, dict) else {}
    details = u.get('input_tokens_details', u.get('prompt_tokens_details')) or {}
    return {'input': u.get('input_tokens', u.get('prompt_tokens')),
            'cached_input': u.get('cached_input_tokens', details.get('cached_tokens')),
            'output': u.get('output_tokens', u.get('completion_tokens'))}


class MeteringRelay:
    def __init__(self, upstream, directory, phase=lambda: 'unknown'):
        self.upstream = urlsplit(upstream.rstrip('/'))
        if self.upstream.scheme not in ('http', 'https') or not self.upstream.hostname or self.upstream.username or self.upstream.query:
            raise ValueError('Expected an HTTP(S) upstream without credentials or query')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.phase = phase
        self.closing = threading.Event()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.server.server_port}{self.upstream.path}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.closing.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def handler(self):
        relay = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args): pass

            def do_GET(self): self.forward()
            def do_POST(self): self.forward()

            def forward(self):
                started = time.monotonic()
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                try: request = json.loads(body)
                except ValueError: request = {}
                if not isinstance(request, dict): request = {}
                record = {'id': uuid.uuid4().hex, 'started_at': now(), 'phase': relay.phase(),
                          'path': self.path, 'model': request.get('model'),
                          'reasoning_effort': (request.get('reasoning') or {}).get('effort', request.get('reasoning_effort')),
                          'request_sha256': hashlib.sha256(body).hexdigest(), 'request_bytes': len(body),
                          'status': 'running', 'usage': None, 'cost_fields': {}, 'status_code': None}
                path = relay.directory/(record['id']+'.json')
                save(path, record)
                cls = http.client.HTTPSConnection if relay.upstream.scheme == 'https' else http.client.HTTPConnection
                connection = cls(relay.upstream.hostname, relay.upstream.port, timeout=3600)
                done, disconnected = threading.Event(), threading.Event()
                def watch():
                    while not done.wait(.25):
                        try:
                            readable = select.select([self.connection], [], [], 0)[0]
                            closed = bool(readable) and not self.connection.recv(1, socket.MSG_PEEK)
                        except OSError: closed = True
                        if closed or relay.closing.is_set():
                            disconnected.set()
                            try:
                                if connection.sock: connection.sock.shutdown(socket.SHUT_RDWR)
                            except OSError: pass
                            return
                watcher = threading.Thread(target=watch, daemon=True)
                watcher.start()
                sent_headers = False
                buffer = b''
                def consume(raw):
                    if raw.startswith(b'data:'): raw = raw[5:].strip()
                    try: value = json.loads(raw)
                    except (ValueError, UnicodeError): return
                    if not isinstance(value, dict): return
                    response = value.get('response', value)
                    if not isinstance(response, dict): return
                    if isinstance(response.get('usage'), dict): record['usage'] = response['usage']
                    if response.get('id'): record['response_id'] = response['id']
                    if response.get('error'): record['api_error'] = response['error']
                    if value.get('type') in ('response.completed', 'response.incomplete', 'response.failed'):
                        record['response_status'] = value['type']
                        record['incomplete_details'] = response.get('incomplete_details')
                    for source in (value, response, response.get('usage') or {}):
                        for key, item in source.items():
                            if 'cost' in key.lower() or key.lower() == 'currency': record['cost_fields'][key] = item
                try:
                    headers = {k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','content-length','transfer-encoding','accept-encoding')}
                    headers['Accept-Encoding'] = 'identity'
                    connection.request(self.command, self.path, body=body or None, headers=headers)
                    response = connection.getresponse()
                    record['status_code'] = response.status
                    record['first_byte_seconds'] = time.monotonic()-started
                    record['response_headers'] = {k:v for k,v in response.getheaders() if 'cost' in k.lower() or k.lower() in ('x-request-id', 'request-id')}
                    self.send_response(response.status)
                    self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                    if response.getheader('Retry-After') is not None:
                        self.send_header('Retry-After', response.getheader('Retry-After'))
                    self.send_header('Connection', 'close')
                    self.end_headers();sent_headers = True
                    streaming = 'text/event-stream' in response.getheader('Content-Type', '')
                    while True:
                        chunk = response.read1(65536)
                        if not chunk: break
                        buffer += chunk
                        if streaming:
                            while b'\n' in buffer:
                                line, buffer = buffer.split(b'\n', 1)
                                consume(line.strip())
                        self.wfile.write(chunk);self.wfile.flush()
                    if buffer: consume(buffer)
                    record['status'] = 'completed' if response.status < 400 else 'http_error'
                except Exception as error:
                    record.update(status='error', error=f'{type(error).__name__}: {error}')
                    if not sent_headers:
                        try: self.send_error(502, 'Metered upstream request failed')
                        except OSError: pass
                finally:
                    done.set();watcher.join(timeout=1)
                    connection.close()
                    record.update(finished_at=now(), seconds=time.monotonic()-started, client_disconnected=disconnected.is_set())
                    save(path, record)
                    self.close_connection = True
        return Handler
