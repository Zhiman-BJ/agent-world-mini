from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI

from agent_world_mini.utils.config import load_local_environment


TextDeltaHandler = Callable[[str], None]


def _env_bool(value: str | None) -> bool:
    """解析环境变量中的布尔值。"""

    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


@dataclass
class LLMClient:
    """OpenAI-compatible 模型调用接口。"""

    model: str = ""
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    timeout_seconds: int = 90
    stream: bool = False
    _client: OpenAI | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_environment(cls) -> "LLMClient":
        """从项目配置或系统环境变量初始化。

        优先读取统一的 ``LLM_*``。未填写时继续兼容现有的
        ``OPENROUTER_*`` 和 ``OPENAI_*`` 配置。
        """

        environment = load_local_environment()
        if any(environment.get(key) for key in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")):
            prefix = "LLM"
        elif environment.get("OPENROUTER_API_KEY"):
            prefix = "OPENROUTER"
        else:
            prefix = "OPENAI"

        return cls(
            model=environment.get(f"{prefix}_MODEL", ""),
            base_url=environment.get(f"{prefix}_BASE_URL", ""),
            api_key=environment.get(f"{prefix}_API_KEY", ""),
            timeout_seconds=int(
                environment.get("LLM_TIMEOUT_SECONDS")
                or environment.get(f"{prefix}_TIMEOUT_SECONDS", "90")
            ),
            stream=_env_bool(
                environment.get("LLM_STREAM") or environment.get(f"{prefix}_STREAM")
            ),
        )

    @property
    def enabled(self) -> bool:
        """模型、地址和密钥齐全时才允许调用。"""

        return bool(self.model and self.base_url and self.api_key)

    @property
    def client(self) -> OpenAI:
        """延迟初始化官方客户端，未配置时不创建连接对象。"""

        if not self.enabled:
            raise RuntimeError("LLM 未配置完整，请检查 model、base_url 和 API key")
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            )
        return self._client

    def _create(
        self,
        messages: list[dict[str, str]],
        *,
        on_delta: TextDeltaHandler | None = None,
        **parameters: Any,
    ) -> tuple[str, dict[str, object]]:
        """调用模型并统一处理普通响应和流式响应。"""

        streaming = self.stream or on_delta is not None
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=streaming,
            **(
                {"stream_options": {"include_usage": True}}
                if streaming
                else {}
            ),
            **parameters,
        )

        if not streaming:
            content = response.choices[0].message.content or ""
            if not content.strip():
                raise RuntimeError("LLM 没有返回文本内容")
            usage = response.usage.model_dump() if response.usage else {}
            return content, usage

        parts: list[str] = []
        usage: dict[str, object] = {}
        for chunk in response:
            if chunk.usage:
                usage = chunk.usage.model_dump()
            if not chunk.choices:
                continue
            text = chunk.choices[0].delta.content
            if text:
                parts.append(text)
                if on_delta is not None:
                    on_delta(text)

        content = "".join(parts)
        if not content.strip():
            raise RuntimeError("LLM 流式响应中没有文本内容")
        return content, usage

    def complete_text(
        self,
        system: str,
        prompt: str,
        *,
        on_delta: TextDeltaHandler | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """生成普通文本。传入 ``on_delta`` 时实时返回文本增量。"""

        parameters: dict[str, object] = {"temperature": temperature}
        if max_tokens is not None:
            parameters["max_tokens"] = max_tokens
        content, _usage = self._create(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            on_delta=on_delta,
            **parameters,
        )
        return content

    def complete_json(
        self,
        system: str,
        prompt: str,
        *,
        on_delta: TextDeltaHandler | None = None,
        max_tokens: int = 6000,
        use_response_format: bool = True,
    ) -> str:
        """生成 JSON 文本。"""

        parameters: dict[str, object] = {
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        if use_response_format:
            parameters["response_format"] = {"type": "json_object"}
        content, _usage = self._create(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            on_delta=on_delta,
            **parameters,
        )
        return content
