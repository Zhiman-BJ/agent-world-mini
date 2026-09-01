"""准备 Step 3 集成现场和 Prompt。"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from ..common.constants import (
    CONTROL_INTEGRATION_LAUNCHER,
    CONTROL_RUN_CONFIG,
    INTEGRATION_GUIDE_FILE,
)
from ..common.control_io import atomic_write_text, control_path, read_json


def _guide(integration_schema: str, environment_contract: str | None) -> str:
    contract = environment_contract or "schemas/环境契约-v2.0.md"
    return f"""# Step 3 建模、深采与集成

## 目标

根据 Step 2 的真实来源画像建立环境模型，对选中来源定向扩大采集，并把结果物化为
`state/records.sqlite` 与 `state/filesystem_scopes/`。本阶段必须反复执行“计划、物化、画像、修正”，
直到环境连贯且可复现。若来源和数据需求已经全部收口，但公开数据客观上达不到丰富度门槛，
可以以 `exhausted` 收口并发布为 `not_rich`；这不是把质量问题伪装成成功，而是保留真实且可用的
有限环境。

先读取：

1. `provenance/scenario_research.json`；
2. `provenance/source_plan.json` 与 `source_inventory.json`；
3. 集成计划 Schema：`{integration_schema}`；
4. 环境协议：`{contract}`；
5. 本文件。

## 集成计划

计划声明最终 Record Set、Relationship、Filesystem Scope、需求绑定和来源决策。模型必须从实际
Raw 长出来，不要把每个来源机械变成一张表，也不要把文件逐个建模成对象。

- 同一业务概念的兼容来源合并为一个 Record Set；
- 多对多使用关联 Record Set；
- 文件和目录保留为 Scope，Record 只保存必要的 Scope 相对路径；
- 一个 Scope 表示一个工具可以理解和操作的工作区，不是所有 Raw 的汇总目录。不要把多个来源、
  多种职责的文件按文件名平铺进 `source_materials` 一类 catch-all Scope；按操作上下文拆分，或保留
  原项目的真实目录层级；
- 目录 Scope 的 `layout` 必须用 file/file_collection 覆盖全部普通文件；同类文件用 glob，
  不要只描述核心格式却把其他内容静默暴露给工具；
- 同一个 Raw 文件只能物化到一个最终 Scope；多个 Record Set 可以共同引用该 Scope，但不要复制
  同一文件到多个工具工作区来绕过固定 scope_id 引用；
- 最终资产不能依赖 main/master/branch 形式的可变 Git 仓库下载；先登记同一 commit SHA 的精确
  raw/archive URL，并通过受控下载取得或复用相同内容。网页/API 则保留为时间戳快照；
- 用于诊断或修复的损坏文件也必须进入 Scope：文件节点声明
  `content_validation=allow_invalid`，并用 Record 的状态/结果字段准确描述；不要为了通过解析校验而只暴露正常文件；
- 每个核心资产绑定至少一个 Step 1 数据需求；
- `importance=core` 表示环境的业务主轴，必须服务 Step 1 中 `priority=core` 的需求；权威但只服务
  supporting 需求的注册表、词典和背景材料仍应标为 supporting，不能靠大表记录量抬高核心丰富度；
- 每个 `priority=core` 的需求都要有至少一个自身足够有深度的 core Record Set 或 Scope；不能让一个
  混合 Scope 同时替所有核心需求过门，也不能把稀疏业务表降级为 supporting 来绕过检查；
- supporting Record Set 的记录不能用于补足核心记录总量；核心字段必须在实际 SQLite 中达到填充度
  门槛，不能靠声明但全为空的列满足字段数量；
- 需求使用 `realized/partial/unavailable/not_applicable` 准确闭环：前两者必须绑定真实资产，
  后两者不得绑定假数据，并在 description 中说明证据；
- `priority=core` 的需求若准确收口为 unavailable，环境仍可作为 `not_rich/exhausted` 发布，但不能
  判为 rich；不要通过改写绑定状态绕过这个事实；
- 真正独立的资产填写 `standalone_reason`，不能用它掩盖错误外键或无关数据；
- 每个资产声明唯一 `transformation_id` 和真实 Raw `source_paths`。
- 关系只有在实际存在至少一条非空命中时才形成资产连接；文件引用也必须至少解析一个真实 Scope
  路径。全空 nullable 字段不会让画像通过；没有实际引用时删除声明或补充真实数据。
- `directory_collection` 的丰富度按同构项目目录数判断，不按所有项目内部文件的总数判断；需要项目
  变化时应补充真实项目成员，不能向两个项目中堆更多文件来满足门槛。
- Record Set、Scope 和需求绑定的 `description` 只写语义、用途与边界，不写“6 个样本”或
  “25 条记录”等当前数量；数量由程序画像从实际状态计算，避免补采后说明过期。
- 来源计划中的 `required_file_formats` 只代表最终 Filesystem Scope 必须出现、并且任务侧会直接
  操作的领域文件格式。网页、API JSON、HTML 文档或 CSV 如果只是抽取 Record Set 或解释语义，
  应列入 `evidence_file_formats`，不要为了格式齐全强行物化为任务侧文件；候选格式不等于最终要求。
- `evidence_only/rejected` 来源只能留在 provenance，不能进入任何最终 Record Set 或 Scope；若某份文件
  对任务侧确实有用，应将来源决策改为 supporting 并说明其服务的数据需求。

保存：

```bash
bash ./.datagen/integratectl save-plan --input .datagen/drafts/integration_plan.json
```

## Record Set 转换

为每个 Record Set 建立独立的 Python 转换包，例如
`.datagen/drafts/<transformation_id>/main.py`。共享解析函数放在同一目录的模块中；不要依赖其他
Record Set 的转换目录。入口脚本接口固定为：

```text
python script.py --run-dir <run_dir> --asset-id <record_set_id> --output <json_path>
```

输出必须是 `{{"<record_set_id>": [record, ...]}}`。脚本只能读取 run_dir 内 Raw 和已保存协议，
不能读取已有 `state/`，不得联网、不得写其他位置、不得使用时间或随机数。控制器会在无网络、
主机只读、隐藏候选状态且只有独立输出目录可写的 bubblewrap 中运行两次并比较输出哈希：

```bash
bash ./.datagen/integratectl build-record-set \\
  --record-set-id <id> \\
  --package-dir .datagen/drafts/<transformation_id> \\
  --script .datagen/drafts/<transformation_id>/main.py
```

控制器先把包内全部 Python 文件安装到 `provenance/transformations/<transformation_id>/`，再从
正式包执行和重放；因此辅助模块也会被保存、校验和独立重放。

## Filesystem Scope

`materialization=copy` 保留单个/多个真实文件；`extract` 安全解包归档：

```bash
bash ./.datagen/integratectl build-scope --scope-id <id>
```

文件节点默认 `content_validation=strict`。只有真实任务需要检查、修复或比较初始无效内容时才使用
`allow_invalid`；它不会取消扩展名/格式家族检查，画像会列出实际解析失败的文件。

## 画像、语义复核与补充

执行 `bash ./.datagen/integratectl assess`。程序检查实际表、关系、文件路径、连通分量、多源整合和
需求绑定，并在 `integration_profile.asset_profile.record_sets[].fields` 中给出有界的字段频次、
样本、空值和形态事实。逐项阅读 `asset_profile.field_review.findings`，回到其中不同位置的 Raw
核对完整记录。提示只是需要复核的信号，不等于程序已经证明数据错误：真实偏斜可以保留；若发现
解析错列、分支覆盖或值域污染，修转换后只重建受影响的 Record Set。来源定义闭合值域时，在字段
定义中声明 `enum`，让后续物化直接拒绝越界值。

如果存在提示，创建 `.datagen/drafts/field_review.json`：

```json
{{
  "schema_version": "1.0",
  "findings": [
    {{
      "finding_id": "<integration_profile 中的 finding_id>",
      "decision": "verified_against_raw",
      "reason": "说明核对的 Raw 结构，以及当前偏斜或重叠为何符合来源事实。",
      "evidence_paths": ["raw/<该 Record Set 的真实 source_path>"]
    }}
  ]
}}
```

只允许在确认结果正确时保存：

```bash
bash ./.datagen/integratectl save-field-review --input .datagen/drafts/field_review.json
```

若核对后发现错误，不要写 `verified_against_raw`，应先修复转换并重新物化。复核收据绑定当前计划和
画像哈希；任何计划、数据或字段分布变化都会使它失效。没有提示时不需要创建该文件。

按 next_actions 选择最小修复：修改计划、重建受影响资产，或定向补采缺失来源记录。不要只看
`integration_tier` 和记录数量就收口；finalize 前必须完成上述字段语义抽查。
补采前更新 source plan 并登记 URL；补采后重建所有受影响资产。

集成画像为 integrated 后执行 `bash ./.datagen/integratectl finalize`。画像为 `rich` 时结果为
`ready`；画像为 `not_rich` 且所有来源和数据需求已经收口时结果为 `exhausted`。
"""


def prepare_integration(run_dir: Path) -> None:
    run_dir = run_dir.resolve()
    for relative in ("state", "provenance/transformations", ".datagen/drafts"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    project_root = Path(__file__).resolve().parents[4]
    executable = shlex.quote(sys.executable)
    python_path = shlex.quote(str(project_root))
    module = shlex.quote(
        "from env_gen.data_gen.steps.integration.integratectl import _main; _main()"
    )
    launcher = control_path(run_dir, CONTROL_INTEGRATION_LAUNCHER)
    atomic_write_text(
        launcher,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"export PYTHONPATH={python_path}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        f"exec {executable} -c {module} --run-dir {shlex.quote(str(run_dir))} \"$@\"\n",
    )
    os.chmod(launcher, 0o755)
    atomic_write_text(
        control_path(run_dir, INTEGRATION_GUIDE_FILE),
        _guide(
            str(config["integration_plan_schema_path"]),
            str(config.get("contract_path")) if config.get("contract_path") else None,
        ),
    )


def build_integration_prompt(run_dir: Path) -> str:
    config = read_json(control_path(run_dir.resolve(), CONTROL_RUN_CONFIG), "运行配置")
    return f"""[DATAGEN_STEP=INTEGRATE_ENVIRONMENT_DATA]

为 `{config['seed_global_id']}` 建立最终数据模型并集成真实数据。完整读取
`.datagen/INTEGRATION_GUIDE.md`、来源画像、场景研究和 v2.0 环境协议。

先基于实际样本保存 integration plan，再按计划定向补采、编写确定性转换、物化 Record Set 与
Filesystem Scope，并运行 assess。根据集成画像修正模型或数据；达到 `ready` 或合法
`exhausted` 后 finalize。
不要把每个来源机械建成独立表，不要用 standalone_reason 掩盖数据割裂。现在执行实际命令。
"""


def build_integration_continuation_prompt(
    run_dir: Path, *, round_index: int, final_round: bool,
) -> str:
    config = read_json(control_path(run_dir.resolve(), CONTROL_RUN_CONFIG), "运行配置")
    ending = (
        "这是可用总预算内的最后一轮。不要再扩展环境主题或补充可选来源；先运行 assess，"
        "只修复 blocking_issues 和完成字段复核。达到 rich 时立即以 ready finalize；来源与需求"
        "均已收口但仍不 rich 时，准确以 exhausted finalize。"
        if final_round else
        "只重建受影响资产；缺数据时定向补采，不重做已经闭合的部分。"
    )
    return f"""[DATAGEN_STEP=INTEGRATION_CONTINUATION]

这是 `{config['seed_global_id']}` 的第 {round_index} 轮集成。读取现有 integration plan、
integration profile、`.datagen/integration_assessment.json` 和物化收据，按 blocking_issues 与
next_actions 继续。{ending} 所有正式操作使用 `bash ./.datagen/integratectl`。
"""


__all__ = [
    "build_integration_continuation_prompt",
    "build_integration_prompt",
    "prepare_integration",
]
