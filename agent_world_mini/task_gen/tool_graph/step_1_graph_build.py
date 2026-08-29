"""Step 1：从环境的公开工具定义构建有证据的直接前置关系图。

本文件只回答一个问题：对任意两个不同工具 ``A`` 和 ``B``，为了完成一个
有意义的 ``B`` 调用，是否应先调用 ``A``。若成立，输出 ``A → B``、
依赖强度和理由。本阶段不生成工具链，也不证明某条链一定可执行。

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

只新增 ``tool_graph`` 一个键。工具节点已存在于 ``environment.tools``，
不在图中重复保存，因此输出只有边。

.. code-block:: text

    {
      tool_graph : [                      [有向带权边列表。顺序稳定：
                                           weight 降序 → from_tool 字典序 →
                                           to_tool 字典序，便于重跑比较]
        {
          from_tool : str                 [前置工具名。必为 environment.tools
                                           中已知的名字，且 != to_tool。
                                           → Step 2 据此做带权随机游走]
          to_tool   : str                 [目标工具名。由本阶段按当前目标填入，
                                           不采信 LLM 返回的同名字段]
          weight    : 1 | 2 | 3           [依赖强度，**不是** LLM 置信度。
                                           图中只出现 1/2/3。LLM 也必须对无依赖的
                                           候选显式返回 weight=0（用于审查完整性
                                           门禁），但那不成为边。
                                           → Step 2 用作转移概率（3:2:1）
                                             和链 score 的累加项]
          reason    : str                 [非空。判定该边的必填依据，
                                           自然语言。→ Step 2 的 LLM 修订会读它]
        }, ...
      ]
    }

对输出的唯一结构保证：无自环、无重复 ``(from_tool, to_tool)``。

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

本阶段拆成六个各自可独立测试的环节，``build_graph`` 只做编排，不内联任何一环的
逻辑。除 :func:`_request_dependencies` 外全部是确定性纯函数，可在无 LLM 的情况下
单测：

===========================  ====================  ==============================
环节                          纯函数                 职责
===========================  ====================  ==============================
:func:`_compact_tool_view`   是                    单个工具 → 紧凑公开视图
:func:`_environment_context`  是                    环境级公开上下文（含 rules）
:func:`_build_prompt`         是                    目标 + 候选 + 上下文 → prompt
:func:`_request_dependencies` 否（唯一调 LLM）      一个目标 → 已解析的原始候选边
:func:`_validate_edges`       是                    校验并归一化单个目标的边
:func:`_assemble_graph`       是                    全目标截断、去重、排序、自查
===========================  ====================  ==============================

数据在环节间的形态固定为：

.. code-block:: text

    environment.tools
      → _compact_tool_view 逐个映射     → list[CompactTool]
      → _build_prompt(目标, 候选, 环境)  → str
      → _request_dependencies           → list[dict]（LLM 原样字段，未校验）
      → _validate_edges(目标, 原始)      → list[Edge]（已补 to_tool，已校验）
      → _assemble_graph(全部目标结果)    → list[Edge]（已截断、已排序）

``_validate_edges`` 是信任边界：在它之前的数据一律视为不可信 LLM 输出，
在它之后的数据保证工具名有效、``weight`` 合法、``reason`` 非空、字段形状正确。
``_assemble_graph`` 只处理已校验的边，不再重复做单条边的字段校验。

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

``A → B`` 必须有一条具体的 ``reason``，说明 ``A`` 产生的参数或状态如何被
``B`` 直接使用。仅字段同名、类型相同、工具主题相似，都不构成依赖。
例如列表结果中的 ``severity`` 与更新工具的 ``severity`` 同名，不代表
必须先调列表工具。

依据只用自然语言的 ``reason`` 承载，不再有结构化证据字段。上一轮实践表明
``parameter_evidence`` / ``state_evidence`` 无人消费，却要求 LLM 严格返回空数组、
本地再逐项校验形状，成本大于收益。

``weight`` 表示依赖强度，不是 LLM 对自己答案的置信度：

* ``3``：强依赖。没有 ``A`` 产生的参数或状态，``B`` 在该任务中无法成立。
* ``2``：条件依赖。在明确的有效任务场景中需要 ``A``，但 ``B`` 也可在其他场景独立调用。
* ``1``：辅助依赖。``A`` 的产物会被 ``B`` 直接消费并能改善任务，但不是成功前提。
* ``0``：**已审查，判定无依赖**。这是有效输出而非错误。

``weight=0`` 的作用是**完整性检查**：prompt 要求模型逐一审查全部候选，因此对
没有关系的候选也应显式回答 0，而不是省略。据此可以判断模型是否漏审了工具 ——
省略和"看过后否定"在只接受 1/2/3 的设计下无法区分。

处理方式：``weight=0`` 的项计入审查完整性统计，但**不进入 tool_graph**
（图只保存真实存在的边），也不要求 ``reason`` 非空。

**完整性是硬门禁。** 每个目标必须对其全部候选（工具总数 - 1）都明确表态；
漏审任何一个即报错，由 ``_target_edges`` 判定。理由是"看过后否定"和"根本没看"
在输出里无法区分，而后者意味着该目标的判定不完整、整张图不可信。报错会触发
单目标重试，重试后仍漏审则整阶段失败。

实测踩过的坑：曾经只接受 1/2/3，结果模型对 ``list_test_runs`` 返回
``weight: 0`` 表达"Level 0 无关系"，导致整阶段失败；更麻烦的是这类语义误解是
**确定性**的，单目标重试必然重现同样的结果，重试只能救偶发的格式抖动。

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

   LLM 必须返回 ``{"dependencies": [...]}``；每项只包含 ``from_tool``、``weight``
   和 ``reason``。``to_tool`` 由当前目标确定，不让 LLM 重复返回。
   无前置关系时返回 ``{"dependencies": []}``。
3a. （``_request_dependencies``）本函数是整个阶段唯一接触 LLM 的地方。它用
   :func:`tool_graph.llm.infer` 发送 prompt，用
   :func:`tool_graph.llm.parse_json_object` 解析回复，不自行剥离 ``` 围栏或
   做括号匹配，并只做一件与结构有关的事：确认顶层存在 ``dependencies`` 且是
   list，然后原样返回其元素。**不在此校验元素内容**，那是 ``_validate_edges``
   的职责，两者不得合并。

   ``MalformedJSONError`` 按下方失败行为处理；其消息会区分"模型没按格式回答"
   和"输出被截断"，后者说明 ``llm.max_tokens`` 对本环境的工具数偏小。
4. （``_validate_edges``）用本地确定性代码校验每一项：``from_tool`` 必须是
   ``environment.tools`` 中的已知工具名且不等于当前目标（自环在此就地丢弃，
   不留到装配阶段）、``weight`` 必须是 0/1/2/3 之一（``True`` 不算 1）。
   ``weight=0`` 计入审查覆盖率后跳过，不要求 ``reason``；``weight`` 为 1/2/3 时
   ``reason`` 必须是非空字符串。``to_tool`` 由本函数按当前目标填入，不采信 LLM
   可能自带的同名字段。不得直接信任 LLM 输出。
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
* ``_request_dependencies``：``MalformedJSONError``、顶层缺 ``dependencies``
  或它不是 list 时，抛出**包含目标工具名**的异常。绝不能把解析失败当成
  "该目标没有前置边"静默返回空列表 —— 那会让一次格式错误伪装成一个真实的
  图结构结论。
* ``_validate_edges``：未知工具名、非法 ``weight``（0/1/2/3 之外）、
  weight 为 1/2/3 但 ``reason`` 为空时，
  抛出同时包含目标工具名和出错项的异常。
* ``_target_edges``：该目标漏审任何候选时抛出 ``ValueError``，消息给出
  已审查数/应审查数和漏审的工具名。
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

Step 1 完成时：每个工具都已作为目标被判定一次；所有边都经过
``_validate_edges``，工具名、``weight`` 和非空 ``reason`` 均已本地校验；
**每个目标都对其全部候选明确表过态**，审查完整性门禁通过；
图中无自环和重复边，但允许有向环；输出顺序稳定，且不包含 ``internal``
或任何工具实现细节。

本阶段不保证图具备任何拓扑性质（零入度工具存在、无环、连通等）。
"""

from __future__ import annotations

import json
from typing import Any

from .contracts import BuildGraphInput, BuildGraphOutput
from .llm import BatchInferenceError, InferenceResult, MalformedJSONError, infer, parse_json_object

# 0 是有效输出：表示模型已审查该候选并判定无依赖。prompt 要求对每个候选都给出
# 结论，weight=0 就是"已审查、无关系"的显式回答，据此可确认模型没有漏审工具。
# 它不进入 tool_graph —— 图只保存真实存在的边。
WEIGHTS = (0, 1, 2, 3)
MAX_OUT_DEPTH = 3


def build_graph(stage_input: BuildGraphInput) -> BuildGraphOutput:
    """编排六个环节，返回只含 ``tool_graph`` 的输出。

    自身不含建图逻辑：构造紧凑视图和环境上下文，按 ``environment.tools`` 顺序
    为每个目标构造 prompt 并取回原始候选边，逐目标校验，最后统一装配。
    """
    environment = stage_input["environment"]
    config = stage_input["config"]

    tools = environment["tools"]
    views = [_compact_tool_view(tool) for tool in tools]
    names = [view["name"] for view in views]
    context = _environment_context(environment)

    # per-target selection：每个工具轮流做 to_tool，其余全部工具作为候选。
    # 批量提交由 llm.max_concurrency 控制并发，但构造与消费顺序都按
    # environment.tools 的原始顺序，不依赖返回时序。
    prompts = [
        _build_prompt(view, [other for other in views if other["name"] != view["name"]], context)
        for view in views
    ]
    try:
        results: list[InferenceResult | Exception] = list(infer(prompts, llm_config=config.llm))
    except BatchInferenceError as error:
        results = list(error.outcomes)
    if len(results) != len(views):
        raise ValueError("建图 LLM 返回数量与目标工具数量不一致")

    edges_by_target: dict[str, list[dict[str, Any]]] = {}
    for index, (name, result) in enumerate(zip(names, results)):
        try:
            if isinstance(result, Exception):
                raise result
            edges = _target_edges(result, name, set(names))
        except Exception:
            # 单目标原子重试：一次格式错误不该让整阶段作废，因为本流水线没有
            # 断点续跑，重来意味着其余目标的调用全部白费。只重发这一个 prompt，
            # 第二次仍失败才整阶段报错。
            # 单字符串 prompt，infer 返回单个 InferenceResult（不是列表）。
            # 捕获 Exception 而非仅 ValueError：回复畸形时可能先撞上类型错误，
            # 那同样是"这个目标的结果不可用"，应统一收敛成带目标名的报错。
            try:
                retry = infer(prompts[index], llm_config=config.llm)
                edges = _target_edges(retry, name, set(names))
            except Exception as error:
                raise ValueError(f"目标工具 {name} 的建图结果非法：{error}") from error
        edges_by_target[name] = edges

    return {"tool_graph": _assemble_graph(edges_by_target, names)}


def _target_edges(
    result: InferenceResult,
    target_name: str,
    tool_names: set[str],
) -> list[dict[str, Any]]:
    """把一次 LLM 回复变成该目标的已校验边，并强制审查完整性。

    完整性是**硬门禁**：prompt 要求逐一审查全部候选，因此每个候选都必须被明确
    表态 —— 有依赖给 1/2/3，无依赖给 0。漏掉任何候选即报错，因为"看过后否定"和
    "根本没看"无法区分，而后者意味着该目标的判定不完整，整张图也就不可信。

    报错会触发 ``build_graph`` 的单目标重试；重试后仍漏审则整阶段失败。
    """
    edges, reviewed = _validate_edges(
        target_name, _request_dependencies(result, target_name), tool_names
    )
    missing = sorted(tool_names - {target_name} - reviewed)
    if missing:
        expected = len(tool_names) - 1
        raise ValueError(
            f"目标 {target_name} 只审查了 {len(reviewed)}/{expected} 个候选，"
            f"漏审 {len(missing)} 个：{', '.join(missing)}"
        )
    return edges


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
    """组装单个目标的 prompt；``candidates`` 已排除目标自身。

    包含依赖语义说明、字段同名不构成依赖的反例，以及固定输出结构。
    """
    return PROMPT_TEMPLATE.format(
        environment=json.dumps(context, ensure_ascii=False, indent=2),
        target=json.dumps(target, ensure_ascii=False, indent=2),
        candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
        target_name=target["name"],
    )


PROMPT_TEMPLATE = """\
你在分析一个工具环境的**直接前置依赖**。

环境上下文（含资源与业务规则）：
{environment}

目标工具（本次要判定它的前置工具）：
{target}

候选工具（不含目标自身）：
{candidates}

任务：从候选中找出为了完成一次**有意义的** `{target_name}` 调用，必须或应当
先调用的工具。

判定标准
- 必须存在具体依据：候选工具产生的**参数值**或**资源状态**被 `{target_name}`
  直接使用。工具视图中 `out` 是输出字段路径，`in` 是参数名与类型。
- 反例：仅字段同名、类型相同或主题相似**都不构成依赖**。例如某个列表工具的
  输出里有 `severity`，而目标的参数也叫 `severity`，这不代表必须先调列表工具 ——
  该值可以由用户直接给出。
- 只要直接前置。若 A 是 B 的前置、B 是本目标的前置，而 A 对本目标没有独立的
  直接依据，就不要列出 A。
- 只列出真正必需的前置工具，不要为了凑数而列。

weight 表示**依赖强度**，不是你对答案的置信度：

- **3 必要的数据或状态交接**：目标需要一个具体参数或状态，该候选的输出或状态能
  直接提供它，且从当前公开环境看它是获得该参数的必要来源。例如目标必须使用某个
  动态 ID，而该候选返回这个 ID。
  不能仅因为字段类型相同、名称相似，或两者处理同一资源，就判为 3。
- **2 任务块之间的工作流转移**：环境中的工具能明确组成几个不同的工作块，该候选
  所在的块完成后，目标所在的块是合理的下一阶段。不要求目标使用它的具体字段，
  但必须有明确的业务工作流关系。并列子任务之间可以双向成立；不能仅因为属于同一
  主题就判为 2。
- **1 可能的语义来源或弱关联**：该候选功能明确、目标需求明确，且存在至少一个合乎
  逻辑的调用场景，在该场景中它的返回结果可能直接满足目标的某个输入，或明显影响
  目标的参数选择、范围或验证方式 —— 但没有足够证据证明它是必要前置或固定的任务块
  转移。不要求它每次都返回目标值。
  只有能说明具体组合场景和影响方式时才给出，不要把所有主题相近、
  字段相似或理论上无法排除的组合都列为 1。

不建立边的情形（Level 0）：
- 只有类型兼容或字段名称相似；
- 只有共同读取同一资源，或属于同一业务主题；
- 该工具的具体结果对目标工具无影响。

判断顺序：对每个候选依次问 —— 是否是必要的数据或状态交接（3）？是否是明确的
任务块工作流转移（2）？是否存在具体、合理但非必要的语义来源（1）？否则不建边。

只返回如下 JSON object，不要输出其他内容。每一个工具都要进行评判，不建边对应weight=0

{{"dependencies": [
  {{"from_tool": "候选工具名",
    "weight": 0-3,
    "reason": "具体说明该工具和目标工具的关联，以及为什么属于该等级"
  }}
]}}

每项只包含 from_tool、weight、reason 三个字段，不要添加其他字段。
"""


def _request_dependencies(result: InferenceResult, target_name: str) -> list[dict[str, Any]]:
    """只取出 ``dependencies`` 列表元素，不校验元素内容。

    这是本阶段唯一消费 LLM 输出的地方。只做与结构有关的一件事：确认顶层存在
    ``dependencies`` 且是 list，然后原样返回其元素；元素内容交给
    ``_validate_edges``。两者不合并，因为"模型没按格式回答"和"模型给了越界
    结论"需要不同的处置（前者调 max_tokens 或改 prompt，后者查 prompt 语义）。

    绝不能把解析失败当成"该目标没有前置边"返回空列表 —— 那会让一次格式错误
    伪装成一个真实的图结构结论。
    """
    try:
        payload = parse_json_object(result.text)
    except MalformedJSONError as error:
        raise ValueError(f"目标 {target_name} 的 LLM 回复无法解析：{error}") from error
    if "dependencies" not in payload:
        raise ValueError(f"目标 {target_name} 的 LLM 回复缺少 dependencies 字段")
    raw_edges = payload["dependencies"]
    if not isinstance(raw_edges, list):
        raise ValueError(
            f"目标 {target_name} 的 dependencies 必须是数组，实际是 {type(raw_edges).__name__}"
        )
    return raw_edges


def _validate_edges(
    target_name: str,
    raw_edges: list[dict[str, Any]],
    tool_names: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    """校验并归一化单个目标的边，填入 ``to_tool``；本阶段的信任边界。

    在本函数之前的数据一律视为不可信 LLM 输出；之后的数据保证工具名有效、
    ``weight`` 合法、``reason`` 非空。因此 ``_assemble_graph`` 不再重复单条边的
    字段校验。

    返回 ``(边列表, 已审查候选名集合)``。后者包含全部被明确表态的候选，
    含 ``weight=0`` 的"已审查、无依赖"，用于完整性检查。
    """
    edges: list[dict[str, Any]] = []
    reviewed: set[str] = set()
    for index, raw in enumerate(raw_edges):
        label = f"目标 {target_name} 的 dependencies[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{label} 必须是 object，实际是 {type(raw).__name__}")

        from_tool = raw.get("from_tool")
        if from_tool not in tool_names:
            raise ValueError(f"{label}.from_tool 引用未知工具：{from_tool!r}")
        # 自环就地丢弃，不留到装配阶段：目标自己不可能是自己的前置。
        if from_tool == target_name:
            continue
        reviewed.add(from_tool)

        weight = raw.get("weight")
        if isinstance(weight, bool) or weight not in WEIGHTS:
            raise ValueError(f"{label}.weight 必须是 0/1/2/3 之一，实际是 {weight!r}")
        # weight=0 是有效输出，表示"已审查该候选，判定无依赖"。它是完整性信号，
        # 不是错误：prompt 要求模型对每个候选都给出结论，据此才能确认没有漏审。
        # 已计入 reviewed，但不进入 tool_graph —— 图只保存真实存在的边。
        if weight == 0:
            continue

        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{label}.reason 必须是非空字符串")

        edges.append(
            {
                "from_tool": from_tool,
                # to_tool 由本地按当前目标填入，不采信 LLM 可能自带的同名字段。
                "to_tool": target_name,
                "weight": weight,
                "reason": reason.strip(),
            }
        )
    return edges, reviewed


def _assemble_graph(
    edges_by_target: dict[str, list[dict[str, Any]]],
    tool_names: list[str],
) -> list[dict[str, Any]]:
    """去重并稳定排序；这是装配的全部职责。

    只接受已通过 ``_validate_edges`` 的边，不再重复单条边的字段校验。
    不限制入度、不检查零入度工具、不检查环、不做传递闭包消减 —— 本阶段对图的
    拓扑性质不作任何要求，LLM 判出多少边就输出多少边。
    """
    # 同一 (from_tool, to_tool) 只保留一条，保留首次出现的那条。
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for target in tool_names:
        for edge in edges_by_target.get(target) or []:
            unique.setdefault((edge["from_tool"], edge["to_tool"]), edge)

    # 稳定排序：weight 降序 → from_tool → to_tool，便于重跑 diff。
    return sorted(
        unique.values(),
        key=lambda edge: (-edge["weight"], edge["from_tool"], edge["to_tool"]),
    )
