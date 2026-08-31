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

from env_gen.data_gen.policy import (
    infer_operation_family,
    normalize_semantic_token,
    operation_target_tokens,
    semantic_match_score,
)


_FORMAT_BY_SUFFIX = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".csv": "csv",
    ".sqlite": "sqlite",
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
    "page",
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
    for token in _tokens(value):
        # 很多 API 文件用 ``obs`` 表示 observation；把它提升为同一语义词，
        # 让文件名和种子实体可以在不认识具体领域的情况下对齐。
        if token == "obs":
            token = "observation"
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
    )


def _is_numeric_measure_field(field: str) -> bool:
    """判断数值字段是否更像业务度量，而非传输/格式元数据。"""

    return not _is_technical_field(field) and str(field).lower() not in _NUMERIC_METADATA_FIELDS


def _unique_key_fields(
    entity_type: str,
    records: list[dict[str, Any]],
    fields: set[str],
) -> list[str]:
    """返回实体中非空且唯一、可作为关系目标的键。

    主键排在第一位；当来源使用另一套稳定编码（例如 ISO2/ISO3）时，
    只要该列在当前实体库存中唯一，也允许关系引用它。没有记录级唯一性
    证明的普通字段不会被提升为关系目标。
    """

    # 关系桥没有“自身”单列主键；即便某个外键在当前样本中碰巧唯一，
    # 也不能把它提升为其它关系的目标，否则会产生 bridge -> bridge 的
    # 伪关系。桥接记录只能作为关系的源端或事实证据。
    if _is_bridge_entity(entity_type, fields):
        return []

    candidates: list[str] = []
    primary = _choose_primary_id(entity_type, fields)
    if primary:
        candidates.append(primary)
    for field in sorted(fields):
        # ``*_id`` 通常是指向别的实体的外键。即使当前样本恰好只有一
        # 条记录，它也不能因此变成被其它关系引用的“唯一键”；否则会
        # 把 issue.repository_id 误当成 issue 自己的主键。备用目标键
        # 限定为明确的编码/业务 key，主键仍由上面的选择逻辑保留。
        if (
            field not in candidates
            and _is_identifier_field(field)
            and not _is_relation_key_field(field)
        ):
            candidates.append(field)
    result: list[str] = []
    for field in candidates:
        values = [
            json.dumps(record.get(field), ensure_ascii=False, sort_keys=True, default=str)
            for record in records
            if isinstance(record, dict) and record.get(field) not in (None, "")
        ]
        if values and len(values) == len(records) and len(set(values)) == len(values):
            result.append(field)
    return result


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


def _read_entity_groups(path: Path) -> dict[str, list[dict[str, Any]]]:
    format_name = _format_for(path)
    if format_name == "json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return {_slug(path.stem): [item for item in payload if isinstance(item, dict)]}
        if isinstance(payload, dict):
            return {
                _slug(entity_type): [item for item in records if isinstance(item, dict)]
                for entity_type, records in payload.items()
                if isinstance(records, list)
            }
        return {}
    if format_name == "jsonl":
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return {_slug(path.stem): [item for item in records if isinstance(item, dict)]}
    if format_name == "csv":
        with path.open("r", encoding="utf-8", newline="") as stream:
            return {_slug(path.stem): [dict(row) for row in csv.DictReader(stream)]}
    if format_name == "sqlite":
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            groups: dict[str, list[dict[str, Any]]] = {}
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            for (table_name,) in tables:
                rows = connection.execute(f'SELECT * FROM "{str(table_name).replace(chr(34), chr(34) * 2)}"')
                groups[_slug(table_name)] = [dict(row) for row in rows]
            return groups
        finally:
            connection.close()
    return {}


def _normalize_groups(groups: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """删除非标量字段并保留复合外键，生成稳定的实体记录视图。

    现实 API 的关系表经常同时包含两个外键（例如 work_id + author_id），
    其中某个外键还可能只在部分记录中出现。先按实体键过滤记录，再推断字段，
    可以避免因为一个可选字段为空而把真正的主键整列丢掉。
    """

    normalized: dict[str, list[dict[str, Any]]] = {}
    for entity_type, raw_records in groups.items():
        records = [record for record in raw_records if isinstance(record, dict)]
        if not records:
            continue
        all_fields = set().union(*(record.keys() for record in records))
        id_candidates = {
            field for field in all_fields
            if isinstance(field, str) and _is_identifier_field(field)
        }
        primary_hint = _choose_primary_id(entity_type, id_candidates)
        # 主键若在记录中重复，说明它只是关系表中的一个外键；多个外键
        # 应组成复合键。唯一的 country_code/indicator_id 等仍可作为实体键。
        primary_values = (
            [record.get(primary_hint) for record in records]
            if primary_hint
            else []
        )
        primary_unique = (
            bool(primary_values)
            and all(value not in (None, "") for value in primary_values)
            and len({json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) for value in primary_values})
            == len(primary_values)
        )
        bridge = primary_hint is None or (
            len(id_candidates) >= 2 and not primary_unique
        )
        required_id_fields = (
            sorted(id_candidates) if bridge else ([primary_hint] if primary_hint else [])
        )
        if required_id_fields:
            records = [
                record
                for record in records
                if all(
                    record.get(field) not in (None, "")
                    and not isinstance(record.get(field), (list, dict))
                    for field in required_id_fields
                )
            ]
        if not records:
            continue
        if bridge:
            # entity_id 是由真实外键值拼出的确定性技术键，不是业务记录。
            records = [
                {
                    **record,
                    "entity_id": "|".join(
                        f"{field}={json.dumps(record[field], ensure_ascii=False, sort_keys=True, default=str)}"
                        for field in required_id_fields
                    ),
                }
                for record in records
            ]

        # 非 *_id 的来源键（例如 country_code）补成契约规定的 entity_id，
        # 同时保留原始 code 字段供搜索/过滤使用。
        if not bridge and primary_hint and not _is_id_field(primary_hint):
            records = [
                {**record, "entity_id": record[primary_hint]}
                for record in records
            ]
        # 对可选字段允许少量缺失：保留非空比例达到 95% 的标量字段，
        # 再舍弃缺该字段的记录。这样观测表不会因少数 null value 丢掉
        # 整个指标字段，同时仍保证最终 entity 记录没有 null。
        field_names = set().union(*(record.keys() for record in records))
        field_map: dict[str, str] = {}
        field_types: dict[str, str] = {}
        used_fields: set[str] = set()
        for original in sorted(field_names):
            safe_name = _unique_slug(original, used_fields, "field")
            values = [
                record.get(original)
                for record in records
                if record.get(original) not in (None, "")
                and not isinstance(record.get(original), (list, dict))
            ]
            expected = _primitive_type(values)
            if expected is not None and len(values) / len(records) >= 0.95:
                field_map[original] = safe_name
                field_types[safe_name] = expected
        records = [
            record
            for record in records
            if all(
                original in record
                and record.get(original) not in (None, "")
                and not isinstance(record.get(original), (list, dict))
                for original in field_map
            )
        ]
        if not records:
            continue
        id_fields = [name for name in field_types if _is_id_field(name)]
        if not id_fields:
            continue
        clean_records = [
            {field_map[original]: record[original] for original in field_map}
            for record in records
        ]
        # bridge 已经用所有真实外键生成了复合 entity_id；此处必须按复合
        # 键去重，不能重新挑出一个重复的单列外键（例如同一 work 的多位
        # author），否则合法的多对多记录会被静默删除。
        primary = "entity_id" if bridge else _choose_primary_id(entity_type, set(field_types))
        if primary is None:
            primary = "entity_id" if "entity_id" in field_types else id_fields[0]
        # 去除同一实体类型在多个来源文件中的重复主键，但不改动原始文件。
        seen: set[str] = set()
        unique_records: list[dict[str, Any]] = []
        for record in clean_records:
            value = json.dumps(record.get(primary), ensure_ascii=False, sort_keys=True, default=str)
            if value not in seen:
                seen.add(value)
                unique_records.append(record)
        if unique_records:
            normalized[entity_type] = unique_records
    return normalized


def _record_identity_key(entity_type: str, record: dict[str, Any]) -> tuple[Any, ...] | None:
    """为跨视图合并选择稳定的记录身份。

    普通实体按自身主键合并；桥接实体按全部业务外键合并。这样一个完整
    主视图和一个只含 ID 的嵌套投影可以补充到同一条记录，而不会把投影
    当作新记录参与全局字段完整度过滤。
    """

    fields = {str(field) for field in record}
    if _is_bridge_entity(entity_type, fields):
        relation_fields = sorted(
            field
            for field in fields
            if _is_relation_key_field(field)
            and record.get(field) not in (None, "")
            and not isinstance(record.get(field), (dict, list))
        )
        if len(relation_fields) >= 2:
            return (
                "relation",
                *(
                    (field, json.dumps(record[field], ensure_ascii=False, sort_keys=True, default=str))
                    for field in relation_fields
                ),
            )

    primary = _choose_primary_id(entity_type, fields)
    if primary and record.get(primary) not in (None, ""):
        value = json.dumps(record[primary], ensure_ascii=False, sort_keys=True, default=str)
        return ("primary", primary, value)

    id_fields = sorted(
        field
        for field in fields
        if _is_identifier_field(field)
        and record.get(field) not in (None, "")
        and not isinstance(record.get(field), (dict, list))
    )
    if id_fields:
        return (
            "ids",
            *(
                (field, json.dumps(record[field], ensure_ascii=False, sort_keys=True, default=str))
                for field in id_fields
            ),
        )
    return None


def _merge_group_records(
    entity_type: str,
    existing_records: list[dict[str, Any]],
    incoming_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按记录身份合并不同来源视图，并只用缺失字段补全主视图。

    该合并不会重新应用 ``_normalize_groups`` 的全局 95% 字段阈值；否则
    一个只含少数字段的投影会让完整实体的业务字段从整个类型中消失。
    """

    merged: list[dict[str, Any]] = []
    positions: dict[tuple[Any, ...], int] = {}
    # 以已有主视图的字段集合作为 canonical 结构。新增投影若不能提供
    # 全部这些字段就跳过；把只有 ID 的稀疏记录放进同一类型会让最终
    # entity_schema 要求完整字段，反而使整个环境不可校验。
    schema_fields = {
        str(field)
        for record in existing_records
        for field in record
    }
    for record in existing_records:
        if not isinstance(record, dict):
            continue
        key = _record_identity_key(entity_type, record)
        if key is None or key not in positions:
            if key is not None:
                positions[key] = len(merged)
            merged.append(dict(record))
            continue
    for record in incoming_records:
        if not isinstance(record, dict):
            continue
        key = _record_identity_key(entity_type, record)
        if key is None or key not in positions:
            if schema_fields and not all(
                record.get(field) not in (None, "")
                and not isinstance(record.get(field), (dict, list))
                for field in schema_fields
            ):
                continue
            candidate = (
                {field: record[field] for field in schema_fields}
                if schema_fields
                else dict(record)
            )
            if key is not None:
                positions[key] = len(merged)
            merged.append(candidate)
            continue
        target = merged[positions[key]]
        for field, value in record.items():
            if (
                field in schema_fields
                and target.get(field) in (None, "")
                and value not in (None, "")
            ):
                target[field] = value
    return merged


def _without_generated_entity_ids(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在跨文件合并前移除规范化器上一次生成的复合 entity_id。

    ``_normalize_groups`` 会为桥接关系补一个复合键。若把这批已规范化
    记录再次和另一分片合并，复合键会被当作普通字段继续嵌套，最终出现
    ``entity_id=...entity_id=...``。只要记录还有其它稳定 ID，就让合并后
    的一次规范化重新计算它；仅有 entity_id 的原始业务记录则原样保留。
    """

    cleaned: list[dict[str, Any]] = []
    for record in records:
        entity_id = record.get("entity_id")
        other_ids = sorted(
            field
            for field, value in record.items()
            if field != "entity_id"
            and _is_identifier_field(str(field))
            and value not in (None, "")
            and not isinstance(value, (list, dict))
        )
        # 只有值严格等于 _normalize_groups 为桥接记录生成的格式时才删除。
        # 来源本身可能合法地同时提供 entity_id 和一个外键，不能依据“存在
        # 其它 ID 字段”这一弱条件把它静默丢掉。
        generated = "|".join(
            f"{field}={json.dumps(record[field], ensure_ascii=False, sort_keys=True, default=str)}"
            for field in other_ids
        )
        if (
            isinstance(entity_id, str)
            and len(other_ids) >= 2
            and entity_id == generated
        ):
            cleaned.append(
                {field: value for field, value in record.items() if field != "entity_id"}
            )
        else:
            cleaned.append(record)
    return cleaned


def _entity_key_stem(
    group_name: str,
    collection_key: str | None,
    seed_entity_names: list[str],
) -> str | None:
    """从集合路径推断当前对象的 ID 前缀。

    API 响应常使用通用 ``id`` 字段。优先用种子实体与当前集合路径的
    词面交集命名（如 ``id`` -> ``work_id``），没有种子匹配时才使用
    集合键（如 ``source``、``label``）。这不依赖具体领域字段。
    """

    path_tokens = _canonical_tokens(group_name)
    key_tokens = _canonical_tokens(collection_key or "")
    generic = {
        "research",
        "scholarly",
        "public",
        "data",
        "entity",
        "record",
        "result",
        "item",
        "detail",
        "search",
        "batch",
    }
    # 非包装集合键是当前嵌套对象最直接的语义来源；先处理它，避免
    # ``issue.labels`` 因继承 issue 路径而把 label.id 命名成 issue_id。
    if collection_key:
        key_lower = str(collection_key).lower()
        if key_lower not in _RAW_COLLECTION_KEYS and key_lower not in _RAW_METADATA_KEYS:
            useful_key = [
                token
                for token in _ordered_tokens(collection_key)
                if token not in generic and len(token) > 1
            ]
            if useful_key:
                key_tokens = _canonical_tokens(collection_key)
                raw_key_tokens = _tokens(collection_key)
                matching_seed = [
                    (
                        # 集合键和种子实体完全相等时优先；否则优先较短的
                        # 精确业务词。这样 indicator 集合不会被
                        # ``indicator observation`` 复合名抢走，外键会稳定
                        # 命名为 indicator_id。
                        1 if raw_key_tokens == _tokens(name) else 0,
                        -len(_tokens(name)),
                        len(key_tokens.intersection(_canonical_tokens(name))),
                        _ordered_tokens(name),
                    )
                    for name in seed_entity_names
                    if key_tokens.intersection(_canonical_tokens(name))
                ]
                if matching_seed:
                    _score, _length, _overlap, matching_tokens = max(matching_seed)
                    matching_useful = [token for token in matching_tokens if token not in generic]
                    if matching_useful:
                        return matching_useful[-1]
                return useful_key[-1]
    seed_matches: list[tuple[int, int, str]] = []
    for name in seed_entity_names:
        tokens = _canonical_tokens(name)
        key_overlap = len(tokens.intersection(key_tokens))
        path_overlap = len(tokens.intersection(path_tokens))
        overlap = key_overlap * 10 + path_overlap
        if overlap:
            useful = [token for token in _ordered_tokens(name) if token not in generic]
            if useful:
                # 只返回真正出现在当前路径/集合键中的种子词。这样
                # ``indicator_*.json`` 会得到 indicator_id，而不会因种子
                # 还有 observation 一词而随机返回 observation_id。
                present = [
                    token
                    for token in useful
                    if token in path_tokens or token in key_tokens
                ]
                chosen = present[-1] if present else useful[-1]
                positions = [group_name.find(token) for token in present if token in group_name]
                position = min((item for item in positions if item >= 0), default=10**6)
                seed_matches.append((overlap, -position, chosen))
    if seed_matches:
        return max(seed_matches)[2]

    useful_key = [
        token
        for token in _ordered_tokens(collection_key or "")
        if token not in generic and len(token) > 1
    ]
    if useful_key:
        return useful_key[-1]
    return None


def _raw_record_groups(
    path: Path,
    *,
    seed_entity_names: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """从 JSON raw 响应递归提取实体和关系候选。

    每个嵌套对象只继承父对象的稳定标识字段，不继承父业务字段；这样
    ``issue.labels``、``work.authorships`` 等数组可以形成关系记录，同时
    不会把整条父记录复制到子实体。原始数组仍完整保留在 raw 文件中。
    """

    if _format_for(path) != "json":
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[str, list[dict[str, Any]]] = {}
    seed_entity_names = list(seed_entity_names or [])
    stem = _slug(path.stem, "raw_records")

    def is_metadata_key(key: Any) -> bool:
        """识别传输包装字段；业务字段仍按原样保留在 raw 中。"""

        normalized = _slug(key, "field")
        return (
            normalized in _RAW_METADATA_KEYS
            or normalized.startswith("x_query")
            or normalized.startswith("request_")
        )

    def add_record(
        group_name: str,
        record: dict[str, Any],
        own_fields: set[str],
    ) -> None:
        # 只接受对象自身带稳定标识的候选。仅继承父对象 ID 的包装节点
        # （例如 primary_location）不是一个可独立引用的实体。
        if not any(_is_identifier_field(field) for field in own_fields):
            return
        flattened = _flatten_record(record)
        if any(_is_identifier_field(field) for field in flattened):
            groups.setdefault(_slug(group_name, "raw_records"), []).append(flattened)

    def visit(
        value: Any,
        group_name: str,
        inherited_ids: dict[str, Any] | None = None,
        *,
        collection_key: str | None = None,
        parent_entity_stem: str | None = None,
    ) -> None:
        inherited_ids = inherited_ids or {}
        if isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    visit(item, group_name, inherited_ids, collection_key=collection_key)
            return
        if not isinstance(value, dict):
            return

        # 当前对象的标量和嵌套业务对象先展开；元数据子树不进入候选，也
        # 不参与继承 ID，避免把查询过滤器伪装成业务关系。
        filtered_value = {
            key: child
            for key, child in value.items()
            if not is_metadata_key(key)
        }
        flattened = _flatten_record(filtered_value)
        key_stem = _entity_key_stem(group_name, collection_key, seed_entity_names)
        # 只把当前对象直接拥有的标量键视为自己的 ID。嵌套对象经展开后
        # 也会出现 ``user_id``、``milestone_id`` 等字段，但它们不能作为
        # 当前对象的父级外键，否则每条关系会同时携带多个无关实体标识。
        own_ids = {
            _slug(key, "field"): item
            for key, item in filtered_value.items()
            if not isinstance(item, (dict, list))
            and _is_identifier_field(_slug(key, "field"))
        }

        # 有些 API 用一个对象同时承载多个业务维度（例如
        # ``{indicator: {id}, country: {id}, date, value}``），它本身没有
        # 顶层 id。若只递归记录两个子对象，就会丢失真正的观测/事实行，
        # 也无法在后续建立关系。对这类结构生成由真实外键和时间/度量值
        # 组成的确定性事实 ID；这不是业务记录合成，而是对同一 raw 对象
        # 的无损结构化投影。
        nested_id_fields = {
            field
            for field in flattened
            if _is_relation_key_field(field)
            and field not in own_ids
            and flattened.get(field) not in (None, "")
            and not isinstance(flattened.get(field), (dict, list))
        }
        fact_tokens = _fact_entity_tokens(group_name)
        has_measure = any(
            field in {"value", "amount", "score", "count", "rate", "total"}
            or any(token in field.lower() for token in ("value", "amount", "score", "count", "rate", "total"))
            for field in flattened
            if not _is_identifier_field(field)
        )
        has_time = any(_is_temporal_field(field) for field in flattened)
        fact_id_field: str | None = None
        if (
            not own_ids
            and len(nested_id_fields) >= 2
            and (bool(fact_tokens) or has_time or has_measure)
        ):
            fact_id_field = (
                f"{sorted(fact_tokens)[0]}_id"
                if fact_tokens
                else "record_id"
            )
            identity_fields = sorted(nested_id_fields)
            identity_fields.extend(
                field
                for field in sorted(flattened)
                if _is_temporal_field(field)
                and flattened.get(field) not in (None, "")
                and not isinstance(flattened.get(field), (dict, list))
            )
            identity = "|".join(
                f"{field}={json.dumps(flattened[field], ensure_ascii=False, sort_keys=True, default=str)}"
                for field in identity_fields
            )
            own_ids[fact_id_field] = identity
            flattened[fact_id_field] = identity
        # 将对象自己的通用 id 转成稳定、可用于关系推断的语义键。若对象
        # 已经包含 author_id/source_id 等明确字段，则保留这些字段，避免
        # 用一个猜测覆盖更强的来源证据。
        if "id" in own_ids and key_stem:
            typed_id = f"{key_stem}_id"
            if typed_id not in own_ids:
                own_ids[typed_id] = own_ids.pop("id")
                flattened.pop("id", None)
                flattened[typed_id] = own_ids[typed_id]
        # 实体字段可以保留嵌套对象展开出的普通标量，但不能把嵌套对象的
        # 标识当作当前实体自己的主键或外键。
        preserved_identifier_fields = set(own_ids)
        if fact_id_field is not None:
            # 事实记录的嵌套外键是后续闭合关系的业务字段；普通子实体仍
            # 只保留自己的 ID，避免父级标识污染其主视图。
            preserved_identifier_fields.update(nested_id_fields)
        flattened = {
            field: item
            for field, item in flattened.items()
            if not _is_identifier_field(field) or field in preserved_identifier_fields
        }
        current_ids = {
            field: item
            for field, item in {**inherited_ids, **own_ids}.items()
            if _is_inheritable_id_field(field)
        }
        # 实体视图只保留当前对象自身字段；父级 ID 不应污染子实体。
        add_record(group_name, flattened, set(own_ids))

        # 父级和当前对象都有稳定 ID 时，额外保留一条关系候选。关系行
        # 可以携带当前位置、角色等标量属性，但不会被当作实体自身字段。
        if inherited_ids and own_ids:
            relation_group = f"{parent_entity_stem or group_name}_{_slug(collection_key or 'relation', 'relation')}_relation"
            # 关系候选只携带父级和当前对象的直接 ID；其它嵌套 ID 已在
            # entity 视图中过滤，不能让一个 relation 变成多实体混合行。
            relation_record = {**inherited_ids, **flattened}
            relation_flattened = {
                field: item
                for field, item in relation_record.items()
                # 关系候选只保留标量属性和可指向业务实体的键。API 的
                # node_id/entity_id 等技术标识留在 raw，不应污染关系类型。
                if not _is_identifier_field(field) or _is_relation_key_field(field)
            }
            relation_ids = {
                field
                for field in relation_flattened
                if _is_relation_key_field(field)
            }
            if len(relation_ids) >= 2:
                groups.setdefault(_slug(relation_group, "relation"), []).append(
                    relation_flattened
                )

        for key, child in filtered_value.items():
            if not isinstance(child, (dict, list)) or is_metadata_key(key):
                continue
            # 集合键（items/results/topics/authorships）提供实体语义；文件
            # stem 保留来源上下文，便于后续基于字段和种子做统一归一化。
            child_group = f"{group_name}_{_slug(key, 'nested')}"
            # 关系只连接最近的两个可识别对象。当前对象拥有自己的 ID
            # 时，不再把更远祖先的 ID 一并传给孙对象；祖先关系已经在
            # 当前节点被单独记录。若当前节点只是无 ID 的包装对象，才
            # 继续沿用最近的 inherited_ids，避免丢失跨包装层的连接。
            child_parent_ids = {
                field: item
                for field, item in own_ids.items()
                if _is_inheritable_id_field(field)
            } or inherited_ids
            visit(
                child,
                child_group,
                child_parent_ids,
                collection_key=str(key),
                parent_entity_stem=key_stem or parent_entity_stem or group_name,
            )

    # 根响应的常见形态是 {results: [...]}; 直接以 results/items 作为第一
    # 业务集合，避免先创建一个没有自身 ID 的根包装组。根数组（例如
    # World Bank 的 [pagination, records]）则由同一个 stem 集合处理。
    if isinstance(payload, dict):
        for key, child in payload.items():
            if is_metadata_key(key) or not isinstance(child, (dict, list)):
                continue
            visit(
                child,
                f"{stem}_{_slug(key, 'records')}",
                {},
                collection_key=str(key),
            )
    elif isinstance(payload, list):
        visit(payload, stem, {})
    return groups


def _canonical_entity_type(
    group_name: str,
    *,
    existing: set[str],
    seed_entity_names: list[str],
    preferred_seed_entity_names: list[str] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> str:
    """把 raw 文件名映射到已有/种子实体名，避免同一实体被拆成多类。"""

    group_tokens = _canonical_tokens(group_name)
    # 递归路径末端通常就是当前对象的集合键（例如 ``..._author`` 或
    # ``..._institution``）。祖先路径只用于根集合判断，不能让父实体词
    # 抢走子实体的类型。
    path_parts = [part for part in str(group_name).split("_") if part]
    tail_tokens: set[str] = set()
    if path_parts:
        # 末段是 results/items 等集合包装时，保持空 tail；不能向前跨过
        # 该包装层，否则 ``works/.../results`` 会错误继承 ``topics``。
        last_tokens = _canonical_tokens(path_parts[-1])
        if path_parts[-1].lower() in _RAW_COLLECTION_KEYS:
            tail_tokens = set()
        elif last_tokens:
            tail_tokens = last_tokens
        elif (
            path_parts[-1].lower() not in _RAW_METADATA_KEYS
            and path_parts[-1].lower() not in _RAW_COLLECTION_KEYS
            and not _tokens(path_parts[-1]).intersection(_CANONICAL_STOP_TOKENS)
            and not re.fullmatch(r"[a-zA-Z]*\d+", path_parts[-1])
        ):
            for width in (2, 3):
                if len(path_parts) < width:
                    continue
                candidate_tokens = _canonical_tokens("_".join(path_parts[-width:]))
                candidate_tokens = {
                    token for token in candidate_tokens if len(token) > 1
                }
                if candidate_tokens:
                    tail_tokens = candidate_tokens
                    break
    record_tokens = {
        token
        for record in records or []
        for field in record
        for token in _canonical_tokens(str(field))
    }
    record_field_names = {
        str(field)
        for record in records or []
        for field in record
    }

    def overlap_score(name: str) -> int:
        name_tokens = _canonical_tokens(name)
        raw_name_tokens = {
            "observation" if token == "obs" else token
            for token in _tokens(name)
        }
        raw_group_tokens = {
            "observation" if token == "obs" else token
            for token in _tokens(group_name)
        }
        # 复合事实实体（如 indicator observation）只有在原始集合也明确
        # 表示 observation/event 时才能作为首选；否则一个 indicator 定义
        # 文件会被错误吸收到事实实体中。普通限定词（research、scholarly）
        # 可以缺省，因为公开 API 常用更短的集合名（topics、works）。
        fact_tokens = _FACT_ENTITY_TOKENS
        if (
            preferred_seed_entity_names
            and any(_slug(name, "entity") == _slug(seed_name, "entity") for seed_name in preferred_seed_entity_names)
            and fact_tokens.intersection(
                _fact_entity_tokens(name) - _fact_entity_tokens(group_name)
            )
        ):
            return 0
        overlap = group_tokens.intersection(name_tokens)
        tail_overlap = tail_tokens.intersection(name_tokens)
        # 有明确的 nested 集合键时，只允许该键对应的种子实体参与映射。
        # 例如 ``..._affiliations`` 继承了 author_id，但它本身不能再被
        # 归类为 research_author；否则所有关系/子实体都会被父实体吞掉。
        if tail_tokens and not tail_overlap:
            # 根级文件常以业务编码结尾（如 ``indicator_*.json`` 的
            # ``cd``/``totl``）。若记录自身已经有与候选实体一致的主键，
            # 该编码不是嵌套集合语义，可以继续匹配；关系候选仍严格受
            # 末段限制，避免父实体词汇吞掉 bridge。
            has_matching_record_id = any(
                f"{token}_id" in record_field_names
                for token in name_tokens
            )
            if not has_matching_record_id or str(group_name).endswith("_relation"):
                return 0
        # 复合种子 ``indicator observation`` 不能因 ``indicator_id`` 一
        # 个字段就吞掉指标定义表；只有来源明确表示 observation/obs 时
        # 才能映射到该复合实体。
        if (
            "observation" in raw_name_tokens
            and "observation" not in raw_group_tokens
        ):
            return 0
        # 单个通用词（例如不同指标文件共有的 ``sp``）不能决定实体归属；
        # 完全相同或一方是另一方的子集时，单词匹配才足够可靠。
        lexical = (
            len(overlap)
            if group_tokens == name_tokens
            or group_tokens.issubset(name_tokens)
            or name_tokens.issubset(group_tokens)
            else (len(overlap) if len(overlap) >= 2 else 0)
        )
        # 来源文件通常带有供应商或接口前缀（如
        # ``openalex_authors_details_results``），实体名只会贡献一个
        # 有区分度的词（``author``）。只要该词不是通用包装词，就允许
        # 这一条语义证据映射到种子实体；共享前缀但没有业务词的文件仍
        # 不会被映射（例如两个不同指标的 obs 文件）。
        if lexical == 0 and len(overlap) == 1:
            token = next(iter(overlap))
            if token not in {"openalex", "github", "search", "batch", "file"}:
                lexical = 1
        if tail_overlap:
            # 集合键是当前节点最直接的类型证据，优先级高于祖先路径中
            # 继承的实体词。
            lexical += len(tail_overlap) * 3
        # 文件名可能只有供应商/接口内部词；字段名仍能提供稳定的实体语义。
        field_overlap = len(record_tokens.intersection(name_tokens))
        short_stem = _tokens(name)
        short_stem = [token for token in short_stem if token not in {"research", "scholarly", "public", "data", "entity", "record"}]
        primary_id_bonus = 4 if any(
            f"{token}_id" in record_tokens for token in short_stem
        ) else 0
        # ``obs_*`` 是事实/观测集合的常见简称；它可帮助映射到
        # ``indicator observation``，但不会参与普通实体的前缀合并。
        is_seed_name = any(_slug(seed_name, "entity") == _slug(name, "entity") for seed_name in seed_entity_names)
        fact_bonus = 2 if (
            is_seed_name
            and "observation" in raw_name_tokens
            and "observation" in raw_group_tokens
            and bool(records)
        ) else 0
        return lexical * 2 + field_overlap + primary_id_bonus + fact_bonus

    scored_existing = sorted(
        (overlap_score(name), name)
        for name in existing
    )
    if scored_existing and scored_existing[-1][0] > 0:
        return scored_existing[-1][1]
    scored_seed = sorted((overlap_score(name), name) for name in seed_entity_names)
    preferred = {
        _slug(name, "entity")
        for name in (preferred_seed_entity_names or [])
        if str(name).strip()
    }
    preferred_scores = [
        item for item in scored_seed
        if _slug(item[1], "entity") in preferred and item[0] > 0
    ]
    if preferred_scores:
        # 种子实体是下游工具和任务的稳定接口；即使 raw 集合只使用
        # ``topic``/``work`` 这种短名，也应保留种子声明的 canonical 名称。
        return _slug(max(preferred_scores)[1], "entity")
    if scored_seed and scored_seed[-1][0] > 0:
        return _slug(scored_seed[-1][1], "entity")
    # 没有种子实体对应的嵌套对象仍应使用稳定的局部类型名（label、source、
    # institution 等），而不是把整条 URL/文件路径当作实体类型。关系候选
    # 保留完整前缀，避免不同父子关系被合并为同一个 relation 类型。
    if tail_tokens and not str(group_name).endswith("_relation"):
        return _slug(path_parts[-1], "entity")
    return group_name


def _field_name_matches(left: str, right: str) -> bool:
    """判断两个来源字段是否是同一语义字段的简单命名变体。"""

    if left == right:
        return True
    # ``id``、``name``、``value`` 等通用词不能通过后缀规则匹配到任意
    # ``*_id``/``*_name``/``*_value``，否则 country 表会被误认成 observation
    # 表，多个关系表也会因为共享一个外键而互相合并。
    generic = {"id", "name", "value", "code", "key", "url", "uri"}
    if left in generic or right in generic:
        return False
    return left.endswith(f"_{right}") or right.endswith(f"_{left}")


def _is_bridge_entity(
    entity_type: str,
    fields: dict[str, str] | set[str] | list[str] | tuple[str, ...],
) -> bool:
    """根据结构识别关系实体，而不是依赖领域名称。"""

    names = {str(field) for field in fields}
    foreign_keys = {field for field in names if _is_relation_key_field(field)}
    if len(foreign_keys) < 2:
        return False
    # 关系文件通常明确带有 relation_id；它即使有 entity_id，也不能作为
    # 可被其他实体单列引用的业务实体。
    if "relation_id" in names:
        return True

    # 只含 ID/编码/关系标志的记录是桥接关系（例如 issue_label）。一个
    # 普通业务实体也可能带多个外键（例如 scholarly_work 的 source_id），
    # 但它还应有可描述、可排序或可检索的业务字段。
    business_fields = {
        field
        for field in names
        if not _is_technical_field(field)
        and field not in {"relation_type", "is_primary", "is_current"}
    }
    if not business_fields:
        return True

    # 观测、事件、交易等事实实体通常有数值度量和时间字段；这类实体
    # 即便同时有多个外键，也不是桥接表。
    has_measure = any(
        field in {"value", "amount", "score", "count", "rate", "total"}
        or any(token in field.lower() for token in ("value", "amount", "score", "count", "rate", "total"))
        for field in business_fields
    )
    has_time = any(_is_temporal_field(field) for field in names)
    if has_measure and has_time:
        return False

    # 明确的实体自身主键 + 至少两个业务字段通常表示普通实体，而非
    # 关系行。实体名可能是复数或包含限定词，不能只检查字面上的
    # ``{entity_type}_id``（例如 awards 的自身键是 award_id）。
    primary_hint = _choose_primary_id(entity_type, names)
    relation_marker = any(
        token in str(entity_type).lower().split("_")
        for token in ("relation", "link", "mapping", "association")
    )
    if (
        primary_hint in foreign_keys
        and not relation_marker
        and len(business_fields) >= 1
    ):
        return False
    if f"{entity_type}_id" in names and len(business_fields) >= 2:
        return False
    return True


def _identifier_values(entity_type: str, records: list[dict[str, Any]]) -> set[str]:
    """提取实体主键值，用于识别换了文件名的同一实体。"""

    fields = {
        str(field)
        for record in records
        for field in record
        if _is_identifier_field(str(field))
    }
    primary = _choose_primary_id(entity_type, fields)
    if primary is None:
        return set()
    return {
        json.dumps(record.get(primary), ensure_ascii=False, sort_keys=True, default=str)
        for record in records
        if record.get(primary) not in (None, "")
    }


def _find_identifier_alias(
    group_name: str,
    records: list[dict[str, Any]],
    existing_groups: dict[str, list[dict[str, Any]]],
) -> str | None:
    """用字段/主键结构识别实体别名，避免按文件名拆分或误合并。"""

    new_fields = {str(field) for record in records for field in record}
    new_ids = {field for field in new_fields if _is_identifier_field(field)}
    new_business = {field for field in new_fields if not _is_technical_field(field)}
    group_tokens = _canonical_tokens(group_name)
    path_parts = [part for part in str(group_name).split("_") if part]
    tail_tokens: set[str] = set()
    if path_parts:
        last_tokens = _canonical_tokens(path_parts[-1])
        if path_parts[-1].lower() in _RAW_COLLECTION_KEYS:
            tail_tokens = set()
        elif last_tokens:
            tail_tokens = last_tokens
        elif (
            path_parts[-1].lower() not in _RAW_METADATA_KEYS
            and path_parts[-1].lower() not in _RAW_COLLECTION_KEYS
            and not _tokens(path_parts[-1]).intersection(_CANONICAL_STOP_TOKENS)
            and not re.fullmatch(r"[a-zA-Z]*\d+", path_parts[-1])
        ):
            for width in (2, 3):
                if len(path_parts) < width:
                    continue
                candidate_tokens = {
                    token
                    for token in _canonical_tokens("_".join(path_parts[-width:]))
                    if len(token) > 1
                }
                if candidate_tokens:
                    tail_tokens = candidate_tokens
                    break
    scored: list[tuple[int, int, int, int, str]] = []
    for entity_type, existing_records in existing_groups.items():
        if tail_tokens and not tail_tokens.intersection(_canonical_tokens(entity_type)):
            continue
        existing_fields = {str(field) for record in existing_records for field in record}
        existing_ids = {field for field in existing_fields if _is_identifier_field(field)}
        existing_business = {
            field for field in existing_fields if not _is_technical_field(field)
        }
        common_fields = sum(
            1
            for field in new_fields
            if any(_field_name_matches(field, other) for other in existing_fields)
        )
        common_ids = sum(
            1
            for field in new_ids
            if any(_field_name_matches(field, other) for other in existing_ids)
        )
        common_business = sum(
            1
            for field in new_business
            if any(_field_name_matches(field, other) for other in existing_business)
        )
        lexical = len(group_tokens.intersection(_canonical_tokens(entity_type)))
        if common_fields < 3 and not (common_fields >= 2 and common_business >= 1):
            continue
        unmatched_new_ids = len(new_ids - existing_ids)
        unmatched_existing_ids = len(existing_ids - new_ids)
        # 关系表只有若干外键时，必须有真正的业务字段；否则 issue_label
        # 之类的表会被错误当成 label 实体。两个关系表若各自还有不同
        # 的外键（region_id vs income_level_id），也不能仅因 relation_type
        # 相同而合并。
        if common_business == 0:
            continue
        # 没有实体名称证据时，两个共同字段不足以区分 country、region、
        # indicator 等都带 ``name``/``*_id`` 的表。此类结构匹配必须至少
        # 有两个业务字段，或由明确的实体词面/多个外键提供额外证据。
        # 观测/事实分片有时只保留两个外键和一个度量字段，文件名中的
        # 指标编码完全不同，因而没有词面交集；两个以上相同外键仍是
        # 足够强的结构证据。单个共享外键不能触发别名合并。
        if common_business <= 1 and lexical == 0 and common_ids < 2:
            continue
        if len(new_ids - existing_ids) > 2 and lexical == 0:
            continue
        if (
            common_business <= 1
            and unmatched_new_ids > 0
            and unmatched_existing_ids > 0
            and lexical < 2
        ):
            continue
        score = common_fields * 3 + common_business * 3 + common_ids * 2 + lexical
        scored.append(
            (
                score,
                common_business,
                common_ids,
                -int(_is_bridge_entity(entity_type, existing_fields)),
                entity_type,
            )
        )
    if not scored:
        return None
    return max(scored)[-1]


def _entity_group_alias_score(
    candidate_type: str,
    candidate_records: list[dict[str, Any]],
    existing_type: str,
    existing_records: list[dict[str, Any]],
) -> int:
    """按结构判断两个 entity group 是否是同一业务实体的不同视图。

    文件名不是可靠的实体身份（同一 API 常按指标、日期或分页拆文件），
    但仅凭一个共同字段也会把关系表误合并。这里要求名称、字段角色以及
    主键/外键结构至少有一组强证据；关系实体只有在名称明确相同或字段结构
    几乎一致时才允许合并。
    """

    candidate_fields = {str(field) for record in candidate_records for field in record}
    existing_fields = {str(field) for record in existing_records for field in record}
    if not candidate_fields or not existing_fields:
        return 0

    candidate_bridge = _is_bridge_entity(candidate_type, candidate_fields)
    existing_bridge = _is_bridge_entity(existing_type, existing_fields)
    candidate_tokens = _canonical_tokens(candidate_type)
    existing_tokens = _canonical_tokens(existing_type)
    lexical_equal = bool(
        candidate_tokens
        and existing_tokens
        and candidate_tokens == existing_tokens
        and _fact_entity_tokens(candidate_type) == _fact_entity_tokens(existing_type)
    )

    # 明确同名/同义实体可合并；两个关系实体若只是共享通用字段，不能靠
    # 词面中的 ``work`` 或 ``country`` 这种单词合并。
    if lexical_equal and candidate_bridge == existing_bridge:
        return 100
    if candidate_bridge != existing_bridge:
        return 0

    common_fields = {
        field
        for field in candidate_fields
        if field in existing_fields
        or any(_field_name_matches(field, other) for other in existing_fields)
    }
    candidate_ids = {
        field for field in candidate_fields if _is_identifier_field(field)
    }
    existing_ids = {
        field for field in existing_fields if _is_identifier_field(field)
    }
    # 按字段基名直接计算外键交集，避免把 ``id`` 这一通用键算作实体语义。
    candidate_id_bases = {
        tuple(sorted(_canonical_tokens(field[:-3] if field.endswith("_id") else field)))
        for field in candidate_ids
        if field not in {"id", "entity_id", "relation_id"}
    }
    existing_id_bases = {
        tuple(sorted(_canonical_tokens(field[:-3] if field.endswith("_id") else field)))
        for field in existing_ids
        if field not in {"id", "entity_id", "relation_id"}
    }
    foreign_overlap = len(candidate_id_bases.intersection(existing_id_bases))
    common_business = sum(
        1
        for field in common_fields
        if not _is_technical_field(field)
    )

    def has_numeric_measure(records: list[dict[str, Any]]) -> bool:
        for field in {str(field) for record in records for field in record}:
            if _is_technical_field(field):
                continue
            values = [
                record.get(field)
                for record in records
                if record.get(field) not in (None, "")
            ]
            if _primitive_type(values) in _NUMERIC_TYPES and _variation(records, field):
                return True
        return False

    def has_temporal(records: list[dict[str, Any]]) -> bool:
        return any(
            _is_temporal_field(field)
            and any(record.get(field) not in (None, "") for record in records)
            for field in {str(field) for record in records for field in record}
        )

    fact_tokens = {"observation", "event", "transaction", "measurement", "reading"}
    candidate_fact_tokens = {
        "observation" if token == "obs" else token
        for token in _tokens(candidate_type)
    }
    existing_fact_tokens = {
        "observation" if token == "obs" else token
        for token in _tokens(existing_type)
    }
    shared_fact_token = bool(
        fact_tokens.intersection(candidate_fact_tokens).intersection(existing_fact_tokens)
    )

    # 事实/观测实体经常按指标或时间拆成多份文件：两个以上相同的外键、
    # 一个变化的数值度量和一个变化的时间字段，是比文件名更强的通用证据。
    fact_shape = (
        foreign_overlap >= 2
        and has_temporal(candidate_records)
        and has_temporal(existing_records)
        and (
            # 数值度量是最常见的事实实体证据；如果某个分片整列缺失
            # （例如接口返回 null），共享的 observation/event 语义词仍
            # 允许它归入同一 canonical 类型，而不会丢掉其它记录。
            (
                has_numeric_measure(candidate_records)
                and has_numeric_measure(existing_records)
            )
            or shared_fact_token
        )
    )
    if fact_shape:
        return 80 + min(common_business, 3) * 2

    # 同一实体的不同分页/导出视图至少应共享三个字段，其中至少两个不是
    # 技术键；关系表则需要更高的相似度，避免 work_author/work_topic 误并。
    if common_business >= 3 and len(common_fields) >= 4:
        return 60 + common_business
    if candidate_bridge and existing_bridge:
        common_fk_count = len(
            {
                field for field in common_fields if field.endswith("_id")
            }
        )
        if common_fk_count >= 2 and candidate_tokens == existing_tokens:
            return 70
    return 0


def _variation(records: list[dict[str, Any]], field: str) -> bool:
    values = {
        json.dumps(record.get(field), ensure_ascii=False, sort_keys=True, default=str)
        for record in records
        if record.get(field) not in (None, "")
    }
    return len(values) >= 2


def _distinct_values(records: list[dict[str, Any]], field: str) -> set[str]:
    """返回字段的稳定非空取值集合。"""

    return {
        json.dumps(record.get(field), ensure_ascii=False, sort_keys=True, default=str)
        for record in records
        if record.get(field) not in (None, "")
    }


def _field_text_profile(records: list[dict[str, Any]], field: str) -> tuple[int, float]:
    values = [
        str(record[field]).strip()
        for record in records
        if record.get(field) not in (None, "") and isinstance(record.get(field), str)
    ]
    if not values:
        return 0, 0.0
    return len(set(values)), sum(len(value) for value in values) / len(values)


def _is_temporal_field(field: str) -> bool:
    lowered = field.lower()
    return any(token in lowered for token in ("year", "date", "time", "timestamp", "created", "updated", "published"))


def _is_text_field(records: list[dict[str, Any]], field: str) -> bool:
    """判断字符串字段是否适合全文搜索，而非仅作为分类枚举。"""

    if _is_temporal_field(field) or _is_technical_field(field):
        return False
    distinct, average_length = _field_text_profile(records, field)
    if distinct < 2:
        return False
    lowered = field.lower()
    # 业务名称可能很短（例如站点名、国家名、状态名），不能因为平均
    # 长度不足 20 个字符就失去 search 能力。字段仍必须有真实变化；ID、
    # URL 和时间字段已在上面排除。
    semantic_text_field = any(
        token in lowered
        for token in (
            "name",
            "label",
            "title",
            "description",
            "summary",
            "body",
            "abstract",
            "text",
            "content",
            "status",
            "state",
            "category",
            "type",
            "kind",
        )
    )
    return average_length >= 20 or semantic_text_field


def _is_category_field(records: list[dict[str, Any]], field: str) -> bool:
    """判断字符串字段是否存在有限的真实类别值。"""

    if _is_temporal_field(field) or _is_technical_field(field):
        return False
    distinct, average_length = _field_text_profile(records, field)
    if distinct < 2:
        return False
    # 高基数字段（例如每条记录唯一的 name）不适合作为类别筛选，但仍
    # 可以作为搜索字段。上限随样本量增长，避免对小数据集写死数量。
    max_categories = max(8, min(50, len(records) // 2))
    return distinct <= max_categories and average_length < 80


def _field_candidates(
    entity_type: str,
    records: list[dict[str, Any]],
    field_types: dict[str, str],
) -> list[str]:
    fields = list(field_types)
    return sorted(
        fields,
        key=lambda field: (
            0 if field in {"name", "title", "display_name", "description"} else 1,
            _field_quality(field),
            field,
        ),
    )


def _field_quality(field: str) -> int:
    """给工具候选字段排序，优先业务指标，避免把 decimal/ID 当主指标。"""

    lowered = field.lower()
    preferred = (
        "value", "score", "count", "amount", "total", "rate", "cited_by",
        "works_count", "h_index", "i10_index", "latitude", "longitude",
        "year", "date",
    )
    if any(token in lowered for token in preferred):
        return 0
    if lowered == "decimal" or lowered.endswith("_id") or lowered == "id" or lowered.endswith("_code"):
        return 3
    return 1


def _operation_family(name: str) -> str | None:
    return infer_operation_family(name)


def _select_entity_type(
    name: str,
    operation_family: str | None,
    entity_groups: dict[str, list[dict[str, Any]]],
    entity_fields: dict[str, dict[str, str]],
    *,
    target_entity_candidates: list[str] | None = None,
) -> str | None:
    """按名称和操作所需字段选择实体，而不是只取词面最相近的类型。"""

    name_tokens = _tokens(name)
    target_tokens = set(operation_target_tokens(name))
    if not target_tokens:
        target_tokens = name_tokens
    numeric_operation = operation_family in {"rank", "compare", "aggregate"}
    preferred_entity_types: set[str] = set()
    if target_entity_candidates:
        candidate_scores = {
            entity_type: max(
                (semantic_match_score(candidate, entity_type) for candidate in target_entity_candidates),
                default=0,
            )
            for entity_type in entity_groups
        }
        best_candidate_score = max(candidate_scores.values(), default=0)
        if best_candidate_score > 0:
            preferred_entity_types = {
                entity_type
                for entity_type, score in candidate_scores.items()
                if score == best_candidate_score
            }
    candidates: list[tuple[int, int, int, int, int, int, int, str]] = []
    for entity_type, records in entity_groups.items():
        if preferred_entity_types and entity_type not in preferred_entity_types:
            continue
        entity_tokens = _tokens(entity_type)
        overlap = len(name_tokens.intersection(entity_tokens))
        target_overlap = len(target_tokens.intersection(entity_tokens))
        if target_overlap == 0 and overlap == 0:
            continue
        fields = entity_fields.get(entity_type, {})
        compatible_fields = 0
        for field, expected in fields.items():
            varied = _variation(records, field)
            if not varied:
                continue
            if operation_family in {"rank", "compare", "aggregate"} and expected in _NUMERIC_TYPES:
                compatible_fields += 3 if _is_numeric_measure_field(field) else 1
            elif operation_family in {"search", "filter", "list"} and expected == "string":
                compatible_fields += 2 if not _is_technical_field(field) else 1
            elif operation_family in {"inspect", "traverse"}:
                compatible_fields += 1
        # 关系实体（例如 work_author）即使包含目标词，也不应抢走
        # inspect/search author 这类面向业务实体的操作。
        bridge_penalty = int(_is_bridge_entity(entity_type, fields))
        # 操作目标是业务实体的契约，不允许为了寻找数值字段而把
        # ``rank indicators`` 改成 ``rank indicator observations``。
        # semantic_match_score 区分精确目标（3）、包含目标的复合实体（2）
        # 和仅共享一个词的弱匹配（1），比布尔 subset 更能稳定处理
        # indicator / indicator_observation、author / work_author 等情况。
        target_match_score = semantic_match_score(
            " ".join(sorted(target_tokens)), entity_type
        ) if target_tokens else 0
        extra_tokens = len(entity_tokens - name_tokens)
        candidates.append(
            (
                target_match_score,
                target_overlap,
                -bridge_penalty,
                compatible_fields if numeric_operation else -extra_tokens,
                -extra_tokens if numeric_operation else compatible_fields,
                overlap,
                -len(entity_tokens),
                entity_type,
            )
        )
    if not candidates:
        return None
    # 所有操作族都先按目标实体语义排序；数值/文本字段只在同等目标
    # 匹配度下用于消除歧义。数值操作若声明 direct_or_related_fact，
    # 调用方还会在目标实体没有度量时寻找有闭合关系的事实实体；这不改变
    # 目标实体的语义，也不会静默换成无关关系表。
    return max(candidates)[-1]


def _related_fact_measure(
    target_entity: str,
    entity_groups: dict[str, list[dict[str, Any]]],
    entity_fields: dict[str, dict[str, str]],
    relations: list[dict[str, str]],
    *,
    min_distinct_values: int = 2,
) -> tuple[str, list[str], dict[str, str]] | None:
    """寻找可支撑目标实体数值操作的闭合事实实体。

    许多真实业务把定义对象（指标、设备、样品）与观测值拆成两张表。
    只有同时满足以下条件才允许跨表支撑 rank/compare/aggregate：

    * 候选实体名称含有 observation/event/measurement 等事实语义；
    * 候选实体有至少一个非技术、且真实存在变化的数值字段；
    * 已有关系明确把候选事实实体连接到目标实体。

    该规则只描述数据形状，不依赖具体领域或 API 名称。
    """

    candidates: list[tuple[int, str, list[str], dict[str, str]]] = []
    for fact_type, records in entity_groups.items():
        if fact_type == target_entity or not _fact_entity_tokens(fact_type):
            continue
        fields = entity_fields.get(fact_type, {})
        numeric_fields = [
            field
            for field, expected in fields.items()
            if expected in _NUMERIC_TYPES
            and _is_numeric_measure_field(field)
            and len(_distinct_values(records, field)) >= min_distinct_values
        ]
        if not numeric_fields:
            continue
        connecting = [
            relation
            for relation in relations
            if (
                relation.get("from_entity") == fact_type
                and relation.get("to_entity") == target_entity
            )
            or (
                relation.get("from_entity") == target_entity
                and relation.get("to_entity") == fact_type
            )
        ]
        if not connecting:
            continue
        # 优先使用关系描述明确指向目标实体的事实类型，其次使用记录量
        # 和数值字段丰富度；排序完全由库存决定，保持跨领域确定性。
        relation = sorted(
            connecting,
            key=lambda item: (
                0 if item.get("from_entity") == fact_type else 1,
                str(item.get("relation_id") or ""),
            ),
        )[0]
        score = len(records) + len(numeric_fields) * 10
        candidates.append((score, fact_type, sorted(numeric_fields), relation))
    if not candidates:
        return None
    _score, fact_type, fields, relation = max(
        candidates,
        key=lambda item: (item[0], item[1]),
    )
    return fact_type, fields, relation


def _resource_id_for_entity(entity_resource_id: str, entity_type: str) -> str:
    return entity_resource_id


def _evidence(
    resource_id: str,
    entity_type: str | list[str] | tuple[str, ...] | None,
    field_refs: list[str],
    note: str,
) -> dict[str, Any]:
    """构造证据；raw 证据不伪造 entity type，只保留资源级引用。"""

    if isinstance(entity_type, str):
        entity_types = [entity_type]
    elif isinstance(entity_type, (list, tuple)):
        entity_types = list(dict.fromkeys(str(value) for value in entity_type if str(value)))
    else:
        entity_types = []
    return {
        "resource_id": resource_id,
        "entity_types": entity_types,
        "field_refs": field_refs,
        "note": note,
    }


def _relation_field_compatible(
    source_field: str,
    target_type: str,
    target_field: str,
) -> bool:
    """只在外键字段和目标主键共享业务词时认定关系候选。

    不能因为目标实体存在任意 ``*_id`` 就建立关系；例如
    ``country_id`` 不应仅凭“目标表有一个主键”被解释为
    ``income_level_id``。严格的词交集会牺牲少量无法从字段名证明的关系，
    但可以避免生成错误的遍历工具。
    """

    source_base = source_field[:-3] if source_field.endswith("_id") else source_field
    target_base = target_field[:-3] if target_field.endswith("_id") else target_field
    if source_base in {"id", "entity"} or target_base in {"id", "entity"}:
        return False
    source_tokens = _canonical_tokens(source_base)
    target_tokens = _canonical_tokens(target_base)
    entity_tokens = _canonical_tokens(target_type)
    target_is_code = _is_identifier_field(target_field) and not target_field.endswith("_id")
    # 目标字段通常与目标实体同名，但目标实体词只能作为补充证据，
    # 不能单独使任意 source_field 成为关系。若目标是唯一业务编码，
    # ``country_id -> country.iso2code`` 这类跨编码关系允许用目标实体
    # 语义作为补充证据；唯一性由 _unique_key_fields 在记录级别证明。
    return bool(
        source_tokens
        and target_tokens
        and (
            bool(source_tokens.intersection(target_tokens))
            or (
                bool(source_tokens.intersection(entity_tokens))
                and (
                    bool(target_tokens.intersection(entity_tokens))
                    or target_is_code
                )
            )
        )
    )


def _relation_analysis(
    entity_groups: dict[str, list[dict[str, Any]]],
    entity_fields: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """同时返回闭合关系和有证据但未闭合的关系候选。

    闭合关系才能被工具安全遍历；未闭合候选不会进入 ``relations``，而是
    交给报告中的 ``relation_gaps``，避免数据缺口被静默吞掉。
    """

    def value_keys(records: list[dict[str, Any]], field: str) -> set[str]:
        return {
            json.dumps(record.get(field), ensure_ascii=False, sort_keys=True, default=str)
            for record in records
            if record.get(field) not in (None, "")
        }

    def primary_field(entity_type: str, records: list[dict[str, Any]]) -> str | None:
        fields = entity_fields.get(entity_type, {})
        names = set(fields)
        # 关系实体通常有两个以上外键，没有可作为被引用目标的单列主键。
        # 只允许唯一稳定键作为 target，避免把 country_id 对到每一张含有
        # country_id 的关系表。
        if _is_bridge_entity(entity_type, names):
            return None
        candidates = [
            f"{entity_type}_id",
            "id",
            "entity_id",
            _choose_primary_id(entity_type, names),
        ]
        for candidate in candidates:
            if not candidate or candidate not in names:
                continue
            values = value_keys(records, candidate)
            if values and len(values) == len(records):
                return candidate
        return None

    relations: list[dict[str, str]] = []
    relation_gaps: list[dict[str, Any]] = []
    used: set[str] = set()
    gap_keys: set[tuple[str, str, str, str]] = set()
    target_keys = {
        entity_type: _unique_key_fields(entity_type, records, set(entity_fields.get(entity_type, {})))
        for entity_type, records in entity_groups.items()
    }
    for source_type, source_records in entity_groups.items():
        for source_field in entity_fields.get(source_type, {}):
            # entity_id 是规范化技术键，不是指向另一实体的外键。
            if not _is_relation_key_field(source_field):
                continue
            source_values = value_keys(source_records, source_field)
            if not source_values:
                continue
            for target_type, target_records in entity_groups.items():
                if source_type == target_type:
                    continue
                candidate_fields = target_keys.get(target_type, [])
                if not candidate_fields:
                    continue
                # 按主键优先、备用唯一键其次尝试。一个源字段对同一
                # 目标实体只保留第一种有真实交集的键，避免重复关系。
                for target_field in candidate_fields:
                    if not _relation_field_compatible(source_field, target_type, target_field):
                        continue
                    target_values = value_keys(target_records, target_field)
                    if not target_values:
                        continue
                    matches = source_values.intersection(target_values)
                    if not matches:
                        continue
                    missing = source_values - target_values
                    relation_id = _slug(
                        f"{source_type}_{source_field}_{target_type}_{target_field}",
                        "relation",
                    )
                    candidate_key = (source_type, source_field, target_type, target_field)
                    if missing:
                        if candidate_key in gap_keys:
                            break
                        gap_keys.add(candidate_key)
                        relation_gaps.append(
                            {
                                "relation_id": relation_id,
                                "from_entity": source_type,
                                "field": source_field,
                                "to_entity": target_type,
                                "target_field": target_field,
                                "source_value_count": len(source_values),
                                "matched_value_count": len(matches),
                                "missing_value_count": len(missing),
                                "coverage_ratio": round(len(matches) / len(source_values), 6),
                                "description": (
                                    f"{source_type}.{source_field} 与 {target_type}.{target_field} "
                                    f"存在 {len(matches)}/{len(source_values)} 个匹配值，"
                                    f"仍缺少 {len(missing)} 个目标记录；未声明为可遍历关系。"
                                ),
                            }
                        )
                        break
                    if relation_id in used:
                        break
                    used.add(relation_id)
                    relations.append(
                        {
                            "relation_id": relation_id,
                            "from_entity": source_type,
                            "field": source_field,
                            "to_entity": target_type,
                            "target_field": target_field,
                            "description": f"{source_type}.{source_field} 引用 {target_type}.{target_field}，且所有真实引用都能匹配目标唯一键。",
                        }
                    )
                    break
    return relations, relation_gaps


def _infer_relations(
    entity_groups: dict[str, list[dict[str, Any]]],
    entity_fields: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """只返回闭合关系，保持原有调用方接口。"""

    relations, _gaps = _relation_analysis(entity_groups, entity_fields)
    return relations


def _infer_relation_gaps(
    entity_groups: dict[str, list[dict[str, Any]]],
    entity_fields: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """返回有真实交集但目标实体不完整的关系候选。"""

    _relations, gaps = _relation_analysis(entity_groups, entity_fields)
    return gaps


def _coalesce_same_named_entities(
    entity_groups: dict[str, list[dict[str, Any]]],
    *,
    seed_slugs: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """合并名称只是单复数/分页变体的普通实体。"""

    groups = dict(entity_groups)
    changed = True
    while changed:
        changed = False
        names = sorted(groups)
        for index, candidate_name in enumerate(names):
            if candidate_name not in groups or candidate_name.endswith("_relation"):
                continue
            for existing_name in names[:index]:
                if existing_name not in groups or existing_name.endswith("_relation"):
                    continue
                if (
                    _canonical_tokens(candidate_name) != _canonical_tokens(existing_name)
                    or _fact_entity_tokens(candidate_name) != _fact_entity_tokens(existing_name)
                ):
                    continue
                score = _entity_group_alias_score(
                    candidate_name,
                    groups[candidate_name],
                    existing_name,
                    groups[existing_name],
                )
                if score < 100:
                    continue
                # 种子实体名优先；否则保留记录更多的稳定名称，避免把一
                # 个完整主视图替换成较小的分页别名。
                candidate_is_seed = candidate_name in seed_slugs
                existing_is_seed = existing_name in seed_slugs
                if candidate_is_seed and not existing_is_seed:
                    target, source = candidate_name, existing_name
                elif existing_is_seed and not candidate_is_seed:
                    target, source = existing_name, candidate_name
                elif len(groups[candidate_name]) > len(groups[existing_name]):
                    target, source = candidate_name, existing_name
                else:
                    target, source = existing_name, candidate_name
                merged = _merge_group_records(
                    target,
                    groups[target],
                    groups[source],
                )
                if merged:
                    groups[target] = merged
                    del groups[source]
                    changed = True
                    break
            if changed:
                break
    return groups


def _relation_id_signature(records: list[dict[str, Any]]) -> tuple[str, ...]:
    """返回关系记录中真实外键字段的集合（忽略规范化技术键）。"""

    fields = {
        str(field)
        for record in records
        for field in record
        if _is_relation_key_field(str(field))
    }
    return tuple(sorted(fields))


def _canonical_relation_name(signature: tuple[str, ...]) -> str:
    """由外键字段生成与文件路径无关的关系类型名。"""

    stems = []
    for field in signature:
        stem = field[:-3] if field.endswith("_id") else field
        stems.append(stem)
    return _slug("_".join(stems) + "_relation", "relation")


def _coalesce_relation_groups(
    entity_groups: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """按外键结构合并跨分页/接口路径产生的重复关系候选。"""

    result: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(entity_groups):
        records = entity_groups[name]
        if not name.endswith("_relation"):
            result[name] = records
            continue
        signature = _relation_id_signature(records)
        if len(signature) < 2:
            # 没有两侧真实标识的候选不是可遍历关系；保留它供审计，
            # 但不要与其它关系猜测合并。
            result[name] = records
            continue
        canonical_name = _canonical_relation_name(signature)
        # 若 canonical 名称碰巧与普通实体冲突，保留原名，避免覆盖业务实体。
        if canonical_name in result and not canonical_name.endswith("_relation"):
            canonical_name = name
        if canonical_name not in result:
            result[canonical_name] = records
            continue
        merged = _merge_group_records(
            canonical_name,
            result[canonical_name],
            records,
        )
        if merged:
            result[canonical_name] = merged
    return result


def deterministic_entity_groups(
    package_root: Path,
    *,
    research_request: dict[str, Any],
    checkpoint: dict[str, Any],
    authoritative_raw: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """从 checkpoint 指定的 raw/entity 文件确定性生成唯一实体视图。

    该函数只读取已经提交的文件，不访问网络。它既是 metadata 兜底的实现
    基线，也是校验 metadata Agent 新建规范化文件时的可复算证据。
    """

    package_root = package_root.resolve()
    workspace = package_root / "workspace"
    entity_paths = [str(value) for value in checkpoint.get("entity_files", []) if isinstance(value, str)]
    raw_paths = [str(value) for value in checkpoint.get("raw_files", []) if isinstance(value, str)]

    entity_candidates: dict[str, list[dict[str, Any]]] = {}
    if not authoritative_raw:
        for relative in entity_paths:
            try:
                groups = _read_entity_groups(workspace / relative)
            except (OSError, UnicodeError, json.JSONDecodeError, csv.Error, sqlite3.Error):
                continue
            for entity_type, records in groups.items():
                entity_candidates.setdefault(entity_type, []).extend(records)

    seed_entity_names = [
        str(requirement.get("name"))
        for requirement in research_request.get("requirements", [])
        if isinstance(requirement, dict) and requirement.get("kind") == "seed_entity"
    ]
    # 操作目标也提供实体语义。某些种子只声明复合事实实体（例如
    # ``indicator observation``），但 ``rank indicators`` 明确要求同时保留
    # 可排序的 indicator 定义实体；将目标候选加入命名推断可以识别这类
    # 扩展实体，而不把它们误归为文件名最后一段（如 ``cd``、``totl``）。
    semantic_entity_names = list(seed_entity_names)
    for requirement in research_request.get("requirements", []):
        if not isinstance(requirement, dict) or requirement.get("kind") != "seed_operation":
            continue
        candidates = requirement.get("target_entity_candidates", [])
        if isinstance(candidates, list):
            semantic_entity_names.extend(
                str(candidate) for candidate in candidates if str(candidate).strip()
            )
    semantic_entity_names = list(dict.fromkeys(semantic_entity_names))

    # 先处理与 seed 实体精确对应的 group，再处理分页、指标或日期后缀的
    # 视图。这样 ``indicator_observation`` 会成为稳定 canonical 类型，后续
    # ``obs_<indicator>_<year>`` 才能按结构归入它，而不会反过来抢走名称。
    seed_slugs = {
        _slug(name, "entity")
        for name in seed_entity_names
        if str(name).strip()
    }
    ordered_entity_names = sorted(
        entity_candidates,
        key=lambda name: (0 if _slug(name, "entity") in seed_slugs else 1, _slug(name, "entity"), name),
    )
    entity_groups: dict[str, list[dict[str, Any]]] = {}
    for group_name in ordered_entity_names:
        normalized = _normalize_groups({group_name: entity_candidates[group_name]})
        if not normalized:
            continue
        records = next(iter(normalized.values()))
        # 统一精确匹配 seed 的名称，避免 ``countries``、``country_records``
        # 在最终环境中形成多个近似实体类型。
        canonical_name = next(
            (
                _slug(seed_name, "entity")
                for seed_name in seed_entity_names
                if _canonical_tokens(group_name)
                and (
                    _canonical_tokens(group_name) == _canonical_tokens(seed_name)
                )
            ),
            _slug(group_name, "entity"),
        )
        aliases = [
            (
                _entity_group_alias_score(canonical_name, records, existing_name, existing_records),
                existing_name,
            )
            for existing_name, existing_records in entity_groups.items()
        ]
        aliases = [item for item in aliases if item[0] > 0]
        if aliases:
            best_score, best_name = max(aliases)
            # 分数 100 是名称/seed 的确定性同义匹配；80+ 是观测/事实
            # 结构匹配。统一保留已有 canonical 视图，避免把 raw 的别名
            # 字段（country_value/date 等）混进规范化字段。
            if best_score >= 60:
                # seed 对应的实体是最终 canonical 视图，分片只作为 raw
                # 证据保留，不把命名不同、字段不完整的分片拼进去。否则
                # 合并后按 95% 完整度过滤字段，会让 year/date 等字段消失，
                # 反过来又无法识别下一份同类分片。
                if best_name == canonical_name or best_name in seed_slugs:
                    continue
                # 同一 canonical 类型来自多个 entity 文件时，只有字段
                # 结构兼容才合并；否则较早的 seed/主视图保持权威，后者由
                # raw 文件保留证据，不制造第二个实体类型。
                existing_records = entity_groups[best_name]
                existing_fields = {field for record in existing_records for field in record}
                candidate_fields = {field for record in records for field in record}
                common = len(existing_fields.intersection(candidate_fields))
                if common >= max(3, min(len(existing_fields), len(candidate_fields)) // 2):
                    merged = _merge_group_records(
                        best_name,
                        existing_records,
                        records,
                    )
                    if merged:
                        entity_groups[best_name] = merged
                continue
        if canonical_name in entity_groups:
            merged = _merge_group_records(
                canonical_name,
                entity_groups[canonical_name],
                records,
            )
            if merged:
                entity_groups[canonical_name] = merged
        else:
            entity_groups[canonical_name] = records

    for relative in raw_paths:
        try:
            groups = _raw_record_groups(
                workspace / relative,
                seed_entity_names=semantic_entity_names,
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        for group_name, records in groups.items():
            # 先根据当前集合和自身字段推断语义类型，再用已有实体做别名
            # 合并。这样一个 work 响应不会因为共享 display_name/primary_topic
            # 字段而被先到的 topic 实体抢走。
            inferred_type = _canonical_entity_type(
                group_name,
                existing=set(),
                seed_entity_names=semantic_entity_names,
                preferred_seed_entity_names=seed_entity_names,
                records=records,
            )
            alias = _find_identifier_alias(group_name, records, entity_groups)
            # ``_find_identifier_alias`` 先用字段结构找候选，但多个关系表
            # 可能共享外键字段，不能把这个候选直接当成最终类型。只有同义
            # 名称或强实体结构证据（>=80）才允许合并；否则保留当前集合
            # 的局部类型，避免跨关系误吞。
            alias_score = (
                _entity_group_alias_score(
                    inferred_type,
                    records,
                    alias,
                    entity_groups[alias],
                )
                if alias is not None and alias in entity_groups
                else 0
            )
            entity_type = alias if alias is not None and alias_score >= 80 else inferred_type
            normalized = _normalize_groups({entity_type: records})
            if not normalized:
                continue
            candidate_records = normalized.get(entity_type, [])
            if not candidate_records:
                continue
            if entity_type not in entity_groups:
                entity_groups[entity_type] = candidate_records
                continue

            # 同一业务实体可能来自多个分页、详情或查询文件。只合并结构
            # 兼容的候选，并按实体主键去重；关系桥和业务实体不会因为
            # 共享一个 ``*_id`` 被强行拼在一起。
            existing_records = entity_groups[entity_type]
            alias_score = _entity_group_alias_score(
                entity_type,
                candidate_records,
                entity_type,
                existing_records,
            )
            if alias_score <= 0:
                continue
            merged = _merge_group_records(
                entity_type,
                existing_records,
                candidate_records,
            )
            if merged:
                entity_groups[entity_type] = merged
    # 同义普通实体和同结构关系可能来自多个 API 文件/分页。合并发生在
    # 所有 raw 候选收集完成之后，因此不会因为处理顺序把一个关系别名
    # 当成另一个实体，也不会让同一类 relation 膨胀成路径名集合。
    entity_groups = _coalesce_same_named_entities(
        entity_groups,
        seed_slugs=seed_slugs,
    )
    entity_groups = _coalesce_relation_groups(entity_groups)
    # 正式 DataGen 只把 raw 视为业务事实源。Agent entity 文件仍由 checkpoint
    # 留存，供人工审计和 provenance 使用，但不能在 raw 没有对应记录时扩展
    # canonical 实体库存；否则模型可以通过 entity 文件注入虚构业务记录。
    return entity_groups


def build_metadata(
    package_root: Path,
    *,
    seed: dict[str, Any],
    research_request: dict[str, Any],
    checkpoint: dict[str, Any],
    preferred_entity_path: str | None = None,
) -> dict[str, Any]:
    """根据 checkpoint 和实际文件生成 environment、sources、research_report。

    checkpoint 中的 raw/entity 文件是数据 Agent 的明确交付物；它们统一经过
    确定性规范化后只暴露一个 entity 资源。这样兜底编译器不会把同一批业务
    记录复制成两个互相竞争的实体视图。
    """

    package_root = package_root.resolve()
    workspace = package_root / "workspace"
    raw_paths = [str(value) for value in checkpoint.get("raw_files", []) if isinstance(value, str)]
    entity_paths = [str(value) for value in checkpoint.get("entity_files", []) if isinstance(value, str)]
    derived_paths = [str(value) for value in checkpoint.get("derived_files", []) if isinstance(value, str)]

    # 所有实体输入都经过同一个确定性规范化函数。这样可选字段、嵌套字段和
    # 多外键关系不会直接污染最终 Schema；原始输入仍由 checkpoint/provenance
    # 保留，最终 workspace 只暴露一个规范化实体视图。
    entity_groups = deterministic_entity_groups(
        package_root,
        research_request=research_request,
        checkpoint=checkpoint,
        authoritative_raw=True,
    )

    def field_types_for(records: list[dict[str, Any]]) -> dict[str, str]:
        field_names = sorted(set().union(*(record.keys() for record in records))) if records else []
        return {
            field: _primitive_type([record.get(field) for record in records]) or "string"
            for field in field_names
        }

    def entity_definition(entity_type: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        """把确定性画像转换为环境协议要求的字段定义。

        画像和关系分析内部仍使用简单的 ``field -> type`` 映射；只有写入
        environment.json 时才为每个字段补充参数式业务说明。
        """

        field_types = field_types_for(records)
        fields = {
            field: {
                "type": field_type,
                "description": f"{entity_type} 记录中的 {field} 业务字段。",
            }
            for field, field_type in field_types.items()
        }
        return {
            "description": f"从公开数据中抽取的 {entity_type} 实体记录。",
            "fields": fields,
        }

    entity_fields = {
        entity_type: field_types_for(records)
        for entity_type, records in entity_groups.items()
        if records
    }

    used_resource_ids: set[str] = set()
    resources: list[dict[str, Any]] = []
    raw_resource_ids: list[str] = []
    for relative in raw_paths:
        resource_id = _unique_slug(f"raw_{Path(relative).stem}", used_resource_ids, "raw_resource")
        raw_resource_ids.append(resource_id)
        resources.append(
            {
                "resource_id": resource_id,
                "name": f"原始文件 {Path(relative).name}",
                "description": "调研阶段从公开来源保存的原始响应，业务工具只读。",
                "data_type": "raw",
                "storage_type": "file",
                "path": relative,
                "format": _format_for(Path(relative)),
                "writable": False,
            }
        )

    entity_resource_by_type: dict[str, str] = {}
    entity_resource_ids: list[str] = []
    entity_source_ids = list(raw_resource_ids)

    def entity_schema_for(groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return {
            entity_type: {
                **entity_definition(entity_type, records),
            }
            for entity_type, records in sorted(groups.items())
            if records
        }

    # 使用单一规范化视图作为最终实体资源。若 Agent 已经创建同名文件，使用
    # 另一个确定性文件名，避免静默覆盖未提交的业务文件；DataGenerator 会在
    # 校验前验证该文件内容并把旧的中间文件移出 workspace。
    if entity_groups:
        generated_relative = "entities/normalized_entities.json"
        # 已经被 DataGenerator 验证过的规范化文件可以原地重写；其它同名
        # 文件保留在 workspace 中等待边界校验，因此使用稳定的备用名称。
        if (workspace / generated_relative).exists() and preferred_entity_path != generated_relative:
            generated_relative = "entities/normalized_entities_generated.json"
        generated_path = workspace / generated_relative
        generated_path.parent.mkdir(parents=True, exist_ok=True)
        generated_path.write_text(
            json.dumps(entity_groups, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        resource_id = _unique_slug("normalized_entities", used_resource_ids, "entities")
        entity_resource_ids.append(resource_id)
        resources.append(
            {
                "resource_id": resource_id,
                "name": "规范化业务实体",
                "description": "由已提交 raw/entity 文件确定性规范化的唯一业务实体视图。",
                "data_type": "entity",
                "storage_type": "file",
                "path": generated_relative,
                "format": "json",
                "writable": False,
                "source_resources": entity_source_ids,
                "entity_schema": entity_schema_for(entity_groups),
            }
        )
        for entity_type in entity_groups:
            entity_resource_by_type.setdefault(entity_type, resource_id)

    for relative in derived_paths:
        path = workspace / relative
        if not path.is_file():
            continue
        resource_id = _unique_slug(f"derived_{Path(relative).stem}", used_resource_ids, "derived_resource")
        resources.append(
            {
                "resource_id": resource_id,
                "name": f"派生数据 {Path(relative).name}",
                "description": "根据原始或实体数据确定性计算的索引或统计结果。",
                "data_type": "derived",
                "storage_type": "file",
                "path": relative,
                "format": _format_for(Path(relative)),
                "writable": False,
                "source_resources": entity_resource_ids or list(raw_resource_ids),
            }
        )

    environment_id = _slug(seed.get("theme_id") or "generated_environment", "environment")
    environment = {
        "schema_version": "1.0",
        "environment_id": environment_id,
        "name": str(seed.get("seed_label") or environment_id),
        "description": f"{seed.get('seed_label') or environment_id} 的真实公开数据工作区。",
        "resources": resources,
        "rules": [],
    }

    relations, relation_gaps = _relation_analysis(entity_groups, entity_fields)
    for relation in relations:
        involved = {
            entity_resource_by_type.get(relation["from_entity"]),
            entity_resource_by_type.get(relation["to_entity"]),
        }
        environment["rules"].append(
            {
                "description": relation["description"],
                "resources": sorted(resource_id for resource_id in involved if resource_id),
            }
        )

    source_urls = [
        value for value in checkpoint.get("source_urls", [])
        if isinstance(value, str) and value.startswith(("http://", "https://"))
    ]
    if not source_urls and isinstance(seed.get("source_url"), str) and seed["source_url"].startswith(("http://", "https://")):
        source_urls = [seed["source_url"]]
    retrieved_at = datetime.now(timezone.utc).isoformat()
    sources: list[dict[str, Any]] = []
    resource_by_path = {
        relative: resource_id
        for relative, resource_id in zip(raw_paths, raw_resource_ids)
        if (workspace / relative).is_file()
    }

    source_file_map = checkpoint.get("source_file_map")
    mapped_sources: list[tuple[str, list[str]]] = []
    if isinstance(source_file_map, list):
        for item in source_file_map:
            if not isinstance(item, dict):
                continue
            source_url = item.get("url")
            file_paths = item.get("file_paths")
            if not isinstance(source_url, str) or not source_url.startswith(("http://", "https://")):
                continue
            if not isinstance(file_paths, list):
                continue
            paths = [
                value
                for value in file_paths
                if isinstance(value, str) and value in resource_by_path
            ]
            if paths:
                mapped_sources.append((source_url, paths))

    # ready checkpoint 要求每个 raw 文件都有精确来源映射。这里不再把
    # 一个文档首页或第一个 URL 退化套到所有文件；缺失映射会由 Validator
    # 拒绝发布，而不是产生看似完整但不可审计的 provenance。
    source_items = mapped_sources

    for source_url, mapped_paths in source_items:
        parsed_url = urlparse(source_url)
        source_type = (
            "official_api"
            if "api" in parsed_url.netloc.lower() or "/api" in parsed_url.path.lower()
            else "official_repository"
            if "github.com" in parsed_url.netloc.lower()
            else "official_dataset"
        )
        sources.append(
            {
                "source_id": _slug(f"source_{parsed_url.netloc}_{len(sources) + 1}", "source"),
                "url": source_url,
                "source_type": source_type,
                "retrieved_at": retrieved_at,
                "license_or_access_note": str(seed.get("license_or_access_note") or "来源条款见原始 URL。"),
                "resource_ids": [resource_by_path[path] for path in mapped_paths],
                "files": [{"path": path} for path in mapped_paths],
            }
        )

    requirements = research_request.get("requirements", [])
    policy = research_request.get("quality_policy", {})
    coverage: list[dict[str, Any]] = []
    seed_entity_types: set[str] = set()
    unavailable_requirements: list[dict[str, Any]] = []
    for requirement in requirements:
        requirement_id = requirement.get("requirement_id")
        name = str(requirement.get("name") or "")
        kind = requirement.get("kind")
        if kind == "seed_scope":
            evidence = [_evidence(raw_resource_ids[0], None, [], "范围证据来自实际访问的原始响应。")] if raw_resource_ids else []
            status = "covered" if evidence else "unavailable"
            item: dict[str, Any] = {
                "requirement_id": requirement_id,
                "status": status,
                "evidence": evidence,
                "explanation": "已保存与种子范围对应的公开原始响应。" if evidence else "没有可用原始响应。",
            }
            if status != "covered":
                unavailable_requirements.append(requirement)
        else:
            operation_family = _operation_family(name) if kind == "seed_operation" else None
            minimum_records = int(requirement.get("minimum_records", 1) or 0)
            target_candidates = requirement.get("target_entity_candidates")
            entity_type = _select_entity_type(
                name,
                operation_family,
                entity_groups,
                entity_fields,
                target_entity_candidates=(
                    [str(value) for value in target_candidates if isinstance(value, str)]
                    if isinstance(target_candidates, list)
                    else None
                ),
            )
            resource_id = entity_resource_by_type.get(entity_type or "")
            field_refs: list[str] = []
            # 通常一个操作只需要目标实体证据；数值字段位于事实表时，
            # 这里会追加第二条 evidence，并保留目标实体作为语义目标。
            evidence_items: list[dict[str, Any]] = []
            operation_available = True
            if entity_type and resource_id:
                fields = entity_fields[entity_type]
                # 记录深度是每个核心要求的局部门槛，不能被其它实体的
                # 大量扩展记录替代。metadata 编译器先标记为 unavailable，
                # Validator 再用同一库存复核，缺口最终会触发停止发布。
                entity_has_minimum_records = (
                    len(entity_groups[entity_type]) >= minimum_records
                )
                operation_available = entity_has_minimum_records
                candidates = _field_candidates(entity_type, entity_groups[entity_type], fields)
                if operation_family in {"rank", "compare", "aggregate"}:
                    min_distinct_values = int(
                        policy.get("min_operation_distinct_values", 2)
                    )
                    numeric = [
                        field
                        for field in candidates
                        if fields[field] in _NUMERIC_TYPES
                        and len(_distinct_values(entity_groups[entity_type], field)) >= min_distinct_values
                        and _is_numeric_measure_field(field)
                    ]
                    # 数值操作没有业务数值字段就不能算“已覆盖”。
                    # 不再回退到 name、ID 或 source_note，也不允许换用
                    # 一个语义相近但不是目标实体的事实表来伪造覆盖。
                    candidates = numeric
                    operation_available = operation_available and bool(candidates)
                    if not candidates and requirement.get("target_resolution") == "direct_or_related_fact":
                        related = _related_fact_measure(
                            entity_type,
                            entity_groups,
                            entity_fields,
                            relations,
                            min_distinct_values=min_distinct_values,
                        )
                        if related is not None and len(entity_groups[entity_type]) >= minimum_records:
                            fact_type, fact_fields, relation = related
                            # 目标实体保留稳定键和可读名称作为语义上下文，
                            # 数值字段来自真实事实实体；两端关系字段显式
                            # 纳入证据，供后续 ToolGen 安全生成 join/聚合。
                            target_id = _choose_primary_id(entity_type, set(fields))
                            target_text = next(
                                (
                                    field
                                    for field in _field_candidates(
                                        entity_type,
                                        entity_groups[entity_type],
                                        fields,
                                    )
                                    if not _is_technical_field(field)
                                    and fields[field] == "string"
                                    and _variation(entity_groups[entity_type], field)
                                ),
                                None,
                            )
                            direct_refs = [
                                f"{entity_type}.{field}"
                                for field in (target_id, target_text)
                                if field
                            ]
                            relation_refs = [
                                f"{relation['from_entity']}.{relation['field']}",
                                f"{relation['to_entity']}.{relation['target_field']}",
                            ]
                            fact_refs = [
                                f"{fact_type}.{field}" for field in fact_fields[:2]
                            ]
                            field_refs = list(dict.fromkeys(direct_refs + relation_refs + fact_refs))
                            evidence_items = [
                                _evidence(
                                    resource_id,
                                    entity_type,
                                    direct_refs,
                                    "目标实体来自真实定义记录；数值通过闭合事实关系取得。",
                                ),
                                _evidence(
                                    entity_resource_by_type[fact_type],
                                    [
                                        fact_type,
                                        relation["from_entity"],
                                        relation["to_entity"],
                                    ],
                                    list(dict.fromkeys(relation_refs + fact_refs)),
                                    "事实实体提供真实变化的数值字段，并通过闭合关系连接目标实体。",
                                ),
                            ]
                            operation_available = True
                elif operation_family in {"search", "filter", "list"}:
                    text = [
                        field
                        for field in candidates
                        if fields[field] == "string"
                        and len(_distinct_values(entity_groups[entity_type], field))
                        >= int(policy.get("min_operation_distinct_values", 2))
                        and not _is_technical_field(field)
                    ]
                    if operation_family in {"search", "filter"}:
                        # 搜索/筛选必须有真实变化的业务字符串；ID、URL
                        # 和常量字段不能作为核心操作证据。
                        candidates = text
                    else:
                        # list 只要求目标实体有可区分的真实字段，但优先
                        # 使用业务文本；技术 ID 不能单独支撑“列出”操作。
                        candidates = text + [
                            field for field in candidates
                            if not _is_technical_field(field)
                            and len(_distinct_values(entity_groups[entity_type], field))
                            >= int(policy.get("min_operation_distinct_values", 2))
                        ]
                    operation_available = operation_available and bool(candidates)
                elif operation_family == "timeline":
                    temporal = [
                        field
                        for field in candidates
                        if _is_temporal_field(field)
                        and len(_distinct_values(entity_groups[entity_type], field))
                        >= int(policy.get("min_operation_distinct_values", 2))
                        and not _is_technical_field(field)
                    ]
                    candidates = temporal + candidates
                    operation_available = operation_available and bool(temporal)
                for field in candidates:
                    ref = f"{entity_type}.{field}"
                    if ref not in field_refs:
                        field_refs.append(ref)
                    if len(field_refs) >= 2:
                        break
                if kind == "seed_operation":
                    if operation_family == "inspect":
                        # inspect 必须能返回可读业务属性，只有 ID/URL 的
                        # 技术记录不能作为可用的实体详情接口。
                        operation_available = operation_available and bool(field_refs) and any(
                            not _is_technical_field(field)
                            and field in fields
                            and any(
                                record.get(field) not in (None, "")
                                for record in entity_groups[entity_type]
                            )
                            for field in fields
                        )
                    elif operation_family == "list":
                        operation_available = (
                            len(entity_groups[entity_type]) >= 2
                            and bool(field_refs)
                        )
                    elif operation_family == "traverse":
                        matching_relations = [
                            relation
                            for relation in relations
                            if relation["from_entity"] == entity_type
                            or relation["to_entity"] == entity_type
                        ]
                        operation_available = operation_available and bool(matching_relations)
                        if operation_available:
                            # 关系操作的证据必须同时包含真实外键两端，
                            # 让 ToolGen 获得可执行的遍历方向。
                            relation = matching_relations[0]
                            field_refs = [
                                f"{relation['from_entity']}.{relation['field']}",
                                f"{relation['to_entity']}.{relation['target_field']}",
                            ]
                    elif operation_family == "audit":
                        operation_available = (
                            operation_available
                            and bool(relations or derived_paths)
                            and bool(field_refs)
                        )
                    elif operation_family in {"mutate", "export"}:
                        # DataGen 只产生只读公开快照，没有可重置的本地
                        # 可变实体或输出资源；不能把这种能力误报给 ToolGen。
                        operation_available = False
                    else:
                        # 所有未特殊处理的操作族至少要有字段证据；实体
                        # 存在本身不能证明排序、搜索等操作可以执行。
                        operation_available = operation_available and bool(field_refs)
                if kind == "seed_entity":
                    seed_entity_types.add(entity_type)
            elif kind == "seed_entity":
                operation_available = False
            evidence = evidence_items or (
                [_evidence(resource_id or "", entity_type, field_refs, "字段和记录来自已提交的实体文件。")]
                if entity_type and resource_id
                else []
            )
            status = "covered" if evidence and (kind != "seed_operation" or operation_available) else "unavailable"
            item = {
                "requirement_id": requirement_id,
                "status": status,
                "evidence": evidence,
                "explanation": "已由真实实体记录和字段支撑。" if status == "covered" else "没有找到足以支撑该种子要求的真实实体字段或运行边界。",
            }
            if operation_family:
                item["operation_family"] = operation_family
            if kind == "seed_operation" and entity_type:
                # 把确定性选择结果写入报告，供后续 ToolGen 直接绑定目标
                # 实体；Validator 仍会用库存再次核验，不能仅凭该字段自证。
                item["target_entity"] = entity_type
            if status != "covered":
                unavailable_requirements.append(requirement)
        coverage.append(item)

    extensions: list[dict[str, Any]] = []
    if entity_resource_ids:
        candidates: list[tuple[str, str, str, str]] = []
        for entity_type, fields in sorted(entity_fields.items()):
            for field, expected in fields.items():
                records = entity_groups[entity_type]
                if (
                    not _variation(records, field)
                    or _is_technical_field(field)
                    or (expected in _NUMERIC_TYPES and not _is_numeric_measure_field(field))
                ):
                    continue
                if expected in _NUMERIC_TYPES:
                    # 同一真实指标可以支持排序、比较和聚合三种不同的
                    # 工具语义；它们不是重复能力，后续 ToolGen 可据此生成
                    # 不同参数和返回结构。
                    candidates.extend(
                        (
                            (f"rank_{entity_type}_by_{field}", "rank", entity_type, field),
                            (f"compare_{entity_type}_by_{field}", "compare", entity_type, field),
                            (f"aggregate_{entity_type}_by_{field}", "aggregate", entity_type, field),
                        )
                    )
                    if _is_temporal_field(field):
                        candidates.append(
                            (f"timeline_{entity_type}_by_{field}", "timeline", entity_type, field)
                        )
                elif expected == "string":
                    if _is_text_field(records, field):
                        candidates.append((f"search_{entity_type}_by_{field}", "search", entity_type, field))
                    if _is_category_field(records, field):
                        candidates.append((f"filter_{entity_type}_by_{field}", "filter", entity_type, field))
        for relation in relations:
            candidates.append(
                (
                    f"traverse_{relation['from_entity']}_to_{relation['to_entity']}",
                    "traverse",
                    relation["from_entity"],
                    relation["field"],
                )
            )
        used_capabilities: set[str] = set()
        selected: list[tuple[str, str, str, str]] = []
        selected_families: set[str] = set()
        required_count = int(
            policy.get("extension_capability_target", policy.get("min_extension_capabilities", 0))
        )
        required_families = int(
            policy.get(
                "extension_operation_family_target",
                policy.get("min_extension_operation_families", 0),
            )
        )
        # 先覆盖不同操作族，再补足数量，避免按字母顺序只得到一组
        # rank。排序只影响确定性，不会把没有真实字段证据的能力补进来。
        family_order = {
            # 关系和聚合通常是种子没有直接列出的能力，优先交给
            # ToolGen；search/filter/rank 等已有核心族放到后面补足数量。
            "traverse": 0,
            "aggregate": 1,
            "compare": 2,
            "filter": 3,
            "timeline": 4,
            "search": 5,
            "rank": 6,
        }
        seed_families = {
            str(item.get("operation_family"))
            for item in research_request.get("requirements", [])
            if isinstance(item, dict)
            and item.get("kind") == "seed_operation"
            and item.get("operation_family")
        }
        ordered_candidates = sorted(
            candidates,
            key=lambda item: (
                1 if item[1] in seed_families else 0,
                family_order.get(item[1], 99),
                item[2],
                _field_quality(item[3]),
                item[3],
                item[0],
            ),
        )
        for candidate in ordered_candidates:
            if len(selected_families) >= required_families:
                break
            if candidate[1] in selected_families:
                continue
            selected.append(candidate)
            selected_families.add(candidate[1])
        for candidate in ordered_candidates:
            if len(selected) >= required_count:
                break
            if candidate not in selected:
                selected.append(candidate)

        for capability_id, family, entity_type, field in selected:
            capability_id = _slug(capability_id, "capability")
            resource_id = entity_resource_by_type.get(entity_type)
            if capability_id in used_capabilities or resource_id is None:
                continue
            used_capabilities.add(capability_id)
            evidence_refs = [f"{entity_type}.{field}"]
            evidence_entities = [entity_type]
            description = f"对 {entity_type} 的 {field} 执行 {family} 操作。"
            if family == "traverse":
                relation = next(
                    (
                        item
                        for item in relations
                        if item["from_entity"] == entity_type
                        and item["field"] == field
                    ),
                    None,
                )
                if relation is not None:
                    evidence_refs = [
                        f"{relation['from_entity']}.{relation['field']}",
                        f"{relation['to_entity']}.{relation['target_field']}",
                    ]
                    evidence_entities = [
                        relation["from_entity"],
                        relation["to_entity"],
                    ]
                    description = (
                        f"沿 {relation['from_entity']}.{relation['field']} 访问 "
                        f"{relation['to_entity']}.{relation['target_field']}。"
                    )
                if relation is not None:
                    evidence_entities = list(dict.fromkeys(evidence_entities))
            extensions.append(
                {
                    "capability_id": capability_id,
                    "description": description,
                    "operation_family": family,
                    "evidence": [_evidence(resource_id, evidence_entities, evidence_refs, "真实记录提供该扩展能力的字段证据。")],
                }
            )
            if len(extensions) >= required_count:
                break

    dimensions: list[dict[str, Any]] = []
    if entity_resource_ids:
        used_dimension_refs: set[str] = set()
        dimension_specs = (
            ("numeric", lambda records, field, expected: expected in _NUMERIC_TYPES and _is_numeric_measure_field(field)),
            ("temporal", lambda records, field, expected: _is_temporal_field(field) and not _is_technical_field(field)),
            ("category", lambda records, field, expected: expected == "string" and not _is_technical_field(field) and _is_category_field(records, field)),
            ("text", lambda records, field, expected: expected == "string" and not _is_technical_field(field) and _is_text_field(records, field)),
        )
        for kind, predicate in dimension_specs:
            found = False
            for entity_type, fields in sorted(entity_fields.items()):
                ordered_fields = sorted(
                    fields.items(),
                    key=lambda item: (
                        0 if not _is_identifier_field(item[0]) and not item[0].lower().endswith(("_url", "_uri")) else 1,
                        0 if item[0] in {"name", "title", "display_name", "description"} else 1,
                        _field_quality(item[0]),
                        item[0],
                    ),
                )
                for field, expected in ordered_fields:
                    resource_id = entity_resource_by_type.get(entity_type)
                    ref = f"{entity_type}.{field}"
                    if (
                        resource_id
                        and ref not in used_dimension_refs
                        and predicate(entity_groups[entity_type], field, expected)
                        and _variation(entity_groups[entity_type], field)
                    ):
                        dimensions.append(
                            {
                                "dimension_id": _slug(f"{kind}_{entity_type}_{field}", "dimension"),
                                "kind": kind,
                                "resource_ids": [resource_id],
                                "field_refs": [f"{entity_type}.{field}"],
                                "description": f"真实记录中的 {entity_type}.{field} 提供 {kind} 变化。",
                            }
                        )
                        used_dimension_refs.add(ref)
                        found = True
                        break
                if found:
                    break
            if len(dimensions) >= int(policy.get("min_dimension_kinds", 3)):
                break

    status = "ready" if not unavailable_requirements else "insufficient_public_data"
    report = {
        "schema_version": "1.0",
        "request_sha256": research_request.get("request_sha256", ""),
        "status": status,
        "representation_mode": "structured_records" if entity_resource_ids else "file_native",
        "summary": "基于已落盘公开数据生成兼容分析元数据。" if status == "ready" else "真实数据无法覆盖全部核心种子要求。",
        "coverage": coverage,
        "extensions": extensions,
        "relations": relations,
        "relation_gaps": relation_gaps,
        "dimensions": dimensions,
        "gaps": [
            {
                "requirement_id": requirement.get("requirement_id"),
                "category": "not_public",
                "reason": "现有文件中没有足以证明该要求的实体或字段。",
                "attempted_urls": source_urls,
                "impact": "不能安全生成依赖该要求的工具或任务。",
            }
            for requirement in unavailable_requirements
        ],
        "data_policy": {
            "business_records": "real_public_only",
            "synthetic_business_record_count": 0,
            "deterministic_transformations_only": True,
        },
    }
    provenance = {"schema_version": "1.0", "sources": sources}
    return {"environment": environment, "sources": provenance, "research_report": report}
