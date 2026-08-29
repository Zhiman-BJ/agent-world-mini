"""Step 2：采样、审查并筛选候选工具链。

本阶段只提出任务候选，不执行工具、不实例化参数、不读写 workspace。流程固定为：

``采样原始链 → 质量/多样性筛选 20 条 → LLM review → LLM 逻辑性评分 → 选出候选``。

输入
====

``config``
    使用 ``config.planning`` 的采样与筛选参数，以及 ``config.llm`` 的 LLM 参数；
    不自行读取其他配置文件。

``environment``
    Step 0 返回的完整环境。随机游走使用工具名；review 和逻辑性评分使用环境名称、
    描述、``resources``、``rules``，以及工具的公开 ``name``、``description``、
    ``inputSchema`` 和 ``outputSchema``。发送给 LLM 前必须移除 ``tools[].internal``。

``tool_graph``
    Step 1 返回的有向带权边。边权只能是 1、2、3；Step 2 暂不读取
    ``prerequisites``。``weight=3`` 的入边只用于确定起点，采样概率由配置单独提供。

配置
====

``planning`` 使用以下默认值：

.. code-block:: yaml

    sample_count: 10000
    review_count: 20
    keep_top_count: 10
    min_chain_length: 8
    max_chain_length: 15
    max_tool_visits: 2
    random_seed: 42
    edge_sampling_probabilities:
      "1": 0.2
      "2": 0.3
      "3": 0.5
    diversity_lambda: 0.3

``sample_count`` 是最多的随机游走尝试次数；``review_count`` 是进入两轮 LLM 处理的
原始链数量；``keep_top_count`` 是最终交给 Step 3 的数量。所有整数必须为正，且最小
链长不得大于最大链长。``edge_sampling_probabilities`` 的键是字符串形式的边权，值为
正数；每个节点只在当前可用出边中按这些值重新归一化。``diversity_lambda`` 控制质量
与链相似度的取舍。

原始链采样
==========

1. 本地校验工具名、边权、重复边和自环，并建立出边索引。
2. 起点是没有任何 ``weight=3`` 入边的全部工具；Level 1/2 入边不影响起点资格。
   起点集合为空时抛出 ``ValueError``，不退回任意工具。没有出边的合格起点可以产生
   单工具链，但会按链长规则参与后续筛选。
3. 每次从合格起点均匀选择一个工具，沿当前节点的合法出边继续游走。出边选择只使用
   ``edge_sampling_probabilities``，不直接把 ``weight`` 当作概率。
4. 单条链中工具访问次数不得超过 ``max_tool_visits``；达到最大长度或没有合法后继时
   自然结束，不为满足最小长度拼接不存在的边。
5. 按完整有序序列 ``tuple(chain)`` 去重；重复链仍计入尝试次数。记录实际观察到的最长
   链；若没有链满足最小长度，则使用最长观测链作为回退候选。

质量与多样性筛选
================

原始链的 ``score`` 是链上边权之和，仅表示采样质量，不表示采样概率。先按
``score``、链长和工具序列做稳定排序，再使用相似度惩罚选择最多 ``review_count`` 条。

两条链的相似度使用共享有向边比例：

.. code-block:: text

    similarity(A, B) = |edges(A) ∩ edges(B)| / min(|edges(A)|, |edges(B)|)

其中 ``edges(chain)`` 是相邻工具组成的有向边集合，单工具链的相似度为 0。第一条
选择质量最高的链；之后每条未选链的选择分数为：

.. code-block:: text

    normalized_score - diversity_lambda * max(similarity(候选, 已选链))

直到选满 ``review_count`` 条或没有候选。选择时尽量覆盖不同起点；起点数量超过名额时，
保留各起点质量最高的代表链。

LLM review
==========

对筛选出的最多 20 条原始链逐条独立调用 LLM。输入包括原始链、环境公开信息、全部
工具公开定义和完整 ``tool_graph``。review 的规则是：

* 默认保留原链，不主动修改工具、顺序或长度；
* 只有发现明确逻辑问题时才删除、插入或调整；
* 优先使用 graph 中已有的边；
* 允许加入 graph 中没有的边，但必须有工具公开描述、输入输出 Schema 或环境规则的
  充分具体依据；不能只凭字段同名、类型相容、主题相似或理论可能性；不确定时保持原链；
* 返回完整链，不返回增删补丁，也不生成 task_text。

返回格式：

.. code-block:: json

    {"chain": ["完整工具链"], "reason": "非空的保留或修改说明"}

解析失败、工具名非法、链为空或 ``reason`` 无效时，保留原始链并记录
``llm_review.error``；单条失败不终止整个 Step。这里不因为相邻边不在 graph 中而拒绝
review 结果。

逻辑性评分
==========

对 review 后的每条链再进行一轮独立 LLM 评分。评分只判断链是否适合转写成任务，不判断
工具是否已经真实执行成功。评分参考 Step 4 的任务原则：是否有自然且明确的业务目标、
整条链是否共同服务该目标、顺序是否连贯、公开契约是否支持该流程、最终结果是否足以
形成可验证任务，以及是否只是机械拼接无关工具。

评分返回：

.. code-block:: json

    {"score": 0, "reason": "说明适合或不适合转写为任务"}

``score`` 为 0–5 的整数：5 表示非常适合，4 表示较好，3 表示勉强可用，2 表示逻辑较弱，
1 表示基本不可用，0 表示明显无关或无法解释。评分输入仍只使用公开环境、工具定义、
graph、review 后的完整 chain 和 review 说明，不使用 workspace、执行结果或其他候选链。
按逻辑性评分降序、原始 ``score`` 降序、链长降序和工具序列字典序，选出最多
``keep_top_count`` 条进入 Step 3；不额外设置最低分阈值。

输出与失败行为
==============

每条最终候选保留：

.. code-block:: python

    {
        "task_id": "task1",
        "chain": [str, ...],
        "score": int,
        "llm_review": {
            "original_chain": [str, ...],
            "reason": str,
            "error": str | None,
        },
        "logic_score": int,
        "logic_reason": str,
    }

``chain`` 是 review 后的完整链；``score`` 是 review 前原始链的边权总和，不因图外边
而重算。逻辑评分失败时保留该链并记录错误，具体错误字段由实现与现有候选结构统一。
最终链按完整有序序列去重后编号为 ``task1``、``task2``……。

``sampling_report`` 至少记录尝试次数、唯一原始链数、最长观测链、短链回退、review 数量、
review 修改/失败数量、逻辑评分分布和最终数量，用于评估采样参数是否合理。

本阶段不调用工具、不读写 workspace、不生成正式 task；Step 3 负责真实可执行性，
Step 4 负责生成 ``task_text``。
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

from .contracts import SampleChainsInput, SampleChainsOutput
from .llm import BatchInferenceError, infer, parse_json_object


DEFAULT_EDGE_SAMPLING_PROBABILITIES = {1: 0.2, 2: 0.3, 3: 0.5}
SCORE_RANGE = range(6)


def sample_chains(stage_input: SampleChainsInput) -> SampleChainsOutput:
    """按文件顶部规格完成采样、review、逻辑评分、去重和任务编号。"""
    config = stage_input["config"]
    planning = config.planning
    names, public_tools = _tools(stage_input["environment"])
    sample_count = _positive(planning, "sample_count", 10000)
    review_count = _positive(planning, "review_count", 20)
    keep_count = _positive(planning, "keep_top_count", 10)
    minimum = _positive(planning, "min_chain_length", 8)
    maximum = _positive(planning, "max_chain_length", 15)
    max_visits = _positive(planning, "max_tool_visits", 2)
    if minimum > maximum:
        raise ValueError("planning.min_chain_length 不能大于 max_chain_length")
    seed = planning.get("random_seed", 42)
    if type(seed) is not int:
        raise ValueError("planning.random_seed 必须是整数")
    probabilities = _sampling_probabilities(planning.get("edge_sampling_probabilities"))
    diversity_lambda = _nonnegative_float(planning, "diversity_lambda", 0.3)

    adjacency, incoming_level3 = _graph(stage_input["tool_graph"], names)
    roots = sorted(name for name in names if not incoming_level3[name])
    if not roots:
        raise ValueError("tool_graph 中不存在没有 weight=3 入边的合法起点")

    rng = random.Random(seed)
    unique: dict[tuple[str, ...], int] = {}
    longest = 0
    for _ in range(sample_count):
        chain = _sample_one_chain(
            rng,
            roots,
            adjacency,
            probabilities,
            maximum,
            max_visits,
        )
        key = tuple(chain)
        longest = max(longest, len(chain))
        unique.setdefault(key, _chain_score(chain, adjacency))

    if not unique:
        raise ValueError("采样没有产生任何非空链")
    eligible = [
        (chain, score)
        for chain, score in unique.items()
        if minimum <= len(chain) <= maximum
    ]
    fallback = not eligible
    if fallback:
        eligible = [(chain, score) for chain, score in unique.items() if len(chain) == longest]
    selected_for_review = _select_diverse_chains(
        eligible,
        review_count,
        diversity_lambda,
    )

    reviewed, review_errors, review_changed = _review_chains(
        selected_for_review,
        stage_input["environment"],
        public_tools,
        stage_input["tool_graph"],
        names,
        config.llm,
        minimum,
        maximum,
    )
    reviewed = _deduplicate_reviewed_chains(reviewed)
    scored, logic_errors = _score_chains(
        reviewed,
        stage_input["environment"],
        public_tools,
        stage_input["tool_graph"],
        config.llm,
    )
    selected = sorted(
        scored,
        key=lambda item: (
            -item["logic_score"],
            -item["score"],
            -len(item["chain"]),
            tuple(item["chain"]),
        ),
    )[:keep_count]

    tasks = []
    for index, item in enumerate(selected, start=1):
        tasks.append({
            "task_id": f"task{index}",
            "chain": item["chain"],
            "score": item["score"],
            "llm_review": item["llm_review"],
            "logic_score": item["logic_score"],
            "logic_reason": item["logic_reason"],
        })
    distribution: dict[str, int] = {}
    for item in scored:
        key = str(item["logic_score"])
        distribution[key] = distribution.get(key, 0) + 1
    return {
        "tasks": tasks,
        "sampling_report": {
            "attempt_count": sample_count,
            "unique_chain_count": len(unique),
            "eligible_chain_count": len(eligible),
            "longest_observed_length": longest,
            "short_chain_fallback": fallback,
            "review_candidate_count": len(selected_for_review),
            "review_changed_count": review_changed,
            "review_error_count": review_errors,
            "logic_score_distribution": distribution,
            "logic_score_error_count": logic_errors,
            "selected_count": len(selected),
            "final_task_count": len(tasks),
        },
    }


def _positive(config: dict[str, Any], name: str, default: int) -> int:
    value = config.get(name, default)
    if type(value) is not int or value <= 0:
        raise ValueError(f"planning.{name} 必须是正整数")
    return value


def _nonnegative_float(config: dict[str, Any], name: str, default: float) -> float:
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"planning.{name} 必须是非负数")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"planning.{name} 必须是非负数")
    return value


def _sampling_probabilities(value: Any) -> dict[int, float]:
    probabilities = dict(DEFAULT_EDGE_SAMPLING_PROBABILITIES)
    if value is None:
        return probabilities
    if not isinstance(value, dict):
        raise ValueError("planning.edge_sampling_probabilities 必须是 object")
    for weight in (1, 2, 3):
        raw = value.get(str(weight), value.get(weight, probabilities[weight]))
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"edge_sampling_probabilities[{weight}] 必须是正数")
        number = float(raw)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"edge_sampling_probabilities[{weight}] 必须是正数")
        probabilities[weight] = number
    return probabilities


def _tools(environment: dict[str, Any]) -> tuple[set[str], list[dict[str, Any]]]:
    tools = environment.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("environment.tools 必须是非空数组")
    public = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError("工具缺少 name")
        required = ("name", "description", "inputSchema", "outputSchema")
        if any(key not in tool for key in required):
            raise ValueError(f"工具 {tool.get('name')} 缺少公开定义")
        public.append({key: tool[key] for key in required})
    names = {tool["name"] for tool in public}
    if len(names) != len(public):
        raise ValueError("environment.tools 工具名重复")
    return names, public


def _graph(
    edges: list[dict[str, Any]],
    names: set[str],
) -> tuple[dict[str, list[tuple[str, int]]], dict[str, set[str]]]:
    if not isinstance(edges, list):
        raise ValueError("tool_graph 必须是 array")
    adjacency = {name: [] for name in names}
    incoming_level3 = {name: set() for name in names}
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("tool_graph 边必须是 object")
        source, target, weight = edge.get("from_tool"), edge.get("to_tool"), edge.get("weight")
        if source not in names or target not in names:
            raise ValueError(f"tool_graph 工具名越界：{source!r} -> {target!r}")
        if source == target:
            raise ValueError(f"tool_graph 不允许自环：{source}")
        if type(weight) is not int or weight not in {1, 2, 3}:
            raise ValueError(f"tool_graph weight 非法：{weight!r}")
        if (source, target) in seen:
            raise ValueError(f"tool_graph 重复边：{source} -> {target}")
        seen.add((source, target))
        adjacency[source].append((target, weight))
        if weight == 3:
            incoming_level3[target].add(source)
    for options in adjacency.values():
        options.sort()
    return adjacency, incoming_level3


def _sample_one_chain(
    rng: random.Random,
    roots: list[str],
    adjacency: dict[str, list[tuple[str, int]]],
    probabilities: dict[int, float],
    maximum: int,
    max_visits: int,
) -> list[str]:
    chain = [rng.choice(roots)]
    visits = {chain[0]: 1}
    while len(chain) < maximum:
        options = [
            edge
            for edge in adjacency[chain[-1]]
            if visits.get(edge[0], 0) < max_visits
        ]
        if not options:
            break
        target, _weight = rng.choices(
            options,
            weights=[probabilities[weight] for _target, weight in options],
            k=1,
        )[0]
        chain.append(target)
        visits[target] = visits.get(target, 0) + 1
    return chain


def _chain_score(chain: list[str], adjacency: dict[str, list[tuple[str, int]]]) -> int:
    return sum(dict(adjacency[source])[target] for source, target in zip(chain, chain[1:]))


def _chain_edges(chain: tuple[str, ...] | list[str]) -> set[tuple[str, str]]:
    return set(zip(chain, chain[1:]))


def _chain_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    left_edges = _chain_edges(left)
    right_edges = _chain_edges(right)
    denominator = min(len(left_edges), len(right_edges))
    if denominator == 0:
        return 0.0
    return len(left_edges & right_edges) / denominator


def _select_diverse_chains(
    candidates: list[tuple[tuple[str, ...], int]],
    count: int,
    diversity_lambda: float,
) -> list[tuple[tuple[str, ...], int]]:
    if not candidates or count <= 0:
        return []
    ranked = sorted(candidates, key=lambda item: (-item[1], -len(item[0]), item[0]))
    by_start: dict[str, list[tuple[tuple[str, ...], int]]] = {}
    for candidate in ranked:
        by_start.setdefault(candidate[0][0], []).append(candidate)
    representatives = [items[0] for items in by_start.values()]
    if len(representatives) > count:
        return sorted(representatives, key=lambda item: (-item[1], -len(item[0]), item[0]))[:count]

    selected = list(representatives)
    selected_keys = {tuple(item[0]) for item in selected}
    remaining = [item for item in ranked if tuple(item[0]) not in selected_keys]
    maximum_score = max(item[1] for item in ranked) or 1
    while remaining and len(selected) < count:
        def key(item: tuple[tuple[str, ...], int]) -> tuple[float, int, int, tuple[str, ...]]:
            similarity = max((_chain_similarity(item[0], other[0]) for other in selected), default=0.0)
            value = item[1] / maximum_score - diversity_lambda * similarity
            return (value, item[1], len(item[0]), tuple(item[0]))

        best = max(remaining, key=key)
        selected.append(best)
        remaining.remove(best)
    return selected


def _review_chains(
    candidates: list[tuple[tuple[str, ...], int]],
    environment: dict[str, Any],
    public_tools: list[dict[str, Any]],
    tool_graph: list[dict[str, Any]],
    names: set[str],
    llm_config: dict[str, Any],
    minimum_length: int,
    maximum_length: int,
) -> tuple[list[dict[str, Any]], int, int]:
    prompts = [
        _review_prompt(
            environment, public_tools, tool_graph, list(chain),
            minimum_length, maximum_length,
        )
        for chain, _score in candidates
    ]
    if not prompts:
        return [], 0, 0
    try:
        responses = infer(prompts, llm_config=llm_config)
        if len(responses) != len(candidates):
            raise ValueError("LLM review 返回数量不一致")
        outcomes = list(responses)
    except BatchInferenceError as error:
        outcomes = list(error.outcomes)
    except Exception as error:
        outcomes = [error] * len(candidates)

    reviewed: list[dict[str, Any]] = []
    error_count = 0
    changed_count = 0
    for (original, score), outcome in zip(candidates, outcomes):
        chain = list(original)
        reason = ""
        error = str(outcome) if isinstance(outcome, Exception) else None
        if error is None:
            try:
                payload = parse_json_object(outcome.text)
                value = payload.get("chain")
                reason_value = payload.get("reason")
                if (
                    not isinstance(value, list)
                    or not value
                    or any(not isinstance(name, str) or name not in names for name in value)
                    or not isinstance(reason_value, str)
                    or not reason_value.strip()
                ):
                    raise ValueError("chain/reason 结构或工具名非法")
                if not minimum_length <= len(value) <= maximum_length:
                    raise ValueError("review 后链长度超出规划范围")
                chain = value
                reason = reason_value.strip()
            except Exception as review_error:
                error = str(review_error)
        if error is not None:
            error_count += 1
        if tuple(chain) != tuple(original):
            changed_count += 1
        reviewed.append({
            "chain": chain,
            "score": score,
            "llm_review": {
                "original_chain": list(original),
                "reason": reason,
                "error": error,
            },
        })
    return reviewed, error_count, changed_count


def _review_prompt(
    environment: dict[str, Any],
    public_tools: list[dict[str, Any]],
    tool_graph: list[dict[str, Any]],
    chain: list[str],
    minimum_length: int,
    maximum_length: int,
) -> str:
    context = {
        "environment": {
            key: environment.get(key)
            for key in ("environment_id", "name", "description", "resources", "rules")
        },
        "tools": public_tools,
        "tool_graph": tool_graph,
        "chain": chain,
    }
    return (
        "审查下面的工具链是否能形成从头到尾有意义的顺序流程。\n"
        "默认保留原链，不主动修改工具、顺序或长度；只有发现明确逻辑问题时才删除、"
        "插入或调整。逐步检查每个 inputSchema 的必填标识（如 *_id），只能依据前序"
        "工具的公开 outputSchema 或环境规则判断；如缺少标识，应插入能产生该标识的发现工具，"
        "禁止假设或编造 ID。\n"
        f"修改后的 chain 必须包含 {minimum_length} 到 {maximum_length} 个工具；无法在此范围内修正时保持原链。\n"
        "优先使用 graph 中已有的边。允许加入 graph 中没有的边，但必须有公开工具描述、"
        "输入输出 Schema 或环境规则的充分具体依据；不能只凭字段同名、类型相容、主题"
        "相似或理论可能性。不确定时保持原链。\n"
        "只返回 JSON object：{\"chain\":[工具名],\"reason\":\"非空说明\"}。\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )


def _deduplicate_reviewed_chains(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    result = []
    for item in items:
        key = tuple(item["chain"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _score_chains(
    items: list[dict[str, Any]],
    environment: dict[str, Any],
    public_tools: list[dict[str, Any]],
    tool_graph: list[dict[str, Any]],
    llm_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    prompts = [
        _logic_score_prompt(environment, public_tools, tool_graph, item)
        for item in items
    ]
    if not prompts:
        return [], 0
    try:
        responses = infer(prompts, llm_config=llm_config)
        if len(responses) != len(items):
            raise ValueError("LLM 逻辑性评分返回数量不一致")
        outcomes = list(responses)
    except BatchInferenceError as error:
        outcomes = list(error.outcomes)
    except Exception as error:
        outcomes = [error] * len(items)

    errors = 0
    result: list[dict[str, Any]] = []
    for item, outcome in zip(items, outcomes):
        score = 0
        reason = ""
        error = str(outcome) if isinstance(outcome, Exception) else None
        if error is None:
            try:
                payload = parse_json_object(outcome.text)
                value = payload.get("score")
                reason_value = payload.get("reason")
                if type(value) is not int or value not in SCORE_RANGE:
                    raise ValueError("逻辑性评分必须是 0 到 5 的整数")
                if not isinstance(reason_value, str) or not reason_value.strip():
                    raise ValueError("逻辑性评分 reason 必须是非空字符串")
                score = value
                reason = reason_value.strip()
            except Exception as score_error:
                error = str(score_error)
        if error is not None:
            errors += 1
            reason = f"评分失败：{error}"
        result.append({
            **item,
            "logic_score": score,
            "logic_reason": reason,
        })
    return result, errors


def _logic_score_prompt(
    environment: dict[str, Any],
    public_tools: list[dict[str, Any]],
    tool_graph: list[dict[str, Any]],
    item: dict[str, Any],
) -> str:
    context = {
        "environment": {
            key: environment.get(key)
            for key in ("environment_id", "name", "description", "resources", "rules")
        },
        "tools": public_tools,
        "tool_graph": tool_graph,
        "chain": item["chain"],
        "review_reason": item["llm_review"]["reason"],
    }
    return (
        "逻辑性评分：请判断下面这条已经审查过的工具链是否适合转写成一个自然、明确、可验证的任务。\n"
        "评分只判断任务适配性，不判断工具是否已经真实执行成功。请检查：是否有一个自然"
        "的业务目标；整条链是否共同服务该目标；顺序是否连贯；公开工具契约是否支持该"
        "流程；最终结果是否足以形成任务和参考答案；是否只是机械拼接无关工具。\n"
        "返回 JSON object：{\"score\":0,\"reason\":\"非空说明\"}。score 必须是 0 到 5"
        "的整数，5 最适合，0 明显无关或无法解释。不要生成 task_text，不要引用工具实现、"
        "workspace 或执行结果。\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )
