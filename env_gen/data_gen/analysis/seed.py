"""最终环境 Seed 的读取、选择和审计标识。

DataGen 不把 Seed 改写成另一份“调研请求”。这里仅验证完整 Seed 集合，按
``global_id`` 选择原始对象，并计算后续中间产物共同引用的稳定摘要。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def canonical_json_sha256(value: object) -> str:
    """计算与排版和 key 顺序无关的 JSON SHA-256。"""

    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _expected_global_id(seed: dict[str, Any]) -> str | None:
    basic = seed.get("environment", {}).get("basic_info", {})
    if not isinstance(basic, dict):
        return None
    source = basic.get("source")
    name = basic.get("name")
    index = basic.get("index")
    if not isinstance(source, str) or not isinstance(name, str) or not isinstance(index, int):
        return None
    normalized_name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return f"{source}_{normalized_name}_{index}"


def load_selected_seed(
    seed_path: Path,
    global_id: str,
    validation_schema_path: Path,
) -> tuple[dict[str, Any], str]:
    """验证最终 Seed 集合并返回未经语义转换的目标对象及其摘要。"""

    try:
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Seed 文件不存在：{seed_path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Seed 文件无法读取：{seed_path}: {error}") from error
    if not isinstance(payload, list):
        raise ValueError("最终 Seed 文件根节点必须是数组")

    schema = json.loads(validation_schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        messages: list[str] = []
        for error in errors[:12]:
            pointer = "$"
            for part in error.absolute_path:
                pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
            messages.append(f"{pointer}: {error.message}")
        raise ValueError("Seed 不符合 validation Schema：" + "; ".join(messages))

    ids = [item.get("global_id") for item in payload if isinstance(item, dict)]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ValueError(f"Seed 集合存在重复 global_id：{duplicates[:10]}")

    matches = [
        item
        for item in payload
        if isinstance(item, dict) and item.get("global_id") == global_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"global_id 必须唯一匹配一条 Seed，实际匹配 {len(matches)} 条：{global_id}"
        )
    selected = dict(matches[0])
    expected = _expected_global_id(selected)
    if expected != global_id:
        raise ValueError(
            f"Seed global_id 与 source/name/index 不一致：声明 {global_id}，应为 {expected}"
        )

    tool_names = [
        item.get("name")
        for item in selected.get("init_ref_tools", [])
        if isinstance(item, dict)
    ]
    duplicate_tools = sorted({value for value in tool_names if tool_names.count(value) > 1})
    if duplicate_tools:
        raise ValueError(f"同一 Seed 的参考工具名重复：{duplicate_tools[:10]}")
    return selected, canonical_json_sha256(selected)


def core_entity_hints(
    seed: dict[str, Any],
    source_plan: dict[str, Any] | None = None,
) -> list[str]:
    """返回画像命名提示，不生成新的 Seed 协议或持久化中间产物。

    数据面清单中的 core entities 是 Agent 调研后的明确声明，优先级最高；当清单
    尚未建立时，只使用参考工具名中的目标词作为 raw 画像的弱提示。
    """

    hints: list[str] = []
    if isinstance(source_plan, dict):
        for source in source_plan.get("sources", []):
            if not isinstance(source, dict) or source.get("priority") != "core":
                continue
            for value in source.get("target_entity_types", []):
                if isinstance(value, str) and value.strip() and value not in hints:
                    hints.append(value.strip())
        # 一旦核心数据面已经给出明确实体，不再把扩展工具绑定的元数据、
        # 小样本或文件型实体升级为核心深度要求。
        if hints:
            return hints
    if hints:
        return hints

    stop_words = {
        "get", "list", "search", "find", "create", "update", "delete", "post",
        "report", "fetch", "query", "read", "write", "check", "validate", "run",
    }
    for tool in seed.get("init_ref_tools", []):
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            continue
        words = [
            part
            for part in re.split(r"[^A-Za-z0-9]+", tool["name"].lower())
            if len(part) > 2 and part not in stop_words
        ]
        if words:
            candidate = words[-1]
            if candidate not in hints:
                hints.append(candidate)
    return hints[:12]


def seed_urls(seed: dict[str, Any]) -> list[str]:
    """收集 Seed 明确携带的 URL，用于 Prompt 提示而不是封闭来源白名单。"""

    urls: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
        elif isinstance(value, str) and value.startswith(("http://", "https://")):
            if value not in urls:
                urls.append(value)

    visit(seed)
    return urls
