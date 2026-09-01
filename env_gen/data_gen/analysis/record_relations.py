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

from .record_primitives import *

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

    primary_hint = _choose_primary_id(entity_type, names)
    foreign_keys_without_primary = foreign_keys - {
        "entity_id",
        "relation_id",
        str(primary_hint or ""),
    }
    # 具有自身技术键、至少两个外键、至多一个关系属性的记录仍是桥表。
    # 例如 authorship(paper_id, author_id, position) 不应因为 position
    # 是数值就被当作需要四个业务维度的事实实体。
    if len(foreign_keys_without_primary) >= 2 and len(business_fields) <= 1:
        return True

    # 明确的实体自身主键 + 至少两个业务字段通常表示普通实体，而非
    # 关系行。实体名可能是复数或包含限定词，不能只检查字面上的
    # ``{entity_type}_id``（例如 awards 的自身键是 award_id）。
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


_IDENTIFIER_SEMANTIC_TOKENS = {
    "id",
    "identifier",
    "code",
    "key",
    "iso",
    "iso2",
    "iso3",
    "alpha",
    "alpha2",
    "alpha3",
    "numeric",
}

# 这些词在业务系统中表示 user 承担的角色，而不是不同的身份命名空间。
# 仅在关系字段概念比较时归一化，原始字段名和实体 Schema 保持不变。
_IDENTITY_ROLE_ALIASES = {
    "assignee": "user",
    "author": "user",
    "creator": "user",
    "owner": "user",
}


def _relation_field_identity_tokens(field: str) -> set[str]:
    """Return the business concept named by an identifier field.

    Identifier namespaces are deliberately removed here.  For example,
    ``income_level_id`` and ``income_level_iso2_code`` both describe an income
    level, while ``country_id`` describes a country.  This lets relation
    inference compare the concept itself without treating the broad entity name
    ``country_income_levels`` as proof that every contained identifier is
    interchangeable.
    """

    return {
        _IDENTITY_ROLE_ALIASES.get(token, token)
        for token in _canonical_tokens(field)
        if token not in _IDENTIFIER_SEMANTIC_TOKENS
    }


def _identifier_namespace(field: str) -> str:
    """Return the identifier namespace implied by a field name."""

    lowered = str(field).lower()
    if lowered == "id" or lowered == "entity_id" or lowered.endswith("_id"):
        return "id"
    if lowered == "code" or lowered.endswith("_code") or lowered.endswith("code"):
        return "code"
    if lowered.endswith("_key"):
        return "key"
    return "other"


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
    source_tokens = _relation_field_identity_tokens(source_base)
    target_tokens = _relation_field_identity_tokens(target_base)
    entity_tokens = _canonical_tokens(target_type)
    if not source_tokens:
        return False

    # When both fields name a business concept, the concepts must agree.  A
    # shared token somewhere in a compound entity name is not enough: it made
    # country.country_id look related to
    # country_income_levels.income_level_iso2_code when five codes happened to
    # collide.
    if target_tokens:
        return source_tokens == target_tokens

    # A generic standard code such as ``iso3_code`` can still be the unique key
    # of a plainly named entity.  The source field must name that whole entity,
    # not merely one component of a compound type.
    entity_business_tokens = {
        _IDENTITY_ROLE_ALIASES.get(token, token)
        for token in entity_tokens
        if token not in _IDENTIFIER_SEMANTIC_TOKENS
    }
    return source_tokens == entity_business_tokens


def _relation_gap_field_compatible(
    source_field: str,
    target_type: str,
    target_field: str,
) -> bool:
    """Return whether partial value overlap is strong evidence of a broken FK.

    A completely closed value set can prove that two different identifier
    namespaces are equivalent in the collected data.  Partial overlap cannot:
    ``*_id`` and ``*_code`` often collide by chance.  Gap detection therefore
    additionally requires the same namespace, while closed relation inference
    continues to use :func:`_relation_field_compatible`.
    """

    return (
        _relation_field_compatible(source_field, target_type, target_field)
        and _identifier_namespace(source_field) == _identifier_namespace(target_field)
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
                        if not _relation_gap_field_compatible(
                            source_field,
                            target_type,
                            target_field,
                        ):
                            continue
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


__all__ = [name for name in globals() if not name.startswith("__")]
