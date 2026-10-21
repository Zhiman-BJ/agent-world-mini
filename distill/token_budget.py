"""Count chat inputs using the tokenizer and template of the serving SGLang model."""

from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen


class SGLangTokenBudget:
    def __init__(self, base_url: str, api_key: str, context_size: int, safety_margin: int = 4096):
        if type(context_size) is not int or context_size < 1:
            raise ValueError("context_size must be positive")
        if type(safety_margin) is not int or not 0 <= safety_margin < context_size:
            raise ValueError("safety_margin must be nonnegative and smaller than context_size")
        self.endpoint = base_url.rstrip("/") + "/tokenize"
        self.api_key = api_key
        self.context_size = context_size
        self.safety_margin = safety_margin

    def check(self, request: dict) -> dict:
        output_tokens = request.get("max_completion_tokens", request.get("max_tokens"))
        if type(output_tokens) is not int or output_tokens < 1:
            raise ValueError("An explicit positive output token cap is required")
        if not isinstance(request.get("messages"), list):
            raise ValueError("SGLang token budgeting requires chat messages")
        fields = ("model", "messages", "tools", "tool_choice", "reasoning_effort",
                  "chat_template_kwargs", "continue_final_message")
        payload = {key: request[key] for key in fields if key in request}
        started = time.monotonic()
        with urlopen(Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False).encode(),
                             headers={"Content-Type": "application/json",
                                      "Authorization": f"Bearer {self.api_key}"}), timeout=120) as response:
            result = json.load(response)
        input_tokens, server_context = result.get("count"), result.get("max_model_len")
        if type(input_tokens) is not int or input_tokens < 0:
            raise ValueError("SGLang tokenize did not return a nonnegative count")
        if type(server_context) is not int or server_context < 1:
            raise ValueError("SGLang tokenize did not return max_model_len")
        context_size = min(self.context_size, server_context)
        return {
            "backend": "sglang_v1_tokenize",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "context_size": context_size,
            "safety_margin": self.safety_margin,
            "input_budget": context_size - output_tokens - self.safety_margin,
            "accepted": input_tokens + output_tokens + self.safety_margin <= context_size,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
