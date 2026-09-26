"""Step 1：调研真实工作任务，并验证当前环境是否能够支持这些任务。

本步不生成 benchmark 题目，也不生成 Solution。它先复用 DataGen 已有的场景调研，
再由具备网页搜索能力的 Agent 补充现实工作描述和必要条件，最后把每个任务原型映射到
当前环境的 Record Set、关系、文件 Scope 和公开工具。只有
``environment_support.generatable=true`` 的原型可以进入 Step 2。
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .utils.schema import Draft202012Validator

from .utils.environment import load_frozen_package
from .utils.io import read_json, write_json
from .utils.schema_docs import STEP1_SCHEMA_DOCS, copy_schema_docs


class TaskResearchAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


def _run_research_agent(
    agent: TaskResearchAgent,
    prompt: str,
    *,
    working_directory: Path,
) -> str:
    """End the Agent call once its JSON handoff is complete and stable."""
    method = getattr(agent, "run_until_json_file", None)
    if callable(method):
        return method(
            prompt,
            working_directory=working_directory,
            required_path=working_directory / "task_research.json",
        )
    return agent.run(prompt, working_directory=working_directory)


@dataclass(frozen=True)
class Step1Result:
    output_path: Path
    archetype_count: int
    generatable_count: int


def build_task_research_prompt(attempt: int) -> str:
    """返回一份不依赖对话上下文的完整现实任务调研说明。"""
    return f"""# 任务：确认这个离线环境能够支持哪些真实工作

## 背景

系统已经准备好一个可重复使用的离线工作环境。环境中有一份固定的初始数据或文件，
以及一组能够读取、分析或修改这些内容的工具。后续任务作者会根据你的调研结果，在这份
环境中设计具体任务。

你没有本项目此前的对话上下文，也不需要了解任何流水线步骤。工作目录中的文件是你判断
事实、环境能力和输出格式的唯一依据。本次工作只调查现实中确实存在的工作方式，并判断
当前离线环境能否完整执行这些工作；不要生成具体题目、标准答案或程序。

这是第 {attempt} 次提交。如果 `validation_feedback.json` 中记录了先前错误，本次结果必须
逐项修正这些错误，而不是忽略后重新提交相同内容。

## 输入文件

完整读取以下文件：

1. `environment.public.json`

   这是当前离线环境允许公开使用的完整说明。顶层包含：

   - `environment`：环境名称、用途和可访问内容；
   - `environment.record_sets`：数据库中的记录集合，可以理解为一类业务记录。每项给出
     集合标识、字段、唯一键和是否允许修改，但这里不包含全部真实记录；
   - `environment.relationships`：不同记录集合之间可以依据哪些字段建立关系；
   - `environment.filesystem_scopes`：按 `scope_id` 命名的文件区域。每个文件区域有自己的
     目录结构和读写权限；
   - `tools`：后续执行者真正可以调用的工具。`name` 是工具标识，`description` 和
     `usageConditions` 说明适用对象、前提和副作用，`inputSchema` 与 `outputSchema`
     分别说明参数和返回结果。工具内部代码不可见，也不需要推测。

2. `initial_state.summary.json`

   这是初始数据的数量、摘要和内容指纹，用来判断环境是否真的有相应材料。它不是完整
   记录，也不能支持从中猜测具体对象、ID 或最终答案。

3. `scenario_research.json`（可能不存在）

   这是准备环境时做过的场景调研，可能包含现实任务、来源和领域说明。它是可复用的研究
   依据，不是必须照抄的结论；其中没有来源支持的细节仍不能当作事实。

4. `task_research.schema.json`

   这是唯一允许的输出 JSON 结构。字段名、字段类型和必填项必须完全符合它。

5. `validation_feedback.json`

   这是程序对此前提交的检查结果。`previous_failures` 为空表示没有先前错误。

6. `references/环境契约-v2.0.md` 和 `references/工具契约-v1.0.md`

   这两份文档解释环境、记录集合、文件区域、公开工具及其参数和返回值。先阅读它们，
   再使用 JSON 文件中的实际标识；文档只用于理解含义，不能替代 JSON 中的具体内容。

## 你的目标

尽可能完整地找出这个领域中真实发生、并且可能由当前环境完成的工作。在来源可靠且当前
工具能够完整支撑的前提下尽量多找；如果证据或能力不足，不得为了增加数量编造内容。
不要预设或偏好任务类别。以真实来源和当前环境能力为准，尽量找出更多能够被现有记录、关系、
文件和工具完整支撑的工作；也不要因为某种工作看起来常见、复杂或容易生成就提高其比例。
后续数据将用于训练 Agent 使用工具。因此，在同样真实、来源可靠且能被环境完整支撑的工作中，
要充分调研那些完成全部业务要求时本来就需要多次工具交互的工作。多次交互可以来自多个真实对象、
多份必要证据或前一结果决定后续处理；不能把一次工作人为拆碎，也不能增加现实工作不需要的环节。
特别关注真实工作中的“结果驱动下一步”：例如前一阶段产生新的候选方案、参数、对象或文件，
后一阶段必须使用它继续处理，并在更全面的条件下验证是否真正达成目标。这种因果闭环是有价值的
任务深度；但只有来源确认它属于真实工作，且当前环境确实能传递这些结果时才应写入调研。
不要把彼此无关的工作串在一起伪造闭环。
输出字段
`task_archetypes` 的每一项表示一种“现实工作类型”：它描述一项完整的业务工作，而不是
一条针对当前某个具体对象的题目。因此不得包含当前环境中的具体记录 ID、具体文件答案、
工具调用顺序、Python 实现或隐藏答案。

每种现实工作类型只需用两部分说明，不要把同一件事拆成互相重复的栏目：

- `description`：一段完整、具体的工作描述，连贯说明谁在什么业务场景下处理什么问题、
  要完成哪些相互依赖的环节、最后交付什么，以及最容易在哪些地方出错。描述应足够让
  陌生读者理解这是一项真实工作，而不是工具操作清单。
- `requirements`：完成这项工作必须满足的关键条件，综合列出需要核对的证据、不可违反
  的业务规则、必须保留或修改的内容和结果确认条件。每项都要能在环境能力或任务正文中
  具体落地，而不是泛泛写“结果正确”。

另外填写 `source_urls` 追溯来源，并填写 `environment_support` 判断当前环境能否执行。
调研结果是后续任务设计的现实依据，不是一份固定步骤模板；不要把某一种常见做法写成该
工作的唯一合法执行顺序，也不要因为当前看到的工具组合较少而省略真实存在的重要变体。

## 调研方法

1. 先理解完整环境说明和已有场景调研，确定现实工作涉及的对象、业务背景和结果。
2. 复用 `scenario_research.json` 已登记的可靠事实与来源，但不要只改写其中已有的任务。
   继续补充网页调研，确认不同岗位、触发场景、失败案例、例外情况和交付检查，扩大真实
   工作覆盖面。
3. 来源优先使用官方文档、标准组织、产品操作指南、维护者仓库、真实案例和问题记录。
   产品主页只能证明产品存在，不能单独证明一套具体工作流程。
4. 来源没有明确支持的字段、状态、阈值或操作要求不要自行补全。尚未确认的能力应被判定为
   不支持，而不是用常识填补。
5. 不要按工具逐个编造工作。先确认现实工作，再判断环境中的数据和工具是否足以完成它。
6. 对每项工作核对从输入到交付所需的完整能力。只有现实过程中的必要环节都能由当前环境
   支撑时，才标记为可生成；不能删除必要环节来迁就现有工具，也不能拼接无关环节制造复杂度。
7. 对具有迭代或优化性质的工作，查明初始方案、停止条件、中间结果如何影响下一步，以及
   最终方案如何在不同于局部搜索的条件下被复验。如果环境只能查看已有结果，就应如实记为复核工作，
   不能把它描述成会产生新方案的设计工作。

先完成来源、环境能力和字段标识的核对。只有全部必填字段、来源和至少一种可生成工作都已
准备完整时，才首次创建 `task_research.json`；不要先写空数组、占位内容或半成品 JSON。
文件写入后系统会立即把它作为本次正式提交，因此写入前必须已经可以通过 Schema 和下述
完成条件。

## 判断当前环境是否支持

每种现实工作类型都要填写 `environment_support`。其中：

- `record_sets`：完成工作实际需要使用的记录集合标识；
- `relationships`：实际需要使用的记录关系标识；
- `filesystem_scopes`：实际需要使用的文件区域标识；
- `tools`：当前已确认能直接支持该工作的主要工具标识；这是能力映射，不是后续任务的封闭
  工具白名单。后续完整实现同一现实工作时，可以使用环境中其他确实相关的公开工具；
- `unsupported_requirements`：现实工作需要、但当前环境缺少的数据或能力；
- `generatable`：当前环境是否能从初始状态出发完整执行这项工作；
- `reason`：基于哪些现有数据和工具作出这个判断。

上述四类标识只能逐字使用 `environment.public.json` 中真实存在的值。不能因为名称或主题
相似就宣称支持，也不能假设执行时可以联网、联系人工或访问未声明的系统。

只有以下条件同时成立时，`generatable` 才能为 `true`：

1. 所有必要数据、文件和执行工具都存在；
2. 每个必要环节都能通过公开工具完成；
3. `unsupported_requirements` 为空；
4. `description` 完整说明现实工作，`requirements` 中的每一项都是真实且能够由当前环境
   落地的必要条件。

不要通过删除现实工作的核心目标来把 `generatable` 改成 `true`。能力不足时保留完整的
现实工作描述，把缺失内容写入 `unsupported_requirements`，并设置为 `false`。

## 完成条件

- 每种工作都有直接支持它的 HTTP(S) 来源，且所有 `source_urls` 都登记在 `sources`；
- 陌生读者只看字段内容就能理解谁在何时做什么、依据什么、受到什么约束以及交付什么；
- 环境支持判断使用了真实标识，没有假设隐藏数据或未提供的能力；
- 不同工作具有不同的触发条件或业务结果，而不是只更换对象名称；
- 至少有一种工作满足全部条件并标记为 `generatable=true`。

## 输出

最终只创建或更新工作目录中的 `task_research.json`。写完后按照
`task_research.schema.json` 逐字段检查。不要修改任何输入文件，不要创建其他交付文件，
也不要在最终回复中粘贴 JSON 内容。
"""


def _schema_errors(payload: Any, schema: dict[str, Any]) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(payload)
    ]


def validate_task_research(
    payload: Any,
    *,
    schema: dict[str, Any],
    public_environment: dict[str, Any],
    env_id: str,
) -> list[str]:
    """执行 Schema 无法表达的环境引用和来源闭包检查。"""
    errors = _schema_errors(payload, schema)
    if errors or not isinstance(payload, dict):
        return errors
    if payload.get("env_id") != env_id:
        errors.append(f"env_id 应为 {env_id!r}")
    environment = public_environment.get("environment", {})
    known = {
        "record_sets": {str(item.get("record_set_id")) for item in environment.get("record_sets", [])},
        "relationships": {str(item.get("relationship_id")) for item in environment.get("relationships", [])},
        "filesystem_scopes": {str(item.get("scope_id")) for item in environment.get("filesystem_scopes", [])},
        "tools": {str(item.get("name")) for item in public_environment.get("tools", [])},
    }
    registered_sources = {
        str(item.get("url"))
        for item in payload.get("sources", [])
        if isinstance(item, dict)
    }
    identifiers: set[str] = set()
    generatable = 0
    for index, archetype in enumerate(payload.get("task_archetypes", [])):
        if not isinstance(archetype, dict):
            continue
        identifier = str(archetype.get("archetype_id") or "")
        if identifier in identifiers:
            errors.append(f"task_archetypes[{index}].archetype_id 重复：{identifier}")
        identifiers.add(identifier)
        for url in archetype.get("source_urls", []):
            if url not in registered_sources:
                errors.append(
                    f"task_archetypes[{index}].source_urls 未登记到 sources：{url}"
                )
        support = archetype.get("environment_support", {})
        if not isinstance(support, dict):
            continue
        for field, allowed in known.items():
            unknown = sorted(set(support.get(field, [])) - allowed)
            if unknown:
                errors.append(
                    f"task_archetypes[{index}].environment_support.{field} 引用未知标识：{unknown}"
                )
        if support.get("generatable") is True:
            generatable += 1
            if support.get("unsupported_requirements"):
                errors.append(
                    f"task_archetypes[{index}] 标记 generatable=true，"
                    "但仍有 unsupported_requirements"
                )
            description = str(archetype.get("description") or "")
            if len(description) < 120:
                errors.append(
                    f"task_archetypes[{index}].description 至少需要 120 个字符，"
                    "并完整描述相互依赖的现实工作"
                )
            requirements = archetype.get("requirements", [])
            if not isinstance(requirements, list) or len(requirements) < 2:
                errors.append(
                    f"task_archetypes[{index}].requirements 至少需要 2 项关键条件，"
                    "才能作为高难度任务原型"
                )
    if generatable == 0:
        errors.append("至少需要一个 environment_support.generatable=true 的任务原型")
    return errors


def _existing_research(step0_path: Path) -> Any:
    receipt = read_json(step0_path)
    value = receipt.get("scenario_research") if isinstance(receipt, dict) else None
    if not value:
        return None
    path = Path(str(value))
    resolved = path if path.is_absolute() else step0_path.resolve().parent / path
    return read_json(resolved) if resolved.is_file() else None


def run_step1(
    *,
    step0_path: Path,
    output_dir: Path,
    agent: TaskResearchAgent | None,
    research_fixture_path: Path | None = None,
    max_attempts: int = 3,
) -> Step1Result:
    """调研并接受一份通过环境引用检查的任务原型集合。"""
    if max_attempts < 1:
        raise ValueError("max_attempts 必须至少为 1")
    package = load_frozen_package(step0_path)
    public_environment = package.public_environment()
    env_id = str(package.environment["environment_id"])
    schema = read_json(Path(__file__).resolve().parent / "schemas" / "task_research.schema.json")
    output_dir = output_dir.resolve()
    output_path = output_dir / "step1_task_research.json"
    attempts_root = output_dir / "step1_research"
    existing_research = _existing_research(step0_path)
    failures: list[dict[str, Any]] = []

    attempts = 1 if research_fixture_path is not None else max_attempts
    for attempt in range(1, attempts + 1):
        audit_dir = attempts_root / f"attempt_{attempt:02d}"
        if audit_dir.exists():
            shutil.rmtree(audit_dir)
        with tempfile.TemporaryDirectory(
            prefix="agent-world-program-step1-"
        ) as temporary:
            attempt_dir = Path(temporary)
            write_json(attempt_dir / "environment.public.json", public_environment)
            receipt = read_json(step0_path)
            write_json(
                attempt_dir / "initial_state.summary.json",
                receipt.get("initial_state", {}),
            )
            write_json(attempt_dir / "task_research.schema.json", schema)
            copy_schema_docs(attempt_dir, STEP1_SCHEMA_DOCS)
            write_json(
                attempt_dir / "validation_feedback.json",
                {"previous_failures": failures},
            )
            if existing_research is not None:
                write_json(attempt_dir / "scenario_research.json", existing_research)
            prompt = build_task_research_prompt(attempt)
            (attempt_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            try:
                if research_fixture_path is not None:
                    payload = read_json(research_fixture_path.resolve())
                    write_json(attempt_dir / "task_research.json", payload)
                else:
                    if agent is None:
                        raise ValueError("没有提供 TaskResearchAgent")
                    _run_research_agent(
                        agent,
                        prompt,
                        working_directory=attempt_dir,
                    )
                    payload = read_json(attempt_dir / "task_research.json")
                errors = validate_task_research(
                    payload,
                    schema=schema,
                    public_environment=public_environment,
                    env_id=env_id,
                )
            except Exception as error:
                errors = [f"{type(error).__name__}: {error}"]
                payload = None
            shutil.copytree(attempt_dir, audit_dir)
        if errors:
            failures.append({"attempt": attempt, "errors": errors})
            continue
        assert isinstance(payload, dict)
        write_json(output_path, payload)
        archetypes = payload["task_archetypes"]
        return Step1Result(
            output_path,
            len(archetypes),
            sum(
                item["environment_support"]["generatable"] is True
                for item in archetypes
            ),
        )

    write_json(output_dir / "step1_task_research_failures.json", failures)
    raise RuntimeError(f"Step 1 在 {attempts} 次尝试后仍未得到有效任务调研")


def research_tasks(input: dict[str, Any], *, agent: Any = None) -> dict[str, Any]:
    """Pipeline adapter for Step 1."""
    config = input["config"]
    output = run_step1(
        step0_path=Path(input["step0_path"]),
        output_dir=Path(input["run_dir"]),
        agent=agent,
        research_fixture_path=config.research_fixture_path,
    )
    return {
        "task_research": read_json(output.output_path),
        "step1_path": str(output.output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step0-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--research-fixture", type=Path)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--max-attempts", type=int, default=3)
    arguments = parser.parse_args()
    agent = None
    if arguments.research_fixture is None:
        from utils.search_agent.codex import CodexAgentClient

        agent = CodexAgentClient(
            model=arguments.model,
            sandbox="workspace-write",
            enable_web_search=True,
            network_access=True,
            reasoning_effort="high",
        )
    result = run_step1(
        step0_path=arguments.step0_path,
        output_dir=arguments.output_dir,
        agent=agent,
        research_fixture_path=arguments.research_fixture,
        max_attempts=arguments.max_attempts,
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
