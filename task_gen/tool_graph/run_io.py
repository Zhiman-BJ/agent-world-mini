"""AppendOnlyBundle 转换、合并与运行产物读写。

职责：
- 从 AppendOnlyBundle 提取每阶段所需字段，构造对应 Input
- 将阶段 Output 作为新字段合入 AppendOnlyBundle（禁止覆盖）
- 创建一次运行的目录结构（runs/taskgen/<时间戳>_<环境>_<模型>/）
- 模型调用审计日志（llm_calls.jsonl）的并发安全追加
- 每个阶段产物的存档与读档（一阶段一文件，存 intermediate/ 下）
- 最终产物（tasks.json / rejected.json / run.json）的写入

不负责：
- 不执行建图、采样、工具调用、转写或验证等阶段业务逻辑。

目录结构（每个 Bundle 都包含截至该 Step 的全部平铺字段）：
    runs/taskgen/<run_name>/
    ├── run.json                 # 配置快照 + 元信息
    ├── llm_calls.jsonl          # 每行一次完整模型调用（含 step、prompt、answer）
    ├── tasks.json               # validation 通过的 TaskArtifact[]
    ├── rejected.json            # validation 失败的完整候选记录[]
    ├── tasks/                  # 本次运行独占的任务 workspace
    │   └── <task_id>/
    │       ├── initial/
    │       └── final/
    └── intermediate/
        ├── step_0_bundle.json
        ├── step_1_bundle.json
        ├── ...
        ├── step_5_bundle.json
        └── step_<n>_progress.jsonl  # 仅昂贵批处理按需追加
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any

import yaml

from .contracts import (
    BuildGraphInput,
    ComposeTasksInput,
    EnvironmentLoadInput,
    ExecuteChainsInput,
    PipelineStep,
    Config,
    RunResult,
    SampleChainsInput,
    AppendOnlyBundle,
    ValidateTasksInput,
)


_LLM_CALL_LOCK = Lock()


# ---------------------------------------------------------------------------
# AppendOnlyBundle ↔ 阶段契约
# ---------------------------------------------------------------------------

def load_config(
    config_path: Path,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """按“代码默认值 < YAML < 命令行覆盖”读取一次运行配置。"""
    config_path = config_path.expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是 object")

    values = asdict(Config())
    paths = raw.get("paths") or {}
    if not isinstance(paths, dict):
        raise ValueError("配置 paths 必须是 object")
    for name in ("environment_dir", "schema_dir", "output_root"):
        if name in paths:
            values[name] = _resolve_path(paths[name], config_path.parent)
    for name in ("llm", "graph", "planning", "execution", "cost"):
        if name in raw:
            if not isinstance(raw[name], dict):
                raise ValueError(f"配置 {name} 必须是 object")
            values[name] = raw[name]

    for name, value in (overrides or {}).items():
        if value is None:
            continue
        if name in ("model", "backend"):
            values["llm"] = dict(values["llm"])
            values["llm"][name] = value
            continue
        if name not in ("environment_dir", "schema_dir", "output_root"):
            raise ValueError(f"不支持的命令行覆盖：{name}")
        values[name] = _resolve_path(value, Path.cwd())
    return Config(**values)


def _resolve_path(value: Any, base: Path) -> Path:
    """把绝对路径原样解析，相对路径按指定来源目录解析。"""
    path = Path(str(value)).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def to_environment_load_input(config: Config) -> EnvironmentLoadInput:
    """构造统一携带完整配置的 Step 0 输入。"""
    return {"config": config}


def to_build_graph_input(bundle: AppendOnlyBundle, config: Config) -> BuildGraphInput:
    """只从 AppendOnlyBundle 提取 Step 1 所需字段。"""
    return {"config": config, "environment": bundle["environment"]}


def to_sample_chains_input(bundle: AppendOnlyBundle, config: Config) -> SampleChainsInput:
    """只从 AppendOnlyBundle 提取 Step 2 所需字段。"""
    return {
        "config": config,
        "environment": bundle["environment"],
        "tool_graph": bundle["tool_graph"],
    }


def to_execute_chains_input(
    bundle: AppendOnlyBundle,
    config: Config,
    run_dir: Path,
) -> ExecuteChainsInput:
    """只从 AppendOnlyBundle 提取 Step 3 所需字段。"""
    return {
        "config": config,
        "run_dir": run_dir,
        "environment": bundle["environment"],
        "tasks": bundle["tasks"],
    }


def to_compose_tasks_input(bundle: AppendOnlyBundle, config: Config) -> ComposeTasksInput:
    """只从 AppendOnlyBundle 提取 Step 4 所需字段。"""
    return {
        "config": config,
        "environment": bundle["environment"],
        "tasks": bundle["tasks"],
    }


def to_validate_tasks_input(
    bundle: AppendOnlyBundle,
    config: Config,
    run_dir: Path,
) -> ValidateTasksInput:
    """只从 AppendOnlyBundle 提取 Step 5 所需字段。"""
    return {
        "config": config,
        "run_dir": run_dir,
        "environment": bundle["environment"],
        "tasks": bundle["tasks"],
    }


def merge_output(
    bundle: AppendOnlyBundle,
    output: Mapping[str, Any],
    step: PipelineStep,
) -> None:
    """合入阶段输出；`tasks` 可逐阶段扩充，其他业务字段禁止覆盖。"""
    duplicates = bundle.keys() & output.keys() - {"_step", "tasks"}
    if duplicates:
        raise KeyError(f"Bundle 字段已存在：{', '.join(sorted(duplicates))}")
    bundle.update(output)
    bundle["_step"] = step.value


def create_run_dir(config: Config) -> Path:
    """在 output_root 下创建本次运行目录及 ``tasks/``、``intermediate/``。

    目录名格式：<时间戳>_<环境名>_<模型名>，已存在则报错（不覆盖历史运行）。
    """
    def safe(value: object) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_.-") or "unknown"

    name = "_".join((
        datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
        safe(config.environment_dir.name),
        safe(config.llm.get("model") or "no_model"),
    ))
    run_dir = config.output_root.expanduser().resolve() / name
    (run_dir / "tasks").mkdir(parents=True)
    (run_dir / "intermediate").mkdir()
    return run_dir


def save_run_meta(run_dir: Path, config: Config) -> None:
    """把配置快照和运行元信息写入 run.json。"""
    _write_json(run_dir / "run.json", {
        "status": "running",
        "created_at": datetime.now().astimezone().isoformat(),
        "run_dir": str(run_dir),
        "config": asdict(config),
    })


def update_run_meta(run_dir: Path, values: Mapping[str, Any]) -> None:
    """原子合并运行状态、阶段耗时或失败信息。"""
    path = run_dir / "run.json"
    meta = _read_json(path)
    if not isinstance(meta, dict):
        raise ValueError("run.json 顶层必须是 object")
    meta.update(values)
    _write_json(path, meta)


def append_llm_call(run_dir: Path, record: Mapping[str, Any]) -> None:
    """线程安全地追加一条完整模型调用记录。"""
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _LLM_CALL_LOCK:
        with (run_dir / "llm_calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


# ---------------------------------------------------------------------------
# 完整 Bundle 检查点
# ---------------------------------------------------------------------------

def save_bundle(run_dir: Path, bundle: AppendOnlyBundle) -> None:
    """按 Bundle 的 `_step` 保存完整平铺快照。"""
    try:
        step = PipelineStep(bundle["_step"])
    except (KeyError, ValueError) as error:
        raise ValueError("Bundle 缺少合法的 _step") from error
    _write_json(_bundle_path(run_dir, step), bundle)


def load_bundle(run_dir: Path, step: PipelineStep) -> AppendOnlyBundle | None:
    """读取指定 Step 的完整 Bundle；文件不存在时返回 None。"""
    path = _bundle_path(run_dir, step)
    if not path.is_file():
        return None
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"Bundle 顶层必须是 object：{path}")
    return value


def load_latest_bundle(run_dir: Path) -> tuple[int, AppendOnlyBundle] | None:
    """读取编号最大的完整 Bundle；尚无检查点时返回 None。"""
    for index in range(len(PipelineStep) - 1, -1, -1):
        bundle = load_bundle(run_dir, list(PipelineStep)[index])
        if bundle is not None:
            return index, bundle
    return None


def append_progress(run_dir: Path, step: PipelineStep, record: dict[str, Any]) -> None:
    """为昂贵批处理追加一条进度记录到 step_<n>_progress.jsonl。"""
    path = _progress_path(run_dir, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def load_progress(run_dir: Path, step: PipelineStep) -> list[dict[str, Any]]:
    """读取指定 Step 的全部进度记录；文件不存在时返回空列表。"""
    path = _progress_path(run_dir, step)
    if not path.is_file():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"进度记录第 {number} 行不是 object：{path}")
        records.append(value)
    return records


# ---------------------------------------------------------------------------
# 最终产物
# ---------------------------------------------------------------------------

def save_tasks(run_dir: Path, tasks: list[dict[str, Any]]) -> None:
    """把已分流的 ``TaskArtifact[]`` 直接写入 ``tasks.json``。"""
    _write_json(run_dir / "tasks.json", tasks)


def save_rejected(run_dir: Path, rejected: list[dict[str, Any]]) -> None:
    """把已分流的失败候选完整记录直接写入 ``rejected.json``。"""
    _write_json(run_dir / "rejected.json", rejected)


def finish_run(run_dir: Path, bundle: AppendOnlyBundle) -> RunResult:
    """机械分流 Step 5 候选、写最终文件并返回运行汇总。

    按 ``bundle["tasks"]`` 的原顺序处理每个候选：

    * ``candidate["validation"]["passed"] is True`` 时，只把
      ``candidate["task"]`` 放入 ``tasks.json``。
    * ``passed is False`` 时，把 candidate 完整外层记录原样放入
      ``rejected.json``，保留 task、全部错误、执行和重试信息。

    不在此重验、修补、去重或改变顺序。Step 5 保证 ``task`` 和 ``validation`` 的结构；
    若字段缺失或 passed 不是 bool，属于流水线契约错误，应报错并停止导出，
    不能静默归类。RunResult 的两个数量分别取最终两个数组的长度。
    """
    tasks = bundle.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("Bundle tasks 必须是 array")
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, candidate in enumerate(tasks):
        if not isinstance(candidate, dict) or not isinstance(candidate.get("validation"), dict):
            raise ValueError(f"tasks[{index}] 缺少 validation")
        passed = candidate["validation"].get("passed")
        if not isinstance(passed, bool):
            raise ValueError(f"tasks[{index}].validation.passed 必须是 bool")
        if passed:
            task = candidate.get("task")
            if not isinstance(task, dict):
                raise ValueError(f"tasks[{index}] 缺少正式 task")
            accepted.append(task)
        else:
            rejected.append(candidate)
    save_tasks(run_dir, accepted)
    save_rejected(run_dir, rejected)
    cost_report = bundle.get("cost_report")
    if not isinstance(cost_report, dict):
        cost_report = {}
    meta_path = run_dir / "run.json"
    meta = _read_json(meta_path) if meta_path.is_file() else {}
    if not isinstance(meta, dict):
        raise ValueError("run.json 顶层必须是 object")
    meta.update({
        "status": "completed",
        "completed_at": datetime.now().astimezone().isoformat(),
        "task_count": len(accepted),
        "rejected_count": len(rejected),
        "cost_report": cost_report,
    })
    _write_json(meta_path, meta)
    return RunResult(run_dir, len(accepted), len(rejected), cost_report)


def _step_number(step: PipelineStep) -> int:
    return int(step.value.split("_", 2)[1])


def _bundle_path(run_dir: Path, step: PipelineStep) -> Path:
    return run_dir / "intermediate" / f"step_{_step_number(step)}_bundle.json"


def _progress_path(run_dir: Path, step: PipelineStep) -> Path:
    return run_dir / "intermediate" / f"step_{_step_number(step)}_progress.jsonl"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取 JSON：{path}: {error}") from error


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
