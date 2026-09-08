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
:func:`_build_prompt`         是                    目标 + 候选 + 上下文 → 单轮 prompt
:func:`_run_round`             否                    批量调用并按目标重试一次
:func:`_validate_decisions`    是                    校验完整覆盖、边及 prerequisite
:func:`_assemble_graph`        是                    合并并稳定排序最终对象
===========================  ====================  ==============================

数据在环节间的形态固定为：

.. code-block:: text

    environment.tools
      → _compact_tool_view 逐个映射     → list[CompactTool]
      → _build_prompt                         → str
      → _run_round + _validate_decisions      → (list[Edge], list[Prerequisite])
      → _assemble_graph                       → ToolGraph object

``_validate_decisions`` 是信任边界：LLM 返回必须通过字段和覆盖范围校验；不在本地
重复裁决模型的语义。失败目标只重试一次，仍失败则终止本阶段，避免部分图泄漏。

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

* ``3``：近乎绑定的强直接关系，A 直接产生或建立 B 本次调用所用的运行时数据、
  实体或状态。
* ``2``：边界清楚、方向正确的逻辑或工作流衔接，但没有强数据或状态绑定。
* ``1``：A 的确定功能在具体场景中可能生成 B 的需求、有效内容或选择依据。
* ``0``：**已审查，判定无依赖**。这是有效输出而非错误。

``weight=0`` 的作用是**完整性检查**：prompt 要求模型逐一审查全部候选，因此对
没有关系的候选也应显式回答 0，而不是省略。据此可以判断模型是否漏审了工具 ——
省略和"看过后否定"在只接受 1/2/3 的设计下无法区分。

处理方式：``weight=0`` 的项计入审查完整性统计，但**不进入 tool_graph**
（图只保存真实存在的边）；每项都要求 reason 非空，以确认模型采用了固定判断视角。

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
3. （``_build_prompt`` + ``_environment_context``）prompt 必须同时给出目标工具、
   候选工具、环境公开上下文、上述反例和固定输出结构；候选不包含目标自身。
   环境上下文由 ``_environment_context`` 单独构造，包含环境 ``name``、
   ``description``、``resources`` 和 ``rules``；``rules`` 必须给出，因为跨资源的
   业务规则常常是状态依赖的唯一线索。

   单轮必须返回 ``{"decisions": [...], "prerequisite_alternatives": [...]}``。
3a. （``_run_round``）通过 :func:`tool_graph.llm.infer` 批量调用 LLM，并用
   :func:`tool_graph.llm.parse_json_object` 解析；每个失败目标只重试一次。
   ``MalformedJSONError`` 等解析或语义错误不得被当作“无边”静默吞掉。
4. （``_validate_decisions``）用本地确定性代码校验每一项：``from_tool`` 必须是
   ``environment.tools`` 中的已知工具名且不等于当前目标（自环在此就地丢弃，
   不留到装配阶段）、``weight`` 必须是 0/1/2/3 之一（``True`` 不算 1）。
   全部候选都必须有 0/1/2/3 分类和非空理由，且 prerequisite 只能引用正边。
   本地只做结构和引用校验，不用另一套规则覆盖模型的语义判断。
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
* ``_validate_decisions``：未知工具名、非法 ``weight``、空理由、漏审或
  prerequisite 结构非法时，
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

每个工具都作为目标完成一次判定；所有候选均有明确结论并通过本地校验；图中无自环
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


def build_graph(stage_input: BuildGraphInput) -> BuildGraphOutput:
    """Classify direct edges and prerequisites once per target tool."""
    environment = stage_input["environment"]
    config = stage_input["config"]
    views = [_compact_tool_view(tool) for tool in environment["tools"]]
    names = [view["name"] for view in views]
    context = _environment_context(environment)
    candidates = [
        [other for other in views if other["name"] != target["name"]]
        for target in views
    ]

    prompts = [
        _build_prompt(target, candidate_set, context)
        for target, candidate_set in zip(views, candidates)
    ]
    classified = _run_round(
        prompts,
        names,
        config.llm,
        "关系判断",
        lambda result, target: _validate_decisions(
            target,
            _request_json_object(result, target, "建图"),
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


def _build_prompt(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
) -> str:
    """Build one prompt that reasons about every incoming candidate for a target."""
    return PROMPT_TEMPLATE.format(
        environment=json.dumps(context, ensure_ascii=False, indent=2),
        target=json.dumps(target, ensure_ascii=False, indent=2),
        candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
    )


PROMPT_TEMPLATE = """\
你的输出将直接用于工具链采样。A -> B 表示允许采样器在更长的工具链中把 B 直接放在 A 后面。
我们需要由这些边组成可执行、连贯并持续产生任务进展的工具链，而不是相关工具
的拼接。

环境公开上下文：
{environment}

目标工具 B：
{target}

候选工具 A（不含 B）：
{candidates}

判断原则
========

1. 边的核心定义

A -> B 成立，当 A、B 在工具契约所支持的典型真实任务中会自然地连续执行并共同产生有效进展，
中间不需要其他工具或被省略的必要业务步骤。参数、对象或判断依据的承接只用于帮助判断两次调用是否
自然连续；权重只由连续调用在真实工作流中的结构作用决定。仅能放进同一任务不足以形成边。

2. 证据边界

只能依据环境信息和两个工具的具体契约判断，不能凭名称、领域常识或未声明的实体关系补全故事。

3. 局部与方向

边有方向且不具传递性，只判断当前 A -> B。A -> B 与 B -> A 分别判断；A -> B 和 B -> C
成立，不能据此推导 A -> C。

weight
======

weight 只表示边在工具链中的结构作用；采样概率和 prerequisite 独立表达：

- 3：同一条实际工作线的连续推进。A 完成后留下的具体进展由 B 的实际执行直接承接，
  使同一个局部目标继续向完成收敛。连续的 Level 3 边构成工具链的主要骨架，并使其具有深度。
- 2：不同子任务之间的明确衔接。A 和 B 推进不同的局部目标，但把 B 放在 A 后面对于
  组织同一个整体任务具有稳定、清楚的意义。Level 2 边把多个子任务组成连贯的整体。
- 1：任务层面的合理关联。A 和 B 直接相邻能够推进一项自然任务，但连贯性主要来自
  整体任务目标；这条边既不延续同一条实际工作线，也不构成稳定的子任务衔接。Level 1
  边用于探索和增加任务多样性。
- 0：A 和 B 无法形成由公开契约支持、能够推进任务的直接相邻执行。

reason 必须说明公开契约支持的相邻执行关系及其结构等级；weight=0 也必须说明缺少什么
关系，因此不连边。

prerequisite
============

prerequisite 不表示 A -> B 是否是一条好边，而表示 B 能否成功执行的工具历史条件。
只记录必须由更早的工具执行建立、不能由自然任务输入提供的条件；如果所有方案都不满足，
B 就不能成功执行。它与 weight 分别判断，不能从 weight=3 推导，也不能反向改变 weight。

prerequisite_alternatives 整体是 any_of，其中任意一个方案满足即可；每个方案的 all_of
必须全部满足。没有这种历史条件时返回空数组。prerequisite 只能引用 B 的正边。

必须按候选顺序恰好判断每个 A 一次。只返回以下 JSON object：

{{
  "decisions":[
    {{"from_tool":"候选工具名","reason":"对相邻链和任务目标的具体作用","weight":3}}
  ],
  "prerequisite_alternatives":[
    {{"all_of":["候选工具名"],"reason":"不可缺少的工具历史依据"}}
  ]
}}

每项只能包含示例中的字段，不输出其他内容。
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


def _validate_decisions(
    target_name: str,
    payload: dict[str, Any],
    tool_names: set[str] | list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate one target's complete edge and prerequisite decision."""
    if not isinstance(payload, dict) or set(payload) != {
        "decisions", "prerequisite_alternatives"
    }:
        raise ValueError(
            f"目标 {target_name} 的建图结果必须只含 decisions 和 prerequisite_alternatives"
        )
    raw_decisions = payload["decisions"]
    alternatives = payload["prerequisite_alternatives"]
    if not isinstance(raw_decisions, list) or not isinstance(alternatives, list):
        raise ValueError(f"目标 {target_name} 的建图数组字段非法")

    expected_order = sorted(tool_names) if isinstance(tool_names, set) else list(tool_names)
    expected_order = [name for name in expected_order if name != target_name]
    expected = set(expected_order)
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
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{label}.reason 必须是非空字符串")
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
