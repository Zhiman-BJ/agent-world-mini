from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import socket
import threading
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest

from distill.relay import StreamingRelay, _prefetch_responses_prelude, _stream_error


def event(kind, **fields):
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()


PRELUDE = event("response.created", response={"id": "pending"}) + event("response.in_progress")
SUCCESS = (event("response.created", response={"id": "accepted"})
           + event("response.output_text.delta", delta="answer") + event("response.completed"))
FAILED = event("response.failed", response={"error": {"code": "server_error", "message": "temporary"}})


@contextmanager
def upstream(bodies):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_arguments):
            pass

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            body = bodies[min(len(requests) - 1, len(bodies) - 1)]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def relay_request(base_url, raw, *, tools=None, max_retries=2):
    request = {"model": "test-gpt", "stream": True, "input": "unchanged task",
               "tools": [] if tools is None else tools, "max_output_tokens": 131072}
    with StreamingRelay(base_url, "test-private-key", raw, max_retries=max_retries,
                        retry_base_seconds=0, retry_max_seconds=0, retry_jitter_seconds=0) as relay:
        with urlopen(Request(relay.base_url + "/responses", data=json.dumps(request).encode(),
                             headers={"Content-Type": "application/json"}), timeout=5) as response:
            received = response.read()
    return received, json.loads((raw / "model_responses.jsonl").read_text())


@pytest.mark.parametrize("failure", [FAILED,
    event("error", code="upstream_stream_read_error", message="interrupted"), b""])
def test_transient_failure_before_any_output_retries_identical_request_and_keeps_evidence(tmp_path, failure):
    raw = tmp_path / "raw"
    with upstream([PRELUDE + failure, SUCCESS]) as (base_url, requests):
        received, result = relay_request(base_url, raw)
    assert received == SUCCESS
    assert len(requests) == 2 and requests[0] == requests[1]
    assert len(result["attempts"]) == 1
    attempt = result["attempts"][0]
    assert attempt["model_output_forwarded"] is False
    body = (raw.parent / attempt["body_path"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == attempt["sha256"]
    assert PRELUDE in body
    assert (raw / "model_io/000001.response.sse").read_bytes() == SUCCESS
    assert "test-private-key" not in (raw / "model_requests.jsonl").read_text()


def test_failure_after_any_output_is_forwarded_without_retry(tmp_path):
    body = PRELUDE + event("response.output_text.delta", delta="partial") + FAILED
    with upstream([body, SUCCESS]) as (base_url, requests):
        received, result = relay_request(base_url, tmp_path / "raw")
    assert len(requests) == 1
    assert received == body
    assert result["attempts"] == []
    assert result["stream_error"]["code"] == "server_error"


def test_preoutput_context_error_is_not_retried_even_when_type_is_server_error(tmp_path):
    body = PRELUDE + event("response.failed", response={"error": {
        "code": "context_length_exceeded", "type": "server_error", "message": "too long"}})
    with upstream([body, SUCCESS]) as (base_url, requests):
        received, result = relay_request(base_url, tmp_path / "raw")
    assert len(requests) == 1 and received == body
    assert result["attempts"] == []
    assert result["stream_error"]["code"] == "context_length_exceeded"


def test_retry_budget_is_finite_and_final_native_error_is_retained(tmp_path):
    body = PRELUDE + FAILED
    with upstream([body]) as (base_url, requests):
        received, result = relay_request(base_url, tmp_path / "raw", max_retries=2)
    assert len(requests) == 3
    assert received == body
    assert len(result["attempts"]) == 2
    assert result["error"] == "upstream_stream_error"


@pytest.mark.parametrize("first", [event("unknown.vendor_event"), event("response.output_item.added", item={})])
def test_unknown_or_output_events_end_the_safe_retry_window(tmp_path, first):
    body = PRELUDE + first + FAILED
    with upstream([body, SUCCESS]) as (base_url, requests):
        received, result = relay_request(base_url, tmp_path / "raw")
    assert len(requests) == 1 and received == body and result["attempts"] == []


def test_requests_with_server_side_tools_are_not_automatically_replayed(tmp_path):
    body = PRELUDE + FAILED
    with upstream([body, SUCCESS]) as (base_url, requests):
        received, result = relay_request(base_url, tmp_path / "raw", tools=[{"type": "web_search"}])
    assert len(requests) == 1 and received == body and result["attempts"] == []


def test_prelude_buffer_is_bounded_and_preserves_unread_bytes():
    body = b":" + b"x" * 100 + b"\n\n" + FAILED
    response = io.BytesIO(body)
    prefix, complete, error = _prefetch_responses_prelude(response, limit=16)
    assert len(prefix) == 16
    assert complete is False and error is None
    assert prefix + response.read() == body


def test_nested_and_flat_responses_errors_are_recognized_without_mistaking_output_text(tmp_path):
    path = tmp_path / "response.sse"
    for body, code in ((FAILED, "server_error"),
                       (event("error", code="stream_read_error", message="interrupted"), "stream_read_error")):
        path.write_bytes(body)
        assert _stream_error(path)["code"] == code
    path.write_bytes(event("response.output_text.delta", delta='{"error":{"code":"server_error"}}'))
    assert _stream_error(path) is None


def test_task_exit_interrupts_a_buffered_prelude_and_closes_upstream(tmp_path):
    started, closed = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_arguments):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(PRELUDE)
            self.wfile.flush()
            started.set()
            self.connection.settimeout(3)
            if self.rfile.read(1) == b"":
                closed.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    relay = StreamingRelay(f"http://127.0.0.1:{server.server_port}/v1", "test-key", tmp_path / "raw")
    relay.__enter__()
    endpoint = urlsplit(relay.base_url)
    client = socket.create_connection((endpoint.hostname, endpoint.port), timeout=3)
    body = b'{"model":"test-gpt","stream":true,"input":"unchanged task"}'
    try:
        client.sendall((f"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
                        f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
        assert started.wait(3)
        relay.__exit__(None, None, None)
        assert closed.wait(3)
        assert not relay._active_connections
        result = json.loads((tmp_path / "raw/model_responses.jsonl").read_text())
        assert result["client_disconnected"] is True
    finally:
        client.close()
        if relay._thread.is_alive():
            relay.__exit__(None, None, None)
        server.shutdown()
        server.server_close()
        thread.join()
