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
from .record_sources import *
from .record_relations import *
from .structured_io import StructuredDataError

def deterministic_entity_groups(
    package_root: Path,
    *,
    entity_hints: list[str] | None,
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
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                csv.Error,
                sqlite3.Error,
                StructuredDataError,
            ):
                continue
            for entity_type, records in groups.items():
                entity_candidates.setdefault(entity_type, []).extend(records)

    seed_entity_names = list(dict.fromkeys(
        str(value).strip()
        for value in (entity_hints or [])
        if str(value).strip()
    ))
    semantic_entity_names = list(seed_entity_names)

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
            # 结构匹配。60 分只表示普通业务字段结构相似，不能据此合并
            # 明确命名的不同实体。例如 OpenAlex 的 author 和 institution
            # 都有 name/works_count/h_index，但它们不是同一实体的分页。
            if best_score >= 80:
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

    # Agent 已经提交规范化实体文件时，它们就是准备交给 ToolGen/TaskGen 的
    # 显式业务投影。raw 继续作为真实性和来源证据，但不能把响应里的每个
    # incidental 嵌套对象自动扩成新的最终实体或关系缺口。需要完全从 raw
    # 重建事实库存的校验/兜底调用仍显式传 authoritative_raw=True。
    if entity_groups and not authoritative_raw:
        entity_groups = _coalesce_same_named_entities(
            entity_groups,
            seed_slugs=seed_slugs,
        )
        return _coalesce_relation_groups(entity_groups)

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

