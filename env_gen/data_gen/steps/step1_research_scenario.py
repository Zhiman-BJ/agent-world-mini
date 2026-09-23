"""Step 1: research one prepared Seed and save a concise scenario brief."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import time
from typing import Any, Callable

from env_gen.data_gen.analysis.scenario_research import (
    validate_scenario_research_payload,
)
from env_gen.data_gen.analysis.seed import is_python_package_seed

from .common.constants import (
    CONTROL_RUN_CONFIG,
    CONTROL_SCENARIO_RESEARCH_RECEIPT,
    CONTROL_SELECTED_SEED,
    RESEARCH_GUIDE_FILE,
    SCENARIO_RESEARCH_PATH,
)
from .common.control_io import atomic_write_text, control_path, read_json, write_json
from .common.workspace_files import file_sha256


AgentRunner = Callable[[str, int, tuple[Path, ...]], str]
_PYTHON_PACKAGE_RESEARCH_SECONDS = 900
_PYTHON_PACKAGE_RESEARCH_TOTAL_SECONDS = 1200


RESEARCH_GUIDE = """# 任务：通过调研丰富环境 Seed

## 背景

我们要根据一条简短 Seed 还原一个可复用的现实工作环境。它不是为某个参考任务准备的样例数据包，
也不是对 Seed 中工具能力的逐项复刻，而应围绕现实中共同维护和使用的一组对象、文件与关系形成稳定
工作空间，使后续能够从同一环境自然设计多个目标、处理路径和结果判断方式不同的复杂任务。这份结果
将用于继续调查来源和下载能够共同组成该环境的数据。

## 输入

完整读取 `.datagen/selected_seed.json`。其中包括初始环境描述、参考工具、参考任务、数据方向，
也可能包含产品主页、项目仓库或文档 URL。首先访问并理解 Seed 自带的 URL，再由其中的产品、
能力、处理对象和使用线索寻找现实工作场景。Seed 提供入口和必须核实的线索，不限定环境只能
支持已有工具或少数任务；现实中的工作方式决定环境最终应包含什么。

## 调研方式

采用持续完善的方式进行调研：

1. 从完整 Seed 和其中的 URL 开始，调查相关产品、项目或领域在现实中由谁使用、解决什么问题，
   以及通常如何开展工作。
   Seed 自带 URL 是调研入口，不是来源边界。理解这些入口后，可以优先使用原生 Web Search，
   从真实应用案例、论文、项目仓库、操作记录或公开数据中寻找其他独立来源，用于核实场景边界、
   实际工作方式和数据流转。若现有来源已经充分且相互印证，不必为了增加数量继续搜索；多个页面
   属于同一项目或组织时，应视为同一来源体系。
2. 沿来源中实际出现的工作过程理解业务对象、文件、参与者、工具和典型任务，并据此持续完善
   环境描述。优先识别现实中的项目、团队或持续工作上下文及其边界；不要根据预想的数据结构反推场景，
   也不要因为工具可以处理某类内容，就把彼此无关的工作合并为一个环境。
3. Seed 中的参考工具能力和任务是调查线索。先理解完整工作流，再核实其中实际用到的能力；当
   `init_ref_tools` 是很长的 API 索引时按名称查询相关项，不必逐项阅读或把整个索引复刻进场景。工具
   数量和 API 数量不定义环境边界，也不代表环境丰富度。
   通过产品页面、官方文档、项目仓库、操作指南、案例或其他可信资料核实结论，不把 Seed 自身的
   简短描述直接扩写成调研结论。
4. 优先使用能反映实际使用方式的资料。规范和接口文档可核实术语及能力边界，操作指南、案例、
   问题记录、测试材料和真实输入输出可说明工作在现实中如何发生。
5. 环境、实体、工具和任务都引用直接支持该结论的 HTTP(S) 来源。来源没有说明的具体字段、参数、
   状态、阈值或操作流程不写入结果；尚未确认的信息放入开放问题。

不要等到全部调研结束才写结果。完整读取 Seed，并在关键来源已经足以确定初步环境边界、核心工作和
数据方向后，立即按输出格式保存一份字段齐全、只包含已核实结论的草稿。此后发现新的重要事实时直接
更新同一文件；开放问题可以保留，不能为了继续搜索而把首次交付推迟到会话末尾。

## 内容要求

- **环境简述**：使用 20-160 个字符、1-2 句直接说明这是一个什么环境、服务什么领域和使用者。
- **环境详细描述**：使用 80-800 个字符展开说明专业背景、参与者、工作触发方式、主要数据流转、
  结果及范围，使读者能理解环境如何运转，而不只是知道它包含哪些概念。
- **实体**：记录现实工作中稳定存在的业务对象。每项说明它是什么、在环境中的作用、由谁使用，
  以及与其他对象的主要联系。一个实体名称对应一种可独立存在和变化的业务对象；能够被分别指称、
  产生、使用或改变的对象分别记录。来源明确将多个概念定义为同一对象时才使用复合名称。不预设
  实体字段、标识符、状态枚举或存储结构。
- **工具**：只记录调研来源中明确存在的工具、产品能力或接口。每项说明现实中的使用者或调用方、
  处理的业务对象、核心作用、产生的结果，以及与相近能力的边界。Seed 中实际进入工作流的参考工具沿用其
  完整标识；同时具有 `module` 和 `name` 时写成 `module.name`。较长的能力索引只核实相关部分。重点描述
  用途和业务效果；来源明确且有助于理解能力时可以说明关键行为，但不枚举
  认证参数、请求字段、响应字段或持久化设计。岗位职责、工作阶段和待建设系统不作为工具。
  对 Python 包能力 Seed，`tools` 只能填写 Seed 中实际存在的精确 `module.name`；调研中用于核实现实
  工作流、但不在 Seed 能力清单中的商业产品或其他软件，只作为来源、背景或边界说明，不写入 `tools`。
  工具名必须从 Seed 的条目逐字复制；不得追加 `__init__`、方法或属性名，也不得添加 `src.` 等源码目录前缀。
- **任务**：记录调研来源中出现的典型工作。每项说明现实触发场景、参与者和目标，概括主要处理
  过程、使用的环境对象或工具，以及完成后形成的结果，使后续阶段能够理解这项工作需要哪些类型的
  数据。每项任务对应一个发生时机和用户目标，不同阶段或不同目标的工作分别记录。名称可以在不
  改变来源原意的前提下归纳；参数、实体字段、完整操作编排和未被来源支持的异常分支留给后续阶段。
  Seed 参考任务用于寻找和核实对应的现实工作。这里的任务用于描述环境可能支持的工作目标，不是
  Step 2 必须逐项定制数据的固定案例；不要按工具函数拆分任务，也不要把同一目标中的 API 调用步骤
  分别写成多个任务。任务集合应体现同一工作空间为什么能够被反复用于不同目标。
- **来源引用**：环境、每个实体、工具和任务的 `source_urls` 至少包含一个直接支持其描述的 URL，
  并且该 URL 同时登记在调研备注的 `sources` 中。一项内容需要综合多个来源时列出全部关键来源。
- **数据方向**：保持字符串列表。每项描述一组能够在同一现实上下文中共同使用的数据，而不是单个
  工具的输入或预先挑选的几个文件。说明它通常由什么系统、组织、项目或工作环节产生，包含哪类
  内容，如何与环境中的其他数据关联，能够扩展哪些工作目标，以及其中自然存在什么条件、状态、
  案例或内容差异。具体差异形式由现实场景决定，不强制所有环境具有版本、历史或异常数据。来源
  确认了文件格式、协议或目录形态时一并说明；实际字段和存储结构留待下载后识别。
- **调研来源与开放问题**：记录关键 HTTP(S) 来源及其提供的信息，并保留仍需确认的问题。

## 停止条件

同时满足以下条件后停止继续扩展：

- 环境简述可以快速识别场景，详细描述足以让陌生读者理解现实工作如何开始、推进和完成；
- 环境边界对应一个可复用的现实工作空间，而不是一个工具包的能力全集或一个参考任务的数据附件；
- Seed 中的参考任务以及现实工作流实际使用的参考工具已经通过外部资料核实；
- 核心实体的业务含义和彼此联系清楚；每项工具足以理解其业务用途、对象和结果；每项任务足以理解
  其触发场景、参与者、主要过程和工作结果；
- 数据方向已经说明要寻找的真实材料、产生环境、业务用途和主要联系，未确认内容已放入开放问题；
- 继续搜索只会重复已有场景，或剩余问题需要通过实际下载和检查数据才能回答。

## 输出格式

把结果写入 `.datagen/drafts/scenario_research.json`。使用以下固定结构，Python 会补充 Seed 身份并
完成格式校验和正式保存：

```json
{
  "environment": {
    "summary": "一到两句环境简述",
    "description": "环境的专业背景、参与者、主要内容和范围",
    "source_urls": ["https://source.example/environment"]
  },
  "entities": [
    {
      "name": "实体名称",
      "description": "业务含义、在现实环境中的作用和重要联系",
      "source_urls": ["https://source.example/entity"]
    }
  ],
  "tools": [
    {
      "name": "工具名称",
      "description": "现实使用者、处理对象、核心作用、结果和能力边界",
      "source_urls": ["https://source.example/tool"]
    }
  ],
  "tasks": [
    {
      "name": "任务名称",
      "description": "现实触发场景、参与者、目标、主要过程和工作结果",
      "source_urls": ["https://source.example/task"]
    }
  ],
  "research_notes": {
    "data_directions": ["要寻找的关联数据组、现实产生环境、可扩展的工作目标、自然差异以及与其他数据的联系"],
    "sources": [
      {
        "url": "https://source.example/path",
        "description": "这个来源为当前环境说明提供了什么信息"
      }
    ],
    "open_questions": ["仍需通过实际来源确认的问题"]
  }
}
```

文件写完后结束任务。
"""


def _build_research_guide(seed: dict[str, Any]) -> str:
    """Use one workflow-oriented research guide for every Seed type."""

    return RESEARCH_GUIDE


class ScenarioResearchError(RuntimeError):
    """Step 1 没有交付有效的 scenario_research。"""


def _build_research_prompt(
    run_dir: Path,
    *,
    attempt: int = 1,
    failure: str | None = None,
) -> str:
    """Point the Agent to the guide, adding only attempt-specific repair context."""

    run_dir = run_dir.resolve()
    if failure is None:
        return f"""工作目录：`{run_dir}`。

完整读取并执行 `.datagen/RESEARCH_GUIDE.md` 中的任务。Guide 中提到的相对路径均以该工作目录为准。
"""

    detail = failure.strip().replace("\x00", " ")[:3000]
    invalid_draft = run_dir / ".datagen/drafts/scenario_research.invalid.json"
    if not invalid_draft.is_file():
        return f"""工作目录：`{run_dir}`。

完整读取 `.datagen/RESEARCH_GUIDE.md` 中的输出格式。Guide 中提到的相对路径均以该工作目录为准。

这是第 {attempt} 次交付修正。上一次 Agent 已经进行了调研，但没有在时限内写出草稿：

{detail}

不要重新联网搜索，也不要重新浏览 API 索引、文档导航、源码或测试目录。先读取
`.datagen/agent_runs/` 中上一轮的 `stderr.log`，从其中已经访问的来源、已经形成的工作流判断和核实结果中
整理内容。你的第一项实质操作必须是把一份结构完整的结果写入
`.datagen/drafts/scenario_research.json`。写完后再用剩余时间按需核对并更新该文件；无论如何不要删除已写草稿。
"""
    return f"""工作目录：`{run_dir}`。

完整读取并执行 `.datagen/RESEARCH_GUIDE.md` 中的任务。Guide 中提到的相对路径均以该工作目录为准。

这是第 {attempt} 次结果修正。上次草稿保存在
`.datagen/drafts/scenario_research.invalid.json`，未通过校验的问题如下：

{detail}

保留草稿中有效的调研结论，修正上述问题，并按照 Guide 要求交付完整结果。
"""


def _research_input_snapshot(run_dir: Path) -> dict[str, str]:
    config_path = control_path(run_dir, CONTROL_RUN_CONFIG)
    config = read_json(config_path, "运行配置")
    data_gen_root = Path(__file__).resolve().parents[1]
    paths = {
        config_path,
        control_path(run_dir, CONTROL_SELECTED_SEED),
        control_path(run_dir, RESEARCH_GUIDE_FILE),
        Path(str(config["seed_path"])),
        Path(str(config["scenario_research_schema_path"])),
        data_gen_root / "analysis/scenario_research.py",
        Path(__file__),
    }
    return {
        str(path): file_sha256(path)
        for path in sorted(paths, key=lambda item: str(item))
        if path.is_file()
    }


def _verify_research_inputs(expected: dict[str, str]) -> None:
    issues: list[str] = []
    for value, digest in expected.items():
        path = Path(value)
        if not path.is_file():
            issues.append(f"删除了只读文件：{path}")
        elif file_sha256(path) != digest:
            issues.append(f"修改了只读文件：{path}")
    if issues:
        raise RuntimeError("；".join(issues[:8]))


def save_scenario_research(
    run_dir: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Bind an Agent draft to the Seed, validate it, and save it."""

    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    schema = read_json(Path(config["scenario_research_schema_path"]), "场景研究 Schema")
    research = {
        **payload,
        "schema_version": "3.0",
        "seed_global_id": str(config["seed_global_id"]),
        "seed_sha256": str(config["seed_sha256"]),
    }
    issues = validate_scenario_research_payload(
        research,
        schema=schema,
        seed=seed,
        seed_sha256=str(config["seed_sha256"]),
    )
    if issues:
        messages = "; ".join(
            f"[{issue.code}] {issue.path}: {issue.message}" for issue in issues[:12]
        )
        if len(issues) > 12:
            messages += f"; 另有 {len(issues) - 12} 条错误"
        raise RuntimeError("scenario_research 不符合要求：" + messages)

    target = run_dir / SCENARIO_RESEARCH_PATH
    write_json(target, research)
    digest = file_sha256(target)
    write_json(
        control_path(run_dir, CONTROL_SCENARIO_RESEARCH_RECEIPT),
        {
            "schema_version": "3.0",
            "path": SCENARIO_RESEARCH_PATH,
            "sha256": digest,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "entity_count": len(research.get("entities", [])),
            "tool_count": len(research.get("tools", [])),
            "task_count": len(research.get("tasks", [])),
            "source_count": len(research.get("research_notes", {}).get("sources", [])),
        },
    )
    return research


def scenario_research_receipt_issues(run_dir: Path) -> list[dict[str, str]]:
    """Check that the formal brief still matches its save receipt."""

    run_dir = run_dir.resolve()
    research_path = run_dir / SCENARIO_RESEARCH_PATH
    receipt_path = control_path(run_dir, CONTROL_SCENARIO_RESEARCH_RECEIPT)
    if not research_path.is_file() or not receipt_path.is_file():
        return [{
            "code": "scenario_research_not_saved",
            "path": SCENARIO_RESEARCH_PATH,
            "message": "初步调研草稿尚未通过 Python 校验并保存",
        }]
    try:
        receipt = read_json(receipt_path, "预调研保存收据")
        actual = file_sha256(research_path)
    except (OSError, RuntimeError) as error:
        return [{
            "code": "invalid_scenario_research_receipt",
            "path": SCENARIO_RESEARCH_PATH,
            "message": str(error),
        }]
    if receipt.get("sha256") != actual:
        return [{
            "code": "scenario_research_modified_after_save",
            "path": SCENARIO_RESEARCH_PATH,
            "message": "预调研报告保存后被直接改写",
        }]
    return []


def read_saved_scenario_research(run_dir: Path) -> dict[str, Any]:
    """Read and revalidate the formal brief for a later pipeline step."""

    issues = scenario_research_receipt_issues(run_dir)
    if issues:
        raise RuntimeError("; ".join(issue["message"] for issue in issues))
    run_dir = run_dir.resolve()
    payload = read_json(run_dir / SCENARIO_RESEARCH_PATH, "scenario_research")
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    schema = read_json(Path(config["scenario_research_schema_path"]), "场景研究 Schema")
    validation_issues = validate_scenario_research_payload(
        payload,
        schema=schema,
        seed=seed,
        seed_sha256=str(config["seed_sha256"]),
    )
    if validation_issues:
        detail = "; ".join(
            f"[{issue.code}] {issue.path}: {issue.message}"
            for issue in validation_issues[:12]
        )
        raise RuntimeError("已保存的 scenario_research 复核失败：" + detail)
    return payload


def run_scenario_research(
    *,
    run_dir: Path,
    agent_runner: AgentRunner,
) -> tuple[dict[str, Any], int]:
    """Run the complete scenario-research loop for one prepared Seed."""

    run_dir = run_dir.resolve()
    run_config = read_json(
        control_path(run_dir, CONTROL_RUN_CONFIG),
        "Step 0 运行配置",
    )
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "Step 0 选中 Seed")
    atomic_write_text(control_path(run_dir, RESEARCH_GUIDE_FILE), _build_research_guide(seed))
    policy = run_config["collection_policy"]
    attempt_seconds = int(policy["scenario_research_seconds"])
    total_seconds = int(policy["scenario_research_total_seconds"])
    if is_python_package_seed(seed):
        attempt_seconds = max(attempt_seconds, _PYTHON_PACKAGE_RESEARCH_SECONDS)
        total_seconds = max(total_seconds, _PYTHON_PACKAGE_RESEARCH_TOTAL_SECONDS)
    max_attempts = int(policy["max_scenario_research_attempts"])
    protected = _research_input_snapshot(run_dir)
    research_path = run_dir / SCENARIO_RESEARCH_PATH
    receipt_path = control_path(run_dir, CONTROL_SCENARIO_RESEARCH_RECEIPT)
    draft_path = run_dir / ".datagen/drafts/scenario_research.json"
    invalid_draft_path = run_dir / ".datagen/drafts/scenario_research.invalid.json"
    deadline = time.monotonic() + total_seconds
    last_failure = "尚未执行"

    for attempt in range(1, max_attempts + 1):
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        if remaining <= 0:
            last_failure = f"预调研超过阶段总预算 {total_seconds} 秒"
            break
        if attempt > 1:
            research_path.unlink(missing_ok=True)
            receipt_path.unlink(missing_ok=True)
            invalid_draft_path.unlink(missing_ok=True)
            if draft_path.is_file():
                draft_path.replace(invalid_draft_path)
        prompt = _build_research_prompt(
            run_dir,
            attempt=attempt,
            failure=last_failure if attempt > 1 else None,
        )
        agent_error: Exception | None = None
        try:
            agent_runner(
                prompt,
                min(attempt_seconds, remaining),
                (draft_path,),
            )
        except Exception as error:
            agent_error = error

        try:
            _verify_research_inputs(protected)
        except Exception as error:
            raise ScenarioResearchError(str(error)) from error
        try:
            research = save_scenario_research(
                run_dir,
                read_json(draft_path, "scenario_research 草稿"),
            )
        except Exception as error:
            last_failure = str(error)
            if agent_error is not None:
                last_failure += f"；Agent 调用错误：{agent_error}"
            continue
        return research, attempt

    raise ScenarioResearchError(f"Step 1 没有交付有效场景研究：{last_failure}")
