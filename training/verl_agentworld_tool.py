from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from agent_world_mini.runtime import LocalToolRuntime
from agent_world_mini.sessions import runtime_for_rollout
from verl.tools.base_tool import BaseTool
from verl.tools.schemas import ToolResponse


class AgentWorldTool(BaseTool):
    """Dispatch one registered veRL tool to an immutable Agent-World environment."""

    def __init__(self, config: dict[str, Any], tool_schema: Any):
        self.config = config
        self.tool_schema = tool_schema
        self.name = tool_schema.function.name
        self.original_name = str(config["original_name"])
        self.instances: dict[str, tuple[dict[str, Any], str, str, Any]] = {}

    async def create(self, instance_id: str | None = None, **kwargs: Any) -> tuple[str, ToolResponse]:
        create_kwargs = kwargs.get("create_kwargs", {})
        relative = Path(str(create_kwargs["environment_file"]))
        root = Path(os.environ["AGENTWORLD_DATA_ROOT"])
        payload = json.loads((root / relative).read_text(encoding="utf-8"))
        token = instance_id or uuid4().hex
        environment_key = str((root / relative).resolve())
        self.instances[token] = (
            payload,
            environment_key,
            str(create_kwargs.get("original_name", self.original_name)),
            SimpleNamespace(),
        )
        return token, ToolResponse()

    async def execute(
        self,
        instance_id: str,
        parameters: dict[str, Any],
        **kwargs: Any,
    ) -> tuple[ToolResponse, float, dict[str, Any]]:
        payload, environment_key, original_name, fallback_rollout = self.instances[instance_id]
        rollout = kwargs.get("agent_data") or fallback_rollout
        runtime = runtime_for_rollout(
            rollout,
            environment_key,
            lambda: LocalToolRuntime.from_dict(payload),
        )
        try:
            result = runtime.call(original_name, parameters)
            if isinstance(result, list) and len(result) > 8:
                result = result[:8] + [{"truncated": len(result) - 8}]
            return ToolResponse(text=json.dumps(result, ensure_ascii=False)), 0.0, {"tool_ok": 1}
        except (KeyError, ValueError, TypeError, StopIteration) as error:
            return ToolResponse(text=f"Tool error: {type(error).__name__}: {error}"), 0.0, {"tool_ok": 0}

    async def release(self, instance_id: str, **kwargs: Any) -> None:
        self.instances.pop(instance_id, None)
