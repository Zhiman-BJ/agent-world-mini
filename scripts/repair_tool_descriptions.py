#!/usr/bin/env python3
"""Create tools_repaired.json files with complete, context-aware descriptions.

The source tools.json files are never modified.  Existing environment field
descriptions and stable MCP result-envelope descriptions are reused first; an
LLM then reviews every tool's public description and fills the remaining schema
nodes while seeing the complete environment and executable tool definition.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from dotenv import dotenv_values
from openai import OpenAI


DEFAULT_ROOTS = (
    Path("/data1/agent_world/toolgen_environments/delivery"),
    Path("/data1/agent_world/toolgen_semiconductor_expanded/delivery"),
)
DEFAULT_ENV_FILE = Path(
    "/data1/home/sunshuo/AgenticDataGeneration/OmniaBench/EnvGen/.env"
)
DEFAULT_KIMI_ENV_FILE = Path("/home/sunshuo/.config/agent-world/kimi-k3.env")
DEFAULT_PROGRESS_DIR = Path(
    "/data1/agent_world/aw-data/tool_description_repair_20261004"
)

SCHEMA_CONTAINER_KEYS = {
    "properties",
    "patternProperties",
    "$defs",
    "definitions",
    "dependentSchemas",
}
SCHEMA_SINGLE_KEYS = {
    "items",
    "additionalProperties",
    "contains",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
}
SCHEMA_LIST_KEYS = {"prefixItems", "oneOf", "anyOf", "allOf"}

COMMON_FIELD_DESCRIPTIONS = {
    "limit": "本次最多返回的记录数量。",
    "offset": "从匹配结果开头跳过的记录数量，用于分页。",
    "page_size": "每页最多返回的记录数量。",
    "descending": "是否按降序排列；为 false 时按升序排列。",
    "sort_by": "用于确定结果顺序的字段或排序规则。",
    "order_by": "按顺序应用的结果排序规则。",
    "filters": "用于缩小处理范围的字段筛选条件；未提供的条件不参与筛选。",
    "group_by": "用于划分统计分组的一个或多个字段。",
    "output_path": "输出文件在工具允许写入范围内的相对路径。",
    "output_file": "输出文件在工具允许写入范围内的相对路径。",
    "output_name": "新生成输出的文件名或稳定名称。",
    "output_format": "要求生成或返回的输出格式。",
    "relative_path": "目标文件相对于已声明文件作用域根目录的路径。",
    "file_path": "目标文件在已声明文件作用域中的路径。",
    "scope_id": "文件作用域的稳定标识，用于限定可访问的目录边界。",
    "record_set_id": "环境中目标记录集的稳定标识。",
    "total": "符合当前查询或统计条件的结果总数。",
    "total_count": "符合当前查询或统计条件的记录总数。",
    "returned_count": "本次响应实际返回的记录数量。",
    "has_more": "当前分页之后是否仍有尚未返回的匹配结果。",
    "count": "当前对象、分组或结果集合中的项目数量。",
    "size_bytes": "文件或数据对象的大小，单位为字节。",
    "sha256": "文件或数据内容的 SHA-256 校验摘要。",
}

RESULT_FIELD_DESCRIPTIONS = {
    "data": "工具成功执行时返回的结构化业务结果。",
    "error": "工具未能完成请求时返回的结构化错误详情。",
    "code": "稳定的机器可读错误代码，用于区分失败原因。",
    "path": "与错误相关的输入字段或数据位置；无法定位到具体位置时可为空。",
    "message": "面向调用者的错误说明，包含失败原因和必要的修正线索。",
    "retryable": "在不修改请求参数或环境状态的情况下，重试是否可能成功。",
}

GENERIC_DESCRIPTION_PATTERNS = (
    re.compile(r"^数组元素[。.]?$"),
    re.compile(r"^(string|number|integer|boolean|object|array)[。.]?$", re.I),
    re.compile(r"^业务字段[。.]?$"),
    re.compile(r"^字段[。.]?$"),
)


@dataclass(frozen=True)
class SchemaTarget:
    pointer: str
    field_name: str
    node: dict[str, Any]
    parent_pointer: str


def json_dump(value: Any, *, pretty: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )


def pointer_escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def pointer_join(base: str, *parts: str) -> str:
    suffix = "/".join(pointer_escape(part) for part in parts)
    return f"{base}/{suffix}" if base else f"/{suffix}"


def iter_schema_targets(
    schema: Any,
    pointer: str,
    *,
    parent_pointer: str = "",
) -> Iterator[SchemaTarget]:
    """Yield property and collection-item schema nodes requiring descriptions."""
    if not isinstance(schema, dict):
        return

    for container_key in SCHEMA_CONTAINER_KEYS:
        children = schema.get(container_key)
        if not isinstance(children, dict):
            continue
        for name, child in children.items():
            if not isinstance(child, dict):
                continue
            child_pointer = pointer_join(pointer, container_key, str(name))
            yield SchemaTarget(child_pointer, str(name), child, pointer)
            yield from iter_schema_targets(
                child, child_pointer, parent_pointer=pointer
            )

    for child_key in SCHEMA_SINGLE_KEYS:
        child = schema.get(child_key)
        if not isinstance(child, dict):
            continue
        child_pointer = pointer_join(pointer, child_key)
        yield SchemaTarget(child_pointer, f"<{child_key}>", child, pointer)
        yield from iter_schema_targets(
            child, child_pointer, parent_pointer=pointer
        )

    for child_key in SCHEMA_LIST_KEYS:
        children = schema.get(child_key)
        if not isinstance(children, list):
            continue
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            child_pointer = pointer_join(pointer, child_key, str(index))
            # A union branch is not a named field. Its nested fields are targets.
            yield from iter_schema_targets(
                child, child_pointer, parent_pointer=pointer
            )


def schema_targets(tool: dict[str, Any]) -> list[SchemaTarget]:
    targets: list[SchemaTarget] = []
    for schema_name in ("inputSchema", "outputSchema"):
        schema = tool.get(schema_name)
        if isinstance(schema, dict):
            targets.extend(iter_schema_targets(schema, f"/{schema_name}"))
    return targets


def get_pointer(document: Any, pointer: str) -> Any:
    value = document
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def description_is_missing(node: dict[str, Any]) -> bool:
    return not isinstance(node.get("description"), str) or not node["description"].strip()


def description_is_generic(description: str) -> bool:
    text = description.strip()
    return any(pattern.fullmatch(text) for pattern in GENERIC_DESCRIPTION_PATTERNS)


def strip_schema_descriptions(schema: Any) -> Any:
    """Remove schema annotations without dropping a property named description."""
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "description":
            continue
        if key in SCHEMA_CONTAINER_KEYS and isinstance(value, dict):
            result[key] = {
                child_name: strip_schema_descriptions(child_schema)
                for child_name, child_schema in value.items()
            }
        elif key in SCHEMA_SINGLE_KEYS and isinstance(value, dict):
            result[key] = strip_schema_descriptions(value)
        elif key in SCHEMA_LIST_KEYS and isinstance(value, list):
            result[key] = [
                strip_schema_descriptions(child) if isinstance(child, dict) else copy.deepcopy(child)
                for child in value
            ]
        else:
            result[key] = copy.deepcopy(value)
    return result


def non_description_projection(document: dict[str, Any]) -> dict[str, Any]:
    """Project away only the description fields this repair is allowed to edit."""
    projected = copy.deepcopy(document)
    tools = projected.get("tools")
    if not isinstance(tools, list):
        return projected
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool.pop("description", None)
        for schema_name in ("inputSchema", "outputSchema"):
            if schema_name in tool:
                tool[schema_name] = strip_schema_descriptions(tool[schema_name])
    return projected


def environment_field_catalog(
    environment: dict[str, Any],
) -> tuple[dict[str, dict[str, str]], dict[str, list[str]]]:
    by_resource: dict[str, dict[str, str]] = {}
    global_fields: dict[str, list[str]] = defaultdict(list)
    for record_set in environment.get("record_sets", []):
        if not isinstance(record_set, dict):
            continue
        resource_id = record_set.get("record_set_id")
        resource_fields: dict[str, str] = {}
        fields = record_set.get("fields", {})
        if isinstance(fields, dict):
            for name, field in fields.items():
                if not isinstance(field, dict):
                    continue
                description = field.get("description")
                if not isinstance(description, str) or not description.strip():
                    continue
                clean = description.strip()
                resource_fields[str(name)] = clean
                if clean not in global_fields[str(name)]:
                    global_fields[str(name)].append(clean)
        if isinstance(resource_id, str):
            by_resource[resource_id] = resource_fields
    return by_resource, global_fields


def target_resource_descriptions(
    tool: dict[str, Any],
    by_resource: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    descriptions: dict[str, list[str]] = defaultdict(list)
    usage = tool.get("usageConditions", {})
    resources = usage.get("targetResources", []) if isinstance(usage, dict) else []
    for resource_id in resources if isinstance(resources, list) else []:
        for name, description in by_resource.get(str(resource_id), {}).items():
            if description not in descriptions[name]:
                descriptions[name].append(description)
    return descriptions


def success_description(node: dict[str, Any]) -> str | None:
    if node.get("const") is True:
        return "表示工具已成功完成请求且 data 中包含可用结果。"
    if node.get("const") is False:
        return "表示工具未完成请求且 error 中包含失败详情。"
    return None


def parent_description(tool: dict[str, Any], pointer: str) -> str | None:
    try:
        parent = get_pointer(tool, pointer)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if isinstance(parent, dict):
        description = parent.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip().rstrip("。.")
    return None


def deterministic_description(
    tool: dict[str, Any],
    target: SchemaTarget,
    resource_fields: dict[str, list[str]],
    global_fields: dict[str, list[str]],
) -> str | None:
    name = target.field_name
    if name == "success":
        return success_description(target.node)
    if name in RESULT_FIELD_DESCRIPTIONS:
        return RESULT_FIELD_DESCRIPTIONS[name]

    resource_matches = resource_fields.get(name, [])
    if len(resource_matches) == 1:
        return resource_matches[0]
    global_matches = global_fields.get(name, [])
    if len(global_matches) == 1:
        return global_matches[0]
    if name in COMMON_FIELD_DESCRIPTIONS:
        return COMMON_FIELD_DESCRIPTIONS[name]

    if name == "<items>":
        parent = parent_description(tool, target.parent_pointer)
        if parent:
            return f"{parent}中的单个数组元素。"
    if name == "<additionalProperties>":
        parent = parent_description(tool, target.parent_pointer)
        if parent:
            return f"{parent}中每个动态键对应的值。"
    if name == "<propertyNames>":
        parent = parent_description(tool, target.parent_pointer)
        if parent:
            return f"{parent}中允许使用的动态键名。"
    return None


def seed_descriptions(
    environment: dict[str, Any], tool: dict[str, Any]
) -> list[str]:
    by_resource, global_fields = environment_field_catalog(environment)
    resource_fields = target_resource_descriptions(tool, by_resource)
    seeded: list[str] = []
    # Repeat until item descriptions can inherit a newly filled parent.
    for _ in range(2):
        for target in schema_targets(tool):
            if not description_is_missing(target.node):
                continue
            description = deterministic_description(
                tool, target, resource_fields, global_fields
            )
            if description:
                target.node["description"] = description
                seeded.append(target.pointer)
    return seeded


def unresolved_pointers(tool: dict[str, Any]) -> list[str]:
    return [
        target.pointer
        for target in schema_targets(tool)
        if description_is_missing(target.node)
        or description_is_generic(str(target.node.get("description", "")))
    ]


def needs_top_review(tool: dict[str, Any]) -> bool:
    description = str(tool.get("description", "")).strip()
    if len(description) < 25 or description_is_generic(description):
        return True
    usage = tool.get("usageConditions")
    if not isinstance(usage, dict):
        return False
    side_effects = usage.get("sideEffects", [])
    has_side_effect = isinstance(side_effects, list) and bool(side_effects)
    side_effect_words = (
        "写",
        "更新",
        "删除",
        "创建",
        "生成",
        "导出",
        "修改",
        "write",
        "update",
        "delete",
        "create",
        "export",
        "modify",
    )
    if has_side_effect and not any(word in description.lower() for word in side_effect_words):
        return True
    return False


def load_model_config(
    env_file: Path,
    slot: int,
    *,
    credential_profile: str,
    kimi_env_file: Path,
) -> tuple[str, str, str]:
    if credential_profile in {"kimi-primary", "kimi-backup"}:
        values = dotenv_values(kimi_env_file)
        key_name = (
            "KIMI_API_KEY"
            if credential_profile == "kimi-primary"
            else "KIMI_API_KEY_BACKUP"
        )
        api_key = str(values.get(key_name) or "").strip()
        base_url = str(values.get("KIMI_BASE_URL") or "").strip()
        if not api_key or not base_url:
            raise RuntimeError(
                f"Kimi 配置 {kimi_env_file} 缺少 {key_name} 或 KIMI_BASE_URL。"
            )
        return api_key, base_url, "kimi-k3"

    values = dotenv_values(env_file)
    api_key = str(values.get(f"OPENAI_API_KEY{slot}") or "").strip()
    base_url = str(values.get(f"OPENAI_BASE_URL{slot}") or "").strip()
    model_value = values.get(f"OPENAI_model_name{slot}") or values.get(
        "OPENAI_model_name1"
    )
    model = str(model_value or "").split("#", 1)[0].strip()
    if not api_key or not base_url or not model:
        raise RuntimeError(f"模型配置 slot {slot} 缺少 key、base URL 或 model。")
    return api_key, base_url, model


def build_prompt(
    environment: dict[str, Any],
    tools: list[dict[str, Any]],
    work_items: list[dict[str, Any]],
) -> str:
    payload = {
        "environment": environment,
        "tools": tools,
        "requested_repairs": work_items,
    }
    return (
        "请审查下面同一环境中的完整环境定义和完整工具定义。工具定义包含公开 schema、"
        "usageConditions 以及 internal.code。只生成描述文本，不得建议或修改任何类型、约束、"
        "枚举、字段名、代码或业务行为。\n\n"
        "对每个工具执行两件事：\n"
        "1. 阅读现有顶层 description、usageConditions、输入输出 schema 和实现代码。只有当"
        "现有 description 不完整、过短、模板化或遗漏关键限制/副作用时，返回更完整、自然、"
        "具体的 description；否则返回 null。不要暴露 internal.code、内部绝对路径或实现细节。\n"
        "2. 为 requested_schema_pointers 中的每个 JSON Pointer 返回非空字段说明。说明必须"
        "准确表达业务含义、单位、可选性或约束中真正重要的部分，不能只翻译字段名，也不能使用"
        "“业务字段”“数组元素”“字符串值”一类空泛模板。对象内字段和数组元素必须结合父对象"
        "语义。语言应与该工具现有描述及环境一致。\n\n"
        "严格返回一个 JSON 对象，格式如下：\n"
        '{"tools":[{"name":"工具名","tool_description":null或字符串,'
        '"schema_descriptions":{"/inputSchema/properties/x":"说明"}}]}\n'
        "每个 requested pointer 必须且只能出现一次；不得返回未请求的 pointer。工具顺序与"
        "输入一致。不要输出 Markdown。\n\n"
        f"输入：\n{json_dump(payload)}"
    )


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("模型响应根节点不是 JSON object。")
    return value


def retry_delay(error: Exception, attempt: int) -> float:
    message = str(error).lower()
    if "gateway_error" in message or "error code: 403" in message:
        return min(180.0, 10.0 * (2.0**attempt)) + random.uniform(0.0, 5.0)
    return min(60.0, 2.0**attempt) + random.random()


def request_repairs(
    *,
    api_key: str,
    base_url: str,
    model: str,
    environment: dict[str, Any],
    original_tools: list[dict[str, Any]],
    work_items: list[dict[str, Any]],
    attempts: int,
    timeout: float,
    max_output_tokens: int,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    prompt = build_prompt(environment, original_tools, work_items)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=0,
            ) as client:
                request_options: dict[str, Any] = dict(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是工具契约编辑。必须忠实于提供的环境、JSON Schema 和执行代码，"
                                "只返回要求的 JSON。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0,
                    max_tokens=max_output_tokens,
                    stream=True,
                )
                if reasoning_effort:
                    request_options["reasoning_effort"] = reasoning_effort
                stream = client.chat.completions.create(**request_options)
                content_parts: list[str] = []
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    content = chunk.choices[0].delta.content
                    if isinstance(content, str):
                        content_parts.append(content)
                content = "".join(content_parts)
            return parse_json_object(content)
        except Exception as exc:  # noqa: BLE001 - retry transport/model failures
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(retry_delay(exc, attempt))
    assert last_error is not None
    raise RuntimeError(f"模型请求在 {attempts} 次尝试后失败：{last_error}") from last_error


def request_validated_repairs(
    *,
    api_key: str,
    base_url: str,
    model: str,
    environment: dict[str, Any],
    original_tools: list[dict[str, Any]],
    work_items: list[dict[str, Any]],
    attempts: int,
    timeout: float,
    max_output_tokens: int,
    reasoning_effort: str | None,
) -> dict[str, tuple[str | None, dict[str, str]]]:
    """Retry transport errors and structurally incomplete model responses alike."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = request_repairs(
                api_key=api_key,
                base_url=base_url,
                model=model,
                environment=environment,
                original_tools=original_tools,
                work_items=work_items,
                attempts=1,
                timeout=timeout,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
            )
            return validate_response(response, work_items)
        except Exception as exc:  # noqa: BLE001 - retry invalid structured output
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(retry_delay(exc, attempt))
    assert last_error is not None
    raise RuntimeError(
        f"模型响应在 {attempts} 次尝试后仍未通过结构校验：{last_error}"
    ) from last_error


def validate_response(
    response: dict[str, Any], work_items: list[dict[str, Any]]
) -> dict[str, tuple[str | None, dict[str, str]]]:
    expected = {item["name"]: item for item in work_items}
    returned = response.get("tools")
    if not isinstance(returned, list):
        raise ValueError("模型响应缺少 tools 数组。")
    parsed: dict[str, tuple[str | None, dict[str, str]]] = {}
    for item in returned:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("模型响应包含无效工具项。")
        name = item["name"]
        if name not in expected or name in parsed:
            raise ValueError(f"模型响应包含未知或重复工具：{name}")
        top = item.get("tool_description")
        if top is not None and (not isinstance(top, str) or not top.strip()):
            raise ValueError(f"{name} 的 tool_description 无效。")
        descriptions = item.get("schema_descriptions")
        if not isinstance(descriptions, dict):
            raise ValueError(f"{name} 缺少 schema_descriptions。")
        expected_pointers = set(expected[name]["requested_schema_pointers"])
        if not expected_pointers.issubset(descriptions):
            missing = sorted(expected_pointers - set(descriptions))[:5]
            raise ValueError(
                f"{name} 缺少请求的 pointer；missing={missing}"
            )
        clean: dict[str, str] = {}
        for pointer in expected_pointers:
            description = descriptions[pointer]
            if not isinstance(description, str) or not description.strip():
                raise ValueError(f"{name} 的 {pointer} 说明为空。")
            if description_is_generic(description):
                raise ValueError(f"{name} 的 {pointer} 说明仍然模板化：{description}")
            clean[pointer] = description.strip()
        parsed[name] = (top.strip() if isinstance(top, str) else None, clean)
    if set(parsed) != set(expected):
        raise ValueError(f"模型响应缺少工具：{sorted(set(expected) - set(parsed))}")
    return parsed


def chunk_tools(
    environment: dict[str, Any],
    original_tools: list[dict[str, Any]],
    repaired_tools: list[dict[str, Any]],
    max_chars: int,
) -> Iterator[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    environment_chars = len(json_dump(environment))
    originals: list[dict[str, Any]] = []
    work_items: list[dict[str, Any]] = []
    size = environment_chars
    for original, repaired in zip(original_tools, repaired_tools, strict=True):
        pointers = unresolved_pointers(repaired)
        requested = set(pointers)
        item = {
            "name": original["name"],
            "review_tool_description": True,
            "requested_schema_pointers": pointers,
            "current_seeded_descriptions": {
                target.pointer: target.node.get("description")
                for target in schema_targets(repaired)
                if not description_is_missing(target.node)
                and target.pointer not in requested
            },
        }
        item_size = len(json_dump(original)) + len(json_dump(item))
        if originals and size + item_size > max_chars:
            yield originals, work_items
            originals, work_items, size = [], [], environment_chars
        originals.append(original)
        work_items.append(item)
        size += item_size
    if originals:
        yield originals, work_items


def apply_model_repairs(
    repaired_by_name: dict[str, dict[str, Any]],
    parsed: dict[str, tuple[str | None, dict[str, str]]],
) -> None:
    for name, (top_description, schema_descriptions) in parsed.items():
        tool = repaired_by_name[name]
        if top_description is not None:
            tool["description"] = top_description
        for pointer, description in schema_descriptions.items():
            node = get_pointer(tool, pointer)
            if not isinstance(node, dict):
                raise ValueError(f"{name} 的 pointer 未指向 schema object：{pointer}")
            node["description"] = description


def validate_repaired(
    original_document: dict[str, Any], repaired_document: dict[str, Any]
) -> dict[str, int]:
    if non_description_projection(original_document) != non_description_projection(
        repaired_document
    ):
        raise ValueError("剥离 description 后 repaired 与原始文件不相等。")
    original_tools = original_document.get("tools")
    repaired_tools = repaired_document.get("tools")
    if not isinstance(original_tools, list) or not isinstance(repaired_tools, list):
        raise ValueError("tools 必须为数组。")
    if [tool.get("name") for tool in original_tools] != [
        tool.get("name") for tool in repaired_tools
    ]:
        raise ValueError("工具名称或顺序发生变化。")
    field_count = 0
    for tool in repaired_tools:
        if not isinstance(tool.get("description"), str) or not tool["description"].strip():
            raise ValueError(f"工具 {tool.get('name')} 的顶层 description 为空。")
        for target in schema_targets(tool):
            field_count += 1
            if description_is_missing(target.node):
                raise ValueError(
                    f"工具 {tool.get('name')} 的字段 {target.pointer} 缺少 description。"
                )
            if description_is_generic(str(target.node["description"])):
                raise ValueError(
                    f"工具 {tool.get('name')} 的字段 {target.pointer} 仍为模板说明："
                    f"{target.node['description']}"
                )
    return {"tools": len(repaired_tools), "schema_nodes": field_count}


def _write_json_file(path: Path, document: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sudo_install_atomic(path: Path, document: dict[str, Any]) -> None:
    owner = path.parent.stat()
    fd, local_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(fd)
    local_path = Path(local_temporary)
    remote_temporary = path.parent / (
        f".{path.name}.{os.getpid()}.{random.randrange(1_000_000_000):09d}.tmp"
    )
    try:
        _write_json_file(local_path, document)
        subprocess.run(
            [
                "sudo",
                "-n",
                "install",
                "-o",
                str(owner.st_uid),
                "-g",
                str(owner.st_gid),
                "-m",
                "0664",
                str(local_path),
                str(remote_temporary),
            ],
            check=True,
        )
        subprocess.run(
            ["sudo", "-n", "mv", "-f", str(remote_temporary), str(path)],
            check=True,
        )
    finally:
        local_path.unlink(missing_ok=True)
        subprocess.run(
            ["sudo", "-n", "rm", "-f", str(remote_temporary)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def atomic_write_json(
    path: Path, document: dict[str, Any], *, sudo_write: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
    except PermissionError:
        if not sudo_write:
            raise
        _sudo_install_atomic(path, document)
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except PermissionError:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        if not sudo_write:
            raise
        _sudo_install_atomic(path, document)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def append_progress(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_dump(record) + "\n")


def process_environment(
    environment_dir: Path,
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_chars: int,
    attempts: int,
    timeout: float,
    max_output_tokens: int,
    reasoning_effort: str | None,
    overwrite: bool,
    sudo_write: bool,
) -> dict[str, Any]:
    source_path = environment_dir / "tools" / "tools.json"
    output_path = environment_dir / "tools" / "tools_repaired.json"
    environment_path = environment_dir / "environment" / "environment.json"
    if output_path.exists() and not overwrite:
        original_document = json.loads(source_path.read_text(encoding="utf-8"))
        repaired_document = json.loads(output_path.read_text(encoding="utf-8"))
        counts = validate_repaired(original_document, repaired_document)
        return {"status": "skipped_valid", **counts}

    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    original_document = json.loads(source_path.read_text(encoding="utf-8"))
    original_tools = original_document.get("tools")
    if not isinstance(original_tools, list):
        raise ValueError(f"{source_path} 缺少 tools 数组。")
    repaired_document = copy.deepcopy(original_document)
    repaired_tools = repaired_document["tools"]

    seeded_count = 0
    for tool in repaired_tools:
        seeded_count += len(seed_descriptions(environment, tool))

    repaired_by_name = {tool["name"]: tool for tool in repaired_tools}
    batch_count = 0
    model_description_count = 0
    top_changed = 0
    for batch_originals, work_items in chunk_tools(
        environment, original_tools, repaired_tools, max_chars
    ):
        parsed = request_validated_repairs(
            api_key=api_key,
            base_url=base_url,
            model=model,
            environment=environment,
            original_tools=batch_originals,
            work_items=work_items,
            attempts=attempts,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        for name, (top, descriptions) in parsed.items():
            model_description_count += len(descriptions)
            if top is not None and top != repaired_by_name[name].get("description"):
                top_changed += 1
        apply_model_repairs(repaired_by_name, parsed)
        batch_count += 1

    counts = validate_repaired(original_document, repaired_document)
    atomic_write_json(output_path, repaired_document, sudo_write=sudo_write)
    persisted_document = json.loads(output_path.read_text(encoding="utf-8"))
    validate_repaired(original_document, persisted_document)
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return {
        "status": "written",
        **counts,
        "seeded_descriptions": seeded_count,
        "model_descriptions": model_description_count,
        "top_descriptions_changed": top_changed,
        "model_batches": batch_count,
        "source_sha256": source_sha,
        "output": str(output_path),
    }


def discover_environments(roots: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    for root in roots:
        environments_root = root / "environments"
        for candidate in sorted(environments_root.iterdir()):
            if (
                candidate.is_dir()
                and (candidate / "environment" / "environment.json").is_file()
                and (candidate / "tools" / "tools.json").is_file()
            ):
                result.append(candidate)
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, dest="roots")
    parser.add_argument("--environment", action="append", default=[])
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--model-slot", type=int, default=2)
    parser.add_argument(
        "--credential-profile",
        choices=("openai-slot", "kimi-primary", "kimi-backup"),
        default="openai-slot",
    )
    parser.add_argument("--kimi-env-file", type=Path, default=DEFAULT_KIMI_ENV_FILE)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-request-chars", type=int, default=180_000)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--max-output-tokens", type=int, default=12_000)
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "max"),
        default=None,
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--sudo-write",
        action="store_true",
        help="使用无交互 sudo 原子写入当前账号无写权限的交付目录。",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress-dir", type=Path, default=DEFAULT_PROGRESS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    roots = tuple(args.roots or DEFAULT_ROOTS)
    environments = discover_environments(roots)
    if args.environment:
        selected = set(args.environment)
        environments = [path for path in environments if path.name in selected]
        missing = selected - {path.name for path in environments}
        if missing:
            raise SystemExit(f"未找到环境：{sorted(missing)}")

    if args.dry_run:
        print(json_dump({"environments": len(environments), "roots": [str(r) for r in roots]}, pretty=True))
        return 0

    api_key, base_url, model = load_model_config(
        args.env_file,
        args.model_slot,
        credential_profile=args.credential_profile,
        kimi_env_file=args.kimi_env_file,
    )
    progress_file = args.progress_dir / "progress.jsonl"
    failures_file = args.progress_dir / "failures.jsonl"
    summary: dict[str, int] = defaultdict(int)

    def run_one(path: Path) -> tuple[Path, dict[str, Any]]:
        result = process_environment(
            path,
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_chars=args.max_request_chars,
            attempts=args.attempts,
            timeout=args.timeout,
            max_output_tokens=args.max_output_tokens,
            reasoning_effort=args.reasoning_effort,
            overwrite=args.overwrite,
            sudo_write=args.sudo_write,
        )
        return path, result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, path): path for path in environments}
        for future in as_completed(futures):
            path = futures[future]
            try:
                _, result = future.result()
                record = {"environment": path.name, **result}
                append_progress(progress_file, record)
                summary[result["status"]] += 1
                print(json_dump(record), flush=True)
            except Exception as exc:  # noqa: BLE001 - preserve other environments
                record = {
                    "environment": path.name,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                append_progress(failures_file, record)
                summary["failed"] += 1
                print(json_dump(record), file=sys.stderr, flush=True)

    print(json_dump({"summary": dict(summary)}, pretty=True))
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
