"""Step 2：调研真实工作任务，并验证当前环境是否能够支持这些任务。

本步不生成 benchmark 题目，也不生成 Solution。它先复用 DataGen 已有的场景调研，
再由具备网页搜索能力的 Agent 补充现实角色、触发条件、工作过程、交付结果和常见
失败方式，最后把每个任务原型映射到当前环境的 Record Set、关系、文件 Scope 和
公开工具。只有 ``environment_support.generatable=true`` 的原型可以进入 Step 3。
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..utils.schema import Draft202012Validator

from ..utils.environment import load_frozen_package
from ..utils.io import read_json, write_json


class TaskResearchAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step2Result:
    output_path: Path
    archetype_count: int
    generatable_count: int


def build_task_research_prompt(attempt: int) -> str:
    """返回 Step 2 的完整调研任务，业务要求保留在当前 Step 文件中。"""
    return f"""# 任务：调研真实工作任务并与当前环境能力对齐

这是第 {attempt} 次提交。你是一名现实工作流研究员，不负责生成 benchmark 题目。

完整读取工作目录中的：

- `environment.public.json`：当前环境的数据声明和公开工具契约；
- `initial_state.summary.json`：初始状态的数量与摘要，不是完整业务记录；
- `scenario_research.json`：DataGen 已有场景调研，文件可能不存在；
- `task_research.schema.json`：唯一允许的输出格式；
- `validation_feedback.json`：上一次提交的问题。

先复用已有场景调研中的 tasks、source_urls 和 sources。只有在角色、触发场景、现实
流程、交付结果或常见失败方式缺少直接证据时才补充网页调研。来源优先级为官方文档、
标准组织、产品操作指南、维护者仓库、真实案例和问题记录。不得把模型常识写成已核实
事实；每个任务原型必须引用直接支持它的 HTTP(S) 来源。

对每个现实任务原型回答：谁在什么情况下开始工作、最终业务目标是什么、需要哪些
证据、必须遵守哪些硬约束、主要过程是什么、完成后交付什么，以及现实中通常怎样失败。
任务原型描述一种真实工作，不包含当前环境中的具体记录 ID、标准答案或 Python 实现。

然后进行环境支持映射：

1. `record_sets/relationships/filesystem_scopes/tools` 只能使用
   `environment.public.json` 中真实存在的标识；
2. 现实工作的一部分如果当前环境没有数据或工具支持，写入
   `unsupported_requirements`；
3. 只有所有必要环节均可通过当前公开工具完成时，`generatable` 才能为 true；
4. 不得因为主题相似就宣称环境支持，也不得假设联网、人工沟通或未声明的外部系统；
5. 不要为了让原型可生成而删除现实任务的核心目标。缺能力时应如实标记 false。

至少交付一个 `generatable=true` 的原型，否则说明当前环境无法生成可靠任务。不同原型
必须具有不同触发场景或业务结果，不能只更换对象名称。

最终只写 `task_research.json`。写完后按 `task_research.schema.json` 自检；不要修改
输入文件，不要在最终回复中重复 JSON 内容。
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
    if generatable == 0:
        errors.append("至少需要一个 environment_support.generatable=true 的任务原型")
    return errors


def _existing_research(step1_path: Path) -> Any:
    receipt = read_json(step1_path)
    value = receipt.get("scenario_research") if isinstance(receipt, dict) else None
    if not value:
        return None
    path = Path(str(value))
    resolved = path if path.is_absolute() else step1_path.resolve().parent / path
    return read_json(resolved) if resolved.is_file() else None


def run_step2(
    *,
    step1_path: Path,
    output_dir: Path,
    agent: TaskResearchAgent | None,
    research_fixture_path: Path | None = None,
    max_attempts: int = 3,
) -> Step2Result:
    """调研并接受一份通过环境引用检查的任务原型集合。"""
    if max_attempts < 1:
        raise ValueError("max_attempts 必须至少为 1")
    package = load_frozen_package(step1_path)
    public_environment = package.public_environment()
    env_id = str(package.environment["environment_id"])
    schema = read_json(Path(__file__).resolve().parents[1] / "schemas" / "task_research.schema.json")
    output_dir = output_dir.resolve()
    output_path = output_dir / "step2_task_research.json"
    attempts_root = output_dir / "step2_research"
    existing_research = _existing_research(step1_path)
    failures: list[dict[str, Any]] = []

    attempts = 1 if research_fixture_path is not None else max_attempts
    for attempt in range(1, attempts + 1):
        audit_dir = attempts_root / f"attempt_{attempt:02d}"
        if audit_dir.exists():
            shutil.rmtree(audit_dir)
        with tempfile.TemporaryDirectory(
            prefix="agent-world-program-step2-"
        ) as temporary:
            attempt_dir = Path(temporary)
            write_json(attempt_dir / "environment.public.json", public_environment)
            receipt = read_json(step1_path)
            write_json(
                attempt_dir / "initial_state.summary.json",
                receipt.get("initial_state", {}),
            )
            write_json(attempt_dir / "task_research.schema.json", schema)
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
                    agent.run(prompt, working_directory=attempt_dir)
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
        return Step2Result(
            output_path,
            len(archetypes),
            sum(
                item["environment_support"]["generatable"] is True
                for item in archetypes
            ),
        )

    write_json(output_dir / "step2_task_research_failures.json", failures)
    raise RuntimeError(f"Step 2 在 {attempts} 次尝试后仍未得到有效任务调研")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--research-fixture", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
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
    result = run_step2(
        step1_path=arguments.step1_path,
        output_dir=arguments.output_dir,
        agent=agent,
        research_fixture_path=arguments.research_fixture,
        max_attempts=arguments.max_attempts,
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
