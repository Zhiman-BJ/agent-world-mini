"""Loopback streaming relay that records exact model requests and responses."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import urlsplit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_kimi_k3_chat_request(request: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Translate Kimi Code provider fields to the public K3 Chat API contract."""
    if request.get("model") != "kimi-k3":
        return request, []
    normalized = dict(request)
    changes = []
    thinking = normalized.pop("thinking", None)
    if thinking is not None:
        changes.append("drop_thinking")
        if isinstance(thinking, dict):
            effort = thinking.get("effort")
            if effort is not None:
                if effort not in {"low", "high", "max"}:
                    raise ValueError(f"unsupported kimi-k3 reasoning effort: {effort!r}")
                existing = normalized.get("reasoning_effort")
                if existing is not None and existing != effort:
                    raise ValueError("conflicting kimi-k3 reasoning effort fields")
                normalized["reasoning_effort"] = effort
                changes.append("thinking_effort_to_reasoning_effort")
    if "max_tokens" in normalized:
        if "max_completion_tokens" not in normalized:
            normalized["max_completion_tokens"] = normalized["max_tokens"]
            changes.append("max_tokens_to_max_completion_tokens")
        del normalized["max_tokens"]
    return normalized, changes


class StreamingRelay(AbstractContextManager["StreamingRelay"]):
    """Forward an OpenAI-compatible endpoint without buffering SSE responses."""

    def __init__(self, upstream_base_url: str, api_key: str, raw_dir: Path):
        upstream = urlsplit(upstream_base_url.rstrip("/"))
        if upstream.scheme not in {"http", "https"} or not upstream.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        if upstream.username or upstream.password or upstream.query or upstream.fragment:
            raise ValueError("base_url must not contain credentials, query parameters, or fragments")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Kimi K3 API key is empty")

        self.upstream = upstream
        self.api_key = api_key
        self.raw_dir = raw_dir.resolve()
        self.io_dir = self.raw_dir / "model_io"
        self.io_dir.mkdir(parents=True, exist_ok=True)
        self.request_log = self.raw_dir / "model_requests.jsonl"
        self.response_log = self.raw_dir / "model_responses.jsonl"
        self.request_log.touch()
        self.response_log.touch()
        self._lock = threading.Lock()
        self._sequence = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        prefix = self.upstream.path.rstrip("/")
        return f"http://127.0.0.1:{self._server.server_port}{prefix}"

    def __enter__(self) -> "StreamingRelay":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def _next_id(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        with self._lock, path.open("a", encoding="utf-8") as stream:
            stream.write(line)

    def _upstream_path(self, incoming: str) -> str:
        parsed = urlsplit(incoming)
        prefix = self.upstream.path.rstrip("/")
        path = parsed.path
        if prefix and path.startswith(prefix + "/"):
            path = path[len(prefix):]
        target = prefix + (path if path.startswith("/") else "/" + path)
        return target + (("?" + parsed.query) if parsed.query else "")

    def _connection(self) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self.upstream.scheme == "https" else http.client.HTTPConnection
        return cls(self.upstream.hostname, self.upstream.port, timeout=3600)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                self._proxy(None, model_request=False)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                endpoint = self.path.rstrip("/")
                is_model = endpoint.endswith("/chat/completions") or endpoint.endswith("/responses")
                self._proxy(body, model_request=is_model)

            def _proxy(self, body: bytes | None, *, model_request: bool) -> None:
                request_id = relay._next_id() if model_request else None
                parsed_body: Any = None
                if body:
                    try:
                        parsed_body = json.loads(body)
                    except json.JSONDecodeError:
                        parsed_body = None
                forwarded_body = body
                forwarded_request = parsed_body
                normalizations: list[str] = []
                endpoint = self.path.rstrip("/")
                if model_request and isinstance(parsed_body, dict) and endpoint.endswith("/chat/completions"):
                    try:
                        forwarded_request, normalizations = _normalize_kimi_k3_chat_request(parsed_body)
                    except ValueError as error:
                        self._json_error(400, str(error))
                        relay._append(relay.response_log, {
                            "request_id": request_id,
                            "recorded_at": _now(),
                            "status": 400,
                            "error": "invalid_kimi_k3_request",
                        })
                        return
                    forwarded_body = json.dumps(
                        forwarded_request, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                    ).encode("utf-8")
                if model_request:
                    request_path = relay.io_dir / f"{request_id:06d}.request.json"
                    request_path.write_bytes(forwarded_body or b"")
                    record = {
                        "request_id": request_id,
                        "recorded_at": _now(),
                        "method": self.command,
                        "path": self.path,
                        "request": forwarded_request,
                        "normalizations": normalizations,
                        "raw_path": request_path.relative_to(relay.raw_dir.parent).as_posix(),
                        "sha256": hashlib.sha256(forwarded_body or b"").hexdigest(),
                    }
                    if normalizations:
                        harness_path = relay.io_dir / f"{request_id:06d}.harness-request.json"
                        harness_path.write_bytes(body or b"")
                        record.update({
                            "harness_request": parsed_body,
                            "harness_raw_path": harness_path.relative_to(relay.raw_dir.parent).as_posix(),
                            "harness_sha256": hashlib.sha256(body or b"").hexdigest(),
                        })
                    relay._append(relay.request_log, record)
                    if not isinstance(forwarded_request, dict) or forwarded_request.get("stream") is not True:
                        self._json_error(400, "All Kimi K3 model requests must use stream=true")
                        relay._append(relay.response_log, {
                            "request_id": request_id,
                            "recorded_at": _now(),
                            "status": 400,
                            "error": "non_streaming_request_rejected",
                        })
                        return

                headers = {
                    key: value for key, value in self.headers.items()
                    if key.lower() not in {
                        "authorization", "connection", "content-length", "host",
                        "proxy-authorization", "transfer-encoding", "accept-encoding",
                    }
                }
                headers["Authorization"] = f"Bearer {relay.api_key}"
                headers["Accept-Encoding"] = "identity"
                if forwarded_body is not None:
                    headers["Content-Length"] = str(len(forwarded_body))

                started = time.monotonic()
                connection = relay._connection()
                response_path = relay.io_dir / f"{request_id:06d}.response.sse" if model_request else None
                received = 0
                digest = hashlib.sha256()
                client_open = True
                try:
                    connection.request(
                        self.command, relay._upstream_path(self.path), body=forwarded_body, headers=headers
                    )
                    response = connection.getresponse()
                    self.send_response(response.status)
                    content_type = response.getheader("Content-Type")
                    if content_type:
                        self.send_header("Content-Type", content_type)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    sink = response_path.open("wb") if response_path is not None else None
                    try:
                        while True:
                            chunk = response.read1(65536)
                            if not chunk:
                                break
                            received += len(chunk)
                            digest.update(chunk)
                            if sink is not None:
                                sink.write(chunk)
                                sink.flush()
                            if client_open:
                                try:
                                    self.wfile.write(chunk)
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    client_open = False
                    finally:
                        if sink is not None:
                            sink.close()
                    if model_request:
                        relay._append(relay.response_log, {
                            "request_id": request_id,
                            "recorded_at": _now(),
                            "status": response.status,
                            "duration_ms": round((time.monotonic() - started) * 1000),
                            "bytes": received,
                            "body_path": response_path.relative_to(relay.raw_dir.parent).as_posix(),
                            "sha256": digest.hexdigest(),
                        })
                except Exception as error:
                    if model_request:
                        relay._append(relay.response_log, {
                            "request_id": request_id,
                            "recorded_at": _now(),
                            "duration_ms": round((time.monotonic() - started) * 1000),
                            "bytes": received,
                            "error": f"{type(error).__name__}: {error}",
                        })
                    if not self.wfile.closed:
                        try:
                            self._json_error(502, "Model relay failed")
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                finally:
                    connection.close()
                    self.close_connection = True

            def _json_error(self, status: int, message: str) -> None:
                body = json.dumps({"error": {"message": message, "type": "relay_error"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        return Handler
