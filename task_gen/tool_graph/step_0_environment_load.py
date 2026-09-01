"""Step 0：读取环境包，并检查生成任务所需的文件是否完整。

输入 Schema（只列本阶段实际检查的字段）
=======================================

下面是 Step 0 会看的**全部**内容。环境包里的其他字段（``schema_version``、
``format``、``data_type``、``source_resources``、``entity_schema``、
``rules``、``provenance/``、``quality_report.json``，以及 validation.json 除
``status`` 外的一切）本阶段一律不看、不校验。方括号里是检查规则。

.. code-block:: text

    environment.json : {                  [文件存在、UTF-8、JSON 可解析、顶层是 object]

      environment_id : str                [非空字符串；小写 snake_case，即
                                           ^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$；
                                           与 task.schema.json 的 pattern 一致，
                                           在此提前校验以免 Step 5 才失败。
                                           不设长度上限 —— 实测 30/32/36 字符]

      tools : [                           [必须是非空数组]
        {
          name         : str              [非空；小写 snake_case（同上 pattern）；
                                           跨条目唯一。实测长度 9–37]
          description  : str              [非空字符串。实测长度 18–60，
                                           是 Step 1 判定依赖的主要语义来源]
          inputSchema  : object           [非空 object；内容不校验，
                                           由 Step 3 用它验参数]
          outputSchema : object           [非空 object，且必须含 oneOf ——
                                           Step 3 依赖成功分支判定
                                           "Schema 通过且 success is True"]
          internal : {
            code : str                    [非空字符串；只查存在与非空，
                                           不检查内容、可导入性或死分支]
          }
        }, ...
      ]

      resources : [                       [必须是非空数组]
        {
          resource_id  : str              [非空；小写 snake_case（同上 pattern）；
                                           跨条目唯一。实测长度 6–19]
          storage_type : str              [必须是 "file" | "file_collection"
                                           | "directory" 之一]
          path         : str              [非空；相对路径，不含 ..；
                                           resolve() 后不得逃出 workspace。
                                           形如 raw/junit/*.xml、exports/]
          writable     : bool             [必须是真正的 bool。字符串 "false"
                                           非空且能过存在性检查，但在 Step 4
                                           会被当成真值，导致只读资源被允许修改]
        }, ...
      ]
    }

    validation.json : {                   [文件存在、UTF-8、JSON 可解析、顶层是 object]
      status : str                        [必须严格等于 "passed"。
                                           不接受 "PASSED" 或其他真值。
                                           这个文件只有这一条规则]
    }

    workspace/ :                          [必须存在且是目录]
      整棵树                               [不得含任何符号链接（包括未被任何
                                           资源声明覆盖的），因为 Step 3 复制
                                           整个 workspace]
      每个 resource 的 path 指向的位置       [file           → 必须是普通文件，目录不算通过
                                           file_collection → glob 至少匹配一个普通文件
                                           directory      → 目录存在即可，允许为空]

输出 Schema
===========

Step 0 只输出一个键，值是 ``environment.json`` 的**原样解析结果**：不裁剪、
不加工、不做公开投影、不输出 workspace 路径。因此输出的字段集恒等于源文件的
字段集，**包含 Step 0 自己并不检查的字段** —— 它们由下游阶段消费。

这是本阶段与其他阶段的关键区别，也是职责划分的核心：Step 0 是唯一原样透传
整个 environment 的阶段；``internal`` 的裁剪由 Step 1、2、4 各自做公开投影时
完成，Step 0 不替它们做。

方括号标注**谁消费该字段**，据此判断本阶段该不该动它：

.. code-block:: text

    {
      environment : {

        schema_version : str              [无人消费。Step 0 不校验，下游也不读；
                                           原样保留。注意 Step 5 组装的
                                           task.schema_version 是 task 契约自己的
                                           硬编码常量 "1.0"，与此字段无关]
        environment_id : str              [Step 5 填入 task.environment_id。
                                           Step 0 已校验格式]
        name           : str              [Step 1/2/4 放进 LLM 的环境上下文]
        description    : str              [Step 1/2/4 放进 LLM 的环境上下文]

        resources : [
          {
            resource_id      : str        [Step 4 生成三类 resource_constraints；
                                           Step 5 校验约束并检查 task_text 未
                                           泄漏该串。Step 0 已校验格式与唯一性]
            name             : str        [Step 1/2/4 的 LLM 上下文]
            description      : str        [Step 1/2/4 的 LLM 上下文]
            data_type        : str        [无人消费；原样保留]
            storage_type     : str        [Step 5 按它判定 initial/final 的变更
                                           归属：file 精确匹配、file_collection
                                           按 glob、directory 匹配整棵子树。
                                           Step 0 已校验取值域]
            path             : str        [Step 5 同上做变更归属。
                                           Step 0 已校验相对性与越界]
            format           : str        [无人消费。可能是 "mixed"（实测
                                           bugagent 的 parser_references），
                                           因此不代表 collection 内格式一致]
            writable         : bool       [Step 4 据此禁止只读资源进入
                                           should_modify/can_modify；
                                           Step 5 据此判定资源变更约束。
                                           Step 0 已校验是真 bool]
            source_resources : [str]      [可选字段，derived/entity 类资源才有
                                           （bugagent 实测 4/7）。无人消费]
            entity_schema    : object     [可选字段，只有 data_type=="entity"
                                           的资源有（实测 bugagent 1 个、
                                           finstat 4 个、happyscribe 1 个）。
                                           无人消费；注意它通不过
                                           environment.schema.json]
          }, ...
        ]

        rules : [                         [Step 1 必须放进 prompt —— 跨资源的
                                           业务规则常是状态依赖的唯一线索；
                                           Step 2/4 也用作 LLM 上下文。
                                           Step 0 完全不校验 rules]
          {
            description : str
            resources   : [str]           [引用 resource_id；Step 0 不校验
                                           这些引用是否都存在]
          }, ...
        ]

        tools : [
          {
            name         : str            [Step 1 的图节点；Step 2 校验 chain
                                           工具名；Step 3 按名取工具执行；
                                           Step 5 填 available_tools 并检查
                                           task_text 未泄漏该串。
                                           Step 0 已校验格式与唯一性]
            description  : str            [Step 1 判定依赖的主要语义来源
                                           （schema 占 payload 97%，语义几乎
                                           全在这里）；Step 2/3/4 的上下文]
            inputSchema  : object         [Step 1 投影出参数名/类型/enum；
                                           Step 3 校验 LLM 生成的 arguments；
                                           Step 5 复校 reference.tool_calls]
            outputSchema : object         [Step 1 投影出输出字段路径；
                                           Step 3 校验工具返回值。
                                           Step 0 已校验含 oneOf]
            internal : {
              code : str                  [**只有 Step 3** 消费：在子进程中
                                           加载并执行 run(arguments, context)。
                                           Step 1/2/4 必须先移除它再送 LLM。
                                           Step 0 只查存在与非空]
            }
          }, ...
        ]
      }
    }

Step 0 **不输出**的东西（下游需要时自行取得）：

* ``initial_state`` 或任何 workspace 路径 —— Step 3 自己从
  ``config.environment_dir / "workspace"`` 复制。
* 公开投影 —— Step 1、2、4 各自构造。
* workspace 文件清单或字节快照 —— Step 5 的 check 4 自己重新遍历源目录。

实现依据
========

实现前必须实际查看下面三个正式参考环境，不能只根据测试夹具或旧 TaskGen 猜测
环境包结构：

* ``artifacts/mcp_test3/bugagent``
* ``artifacts/mcp_test3/finstat``
* ``artifacts/mcp_test3/happyscribe``

每个环境包的有效输入只有：

``environment.json``
    完整环境定义，包含环境信息、``resources``、``rules`` 和 ``tools``。

``validation.json``
    上游环境生成流程的验证结果。Step 0 只以顶层
    ``status == "passed"`` 作为准入条件；不依赖 ``schema_valid``、
    ``resource_valid``、质量评分或逐工具测试字段。

``workspace/``
    真实初始文件树。它可能包含普通文件、glob 文件集合、空输出目录和二进制文件，
    具体形态以三个正式参考环境为准，不能假设所有资源都是 JSON。

三个参考环境的实测形态
======================

实现和测试时按下表预期，不要按"资源都是 JSON 文件"来写代码：

===========  =====  ================================================  ===========
环境          资源   storage_type 分布                                  workspace
===========  =====  ================================================  ===========
bugagent      7     file 3、file_collection 4、directory 0             18 文件 230 KB
finstat       15    file 11、file_collection 1、**directory 3**        19 文件 22 KB
happyscribe   7     file 3、file_collection 2、**directory 2**         9 文件 1.4 MB
===========  =====  ================================================  ===========

必须按真实情况处理的几点：

* **空目录资源真实存在。** finstat 的 ``exports/``、``reports/`` 和 happyscribe 的
  ``exports/``、``briefs/`` 都是 ``writable=true`` 的空目录，是给工具写产物用的
  输出位置。第 4 项的"允许为空"不是理论兜底，而是三个环境里 5 个资源的常态。
* **空目录不被 git 跟踪，且 ``artifacts/`` 整体在 ``.gitignore`` 中。** 环境包是
  本地产物而非仓库内容，因此不能假设它在任何机器上都存在或形态一致；Step 0 的
  存在性检查是真检查，不是形式主义。这也是 Step 3 复制 workspace 时必须保留空
  目录的原因（用 ``copytree`` 而不是只遍历文件）。
* **二进制与非 JSON 文件真实存在。** happyscribe 的 ``whisper_jfk.flac`` 是 1.1 MB
  FLAC（占该环境 workspace 体积的 78%），bugagent 有 XML 和 SARIF。一律按字节
  处理，不要尝试解码或解析。
* **``file_collection`` 的 glob 可以跨格式。** bugagent 的 ``parser_references``
  用 ``derived/reference/*`` 同时匹配 ``.xml`` 和 ``.sarif``，其 ``format`` 字段
  写作 ``mixed``。不要假设一个 collection 内格式一致。
* **``writable`` 分布很不均。** bugagent 只有 1 个可写资源（7 个中），
  finstat 有 6 个，happyscribe 有 3 个。Step 4 和 Step 5 都依赖该字段，
  因此第 3c 项要求它必须是真正的 bool，缺失或字符串 ``"false"`` 都算非法。

输入与输出
==========

输入 ``EnvironmentLoadInput`` 只包含完整 ``config``，Step 0 从
``config.environment_dir`` 读取上述环境包。

输出 ``EnvironmentLoadOutput`` 只新增：

``environment``
    ``environment.json`` 解析后的完整 dict，原样保留 ``tools[].internal``，供后续
    建图、真实执行和最终校验使用。

Step 0 不输出 ``initial_state`` 或 workspace 路径。后续 Step 3 直接从
``config.environment_dir / "workspace"`` 复制内容，并在 run_io 创建的本次
``run_dir`` 中为每个任务创建：

``run_dir/tasks/<task_id>/initial/``
    工具执行前的完整 workspace 副本，创建后保持不变。

``run_dir/tasks/<task_id>/final/``
    从 ``initial/`` 复制得到，并作为工具链的实际执行目录；执行结束时它自然就是
    该任务的最终 workspace。

内部分工
========

本阶段没有 LLM，全部是确定性的字段与文件系统检查，因此只需两个函数：

``load_environment``
    编排：读两个 JSON、调用合规性检查、把结果包成 Output。

:func:`check_environment_compliance`
    环境合规性检查。三个被检查对象（``environment.json``、``validation.json``、
    ``workspace/``）的全部规则集中在这里，按分区顺序执行。

检查的性质
==========

主体是字段存在性，但有几处不是，且恰好是最能抓到真问题的部分，实现时不要
统一退化成"字段在不在"：

* **值相等**：``validation.json`` 的 ``status`` 必须严格等于 ``"passed"``。
  字段存在但值为 ``"failed"`` 必须拒绝。这个文件只有这一条规则。
* **跨条目唯一性**：``resource_id`` 与工具 ``name`` 各自唯一。单看任何一条都
  合法，重复只在放到一起时才暴露。
* **类型与取值域**：``writable`` 必须是真正的 bool，``storage_type`` 必须是三个
  枚举值之一。``writable`` 尤其重要 —— 字符串 ``"false"`` 是存在且非空的，
  能通过存在性检查，但在 Step 4 会被当成真值，导致只读资源被允许修改。
* **要求不存在**：workspace 内不得有符号链接。
* **越界检查**：资源路径必须非绝对、不含 ``..``、``resolve()`` 后仍在 workspace
  之内。
* **"存在"的判定标准按 storage_type 不同**：``file`` 必须是普通文件（目录不算
  通过），``file_collection`` 要求 glob 至少匹配一个普通文件，``directory``
  只要目录存在、**允许为空**。

执行顺序
========

只有一处是硬要求：**符号链接检查必须早于逐资源路径解析**，否则前者要防的
"访问环境包之外的数据"已经在后者解析路径时发生了。其余按"便宜的先做、能定位
根因的先做"排列，即读文件 → 准入 → 清单字段 → workspace。

清单字段部分（分区 2）**一次收集全部问题再统一报错**，不遇到第一个缺字段就抛。
环境包由上游批量生成，问题往往成片出现，逐个报错会让人反复重跑。

必须完成的检查
==============

以下每条标注所属分区（见 :func:`check_environment_compliance` 内的分区注释）。

1. （分区 0 / ``load_environment``）``environment.json``、``validation.json``
   必须存在、可解析，且 JSON 顶层是 object；``workspace/`` 必须存在且是目录。
   文件不存在、不是 UTF-8、JSON 语法错误和顶层不是 object 要给出可区分的错误
   信息，因为这四种情况对应完全不同的上游问题。
2. （分区 1）``validation.json.status`` 必须严格等于 ``"passed"``，
   否则拒绝环境。严格相等，不接受 ``"PASSED"`` 或其他真值。
3. （分区 2）``environment.resources`` 和 ``environment.tools``
   必须是非空数组；``resource_id`` 和工具 ``name`` 分别必须唯一。
3a. 顶层必须存在非空字符串 ``environment_id``（Step 5 组装正式 task 时必须填写，
   缺失要到最后一步才暴露）。
3b. 每个工具必须同时具备非空 ``name``、``description``、``inputSchema``、
   ``outputSchema``，以及 ``internal.code`` 非空字符串。``inputSchema`` 与
   ``outputSchema`` 必须是 object；``outputSchema`` 必须含 ``oneOf``，因为
   Step 3 依赖“Schema 通过 **且** ``success is True``”两道独立门禁。
3c. 每个资源必须具备 ``resource_id``、``storage_type``、``path``，
   ``storage_type`` 只能是 ``file``、``file_collection``、``directory``，
   且必须具备 bool 类型的 ``writable``（Step 4 依据它裁剪
   ``should_modify``/``can_modify``，Step 5 依据它判定资源变更约束）。
   ``writable`` 必须是真正的 bool，字符串 ``"false"`` 或缺失都算非法 —— 后两者
   在 Step 4 会被当成真值，导致只读资源被允许修改。

   3–3c 只做字段存在性、类型和唯一性检查，成本极低，但能把“环境缺字段”从
   Step 3、Step 4 或 Step 5 提前到 Step 0。否则要等到 Step 1 和 Step 2 的全量
   LLM 调用花完之后才失败，而本流水线没有断点续跑。
4. （分区 4）对每项资源只检查路径完整性：

   * ``storage_type=file``：对应**普通文件**存在（目录不算通过）；
   * ``storage_type=file_collection``：glob 至少匹配一个普通文件；
   * ``storage_type=directory``：对应目录存在，**允许为空**。

   glob 只按 ``path`` 字面展开，不递归推断，也不校验 ``format`` 字段与实际
   扩展名是否一致（``parser_references`` 的 ``format`` 就是 ``mixed``）。
5. （分区 3 与分区 4）资源路径必须是 workspace 内的相对路径，不能包含 ``..``，
   解析后不能逃出 workspace；workspace 中的符号链接一律拒绝，避免完整性检查
   或 Step 3 复制时访问环境包之外的数据。

   这是两件不同的事，两个分区都要有：分区 4 的路径解析防的是**清单里写了**
   越界路径（``../../etc/passwd``、绝对路径），按声明逐个资源检查；
   分区 3 防的是**磁盘上存在**指向外部的链接，需要遍历整个 workspace，
   而不只是被资源声明覆盖的路径 —— 因为 Step 3 复制的是整个 workspace，
   未被任何资源声明的符号链接同样会被复制。

   解析必须同时防住"路径本身合法但中间某段是符号链接"的情况：先做字面检查
   （非绝对、无 ``..``），再用 ``Path.resolve()`` 确认结果仍在 workspace 的
   ``resolve()`` 结果之下。三个参考环境实测均无符号链接，因此这条检查平时
   不会触发，实现时要自己构造夹具验证它真的生效。
6. （全部环节）报错信息应指出缺失或非法的文件、字段、``resource_id`` 和资源
   路径；任一检查失败立即抛出 ``ValueError``，不得返回部分环境继续流水线。

明确不做的事情
==============

* 全部检查都是本地手写的存在性、类型和唯一性判断，不用 JSON Schema 校验环境。
  这不是纪律偏好，而是因为参考环境**通不过** ``environment.schema.json``：实测
  bugagent 57 条、finstat 81 条、happyscribe 63 条错误，全部来自
  ``entity_schema.<entity>.fields.<field>``（schema 期望字符串，环境实际写的是
  ``{"type": ..., "description": ...}`` object）。另外
  ``complete_environment.schema.json`` 用 ``$ref`` 指向
  ``https://agent-world-mini.local/...``，在 jsonschema 3.2.0 下会尝试发起网络
  请求，必须自行配置 ``RefResolver`` 才能使用。
  因此本阶段只检查后续步骤真正依赖的那些字段，不追求环境满足自身 schema。
* 不解析资源文件内容，不检查文件格式、实体 Schema、数据质量或业务规则；这些是
  上游环境生成与验证的职责。
* 不检查 ``internal.code`` 的内容、可导入性或是否存在无用分支。参考环境中同一
  环境的全部工具携带完全相同的 ``_dispatch`` 体，且其中含有指向不存在路径的
  孤儿分支；这些属于上游产物特征，由 Step 3 的信任边界处理，Step 0 只确认
  ``internal.code`` 字段存在且非空。
* 不读取 ``provenance/``、``quality_report.json`` 或同级其他环境；只处理
  ``config.environment_dir`` 明确指向的一个环境包。
* 不复制 workspace，不创建任务 ID、任务目录、``initial/`` 或 ``final/``。
* 不分析工具关系、不调用 LLM、不执行工具、不生成候选链或任务文本。
* 不修改环境定义或源 workspace，不把任何产物写回 ``config.environment_dir``。

完成条件
========

Step 0 成功返回时，只能说明：指定环境已由上游标记为 passed；环境清单具备后续
阶段必需的基本字段（含 ``environment_id``、工具四字段与 ``internal.code``、
资源的 ``storage_type`` 与 bool ``writable``）；workspace 内不存在符号链接；
三个参考环境所体现的 file、file_collection 和 directory 资源均能在源 workspace
中找到。它不重新证明环境满足 Schema 或业务语义。

返回的 ``environment`` 是原样解析结果，**保留 ``tools[].internal``**。这是
本阶段与其他阶段的重要区别：Step 1、2、4 都必须自己做公开投影，Step 0 不替
它们裁剪，因为 Step 3 需要 ``internal.code`` 来真实执行。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .contracts import EnvironmentLoadInput, EnvironmentLoadOutput


STORAGE_TYPES = ("file", "file_collection", "directory")
TOOL_FIELDS = ("name", "description", "inputSchema", "outputSchema")

# 与 task.schema.json 中 environment_id 和 available_tools[].name 的 pattern 一致。
# 在此提前校验，否则格式问题要到 Step 5 才暴露，而那时全部 LLM 开销已经花完。
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def load_environment(stage_input: EnvironmentLoadInput) -> EnvironmentLoadOutput:
    """读取环境包、做合规性检查，并返回完整 environment dict。

    只使用 ``config.environment_dir``；不读 workspace 内容，不复制任何文件。
    """
    environment_dir = stage_input["config"].environment_dir

    # ==================== 分区 0：读入三个被检查对象 ====================
    environment = _read_json_object(environment_dir / "environment.json")
    validation = _read_json_object(environment_dir / "validation.json")
    workspace = environment_dir / "workspace"
    if not workspace.is_dir():
        raise ValueError(f"workspace 不存在或不是目录：{workspace}")

    check_environment_compliance(environment, validation, workspace)
    return {"environment": environment}


def _read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 文件并确认顶层是 object；四种失败给出可区分的消息。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"环境包缺少必需文件：{path}") from None
    except UnicodeDecodeError as error:
        raise ValueError(f"文件不是合法 UTF-8：{path}（{error}）") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"JSON 解析失败：{path}（{error}）") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是 object，实际是 {type(value).__name__}：{path}")
    return value


def check_environment_compliance(
    environment: dict[str, Any],
    validation: dict[str, Any],
    workspace: Path,
) -> None:
    """环境合规性检查：三个被检查对象的全部规则集中在此，按分区顺序执行。

    分区 1 与 2 是纯内存检查，分区 3 与 4 访问文件系统。分区 3 必须早于分区 4，
    理由见模块文档"执行顺序"。任一分区失败都抛出 ``ValueError``，不返回部分结果。
    """

    # ==================== 分区 1：validation.json 准入 ====================
    # 唯一的值相等检查。严格等于 "passed"，不接受 "PASSED" 或其他真值。
    status = validation.get("status")
    if status != "passed":
        raise ValueError(f"环境未通过上游验证：validation.status={status!r}，要求 'passed'")

    # ==================== 分区 2：environment.json 字段 ====================
    # 存在性、类型、取值域和跨条目唯一性。一次收集全部问题再统一报错，
    # 因为环境包由上游批量生成，问题往往成片出现。
    problems: list[str] = []

    environment_id = environment.get("environment_id")
    if not isinstance(environment_id, str) or not environment_id.strip():
        problems.append(f"environment_id 必须是非空字符串，实际是 {environment_id!r}")
    elif not IDENTIFIER.match(environment_id):
        problems.append(f"environment_id 必须是小写 snake_case：{environment_id!r}")

    tools = environment.get("tools")
    resources = environment.get("resources")
    if not isinstance(tools, list) or not tools:
        problems.append("tools 必须是非空数组")
        tools = []
    if not isinstance(resources, list) or not resources:
        problems.append("resources 必须是非空数组")
        resources = []

    problems.extend(_tool_problems(tools))
    problems.extend(_resource_problems(resources))
    problems.extend(_duplicate_problems(tools, "tools", "name"))
    problems.extend(_duplicate_problems(resources, "resources", "resource_id"))

    if problems:
        listed = "\n".join(f"  - {item}" for item in problems)
        raise ValueError(f"环境清单不合格，共 {len(problems)} 项：\n{listed}")

    # ==================== 分区 3：workspace 符号链接 ====================
    # 要求"不存在"。必须遍历整棵树而非仅资源覆盖的路径，因为 Step 3 复制整个
    # workspace；且必须早于分区 4，否则解析资源路径时就已经跟随链接读到外部。
    #
    # 先查 workspace 目录自身：它是符号链接时，rglob 仍能正常遍历链接目标的内容，
    # 于是整条检查被静默绕过 —— Step 0 会去环境包之外做完整性检查，Step 3 也会从
    # 外部复制 workspace。这正是本检查要防的事，因此根必须单独判。
    if workspace.is_symlink():
        raise ValueError(f"workspace 自身不允许是符号链接：{workspace} -> {workspace.resolve()}")
    links = sorted(str(item.relative_to(workspace)) for item in workspace.rglob("*") if item.is_symlink())
    if links:
        raise ValueError(f"workspace 内不允许符号链接，发现 {len(links)} 个：{', '.join(links)}")

    # ==================== 分区 4：workspace 资源路径 ====================
    # 先做越界检查，再按 storage_type 判定"存在"。三种 storage_type 的判定标准
    # 各不相同，不能统一成"路径存在"。
    for resource in resources:
        resource_id = resource["resource_id"]
        target = _resolve_inside(workspace, resource["path"], resource_id)
        storage_type = resource["storage_type"]

        if storage_type == "file":
            # 目录不算通过。
            if not target.is_file():
                raise ValueError(f"资源 {resource_id} 的文件不存在：{resource['path']}")
        elif storage_type == "file_collection":
            # 按 path 字面展开 glob，至少匹配一个普通文件；不递归推断，
            # 也不校验 format 与实际扩展名一致（parser_references 的 format 是 mixed）。
            matches = [item for item in workspace.glob(resource["path"]) if item.is_file()]
            if not matches:
                raise ValueError(f"资源 {resource_id} 的 glob 未匹配任何文件：{resource['path']}")
        else:
            # directory：存在即可，允许为空。三个参考环境共有 5 个空目录资源，
            # 它们是 writable=true 的输出位置，为空是常态。
            if not target.is_dir():
                raise ValueError(f"资源 {resource_id} 的目录不存在：{resource['path']}")


def _tool_problems(tools: list[Any]) -> list[str]:
    """每个工具的必备公开字段与 ``internal.code``。"""
    problems: list[str] = []
    for index, tool in enumerate(tools):
        label = f"tools[{index}]"
        if not isinstance(tool, dict):
            problems.append(f"{label} 必须是 object")
            continue
        label = f"tools[{index}]({tool.get('name', '?')})"
        for field in TOOL_FIELDS:
            value = tool.get(field)
            if field in ("name", "description"):
                if not isinstance(value, str) or not value.strip():
                    problems.append(f"{label}.{field} 必须是非空字符串")
                elif field == "name" and not IDENTIFIER.match(value):
                    problems.append(f"{label}.name 必须是小写 snake_case：{value!r}")
            elif not isinstance(value, dict) or not value:
                problems.append(f"{label}.{field} 必须是非空 object")
        # Step 3 依赖 oneOf 的成功分支来判定"Schema 通过且 success is True"。
        output_schema = tool.get("outputSchema")
        if isinstance(output_schema, dict) and "oneOf" not in output_schema:
            problems.append(f"{label}.outputSchema 必须含 oneOf")
        # Step 3 在子进程中加载 internal.code 真实执行；此处只查存在与非空。
        code = (tool.get("internal") or {}).get("code") if isinstance(tool.get("internal"), dict) else None
        if not isinstance(code, str) or not code.strip():
            problems.append(f"{label}.internal.code 必须是非空字符串")
    return problems


def _resource_problems(resources: list[Any]) -> list[str]:
    """每个资源的必备字段、``storage_type`` 取值域和 bool ``writable``。"""
    problems: list[str] = []
    for index, resource in enumerate(resources):
        label = f"resources[{index}]"
        if not isinstance(resource, dict):
            problems.append(f"{label} 必须是 object")
            continue
        label = f"resources[{index}]({resource.get('resource_id', '?')})"
        for field in ("resource_id", "path"):
            value = resource.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{label}.{field} 必须是非空字符串")
            elif field == "resource_id" and not IDENTIFIER.match(value):
                problems.append(f"{label}.resource_id 必须是小写 snake_case：{value!r}")
        if resource.get("storage_type") not in STORAGE_TYPES:
            problems.append(
                f"{label}.storage_type 必须是 {STORAGE_TYPES} 之一，"
                f"实际是 {resource.get('storage_type')!r}"
            )
        # 必须是真正的 bool：字符串 "false" 存在且非空，能过存在性检查，
        # 但在 Step 4 会被当成真值，导致只读资源被允许修改。
        if not isinstance(resource.get("writable"), bool):
            problems.append(
                f"{label}.writable 必须是 bool，实际是 {resource.get('writable')!r}"
            )
    return problems


def _duplicate_problems(items: list[Any], label: str, key: str) -> list[str]:
    """跨条目唯一性：单看任何一条都合法，重复只在放到一起时暴露。"""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if not isinstance(value, str):
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [f"{label}.{key} 重复：{name}" for name in sorted(duplicates)]


def _resolve_inside(workspace: Path, resource_path: str, resource_id: str) -> Path:
    """越界检查：拒绝绝对路径、``..``，以及 resolve 后逃出 workspace 的路径。

    先做字面检查，再比对 ``resolve()`` 结果，以防"路径字面合法但中间某段是
    符号链接"。分区 3 已排除 workspace 内的符号链接，这里是第二道防线。
    """
    candidate = Path(resource_path)
    if candidate.is_absolute():
        raise ValueError(f"资源 {resource_id} 的 path 必须是相对路径：{resource_path}")
    if ".." in candidate.parts:
        raise ValueError(f"资源 {resource_id} 的 path 不允许包含 ..：{resource_path}")
    root = workspace.resolve()
    target = (root / candidate).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"资源 {resource_id} 的 path 解析后逃出 workspace：{resource_path}")
    return target
