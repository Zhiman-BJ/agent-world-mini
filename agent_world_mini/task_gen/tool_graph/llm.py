"""建图和任务转写共用的稳定 LLM 调用入口。

:func:`infer` 负责调用，:func:`parse_json_object` 负责把回复解析成固定形状的
JSON object。需要结构化输出的阶段必须使用后者，不要各自实现围栏剥离或括号
匹配，以免四个阶段对"模型没按格式回答"的判定不一致。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any, overload

from agent_world_mini.utils.llm import LLMClient
from agent_world_mini.utils.search_agent.codex import CodexAgentClient


Message = dict[str, str]
ChunkHandler = Callable[[int, str], None]


class MalformedJSONError(ValueError):
    """LLM 回复无法解析为单个顶层 JSON object。

    Step 1、2、3、4 都要求 LLM 返回固定形状的 JSON object，因此解析逻辑集中在
    本模块，只抛出这一种异常；各阶段的失败策略仍由自己决定：

    * Step 1：终止整个阶段，不返回不完整的 ``tool_graph``；
    * Step 2：保留该项原始链，并写入 ``llm_review.error``；
    * Step 3：记为 ``failure_kind="llm"`` 并触发重试；
    * Step 4：写入 ``compose_error``，不猜测或修补任何字段。
    """


class BatchInferenceError(RuntimeError):
    """批量推理部分失败；``outcomes`` 按输入顺序保留成功结果与异常。"""

    def __init__(self, outcomes: list[InferenceResult | Exception]) -> None:
        self.outcomes = tuple(outcomes)
        failed = sum(isinstance(item, Exception) for item in outcomes)
        super().__init__(f"批量推理 {failed}/{len(outcomes)} 项失败")


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"\A\s*```(?:json)?[ \t]*\r?\n?|\r?\n?[ \t]*```\s*\Z", re.IGNORECASE)
_CONTROL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class InferenceResult:
    """一次推理的完整文本和计量信息。"""

    text: str
    usage: Mapping[str, object] = field(hash=False)
    model: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", _freeze(self.usage))


@overload
def infer(
    prompt: str,
    *,
    system_prompt: str | None = None,
    history: Sequence[Message] | None = None,
    llm_config: Mapping[str, Any] | None = None,
    on_chunk: ChunkHandler | None = None,
) -> InferenceResult: ...


@overload
def infer(
    prompt: list[str],
    *,
    system_prompt: str | None = None,
    history: Sequence[Message] | None = None,
    llm_config: Mapping[str, Any] | None = None,
    on_chunk: ChunkHandler | None = None,
) -> list[InferenceResult]: ...


def infer(
    prompt: str | list[str],
    *,
    system_prompt: str | None = None,
    history: Sequence[Message] | None = None,
    llm_config: Mapping[str, Any] | None = None,
    on_chunk: ChunkHandler | None = None,
) -> InferenceResult | list[InferenceResult]:
    """完成单条或并发批量推理；传入 ``on_chunk`` 时启用流式读取。"""

    prompts = [prompt] if isinstance(prompt, str) else prompt
    if not prompts or any(not item.strip() for item in prompts):
        raise ValueError("prompt 不能为空")

    config = llm_config or {}
    # codex 后端走本机已登录的 Codex CLI，用于 API 账户配额不足时；
    # 阶段 prompt 和返回契约完全不变。它没有 max_tokens 概念，该配置只影响 api 后端。
    if str(config.get("backend") or "api") == "codex":
        return _infer_codex(
            prompt,
            system_prompt=system_prompt,
            history=history,
            config=config,
            on_chunk=on_chunk,
        )
    client = _client(config)
    client.stream = False
    client.client  # 在启动工作线程前初始化并验证共享连接池。
    messages = list(history or ())
    parameters: dict[str, object] = {"temperature": float(config.get("temperature", 0.2))}
    if config.get("max_tokens") is not None:
        parameters["max_tokens"] = int(config["max_tokens"])

    def run(index: int, item: str) -> InferenceResult:
        request = ([{"role": "system", "content": system_prompt}] if system_prompt else [])
        request += [dict(message) for message in messages]
        request.append({"role": "user", "content": item})
        callback = (lambda text: on_chunk(index, text)) if on_chunk else None
        text, usage = client.complete_messages(request, on_delta=callback, **parameters)
        return InferenceResult(text, usage, client.model)

    if isinstance(prompt, str):
        return run(0, prompt)

    max_workers = int(config.get("max_concurrency", 8))
    if max_workers < 1:
        raise ValueError("llm.max_concurrency 必须大于 0")
    return _run_batch(run, prompts, max_workers)


def _infer_codex(
    prompt: str | list[str],
    *,
    system_prompt: str | None,
    history: Sequence[Message] | None,
    config: Mapping[str, Any],
    on_chunk: ChunkHandler | None,
) -> InferenceResult | list[InferenceResult]:
    """通过本机已登录的 Codex CLI 推理；沙箱只读，工作目录用临时目录隔离。

    不接受 ``max_tokens``（CLI 没有该参数），其余配置语义与 api 后端一致。
    """
    client = CodexAgentClient(
        model=str(config["model"]) if config.get("model") else None,
        timeout_seconds=int(config.get("timeout_seconds", 1800)),
        sandbox="read-only",
    )
    context = ""
    if system_prompt:
        context += f"<system>\n{system_prompt}\n</system>\n"
    for message in history or ():
        context += f"<{message['role']}>\n{message['content']}\n</{message['role']}>\n"

    def run(index: int, item: str) -> InferenceResult:
        request = (
            "不要使用工具或读取文件，只根据下面请求中已经提供的信息作答。"
            "最终响应只包含请求要求的内容。\n<request>\n"
            + context
            + item
        )
        with tempfile.TemporaryDirectory(prefix="tool-graph-llm-") as temporary:
            text = client.run(request, working_directory=Path(temporary))
        if on_chunk is not None:
            on_chunk(index, text)
        return InferenceResult(text, {}, client.model or "codex-default")

    if isinstance(prompt, str):
        return run(0, prompt)
    max_workers = int(config.get("max_concurrency", 1))
    if max_workers < 1:
        raise ValueError("llm.max_concurrency 必须大于 0")
    return _run_batch(run, prompt, max_workers)


def _run_batch(
    run: Callable[[int, str], InferenceResult],
    prompts: list[str],
    max_workers: int,
) -> list[InferenceResult]:
    with ThreadPoolExecutor(max_workers=min(max_workers, len(prompts))) as executor:
        futures = [executor.submit(run, index, prompt) for index, prompt in enumerate(prompts)]
        outcomes: list[InferenceResult | Exception] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as error:
                outcomes.append(error)
    if any(isinstance(item, Exception) for item in outcomes):
        raise BatchInferenceError(outcomes)
    return [item for item in outcomes if isinstance(item, InferenceResult)]


def parse_json_object(text: str) -> dict[str, Any]:
    """从 LLM 回复中提取唯一的顶层 JSON object。

    容忍 ``` 围栏以及 object 前后的解释性文字；不容忍顶层不是 object、
    结构被截断或根本没有 object。失败时抛出 :class:`MalformedJSONError`，
    消息带原始回复的首尾片段，用于区分"模型没按格式回答"和"输出被截断"
    这两种需要不同处置的情况。
    """
    # reasoning 模型（如 gpt-5.6-luna）会输出 <think>…</think>，其中常含花括号，
    # 不先剥离会让下面的括号匹配取到思考过程里的 object。
    stripped = _FENCE.sub("", _THINK.sub("", text).strip())
    if stripped[:1] == "[":
        raise MalformedJSONError(f"顶层是数组而不是 object：{_excerpt(text)}")
    start = stripped.find("{")
    if start < 0:
        raise MalformedJSONError(f"回复中没有 JSON object：{_excerpt(text)}")
    end = _match_object(stripped, start)
    if end < 0:
        raise MalformedJSONError(f"JSON object 未闭合，可能被截断：{_excerpt(text)}")
    candidate = stripped[start:end]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as error:
        # 模型常在多行 reason 里直接写裸换行；严格 JSON 不允许，但这属于格式瑕疵
        # 而非语义错误，转义后重试一次。仍失败才视为格式错误。
        try:
            value = json.loads(_escape_control_characters(candidate))
        except json.JSONDecodeError:
            raise MalformedJSONError(
                f"JSON 解析失败（{error}）：{_excerpt(text)}"
            ) from error
    if not isinstance(value, dict):
        raise MalformedJSONError(f"顶层不是 JSON object：{_excerpt(text)}")
    return value


def _escape_control_characters(text: str) -> str:
    """转义字符串字面量内部的裸控制字符，其余位置原样保留。"""
    output: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            if char in _CONTROL_ESCAPES:
                output.append(_CONTROL_ESCAPES[char])
                continue
        elif char == '"':
            in_string = True
        output.append(char)
    return "".join(output)


def _match_object(text: str, start: int) -> int:
    """返回 ``text[start]`` 处 object 的结束下标（不含）；未闭合时返回 ``-1``。"""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _excerpt(text: str, limit: int = 200) -> str:
    """折叠空白并只保留首尾片段，避免超长回复淹没错误信息。"""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit * 2:
        return repr(collapsed)
    return repr(f"{collapsed[:limit]} …… {collapsed[-limit:]}")


def _client(config: Mapping[str, Any]) -> LLMClient:
    """读取环境默认值，并应用本次运行明确给出的覆盖项。"""

    backend = str(config.get("backend") or "api")
    if backend != "api":
        raise ValueError(f"tool_graph.llm 仅支持 api 后端，不支持 {backend!r}")
    client = LLMClient.from_environment()
    if "model" in config:
        client.model = str(config["model"] or "")
    if "base_url" in config:
        client.base_url = str(config["base_url"] or "")
    if "api_key_env" in config:
        client.api_key = os.environ.get(str(config["api_key_env"]), "")
    if "timeout_seconds" in config:
        client.timeout_seconds = int(config["timeout_seconds"])
    return client
