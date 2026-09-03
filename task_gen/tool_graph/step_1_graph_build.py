"""Step 1：从环境的公开工具定义构建有证据的直接前置关系图。

本文件对任意两个不同工具 ``A`` 和 ``B`` 判断 ``B`` 能否作为 ``A`` 的直接下一跳，
并独立判断调用 ``B`` 前必须满足的工具历史。本阶段不生成工具链，也不证明某条链
一定可执行。

输入 Schema（只列本阶段实际读取的字段）
=======================================

方括号是本阶段怎么用该字段。``environment`` 里未列出的字段
（``schema_version``、``environment_id``、``resources[].data_type``、
``format``、``source_resources``、``entity_schema``）一律不读。

.. code-block:: text

    config : {
      llm   : dict                        [传给 tool_graph.llm.infer；
                                           读 max_concurrency 控制并发]
      graph : dict                        [当前为空 {}。本阶段不接受任何建图参数]
    }                                     [其余 config 字段本阶段不读：
                                           environment_dir、schema_dir、
                                           output_root、planning、execution、cost]

    environment : {
      name        : str                   [→ 环境上下文，进 prompt]
      description : str                   [→ 环境上下文，进 prompt]

      resources : [
        { resource_id : str               [随 resources 一起进 prompt 上下文；
                                           本阶段不据此做任何校验]
          name        : str
          description : str
          storage_type / path / writable  [随 resources 一起进上下文；
                                           本阶段不据此做任何判定]
        }, ...
      ]

      rules : [                           [必须进 prompt —— 跨资源的业务规则
                                           常是状态依赖的唯一线索]
        { description : str, resources : [str] }, ...
      ]

      tools : [                           [每个工具轮流做一次 to_tool 目标；
                                           其余全部工具作为该次的候选]
        { name         : str              [图节点标识；候选与目标的匹配依据]
          description  : str              [判定依赖的主要语义来源 ——
                                           schema 占 payload 97%，语义几乎全在
                                           这里。实测仅 18-60 字符]
          inputSchema  : object           [→ 投影出参数名/type/required/enum]
          outputSchema : object           [→ 投影出成功分支的输出字段路径]
          internal                        [**严禁读取**。必须通过"只挑选公开
                                           字段"来排除，而不是拷贝后 del，
                                           更不是在 prompt 里声称不使用]
        }, ...
      ]
    }

本阶段**不接收** ``run_dir``，也不接收 ``initial_state``；不得通过读 workspace
偷渡运行时知识。

输出 Schema
===========

只新增 ``tool_graph`` 一个键。工具节点已存在于 ``environment.tools``，不重复保存。

.. code-block:: text

    {
      tool_graph : {
        edges: [
          {from_tool: str, to_tool: str, weight: 1|2|3, reason: str}, ...
        ],
        prerequisites: [
          {to_tool: str, any_of: [{all_of: [str, ...], reason: str}, ...]}, ...
        ]
      }
    }

``weight`` 只表示直接下一跳关系的等级，不表示 prerequisite、采样概率或置信度。
``prerequisites`` 是独立的目标级历史约束；``any_of`` 中任一方案满足即可，每个方案的
``all_of`` 必须全部已执行。不能从 ``weight=3`` 推导 prerequisite。

输出保证无自环、无重复 ``(from_tool, to_tool)``，所有引用的工具均存在。

**本阶段不对图的性质做任何要求或干预。** 不限制入度、不检查是否存在零入度
工具、不做环检测、不做传递闭包消减。图允许有向环 —— 若 ``A → B`` 和
``B → A`` 各有成立的任务语义，两条都保留；禁止链内循环是 Step 2 的职责。
LLM 判出多少边就输出多少边。

输入
====

``stage_input`` 是 ``BuildGraphInput``：

``config``
    完整运行配置。本阶段只使用 ``config.llm``；不自行读取其他配置文件或环境变量。
    ``config.graph`` 当前为空 ``{}``，本阶段不接受建图参数。

``environment``
    Step 0 已校验的完整 ``environment.json``。可使用环境名称与描述、
    ``resources``、``rules``，以及工具的 ``name``、``description``、
    ``inputSchema`` 和 ``outputSchema``。严禁读取或向 LLM 发送 ``tools[].internal``。
    Step 1 不接收 ``initial_state``，不得通过读 workspace 偷渡运行时知识。

内部分工
========

本阶段拆成六个各自可独立测试的环节，``build_graph`` 只做编排：

===========================  ====================  ==============================
环节                          纯函数                 职责
===========================  ====================  ==============================
:func:`_compact_tool_view`   是                    单个工具 → 紧凑公开视图
:func:`_environment_context`  是                    环境级公开上下文（含 rules）
:func:`_build_evidence_prompt` 是                   目标 + 候选 + 上下文 → 事实分析 prompt
:func:`_build_decision_prompt` 是                  事实分析 + 候选 → 分类 prompt
:func:`_run_round`             否                    批量调用并按目标重试一次
:func:`_validate_assessments`  是                    校验第一轮完整覆盖与证据
:func:`_validate_decisions`    是                    校验第二轮边及 prerequisite
:func:`_assemble_graph`        是                    合并并稳定排序最终对象
===========================  ====================  ==============================

数据在环节间的形态固定为：

.. code-block:: text

    environment.tools
      → _compact_tool_view 逐个映射     → list[CompactTool]
      → _build_evidence_prompt                 → str
      → _run_round + _validate_assessments      → list[Assessment]
      → _build_decision_prompt                 → str
      → _run_round + _validate_decisions       → (list[Edge], list[Prerequisite])
      → _assemble_graph                        → ToolGraph object

两个 ``_run_round`` 调用都是信任边界：LLM 返回必须通过严格的字段、覆盖范围和语义
一致性校验；失败目标只重试一次，仍失败则终止本阶段，避免部分图泄漏。

公开视图的形态
==============

:func:`_compact_tool_view` 不是把 ``inputSchema``/``outputSchema`` 原样透传，而是
投影成判定依赖真正需要的信息：

.. code-block:: python

    {
        "name": str,
        "description": str,
        "in": {
            "<参数名>": {
                "type": str,
                "required": bool,        # 仅在 required 中出现时才写
                "enum": list,            # 仅在原 schema 有 enum 时才写
            },
        },
        "out": [str, ...],               # 扁平化后的输出字段路径
    }

``out`` 是输出 ``data`` 分支的扁平字段路径，数组用 ``[]`` 标记、嵌套用 ``.``
连接，例如 ``items[].run_id``、``count``。展开深度上限为 3 层，超出部分截断，
因为更深的嵌套对"A 的输出能否喂给 B 的输入"这一判断没有增量价值。
只取 ``outputSchema.oneOf`` 的成功分支（``success`` 为 ``true`` 的那一支）中
``data`` 的结构，失败分支对依赖判定无意义，不投影。

**为什么必须投影而不是原样透传。** 已测量三个参考环境：完整 schema 中约 **97%**
的字符是 JSON Schema 的结构性噪声（``type``、``description``、
``additionalProperties`` 等重复关键字），而全部工具的 ``description`` 合计仅
约 800 字符。原样透传时单个目标的 prompt 约 10,000–12,500 tokens，整个阶段
（每个工具一次）达到 **23.5 万–35 万 input tokens**；换用上述紧凑视图后单次
降到约 3,100–4,500 tokens，整阶段约 7.7 万–12.6 万，**减少约 81%**，
且参数流动信号完整保留 —— 例如 ``list_test_runs`` 的
``out`` 含 ``items[].run_id``，``create_bug_report`` 的 ``in`` 需要
``source_run_id``，这条边的依据在紧凑视图里依然可直接读出。

投影同时降低了截断风险：Step 1 的失败模式之一就是输出撞上 ``max_tokens``
导致 JSON 不完整，而输入越短、留给输出的额度越大。

依赖语义
========

``A → B`` 表示：假设 ``A`` 已成功，存在公开契约支持的具体任务，使 ``B`` 可以立即
作为下一次工具调用；若中间还必须调用另一个工具，则不是直接边。判断时不得因为调用方
也可能已知输入、或另有来源而降级。仅字段同名、类型相同或主题相似不构成关系。

依据只用自然语言的 ``reason`` 承载，不再有结构化证据字段。上一轮实践表明
``parameter_evidence`` / ``state_evidence`` 无人消费，却要求 LLM 严格返回空数组、
本地再逐项校验形状，成本大于收益。

``weight`` 只表示直接下一跳关系等级，不是 prerequisite、采样概率或置信度：

* ``3``：强直接关系，A 的确定产物或状态使 B 成为紧接着的自然调用。
* ``2``：明确工作流转移，A 与 B 属于清晰连续的业务步骤，但并非强绑定。
* ``1``：有具体任务依据的弱直接关系；A 的确定功能可能生成 B 的需求或有效输入。
* ``0``：**已审查，判定无依赖**。这是有效输出而非错误。

``weight=0`` 的作用是**完整性检查**：prompt 要求模型逐一审查全部候选，因此对
没有关系的候选也应显式回答 0，而不是省略。据此可以判断模型是否漏审了工具 ——
省略和"看过后否定"在只接受 1/2/3 的设计下无法区分。

处理方式：``weight=0`` 的项计入审查完整性统计，但**不进入 tool_graph**
（图只保存真实存在的边）；只有正边要求 reason 非空。

**完整性是硬门禁。** 每个目标必须对其全部候选（工具总数 - 1）都明确表态；
漏审任何一个即报错。理由是"看过后否定"和"根本没看"
在输出里无法区分，而后者意味着该目标的判定不完整、整张图不可信。报错会触发
单目标重试，重试后仍漏审则整阶段失败。

prerequisite 另行表达执行目标前必须满足的历史。它支持 ``any_of`` 方案和方案内
``all_of`` 组合；不能从边权推导，也不能把所有 weight=3 来源自动合并为硬前置。

处理要求
========

以下每条都标注归属函数，实现时不要把逻辑写到别的环节里。

1. （``_compact_tool_view``）先为每个工具构造上节定义的紧凑公开视图；
   通过**只挑选**公开字段来移除 ``internal``，而不是拷贝整个工具再 ``del``，
   更不是在 prompt 中口头声称不使用它。视图里出现 ``internal`` 的任何片段
   都属于实现错误。
2. （``build_graph`` 编排）只采用 **per-target selection**：每个工具轮流作为
   ``to_tool``，每次把其他全部工具作为候选交给 LLM，选出它的直接前置工具。
   不做候选召回、不实现 pairwise 双跑，不对多套边做仲裁。

   全部目标的请求可用 :func:`tool_graph.llm.infer` 的批量形式一次提交，
   由 ``llm.max_concurrency`` 控制并发；但 prompt 的构造顺序和结果的消费顺序
   都必须按 ``environment.tools`` 的原始顺序，不依赖返回时序。
   prompt 可以说明"只列出真正必需的前置工具"，但本阶段不对返回条数做任何
   本地限制 —— 不截断入边，不干预图的密度。
3. （``_build_evidence_prompt``、``_build_decision_prompt`` + ``_environment_context``）prompt 必须同时给出目标工具、
   候选工具、环境公开上下文、上述反例和固定输出结构；候选不包含目标自身。
   环境上下文由 ``_environment_context`` 单独构造，包含环境 ``name``、
   ``description``、``resources`` 和 ``rules``；``rules`` 必须给出，因为跨资源的
   业务规则常常是状态依赖的唯一线索。

   第一轮必须返回 ``{"assessments": [...]}``；第二轮必须返回
   ``{"decisions": [...], "prerequisite_alternatives": [...]}``。
3a. （``_run_round``）每轮通过 :func:`tool_graph.llm.infer` 批量调用 LLM，并用
   :func:`tool_graph.llm.parse_json_object` 解析；每个失败目标只重试一次。
   ``MalformedJSONError`` 等解析或语义错误不得被当作“无边”静默吞掉。
4. （``_validate_assessments`` 与 ``_validate_decisions``）用本地确定性代码校验每一项：``from_tool`` 必须是
   ``environment.tools`` 中的已知工具名且不等于当前目标（自环在此就地丢弃，
   不留到装配阶段）、``weight`` 必须是 0/1/2/3 之一（``True`` 不算 1）。
   第一轮要求全部候选都有事实判断；第二轮要求全部候选都有 0/1/2/3 分类，且
   prerequisite 只能引用正边。不得直接信任 LLM 输出。
5. （``_assemble_graph``）对同一 ``from_tool → to_tool`` 只保留一条边，
   严禁自环。边按 ``weight`` 降序、``from_tool`` 和 ``to_tool`` 字典序稳定输出。
   这两件事（去重、稳定排序）是装配的**全部**职责。
6. 图不强制无环。若 ``A → B`` 和 ``B → A`` 各有成立的任务语义，两条都保留；
   禁止链内循环是 Step 2 的职责。``_assemble_graph`` 不做环检测，也不因为
   存在环而报错；同样不检查入度分布或零入度工具是否存在。
7. 只输出直接前置边。如果已有 ``A → B`` 和 ``B → C`` 且 ``A → C`` 没有独立的
   直接证据，不能因为可传递到达就保留 ``A → C``。这条靠 per-target 的设问方式
   和 prompt 中的反例来保证，``_assemble_graph`` 不做传递闭包消减 —— 它无法
   区分"可传递到达"和"独立成立的直接依赖"。

输出
====

返回 ``BuildGraphOutput``，只新增 ``tool_graph``。工具节点已存在于
``environment.tools``，不在图中重复保存。每条边的形状是：

.. code-block:: python

    {
        "from_tool": str,
        "to_tool": str,
        "weight": 1 | 2 | 3,
        "reason": str,
    }

``reason`` 是判定该边的必填依据。
输出顺序必须稳定，便于重跑比较和 Bundle 差异审查。

失败行为
========

本阶段的失败一律是**整阶段失败**，没有"部分成功"这种中间状态。归属如下：

* ``_compact_tool_view``：工具缺少 ``name``、``description``、``inputSchema`` 或
  ``outputSchema`` 时抛异常。正常情况下 Step 0 的字段检查已经挡住，这里是
  防御性断言，不做兜底填充。
* ``_validate_assessments`` / ``_validate_decisions``：未知工具名、非法 ``weight``、
  漏审、语义冲突或 prerequisite 结构非法时，
  抛出同时包含目标工具名和出错项的异常。
* ``_assemble_graph``：本环节没有失败条件。它只做去重和排序，不校验图的性质，
  因此不会抛异常。

以上任何失败都先触发该目标的单目标重试；重试后仍失败才整阶段失败。

任一目标失败时，本阶段不返回不完整的 ``tool_graph``，也不在返回值中私自增加
部分成功字段。当前流水线不做断点续跑，因此一次失败意味着整个 run 重来 ——
这正是把 ``max_tokens`` 配足、并用紧凑视图压缩输入的现实理由。

边界与禁止事项
==============

* 不读 workspace、不执行工具、不修改环境或初始状态。
* 不生成候选链、不实例化参数、不撰写任务文本。
* 不保存传递闭包，不把字段同名当作依赖。
* 不做候选召回、成本预算或 token 统计。
* 不对图的性质提要求：不限入度、不检查零入度工具、不检查连通性或环。
  Step 2 若因图的形态无法采样，由 Step 2 自己报错。
* 不读写运行产物，不访问 ``run_dir``；本阶段的 Input 里没有它。

完成条件
========

每个工具都作为目标完成两轮判定；所有候选均有明确结论并通过本地校验；图中无自环
和重复边，prerequisite 引用合法；输出顺序稳定且不包含 ``internal`` 或工具实现细节。

本阶段不保证图具备任何拓扑性质（零入度工具存在、无环、连通等）。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .contracts import BuildGraphInput, BuildGraphOutput
from .llm import BatchInferenceError, InferenceResult, MalformedJSONError, infer, parse_json_object

# 0 是有效输出：表示模型已审查该候选并判定无依赖。prompt 要求对每个候选都给出
# 结论，weight=0 就是"已审查、无关系"的显式回答，据此可确认模型没有漏审工具。
# 它不进入 tool_graph —— 图只保存真实存在的边。
WEIGHTS = (0, 1, 2, 3)
MAX_OUT_DEPTH = 3
ASSESSMENT_CONNECTIONS = {
    "required_input",
    "optional_input",
    "required_state",
    "state_observation",
    "workflow_transition",
    "semantic_influence",
    "none",
}
VALUE_ORIGINS = {
    "generated",
    "selected",
    "derived",
    "echoed",
    "not_applicable",
    "unknown",
}
INPUT_AVAILABILITIES = {
    "runtime_only",
    "task_input",
    "not_applicable",
    "unknown",
}
ASSESSMENT_FIELDS = {
    "from_tool",
    "immediate_next",
    "intermediate_tool_required",
    "connection",
    "value_origin",
    "input_availability",
    "evidence",
    "condition",
}


def build_graph(stage_input: BuildGraphInput) -> BuildGraphOutput:
    """Analyze evidence, classify direct edges, and assemble the tool graph."""
    environment = stage_input["environment"]
    config = stage_input["config"]
    views = [_compact_tool_view(tool) for tool in environment["tools"]]
    names = [view["name"] for view in views]
    context = _environment_context(environment)
    candidates = [
        [other for other in views if other["name"] != target["name"]]
        for target in views
    ]

    evidence_prompts = [
        _build_evidence_prompt(target, candidate_set, context)
        for target, candidate_set in zip(views, candidates)
    ]
    assessments = _run_round(
        evidence_prompts,
        names,
        config.llm,
        "事实分析",
        lambda result, target: _validate_assessments(
            target, _request_assessments(result, target), names
        ),
    )

    decision_prompts = [
        _build_decision_prompt(target, candidate_set, context, target_assessments)
        for target, candidate_set, target_assessments in zip(views, candidates, assessments)
    ]
    assessment_by_target = dict(zip(names, assessments))
    classified = _run_round(
        decision_prompts,
        names,
        config.llm,
        "关系分类",
        lambda result, target: _validate_decisions(
            target,
            _request_json_object(result, target, "第二轮"),
            assessment_by_target[target],
            names,
        ),
    )
    edges_by_target = {
        name: result[0] for name, result in zip(names, classified)
    }
    prerequisites_by_target = {
        name: result[1] for name, result in zip(names, classified)
    }
    return {
        "tool_graph": _assemble_graph(
            edges_by_target, prerequisites_by_target, names
        )
    }


def _run_round(
    prompts: list[str],
    target_names: list[str],
    llm_config: dict[str, Any],
    round_name: str,
    consume: Callable[[InferenceResult, str], Any],
) -> list[Any]:
    """Run one batch and retry all invalid targets together once."""
    try:
        results: list[InferenceResult | Exception] = list(infer(prompts, llm_config=llm_config))
    except BatchInferenceError as error:
        results = list(error.outcomes)
    if len(results) != len(target_names):
        raise ValueError(f"建图{round_name}返回数量与目标工具数量不一致")

    output: list[Any] = [None] * len(target_names)
    invalid: list[int] = []
    for index, (target_name, result) in enumerate(zip(target_names, results)):
        try:
            if isinstance(result, Exception):
                raise result
            output[index] = consume(result, target_name)
        except Exception:
            invalid.append(index)
    if not invalid:
        return output

    try:
        retries: list[InferenceResult | Exception] = list(infer(
            [prompts[index] for index in invalid], llm_config=llm_config
        ))
    except BatchInferenceError as error:
        retries = list(error.outcomes)
    if len(retries) != len(invalid):
        raise ValueError(f"建图{round_name}重试返回数量不一致")
    for index, retry in zip(invalid, retries):
        target_name = target_names[index]
        try:
            if isinstance(retry, Exception):
                raise retry
            output[index] = consume(retry, target_name)
        except Exception as error:
            raise ValueError(
                f"目标工具 {target_name} 的{round_name}结果非法：{error}"
            ) from error
    return output


def _compact_tool_view(tool: dict[str, Any]) -> dict[str, Any]:
    """把单个工具投影成"公开视图的形态"一节定义的紧凑视图。

    只挑选公开字段（name/description/inputSchema/outputSchema），因此
    ``internal`` 天然不可能出现在结果中 —— 不是拷贝后 del。
    """
    for field in ("name", "description", "inputSchema", "outputSchema"):
        if field not in tool:
            raise ValueError(f"工具缺少公开字段 {field}：{tool.get('name', '<未命名>')}")

    input_schema = tool["inputSchema"]
    required = set(input_schema.get("required") or ())
    parameters: dict[str, Any] = {}
    for key, spec in (input_schema.get("properties") or {}).items():
        entry: dict[str, Any] = {"type": spec.get("type")}
        if key in required:
            entry["required"] = True
        if "enum" in spec:
            entry["enum"] = spec["enum"]
        # 数组/对象参数必须保留元素字段名，否则会丢掉判定依赖的关键信号：
        # 例如 finstat 的 create_standard_journal_entry.lines[] 含 account_id，
        # 只写 "type": "array" 就无法看出它需要别的工具先产出账户 ID。
        nested = _nested_field_names(spec)
        if nested:
            entry["fields"] = nested
        parameters[key] = entry

    return {
        "name": tool["name"],
        "description": tool["description"],
        "in": parameters,
        "out": _flatten_output(tool["outputSchema"]),
    }


def _nested_field_names(spec: dict[str, Any]) -> list[str]:
    """取数组元素或对象参数的字段名；标量参数返回 ``[]``。

    只取一层字段名，不展开更深结构 —— 判定依赖需要知道"这个参数要 account_id"，
    不需要知道 account_id 的完整 schema。
    """
    node = spec.get("items") if spec.get("type") == "array" else spec
    if not isinstance(node, dict):
        return []
    return list((node.get("properties") or {}).keys())


def _flatten_output(output_schema: dict[str, Any]) -> list[str]:
    """取成功分支 ``data`` 的扁平字段路径，如 ``items[].run_id``、``count``。

    成功分支按 ``success.const is True`` 定位，找不到时退回"含 data 键的分支"；
    实测三个参考环境共 76 个工具，两种判据结果完全一致。失败分支对依赖判定
    无意义，不投影。
    """
    branches = output_schema.get("oneOf") or []
    success = next(
        (
            branch
            for branch in branches
            if (branch.get("properties") or {}).get("success", {}).get("const") is True
        ),
        None,
    )
    if success is None:
        success = next((branch for branch in branches if "data" in (branch.get("properties") or {})), None)
    if success is None:
        return []
    return _flatten_node((success.get("properties") or {}).get("data") or {})


def _flatten_node(node: dict[str, Any], prefix: str = "", depth: int = 0) -> list[str]:
    """递归展开 schema 节点为路径列表；深度超过 ``MAX_OUT_DEPTH`` 即截断。

    更深的嵌套对"A 的输出能否喂给 B 的输入"这一判断没有增量价值。
    """
    if depth > MAX_OUT_DEPTH or not isinstance(node, dict):
        return []
    if node.get("type") == "array" and isinstance(node.get("items"), dict):
        return _flatten_node(node["items"], f"{prefix}[]", depth + 1)
    properties = node.get("properties")
    if not properties:
        return [prefix] if prefix else []
    paths: list[str] = []
    for key, spec in properties.items():
        path = f"{prefix}.{key}" if prefix else key
        nested = _flatten_node(spec, path, depth + 1) if isinstance(spec, dict) else []
        paths.extend(nested or [path])
    return paths


def _environment_context(environment: dict[str, Any]) -> dict[str, Any]:
    """提取环境级公开上下文：``name``、``description``、``resources``、``rules``。

    ``rules`` 必须包含 —— 跨资源的业务规则常常是状态依赖的唯一线索。
    """
    return {
        "name": environment.get("name"),
        "description": environment.get("description"),
        "resources": environment.get("resources") or [],
        "rules": environment.get("rules") or [],
    }


def _build_evidence_prompt(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
) -> str:
    """Build the first-round prompt that extracts evidence without classifying it."""
    return EVIDENCE_PROMPT_TEMPLATE.format(
        environment=json.dumps(context, ensure_ascii=False, indent=2),
        target=json.dumps(target, ensure_ascii=False, indent=2),
        candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
    )


def _build_decision_prompt(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
    assessments: list[dict[str, Any]],
) -> str:
    """Build the second-round prompt that classifies validated evidence."""
    return DECISION_PROMPT_TEMPLATE.format(
        environment=json.dumps(context, ensure_ascii=False, indent=2),
        target=json.dumps(target, ensure_ascii=False, indent=2),
        candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
        assessments=json.dumps(assessments, ensure_ascii=False, indent=2),
    )


EVIDENCE_PROMPT_TEMPLATE = """\
你在分析有向工具图中的候选关系 A -> B。本轮只提取可核对的事实，不决定是否连边，
不分配 weight，也不判断 prerequisite。

环境公开上下文：
{environment}

目标工具 B：
{target}

候选工具 A（不含 B 自身）：
{candidates}

固定判断视角：假设某个候选工具 A 已经成功执行。只考虑公开工具契约能够支持的
具体任务场景，判断在 B 的其他硬前置已经满足时，B 能否直接作为下一次工具调用。
不要改问“B 在所有其他任务中能否独立执行”。调用方可能提前知道某个值、或者另一个
工具也可能提供这个值，都不能否定当前 A -> B 路径中真实存在的数据或状态交接。

对每个候选分别分析：

1. immediate_next：A 成功后，不调用其他工具，B 是否可以成为合理的下一次调用。
   从 A 返回的列表中选择一个元素，以及填写普通的人类可决定文本，不算调用其他工具。
   这里判断的是是否存在公开契约支持的直接路径，而不是 B 是否为最常见、最推荐的下一步；
   只要 A 的确定结果可直接改变 B 的输入、范围或动作，且没有技术中间步骤，就写 true。
2. intermediate_tool_required：A 与 B 之间是否必须调用另一个工具完成查询、验证、
   转换、解析或目标对象发现。若是，必须写 true。
3. connection：只选择最具体的一项：
   - required_input：A 的结果可填入 B 的必填输入；
   - optional_input：A 的结果可填入 B 的可选输入；
   - required_state：A 创建或建立了 B 本次执行所需的实体或状态；
   - state_observation：B 会直接读取、核验或呈现 A 刚改变的状态；
   - workflow_transition：没有直接字段交接，但两个工作块存在明确的直接衔接；
   - semantic_influence：A 的具体结果会影响 B 的参数选择、范围或验证方式；
   - none：以上均不成立。
4. value_origin：只能填写以下六个字符串之一：
   - `generated`：A 新生成该值或实体；
   - `selected`：从 A 的结果中选择该值或实体；
   - `derived`：根据 A 的结果计算或推导；
   - `echoed`：A 仅原样回显自己的输入；
   - `not_applicable`：当前关系不涉及值来源；
   - `unknown`：公开契约无法确认来源。
5. input_availability：只能填写以下四个字符串之一：
   - `runtime_only`：B 需要的动态标识、句柄、实体或状态只能在工具运行后取得，不能作为
     自然任务描述中的业务信息直接提供；
   - `task_input`：名称、标题、描述、目标状态、分类、筛选条件等普通业务信息可以自然地
     写在任务描述中；
   - `not_applicable`：当前关系不涉及向 B 提供输入或所需状态；
   - `unknown`：公开契约无法判断。
   该字段描述信息的固有可见性，不描述本条假设路径恰好怎样取得它。名称、标题、描述、
   分类、组件、目标状态、筛选条件等有业务含义的信息，即使本路径从 A 的结果中选择，
   仍是 task_input。runtime_only 只用于运行时才产生或发现的内部引用（如不透明 ID、句柄、
   令牌）或必须由工具建立的状态；不能因为 A 碰巧返回了某项业务信息就写 runtime_only。
6. evidence：用一句话指出 A 的具体输出/状态和 B 的具体输入/行为。不能只写字段同名、
   类型相同、共享资源或主题相近。
7. condition：只有关系需要额外业务条件时填写该条件，否则返回 null。

直接性规则：
- A -> B 和 B -> A 必须分别判断，不能因为一个方向成立而补出反向关系。
- 若 A -> B -> C，而 A 到 C 必须经过 B 的验证、转换、解析、选择或状态处理，
  则 A 不是 C 的直接来源。
- 如果 C 同时需要 A 和 B，分析 A -> C 时可以假定 B 已在更早位置完成；
  但必须明确 A 自己为 C 提供了哪一项独立前置。
- 如果 B 创建新实体，且它的标识由调用方为新实体指定或重复标识会被拒绝，A 返回的
  已有实体标识不能当作 B 的 required_input；A 的其他结果仍可按实际用途判断。
- 查询工具把调用时用于定位对象的键放进结果对象，即使字段嵌套或同时返回其他详情，
  该定位键仍是 echoed，不是 selected；selected 只表示从 A 新返回的候选集合中作选择。
- state_observation 的依据是 A 改变了 B 随后读取的状态，不是 A 回显的定位键；此时
  value_origin 和 input_availability 均填 not_applicable。
- A 的功能和具体结果能明确决定 B 的文本、范围或选择时，可以是 semantic_influence；
  但仅仅“可能有帮助”“属于同一主题”或“任意文本理论上都能写入文本字段”仍是 none。
  evidence 必须写出该结果在 B 中的明确用途。A 不必提供 B 的全部必填输入；其他输入
  已满足时，只要 A 的确定结果确实会改变 B 的调用内容，仍是直接的弱关系。
- semantic_influence 必须改变 B 的调用参数、范围或动作内容。对于无参数且行为固定的
  查询，A 的结果不能改变 B 的调用；仅帮助解释或对照 B 的固定输出不算 semantic_influence。
- 不要用“不常见”“通常不会这样做”否定一条由公开契约明确支持的具体弱路径；但也不能
  仅凭同领域、共享资源或想象 A 未声明的输出补边。完整覆盖所有有明确用途的候选。

必须恰好返回每一个候选一次，顺序与候选列表一致。只返回 JSON object：

{{"assessments":[
  {{
    "from_tool":"候选工具名",
    "immediate_next":true,
    "intermediate_tool_required":false,
    "connection":"required_input",
    "value_origin":"selected",
    "input_availability":"runtime_only",
    "evidence":"候选输出中的某个动态值可直接填入目标的某个必填输入",
    "condition":null
  }}
]}}

每项只能包含上述八个字段，不要输出其他内容。
"""


DECISION_PROMPT_TEMPLATE = """\
你在为有向工具图中的候选关系 A -> B 作最终分类。你会看到公开工具定义和上一轮的
事实分析。本轮决定每条边的 weight，并为目标 B 汇总 prerequisite 完整组合。

环境公开上下文：
{environment}

目标工具 B：
{target}

候选工具 A：
{candidates}

上一轮事实分析：
{assessments}

固定判断视角：假设 A 已成功执行，判断在一个由公开契约支持的具体路径中，B 是否适合
作为下一次工具调用。不要因为 B 在别的任务中可以独立执行、调用方可能已经知道参数、
或其他工具也能提供参数，而否定当前 A -> B 路径。

先判断是否连边：
- immediate_next=false，或者 intermediate_tool_required=true：weight=0。
- connection=none，或者 evidence 只有字段同名、类型相同、共享资源、主题相近：weight=0。
- 其他情况再判断 weight；全部候选判断完成后，再单独汇总 prerequisite 组合。

prerequisite 只表示链历史约束：在本流水线生成的任务链中，执行 B 以前，哪些工具组合
必须已经执行，才能取得 B 所需且不能作为自然任务输入直接提供的动态值、实体或资源状态。

- prerequisite_alternatives 是任选关系：其中任意一个方案满足，B 就具备工具历史前置。
- 每个方案的 all_of 是同时关系：该方案列出的工具必须全部已经执行。
- 如果 A、B 必须共同准备目标 C，写一个 all_of=[A,B] 的方案。
- 如果 A 或 D 任意一个都能独立准备目标 C，写两个方案：all_of=[A] 和 all_of=[D]。
- A 只回显自己的输入值，不能仅据此进入 prerequisite 方案。
- 只有上一轮 input_availability=runtime_only，且值确由 A 生成、选择或推导的来源，才能
  进入 prerequisite 方案。普通名称、标题、描述、分类、期望状态、筛选条件等可以自然
  写进任务的业务信息，不构成全局硬前置。
- state_observation 表示 B 可以观察 A 刚完成的变更，但 B 本身并不以 A 为全局硬前置，
  不能进入 prerequisite；required_state 才表示 A 建立了 B 本次执行不可缺少的状态。
- “新标识不得重复”不等于必须先查询已有标识；如果调用方可以自行生成或指定新标识，
  查询工具不是创建工具的 prerequisite。只有契约明确要求通过工具分配时才是硬前置。
- 可选输入、改善结果、辅助验证、影响选择或自然工作流不进入 prerequisite 方案。
- 没有硬前置时 prerequisite_alternatives 返回空数组。
- prerequisite 中出现的工具必须对目标存在 weight>0 的直接边，不能凭空引用候选。

weight 只表示 B 作为 A 的直接下一跳有多强的依据，不表示 prerequisite、采样概率或
模型置信度：

- 3 强直接关系：A 产生或改变的具体值、实体或状态被 B 直接使用，关系明确。
- 2 明确工作流转移：没有足够依据判为 3，但 A 完成的工作块与 B 开始的工作块存在
  清楚、直接、常规的业务衔接。
- 1 具体的弱关系：存在公开契约支持的合理场景，A 的具体结果会直接影响 B 的可选输入、
  范围、判断或验证方式，但不是强交接或固定工作流。
- 0 无直接关系：只存在主题、字段名、类型或共享资源关联，或者中间需要其他工具。

weight 与 prerequisite 组合必须分别判断，不能把所有 weight=3 的来源自动合并成一个
all_of 方案，也不能因为某条边不属于 prerequisite 就降低它本来明确的关系等级。

等级上限必须遵守：semantic_influence 最多为 1；workflow_transition 和 optional_input
最多为 2；如果唯一依据是 A 回显调用 A 时已有的值（value_origin=echoed），最多为 1；
value_origin=unknown 时也最多为 1。不能只因某字段可填入 B 的必填参数就突破这些上限。
state_observation 表示 A 已实际改变状态且 B 直接读取该新状态，必须为 3；用于定位状态
的标识即使来自 A 的输入回显，也不能降低这项状态关系。

反例：
- A 返回 B 需要的动态 ID；即使调用方可能提前知道该 ID，当前选定路径仍可构成强交接。
- A 只把调用 A 时已有的 ID 原样返回；回显本身不产生新的 prerequisite 方案。
- A 与 C 属于同一个长任务，但必须先调用 B 完成验证或转换；A -> C 应为 weight=0。
- A 的结果只是在理论上可能被写入 B 的任意文本字段，没有明确用途；应为 weight=0。

每条边的 reason 必须说明 A 的哪项具体结果或状态被 B 如何使用、为什么能够直接相邻、
以及为何属于该 weight。每个 prerequisite 方案的 reason 必须说明为什么 all_of 中的工具
需要共同完成，以及不同方案为什么可以互相替代。不要复述规则，不要使用“可能相关”
作为唯一理由。

必须恰好返回每一个候选一次，顺序与候选列表一致。不连边也必须返回 weight=0。
只返回 JSON object：

{{
  "decisions":[
    {{
      "from_tool":"候选工具名",
      "weight":3,
      "reason":"候选选出的动态标识可直接填入目标的必填标识参数；目标可立即操作该对象"
    }}
  ],
  "prerequisite_alternatives":[
    {{
      "all_of":["候选工具名"],
      "reason":"该方案产生目标本次调用不可从自然任务文本直接取得的动态标识"
    }}
  ]
}}

decisions 每项只能包含 from_tool、weight、reason；prerequisite_alternatives 每项只能
包含 all_of、reason。不要输出其他字段。
"""


def _request_json_object(
    result: InferenceResult,
    target_name: str,
    round_name: str,
) -> dict[str, Any]:
    """Parse one LLM response without attempting lossy JSON repair."""
    try:
        return parse_json_object(result.text)
    except MalformedJSONError as error:
        raise ValueError(f"目标 {target_name} 的{round_name}回复无法解析：{error}") from error


def _request_assessments(
    result: InferenceResult,
    target_name: str,
) -> list[dict[str, Any]]:
    payload = _request_json_object(result, target_name, "第一轮")
    if set(payload) != {"assessments"} or not isinstance(payload["assessments"], list):
        raise ValueError(f"目标 {target_name} 的第一轮回复必须只含 assessments 数组")
    return payload["assessments"]


def _validate_assessments(
    target_name: str,
    raw_assessments: list[dict[str, Any]],
    tool_names: set[str] | list[str],
) -> list[dict[str, Any]]:
    """Validate the complete first-round evidence assessment for one target."""
    if not isinstance(raw_assessments, list):
        raise ValueError(f"目标 {target_name} 的 assessments 必须是 array")
    expected_order = sorted(tool_names) if isinstance(tool_names, set) else list(tool_names)
    expected_order = [name for name in expected_order if name != target_name]
    expected = set(expected_order)
    reviewed: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, item in enumerate(raw_assessments):
        label = f"目标 {target_name} 的 assessments[{index}]"
        if not isinstance(item, dict) or set(item) != ASSESSMENT_FIELDS:
            raise ValueError(f"{label} 字段必须恰好为 {sorted(ASSESSMENT_FIELDS)}")
        source = item["from_tool"]
        if source not in expected or source in reviewed:
            raise ValueError(f"{label}.from_tool 未知、重复或为目标自身：{source!r}")
        if type(item["immediate_next"]) is not bool:
            raise ValueError(f"{label}.immediate_next 必须是 bool")
        if type(item["intermediate_tool_required"]) is not bool:
            raise ValueError(f"{label}.intermediate_tool_required 必须是 bool")
        if item["immediate_next"] and item["intermediate_tool_required"]:
            raise ValueError(f"{label} 的 immediate_next 与 intermediate_tool_required 冲突")
        if item["connection"] not in ASSESSMENT_CONNECTIONS:
            raise ValueError(f"{label}.connection 非法：{item['connection']!r}")
        if item["value_origin"] not in VALUE_ORIGINS:
            raise ValueError(f"{label}.value_origin 非法：{item['value_origin']!r}")
        if item["input_availability"] not in INPUT_AVAILABILITIES:
            raise ValueError(
                f"{label}.input_availability 非法：{item['input_availability']!r}"
            )
        if item["connection"] == "state_observation" and (
            not item["immediate_next"]
            or item["intermediate_tool_required"]
            or item["value_origin"] != "not_applicable"
            or item["input_availability"] != "not_applicable"
        ):
            raise ValueError(
                f"{label}.state_observation 必须直接相邻，且值来源和输入可见性均为"
                "not_applicable"
            )
        evidence = item["evidence"]
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(f"{label}.evidence 必须是非空字符串")
        condition = item["condition"]
        if condition is not None and not isinstance(condition, str):
            raise ValueError(f"{label}.condition 必须是字符串或 null")
        reviewed.add(source)
        validated.append(item)
    missing = sorted(expected - reviewed)
    if missing:
        raise ValueError(f"目标 {target_name} 的 assessments 漏审：{', '.join(missing)}")
    order = {name: index for index, name in enumerate(expected_order)}
    return sorted(validated, key=lambda item: order[item["from_tool"]])


def _validate_decisions(
    target_name: str,
    payload: dict[str, Any],
    assessments: list[dict[str, Any]],
    tool_names: set[str] | list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate second-round edge decisions and target prerequisite alternatives."""
    if not isinstance(payload, dict) or set(payload) != {
        "decisions", "prerequisite_alternatives"
    }:
        raise ValueError(
            f"目标 {target_name} 的第二轮结果必须只含 decisions 和 prerequisite_alternatives"
        )
    raw_decisions = payload["decisions"]
    alternatives = payload["prerequisite_alternatives"]
    if not isinstance(raw_decisions, list) or not isinstance(alternatives, list):
        raise ValueError(f"目标 {target_name} 的第二轮数组字段非法")

    expected_order = sorted(tool_names) if isinstance(tool_names, set) else list(tool_names)
    expected_order = [name for name in expected_order if name != target_name]
    expected = set(expected_order)
    assessment_by_source = {item["from_tool"]: item for item in assessments}
    reviewed: set[str] = set()
    positive: set[str] = set()
    edges: list[dict[str, Any]] = []
    for index, item in enumerate(raw_decisions):
        label = f"目标 {target_name} 的 decisions[{index}]"
        if not isinstance(item, dict) or set(item) != {"from_tool", "weight", "reason"}:
            raise ValueError(f"{label} 字段必须恰好为 from_tool、weight、reason")
        source = item["from_tool"]
        if source not in expected or source in reviewed:
            raise ValueError(f"{label}.from_tool 未知、重复或为目标自身：{source!r}")
        weight = item["weight"]
        if type(weight) is not int or weight not in WEIGHTS:
            raise ValueError(f"{label}.weight 必须是 0/1/2/3")
        reason = item["reason"]
        if not isinstance(reason, str) or (weight and not reason.strip()):
            raise ValueError(f"{label}.reason 必须是非空字符串")
        assessment = assessment_by_source.get(source)
        if assessment is None:
            raise ValueError(f"{label} 在第一轮中不存在")
        if weight and (
            not assessment["immediate_next"]
            or assessment["intermediate_tool_required"]
            or assessment["connection"] == "none"
        ):
            raise ValueError(f"{label} 与第一轮的非直接判断冲突")
        maximum_weight = {
            "semantic_influence": 1,
            "workflow_transition": 2,
            "optional_input": 2,
        }.get(assessment["connection"], 3)
        if assessment["value_origin"] == "unknown" or (
            assessment["value_origin"] == "echoed"
            and assessment["connection"] in {"required_input", "optional_input"}
        ):
            maximum_weight = min(maximum_weight, 1)
        if weight > maximum_weight:
            raise ValueError(
                f"{label}.weight 超过 {assessment['connection']}/"
                f"{assessment['value_origin']} 的上限 {maximum_weight}"
            )
        if assessment["connection"] == "state_observation" and weight != 3:
            raise ValueError(f"{label}.state_observation 必须使用 weight=3")
        reviewed.add(source)
        if weight:
            positive.add(source)
            edges.append({
                "from_tool": source,
                "to_tool": target_name,
                "weight": weight,
                "reason": reason.strip(),
            })
    missing = sorted(expected - reviewed)
    if missing:
        raise ValueError(f"目标 {target_name} 的 decisions 漏审：{', '.join(missing)}")

    normalized_alternatives: list[dict[str, Any]] = []
    seen_alternatives: set[tuple[str, ...]] = set()
    for index, item in enumerate(alternatives):
        label = f"目标 {target_name} 的 prerequisite_alternatives[{index}]"
        if not isinstance(item, dict) or set(item) != {"all_of", "reason"}:
            raise ValueError(f"{label} 字段必须恰好为 all_of、reason")
        required = item["all_of"]
        if (
            not isinstance(required, list)
            or not required
            or any(not isinstance(name, str) for name in required)
            or len(required) != len(set(required))
        ):
            raise ValueError(f"{label}.all_of 必须是非空、无重复的工具名数组")
        required_key = tuple(sorted(required))
        if set(required_key) - positive:
            raise ValueError(f"{label} 只能引用目标工具的正边")
        reason = item["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{label}.reason 必须是非空字符串")
        invalid_sources = [
            name for name in required_key
            if assessment_by_source[name]["connection"] not in {
                "required_input", "required_state"
            }
            or assessment_by_source[name]["input_availability"] != "runtime_only"
            or assessment_by_source[name]["value_origin"] not in {
                "generated", "selected", "derived"
            }
        ]
        if invalid_sources:
            raise ValueError(
                f"{label} 只能引用 required_input/required_state、"
                f"input_availability=runtime_only 且由来源工具产生的输入："
                f"{', '.join(invalid_sources)}"
            )
        if required_key in seen_alternatives:
            continue
        seen_alternatives.add(required_key)
        normalized_alternatives.append({
            "all_of": list(required_key),
            "reason": reason.strip(),
        })

    prerequisites = []
    if normalized_alternatives:
        prerequisites.append({
            "to_tool": target_name,
            "any_of": normalized_alternatives,
        })
    return edges, prerequisites


def _assemble_graph(
    edges_by_target: dict[str, list[dict[str, Any]]],
    prerequisites_by_target: dict[str, list[dict[str, Any]]],
    tool_names: list[str],
) -> dict[str, Any]:
    """Deduplicate and deterministically order validated graph records."""
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for target in tool_names:
        for edge in edges_by_target.get(target) or []:
            unique.setdefault((edge["from_tool"], edge["to_tool"]), edge)
    edges = sorted(
        unique.values(),
        key=lambda edge: (-edge["weight"], edge["from_tool"], edge["to_tool"]),
    )
    prerequisites = [
        item
        for target in tool_names
        for item in prerequisites_by_target.get(target) or []
    ]
    return {"edges": edges, "prerequisites": prerequisites}
