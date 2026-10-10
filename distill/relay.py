"""Loopback streaming relay that records exact model requests and responses."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import random
import threading
import time
from typing import Any, Callable
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
    if "parallel_tool_calls" not in normalized:
        normalized["parallel_tool_calls"] = True
        changes.append("parallel_tool_calls=true")
    return normalized, changes


class StreamingRelay(AbstractContextManager["StreamingRelay"]):
    """Forward an OpenAI-compatible endpoint without buffering SSE responses."""

    def __init__(
        self,
        upstream_base_url: str,
        api_key: str,
        raw_dir: Path,
        *,
        max_retries: int = 4,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 30.0,
        retry_jitter_seconds: float = 0.25,
        first_request_tool_validator: Callable[[set[str]], bool] | None = None,
        expected_tool_names: set[str] | None = None,
    ):
        upstream = urlsplit(upstream_base_url.rstrip("/"))
        if upstream.scheme not in {"http", "https"} or not upstream.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        if upstream.username or upstream.password or upstream.query or upstream.fragment:
            raise ValueError("base_url must not contain credentials, query parameters, or fragments")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Kimi K3 API key is empty")
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if retry_base_seconds < 0 or retry_max_seconds < 0 or retry_jitter_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if retry_max_seconds < retry_base_seconds:
            raise ValueError("retry_max_seconds must be at least retry_base_seconds")

        self.upstream = upstream
        self.api_key = api_key
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.retry_jitter_seconds = retry_jitter_seconds
        self.first_request_tool_validator = first_request_tool_validator
        self.expected_tool_names = set(expected_tool_names or ())
        self.raw_dir = raw_dir.resolve()
        self.io_dir = self.raw_dir / "model_io"
        self.io_dir.mkdir(parents=True, exist_ok=True)
        self.request_log = self.raw_dir / "model_requests.jsonl"
        self.response_log = self.raw_dir / "model_responses.jsonl"
        self.request_log.touch()
        self.response_log.touch()
        self._lock = threading.Lock()
        self._sequence = 0
        self._first_model_request_checked = False
        self.first_model_request_event = threading.Event()
        self.first_request_tool_failure: dict[str, Any] | None = None
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

    @staticmethod
    def _request_tool_names(request: dict[str, Any]) -> set[str]:
        names: set[str] = set()
        tools = request.get("tools")
        if not isinstance(tools, list):
            return names
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            name = function.get("name") if isinstance(function, dict) else tool.get("name")
            if isinstance(name, str):
                names.add(name)
        return names

    def _check_first_model_request(self, request: Any) -> dict[str, Any] | None:
        with self._lock:
            if self.first_request_tool_failure is not None:
                return self.first_request_tool_failure
            if self._first_model_request_checked:
                return None
            self._first_model_request_checked = True
            names = self._request_tool_names(request) if isinstance(request, dict) else set()
            validator = self.first_request_tool_validator
            if validator is not None and not validator(names):
                self.first_request_tool_failure = {
                    "reason": "mcp_tools_missing_from_first_model_request",
                    "expected_tool_names": sorted(self.expected_tool_names),
                    "observed_tool_names": sorted(names),
                }
            self.first_model_request_event.set()
            return self.first_request_tool_failure

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

    @staticmethod
    def _retryable_response(status: int, body: bytes) -> bool:
        if status in {408, 425, 429, 500, 502, 503, 504}:
            return True
        if status != 400:
            return False
        try:
            decoded = body.decode("utf-8", errors="replace")
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        message = payload.get("error", {}).get("message", "") if isinstance(payload, dict) else ""
        if not isinstance(message, str):
            message = ""
        normalized = message.casefold()
        return "internal maas component" in normalized and "reject" in normalized

    def _retry_delay(self, retry_number: int, retry_after: str | None = None) -> float:
        exponential = min(
            self.retry_max_seconds,
            self.retry_base_seconds * (2 ** max(0, retry_number - 1)),
        )
        delay = exponential + random.uniform(0, self.retry_jitter_seconds)
        if retry_after is not None:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        return delay

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
                    tool_failure = relay._check_first_model_request(forwarded_request)
                    if tool_failure is not None:
                        marker = relay.raw_dir / "mcp_startup_failure.json"
                        marker.write_text(
                            json.dumps(tool_failure, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        self._json_error(
                            503,
                            "Environment MCP tools were not registered before the first model request",
                        )
                        relay._append(relay.response_log, {
                            "request_id": request_id,
                            "recorded_at": _now(),
                            "status": 503,
                            "error": tool_failure["reason"],
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
                response_path = relay.io_dir / f"{request_id:06d}.response.sse" if model_request else None
                received = 0
                digest = hashlib.sha256()
                client_open = True
                attempts: list[dict[str, Any]] = []
                connection = None
                prefetched_body: bytes | None = None
                try:
                    response = None
                    for attempt in range(relay.max_retries + 1):
                        connection = relay._connection()
                        connection.request(
                            self.command,
                            relay._upstream_path(self.path),
                            body=forwarded_body,
                            headers=headers,
                        )
                        response = connection.getresponse()
                        if response.status not in {400, 408, 425, 429, 500, 502, 503, 504}:
                            break
                        retry_body = response.read()
                        retryable = relay._retryable_response(response.status, retry_body)
                        if not retryable or attempt >= relay.max_retries:
                            prefetched_body = retry_body
                            break
                        retry_after = response.getheader("Retry-After")
                        attempt_path = relay.io_dir / (
                            f"{request_id:06d}.attempt{attempt + 1:02d}.response"
                        )
                        attempt_path.write_bytes(retry_body)
                        delay = relay._retry_delay(
                            attempt + 1,
                            retry_after,
                        )
                        attempts.append({
                            "attempt": attempt + 1,
                            "status": response.status,
                            "body_path": attempt_path.relative_to(relay.raw_dir.parent).as_posix(),
                            "retryable": True,
                            "delay_seconds": delay,
                            "retry_after": retry_after,
                        })
                        response.close()
                        connection.close()
                        connection = None
                        time.sleep(delay)
                    if response is None:
                        raise RuntimeError("upstream returned no response")
                    self.send_response(response.status)
                    content_type = response.getheader("Content-Type")
                    if content_type:
                        self.send_header("Content-Type", content_type)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    sink = response_path.open("wb") if response_path is not None else None
                    try:
                        chunks = [prefetched_body] if prefetched_body is not None else None
                        while True:
                            if chunks is not None:
                                chunk = chunks.pop(0) if chunks else b""
                            else:
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
                            "attempts": attempts,
                        })
                except Exception as error:
                    if model_request:
                        relay._append(relay.response_log, {
                            "request_id": request_id,
                            "recorded_at": _now(),
                            "duration_ms": round((time.monotonic() - started) * 1000),
                            "bytes": received,
                            "error": f"{type(error).__name__}: {error}",
                            "attempts": attempts,
                        })
                    if not self.wfile.closed:
                        try:
                            self._json_error(502, "Model relay failed")
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                finally:
                    if connection is not None:
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
