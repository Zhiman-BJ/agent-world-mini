import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest

from distill.relay import StreamingRelay


@pytest.mark.parametrize('send_headers', [False, True])
@pytest.mark.parametrize('stop', ['client_disconnect', 'task_exit'])
def test_abandoned_model_request_releases_upstream_and_retains_evidence(tmp_path, send_headers, stop):
    received = []
    request_started = threading.Event()
    upstream_closed = threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if send_headers:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
                self.wfile.flush()
            request_started.set()
            self.connection.settimeout(3)
            if self.rfile.read(1) == b'':
                upstream_closed.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    raw = tmp_path / 'raw'
    relay = StreamingRelay(f'http://127.0.0.1:{server.server_port}/v1', 'test-private-key', raw)
    relay.__enter__()
    endpoint = urlsplit(relay.base_url)
    client = socket.create_connection((endpoint.hostname, endpoint.port), timeout=3)
    body = json.dumps({'model': 'kimi-k3', 'stream': True,
                       'messages': [{'role': 'user', 'content': 'original task'}],
                       'max_completion_tokens': 131072}).encode()
    request = (f'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n'
               f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode() + body
    try:
        client.sendall(request)
        assert request_started.wait(3)
        if send_headers:
            response_path = raw / 'model_io/000001.response.sse'
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if response_path.is_file() and b'partial' in response_path.read_bytes():
                    break
                time.sleep(0.01)
            assert response_path.is_file() and b'partial' in response_path.read_bytes()
        if stop == 'client_disconnect':
            client.close()
            assert upstream_closed.wait(3)
        relay.__exit__(None, None, None)
        assert upstream_closed.wait(3)
        assert not relay._active_connections
        assert len(received) == 1
        assert received[0]['messages'] == [{'role': 'user', 'content': 'original task'}]
        assert received[0]['stream'] is True
        assert (raw / 'model_requests.jsonl').is_file()
        responses = [json.loads(line) for line in (raw / 'model_responses.jsonl').read_text().splitlines()]
        assert len(responses) == 1
        assert responses[0]['client_disconnected'] is True
        if send_headers:
            assert b'partial' in (raw / 'model_io/000001.response.sse').read_bytes()
        assert 'test-private-key' not in (raw / 'model_requests.jsonl').read_text()
    finally:
        client.close()
        if relay._thread.is_alive():
            relay.__exit__(None, None, None)
        server.shutdown()
        server.server_close()
        server_thread.join()


def test_task_exit_interrupts_retry_backoff_without_another_upstream_request(tmp_path):
    requests = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(503)
            self.send_header('Content-Length', '9')
            self.end_headers()
            self.wfile.write(b'temporary')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    raw = tmp_path / 'raw'
    relay = StreamingRelay(f'http://127.0.0.1:{server.server_port}/v1', 'test-key', raw,
                           retry_base_seconds=30, retry_max_seconds=30, retry_jitter_seconds=0)
    relay.__enter__()
    endpoint = urlsplit(relay.base_url)
    client = socket.create_connection((endpoint.hostname, endpoint.port), timeout=3)
    body = b'{"model":"kimi-k3","stream":true}'
    try:
        client.sendall((f'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n'
                        f'Content-Length: {len(body)}\r\n\r\n').encode() + body)
        deadline = time.monotonic() + 3
        attempt = raw / 'model_io/000001.attempt01.response'
        while not attempt.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert attempt.is_file()
        started = time.monotonic()
        relay.__exit__(None, None, None)
        assert time.monotonic() - started < 2
        assert len(requests) == 1
        assert not relay._active_connections
        result = json.loads((raw / 'model_responses.jsonl').read_text())
        assert result['client_disconnected'] is True
        assert result['attempts'][0]['delay_seconds'] == 30
    finally:
        client.close()
        if relay._thread.is_alive():
            relay.__exit__(None, None, None)
        server.shutdown()
        server.server_close()
        thread.join()
