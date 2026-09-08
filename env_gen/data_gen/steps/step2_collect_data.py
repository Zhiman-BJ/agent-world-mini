"""Step 2: let one Agent collect useful files and describe their coverage.

The Agent owns the complete download loop and decides when further useful
collection has little expected value. Coverage percentages are acceptance
floors, not collection targets. Python only checks final paths, records coarse
file facts and derives the compatibility artifacts consumed by Step 3.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

from .common.constants import (
    COLLECTION_PROFILE_PATH,
    CONTROL_COLLECTION_RESULT,
    CONTROL_DOWNLOAD_RECEIPTS,
    CONTROL_RAW_INTEGRITY_SNAPSHOT,
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    CONTROL_SOURCE_FINALIZATION,
    CONTROL_SOURCE_RESEARCH_RECEIPT,
    SCENARIO_RESEARCH_PATH,
    SOURCE_INVENTORY_PATH,
    SOURCE_RESEARCH_PATH,
)
from .common.control_io import control_path, read_json, write_json
from .common.download import DOWNLOAD_LEDGER, simple_file_stats
from .common.workspace_files import business_snapshot, file_sha256
from .step1_research_scenario import read_saved_scenario_research


AgentRunner = Callable[[str, int, tuple[Path, ...]], str]
_SOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ALLOWED_ROLES = {"business_records", "task_domain_files"}
_SUBJECT_TYPES = ("entity", "tool", "task")
_RESULT_REPAIR_SECONDS = 180


class DataCollectionError(RuntimeError):
    """Step 2 could not produce a valid terminal collection."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed_task_label(item: dict[str, Any]) -> str | None:
    for field in ("name", "description"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _pointer(error: Any) -> str:
    value = "$"
    for part in error.absolute_path:
        value += f"[{part}]" if isinstance(part, int) else f".{part}"
    return value


def _source_research_issues(run_dir: Path, payload: dict[str, Any]) -> list[str]:
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    schema = read_json(Path(config["source_research_schema_path"]), "来源报告 Schema")
    errors = [
        f"{_pointer(error)}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    ]
    if payload.get("seed_global_id") != seed.get("global_id"):
        errors.append("$.seed_global_id: 与当前 Seed 不一致")
    if payload.get("seed_sha256") != config.get("seed_sha256"):
        errors.append("$.seed_sha256: 与当前 Seed 哈希不一致")
    return errors


def save_source_research(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Save the compatibility source report consumed by Step 3."""

    run_dir = run_dir.resolve()
    issues = _source_research_issues(run_dir, payload)
    if issues:
        raise RuntimeError("source_research 无效：" + "; ".join(issues[:12]))
    target = run_dir / SOURCE_RESEARCH_PATH
    write_json(target, payload)
    write_json(control_path(run_dir, CONTROL_SOURCE_RESEARCH_RECEIPT), {
        "schema_version": "1.0",
        "path": SOURCE_RESEARCH_PATH,
        "sha256": file_sha256(target),
        "seed_sha256": payload["seed_sha256"],
        "saved_at": _now(),
    })
    return payload


def save_source_research_file(run_dir: Path, input_path: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    candidate = input_path.resolve()
    try:
        candidate.relative_to(run_dir)
    except ValueError as error:
        raise RuntimeError("来源报告输入必须位于当前运行目录") from error
    return save_source_research(run_dir, read_json(candidate, "来源报告草稿"))


def read_saved_source_research(run_dir: Path) -> dict[str, Any]:
    payload = read_json(run_dir.resolve() / SOURCE_RESEARCH_PATH, "来源报告")
    issues = _source_research_issues(run_dir.resolve(), payload)
    if issues:
        raise RuntimeError("source_research 无效：" + "; ".join(issues[:12]))
    return payload


def source_research_receipt_issues(run_dir: Path) -> list[dict[str, str]]:
    run_dir = run_dir.resolve()
    report = run_dir / SOURCE_RESEARCH_PATH
    receipt_path = control_path(run_dir, CONTROL_SOURCE_RESEARCH_RECEIPT)
    if not report.is_file() or not receipt_path.is_file():
        return [{"code": "source_research_missing", "path": SOURCE_RESEARCH_PATH, "message": "缺少来源报告或收据"}]
    try:
        payload = read_json(report, "来源报告")
        receipt = read_json(receipt_path, "来源报告收据")
    except RuntimeError as error:
        return [{"code": "source_research_invalid", "path": SOURCE_RESEARCH_PATH, "message": str(error)}]
    issues = _source_research_issues(run_dir, payload)
    if receipt.get("sha256") != file_sha256(report):
        issues.append("来源报告哈希与收据不一致")
    return [
        {"code": "source_research_invalid", "path": SOURCE_RESEARCH_PATH, "message": message}
        for message in issues
    ]


def _subject_universe(seed: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def add(subject_type: str, name: str | None, origin: str) -> None:
        if not name or not name.strip():
            return
        key = (subject_type, name.strip())
        item = found.setdefault(key, {
            "subject_type": subject_type,
            "subject_name": name.strip(),
            "origins": [],
        })
        if origin not in item["origins"]:
            item["origins"].append(origin)

    for item in seed.get("init_ref_tools", []):
        if isinstance(item, dict):
            add("tool", str(item.get("name") or ""), "seed")
    for item in seed.get("init_ref_tasks", []):
        if isinstance(item, dict):
            add("task", seed_task_label(item), "seed")
    for subject_type, collection in (("entity", "entities"), ("tool", "tools"), ("task", "tasks")):
        for item in scenario.get(collection, []):
            if isinstance(item, dict):
                add(subject_type, str(item.get("name") or ""), "scenario")
    return sorted(found.values(), key=lambda item: (_SUBJECT_TYPES.index(item["subject_type"]), item["subject_name"]))


def _build_collection_prompt(run_dir: Path) -> str:
    config = read_json(control_path(run_dir.resolve(), CONTROL_RUN_CONFIG), "运行配置")
    policy = config["collection_policy"]
    seed_min = int(policy.get("min_seed_coverage_percent", 90))
    scenario_min = int(policy.get("min_scenario_coverage_percent", 75))
    max_single_mib = int(policy.get("max_single_file_bytes", 256 * 1024 * 1024)) // (1024 * 1024)
    max_raw_mib = int(policy.get("max_raw_bytes", 512 * 1024 * 1024)) // (1024 * 1024)
    max_workspace_mib = int(policy.get("max_workspace_bytes", 768 * 1024 * 1024)) // (1024 * 1024)
    max_raw_files = int(policy.get("max_raw_files", 200))
    return f"""工作目录：`{run_dir.resolve()}`。

# 背景与任务

我们要构建一个可以离线运行的真实业务环境。你的首要目标是取得环境的真实核心数据。这些数据必须能够实际支撑业务操作：
包含后续查询、创建或修改业务对象所需的对象、状态、关系、标识和字段，或者任务需要直接读取
和编辑的原始文件内容。只与主题相关、却不能用于完成这些操作的材料，不是我们要找的数据。

优先寻找现实业务中实际产生、维护和使用的数据，同一业务流程的数据尽可能同源或能够相互关联，使最终
环境保持连贯。模板、演示样例、测试夹具和说明材料可以补充特殊情况，但不能代替真实核心数据，也不能仅凭
内容相关或提到某项功能，就认为数据足以支撑相应操作。

先读取两个输入：

- `.datagen/selected_seed.json`：来源平台提供的原始入口，包含来源地址、简要场景描述、参考工具与任务，
  以及可能的数据方向。参考工具和任务构成第一组覆盖清单；其他内容用于定位来源，不视为已经核实的事实。
- `provenance/scenario_research.json`：基于外部来源核实并扩展后的现实业务报告，包含工作背景、业务实体、
  工具、典型任务、建议寻找的数据、来源证据和待确认问题。实体、工具和任务构成第二组覆盖清单；
  `data_directions` 用于选择数据，`open_questions` 用于避免把尚未确认的内容当成事实。

围绕这两组覆盖清单，将可用原件下载到 `workspace/raw/<source>/`。所有下载命令的目标路径必须明确包含
`workspace/raw/`，不要在运行目录顶层另建 `raw/`。文件卡中的 `path` 才省略 `workspace/` 前缀，写成
`raw/<source>/<file>`。尽可能让同一批数据共同支持多项相关操作，并优先选择来自同一系统、可以通过稳定 ID
相互关联的数据。

# 根据场景选择数据形态

采集结果有两种平级的数据形态，不表示主次或质量高低：

1. `business_records`：后续会把内容拆成一条条记录存入数据库，工具按记录的字段和 ID 进行查询、关联、创建
   或更新。例如对象列表、事件记录、状态历史和统计观测。
2. `task_domain_files`：后续会保留文件名、目录和原始内容，工具把整个文件作为工作对象读取、校验、比较或
   修改。例如配置文件、代码工程、VAST XML、地图和媒体文件。

分类依据是后续如何使用，不是文件扩展名。同一个 JSON，若内容要拆成对象记录就是 `business_records`；
若任务要保持原文件并编辑其内容，就是 `task_domain_files`。

普通产品页面、API 说明、教程和仅用于证明功能存在的源码不属于以上两类，不要保留。如果场景本身是代码维护、
文档处理或测试，相关代码、文档或测试文件才可以作为 `task_domain_files`。数据库和领域文件都按实际需要选择，
没有固定数量要求。根据两个输入描述的实际业务决定本次只需要其中一类还是两类都需要；不要为了凑齐类型而下载。

# 下载循环

按以下顺序重复执行，不要先搜完所有来源再统一整理：

1. 把尚未覆盖的实体、工具和任务按它们共同需要的数据分组。优先处理能用同一批数据同时支持最多清单项的组，
   尤其是能够连接多个实体或完成一条典型任务流程的数据，不要按单个工具逐一下载。
2. 为这一组依次尝试最多 3 个真正可能提供同类业务对象的不同来源：首选官方 API 或数据集，其次是同一机构
   的其他正式出口，最后是可信公开镜像或同类数据。只有来源预计包含这一组所需的对象和关键字段时才计为一次
   尝试；同一站点的不同 URL 不算多个来源，源码、产品页和说明文档也不能为普通业务记录凑满尝试次数。
   某个来源超时、认证后仍无权限、返回错误内容或下载失败时，记住 URL 和原因并换下一个来源；任一来源成功
   后不必凑满 3 次。
3. 下载该来源中一个或几个相互关联的文件到 `workspace/raw/<source>/`。
4. 打开实际内容。确认它不是登录页、错误响应、空文件或只有说明文字，并检查是否包含所需对象、状态、ID
   和业务字段。无用、损坏或与现有 URL/内容重复的文件直接删除。
5. 为每个保留文件立即写一张文件卡，然后重新检查待支持清单并开始下一轮。

如果一组数据连续尝试 3 个合格来源仍未取得，把该组未覆盖的实体、工具或任务、三个来源 URL、各来源原本
预计提供的数据及失败原因写入最终 `summary`，然后继续处理下一组缺口。

网络请求应设置超时。全部文件必须遵守以下上限：单文件 {max_single_mib} MiB、Raw 合计 {max_raw_mib} MiB、
workspace 合计 {max_workspace_mib} MiB、Raw 文件最多 {max_raw_files} 个。来源过大时选取能够保留主要对象、
状态和变化类型的官方切片。结束前删除临时文件并停止未完成的下载进程。

# 已有认证的使用规则

运行环境可能已经配置 `GH_TOKEN`、`HF_TOKEN`、`KAGGLE_API_TOKEN` 或已经登录的官方 CLI。只检查凭据是否
存在，不得输出、复制或写入任何 Token、Cookie 或密码。目标网站有对应凭据时，从第一次请求就使用认证的
官方 CLI 或 API；例如 GitHub 使用 `gh api`，不要假定普通 `curl` 会自动携带 `GH_TOKEN`。

遇到 `401`、`403`、匿名限流或登录页时，若有对应凭据，必须先对同一端点做一次认证请求。只有认证后仍然
权限不足，或确实没有该服务的凭据，才能把它记为失败来源；随后再尝试公开 API、静态导出、发布归档或可信
镜像。一个受限端点不能作为停止整个采集的理由。最终仍无法取得时，在总结中写明缺少的数据、实际请求过的
来源和限制，不得用源码或说明文档冒充业务数据。

# 文件卡

每接受一个文件就更新 `.datagen/collection_result.json`：

```json
{{
  "schema_version": "1.0",
  "result": "collecting",
  "summary": "当前已取得的数据和仍缺少的内容",
  "data_independent_tools": [],
  "file_cards": [
    {{
      "path": "raw/example/items.json",
      "url": "https://example.org/items.json",
      "source_id": "example",
      "role": "business_records",
      "name": "业务对象记录",
      "summary": "包含对象 ID、状态以及与其他对象的关联 ID。",
      "subjects": [
        {{
          "subject_type": "tool",
          "subject_name": "list_items",
          "status": "supported",
          "reason": "记录包含列举和筛选该对象所需的 ID、状态和分类字段。"
        }}
      ],
      "limitations": []
    }}
  ]
}}
```

`subjects` 用来说明这个文件支持待支持清单中的哪些项目：

- `subject_type` 只能是 `entity`、`tool` 或 `task`；`subject_name` 必须原样复制输入文件中的名称。
- `supported` 表示文件具备完成该项操作所需的对象、状态、ID 和关键业务字段。
- `partial` 表示内容相关，但缺少完成操作所需的数据；`partial` 不计入覆盖率。
- 写操作不要求在真实网站上执行。只要数据提供可修改对象的初始状态、稳定 ID 和必要字段，离线环境就能支持写操作。
- 同一文件可以登记多个项目，但每项都必须说明具体由哪些数据支持。

若某个工具本身不依赖任何初始业务数据，把它写入根对象的 `data_independent_tools`，每项只写输入文件中的
`tool_name` 和具体 `reason`。仅限以下情况：结果完全来自固定能力声明；来源明确规定它始终返回空集合；或者
调用方提供创建首个对象所需的全部内容，且不依赖已有父对象或参考记录。它不算已覆盖，只从数据覆盖分母排除。
查询、读取、更新或删除业务对象的工具，以及需要代表性文件才能执行的校验和文件处理工具，不得放入此列表。
静态规则、分类、字典等可查询参考记录仍然属于初始数据，不能因为它们变化较少就排除。实体和任务也不得
放入。不要为了提高覆盖率滥用这一项。

Raw 必须保留从 `url` 实际取得的原件。只需要归档中的少量成员时，优先改用每个成员稳定的直接下载 URL；
没有直接 URL 时，把完整原始归档保留为一张 Raw 文件卡，并把安全展开后的成员写入 `prepared_paths`。禁止
把提取出的少量文件重新打包后冒充上游完整归档，也禁止给本地生成的文件填写一个内容不同的远端 URL。

# 完成条件

Python 会按名称去重，只把 `supported` 计入覆盖率，并把经过说明的 `data_independent_tools` 单独列出、从
数据覆盖分母排除。初始参考工具和任务至少覆盖 {seed_min}%；调研文件中的实体、工具和任务至少覆盖 {scenario_min}%。
这两个数字是最低线，不是达到后立即停止的目标。

- 低于任一最低线：继续采集。只有剩余数据组都已成功取得数据，或已分别记录 3 个失败来源，才以 `partial` 结束。
- 达到两条最低线：再检查一遍未覆盖项目；仍有明确可取得且能增加新业务内容的来源就继续，否则以 `ready` 结束。
- 没有取得任何可用文件：以 `insufficient_data` 结束。

结束前更新所有文件卡，并在 `summary` 中写清最终数据范围、覆盖数字、剩余缺口及停止原因。
"""


def _build_result_repair_prompt(run_dir: Path, error: Exception) -> str:
    detail = str(error).strip()[-2000:]
    return f"""工作目录：`{run_dir.resolve()}`。

数据采集已经结束，但 Python 对 `.datagen/collection_result.json` 的机械校验失败：

`{detail}`

只修复现有下载文件、必要的目录包装和文件卡，不再调查或下载新来源，也不要检查流水线源码。确保每张文件卡的 `path` 指向
`workspace/raw/` 下的普通文件，所有 Raw 文件各有且仅有一张卡，URL、路径和内容不重复，Prepared 路径
真实存在。只有来源本身交付为多文件目录制品或 Git 检出时才打包成单个 Raw 归档并移除检出目录；
同一来源目录中的独立 API 响应不得合并。需要展开使用的内容放到 `workspace/prepared/`。不得把提取成员
重新打包后填写上游完整归档 URL；必要时可以重新下载已经登记的原 URL，或改用成员自身的直接 URL。保留原来的
语义判断和最终 `result`，除非错误明确说明它与最低覆盖线冲突。

修复后重写完整、有效的 `.datagen/collection_result.json` 并立即结束。
"""


def _workspace_path(run_dir: Path, logical: str, *, root_name: str) -> Path:
    normalized = logical.removeprefix("workspace/").lstrip("/")
    if not normalized.startswith(root_name + "/"):
        raise RuntimeError(f"路径必须位于 workspace/{root_name}/：{logical}")
    root = (run_dir.resolve() / "workspace" / root_name).resolve()
    target = (run_dir.resolve() / "workspace" / normalized).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"路径越过 workspace/{root_name}：{logical}") from error
    return target


def _validate_agent_result(
    run_dir: Path,
    report: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]], dict[tuple[str, str], str]]:
    if report.get("schema_version") != "1.0" or not isinstance(report.get("file_cards"), list):
        raise RuntimeError("collection_result 必须使用 schema_version=1.0 和 file_cards 数组")
    decision = str(report.get("result") or "")
    if decision not in {"ready", "partial", "insufficient_data"}:
        raise RuntimeError("collection_result.result 必须是 ready、partial 或 insufficient_data")
    report_summary = str(report.get("summary") or "").strip()
    if len(report_summary) < 10:
        raise RuntimeError("collection_result.summary 必须说明最终采集结论")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    scenario = read_saved_scenario_research(run_dir)
    universe = _subject_universe(seed, scenario)
    allowed = {
        (item["subject_type"], item["subject_name"])
        for item in universe
    }
    tool_descriptions: dict[str, list[str]] = {}
    for payload, collection in ((seed, "init_ref_tools"), (scenario, "tools")):
        for item in payload.get(collection, []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            if name and description:
                tool_descriptions.setdefault(name, []).append(description)
    independent_raw = report.get("data_independent_tools", [])
    if not isinstance(independent_raw, list):
        raise RuntimeError("collection_result.data_independent_tools 必须是数组")
    independent: dict[tuple[str, str], str] = {}
    for index, item in enumerate(independent_raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"data_independent_tools[{index}] 必须是对象")
        if set(item) != {"tool_name", "reason"}:
            raise RuntimeError(
                f"data_independent_tools[{index}] 只能包含 tool_name 和 reason"
            )
        name = str(item.get("tool_name") or "").strip()
        reason = str(item.get("reason") or "").strip()
        key = ("tool", name)
        if key not in allowed:
            raise RuntimeError(f"无需初始数据列表引用未知工具：{name}")
        if len(reason) < 10:
            raise RuntimeError(f"data_independent_tools[{index}] 必须具体说明无需初始数据的原因")
        if key in independent:
            raise RuntimeError(f"data_independent_tools 包含重复工具：{name}")
        description = " ".join(tool_descriptions.get(name, [])).lower()
        explicitly_empty = any(marker in description for marker in (
            "always returns an empty collection",
            "always returns empty",
            "始终返回空集合",
            "总是返回空集合",
        ))
        fixed_capability = "capabil" in name.lower() or "capabil" in description
        creates_first_object = name.lower().startswith("create_")
        if not (explicitly_empty or fixed_capability or creates_first_object):
            raise RuntimeError(
                f"工具 {name} 不是固定能力声明、明确空集合或创建首对象操作，不能排除数据需求"
            )
        independent[key] = reason

    cards: list[dict[str, Any]] = []
    card_subjects: set[tuple[str, str]] = set()
    paths: set[str] = set()
    urls: set[str] = set()
    hashes: set[str] = set()
    for index, raw_card in enumerate(report.get("file_cards", [])):
        if not isinstance(raw_card, dict):
            raise RuntimeError(f"file_cards[{index}] 必须是对象")
        path = str(raw_card.get("path") or "").removeprefix("workspace/").lstrip("/")
        physical = _workspace_path(run_dir, path, root_name="raw")
        if not physical.is_file():
            raise RuntimeError(f"file_cards[{index}] 对应文件不存在：{path}")
        url = str(raw_card.get("url") or "").strip()
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise RuntimeError(f"file_cards[{index}].url 必须是 HTTP(S) 下载地址")
        source_id = str(raw_card.get("source_id") or "").strip()
        if not _SOURCE_ID.fullmatch(source_id):
            raise RuntimeError(f"file_cards[{index}].source_id 必须是小写稳定标识")
        role = str(raw_card.get("role") or "")
        if role not in _ALLOWED_ROLES:
            raise RuntimeError(f"file_cards[{index}].role 无效")
        name = str(raw_card.get("name") or "").strip()
        summary = str(raw_card.get("summary") or "").strip()
        if not name or len(summary) < 10:
            raise RuntimeError(f"file_cards[{index}] 必须填写名称和简短业务摘要")
        limitations = raw_card.get("limitations", [])
        prepared_paths = raw_card.get("prepared_paths", [])
        subjects = raw_card.get("subjects", [])
        if isinstance(limitations, str):
            limitations = [limitations] if limitations.strip() else []
        if isinstance(prepared_paths, str):
            prepared_paths = [prepared_paths] if prepared_paths.strip() else []
        if not all(isinstance(value, str) and value.strip() for value in limitations):
            raise RuntimeError(f"file_cards[{index}].limitations 必须是字符串数组")
        for value in prepared_paths:
            if not isinstance(value, str) or not _workspace_path(run_dir, value, root_name="prepared").exists():
                raise RuntimeError(f"file_cards[{index}].prepared_paths 包含不存在的 Prepared")
        normalized_subjects: list[dict[str, str]] = []
        for subject_index, subject in enumerate(subjects):
            if not isinstance(subject, dict):
                raise RuntimeError(f"file_cards[{index}].subjects[{subject_index}] 必须是对象")
            subject_type = str(subject.get("subject_type") or "")
            subject_name = str(subject.get("subject_name") or "")
            status = str(subject.get("status") or "")
            reason = str(subject.get("reason") or "").strip()
            if (subject_type, subject_name) not in allowed:
                raise RuntimeError(f"文件卡引用未知主体：{subject_type}/{subject_name}")
            if status not in {"supported", "partial"} or len(reason) < 5:
                raise RuntimeError("文件卡覆盖状态只能是 supported/partial，并必须说明依据")
            normalized_subjects.append({
                "subject_type": subject_type,
                "subject_name": subject_name,
                "status": status,
                "reason": reason,
            })
            card_subjects.add((subject_type, subject_name))
        digest = file_sha256(physical)
        if path in paths or url in urls or digest in hashes:
            raise RuntimeError(f"file_cards[{index}] 与已有文件的路径、URL 或内容重复")
        paths.add(path)
        urls.add(url)
        hashes.add(digest)
        stats = simple_file_stats(physical)
        cards.append({
            "path": path,
            "source_id": source_id,
            "url": url,
            "sha256": digest,
            "bytes": stats["bytes"],
            "format": stats["format"],
            "record_count": stats.get("record_count"),
            "file_count": stats.get("file_count", 1),
            "role": role,
            "name": name,
            "summary": summary,
            "subjects": normalized_subjects,
            "prepared_paths": sorted(set(prepared_paths)),
            "limitations": sorted(set(limitations)),
        })
    raw_root = run_dir.resolve() / "workspace/raw"
    actual_paths = {
        path.relative_to(run_dir.resolve() / "workspace").as_posix()
        for path in raw_root.rglob("*") if path.is_file()
    }
    if actual_paths != paths:
        difference = sorted(actual_paths.symmetric_difference(paths))
        raise RuntimeError("workspace/raw 与文件卡不一致：" + "、".join(difference[:12]))
    if decision == "ready" and not cards:
        raise RuntimeError("ready 至少需要一个有效文件卡")
    overlap = sorted(
        name
        for subject_type, name in independent
        if (subject_type, name) in card_subjects
    )
    if overlap:
        raise RuntimeError("工具不能同时登记文件支持和无需初始数据：" + "、".join(overlap))
    return decision, report_summary, sorted(cards, key=lambda item: item["path"]), independent


def _write_download_evidence(run_dir: Path, cards: list[dict[str, Any]]) -> None:
    timestamp = _now()
    downloads = []
    receipts = []
    for card in cards:
        request_key = hashlib.sha256(f"GET\0{card['url']}\0".encode()).hexdigest()
        downloads.append({
            "request_key": request_key,
            "method": "GET",
            "request_body_sha256": None,
            "urls": [card["url"]],
            "effective_url": card["url"],
            "source_id": card["source_id"],
            "path": card["path"],
            "sha256": card["sha256"],
            "bytes": card["bytes"],
            "format": card["format"],
            "record_count": card["record_count"],
            "file_count": card["file_count"],
            "role": card["role"],
            "status": "downloaded",
            "downloaded_at": timestamp,
            "elapsed_seconds": None,
        })
        receipts.append({
            "url": card["url"],
            "effective_url": card["url"],
            "source_id": card["source_id"],
            "path": card["path"],
            "bytes": card["bytes"],
            "sha256": card["sha256"],
            "retrieved_at": timestamp,
            "reused_existing_file": False,
        })
    write_json(control_path(run_dir, DOWNLOAD_LEDGER), {
        "schema_version": "1.0", "downloads": downloads, "failures": [],
    })
    write_json(control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS), {
        "schema_version": "1.0", "downloads": receipts,
    })


def _status_rank(status: str) -> int:
    return {"missing": 0, "partial": 1, "supported": 2}.get(status, 0)


def _coverage(
    cards: list[dict[str, Any]],
    universe: list[dict[str, Any]],
    independent: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for card in cards:
        if card["role"] == "semantic_evidence":
            continue
        for item in card["subjects"]:
            key = (item["subject_type"], item["subject_name"])
            if _status_rank(item["status"]) >= _status_rank(str(evidence.get(key, {}).get("status") or "missing")):
                evidence[key] = {
                    "status": item["status"],
                    "reason": item["reason"],
                    "path": card["path"],
                    "basis": card["role"],
                }
    result = []
    for subject in universe:
        key = (subject["subject_type"], subject["subject_name"])
        finding = evidence.get(key)
        independent_reason = independent.get(key)
        result.append({
            "subject_type": key[0],
            "subject_name": key[1],
            "origins": subject["origins"],
            "status": "not_required" if independent_reason else (finding["status"] if finding else "missing"),
            "basis": finding["basis"] if finding else "none",
            "reason": independent_reason or (finding["reason"] if finding else "尚无有效文件卡覆盖该主体。"),
            "path": finding["path"] if finding else None,
        })
    return result


def _measure(coverage: list[dict[str, Any]], *, origin: str, subject_type: str) -> dict[str, Any]:
    items = [item for item in coverage if item["subject_type"] == subject_type and origin in item["origins"]]
    evaluated = [item for item in items if item["status"] != "not_required"]
    supported = sum(item["status"] == "supported" for item in evaluated)
    total = len(evaluated)
    return {
        "supported": supported,
        "total": total,
        "not_required": len(items) - total,
        "listed_total": len(items),
        "percent": round(100 * supported / total, 1) if total else 100.0,
    }


def _measure_origin(coverage: list[dict[str, Any]], *, origin: str) -> dict[str, Any]:
    items = [item for item in coverage if origin in item["origins"]]
    evaluated = [item for item in items if item["status"] != "not_required"]
    supported = sum(item["status"] == "supported" for item in evaluated)
    total = len(evaluated)
    return {
        "supported": supported,
        "total": total,
        "not_required": len(items) - total,
        "listed_total": len(items),
        "percent": round(100 * supported / total, 1) if total else 100.0,
    }


def _build_profile(
    run_dir: Path,
    cards: list[dict[str, Any]],
    *,
    decision: str,
    agent_summary: str,
    independent: dict[tuple[str, str], str],
) -> dict[str, Any]:
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    universe = _subject_universe(
        read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed"),
        read_saved_scenario_research(run_dir),
    )
    coverage = _coverage(cards, universe, independent)
    metrics = {
        "seed": {
            "overall": _measure_origin(coverage, origin="seed"),
            **{subject_type: _measure(coverage, origin="seed", subject_type=subject_type) for subject_type in ("tool", "task")},
        },
        "scenario": {
            "overall": _measure_origin(coverage, origin="scenario"),
            **{subject_type: _measure(coverage, origin="scenario", subject_type=subject_type) for subject_type in _SUBJECT_TYPES},
        },
    }
    missing = [item for item in coverage if item["status"] in {"missing", "partial"}]
    return {
        "schema_version": "2.0",
        "seed_global_id": config["seed_global_id"],
        "seed_sha256": config["seed_sha256"],
        "summary": agent_summary,
        "file_cards": cards,
        "coverage": coverage,
        "metrics": metrics,
        "gaps": [{
            "subject_type": item["subject_type"],
            "subject_name": item["subject_name"],
            "status": item["status"],
            "reason": item["reason"],
        } for item in missing],
        "decision": decision,
    }


def _validate_ready_floor(run_dir: Path, profile: dict[str, Any]) -> None:
    if profile["decision"] != "ready":
        return
    policy = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")["collection_policy"]
    requirements = (
        ("Seed", profile["metrics"]["seed"]["overall"]["percent"], int(policy.get("min_seed_coverage_percent", 90))),
        ("Step 1", profile["metrics"]["scenario"]["overall"]["percent"], int(policy.get("min_scenario_coverage_percent", 75))),
    )
    below = [f"{name} {actual}% < {minimum}%" for name, actual, minimum in requirements if actual < minimum]
    if below:
        raise RuntimeError("Agent 将结果标为 ready，但实际 supported 覆盖低于最低验收线：" + "；".join(below))


def _source_type(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "github.com" in host or "githubusercontent.com" in host:
        return "repository"
    return "api" if "/api" in url or "apis" in host else "dataset"


def _compat_source_research(run_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
    cards = profile["file_cards"]
    by_source: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        by_source.setdefault(card["source_id"], []).append(card)
    role_names = {
        "business_records": "structured_data",
        "task_domain_files": "domain_files",
        "semantic_evidence": "semantic_evidence",
    }
    sources = []
    for source_id, items in sorted(by_source.items()):
        urls = list(dict.fromkeys(item["url"] for item in items))
        roles = sorted({role_names[item["role"]] for item in items})
        sources.append({
            "source_id": source_id,
            "name": items[0]["name"],
            "source_type": _source_type(urls[0]),
            "content_roles": roles,
            "url": urls[0],
            "registered_urls": urls,
            "target_ids": ["environment_data"],
            "collection_mode": "representative_sample",
            "collection_strategy": "Agent 下载并检查实际内容后，用简单文件卡登记用途和覆盖主体。",
            "status": "complete",
            "findings": "；".join(dict.fromkeys(item["summary"] for item in items)),
            "limitations": sorted({value for item in items for value in item["limitations"]}),
        })
    coverage = profile["coverage"]
    related = {
        subject_type: [item["subject_name"] for item in coverage if item["subject_type"] == subject_type]
        for subject_type in _SUBJECT_TYPES
    }
    covered_names = [item["subject_name"] for item in coverage if item["status"] == "supported"]
    target_status = "covered" if profile["decision"] == "ready" else ("partial" if cards else "unavailable")
    target = {
        "target_id": "environment_data",
        "description": "当前环境已经下载并用文件卡确认的真实业务数据或任务直接使用的领域文件。",
        "priority": "core",
        "related_entities": related["entity"],
        "related_tools": related["tool"],
        "related_tasks": related["task"],
        "expected_content_roles": sorted({role_names[item["role"]] for item in cards}) or ["structured_data"],
        "expected_data": ["能够支撑 Seed 和 Step 1 代表性操作的真实数据文件。"],
        "variation_dimensions": ["文件卡按来源实际记录地域、时期、类型或文件集合的代表性变化。"],
        "connection_keys": [],
        "file_context": [],
        "status": target_status,
        "source_ids": sorted(by_source),
        "gap": None if target_status == "covered" else "仍未完整覆盖：" + "、".join(item["subject_name"] for item in coverage if item["status"] in {"missing", "partial"})[:800],
    }
    result = profile["decision"] if profile["decision"] != "continue" else "in_progress"
    summary = (
        f"Step 2 已按 URL 和内容哈希去重，保留 {len(cards)} 个有效文件、{len(sources)} 个来源。"
        f"文件卡确认 {len(covered_names)} 个实体、工具或任务具有直接 supported 数据；"
        "这里只记录文件规模、用途和覆盖主体，字段规范化、关系建模及跨源合并留给 Step 3。"
    )
    return {
        "schema_version": "3.0",
        "seed_global_id": profile["seed_global_id"],
        "seed_sha256": profile["seed_sha256"],
        "summary": summary,
        "investigation_targets": [target],
        "sources": sources,
        "expansion_findings": [],
        "task_file_formats": sorted({item["format"] for item in cards if item["role"] == "task_domain_files"}),
        "result": result,
    }


def _write_derived_outputs(run_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
    from env_gen.data_gen.analysis.collection_analysis import build_source_inventory, validate_source_inventory

    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    profile_schema = read_json(Path(config["collection_profile_schema_path"]), "文件卡 Schema")
    profile_errors = [
        f"{_pointer(error)}: {error.message}"
        for error in Draft202012Validator(
            profile_schema, format_checker=FormatChecker()
        ).iter_errors(profile)
    ]
    if profile_errors:
        raise RuntimeError("collection_profile 无效：" + "; ".join(profile_errors[:12]))
    write_json(run_dir / COLLECTION_PROFILE_PATH, profile)
    research = save_source_research(run_dir, _compat_source_research(run_dir, profile))
    inventory = build_source_inventory(
        run_dir,
        seed_global_id=config["seed_global_id"],
        seed_sha256=config["seed_sha256"],
        source_research=research,
    )
    errors = validate_source_inventory(inventory, Path(config["source_inventory_schema_path"]))
    if errors:
        raise RuntimeError("source_inventory 无效：" + "; ".join(errors[:12]))
    write_json(run_dir / SOURCE_INVENTORY_PATH, inventory)
    return inventory


def _finalize_agent_result(run_dir: Path) -> tuple[str, dict[str, Any]]:
    """Record coarse facts after the Agent has ended its collection loop."""

    run_dir = run_dir.resolve()
    report = read_json(control_path(run_dir, CONTROL_COLLECTION_RESULT), "Agent 采集结果")
    decision, summary, cards, independent = _validate_agent_result(run_dir, report)
    profile = _build_profile(
        run_dir,
        cards,
        decision=decision,
        agent_summary=summary,
        independent=independent,
    )
    _validate_ready_floor(run_dir, profile)
    _write_download_evidence(run_dir, cards)
    inventory = _write_derived_outputs(run_dir, profile)
    write_json(control_path(run_dir, CONTROL_SOURCE_FINALIZATION), {
        "schema_version": "4.0",
        "result": decision,
        "file_count": len(cards),
        "source_research_sha256": file_sha256(run_dir / SOURCE_RESEARCH_PATH),
        "source_inventory_sha256": file_sha256(run_dir / SOURCE_INVENTORY_PATH),
        "collection_profile_sha256": file_sha256(run_dir / COLLECTION_PROFILE_PATH),
        "finalized_at": _now(),
    })
    write_json(control_path(run_dir, CONTROL_RAW_INTEGRITY_SNAPSHOT), business_snapshot(run_dir))
    return decision, inventory


def _prepare_collection(run_dir: Path) -> None:
    run_dir = run_dir.resolve()
    for relative in ("workspace/raw", "workspace/prepared", ".datagen"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    read_saved_scenario_research(run_dir)


def run_data_collection(*, run_dir: Path, agent_runner: AgentRunner) -> tuple[str, dict[str, Any], int]:
    """Let one Agent own the collection loop, then materialize its file cards."""

    run_dir = run_dir.resolve()
    _prepare_collection(run_dir)
    control_path(run_dir, CONTROL_SOURCE_FINALIZATION).unlink(missing_ok=True)
    control_path(run_dir, CONTROL_RAW_INTEGRITY_SNAPSHOT).unlink(missing_ok=True)
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    timeout = int(config["collection_policy"].get("source_collection_total_seconds", 2400))
    calls = 0
    try:
        calls += 1
        agent_runner(_build_collection_prompt(run_dir), timeout, ())
        if not control_path(run_dir, CONTROL_COLLECTION_RESULT).is_file():
            raise RuntimeError("Agent 结束时没有写入 .datagen/collection_result.json")
        try:
            decision, inventory = _finalize_agent_result(run_dir)
        except Exception as validation_error:
            calls += 1
            repair_error: Exception | None = None
            try:
                agent_runner(
                    _build_result_repair_prompt(run_dir, validation_error),
                    min(timeout, _RESULT_REPAIR_SECONDS),
                    (),
                )
            except Exception as error:
                repair_error = error
            try:
                decision, inventory = _finalize_agent_result(run_dir)
            except Exception:
                if repair_error is not None:
                    raise repair_error
                raise
    except Exception as error:
        raise DataCollectionError(f"Step 2 Agent 未形成有效采集结果：{error}") from error
    return decision, inventory, calls


__all__ = [
    "DataCollectionError",
    "read_saved_source_research",
    "run_data_collection",
    "save_source_research",
    "save_source_research_file",
    "seed_task_label",
    "source_research_receipt_issues",
]
