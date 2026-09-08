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


RESEARCH_GUIDE = """# 任务：通过调研丰富环境 Seed

## 背景

我们要根据一条简短 Seed 构建能够支持多种任务的真实数据环境。当前任务以 Seed 为切口，通过
互联网调研还原相关工作在现实中如何发生，再从真实工作中提炼环境、实体、工具和任务。这份结果
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
2. 沿来源中实际出现的工作过程理解业务对象、文件、参与者、工具和典型任务，并据此持续完善
   环境描述。不要根据预想的数据结构反推场景。
3. Seed 中的工具和任务是必须调查的线索。通过产品页面、官方文档、项目仓库、操作指南、案例或
   其他可信资料核实它们，不把 Seed 自身的简短描述直接扩写成调研结论。
4. 优先使用能反映实际使用方式的资料。规范和接口文档可核实术语及能力边界，操作指南、案例、
   问题记录、测试材料和真实输入输出可说明工作在现实中如何发生。
5. 环境、实体、工具和任务都引用直接支持该结论的 HTTP(S) 来源。来源没有说明的具体字段、参数、
   状态、阈值或操作流程不写入结果；尚未确认的信息放入开放问题。

## 内容要求

- **环境简述**：用 1-2 句直接说明这是一个什么环境、服务什么领域和使用者。
- **环境详细描述**：展开说明专业背景、参与者、工作触发方式、主要数据流转、结果及范围，使读者
  能理解环境如何运转，而不只是知道它包含哪些概念。
- **实体**：记录现实工作中稳定存在的业务对象。每项说明它是什么、在环境中的作用、由谁使用，
  以及与其他对象的主要联系。一个实体名称对应一种可独立存在和变化的业务对象；能够被分别指称、
  产生、使用或改变的对象分别记录。来源明确将多个概念定义为同一对象时才使用复合名称。不预设
  实体字段、标识符、状态枚举或存储结构。
- **工具**：只记录调研来源中明确存在的工具、产品能力或接口。每项说明现实中的使用者或调用方、
  处理的业务对象、核心作用、产生的结果，以及与相近能力的边界。Seed 中每个参考工具使用原名称
  核实和记录。重点描述用途和业务效果；来源明确且有助于理解能力时可以说明关键行为，但不枚举
  认证参数、请求字段、响应字段或持久化设计。岗位职责、工作阶段和待建设系统不作为工具。
- **任务**：记录调研来源中出现的典型工作。每项说明现实触发场景、参与者和目标，概括主要处理
  过程、使用的环境对象或工具，以及完成后形成的结果，使后续阶段能够理解这项工作需要哪些类型的
  数据。每项任务对应一个发生时机和用户目标，不同阶段或不同目标的工作分别记录。名称可以在不
  改变来源原意的前提下归纳；参数、实体字段、完整操作编排和未被来源支持的异常分支留给后续阶段。
  Seed 参考任务用于寻找和核实对应的现实工作。
- **来源引用**：环境、每个实体、工具和任务的 `source_urls` 至少包含一个直接支持其描述的 URL，
  并且该 URL 同时登记在调研备注的 `sources` 中。一项内容需要综合多个来源时列出全部关键来源。
- **数据方向**：保持字符串列表。每项用一段完整描述说明后续要寻找的真实记录、领域文件或数据
  集合，它通常由什么系统、组织或工作环节产生，包含哪类业务内容，支持哪些实体、工具或任务，
  以及如何与其他环境数据联系。来源确认了文件格式、协议或目录形态时一并说明；实际字段和存储
  结构留待下载后识别。
- **调研来源与开放问题**：记录关键 HTTP(S) 来源及其提供的信息，并保留仍需确认的问题。

## 停止条件

同时满足以下条件后停止继续扩展：

- 环境简述可以快速识别场景，详细描述足以让陌生读者理解现实工作如何开始、推进和完成；
- Seed 中的参考工具和参考任务已经通过外部资料核实，并放入现实使用场景；
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
    "data_directions": ["要寻找的材料、现实产生环境、业务用途以及与其他环境数据的联系"],
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
    read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "Step 0 选中 Seed")
    atomic_write_text(control_path(run_dir, RESEARCH_GUIDE_FILE), RESEARCH_GUIDE)
    policy = run_config["collection_policy"]
    attempt_seconds = int(policy["scenario_research_seconds"])
    total_seconds = int(policy["scenario_research_total_seconds"])
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
