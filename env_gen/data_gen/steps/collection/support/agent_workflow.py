"""准备 Step 2 控制入口，并生成首次和续轮的采集 Prompt。"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from ...common.constants import (
    COLLECTION_GUIDE_FILE,
    CONTROL_ASSESSMENT,
    CONTROL_COLLECTION_LAUNCHER,
    CONTROL_DATA_FILE_RECEIPTS,
    CONTROL_DIRECTORY,
    CONTROL_DOWNLOAD_ATTEMPTS,
    CONTROL_DOWNLOAD_RECEIPTS,
    CONTROL_FINALIZATION,
    CONTROL_ROUND_FEEDBACK,
    CONTROL_ROUND_HISTORY,
    CONTROL_RUN_CONFIG,
    CONTROL_SCENARIO_RESEARCH_RECEIPT,
    CONTROL_SOURCE_PLAN_RECEIPT,
    CONTROL_WORKSPACE_CHECKPOINT,
    SCENARIO_RESEARCH_PATH,
    SOURCE_PLAN_PATH,
)
from ...common.control_io import atomic_write_text, control_path, read_json, write_json
from ...common.workspace_files import file_sha256
from ...step1_research_scenario import read_saved_scenario_research


def _collection_guide(source_plan_schema: str) -> str:
    return f"""# Step 2 深度调研与真实数据采集

## 目标

以 `provenance/scenario_research.json` 的场景简报为起点，继续调查真实来源，用实际文档、API 响应、
批量数据和领域文件验证其中的实体、关系、操作和任务假设。将有用来源保存为 Raw，将稳定业务
对象整理成 Entity，并使最终数据足以支持高价值的查询、筛选、比较、统计、跨实体关联，以及
Seed 确实需要的文件处理任务。

开始时完整读取：

1. `.datagen/selected_seed.json`；
2. `provenance/scenario_research.json`；
3. 本文件；
4. `{source_plan_schema}`。

## A. 深度调研和来源计划

优先核实预调研给出的官方或维护者候选来源，再调查字段说明、分页机制、总量、许可、更新频率、
关联端点和领域文件结构。预调研不是白名单：覆盖不足、URL 失效或发现更权威数据时，可以继续
搜索并登记新来源。

进入 workspace 的业务记录必须来自已下载的真实公开来源，并能由直接来源复算。文档可以证明
字段形状和协议语义，但不能作为凭据虚构账户私有记录、运行历史、策略、投放或交易数据；这类
场景假设应记录为 rejected/not_applicable/unavailable，并把实际可公开支持的部分准确收口。

在临时 `source_plan.json` 中记录当前结论：

- `deep_research_summary`：实际调查后对领域和数据可得性的总结；
- `research_refinements`：对潜在实体、关系、操作、任务、数据需求或文件格式的
  confirmed/revised/rejected/new 结论，每项绑定 `evidence_source_ids`；
- `sources`：用 `url` 标识来源，用 `registered_urls` 列出准备实际请求的每个精确 URL，并记录
  覆盖需求、目标实体、获取方式和当前状态；分页产生新 URL 时先更新计划再请求；
- `data_need_coverage`：逐项跟踪场景研究中数据需求的事实证据；
- `scenario_source_lead_id=null` 表示 Step 2 新发现的来源，`discovery_note` 说明发现依据；
- 实际证据改变数据模式或文件依赖时，用 `research_deviation_note` 说明原因。

每次更新正式计划都执行：

```bash
bash ./.datagen/datagenctl save-source-plan --input .datagen/drafts/source_plan.json
```

## B. 下载 Raw

来源必须先进入已保存的 source plan。先做最小真实请求验证内容，再按分页、分层抽样或批量文件
继续采集。每个不同响应保存为新的 Raw 路径：

```bash
bash ./.datagen/datagenctl download \
  --source-id <source_id> \
  --url '<exact_url>' \
  --output raw/<path> \
  --format json
```

多个已登记 URL 可写入 manifest 后使用 `download-batch`。下载命令负责 URL 绑定、预算、重试、
内容检查、去重和收据。分页结束、报告总量取完、代表性样本完成、仓库枚举完成或真实访问失败，
都要准确反映到 source plan 的状态及证据中。

## C. 业务数据层

- `workspace/raw/` 保存来源原貌和真实领域文件；评估成功后只能追加，不能覆盖旧证据。
- `workspace/entities/` 保存稳定业务表，支持 JSON、JSONL、CSV 和 Parquet。每个实体应有稳定 ID、
  足够的非技术字段和可闭合外键。
- `workspace/derived/` 只在后续工具确实需要时保存可由 Raw/Entity 复算的 `extract`、`convert` 或
  `aggregate` 结果。它不是每个环境的必交付目录。

Entity 和 Derived 必须通过受控命令加入并声明直接来源：

```bash
bash ./.datagen/datagenctl add-entity \
  --input /tmp/item.json --output entities/item.json --source raw/items.json

bash ./.datagen/datagenctl add-derived \
  --input /tmp/layout.xml --output derived/layout.xml \
  --derivation extract --source raw/archive.zip
```

当真实领域文件参与任务时，保留文件本体，并建立含 `file_path` 和业务元数据的 Entity 索引；
文件是否需要由 Seed 和深度调研决定。

## D. 评估和补充

执行 `bash ./.datagen/datagenctl assess`。程序会重算文件清单、收据、实体字段、关系闭合、Seed
需求覆盖和必要文件，并把结果写入 `.datagen/assessment.json`。按 `blocking_issues` 修错误，按
`next_actions` 补充数据。候选操作和组合数量只用于观察潜力，不是 rich 门槛。

## E. 收口

所有来源都要落到 complete、blocked 或 unavailable，所有需求都要得到事实支持或准确说明公开
数据限制。然后执行：

```bash
bash ./.datagen/datagenctl finalize --result complete
# 或 exhausted / insufficient_public_data
```

生成 `.datagen/finalization.json` 和 `provenance/data_checkpoint.json` 后，本阶段完成。
"""


def prepare_collection(run_dir: Path) -> None:
    """在 Step 1 现场上安装 Step 2 Guide 和受控命令。"""

    run_dir = run_dir.resolve()
    for relative in (
        "workspace/raw",
        "workspace/entities",
        "workspace/derived",
        "workspace/reports",
    ):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    read_saved_scenario_research(run_dir)
    project_root = Path(__file__).resolve().parents[5]
    executable = shlex.quote(sys.executable)
    python_path = shlex.quote(str(project_root))
    module = shlex.quote(
        "from env_gen.data_gen.steps.collection.commands.datagenctl import _main; _main()"
    )
    launcher = control_path(run_dir, CONTROL_COLLECTION_LAUNCHER)
    atomic_write_text(
        launcher,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"export PYTHONPATH={python_path}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        f"exec {executable} -c {module} --run-dir {shlex.quote(str(run_dir))} \"$@\"\n",
    )
    os.chmod(launcher, 0o755)
    atomic_write_text(
        control_path(run_dir, COLLECTION_GUIDE_FILE),
        _collection_guide(str(config["source_plan_schema_path"])),
    )


def build_collection_prompt(run_dir: Path) -> str:
    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    return f"""[DATAGEN_STEP=DEEP_RESEARCH_AND_COLLECTION]

为环境 `{config['seed_global_id']}` 继续深度调研并采集真实公开数据。先完整读取
`.datagen/COLLECTION_GUIDE.md`、`provenance/scenario_research.json` 和 `.datagen/selected_seed.json`。

预调研是起点而不是终点：验证并扩展其中的实体、关系、操作、任务和来源假设，把结论记录到
source plan 的 `deep_research_summary` 与 `research_refinements`；随后完成“保存来源计划 -> 探测
并下载 Raw -> 整理 Entity/必要 Derived -> assess -> 补充或修复 -> finalize”的循环。正式写入均
使用 `bash ./.datagen/datagenctl`。现在开始执行。
"""


def build_collection_continuation_prompt(
    run_dir: Path,
    *,
    round_index: int,
    final_round: bool = False,
) -> str:
    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    closing = (
        "这是最后一轮：集中解决已有 blocking_issues，并准确收口来源和需求后立即 finalize。"
        if final_round
        else "继续验证高价值缺口；新发现必须记录为 research_refinements 并绑定来源证据。"
    )
    return f"""[DATAGEN_STEP=COLLECTION_CONTINUATION]

这是环境 `{config['seed_global_id']}` 的第 {round_index} 轮 Step 2 会话。保留上一轮已经取得的
Raw、Entity、Derived 和来源调查结论，不重复下载相同 URL。

先读取 `.datagen/round_feedback.json`、`.datagen/assessment.json`、现有
`provenance/source_plan.json` 和 `provenance/scenario_research.json`。按 `blocking_issues` 与
`next_actions` 继续深度调研、下载、整理和评估。对终态失败 URL 使用调查得到的官方替代来源；
可重试失败最多按反馈再尝试一次。{closing}

本轮必须执行实际命令。完成时通过 `bash ./.datagen/datagenctl finalize --result
complete|exhausted|insufficient_public_data` 交付。
"""


def remove_collection_controls(run_dir: Path) -> None:
    """发布前归档程序控制记录和 Agent 日志，然后删除临时控制目录。"""

    run_dir = run_dir.resolve()
    control_root = run_dir / CONTROL_DIRECTORY
    provenance = run_dir / "provenance"
    provenance.mkdir(parents=True, exist_ok=True)
    records: dict[str, Any] = {}
    for name in (
        CONTROL_SCENARIO_RESEARCH_RECEIPT,
        CONTROL_SOURCE_PLAN_RECEIPT,
        CONTROL_DOWNLOAD_RECEIPTS,
        CONTROL_DOWNLOAD_ATTEMPTS,
        CONTROL_DATA_FILE_RECEIPTS,
        CONTROL_ROUND_HISTORY,
        CONTROL_ROUND_FEEDBACK,
        CONTROL_ASSESSMENT,
        CONTROL_WORKSPACE_CHECKPOINT,
        CONTROL_FINALIZATION,
    ):
        path = control_path(run_dir, name)
        if not path.is_file():
            continue
        try:
            records[name] = read_json(path, f"控制记录 {name}")
        except RuntimeError as error:
            records[name] = {"read_error": str(error)}

    source_logs = control_root / "agent_runs"
    target_logs = provenance / "agent_runs"
    log_manifest: list[dict[str, Any]] = []
    if source_logs.is_dir():
        shutil.copytree(source_logs, target_logs, dirs_exist_ok=True)
        for path in sorted(target_logs.rglob("*")):
            if path.is_file():
                log_manifest.append(
                    {
                        "path": path.relative_to(provenance).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": file_sha256(path),
                    }
                )
    write_json(
        provenance / "collection_audit.json",
        {
            "schema_version": "2.0",
            "control_records": records,
            "agent_log_files": log_manifest,
        },
    )
    shutil.rmtree(control_root, ignore_errors=True)


__all__ = [
    "build_collection_continuation_prompt",
    "build_collection_prompt",
    "prepare_collection",
    "remove_collection_controls",
]
