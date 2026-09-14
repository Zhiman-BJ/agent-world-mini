from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from utils.io import write_json

from .runtime import ToolPackage, ToolRuntime, state_diff


PROVENANCE_FILES = (
    "scenario_research.json",
    "integration_plan.json",
    "integration_profile.json",
    "field_review.json",
    "source_inventory.json",
    "quality_profile.json",
    "source_manifest.json",
    "source_research.json",
)

TOOL_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "validation" / "tool.schema.json"
)


class ToolCodingAgent(Protocol):
    """The file-oriented coding-agent surface used by ToolGen."""

    def run(self, prompt: str, *, working_directory: Path) -> str: ...


class ToolGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolGenerationResult:
    package_root: Path
    environment_path: Path
    tools_path: Path
    action_plan_path: Path
    validation_path: Path
    grounding_path: Path
    tool_names: tuple[str, ...]


class ToolGenerator:
    """Turn one DataGen package into an executable environment."""

    def __init__(
        self,
        agent: ToolCodingAgent,
        *,
        draft_agent: ToolCodingAgent | None = None,
        draft_batch_size: int = 5,
        max_repairs: int = 1,
        software_repair_attempts: int = 3,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs 不能小于 0")
        if draft_batch_size < 1:
            raise ValueError("draft_batch_size 必须至少为 1")
        if software_repair_attempts < 0:
            raise ValueError("software_repair_attempts 不能小于 0")
        self.agent = agent
        self.draft_agent = draft_agent or agent
        self.draft_batch_size = draft_batch_size
        self.max_repairs = max_repairs
        self.software_repair_attempts = software_repair_attempts

    def generate(
        self,
        package_path: Path,
        *,
        tool_hints: list[dict[str, Any]] | None = None,
    ) -> ToolGenerationResult:
        package_root, environment = self._load_data_environment(package_path)
        output_dir = package_root / "tool_generation"
        drafts_dir = output_dir / "drafts"
        self._prepare_agent_workspace(
            package_root,
            environment,
            output_dir,
            drafts_dir,
            tool_hints or [],
        )

        inventory_path = output_dir / "capability_inventory.json"
        inventory_done_path = output_dir / "inventory_done.json"
        inventory_error: ToolGenerationError | None = None
        if inventory_path.is_file():
            inventory = None
            try:
                inventory = self._load_capability_inventory(environment, inventory_path)
            except ToolGenerationError as error:
                inventory_error = error
        else:
            inventory = None
        if inventory is None and inventory_error is None:
            self._run_agent_until(
                self._build_inventory_prompt(package_root),
                working_directory=output_dir,
                required_path=inventory_done_path,
            )
            if not inventory_done_path.is_file():
                raise ToolGenerationError("能力盘点 Agent 没有写入 inventory_done.json")
        if inventory is None:
            try:
                inventory = self._load_capability_inventory(environment, inventory_path)
            except ToolGenerationError as error:
                inventory_error = error
                for repair_round in range(1, self.max_repairs + 1):
                    repair_done_path = output_dir / "inventory_repair_done.json"
                    repair_done_path.unlink(missing_ok=True)
                    try:
                        self._run_agent_until(
                            self._build_inventory_repair_prompt(repair_round, inventory_error),
                            working_directory=output_dir,
                            required_path=repair_done_path,
                        )
                    except Exception as error:
                        if not getattr(error, "retryable", False):
                            raise
                        break
                    if not repair_done_path.is_file():
                        break
                    try:
                        inventory = self._load_capability_inventory(environment, inventory_path)
                        break
                    except ToolGenerationError as error:
                        inventory_error = error
                if inventory is None:
                    inventory = self._load_capability_inventory(
                        environment, inventory_path, allow_partial=True
                    )
        self._prepare_software(package_root)
        action_plan_path = output_dir / "action_plan.json"
        if action_plan_path.is_file():
            try:
                actions = self._load_action_plan(environment, inventory, action_plan_path)
            except ToolGenerationError:
                actions = None
        else:
            actions = None
        if actions is None:
            self._run_agent_until(
                self._build_action_plan_prompt(package_root),
                working_directory=output_dir,
                required_path=action_plan_path,
            )
            actions = self._load_action_plan(environment, inventory, action_plan_path)

        generation_failures = self._generate_draft_batches(
            package_root, output_dir, drafts_dir, actions, status="generating"
        )
        for retry_round in range(1, self.max_repairs + 1):
            retry_actions = [
                action for action in actions if str(action["name"]) in generation_failures
            ]
            if not retry_actions:
                break
            generation_failures = self._generate_draft_batches(
                package_root,
                output_dir,
                drafts_dir,
                actions,
                status=f"retrying_{retry_round}",
                selected=retry_actions,
            )

        completed_names = [
            str(action["name"])
            for action in actions
            if self._ensure_draft_ready(
                drafts_dir / f"{action['name']}.json",
                str(action["name"]),
                action,
            )
            is None
        ]
        self._write_progress(
            output_dir,
            actions,
            completed=completed_names,
            failed=list(generation_failures),
            status="drafts_ready" if not generation_failures else "drafts_ready_with_skips",
        )

        _actions, drafts = self._load_agent_drafts(
            environment,
            inventory,
            action_plan_path,
            drafts_dir,
            allow_missing=True,
        )
        self._prepare_software(package_root)
        reports = [
            {"tool": name, "status": "skipped", "failures": [reason], "tests": []}
            for name, reason in generation_failures.items()
        ] + self._validate(package_root, environment, drafts)
        validation_path = output_dir / "tool_validation.json"
        write_json(
            validation_path,
            {"environment_id": environment["environment_id"], "reports": reports},
        )

        for repair_round in range(1, self.max_repairs + 1):
            failed = [report for report in reports if report["status"] == "rejected"]
            if not failed:
                break
            repair_done_path = output_dir / "repair_done.json"
            repair_done_path.unlink(missing_ok=True)
            try:
                self._run_agent_until(
                    self._build_repair_prompt(repair_round, failed),
                    working_directory=output_dir,
                    required_path=repair_done_path,
                    agent=self.draft_agent,
                )
            except Exception as error:
                write_json(output_dir / "repair_error.json", {
                    "round": repair_round, "error": f"{type(error).__name__}: {error}",
                })
                break
            if not repair_done_path.is_file():
                write_json(output_dir / "repair_error.json", {
                    "round": repair_round, "error": "工具修复 Agent 没有写入 repair_done.json",
                })
                break
            passed = {report["tool"] for report in reports if report["status"] == "passed"}
            passed_drafts = [draft for draft in drafts if draft["tool"]["name"] in passed]
            try:
                _actions, drafts = self._load_agent_drafts(
                    environment,
                    inventory,
                    action_plan_path,
                    drafts_dir,
                    allow_missing=True,
                )
            except Exception as error:
                write_json(output_dir / "repair_error.json", {
                    "round": repair_round, "error": f"{type(error).__name__}: {error}",
                })
                break
            drafts = passed_drafts + [draft for draft in drafts if draft["tool"]["name"] not in passed]
            available = {draft["tool"]["name"] for draft in drafts}
            generation_failures = {
                action["name"]: generation_failures.get(action["name"], "missing_or_invalid_draft_after_repair")
                for action in actions if action["name"] not in available
            }
            self._prepare_software(package_root)
            reports = self._validate(package_root, environment, drafts)
            reports = [
                {"tool": name, "status": "skipped", "failures": [reason], "tests": []}
                for name, reason in generation_failures.items()
            ] + reports
            write_json(
                validation_path,
                {"environment_id": environment["environment_id"], "reports": reports},
            )

        accepted_names = {
            str(report["tool"])
            for report in reports
            if report["status"] == "passed"
        }
        tools = [
            deepcopy(draft["tool"])
            for draft in drafts
            if str(draft["tool"]["name"]) in accepted_names
        ]
        if not tools:
            names = ", ".join(str(report["tool"]) for report in reports)
            raise ToolGenerationError(f"没有工具通过实际执行验证：{names}")

        grounding_path = output_dir / "tool_grounding.json"
        actions_by_name = {str(action["name"]): action for action in actions}
        write_json(
            grounding_path,
            {
                "environment_id": environment["environment_id"],
                "tools": [
                    {
                        "name": str(tool["name"]),
                        "capability_ids": actions_by_name[str(tool["name"])]["capability_ids"],
                        "reality_evidence": actions_by_name[str(tool["name"])]["reality_evidence"],
                        "execution_backends": actions_by_name[str(tool["name"])]["execution_backends"],
                    }
                    for tool in tools
                ],
            },
        )

        environment_path = package_root / "environment.json"
        tools_path = package_root / "tools.json"
        write_json(
            tools_path,
            {
                "schema_version": "1.0",
                "environment_id": environment["environment_id"],
                "tools": tools,
            },
        )
        ToolPackage.load(package_root)
        return ToolGenerationResult(
            package_root=package_root,
            environment_path=environment_path,
            tools_path=tools_path,
            action_plan_path=action_plan_path,
            validation_path=validation_path,
            grounding_path=grounding_path,
            tool_names=tuple(str(tool["name"]) for tool in tools),
        )

    @staticmethod
    def _write_progress(
        output_dir: Path,
        actions: list[dict[str, Any]],
        *,
        completed: list[str] | None = None,
        current: str | None = None,
        failed: list[str] | None = None,
        status: str,
    ) -> None:
        if completed is None:
            completed = [
                str(action["name"])
                for action in actions
                if (output_dir / "drafts" / f"{action['name']}.json").is_file()
            ]
        write_json(
            output_dir / "progress.json",
            {
                "status": status,
                "completed_actions": completed,
                "current_action": current,
                "failed_actions": failed or [],
                "total_actions": len(actions),
            },
        )

    def _generate_draft_batches(
        self,
        package_root: Path,
        output_dir: Path,
        drafts_dir: Path,
        actions: list[dict[str, Any]],
        *,
        status: str,
        selected: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        targets = selected if selected is not None else actions
        pending = [
            action
            for action in targets
            if self._ensure_draft_ready(
                drafts_dir / f"{action['name']}.json",
                str(action["name"]),
                action,
            )
            is not None
        ]
        failures: dict[str, str] = {}
        for start in range(0, len(pending), self.draft_batch_size):
            batch = pending[start : start + self.draft_batch_size]
            names = [str(action["name"]) for action in batch]
            paths = tuple(drafts_dir / f"{name}.json" for name in names)
            self._write_progress(
                output_dir,
                actions,
                current=", ".join(names),
                failed=list(failures),
                status=status,
            )
            agent_error: str | None = None
            try:
                run_until_files = getattr(self.draft_agent, "run_until_files", None)
                if callable(run_until_files):
                    run_until_files(
                        self._build_action_batch_prompt(package_root, batch),
                        working_directory=output_dir,
                        required_paths=paths,
                    )
                else:
                    self.draft_agent.run(
                        self._build_action_batch_prompt(package_root, batch),
                        working_directory=output_dir,
                    )
            except Exception as error:
                agent_error = f"agent_error:{type(error).__name__}: {error}"
            for name, path in zip(names, paths):
                action = next(item for item in batch if str(item["name"]) == name)
                error = self._ensure_draft_ready(path, name, action)
                if error is not None:
                    failures[name] = agent_error or error
                    if path.is_file():
                        history = output_dir / "draft_history"
                        history.mkdir(exist_ok=True)
                        version = len(list(history.glob(f"{name}.*.json"))) + 1
                        path.rename(history / f"{name}.{version}.json")
            self._write_progress(
                output_dir,
                actions,
                current=None,
                failed=list(failures),
                status=status,
            )
        return failures

    @staticmethod
    def _ensure_draft_ready(
        path: Path,
        name: str,
        action: dict[str, Any] | None = None,
    ) -> str | None:
        if not path.is_file():
            return f"missing_draft:{path.name}"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return f"invalid_json:{error}"
        if not isinstance(document, dict):
            return "draft_root_must_be_object"
        if isinstance(document.get("tool"), dict):
            tool = document["tool"]
        elif isinstance(document.get("name"), str):
            tool = {
                key: document[key]
                for key in ("name", "description", "usageConditions", "inputSchema", "outputSchema", "internal")
                if key in document
            }
            document = {"tool": tool, "tests": document.get("tests", [])}
            write_json(path, document)
        else:
            return "draft_missing_tool"
        if tool.get("name") != name:
            return f"draft_name_mismatch:{tool.get('name')}"
        if not isinstance(tool.get("usageConditions"), dict):
            return "draft_missing_usage_conditions"
        if action is not None:
            if tool["usageConditions"] != action["usageConditions"]:
                return "draft_usage_conditions_mismatch"
            input_fields = set((tool.get("inputSchema") or {}).get("properties", {}))
            condition_fields = {
                field
                for item in action["usageConditions"]["targetObjects"]
                for field in item["identifiedBy"]
            }
            missing_fields = sorted(condition_fields - input_fields)
            if missing_fields:
                return "draft_condition_fields_missing:" + ",".join(missing_fields)
        return None

    def _run_agent_until(
        self,
        prompt: str,
        *,
        working_directory: Path,
        required_path: Path,
        agent: ToolCodingAgent | None = None,
    ) -> str:
        active_agent = agent or self.agent
        run_until_files = getattr(active_agent, "run_until_files", None)
        for attempt in range(2):
            try:
                if callable(run_until_files):
                    result = run_until_files(
                        prompt,
                        working_directory=working_directory,
                        required_paths=(required_path,),
                    )
                else:
                    result = active_agent.run(prompt, working_directory=working_directory)
                if required_path.is_file() or attempt:
                    return result
                write_json(working_directory / "agent_retry.json", {
                    "required_file": required_path.name,
                    "error": "Agent 会话结束但未交付阶段文件",
                    "retry": 1,
                })
                prompt += "\n上次会话未交付要求的文件。先检查已有成果，使用本会话提供的终端和文件工具继续完成本阶段。"
            except Exception as error:
                if not getattr(error, "retryable", False) or attempt:
                    raise
                write_json(working_directory / "agent_retry.json", {
                    "required_file": required_path.name,
                    "error": str(error),
                    "retry": 1,
                })
                prompt += "\n上次会话因临时故障中断。先检查已有文件，沿用已经完成的内容，继续完成本阶段并写入要求的交付文件。"

    @staticmethod
    def _load_data_environment(package_path: Path) -> tuple[Path, dict[str, Any]]:
        environment_path = package_path / "environment.json" if package_path.is_dir() else package_path
        if not environment_path.is_file():
            raise FileNotFoundError(f"找不到环境文件：{environment_path}")
        package_root = environment_path.parent.resolve()
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        if not isinstance(environment, dict):
            raise ToolGenerationError("environment.json 根节点必须是 object")
        required = {
            "schema_version",
            "environment_id",
            "name",
            "summary",
            "description",
            "record_sets",
            "relationships",
            "filesystem_scopes",
        }
        missing = sorted(required - set(environment))
        if missing:
            raise ToolGenerationError(f"环境缺少字段：{', '.join(missing)}")
        if environment.get("schema_version") != "2.0":
            raise ToolGenerationError("ToolGen 需要 DataGen v2 环境包")
        try:
            ToolPackage.load(package_root, tools=[_draft_example()["tool"]])
        except (OSError, ValueError) as error:
            raise ToolGenerationError(str(error)) from error
        return package_root, environment

    @staticmethod
    def _file_inventory(root: Path) -> list[dict[str, Any]]:
        if not root.is_dir():
            return []
        return [
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
            }
            for path in sorted(item for item in root.rglob("*") if item.is_file())
        ]

    def _prepare_agent_workspace(
        self,
        package_root: Path,
        environment: dict[str, Any],
        output_dir: Path,
        drafts_dir: Path,
        tool_hints: list[dict[str, Any]],
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        drafts_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "inventory_repair_done.json",
            "repair_done.json",
        ):
            (output_dir / name).unlink(missing_ok=True)

        provenance = package_root / "provenance"
        provenance_files = [
            f"../provenance/{name}"
            for name in PROVENANCE_FILES
            if (provenance / name).is_file()
        ]
        reference_tools = list(tool_hints)
        scenario_path = provenance / "scenario_research.json"
        if scenario_path.is_file():
            try:
                scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                scenario = {}
            for item in scenario.get("tools", []):
                if isinstance(item, dict):
                    reference_tools.append(dict(item))
        reference_tools = list(
            {
                str(item.get("name")): item
                for item in reference_tools
                if isinstance(item, dict) and item.get("name")
            }.values()
        )
        scopes_root = package_root / "state/filesystem_scopes"
        context = {
            "environment_id": environment["environment_id"],
            "environment_path": "../environment.json",
            "environment_context_path": (
                "../environment.md" if (package_root / "environment.md").is_file() else None
            ),
            "records_database_path": (
                "../state/records.sqlite"
                if (package_root / "state/records.sqlite").is_file()
                else None
            ),
            "filesystem_scopes_path": "../state/filesystem_scopes",
            "provenance_files": provenance_files,
            "scope_files": self._file_inventory(scopes_root),
            "reference_tools_path": "reference_tools.json",
            "runtime_api_path": "runtime_api.md",
            "tool_schema_path": "tool.schema.json",
            "draft_example_path": "draft_example.json",
        }
        from .software import COMMON_MODULES
        context["software_guide_path"] = "software_guide.md"
        context["software_environment_path"] = "software_environment.json"
        write_json(output_dir / "context.json", context)
        write_json(output_dir / "common_software.json", COMMON_MODULES)
        (output_dir / "software_guide.md").write_text('''# 软件准备与执行

common_software.json 是按用途整理的通用依赖目录。领域核心包由当前环境根据官方资料选择。
software_plan.json 示例：
{"python":"3.11","common_modules":["numerical"],"python_packages":[{"name":"软件包名称","version":">=1,<2","purpose":"所需接口"}],"node_packages":[]}
包名和版本应根据注册表核实；python 指定该软件所需的解释器。依赖可以用字符串或 name/version 对象，Node 字符串采用 package@version。
主程序安装失败后会返回错误让智能体修正计划，再自动安装。也可使用当前代码的 Python -m env_gen.tool_gen.software install <环境包目录> 主动安装。
安装位置在 software_environment.json 的 python/prefix/root。草稿和修复中的 Python 探测使用这里的 python，Node 包在 root/node/node_modules 下。可以联网读取官方源码、调用示例及版本信息，并在本环境软件目录安装辅助程序。
草稿验证自动使用所选 Python。工具用 context.software_root 定位辅助程序及 Node 包，业务文件仍通过 context.scope_root 访问任务副本。
安装是运行条件，不是业务能力证明；编写时记录和使用真实接口。缺包错误通过补齐安装解决，专业功能保持其操作含义。
下游先用 python -m env_gen.tool_gen.software prepare <包目录> 恢复软件，再用 python -m env_gen.tool_gen.software exec <包目录> -- <脚本与参数> 在该环境执行。
''', encoding="utf-8")
        write_json(output_dir / "reference_tools.json", reference_tools)
        (output_dir / "runtime_api.md").write_text(_runtime_api(), encoding="utf-8")
        shutil.copy2(TOOL_SCHEMA_PATH, output_dir / "tool.schema.json")
        write_json(output_dir / "draft_example.json", _draft_example())

    @staticmethod
    def _build_inventory_prompt(package_root: Path) -> str:
        return f"""你负责盘点一个 DataGen 环境真正能够实现的工具能力。当前目录是 {package_root.name}/tool_generation。

先读 environment.md（若存在）和 environment.json，理解业务场景、Record Set、已验证关系、Filesystem Scope 与 read_only/copy_on_write 边界。再按需抽样 state/records.sqlite 中的真实记录并查看 Scope 中的真实文件。reference_tools.json 汇总了种子和 DataGen 场景研究中的参考工具；联网核对专业系统的官方文档、官方 API/CLI 或真实产品工作流。若内置搜索服务不可用，使用允许联网的 curl、python urllib 或 git 直接读取官方页面，不要反复重试搜索服务。若专业操作需要额外软件，将软件名称、版本和用途写入 software_plan.json；通用计算、表格、文档、图像和 HDF5 依赖写入 common_modules，领域核心软件写入 python_packages 或 node_packages。不要因为尚未安装就把能力判为不可实现。

逐项盘点专业系统中真实存在、并且当前环境可以真实执行的工作面：每个 Record Set 的按键获取、搜索筛选、比较统计和适合其字段的领域分析；每条 Relationship 的关联查询或业务操作；每个 Scope 中现实文件格式支持的查看、校验、计算、转换和产物操作；以及跨记录与文件的常见工作流。copy_on_write 只说明技术上允许修改；创建、更新、删除或状态变化仍须有现实业务依据。专业软件操作必须调用真实 API、CLI 或操作真实项目文件，不能用自建 JSON 状态模拟软件动作。读取复杂 JSON 时先查看实际类型和字段；某个探索脚本报错时记录错误并换用更简单的读取方法继续，不要让单个样本阻塞盘点。先写 software_plan.json 和 capability_inventory.json，再进行更深入的补充调研；软件计划至少包含 common_modules、python_packages、node_packages 三个数组，缺少额外软件时写空数组。

能力只有同时具备现实操作依据和当前环境执行后端时才能 decision=implement。现实依据可来自 reference_tools.json 中已有工具、官方文档或公认的标准数据/文件操作；standard_operation 只用于读取、写入、统计、转换和比较文件或表格等领域无关操作，领域业务动作必须有参考工具或官方文档。execution_backends 必须指向当前 environment.json 声明的 Record Set 或 Filesystem Scope。参考工具字段不同时保留原业务含义并映射到本地字段，可 direct、adapted、narrowed 或 composed；缺少关键对象、状态或执行接口时 decision=skip。

每项能力标明 operation_kind：standard_data 表示通用数据/文件操作（查询、分组计数、比较、转换等），domain 表示专业业务操作（如解决工单、部署合约）。分类取决于动作含义，与 family 无关；在图标目录上做分组计数仍是通用数据操作。domain 必须引用参考工具或官方文档；standard_data 可以引用具体标准操作，同时说明本地字段或文件依据。

能力边界以真实用户目标为准。过滤字段、排序方式或输出格式不同但目的相同的，合并成一个参数化能力；用户目标、主要输入、返回结果或副作用不同的，保留为不同能力。不要把读取文件、执行 SQL 等内部步骤单独算作业务能力，也不要创造当前数据无法执行的在线动作。成熟而丰富的环境应覆盖完整的常用工作面。

写 capability_inventory.json。每项 implement 能力使用以下格式：{{"capability_id":"cap_...","name":"业务动作","family":"query|analysis|relation|state_change|file","asset_ids":["record_set_id 或 scope_id"],"evidence":["本地字段、关系或文件依据"],"reference_tools":["可选工具名"],"reality_evidence":[{{"source_type":"reference_tool|official_documentation|standard_operation","source":"参考工具名、官方 URL 或标准操作名称","operation":"来源实际支持的动作","adaptation":"direct|adapted|narrowed|composed"}}],"execution_backends":[{{"kind":"record_store|filesystem_scope","asset_id":"environment.json 中的资产 ID"}}],"decision":"implement","reason":"现实动作如何由当前环境执行"}}。skip 能力保留 reason 即可。盘点完成后写 inventory_done.json，内容为 {{"status":"ready"}}，然后结束。不要编写工具，不要修改上游环境包。"""

    @staticmethod
    def _build_action_plan_prompt(package_root: Path) -> str:
        return f"""你负责为 {package_root.name} 安排工具动作。

读取 approved_capabilities.json 和 context.json，为其中全部 decision=implement 的能力安排业务工具。approved_capabilities.json 是逐项检查后通过的清单，待修复项另存于 capability_review.json。一个工具应完成一个完整、常用的用户目的：同一对象的多种筛选、排序或统计维度可以参数化，目标或副作用不同的动作分别保留。不要把检索、分析、编辑和导出塞进同一个万能工具，也不要把一个正常动作拆成字段级小工具。

只写 action_plan.json，格式为 {{"environment_id":"...","actions":[{{"name":"snake_case","description":"...","capability_ids":["cap_..."],"asset_ids":["record_set_id 或 scope_id"],"effect":"read|write","usageConditions":{{"targetResources":["asset_id"],"targetObjects":[{{"objectType":"业务对象","identifiedBy":["inputSchema 参数名"]}}],"preconditions":["调用前必须满足的对象状态"],"sideEffects":["成功调用后的可观察变化；只读工具为空数组"]}}}}]}}。每个 implement 能力只归入一个动作。条件描述业务状态，不写 Python、SQL 或内部路径。不要写工具草稿，不要修改上游环境包。写完后结束。"""

    @staticmethod
    def _build_action_batch_prompt(
        package_root: Path, actions: list[dict[str, Any]]
    ) -> str:
        action_json = json.dumps(actions, ensure_ascii=False, indent=2)
        action_json += "\n先读 software_guide.md 与 software_environment.json（若存在），在已安装的解释器中探测接口和执行示例。缺包时可以补充软件计划并安装；联网查证来源中的具体函数、参数与返回值。"
        names = ", ".join(str(action["name"]) for action in actions)
        return f"""你负责为 {package_root.name} 编写这一组相关工具：{names}。

动作计划如下：
{action_json}

读取 context.json、environment.json、runtime_api.md、tool.schema.json 和 draft_example.json，并只抽样这些动作涉及的真实记录或文件。每个动作分别写入 drafts/<name>.json，格式为 {{"tool": ToolSpec, "tests": [...]}}。将动作中的 usageConditions 原样写入工具的 usageConditions。工具代码定义 run(arguments, context)，通过 context.records 访问 Record Set，通过 context.scope_root(scope_id) 访问文件；不要直接打开 records.sqlite。专业软件动作必须调用真实接口或处理真实项目文件，不得用另建状态文件模拟。

保持 action_plan 中的业务边界，不再扩展或合并动作。测试参数取自真实状态，至少包含一次正常调用；写操作测试应使 expect_changed=true。完成全部 {len(actions)} 个草稿后结束，不要修改上游环境、状态或其他工具草稿。"""

    @staticmethod
    def _build_inventory_repair_prompt(
        repair_round: int,
        error: ToolGenerationError,
    ) -> str:
        return f"""这是第 {repair_round} 次能力盘点文件修复。capability_inventory.json 当前无法读取：{error}

读取 capability_review.json 中汇总的全部问题，集中修正对应条目。通用表格或文件的读取、分组统计、比较、转换等操作注明 operation_kind=standard_data，并写明具体的标准操作；专业业务操作注明 operation_kind=domain，补充参考工具或官方文档。分类取决于动作含义，与 family 无关；在专业数据上做普通计数仍是通用操作。现实依据或执行后端不足时，补充可核实依据；确实不满足准入条件时将该能力改为 skip 并写明原因。保留其他已完成条目。修正后写 inventory_repair_done.json，内容为 {{"status":"ready"}}，然后结束。"""

    @staticmethod
    def _build_repair_prompt(repair_round: int, failed: list[dict[str, Any]]) -> str:
        names = ", ".join(str(item["tool"]) for item in failed)
        return f"""这是 ToolGen 第 {repair_round} 次工具修复。请读取 tool_validation.json，只处理这些失败工具：{names}。

查看对应 drafts/<name>.json、runtime_api.md、tool.schema.json、实际环境状态和失败原因。先确认 software_environment.json 中的软件是否可用；缺少依赖时修正 software_plan.json 并重新准备，不能把真实专业算法替换成复制、占位或简单字符串处理。直接修正工具代码、Schema 或调用样例。不要改变 action_plan.json，不要修改已经通过的工具，也不要修改上游环境包。修好后写 repair_done.json，内容为 {{"status":"ready"}}，然后结束。"""

    @staticmethod
    def _load_capability_inventory(
        environment: dict[str, Any],
        inventory_path: Path,
        *,
        allow_partial: bool = False,
    ) -> list[dict[str, Any]]:
        if not inventory_path.is_file():
            raise ToolGenerationError("能力盘点 Agent 没有写入 capability_inventory.json")
        try:
            document = json.loads(inventory_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ToolGenerationError(f"capability_inventory.json 不是合法 JSON：{error}") from error
        if isinstance(document, list):
            document = {
                "environment_id": environment["environment_id"],
                "capabilities": document,
            }
            write_json(inventory_path, document)
        values = document.get("capabilities") if isinstance(document, dict) else None
        if not isinstance(values, list) or not values:
            raise ToolGenerationError("capability_inventory.json 没有能力条目")
        asset_ids = _asset_ids(environment)
        backend_assets = {
            "record_store": {
                str(item["record_set_id"])
                for item in environment.get("record_sets", [])
                if isinstance(item, dict) and item.get("record_set_id")
            },
            "filesystem_scope": {
                str(item["scope_id"])
                for item in environment.get("filesystem_scopes", [])
                if isinstance(item, dict) and item.get("scope_id")
            },
        }
        reference_tool_names = _reference_tool_names(inventory_path.parent / "reference_tools.json")
        capabilities: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, value in enumerate(values):
            try:
                capability = ToolGenerator._validate_capability(
                    value, seen, asset_ids, backend_assets, reference_tool_names
                )
            except ToolGenerationError as error:
                rejected.append({"index": index, "capability": value, "error": str(error)})
                continue
            capabilities.append(capability)
            seen.add(capability["capability_id"])
        write_json(inventory_path.parent / "capability_review.json", {
            "accepted_ids": [item["capability_id"] for item in capabilities if item["decision"] == "implement"],
            "rejected": rejected,
        })
        if rejected and not allow_partial:
            raise ToolGenerationError("能力清单待修复条目：\n" + "\n".join(item["error"] for item in rejected))
        if not any(item["decision"] == "implement" for item in capabilities):
            raise ToolGenerationError("能力盘点没有任何可实现能力")
        write_json(inventory_path.parent / "approved_capabilities.json", {
            "environment_id": environment["environment_id"], "capabilities": capabilities,
        })
        return capabilities

    @staticmethod
    def _validate_capability(
        value: Any,
        seen: set[str],
        asset_ids: set[str],
        backend_assets: dict[str, set[str]],
        reference_tool_names: set[str],
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ToolGenerationError("capability_inventory.json 中的能力必须是 object")
        capability_id = str(value.get("capability_id") or "")
        if not _is_snake_case(capability_id) or capability_id in seen:
            raise ToolGenerationError(f"能力 ID 无效或重复：{capability_id}")
        decision = str(value.get("decision") or "")
        if decision not in {"implement", "skip"}:
            raise ToolGenerationError(f"能力 {capability_id} 的 decision 必须是 implement 或 skip")
        capability = dict(value)
        capability["asset_ids"] = [str(item) for item in value.get("asset_ids", []) if str(item) in asset_ids]
        if decision == "implement":
            family = str(value.get("family") or "")
            if family not in {"query", "analysis", "relation", "state_change", "file"}:
                raise ToolGenerationError(f"能力 {capability_id} 的 family 无效：{family}")
            capability["reality_evidence"] = _validate_reality_evidence(
                capability_id, value.get("reality_evidence"), reference_tool_names,
            )
            capability["execution_backends"] = _validate_execution_backends(
                capability_id, value.get("execution_backends"), backend_assets,
            )
            operation_kind = value.get("operation_kind", "standard_data" if family == "file" else "domain")
            if operation_kind not in {"standard_data", "domain"}:
                raise ToolGenerationError(f"能力 {capability_id} 的 operation_kind 无效")
            if operation_kind == "domain" and all(
                item["source_type"] == "standard_operation" for item in capability["reality_evidence"]
            ):
                raise ToolGenerationError(f"领域能力 {capability_id} 需要参考工具或官方文档依据；通用数据操作应明确 operation_kind=standard_data")
        return capability

    @staticmethod
    def _load_agent_drafts(
        environment: dict[str, Any],
        inventory: list[dict[str, Any]],
        action_plan_path: Path,
        drafts_dir: Path,
        *,
        allow_missing: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        actions = ToolGenerator._load_action_plan(environment, inventory, action_plan_path)

        drafts: list[dict[str, Any]] = []
        for action in actions:
            name = str(action["name"])
            draft_path = drafts_dir / f"{name}.json"
            if not draft_path.is_file():
                if allow_missing:
                    continue
                raise ToolGenerationError(f"缺少工具草稿：{draft_path}")
            try:
                draft = json.loads(draft_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                if allow_missing:
                    continue
                raise ToolGenerationError(f"工具草稿 {name} 不是合法 JSON：{error}") from error
            if not isinstance(draft, dict):
                if allow_missing:
                    continue
                raise ToolGenerationError(f"工具草稿 {name} 根节点必须是 object")
            if not isinstance(draft.get("tool"), dict):
                if isinstance(draft.get("name"), str):
                    tool = {
                        key: draft[key]
                        for key in ("name", "description", "usageConditions", "inputSchema", "outputSchema", "internal")
                        if key in draft
                    }
                    draft = {"tool": tool, "tests": draft.get("tests", [])}
                    write_json(draft_path, draft)
                elif allow_missing:
                    continue
                else:
                    raise ToolGenerationError(f"工具草稿 {name} 缺少 tool")
            if draft["tool"].get("name") != name:
                if allow_missing:
                    continue
                raise ToolGenerationError(f"工具草稿名称不匹配：{name}")
            output_schema = draft["tool"].get("outputSchema")
            if isinstance(output_schema, dict) and "$defs" in output_schema:
                try:
                    expanded = _inline_output_schema(output_schema)
                except ValueError:
                    pass  # Keep unsupported references visible to the normal validator.
                else:
                    history = drafts_dir.parent / "draft_history"
                    history.mkdir(exist_ok=True)
                    version = len(list(history.glob(f"{name}.*.json"))) + 1
                    shutil.copy2(draft_path, history / f"{name}.{version}.json")
                    draft["tool"]["outputSchema"] = expanded
                    write_json(draft_path, draft)
            if draft["tool"].get("usageConditions") != action["usageConditions"]:
                if allow_missing:
                    continue
                raise ToolGenerationError(f"工具草稿 {name} 的 usageConditions 与动作计划不一致")
            input_fields = set(
                (draft["tool"].get("inputSchema") or {}).get("properties", {})
            )
            condition_fields = {
                field
                for item in action["usageConditions"]["targetObjects"]
                for field in item["identifiedBy"]
            }
            if not condition_fields.issubset(input_fields):
                if allow_missing:
                    continue
                missing = ", ".join(sorted(condition_fields - input_fields))
                raise ToolGenerationError(f"工具草稿 {name} 的对象定位字段未在 inputSchema 声明：{missing}")
            drafts.append(
                {
                    "action": action,
                    "tool": dict(draft["tool"]),
                    "tests": _normalize_tests(draft.get("tests")),
                }
            )
        return actions, drafts

    @staticmethod
    def _load_action_plan(
        environment: dict[str, Any],
        inventory: list[dict[str, Any]],
        action_plan_path: Path,
    ) -> list[dict[str, Any]]:
        if not action_plan_path.is_file():
            raise ToolGenerationError("工具生成 Agent 没有写入 action_plan.json")
        try:
            plan = json.loads(action_plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ToolGenerationError(f"action_plan.json 不是合法 JSON：{error}") from error
        raw_actions = plan.get("actions") if isinstance(plan, dict) else None
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ToolGenerationError("action_plan.json 没有业务动作")

        asset_ids = _asset_ids(environment)
        valid_capability_ids = {
            str(item["capability_id"])
            for item in inventory
            if item["decision"] == "implement"
        }
        capabilities_by_id = {
            str(item["capability_id"]): item
            for item in inventory
            if item["decision"] == "implement"
        }
        actions: list[dict[str, Any]] = []
        names: set[str] = set()
        for raw in raw_actions:
            if not isinstance(raw, dict):
                raise ToolGenerationError("action_plan.json 中的动作必须是 object")
            name = str(raw.get("name") or "")
            if not _is_snake_case(name) or name in names:
                raise ToolGenerationError(f"工具动作名无效或重复：{name}")
            action = dict(raw)
            action["capability_ids"] = [
                str(value)
                for value in raw.get("capability_ids", [])
                if str(value) in valid_capability_ids
            ]
            action["asset_ids"] = [
                str(value)
                for value in raw.get("asset_ids", [])
                if str(value) in asset_ids
            ]
            effect = str(raw.get("effect") or "")
            if effect not in {"read", "write"}:
                raise ToolGenerationError(f"工具动作 {name} 的 effect 必须是 read 或 write")
            action["effect"] = effect
            action["usageConditions"] = _validate_usage_conditions(
                name,
                raw.get("usageConditions"),
                asset_ids,
                set(action["asset_ids"]),
            )
            if effect == "read" and action["usageConditions"]["sideEffects"]:
                raise ToolGenerationError(f"只读工具动作 {name} 不应声明 sideEffects")
            if effect == "write" and not action["usageConditions"]["sideEffects"]:
                raise ToolGenerationError(f"写工具动作 {name} 必须声明 sideEffects")
            selected_capabilities = [
                capabilities_by_id[capability_id]
                for capability_id in action["capability_ids"]
            ]
            action["reality_evidence"] = _deduplicate_dicts(
                evidence
                for capability in selected_capabilities
                for evidence in capability["reality_evidence"]
            )
            action["execution_backends"] = _deduplicate_dicts(
                backend
                for capability in selected_capabilities
                for backend in capability["execution_backends"]
            )
            actions.append(action)
            names.add(name)
        required_capabilities = {
            str(item["capability_id"])
            for item in inventory
            if item["decision"] == "implement"
        }
        covered_capabilities = {
            capability_id
            for action in actions
            for capability_id in action["capability_ids"]
        }
        missing = sorted(required_capabilities - covered_capabilities)
        if missing:
            raise ToolGenerationError(
                "action_plan.json 遗漏了可实现能力：" + ", ".join(missing)
            )
        duplicates = sorted(
            capability_id
            for capability_id in covered_capabilities
            if sum(
                capability_id in action["capability_ids"] for action in actions
            )
            > 1
        )
        if duplicates:
            raise ToolGenerationError(
                "action_plan.json 重复安排了能力：" + ", ".join(duplicates)
            )
        return actions

    def _prepare_software(self, package_root: Path) -> None:
        from .software import prepare_software

        output = package_root / "tool_generation"
        if not (output / "software_plan.json").is_file():
            return
        for attempt in range(self.software_repair_attempts + 1):
            try:
                prepare_software(package_root)
                write_json(output / "software_status.json", {"status": "ready", "repairs": attempt})
                return
            except Exception as error:
                write_json(output / "software_status.json", {
                    "status": "repairing" if attempt < self.software_repair_attempts else "blocked",
                    "attempt": attempt, "error": f"{type(error).__name__}: {error}",
                })
                if attempt == self.software_repair_attempts:
                    raise ToolGenerationError(f"软件准备经 {attempt} 次修复仍未完成，产物保留在 {output}") from error
                checkpoint = output / f"software_repair_{attempt + 1}.json"
                checkpoint.unlink(missing_ok=True)
                self._run_agent_until(
                    f"""你负责修复当前环境的软件安装。读取 software_status.json、software_install.log、software_plan.json、context.json 和上游参考工具及 source_research.json。
失败原因：{type(error).__name__}: {error}
通过联网命令查看官方软件包注册表、安装说明和接口，确定正确包名、发布版本、Python 要求与可用功能。可在 tool_generation/software 下安装、执行命令、探测 import 和官方示例；保持操作在当前用户和当前环境内。修订 software_plan.json 后主程序会自动再次安装，缓存和已有文件继续复用。对于临时下载故障可以保持计划并重试。版本选择应保留所需专业接口；业务输入参数不需要预存在数据表中。不要通过删除所需依赖或简化专业算法绕过安装错误。
软件计划格式见 software_guide.md。若发现需要系统服务或外部凭据，记录具体条件。完成后写 {checkpoint.name}，包含 status 和说明本次修复的 changes。""",
                    working_directory=output, required_path=checkpoint,
                )

    def _validate(
        self, package_root: Path, environment: dict[str, Any], drafts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        from .software import validate_in_runtime
        reports = validate_in_runtime(package_root, drafts)
        return reports if reports is not None else self._validate_local(package_root, environment, drafts)

    def _validate_local(
        self,
        package_root: Path,
        environment: dict[str, Any],
        drafts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        reports = []
        tools_by_name = {item["tool"]["name"]: item["tool"] for item in drafts}
        dependencies = {}
        for draft in drafts:
            tool_name = str(draft["tool"]["name"])
            required = {tool_name} | {
                str(call.get("tool"))
                for test in draft["tests"]
                for call in test.get("calls", [])
                if isinstance(call, dict)
            }
            dependencies[tool_name] = required - {tool_name}
            try:
                # Load only the tools this draft's tests actually call.
                missing = required - tools_by_name.keys()
                if missing:
                    raise ValueError("测试依赖工具缺失：" + ", ".join(sorted(missing)))
                package = ToolPackage.load(package_root, tools=[
                    deepcopy(tools_by_name[name]) for name in sorted(required)
                ])
                failures = self._run_tests(package, tool_name, draft["tests"])
            except Exception as error:
                failures = [f"tool_load_error:{type(error).__name__}: {error}"]
            reports.append({"tool": tool_name, "status": "passed" if not failures else "rejected",
                            "failures": failures, "tests": draft["tests"]})
        # A published tool's executable tests must not depend on a rejected tool.
        while True:
            accepted = {report["tool"] for report in reports if report["status"] == "passed"}
            affected = [report for report in reports if report["status"] == "passed"
                        and dependencies[report["tool"]] - accepted]
            if not affected:
                return reports
            for report in affected:
                report["status"] = "rejected"
                report["failures"].append("test_dependency_rejected:" + ",".join(
                    sorted(dependencies[report["tool"]] - accepted)))

    @staticmethod
    def _run_tests(
        package: ToolPackage,
        tool_name: str,
        tests: list[dict[str, Any]],
    ) -> list[str]:
        if not tests:
            return ["missing_executable_test"]
        tool = next(item for item in package.tools if str(item["name"]) == tool_name)
        target_resources = set(tool["usageConditions"]["targetResources"])
        failures: list[str] = []
        for index, test in enumerate(tests):
            calls = test.get("calls")
            if not isinstance(calls, list) or not calls or str(calls[-1].get("tool")) != tool_name:
                failures.append(f"test_{index}:final_call_must_be_{tool_name}")
                continue
            try:
                with ToolRuntime(package) as runtime:
                    before = runtime.snapshot()
                    result: dict[str, Any] | None = None
                    for call in calls:
                        result = runtime.call(str(call["tool"]), dict(call.get("arguments", {})))
                    assert result is not None
                    after = runtime.snapshot()
                    change = state_diff(before, after)
                    changed = any(change.values())
                    changed_assets = {
                        *change["record_sets"],
                        *change["filesystem_scopes"],
                    }
                    unexpected_changes = sorted(changed_assets - target_resources)
                    if unexpected_changes:
                        failures.append(
                            f"test_{index}:changed_undeclared_resources="
                            + ",".join(unexpected_changes)
                        )
                    if bool(result.get("success")) != bool(test.get("expect_success", True)):
                        failures.append(f"test_{index}:unexpected_success={result.get('success')}")
                    if bool(test.get("expect_changed")) != changed:
                        failures.append(
                            f"test_{index}:workspace_change_mismatch="
                            f"expected_{bool(test.get('expect_changed'))}_actual_{changed}"
                        )
                    expected = test.get("expected_data")
                    if isinstance(expected, dict) and not _contains(result.get("data"), expected):
                        failures.append(f"test_{index}:returned_data_does_not_match_expectation")
            except Exception as error:
                failures.append(f"test_{index}:runtime_error:{type(error).__name__}: {error}")
        return failures


def _inline_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Expand plain local references into the contract's inline output format."""
    def expand(value: Any, active: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [expand(item, active) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str):
            if set(value) != {"$ref"} or not reference.startswith("#/$defs/") or reference in active:
                raise ValueError("Reference requires manual schema adaptation")
            target: Any = schema
            try:
                for segment in reference[2:].split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as error:
                raise ValueError("Unresolved local schema reference") from error
            return expand(target, (*active, reference))
        return {key: deepcopy(item) if key in {"const", "enum", "default", "examples"}
                else expand(item, active) for key, item in value.items()}

    return expand({key: value for key, value in schema.items() if key != "$defs"})


def _is_snake_case(value: str) -> bool:
    return bool(value) and value[0].islower() and all(char.islower() or char.isdigit() or char == "_" for char in value) and "__" not in value and not value.endswith("_")


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _contains(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and all(item in actual for item in expected)
    return actual == expected


def _normalize_tests(value: Any) -> list[dict[str, Any]]:
    tests = [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    if tests and all("tool" in item and "calls" not in item for item in tests):
        final = tests[-1]
        normalized = {
            "calls": [
                {"tool": str(item["tool"]), "arguments": dict(item.get("arguments", {}))}
                for item in tests
            ],
            "expect_success": bool(final.get("expect_success", True)),
            "expect_changed": bool(final.get("expect_changed", False)),
        }
        if isinstance(final.get("expected_data"), dict):
            normalized["expected_data"] = dict(final["expected_data"])
        return [normalized]
    return tests


def _asset_ids(environment: dict[str, Any]) -> set[str]:
    return {
        str(item.get("record_set_id") or item.get("scope_id"))
        for item in [
            *environment.get("record_sets", []),
            *environment.get("filesystem_scopes", []),
        ]
        if isinstance(item, dict)
    }


def _reference_tool_names(path: Path) -> set[str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        str(item["name"])
        for item in document
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    } if isinstance(document, list) else set()


def _validate_reality_evidence(
    capability_id: str,
    value: Any,
    reference_tool_names: set[str],
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ToolGenerationError(f"能力 {capability_id} 缺少现实操作依据")
    allowed_sources = {"reference_tool", "official_documentation", "standard_operation"}
    allowed_adaptations = {"direct", "adapted", "narrowed", "composed"}
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ToolGenerationError(f"能力 {capability_id} 的现实操作依据必须是 object")
        source_type = str(item.get("source_type") or "")
        source = str(item.get("source") or "").strip()
        operation = str(item.get("operation") or "").strip()
        adaptation = str(item.get("adaptation") or "")
        if source_type not in allowed_sources or not source or not operation:
            raise ToolGenerationError(f"能力 {capability_id} 的现实操作依据不完整")
        if adaptation not in allowed_adaptations:
            raise ToolGenerationError(f"能力 {capability_id} 的 adaptation 无效：{adaptation}")
        if source_type == "reference_tool" and source not in reference_tool_names:
            raise ToolGenerationError(f"能力 {capability_id} 引用了未知参考工具：{source}")
        if source_type == "official_documentation" and not source.startswith(("https://", "http://")):
            raise ToolGenerationError(f"能力 {capability_id} 的官方文档必须使用 URL")
        result.append({
            "source_type": source_type,
            "source": source,
            "operation": operation,
            "adaptation": adaptation,
        })
    return result


def _validate_execution_backends(
    capability_id: str,
    value: Any,
    backend_assets: dict[str, set[str]],
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ToolGenerationError(f"能力 {capability_id} 缺少真实执行后端")
    allowed = set(backend_assets)
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ToolGenerationError(f"能力 {capability_id} 的执行后端必须是 object")
        kind = str(item.get("kind") or "")
        asset_id = str(item.get("asset_id") or "")
        if kind not in allowed or asset_id not in backend_assets.get(kind, set()):
            raise ToolGenerationError(f"能力 {capability_id} 的执行后端无效：{kind}:{asset_id}")
        result.append({"kind": kind, "asset_id": asset_id})
    return result


def _validate_usage_conditions(
    tool_name: str,
    value: Any,
    asset_ids: set[str],
    action_asset_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolGenerationError(f"工具动作 {tool_name} 缺少 usageConditions")
    target_resources = value.get("targetResources")
    target_objects = value.get("targetObjects")
    preconditions = value.get("preconditions")
    side_effects = value.get("sideEffects")
    if not isinstance(target_resources, list) or not target_resources:
        raise ToolGenerationError(f"工具动作 {tool_name} 缺少 targetResources")
    normalized_resources = [str(item) for item in target_resources]
    if any(item not in asset_ids or item not in action_asset_ids for item in normalized_resources):
        raise ToolGenerationError(f"工具动作 {tool_name} 的 targetResources 与资产依据不一致")
    if not isinstance(target_objects, list) or not target_objects:
        raise ToolGenerationError(f"工具动作 {tool_name} 缺少 targetObjects")
    normalized_objects = []
    for item in target_objects:
        if not isinstance(item, dict) or not str(item.get("objectType") or "").strip():
            raise ToolGenerationError(f"工具动作 {tool_name} 的 targetObjects 无效")
        identified_by = item.get("identifiedBy")
        if not isinstance(identified_by, list) or any(not str(field).strip() for field in identified_by):
            raise ToolGenerationError(f"工具动作 {tool_name} 的 identifiedBy 无效")
        normalized_objects.append({
            "objectType": str(item["objectType"]).strip(),
            "identifiedBy": [str(field).strip() for field in identified_by],
        })
    if not isinstance(preconditions, list) or any(not str(item).strip() for item in preconditions):
        raise ToolGenerationError(f"工具动作 {tool_name} 的 preconditions 无效")
    if not isinstance(side_effects, list) or any(not str(item).strip() for item in side_effects):
        raise ToolGenerationError(f"工具动作 {tool_name} 的 sideEffects 无效")
    return {
        "targetResources": normalized_resources,
        "targetObjects": normalized_objects,
        "preconditions": [str(item).strip() for item in preconditions],
        "sideEffects": [str(item).strip() for item in side_effects],
    }


def _deduplicate_dicts(values: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            result.append(dict(value))
            seen.add(key)
    return result


def _runtime_api() -> str:
    return """# Tool Runtime API

工具代码定义 `run(arguments, context)`，可以使用：

```python
context.environment
context.records.list(record_set_id, filters={}, limit=100, offset=0,
                     order_by=None, descending=False)
context.records.get(record_set_id, key)
context.records.create(record_set_id, record)
context.records.update(record_set_id, key, changes)
context.records.delete(record_set_id, key)
context.scope_root(scope_id)
```

`get` 的 key 必须包含该 Record Set 声明的全部 key_fields。`create` 接收完整记录；
`update` 和 `delete` 返回受影响记录数。`scope_root` 返回任务隔离副本中的 pathlib.Path。
只对 `access=copy_on_write` 的 Record Set 或 Scope 执行写操作。
"""


def _draft_example() -> dict[str, Any]:
    closed_object = {
        "type": "object",
        "properties": {"item_id": {"type": "string"}},
        "required": ["item_id"],
        "additionalProperties": False,
    }
    error_object = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "enum": ["not_found"]},
            "path": {"type": "string"},
            "message": {"type": "string", "minLength": 1},
            "retryable": {"type": "boolean"},
        },
        "required": ["code", "path", "message", "retryable"],
        "additionalProperties": False,
    }
    return {
        "tool": {
            "name": "get_item",
            "description": "Get one item by its ID.",
            "usageConditions": {
                "targetResources": ["items"],
                "targetObjects": [{"objectType": "item", "identifiedBy": ["item_id"]}],
                "preconditions": ["The item exists in the current environment state."],
                "sideEffects": [],
            },
            "inputSchema": closed_object,
            "outputSchema": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean", "const": True},
                            "data": closed_object,
                        },
                        "required": ["success", "data"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean", "const": False},
                            "error": error_object,
                        },
                        "required": ["success", "error"],
                        "additionalProperties": False,
                    },
                ]
            },
            "internal": {
                "code": "def run(arguments, context):\n    return {'success': True, 'data': {'item_id': arguments['item_id']}}"
            },
        },
        "tests": [
            {
                "calls": [{"tool": "get_item", "arguments": {"item_id": "item-1"}}],
                "expect_success": True,
                "expect_changed": False,
                "expected_data": {"item_id": "item-1"},
            }
        ],
    }
