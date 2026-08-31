from __future__ import annotations

import fnmatch
import json
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from task_gen.program_form.loader import CompleteEnvironmentPackage
from task_gen.program_form.runtime import CompleteEnvironmentRuntime, workspace_diff
from utils.io import write_json


PROVENANCE_FILES = (
    "research_request.json",
    "research_report.json",
    "source_inventory.json",
    "data_profile.json",
    "quality_profile.json",
    "sources.json",
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
    action_plan_path: Path
    validation_path: Path
    tool_names: tuple[str, ...]


class ToolGenerator:
    """Turn one DataGen package into an executable environment."""

    def __init__(self, agent: ToolCodingAgent, *, max_repairs: int = 1) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs 不能小于 0")
        self.agent = agent
        self.max_repairs = max_repairs

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
        if inventory_path.is_file():
            inventory = None
            try:
                inventory = self._load_capability_inventory(environment, inventory_path)
            except ToolGenerationError:
                pass
        else:
            inventory = None
        if inventory is None:
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
            except ToolGenerationError as inventory_error:
                for repair_round in range(1, self.max_repairs + 1):
                    repair_done_path = output_dir / "inventory_repair_done.json"
                    repair_done_path.unlink(missing_ok=True)
                    self._run_agent_until(
                        self._build_inventory_repair_prompt(repair_round, inventory_error),
                        working_directory=output_dir,
                        required_path=repair_done_path,
                    )
                    if not repair_done_path.is_file():
                        raise ToolGenerationError("能力盘点修复 Agent 没有写入 inventory_repair_done.json")
                    try:
                        inventory = self._load_capability_inventory(environment, inventory_path)
                        break
                    except ToolGenerationError as error:
                        inventory_error = error
                else:
                    raise inventory_error
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

        self._write_progress(output_dir, actions, status="generating")
        generation_failures: dict[str, str] = {}
        for index, action in enumerate(actions):
            name = str(action["name"])
            draft_path = drafts_dir / f"{name}.json"
            error = self._ensure_draft_ready(draft_path, name)
            if error is None:
                self._write_progress(
                    output_dir,
                    actions,
                    completed=[str(item["name"]) for item in actions[: index + 1] if self._ensure_draft_ready(drafts_dir / f"{item['name']}.json", str(item["name"])) is None],
                    current=None,
                    status="generating" if index + 1 < len(actions) else "drafts_ready",
                )
                continue

            self._write_progress(output_dir, actions, current=name, status="generating")
            try:
                run_until_json = getattr(self.agent, "run_until_json_file", None)
                if callable(run_until_json):
                    run_until_json(
                        self._build_single_action_prompt(package_root, action),
                        working_directory=output_dir,
                        required_path=draft_path,
                    )
                else:
                    self.agent.run(
                        self._build_single_action_prompt(package_root, action),
                        working_directory=output_dir,
                    )
            except Exception as agent_error:
                error = f"agent_error:{type(agent_error).__name__}: {agent_error}"
            else:
                error = self._ensure_draft_ready(draft_path, name)
            if error is not None:
                generation_failures[name] = error
                draft_path.unlink(missing_ok=True)
            self._write_progress(
                output_dir,
                actions,
                completed=[str(item["name"]) for item in actions[: index + 1] if self._ensure_draft_ready(drafts_dir / f"{item['name']}.json", str(item["name"])) is None],
                current=None,
                failed=list(generation_failures),
                status="generating" if index + 1 < len(actions) else "drafts_ready",
            )

        for retry_round in range(1, self.max_repairs + 1):
            for action in actions:
                name = str(action["name"])
                if name not in generation_failures:
                    continue
                draft_path = drafts_dir / f"{name}.json"
                self._write_progress(output_dir, actions, current=name, failed=list(generation_failures), status="retrying")
                try:
                    run_until_json = getattr(self.agent, "run_until_json_file", None)
                    if callable(run_until_json):
                        run_until_json(
                            self._build_single_action_prompt(package_root, action),
                            working_directory=output_dir,
                            required_path=draft_path,
                        )
                    else:
                        self.agent.run(
                            self._build_single_action_prompt(package_root, action),
                            working_directory=output_dir,
                        )
                except Exception as agent_error:
                    generation_failures[name] = f"retry_{retry_round}:agent_error:{type(agent_error).__name__}: {agent_error}"
                    draft_path.unlink(missing_ok=True)
                    continue
                error = self._ensure_draft_ready(draft_path, name)
                if error is None:
                    del generation_failures[name]
                else:
                    generation_failures[name] = f"retry_{retry_round}:{error}"
                    draft_path.unlink(missing_ok=True)

        completed_names = [
            str(action["name"])
            for action in actions
            if self._ensure_draft_ready(drafts_dir / f"{action['name']}.json", str(action["name"])) is None
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
            self._run_agent_until(
                self._build_repair_prompt(repair_round, failed),
                working_directory=output_dir,
                required_path=repair_done_path,
            )
            if not repair_done_path.is_file():
                raise ToolGenerationError("工具修复 Agent 没有写入 repair_done.json")
            _actions, drafts = self._load_agent_drafts(
                environment,
                inventory,
                action_plan_path,
                drafts_dir,
                allow_missing=True,
            )
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

        complete = deepcopy(environment) | {"tools": tools}
        environment_path = package_root / "environment.json"
        write_json(environment_path, complete)
        CompleteEnvironmentPackage.load(package_root)
        return ToolGenerationResult(
            package_root=package_root,
            environment_path=environment_path,
            action_plan_path=action_plan_path,
            validation_path=validation_path,
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

    @staticmethod
    def _ensure_draft_ready(path: Path, name: str) -> str | None:
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
                for key in ("name", "description", "inputSchema", "outputSchema", "internal")
                if key in document
            }
            document = {"tool": tool, "tests": document.get("tests", [])}
            write_json(path, document)
        else:
            return "draft_missing_tool"
        if tool.get("name") != name:
            return f"draft_name_mismatch:{tool.get('name')}"
        return None

    def _run_agent_until(
        self,
        prompt: str,
        *,
        working_directory: Path,
        required_path: Path,
    ) -> str:
        run_until_files = getattr(self.agent, "run_until_files", None)
        if callable(run_until_files):
            return run_until_files(
                prompt,
                working_directory=working_directory,
                required_paths=(required_path,),
            )
        return self.agent.run(prompt, working_directory=working_directory)

    @staticmethod
    def _load_data_environment(package_path: Path) -> tuple[Path, dict[str, Any]]:
        environment_path = package_path / "environment.json" if package_path.is_dir() else package_path
        if not environment_path.is_file():
            raise FileNotFoundError(f"找不到环境文件：{environment_path}")
        package_root = environment_path.parent.resolve()
        if not (package_root / "workspace").is_dir():
            raise ToolGenerationError(f"环境包缺少 workspace/：{package_root}")
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        if not isinstance(environment, dict):
            raise ToolGenerationError("environment.json 根节点必须是 object")
        if "tools" in environment:
            raise ToolGenerationError("该环境已经包含 tools；请为它创建新的完整环境包，而不是重复生成")
        required = {"schema_version", "environment_id", "name", "description", "resources", "rules"}
        missing = sorted(required - set(environment))
        if missing:
            raise ToolGenerationError(f"环境缺少字段：{', '.join(missing)}")
        return package_root, environment

    @staticmethod
    def _file_inventory(root: Path) -> list[dict[str, Any]]:
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
        context = {
            "environment_id": environment["environment_id"],
            "environment_path": "../environment.json",
            "workspace_path": "../workspace",
            "provenance_files": provenance_files,
            "workspace_files": self._file_inventory(package_root / "workspace"),
            "reference_tools_path": "reference_tools.json",
            "tool_schema_path": "tool.schema.json",
            "draft_example_path": "draft_example.json",
        }
        write_json(output_dir / "context.json", context)
        write_json(output_dir / "reference_tools.json", tool_hints)
        shutil.copy2(TOOL_SCHEMA_PATH, output_dir / "tool.schema.json")
        write_json(output_dir / "draft_example.json", _draft_example())

    @staticmethod
    def _build_inventory_prompt(package_root: Path) -> str:
        return f"""你负责盘点一个 DataGen 环境真正能够实现的工具能力。当前目录是 {package_root.name}/tool_generation。

先读 context.json，再按需查看 ../environment.json、../provenance/ 和 ../workspace/ 中的真实文件。reference_tools.json 是上游 MCP 的能力线索：可以复用它的业务含义和字段关系，但不要照抄，也不要实现当前数据不支持的动作。

先完整盘点再结束，不要只挑少数代表性动作。依次考虑：单资源查询与筛选、跨资源关系、真实业务状态变化、文件或目录操作，以及 reference_tools.json 中每项能力。相近变体可以合并为一个带参数的能力；数据或规则没有依据的能力标成 skip，不要硬造。能力应当是用户能理解和调用的业务动作；普通 JSON 文件的读取、写回只是工具内部实现，不要和相同业务能力重复，只有当文件或目录本身就是用户要操作的对象时才单独列为 file 能力。

写 capability_inventory.json，格式为 {{"environment_id":"...","capabilities":[{{"capability_id":"cap_...","name":"业务动作","family":"query|relation|state_change|file","resource_ids":["..."],"evidence":["实际文件、字段或规则"],"reference_tools":["可选工具名"],"decision":"implement|skip","reason":"为什么能实现或为什么跳过"}}]}}。盘点完成后写 inventory_done.json，内容为 {{"status":"ready"}}，然后结束。不要编写工具，不要修改 ../environment.json、../workspace 或 ../provenance。"""

    @staticmethod
    def _build_action_plan_prompt(package_root: Path) -> str:
        return f"""你负责为 {package_root.name} 安排工具动作。

读取 capability_inventory.json、context.json，并按需查看真实环境文件。为所有 decision=implement 的能力安排动作；相近的查询或分析变体可以合并为一个带参数的动作，已有上游参考工具的复杂业务动作也可以保留为一个动作。不要遗漏可实现能力，也不要实现 decision=skip 的能力。

只写 action_plan.json，格式为 {{"environment_id":"...","actions":[{{"name":"snake_case","description":"...","capability_ids":["cap_..."],"resource_ids":["..."],"evidence":["实际文件或字段依据"],"reference_tools":["可选的上游工具名"],"effect":"读取或状态变化"}}]}}。不要写工具草稿，不要修改 ../environment.json、../workspace 或 ../provenance。写完后结束。"""

    @staticmethod
    def _build_single_action_prompt(package_root: Path, action: dict[str, Any]) -> str:
        name = str(action["name"])
        return f"""你负责为 {package_root.name} 编写一个可执行工具：{name}。

读取 action_plan.json、capability_inventory.json、context.json 和真实环境文件，理解当前动作的 capability_ids、资源字段和业务规则。查看大文件时只抽样或统计字段，不要把整个文件打印到终端。只处理这个动作，写入 drafts/{name}.json；不要重写 action_plan.json，也不要写其他工具草稿。

工具格式必须符合 tool.schema.json，测试序列写成 tests[].calls[]，代码定义 run(arguments, context)，通过 context.workspace_root 操作真实文件；该值是 pathlib.Path，不要要求它必须是字符串。只写 environment.json 声明为 writable 的资源。测试参数要来自真实数据，能覆盖这个动作的正常调用；expected_data 可以省略，对于很大的列表不要复制数据内容，只检查调用成功或数量即可。建议用 Python 的 json.dump 一次写入完整草稿，避免手工拼接转义。完整写好并保存 drafts/{name}.json 后结束，不要修改 ../environment.json、../workspace 或 ../provenance。"""

    @staticmethod
    def _build_inventory_repair_prompt(
        repair_round: int,
        error: ToolGenerationError,
    ) -> str:
        return f"""这是第 {repair_round} 次能力盘点文件修复。capability_inventory.json 当前无法读取：{error}

只修正这个文件的 JSON 或字段结构，不重新盘点、不增删能力，也不要修改其他文件。修正后写 inventory_repair_done.json，内容为 {{"status":"ready"}}，然后结束。"""

    @staticmethod
    def _build_repair_prompt(repair_round: int, failed: list[dict[str, Any]]) -> str:
        names = ", ".join(str(item["tool"]) for item in failed)
        return f"""这是 ToolGen 第 {repair_round} 次工具修复。请读取 tool_validation.json，只处理这些失败工具：{names}。

查看对应 drafts/<name>.json、tool.schema.json、draft_example.json、实际环境文件和失败原因，直接修正工具代码、Schema或调用样例。不要改变 action_plan.json，不要修改已经通过的工具，也不要修改 ../environment.json、../workspace 或 ../provenance。修好并确认 JSON 可读取后，写 repair_done.json，内容为 {{"status":"ready"}}，然后结束。"""

    @staticmethod
    def _load_capability_inventory(
        environment: dict[str, Any],
        inventory_path: Path,
    ) -> list[dict[str, Any]]:
        if not inventory_path.is_file():
            raise ToolGenerationError("能力盘点 Agent 没有写入 capability_inventory.json")
        try:
            document = json.loads(inventory_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ToolGenerationError(f"capability_inventory.json 不是合法 JSON：{error}") from error
        values = document.get("capabilities") if isinstance(document, dict) else None
        if not isinstance(values, list) or not values:
            raise ToolGenerationError("capability_inventory.json 没有能力条目")
        resource_ids = {
            str(item.get("resource_id"))
            for item in environment.get("resources", [])
            if isinstance(item, dict)
        }
        capabilities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                raise ToolGenerationError("capability_inventory.json 中的能力必须是 object")
            capability_id = str(value.get("capability_id") or "")
            if not _is_snake_case(capability_id) or capability_id in seen:
                raise ToolGenerationError(f"能力 ID 无效或重复：{capability_id}")
            decision = str(value.get("decision") or "")
            if decision not in {"implement", "skip"}:
                raise ToolGenerationError(f"能力 {capability_id} 的 decision 必须是 implement 或 skip")
            capability = dict(value)
            capability["resource_ids"] = [
                str(item)
                for item in value.get("resource_ids", [])
                if str(item) in resource_ids
            ]
            capabilities.append(capability)
            seen.add(capability_id)
        if not any(item["decision"] == "implement" for item in capabilities):
            raise ToolGenerationError("能力盘点没有任何可实现能力")
        return capabilities

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
                raise ToolGenerationError(f"工具草稿 {name} 不是合法 JSON：{error}") from error
            if not isinstance(draft, dict):
                if allow_missing:
                    continue
                raise ToolGenerationError(f"工具草稿 {name} 根节点必须是 object")
            if not isinstance(draft.get("tool"), dict):
                if isinstance(draft.get("name"), str):
                    tool = {
                        key: draft[key]
                        for key in ("name", "description", "inputSchema", "outputSchema", "internal")
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

        resource_ids = {
            str(item.get("resource_id"))
            for item in environment.get("resources", [])
            if isinstance(item, dict)
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
            ]
            action["resource_ids"] = [
                str(value)
                for value in raw.get("resource_ids", [])
                if str(value) in resource_ids
            ]
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
        return actions

    def _validate(
        self,
        package_root: Path,
        environment: dict[str, Any],
        drafts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        complete = deepcopy(environment) | {"tools": [deepcopy(item["tool"]) for item in drafts]}
        try:
            with tempfile.TemporaryDirectory(prefix="agent-world-toolgen-stage-") as temporary:
                staged = Path(temporary) / "package"
                shutil.copytree(package_root, staged, ignore=shutil.ignore_patterns("tool_generation"))
                write_json(staged / "environment.json", complete)
                package = CompleteEnvironmentPackage.load(staged)
                reports = []
                for draft in drafts:
                    tool_name = str(draft["tool"]["name"])
                    failures = self._run_tests(package, tool_name, draft["tests"], environment["resources"])
                    reports.append({"tool": tool_name, "status": "passed" if not failures else "rejected", "failures": failures, "tests": draft["tests"]})
                return reports
        except Exception as error:
            return [
                {"tool": str(item["tool"].get("name") or item["action"]["name"]), "status": "rejected", "failures": [f"complete_environment_invalid:{type(error).__name__}: {error}"], "tests": item["tests"]}
                for item in drafts
            ]

    @staticmethod
    def _run_tests(
        package: CompleteEnvironmentPackage,
        tool_name: str,
        tests: list[dict[str, Any]],
        resources: list[dict[str, Any]],
    ) -> list[str]:
        if not tests:
            return ["missing_executable_test"]
        failures: list[str] = []
        for index, test in enumerate(tests):
            calls = test.get("calls")
            if not isinstance(calls, list) or not calls or str(calls[-1].get("tool")) != tool_name:
                failures.append(f"test_{index}:final_call_must_be_{tool_name}")
                continue
            try:
                with CompleteEnvironmentRuntime(package) as runtime:
                    before = runtime.snapshot()
                    result: dict[str, Any] | None = None
                    for call in calls:
                        result = runtime.call(str(call["tool"]), dict(call.get("arguments", {})))
                    assert result is not None
                    after = runtime.snapshot()
                    change = workspace_diff(before, after)
                    changed = any(
                        change[key] for key in ("created", "modified", "deleted")
                    )
                    if bool(result.get("success")) != bool(test.get("expect_success", True)):
                        failures.append(f"test_{index}:unexpected_success={result.get('success')}")
                    if bool(test.get("expect_changed")) != changed:
                        failures.append(
                            f"test_{index}:workspace_change_mismatch="
                            f"expected_{bool(test.get('expect_changed'))}_actual_{changed}"
                        )
                    if not _changes_are_writable(change, resources):
                        failures.append(f"test_{index}:modified_non_writable_resource")
                    expected = test.get("expected_data")
                    if isinstance(expected, dict) and not _contains(result.get("data"), expected):
                        failures.append(f"test_{index}:returned_data_does_not_match_expectation")
            except Exception as error:
                failures.append(f"test_{index}:runtime_error:{type(error).__name__}: {error}")
        return failures


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


def _changes_are_writable(change: dict[str, Any], resources: list[dict[str, Any]]) -> bool:
    writable = [item for item in resources if isinstance(item, dict) and item.get("writable")]
    for path in [*change.get("created", []), *change.get("modified", []), *change.get("deleted", [])]:
        normalized = str(path).replace("\\", "/")
        if not any(_path_matches_resource(normalized, item) for item in writable):
            return False
    return True


def _path_matches_resource(path: str, resource: dict[str, Any]) -> bool:
    declared = str(resource.get("path") or "").replace("\\", "/").rstrip("/")
    if resource.get("storage_type") == "directory":
        return path == declared or path.startswith(declared + "/")
    return bool(declared and fnmatch.fnmatchcase(path, declared))


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
