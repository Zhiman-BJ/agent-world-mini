"""从已落盘文件提取记录、字段和关系候选。

这里的结果用于数据画像和声明校验，不是最终环境语义。环境资源的业务含义由
环境描述 Agent 声明，Validator 再用本模块提取的事实进行核对。
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from env_gen.data_gen.analysis.semantics import (
    infer_operation_family,
    normalize_semantic_token,
    operation_target_tokens,
    semantic_match_score,
    semantic_tokens,
)

_FORMAT_BY_SUFFIX = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".csv": "csv",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
    ".db": "sqlite",
    ".parquet": "parquet",
}
_NUMERIC_TYPES = {"integer", "number"}
_CANONICAL_STOP_TOKENS = {
    "raw",
    "record",
    "records",
    "data",
    "response",
    "result",
    "results",
    "observation",
    "detail",
    "details",
    "search",
    "batch",
    "obs",
}
_FACT_ENTITY_TOKENS = {"observation", "event", "transaction", "measurement", "reading"}
# 这些数值字段描述 API 的编码精度或分页参数，不是业务度量。统一过滤
# 它们可以避免把 ``decimal=0/1`` 误当成可排序指标，同时保留 count、
# amount、score 等可能真正有业务意义的字段。
_NUMERIC_METADATA_FIELDS = {"decimal", "precision", "scale", "offset", "limit", "page", "page_size"}

# 常见公开 API 会把分页、查询和过滤条件包装在同一个 JSON 响应中。这些
# 字段是传输元数据，不是业务实体；如果把其中的 ``column_id`` 等字段
# 递归展开，会凭空得到 ``*_query``、``*_filters`` 实体。这里按结构统一
# 过滤，而不是按某个领域或具体 API 写特例。
_RAW_METADATA_KEYS = {
    "meta",
    "metadata",
    "pagination",
    "page",
    "pages",
    "per_page",
    "total",
    "total_count",
    "incomplete_results",
    "query",
    "x_query",
    "oqo",
    "filter_rows",
    "filters",
    "group_by",
    "groups_count",
    "facets",
    "warnings",
    "errors",
    "error",
}
_RAW_COLLECTION_KEYS = {"items", "results", "data"}


def _slug(value: Any, fallback: str = "item") -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    text = re.sub(r"_+", "_", text)
    if not text or not text[0].isalpha():
        text = f"{fallback}_{text}" if text else fallback
    return text


def _unique_slug(value: str, used: set[str], fallback: str) -> str:
    base = _slug(value, fallback)
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def _format_for(path: Path) -> str:
    return _FORMAT_BY_SUFFIX.get(path.suffix.lower(), "text")


def _primitive_type(values: list[Any]) -> str | None:
    if not values or any(value is None or isinstance(value, (list, dict)) for value in values):
        return None
    if all(isinstance(value, bool) for value in values):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return "integer"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return "number"
    if all(isinstance(value, str) for value in values):
        return "string"
    return None


def _tokens(value: str) -> set[str]:
    """把实体/字段名称拆成可比较的英文词，并处理常见复数。"""

    result: set[str] = set()
    for token in re.findall(r"[^\W_]+", str(value).lower(), flags=re.UNICODE):
        token = normalize_semantic_token(token)
        result.add(token)
    return result


def _ordered_tokens(value: str) -> list[str]:
    """按原文顺序返回规范化词，供命名推断使用。"""

    result: list[str] = []
    for token in re.findall(r"[^\W_]+", str(value).lower(), flags=re.UNICODE):
        token = normalize_semantic_token(token)
        if token not in result:
            result.append(token)
    return result


def _canonical_tokens(value: str) -> set[str]:
    """返回用于实体类型归一化的业务词，忽略通用前缀和年份。"""

    result: set[str] = set()
    ordered = _ordered_tokens(value)
    for index, token in enumerate(ordered):
        # 很多 API 文件用 ``obs`` 表示 observation；把它提升为同一语义词，
        # 让文件名和种子实体可以在不认识具体领域的情况下对齐。
        if token == "obs":
            token = "observation"
        # 只把 ``page_001`` 这类分页编号视为传输噪声。没有编号的
        # ``documentation_page``、``landing_page`` 仍是有意义的实体名。
        if token == "page" and (
            (index > 0 and ordered[index - 1].isdigit())
            or (index + 1 < len(ordered) and ordered[index + 1].isdigit())
        ):
            continue
        if token not in _CANONICAL_STOP_TOKENS and not token.isdigit():
            result.add(token)
    return result


def _fact_entity_tokens(value: str) -> set[str]:
    """返回实体名称中的事实/观测语义词，不能被普通路径停用词吞掉。"""

    return {
        "observation" if token == "obs" else token
        for token in _tokens(value)
        if ("observation" if token == "obs" else token) in _FACT_ENTITY_TOKENS
    }


def _is_id_field(field: str) -> bool:
    return field == "id" or field == "entity_id" or field.endswith("_id")


def _is_identifier_field(field: str) -> bool:
    """识别来源中常见的稳定键；最终 entity 契约仍统一补 entity_id。"""

    lowered = str(field).lower()
    return (
        _is_id_field(lowered)
        or lowered == "code"
        or lowered.endswith("_code")
        or lowered.endswith("_key")
        # 常见标准编码没有下划线（iso2code、iso3code）。它们仍是可
        # 唯一引用键，但不应被当作普通业务维度。
        or lowered.endswith("code") and lowered.startswith(("iso", "alpha", "numeric"))
    )


def _is_relation_key_field(field: str) -> bool:
    """识别可以指向另一业务实体的外键字段。

    ``node_id`` 等 API 内部标识可以帮助追溯原始响应，但不代表实体之间的
    业务连接。关系推断、桥接分类和关系类型合并必须排除这类技术键，否则
    同一关系会因为每个来源对象都有不同的 node_id 而被拆成许多类型。
    """

    lowered = str(field).lower()
    return (
        lowered.endswith("_id")
        and lowered not in {"id", "entity_id", "relation_id", "node_id"}
        and not lowered.startswith("ids_")
    )


def _is_inheritable_id_field(field: str) -> bool:
    """判断字段是否可以作为子对象的父级外键继承。"""

    # ``*_key`` 经常是 API 的分组/缓存键（例如作者详情里的 block_key），
    # 它不是业务关系。真正可遍历的父级标识只允许 id 和 code 语义。
    return _is_id_field(field) or field == "code" or field.endswith("_code")


def _is_technical_field(field: str) -> bool:
    lowered = str(field).lower()
    return (
        _is_identifier_field(lowered)
        or lowered in {"url", "uri", "uuid"}
        or lowered.endswith(("_url", "_uri", "_uuid"))
        # 这些字段描述 DataGen 抽取过程，不是业务实体能力证据。
        or lowered in {"raw_file", "source_file", "source_raw_file", "source_path"}
        or lowered.endswith(("_raw_file", "_source_file", "_source_path"))
    )


def _is_numeric_measure_field(field: str) -> bool:
    """判断数值字段是否更像业务度量，而非传输/格式元数据。"""

    return not _is_technical_field(field) and str(field).lower() not in _NUMERIC_METADATA_FIELDS


def _is_temporal_field(field: str) -> bool:
    """判断字段名是否表达日期、年份或时间戳。"""

    lowered = str(field).lower()
    return any(
        token in lowered
        for token in ("year", "date", "time", "timestamp", "created", "updated", "published")
    )




def _choose_primary_id(entity_type: str, fields: set[str]) -> str | None:
    """选择实体自己的业务键；无法区分时返回 ``None``，交给复合键处理。

    ``entity_id`` 是规范化器可能生成的技术键，不能在已有业务主键时抢先
    被选中。实体名使用复数或限定词时，也要用 token 集合匹配（例如
    ``awards`` -> ``award_id``），否则一个带 ``funder_id`` 的 award 会被
    错误分类为关系桥。
    """

    names = {str(field) for field in fields}
    if "id" in names:
        return "id"
    id_fields = sorted(field for field in names if _is_identifier_field(field))
    if not id_fields:
        return None

    # 关系/规范化产生的技术键只在没有更有语义的来源键时兜底。
    semantic_ids = [
        field for field in id_fields
        if field not in {"entity_id", "relation_id", "node_id"}
        and not field.lower().startswith("ids_")
    ]
    candidates = semantic_ids or id_fields
    exact = f"{entity_type}_id"
    if exact in candidates:
        return exact

    entity_tokens = _tokens(entity_type)
    # ``awards``、``research_authors`` 等名称已经在 _tokens 中做了单复数
    # 归一化；完全相同的 token 集合比单词交集更强。
    exact_token_matches = []
    scored: list[tuple[int, str]] = []
    for field in candidates:
        base = field[:-3] if field.endswith("_id") else field
        base_tokens = _tokens(base)
        if entity_tokens and base_tokens == entity_tokens:
            exact_token_matches.append(field)
        scored.append((len(entity_tokens.intersection(base_tokens)), field))
    if len(exact_token_matches) == 1:
        return exact_token_matches[0]

    best_score = max((score for score, _field in scored), default=0)
    best = [field for score, field in scored if score == best_score]
    if best_score > 0 and len(best) == 1:
        return best[0]
    if len(candidates) == 1:
        return candidates[0]
    # 最后才使用来源已有的 entity_id；多个语义外键无法判断哪个是自身键
    # 时必须留给 bridge 逻辑，不能任意猜一个目标实体。
    return "entity_id" if "entity_id" in names else None


def _flatten_record(value: Any, prefix: str = "") -> dict[str, Any]:
    """将 raw 响应中的嵌套对象确定性展开；数组保留在 raw，不强行扁平化。"""

    if not isinstance(value, dict):
        return {}
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        safe_key = _slug(key, "field")
        output_key = f"{prefix}_{safe_key}" if prefix else safe_key
        if isinstance(item, dict):
            flattened.update(_flatten_record(item, output_key))
        elif isinstance(item, list):
            # 数组通常代表一对多/多对多关系；只有原始文件能无损保留它。
            continue
        elif item is not None and isinstance(item, (str, int, float, bool)):
            flattened[output_key] = item
    return flattened


__all__ = [name for name in globals() if not name.startswith("__")]
