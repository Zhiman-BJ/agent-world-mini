"""从已落盘文件提取记录、字段和关系候选。

这里的结果用于数据画像和声明校验，不是最终环境语义。环境资源的业务含义由
环境描述 Agent 声明，Validator 再用本模块提取的事实进行核对。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .record_primitives import *
from .structured_io import StructuredDataError, read_entity_groups

def _read_entity_groups(path: Path) -> dict[str, list[dict[str, Any]]]:
    return read_entity_groups(path)


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
        try:
            return read_entity_groups(path)
        except StructuredDataError:
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


__all__ = [name for name in globals() if not name.startswith("__")]
