"""Step 3: let one Agent directly build the final environment and repair it."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from env_gen.data_gen.config import CollectionPolicy

from .common.constants import (
    COLLECTION_PROFILE_PATH,
    CONTROL_INTEGRATION_ASSESSMENT,
    CONTROL_INTEGRATION_LAUNCHER,
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    INTEGRATION_GUIDE_FILE,
    SCENARIO_RESEARCH_PATH,
    SOURCE_INVENTORY_PATH,
    SOURCE_RESEARCH_PATH,
)
from .common.control_io import atomic_write_text, control_path, read_json
from .common.round_budget import is_last_available_round
from .common.workspace_files import file_sha256
from .common.download import cleanup_download_temporaries
from .integration.direct_commands import assess_environment


AgentRunner = Callable[[str, int, tuple[Path, ...]], str]


def _integration_guide(
    environment_schema: str,
    environment_contract: str | None,
    *,
    allow_partial_integration: bool = False,
) -> str:
    contract = environment_contract or "schemas/环境契约-v2.0.md"
    data_limit_rule = (
        "原始文件说明中已经标明无法取得的内容。只声明现有数据确实能够支持的对象和操作，不要用虚构记录"
        "补齐这些缺口。"
        if allow_partial_integration else
        "原始文件已经完成采集，仍要以实际内容为准，保留其中全部与业务有关的数据。"
    )
    return f"""# 任务：集成最终环境

你要把已经下载的原始材料转换成一个可供后续工具直接查询和修改的离线业务环境。最终环境由
结构化数据库和文件工作区组成；两者地位相同，是否需要其中一种或同时需要两种，取决于业务要求和
原始材料的实际内容。

## 先读什么

1. `provenance/scenario_research.json` 是业务要求：它说明环境要表达的对象、操作和典型工作，
   用来判断哪些原始内容有业务价值；它不是已取得数据的证明。
2. `.datagen/collection_profile.json` 是原始文件说明：它列出每个文件的路径、用途、内容概况和已知缺口，
   用来快速找到材料；其中的用途和分类只是采集时的判断，说明可能不完整，不能代替检查文件本身或
   决定最终数据形态。
3. `workspace/raw/` 保存实际原件。最终记录、字段、关系和文件必须来自这里，原件内容是事实依据。
4. `{environment_schema}` 和 `{contract}` 规定 `environment.json` 的结构和最终状态规则。

## 两种落地方式

- 某类内容需要按字段或 ID 查询、关联、创建、更新时，把它整理成 `state/records.sqlite` 中的业务表，
  并在 `environment.json.record_sets` 中声明。
- 某类内容需要保留文件内容、文件名或目录结构，供工具整份读取、校验、比较或修改时，把实际可操作文件
  放入 `state/filesystem_scopes/<scope_id>/`，并在 `environment.json.filesystem_scopes` 中声明。
- 同一个原件可以同时贡献数据库记录和文件工作区。不要根据扩展名机械分类：JSON、XML、代码或压缩包
  最终放在哪里，只由后续如何使用决定。
- ZIP、TAR、GZIP 通常只是下载容器。先检查内部成员；除非业务本身就是操作压缩包，否则应解析其中的
  业务记录，或把需要直接操作的真实文件安全解包到文件工作区，不能用一条“归档记录”或一个原始压缩包
  代替其中的有效内容。
- 文件路径、成员清单、哈希、下载来源和转换信息用于集成自检与溯源，不是默认的业务对象。除非业务要求
  明确需要按这些信息查询或修改，否则不要为它们建立 Record Set；文件工作区已经能够保留实际文件。
- 源码、说明文档和测试实现有时只是帮助理解数据或生成后续工具的参考材料。只有业务要求中的典型工作
  确实需要直接读取或修改它们时，才放入最终文件工作区；不要自行发明“审阅源码”等新任务来暴露整套
  项目。归档同时包含业务样例和实现代码时，可以只展开其中真正供业务任务操作的文件。

## 要生成什么

- `environment.json`：声明结构化业务数据表、表间关系，以及任务需要直接操作的文件目录；
- `state/records.sqlite`：最终结构化业务记录；没有 Record Set 时不需要创建；
- `state/filesystem_scopes/<scope_id>/`：任务需要直接操作的最终文件；没有文件 Scope 时不需要创建。

直接把原件转换为这些最终产物。你可以自由选择命令、已安装工具或临时脚本完成解析和批量处理；验收只看
最终数据是否正确、完整并符合环境声明，不要求提交固定形式的转换程序。临时文件不要放进最终 `state/`。

## 按这个顺序完成

1. 逐项检查原始文件说明和对应原件。对归档查看完整成员清单并打开实际内容；对大型
   JSON/JSONL/GZIP 采用流式读取并统计真实记录类型和数量。先弄清每个原件含有哪些对象、状态、标识、
   关系和可直接操作的文件。
2. 围绕实际业务对象设计最终表和文件工作区。同一对象的兼容来源合并、去重并统一字段名和类型；
   主键必须稳定且唯一，跨表关系使用统一后的键。保持不同项目、批次、实验或设计的原始边界；只有原件或
   `work_coverage` 中存在稳定 ID、路径、版本、时间或明确业务事实时才合并来源，格式相同、名称相似或能被
   同一工具处理不能作为合并依据。
3. 直接生成 `environment.json`、最终数据库和文件工作区。转换全部与业务要求有关的有效内容，
   不得只取前几条、最容易处理的类型或演示样例。保留有意义的原始 ID、状态、时间、分类、文本和关联字段；
   只排除重复、无关、确实不可解析或纯说明内容。
4. 内容自检只关注程序无法判断的部分：有用原件是否真的进入最终状态，记录数量和主要变化类型是否与原件
   相符，需要直接操作的归档内容是否已经解包并保留有意义的目录。SQLite 结构、键、关系和文件路径交给
   assess 检查，不必手工重复验证。
5. 执行 `bash ./.datagen/integratectl assess`。若 `decision=fix`，按 `blocking_issues` 修复最终声明或状态后
   重新 assess；若 `decision=ready`，完成第 4 步的内容检查后结束。程序通过只证明结构和引用正确，不证明
   你已经完整使用原件。

{data_limit_rule}

验收程序会检查输出结构、SQLite 完整性、表列类型、唯一键、关系、文件引用和文件目录，
但不限制你采用哪种转换方法。
"""


def prepare_integration(run_dir: Path) -> None:
    """Create the Step 3 guide and command launcher next to the run state."""

    run_dir = run_dir.resolve()
    for relative in ("state", "provenance", ".datagen/drafts"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    project_root = Path(__file__).resolve().parents[3]
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
        _integration_guide(
            str(config["environment_schema_path"]),
            str(config.get("contract_path")) if config.get("contract_path") else None,
            allow_partial_integration=config.get("allow_partial_integration") is True,
        ),
    )


def build_integration_prompt(run_dir: Path) -> str:
    return """完整读取 `.datagen/INTEGRATION_GUIDE.md` 并按其中的顺序完成环境集成。先实际检查每个原件
及归档内容，再直接生成 `environment.json` 和最终 `state/`；不要用压缩包、文件清单或少量样例代替
原件中的有效业务内容。完成逐项内容自检，并让 assess 返回 ready 后结束。
"""


def build_integration_continuation_prompt(
    run_dir: Path, *, round_index: int, final_round: bool,
) -> str:
    ending = "修复后重新执行 assess；返回 ready 后结束。"
    if final_round:
        ending = "优先修复 blocking_issues，并在结束前重新执行 assess。"
    return f"""继续完成 `.datagen/INTEGRATION_GUIDE.md` 中的集成任务。读取
`.datagen/integration_assessment.json` 的具体错误，同时检查是否仍有原件、归档成员、记录类型或有效记录
尚未进入最终数据库或文件工作区；直接修复 `environment.json` 或 `state/`。{ending}
"""


@dataclass(frozen=True)
class IntegrationResult:
    environment: dict[str, Any]
    assessment: dict[str, Any]
    agent_calls: int
    assessment_runs: int


class IntegrationError(RuntimeError):
    """Step 3 未能形成通过程序验收的 integrated 环境。"""


def _file_tree(root: Path, *, relative_to: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(relative_to).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _integration_input_snapshot(run_dir: Path) -> dict[str, str]:
    """Freeze all Step 3 inputs; integration is not another collection stage."""

    paths = {
        control_path(run_dir, "run_config.json"),
        control_path(run_dir, "selected_seed.json"),
        control_path(run_dir, CONTROL_INTEGRATION_LAUNCHER),
        control_path(run_dir, INTEGRATION_GUIDE_FILE),
        run_dir / COLLECTION_PROFILE_PATH,
        run_dir / SCENARIO_RESEARCH_PATH,
        run_dir / SOURCE_RESEARCH_PATH,
        run_dir / SOURCE_INVENTORY_PATH,
        Path(__file__),
    }
    raw_root = run_dir / "workspace/raw"
    if raw_root.is_dir():
        paths.update(path for path in raw_root.rglob("*") if path.is_file())
    return {
        str(path): file_sha256(path)
        for path in sorted(paths, key=lambda item: str(item))
        if path.is_file()
    }


def _verify_integration_inputs(expected: dict[str, str]) -> None:
    issues = []
    for value, digest in expected.items():
        path = Path(value)
        if not path.is_file():
            issues.append(f"删除了只读文件：{path}")
        elif file_sha256(path) != digest:
            issues.append(f"修改了只读文件：{path}")
    if issues:
        raise RuntimeError("；".join(issues[:8]))


def integration_progress_snapshot(run_dir: Path) -> dict[str, Any]:
    """Track the Agent-owned declaration and final state."""

    run_dir = run_dir.resolve()
    state = run_dir / "state"
    environment_path = run_dir / "environment.json"
    payload = {
        "environment": file_sha256(environment_path) if environment_path.is_file() else None,
        "state": _file_tree(state, relative_to=run_dir),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        **payload,
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
    }


def _assessment_after_round(run_dir: Path) -> dict[str, Any]:
    try:
        # Agent-visible assessment may be stale or forged. Recompute validation before
        # accepting a round, and retain the state digest for Step 4.
        assessment = assess_environment(run_dir)
        return assessment
    except Exception as error:
        return {
            "workflow_version": "3.0",
            "decision": "fix",
            "blocking_issues": [{
                "code": "integration_assessment_failed",
                "path": control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT).as_posix(),
                "message": str(error),
            }],
            "next_actions": [],
        }


def run_integration_phase(
    *,
    run_dir: Path,
    collection_policy: CollectionPolicy,
    agent_runner: AgentRunner,
) -> IntegrationResult:
    """Give the Agent the whole integration task; Python only accepts or repairs."""

    run_dir = run_dir.resolve()
    prepare_integration(run_dir)
    protected = _integration_input_snapshot(run_dir)
    deadline = time.monotonic() + collection_policy.integration_total_seconds
    calls = 0
    assessments = 0
    no_progress_rounds = 0
    last_error: Exception | None = None
    last_assessment: dict[str, Any] | None = None

    for round_index in range(1, collection_policy.max_integration_rounds + 1):
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        if remaining <= 0:
            last_error = TimeoutError(
                f"Step 3 超过集成预算 {collection_policy.integration_total_seconds} 秒"
            )
            break
        before = integration_progress_snapshot(run_dir)
        final_round = is_last_available_round(
            round_index=round_index,
            max_rounds=collection_policy.max_integration_rounds,
            remaining_seconds=remaining,
            per_round_seconds=collection_policy.integration_seconds,
        )
        prompt = (
            build_integration_prompt(run_dir)
            if round_index == 1
            else build_integration_continuation_prompt(
                run_dir,
                round_index=round_index,
                final_round=final_round,
            )
        )
        calls += 1
        try:
            agent_runner(
                prompt,
                min(collection_policy.integration_seconds, remaining),
                (),
            )
        except Exception as error:
            last_error = error
        cleanup_download_temporaries(run_dir)
        _verify_integration_inputs(protected)
        last_assessment = _assessment_after_round(run_dir)
        assessments += 1

        if last_assessment.get("decision") == "ready":
            try:
                _verify_integration_inputs(protected)
                environment = read_json(run_dir / "environment.json", "环境声明")
            except Exception as error:
                last_error = error
            else:
                return IntegrationResult(environment, last_assessment, calls, assessments)

        after = integration_progress_snapshot(run_dir)
        if before["fingerprint"] == after["fingerprint"]:
            no_progress_rounds += 1
        else:
            no_progress_rounds = 0
        if no_progress_rounds >= collection_policy.max_no_progress_rounds:
            last_error = RuntimeError(
                f"连续 {no_progress_rounds} 轮没有修改环境声明或最终状态"
            )
            break

    details: list[str] = []
    if last_assessment:
        for issue in last_assessment.get("blocking_issues", [])[:6]:
            if isinstance(issue, dict):
                details.append(str(issue.get("message") or issue.get("code")))
        for action in last_assessment.get("next_actions", [])[:6]:
            if isinstance(action, dict):
                details.append(str(action.get("action") or action.get("code")))
    if last_error is not None:
        details.append(str(last_error))
    suffix = "：" + "；".join(value for value in details if value) if details else ""
    raise IntegrationError(
        f"Step 3 在 {calls}/{collection_policy.max_integration_rounds} 轮内未形成有效最终环境{suffix}"
    ) from last_error


__all__ = [
    "IntegrationError",
    "IntegrationResult",
    "integration_progress_snapshot",
    "run_integration_phase",
]
