from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class LLMClient:
    model: str = "deepseek/deepseek-v4-flash"
    endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout_seconds: int = 90

    @property
    def enabled(self) -> bool:
        return bool(os.environ.get("OPENROUTER_API_KEY"))

    @classmethod
    def from_environment(cls) -> "LLMClient":
        # The prototype intentionally has no dotenv dependency.  Loading this
        # workspace-local file makes command-line runs use the configured
        # research/review model while still letting explicit environment
        # variables take precedence.
        env_file = Path(".env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and key and not key.lstrip().startswith("#"):
                    os.environ.setdefault(key.strip(), value.strip())
        return cls(
            model=os.environ.get("OPENROUTER_MODEL", cls.model),
            endpoint=os.environ.get("OPENROUTER_BASE_URL", cls.endpoint),
            timeout_seconds=int(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "35")),
        )

    def _complete(self, payload: dict[str, object]) -> tuple[str, dict[str, object]]:
        if not self.enabled:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://localhost/agent-world-mini",
                "X-Title": "agent-world-mini",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"LLM request failed ({error.code}): {detail}") from error
        except URLError as error:
            raise RuntimeError(f"LLM request failed: {error.reason}") from error
        try:
            message = result["choices"][0]["message"]
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("LLM returned no textual content")
            usage = dict(result.get("usage", {}))
            if isinstance(message.get("annotations"), list):
                usage["annotations"] = message["annotations"]
            return content, usage
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError(f"Unexpected LLM response: {result}") from error

    def complete_json(self, system: str, prompt: str) -> str:
        content, _usage = self._complete({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        })
        return content

    def research_json(self, system: str, prompt: str, max_tool_calls: int = 10) -> tuple[str, dict[str, object]]:
        return self._complete({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "tools": [
                {
                    "type": "openrouter:web_search",
                    "parameters": {
                        "max_results": 5,
                        "max_total_results": 30,
                        "search_context_size": "medium",
                    },
                },
                {
                    "type": "openrouter:web_fetch",
                    "parameters": {"max_content_tokens": 12000},
                },
            ],
            "tool_choice": "required",
            "max_tool_calls": max_tool_calls,
        })
