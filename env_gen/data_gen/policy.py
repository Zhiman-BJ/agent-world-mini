from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse


_OPERATION_FAMILIES = {
    "search": "search",
    "find": "search",
    "query": "search",
    "inspect": "inspect",
    "get": "inspect",
    "show": "inspect",
    "view": "inspect",
    "list": "list",
    "rank": "rank",
    "sort": "rank",
    "compare": "compare",
    "count": "aggregate",
    "aggregate": "aggregate",
    "filter": "filter",
    "traverse": "traverse",
    "audit": "audit",
    "export": "export",
    "update": "mutate",
    "create": "mutate",
    "delete": "mutate",
    "history": "timeline",
    "trend": "timeline",
    # 常见中文操作动词；目标实体仍由后续 token/种子实体对齐决定。
    "搜索": "search",
    "检索": "search",
    "查询": "search",
    "查看": "inspect",
    "检查": "inspect",
    "获取": "inspect",
    "列出": "list",
    "列表": "list",
    "排序": "rank",
    "排名": "rank",
    "比较": "compare",
    "对比": "compare",
    "统计": "aggregate",
    "聚合": "aggregate",
    "筛选": "filter",
    "过滤": "filter",
    "遍历": "traverse",
    "审计": "audit",
    "导出": "export",
    "创建": "mutate",
    "新增": "mutate",
    "更新": "mutate",
    "删除": "mutate",
}

_OPERATION_PREFIXES = tuple(
    sorted(
        (key for key in _OPERATION_FAMILIES if any("\u4e00" <= char <= "\u9fff" for char in key)),
        key=len,
        reverse=True,
    )
)

# 操作名称中这些词通常开始字段/条件说明，而不是目标实体名称。保留
# ``search issues by state`` 的目标为 ``issue``，避免把 ``state`` 当成
# 第二个实体候选；该规则只影响边界对齐，不改动原始工具名称。
_TARGET_QUALIFIER_WORDS = {
    "by",
    "with",
    "where",
    "for",
    "from",
    "using",
    "via",
    "on",
    "in",
    "of",
    "按",
    "根据",
    "通过",
    "使用",
}


def normalize_semantic_token(value: Any) -> str:
    """规范化用于实体/操作对齐的单个词。

    这里只处理常见英文复数，不做通用词干化。简单地删除所有末尾
    ``s`` 会把 ``status``、``analysis`` 和 ``class`` 错误改成不存在的
    ``statu``、``analysi`` 和 ``clas``，从而让合法的种子目标无法匹配。
    """

    token = str(value or "").lower()
    irregular = {
        "analyses": "analysis",
        "diagnoses": "diagnosis",
        "hypotheses": "hypothesis",
        "statuses": "status",
        "theses": "thesis",
    }
    if token in irregular:
        return irregular[token]
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith(("sses", "shes", "ches", "xes", "zes")) and len(token) > 4:
        return token[:-2]
    # 这些词以 s 结尾但通常不是复数；保守保留，避免把业务实体名改坏。
    if token.endswith(("ss", "us", "is")):
        return token
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


@dataclass(frozen=True)
class ResearchPolicy:
    """DataGen 的通用调研边界和最低数据充分性要求。

    数量阈值只用于防止空壳环境；真正的覆盖要求由种子中的实体、操作和
    业务范围逐项编译得到。阈值不随具体领域写特例。
    """

    max_total_seconds: int = 1800
    max_sources: int = 50
    max_raw_files: int = 200
    max_download_bytes: int = 512 * 1024 * 1024
    max_derived_bytes: int = 64 * 1024 * 1024
    max_workspace_bytes: int = 768 * 1024 * 1024
    data_phase_seconds: int = 900
    metadata_phase_seconds: int = 300
    repair_phase_seconds: int = 180
    min_total_entity_records: int = 50
    # 全局记录数不能替代每个核心实体的样本深度：否则可以用大量无关
    # 扩展记录掩盖某个核心实体只有一条记录的问题。
    min_core_entity_records: int = 3
    # 需要搜索、列出、排序、比较或聚合的核心目标，至少要有这个数量的
    # 真实记录；inspect 仍使用 min_core_entity_records。
    min_operation_records: int = 3
    # 能支撑排序/比较/聚合/搜索的字段，至少要有这么多个不同真实值，
    # 防止常量字段或只有两个样本的窄快照被误报为丰富数据。
    min_operation_distinct_values: int = 3
    min_extension_entity_types: int = 2
    # 扩展不是“凑数”字段：默认要求至少 6 个有证据的能力，并尽量覆盖
    # 4 个不同操作族。compile_research_request 会按种子规模提高目标，
    # 给后续工具生成留下明显余量。
    min_extension_capabilities: int = 6
    min_extension_operation_families: int = 4
    min_relations: int = 1
    min_dimension_kinds: int = 3
    min_dimension_distinct_values: int = 2
    min_operation_evidence_fields: int = 1

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"ResearchPolicy.{name} 必须是非负整数")
        if self.max_total_seconds == 0:
            raise ValueError("max_total_seconds 必须大于 0")


def _unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, (list, tuple)):
        return result
    for value in values:
        text = str(value).strip()
        key = " ".join(text.lower().split())
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def infer_operation_family(name: str) -> str | None:
    """从操作名的第一个词得到稳定的通用操作族。"""

    text = str(name).strip().lower().replace("-", " ")
    for prefix in _OPERATION_PREFIXES:
        if text.startswith(prefix):
            return _OPERATION_FAMILIES[prefix]
    first = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    first = first[0] if first else ""
    return _OPERATION_FAMILIES.get(first)


def operation_target_tokens(name: str) -> list[str]:
    """提取操作动词之后的目标词，供实体选择和覆盖校验共用。"""

    text = str(name).strip().lower().replace("-", " ")
    for prefix in _OPERATION_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip(" _:")
            break
    words = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    if words and words[0] in _OPERATION_FAMILIES:
        words = words[1:]
    normalized: list[str] = []
    for word in words:
        word = normalize_semantic_token(word)
        if word in _TARGET_QUALIFIER_WORDS:
            break
        if word and word not in normalized:
            normalized.append(word)
    return normalized


def semantic_tokens(value: Any) -> set[str]:
    """把实体或操作目标名称拆成可比较的规范词。

    种子中的 ``research author``、规范化后的 ``research_author`` 和
    API 常见的复数形式应当被视为同一语义；这个函数只用于证据对齐，
    不参与业务数据改名。
    """

    result: set[str] = set()
    for token in re.findall(r"[^\W_]+", str(value or "").lower(), flags=re.UNICODE):
        token = normalize_semantic_token(token)
        if token:
            result.add(token)
    return result


def semantic_match_score(requirement_name: str, candidate_name: str) -> int:
    """返回候选实体对种子名称的匹配强度，0 表示语义不相关。

    完全相同得 3 分，种子词全部包含在候选名称中得 2 分，单词种子
    与复合实体有交集得 1 分。调用方可以用最高分约束模型不能引用
    一个更弱、仅共享通用词的实体。
    """

    required = semantic_tokens(requirement_name)
    candidate = semantic_tokens(candidate_name)
    if not required or not candidate:
        return 0
    if required == candidate:
        return 3
    if required.issubset(candidate):
        return 2
    if len(required) == 1 and required.intersection(candidate):
        return 1
    return 0


def _operation_field_role(operation_family: str | None) -> str:
    """把操作族映射成数据必须提供的字段角色。"""

    if operation_family in {"rank", "compare", "aggregate"}:
        return "business_numeric"
    if operation_family in {"search", "filter"}:
        return "business_text"
    if operation_family == "list":
        return "varied_field"
    if operation_family == "timeline":
        return "temporal"
    if operation_family == "inspect":
        return "record_fields"
    if operation_family == "traverse":
        return "foreign_key"
    if operation_family == "mutate":
        return "writable_target"
    if operation_family == "export":
        return "writable_output"
    if operation_family == "audit":
        return "integrity_evidence"
    return "none"


def _source_hosts(seed: dict[str, Any]) -> list[str]:
    """提取种子锚点的注册域，用于限制调研不得漂移到无关网站。"""

    urls: list[str] = []
    for key in ("source_url", "source_urls"):
        value = seed.get(key)
        if isinstance(value, str):
            urls.append(value)
        elif isinstance(value, (list, tuple)):
            urls.extend(str(item) for item in value)
    for key in ("anchors", "tool_clues", "documented_tools", "candidate_tools"):
        values = seed.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            for url_key in ("uri", "url", "source_url", "documentation_url"):
                if isinstance(item.get(url_key), str):
                    urls.append(item[url_key])
    hosts: set[str] = set()
    for value in urls:
        try:
            parsed = urlparse(value)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower().strip(".")
        if not host:
            continue
        # 使用注册域而不是单一子域，允许 docs.example.org 与
        # api.example.org 共同服务同一个种子，同时避免 evil-example.org。
        labels = host.split(".")
        hosts.add(".".join(labels[-2:]) if len(labels) >= 2 else host)
    return sorted(hosts)


def compile_research_request(seed: dict[str, Any], policy: ResearchPolicy) -> dict[str, Any]:
    """把开放式种子确定性编译成 Agent 和 Validator 共用的调研请求。"""

    entities = _unique_strings(seed.get("candidate_entities", []))
    operations = _unique_strings(seed.get("candidate_operations", []))
    documented_tools = seed.get("documented_tools", seed.get("candidate_tools", []))
    if isinstance(documented_tools, (list, tuple)):
        operations.extend(
            name
            for name in _unique_strings(
                [
                    item.get("name", item.get("operation", ""))
                    for item in documented_tools
                    if isinstance(item, dict)
                ]
            )
            if " ".join(name.lower().split())
            not in {" ".join(value.lower().split()) for value in operations}
        )

    requirements: list[dict[str, Any]] = []
    for index, name in enumerate(entities, start=1):
        requirements.append(
            {
                "requirement_id": f"seed_entity_{index:02d}",
                "kind": "seed_entity",
                "name": name,
                "description": f"数据必须直接表示或可无损解析出种子实体：{name}",
                "priority": "core",
                "minimum_records": policy.min_core_entity_records,
            }
        )
    for index, name in enumerate(operations, start=1):
        requirement = {
            "requirement_id": f"seed_operation_{index:02d}",
            "kind": "seed_operation",
            "name": name,
            "description": f"现有数据和字段必须足以实现种子操作：{name}",
        }
        operation_family = infer_operation_family(name)
        if operation_family:
            requirement["operation_family"] = operation_family
        target_tokens = operation_target_tokens(name)
        if target_tokens:
            requirement["target_tokens"] = target_tokens
            # 操作名中的目标词只用于定位 seed 声明的业务实体。将最强的
            # 语义匹配写入请求，让 Agent 明确知道 ``rank indicators``
            # 必须作用于 indicator，而不是为了寻找数值列改用
            # indicator_observation 或其它关系/事实表。
            target_name = " ".join(target_tokens)
            # 目标词本身必须保留为第一候选，即使种子只声明了一个更宽的
            # 复合实体（例如 indicator observation）。否则后续从真实数据
            # 中发现的 standalone indicator 会被请求阶段提前排除，导致
            # rank indicators 被错误绑定到观测事实表。
            target_candidates = [target_name]
            target_candidates.extend(
                entity
                for entity in entities
                if semantic_match_score(target_name, entity) > 0
            )
            requirement["target_entity_candidates"] = list(dict.fromkeys(target_candidates))
        if operation_family == "traverse":
            requirement["target_resolution"] = "relation"
        elif operation_family in {"rank", "compare", "aggregate"}:
            # 数值字段在真实业务中经常位于 observation/measurement/event
            # 事实表，而不是定义实体本身（例如 indicator 的年度值）。
            # 允许“目标实体 + 闭合事实关系”这一明确模式，但没有直接
            # 数值字段且找不到可解释事实关系时仍必须停止，不能拿无关
            # 子表或模型记忆冒充覆盖。
            requirement["target_resolution"] = "direct_or_related_fact"
        else:
            requirement["target_resolution"] = "direct_entity"
        requirement["required_field_role"] = _operation_field_role(operation_family)
        requirement["minimum_records"] = (
            policy.min_operation_records
            if operation_family in {
                "search",
                "filter",
                "list",
                "rank",
                "compare",
                "aggregate",
                "timeline",
            }
            else policy.min_core_entity_records
        )
        requirement["priority"] = "core"
        requirements.append(requirement)

    scope_parts = _unique_strings(
        [
            seed.get("seed_label", ""),
            seed.get("source_description", ""),
            *_unique_strings(seed.get("data_directions", [])),
        ]
    )
    if scope_parts:
        requirements.append(
            {
                "requirement_id": "seed_scope_01",
                "kind": "seed_scope",
                "name": str(seed.get("seed_label") or seed.get("theme_id") or "environment scope"),
                "description": "；".join(scope_parts),
                "priority": "core",
            }
        )

    seed_operation_families = {
        str(item["operation_family"])
        for item in requirements
        if item.get("kind") == "seed_operation" and item.get("operation_family")
    }
    # 零阈值是测试/调试显式选择；正常策略按种子复杂度提升扩展目标，避免
    # 大主题只生成恰好覆盖核心操作的“薄环境”。
    extension_capability_target = (
        max(policy.min_extension_capabilities, 2 * len(entities) + len(operations))
        if policy.min_extension_capabilities > 0
        else 0
    )
    extension_family_target = (
        max(
            policy.min_extension_operation_families,
            min(5, len(seed_operation_families) + 1),
        )
        if policy.min_extension_operation_families > 0
        else 0
    )

    request: dict[str, Any] = {
        "schema_version": "1.0",
        "seed": {
            key: seed.get(key)
            for key in (
                "theme_id",
                "seed_label",
                "source_type",
                "source_url",
                "license_or_access_note",
                "coarse_route_label",
                "adapter",
            )
            if seed.get(key) not in (None, "")
        },
        "requirements": requirements,
        "quality_policy": {
            **asdict(policy),
            # 实体类型门槛必须由本次种子和策略共同决定，不能把“至少 4 类”
            # 写死；文件原生或小型业务环境也应能在明确声明后合法发布。
            "min_entity_types": max(1, len(entities) + policy.min_extension_entity_types),
            # 这两个 target 是硬质量门，不是让 Agent 恰好凑数的建议值。
            # 保留 min_* 基线字段，便于审计者知道策略的通用最低门槛。
            "extension_capability_target": extension_capability_target,
            "extension_operation_family_target": extension_family_target,
            # 三个以上核心实体至少应有两条闭合关系；单实体主题不强行
            # 制造自引用，两个实体主题仍要求至少一条真实关系。
            "min_relations": (
                max(policy.min_relations, min(3, max(0, len(entities) - 1)))
                if policy.min_relations > 0
                else 0
            ),
            "primary_sources_required": True,
            "synthetic_business_records": "prohibited",
            "core_missing_data_action": "stop_with_insufficient_public_data",
            "optional_missing_data_action": "record_gap_and_continue_only_if_all_gates_pass",
            "relation_policy": "closed_relations_only; partial_candidates_must_be_reported_in_relation_gaps",
            "source_scope": {
                "allowed_registered_domains": _source_hosts(seed),
                "allow_subdomains": True,
                "action_outside_scope": "reject_source",
            },
        },
    }
    canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request["request_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return request


def request_sha256(request: dict[str, Any]) -> str:
    payload = {key: value for key, value in request.items() if key != "request_sha256"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
