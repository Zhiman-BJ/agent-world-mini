"""Persistence and Bundle conversion for the Program pipeline."""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any, Mapping

from .contracts import (
    AppendOnlyBundle,
    Config,
    ProgramPipelineStep,
    RunResult,
)

_LLM_CALL_LOCK = Lock()


def load_config(config_path: Path | None = None, overrides: Mapping[str, Any] | None = None) -> Config:
    """Load a small JSON/YAML config; CLI overrides win over file values."""
    values: dict[str, Any] = {}
    if config_path is not None:
        path = config_path.expanduser().resolve()
        text = path.read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            import yaml

            raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ValueError("配置顶层必须是 object")
        values.update(raw.get("paths") or {})
        values.update({key: raw[key] for key in ("model", "agent_timeout_seconds", "environment_package", "output_root", "tools_path", "delivery_root", "binding_path", "package_id", "scenario_research_path", "research_fixture_path", "candidates_path") if key in raw})
        if isinstance(raw.get("llm"), dict) and raw["llm"].get("model"):
            values.setdefault("model", raw["llm"]["model"])
        if "policy" in raw:
            from .utils.contracts import ProgramGenerationPolicy

            policy_values = raw["policy"] or {}
            if not isinstance(policy_values, dict):
                raise ValueError("配置 policy 必须是 object")
            values["policy"] = ProgramGenerationPolicy(**{
                name: policy_values[name]
                for name in ProgramGenerationPolicy.__dataclass_fields__
                if name in policy_values
            })
    for key, value in (overrides or {}).items():
        if value is not None:
            values[key] = value
    for key in ("environment_package", "output_root", "tools_path", "delivery_root", "binding_path", "scenario_research_path", "research_fixture_path", "candidates_path"):
        if values.get(key) is not None:
            values[key] = Path(values[key]).expanduser().resolve()
    return Config(**{field.name: values[field.name] for field in fields(Config) if field.name in values})


def _json_default(value: Any) -> str:
    return str(value)


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def merge_output(bundle: AppendOnlyBundle, output: Mapping[str, Any], step: ProgramPipelineStep) -> None:
    duplicates = (bundle.keys() & output.keys()) - {"tasks", "_step"}
    if duplicates:
        raise KeyError(f"Bundle 字段已存在：{', '.join(sorted(duplicates))}")
    bundle.update(output)
    bundle["_step"] = step.value


def create_run_dir(config: Config) -> Path:
    safe = lambda value: re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_.-") or "unknown"
    source_name = (
        config.package_id
        or (config.binding_path.name if config.binding_path is not None else None)
        or (
            config.environment_package.name
            if config.environment_package is not None
            else "unknown"
        )
    )
    name = "_".join((datetime.now().strftime("%Y%m%d_%H%M%S_%f"), safe(source_name), safe(config.model)))
    run_dir = config.output_root.expanduser().resolve() / name
    (run_dir / "intermediate").mkdir(parents=True)
    (run_dir / "tasks").mkdir()
    return run_dir


def save_run_meta(run_dir: Path, config: Config) -> None:
    values = asdict(config)
    _write(run_dir / "run.json", {"status": "running", "created_at": datetime.now().astimezone().isoformat(), "config": values})


def update_run_meta(run_dir: Path, values: Mapping[str, Any]) -> None:
    meta = _read(run_dir / "run.json")
    meta.update(values)
    _write(run_dir / "run.json", meta)


def save_bundle(run_dir: Path, bundle: AppendOnlyBundle) -> None:
    step = str(bundle.get("_step", "unknown"))
    _write(run_dir / "intermediate" / f"{step}.json", bundle)


def load_latest_bundle(run_dir: Path) -> tuple[int, AppendOnlyBundle] | None:
    files = []
    for path in sorted((run_dir / "intermediate").glob("step_*.json")):
        value = _read(path)
        if value.get("_step") in {item.value for item in ProgramPipelineStep}:
            files.append(path)
    if not files:
        return None
    path = files[-1]
    bundle = _read(path)
    values = list(ProgramPipelineStep)
    try:
        index = [item.value for item in values].index(bundle.get("_step"))
    except ValueError as error:
        raise ValueError(f"检查点无效：{path}") from error
    return index, bundle


def append_llm_call(run_dir: Path, record: Mapping[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
    with _LLM_CALL_LOCK:
        with (run_dir / "llm_calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line)


def finish_run(run_dir: Path, bundle: AppendOnlyBundle) -> RunResult:
    final = list(bundle.get("tasks", []))
    rejected = list(bundle.get("rejected", []))
    _write(run_dir / "tasks.json", final)
    _write(run_dir / "rejected.json", rejected)
    update_run_meta(run_dir, {"status": "completed", "task_count": len(final), "rejected_count": len(rejected)})
    return RunResult(run_dir, len(final), len(rejected))
