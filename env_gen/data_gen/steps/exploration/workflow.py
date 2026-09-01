"""准备 Step 2 来源探索现场和 Prompt。"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from ..common.constants import (
    CONTROL_EXPLORATION_LAUNCHER,
    CONTROL_RUN_CONFIG,
    EXPLORATION_GUIDE_FILE,
)
from ..common.control_io import atomic_write_text, control_path, read_json
from ..step1_research_scenario import read_saved_scenario_research


def _guide(source_plan_schema: str) -> str:
    return f"""# 当前任务：查清来源并取得可验证样本

你没有任何先前对话背景。本次任务的结果是一个已验证的来源计划和一组带下载收据的原始样本。
这些结果必须足以回答：每项核心数据需求从哪里取得、实际响应长什么样、可取得到什么程度。

## 必须读取的输入

1. `.datagen/selected_seed.json`：完整 Seed，参考工具和任务是必须覆盖的能力目标。
2. `provenance/scenario_research.json`：上一阶段提出的业务场景、数据需求和来源线索；其中候选项
   仍是假设，必须用本阶段调查修正。
3. `{source_plan_schema}`：`source_plan.json` 的字段与枚举要求。
4. `.datagen/EXPLORATION_GUIDE.md`：本任务说明。续轮还要读取已有
   `provenance/source_plan.json`、`provenance/source_inventory.json` 和
   `.datagen/exploration_assessment.json`。

## 本任务中的术语

- **来源（source）**：一个职责明确、可独立判断可用性的 API、数据包、仓库、文件集合或文档入口。
- **Raw**：通过 `explorectl download` 原样保存并记录 URL、时间和 SHA-256 的来源响应。Raw 是后续
  转换输入，不是已经整理好的业务对象。
- **代表性样本**：覆盖真实结构差异的最小样本，例如不同分页、状态、时间、地区、文件格式或
  有效/无效分支；不能只下载最容易成功的一条。
- **complete**：已取得可解析样本，且本轮所需的结构分支已经有证据；**blocked**：入口存在但受
  认证、权限、限流或本地预算阻塞；**unavailable**：精确入口不存在、不可达或没有可用数据。

## 执行要求

1. 从每个参考工具、参考任务和 `data_needs` 建立调查清单。检查它们所需的业务记录、可变状态和
   领域文件是否都有来源路径；不能因为多个工具属于同一主题就只调查其中一个。
2. 核实 `source_leads`，并继续搜索遗漏的官方端点、批量文件、固定版本仓库、标准样例和可信
   替代来源。Step 1 的 URL 是起点，不是白名单。
3. 先把准备访问的精确 URL 写入 `.datagen/drafts/source_plan.json` 的 `registered_urls`，保存计划，
   再使用受控下载命令。下载后更新同一计划中的 `raw_files`、数量、访问状态和完成证据并再次保存。
4. 对小型完整数据直接收集全量；对大型来源收集覆盖结构分支的样本。可能进入最终文件工作区的
   Git 内容必须固定到 commit SHA；分支 URL 只能用于发现。
5. 在 `research_refinements` 中记录与 Step 1 相比被确认、修正、淘汰或新发现的实体、关系、操作、
   任务、数据需求和文件格式。`deep_research_summary` 要说明实际查到了什么，而不是复述计划。
6. `required_file_formats` 只列任务会直接解析或修改的领域文件；仅用于抽取记录或证明语义的
   JSON、CSV、HTML、文档格式列入 `evidence_file_formats`。两者没有内容时写空数组。
7. 对取得方式如实记录：匿名可取用 `access_status=public`；需要当前运行环境未提供的合法凭据时
   记录 `authentication_required` 并收口为 blocked；不要绕过认证。若某项能力只能由确定性 fixture
   表示，在 refinement 和需求评估中明确写出依据与限制，不能把 fixture 声称为下载到的真实记录。
8. 每轮保存计划后运行 `assess`，逐条处理 `blocking_issues` 和 `next_actions`。所有来源必须收口为
   complete、blocked 或 unavailable，且每个 Raw 文件恰好归属一个来源。

## 可用命令

```bash
bash ./.datagen/explorectl save-source-plan --input .datagen/drafts/source_plan.json
bash ./.datagen/explorectl download --source-id <id> --url '<url>' --output raw/<path> --format <format>
bash ./.datagen/explorectl download-batch --manifest .datagen/drafts/download_manifest.json
bash ./.datagen/explorectl assess
bash ./.datagen/explorectl finalize --result ready
```

## 完成条件

- 每项核心数据需求都有可用样本，或有绑定精确入口的失败证据和明确结论；
- 每个参考工具/任务的数据条件都被实际来源覆盖，或被明确记录为缺口；
- `assess` 返回 `decision=ready`；
- 执行 `finalize --result ready` 成功后停止。

只有所有核心来源都经过真实探测且没有任何可用核心样本时，才使用
`finalize --result insufficient_public_data`。
"""


def prepare_exploration(run_dir: Path) -> None:
    run_dir = run_dir.resolve()
    (run_dir / "workspace/raw").mkdir(parents=True, exist_ok=True)
    read_saved_scenario_research(run_dir)
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    project_root = Path(__file__).resolve().parents[4]
    executable = shlex.quote(sys.executable)
    python_path = shlex.quote(str(project_root))
    module = shlex.quote(
        "from env_gen.data_gen.steps.exploration.explorectl import _main; _main()"
    )
    launcher = control_path(run_dir, CONTROL_EXPLORATION_LAUNCHER)
    atomic_write_text(
        launcher,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"export PYTHONPATH={python_path}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        f"exec {executable} -c {module} --run-dir {shlex.quote(str(run_dir))} \"$@\"\n",
    )
    os.chmod(launcher, 0o755)
    atomic_write_text(
        control_path(run_dir, EXPLORATION_GUIDE_FILE),
        _guide(str(config["source_plan_schema_path"])),
    )


def build_exploration_prompt(run_dir: Path) -> str:
    config = read_json(control_path(run_dir.resolve(), CONTROL_RUN_CONFIG), "运行配置")
    return f"""[DATAGEN_STEP=SOURCE_EXPLORATION]

工作目录：`{run_dir}`。目标 Seed：`{config['seed_global_id']}`。

你的具体任务是深入调查这条 Seed 所需的数据来源，并通过受控下载取得能体现真实结构差异的 Raw
样本。你没有先前对话背景，必须从当前目录中的文件判断需求。

先完整读取 `.datagen/EXPLORATION_GUIDE.md`、`.datagen/selected_seed.json`、
`provenance/scenario_research.json` 和 Guide 中给出的 source plan Schema。逐个核对参考工具、参考
任务和数据需求，不要停留在 Step 1 已列出的来源线索。

产出并持续更新 `.datagen/drafts/source_plan.json`；所有 URL 先登记、再用
`bash ./.datagen/explorectl` 下载。运行 `assess` 并处理其具体问题，直到每个来源准确收口且
`finalize` 成功。现在开始执行实际调查和命令。
"""


def build_exploration_continuation_prompt(
    run_dir: Path,
    *,
    round_index: int,
    final_round: bool,
) -> str:
    config = read_json(control_path(run_dir.resolve(), CONTROL_RUN_CONFIG), "运行配置")
    ending = (
        "这是可用总预算内的最后一轮。优先关闭核心能力缺口和 blocking_issues；只有某个核心需求"
        "仍无来源时才快速寻找替代入口，不再扩展低优先级主题。对已有样本更新唯一来源归属和完成"
        "证据，对真实失败更新 blocked/unavailable，随后再次 assess 并立即 finalize。"
        if final_round else
        "继续补齐核心能力缺口、结构盲区和未收口来源；不要重复下载已经能证明相同结构的数据。"
    )
    return f"""[DATAGEN_STEP=SOURCE_EXPLORATION_CONTINUATION]

工作目录：`{run_dir}`。这是 `{config['seed_global_id']}` 的第 {round_index} 轮来源调查，目标仍是
取得覆盖参考工具/任务数据条件的代表性 Raw，并成功收口来源计划。

先读取 `.datagen/EXPLORATION_GUIDE.md`、现有 source plan、`provenance/source_inventory.json` 和
`.datagen/exploration_assessment.json`。先运行一次 `bash ./.datagen/explorectl assess`，将每条
`blocking_issues` 和 `next_actions` 对应到具体 source_id、URL 或数据需求后再行动。保留已验证
成果。{ending} 所有正式写入使用 `bash ./.datagen/explorectl`。
"""


__all__ = [
    "build_exploration_continuation_prompt",
    "build_exploration_prompt",
    "prepare_exploration",
]
