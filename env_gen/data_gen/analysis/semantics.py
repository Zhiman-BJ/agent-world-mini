"""Seed 工具、实体画像和环境校验共用的轻量语义规则。

这里仅处理操作族、实体目标词和名称规范化，不包含预算或请求编译逻辑。
这些规则同时被研究请求编译器、实体画像和环境校验复用。
"""

from __future__ import annotations

import re
from typing import Any


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

