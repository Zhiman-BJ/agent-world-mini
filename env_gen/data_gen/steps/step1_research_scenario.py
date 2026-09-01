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

我们要根据一条简短 Seed 构建数据环境。当前任务通过互联网调研把 Seed 丰富成具体、连贯的环境
说明，重点说明环境、实体、工具和任务的业务语义。这份结果将用于继续调查数据来源和下载数据。

## 输入

完整读取 `.datagen/selected_seed.json`。其中包括初始环境描述、参考工具、参考任务、数据方向，
也可能包含产品主页、项目仓库或文档 URL。首先访问并理解 Seed 自带的 URL，再围绕相关领域、
产品、项目和概念继续搜索。Seed 提供调研起点；外部资料用于核实和扩展其中的信息。

## 调研方式

采用持续完善的方式进行调研：

1. 根据完整 Seed 和其中的 URL，先形成对环境的初步理解。
2. 调研真实领域、产品或项目的用途、用户、工作方式、术语、数据形态和常见操作。
3. 每获得新的有效信息，同时完善环境、实体、工具和任务，并检查它们能否组成同一套工作场景。
4. 当前理解中的缺口决定下一步搜索内容，包括工具处理的对象、任务需要的信息、实体参与的用途和
   参考能力在环境中的位置。
5. 核心场景形成后，继续补充能够实际参与同一工作流的实体、工具能力和典型任务。

## 内容要求

- **环境简述**：用 1-2 句直接说明这是一个什么环境、服务什么领域和使用者。
- **环境详细描述**：展开说明专业背景、参与者、环境中的主要内容及范围。
- **实体**：实体粒度以交互和数据组织所需的稳定业务对象为准。每项包含名称、业务含义、在环境
  中的作用及关键属性；与其他实体的联系写入描述。多个概念在来源中共享同一标识、始终作为整体
  被查询或改变、生命周期保持一致时，可以形成复合实体。具有独立标识、属性、生命周期或受操作
  方式的概念分别形成实体。仅用于刻画另一个实体的概念归入关键属性。
- **工具**：每项包含名称，以及它对什么对象执行什么操作、需要什么信息、返回或改变什么。
  Seed 中每个参考工具使用原名称形成独立说明；紧密相关的新能力可以作为新增工具列出。
- **任务**：每项包含名称，以及用户目标、执行过程、涉及的实体和工具、预期结果。Seed 中已有
  参考任务时分别覆盖，并根据调研补充能够体现环境主要工作方式的典型任务。
- **调研备注**：列出后续应寻找的记录、状态、关系或领域文件，记录关键 HTTP(S) 来源及其提供的
  信息，并保留仍需通过实际来源确认的问题。

## 停止条件

同时满足以下条件后停止继续扩展：

- 环境简述可以快速识别场景，详细描述足以让陌生读者理解使用者、主要内容和范围；
- Seed 中每个参考工具和参考任务都有具体说明；
- 每个核心实体都有明确、稳定的对象边界及重要属性和用途；每项核心工具与任务都能连接到相应
  实体和数据条件；
- 核心概念有 Seed URL 或可信外部资料支持，事实、合理推断和未确认问题已经区分；
- 后续需要寻找的记录、状态、文件和来源方向已经明确；
- 新搜索结果与已有语义基本重复，剩余问题集中在来源可用性、字段细节和数据规模。

## 输出格式

把结果写入 `.datagen/drafts/scenario_research.json`。使用以下固定结构，Python 会补充 Seed 身份并
完成格式校验和正式保存：

```json
{
  "environment": {
    "summary": "一到两句环境简述",
    "description": "环境的专业背景、参与者、主要内容和范围"
  },
  "entities": [
    {
      "name": "实体名称",
      "description": "业务含义、用途和重要联系",
      "key_attributes": ["对实际工作重要的属性及含义"]
    }
  ],
  "tools": [
    {
      "name": "工具名称",
      "description": "处理对象、执行动作、所需信息和结果"
    }
  ],
  "tasks": [
    {
      "name": "任务名称",
      "description": "用户目标、执行过程、涉及内容和预期结果"
    }
  ],
  "research_notes": {
    "data_directions": ["后续应寻找的数据、状态或领域文件"],
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
    """Build either the initial research task or a concrete repair task."""

    run_dir = run_dir.resolve()
    if failure is None:
        return f"""工作目录：`{run_dir}`。

你要根据一条简短 Seed 调研并丰富一个数据环境，使不了解该领域的人也能理解这个环境的业务场景、
重要实体、工具能力和典型任务，并能据此继续调查和下载所需数据。

完整读取 `.datagen/selected_seed.json` 和 `.datagen/RESEARCH_GUIDE.md`。从 Seed 中的 URL 开始
互联网调研；每获得新的领域信息，同时完善环境、实体、工具和任务，并检查它们能否形成同一套
连贯工作场景。

达到 Guide 中的停止条件后，将结果写入 `.datagen/drafts/scenario_research.json`，然后结束任务。
"""

    detail = failure.strip().replace("\x00", " ")[:3000]
    return f"""工作目录：`{run_dir}`。这是第 {attempt} 次也是最后一次结果修正。上次草稿没有通过
Python 校验，具体问题如下：

{detail}

重新读取完整 Seed、`.datagen/RESEARCH_GUIDE.md` 和
`.datagen/drafts/scenario_research.invalid.json`。保留其中有效的调研结论，针对上述问题形成修正后
的完整结果，写入 `.datagen/drafts/scenario_research.json`，然后结束任务。
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
        "schema_version": "2.0",
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
            "schema_version": "2.0",
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
