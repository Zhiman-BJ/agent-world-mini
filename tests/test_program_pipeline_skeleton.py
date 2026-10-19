from __future__ import annotations

from pathlib import Path

import pytest


def test_bundle_merge_is_append_only():
    from task_gen.program.contracts import ProgramPipelineStep
    from task_gen.program.run_io import merge_output

    bundle = {"environment": {"environment_id": "e1"}}
    merge_output(bundle, {"tasks": []}, ProgramPipelineStep.SOLUTION_GENERATE)
    assert bundle["_step"] == ProgramPipelineStep.SOLUTION_GENERATE.value
    with pytest.raises(KeyError):
        merge_output(bundle, {"environment": {}}, ProgramPipelineStep.SOLUTION_GENERATE)


def test_pipeline_agent_retains_logs(tmp_path: Path, monkeypatch):
    from task_gen.program import pipeline
    from task_gen.program.contracts import Config
    from utils.search_agent import codex

    captured = {}
    monkeypatch.setattr(codex, "CodexAgentClient", lambda **options: captured.update(options) or captured)
    logs = tmp_path / "agent_logs" / "research"
    pipeline._client(Config(), web_research=True, log_directory=logs)
    assert captured["log_directory"] == logs
    assert captured["enable_web_search"] is True
    assert captured["reasoning_effort"] == "high"


def test_pipeline_can_stop_after_environment_and_resume(tmp_path: Path, monkeypatch):
    from task_gen.program import pipeline
    from task_gen.program.contracts import Config

    calls: list[str] = []
    monkeypatch.setattr(pipeline, "load_environment", lambda _input: calls.append("env") or {"environment": {"environment_id": "e1"}})
    config = Config(environment_package=tmp_path / "env", output_root=tmp_path / "runs")
    first = pipeline.run(config=config, stop_after=0)
    assert first is None
    run_dir = next((tmp_path / "runs").iterdir())
    pipeline.run(resume=run_dir, stop_after=0)
    assert calls == ["env"]


@pytest.mark.parametrize("location", ["top_level", "paths", "overrides"])
def test_retired_hardening_configuration_cannot_start_normal_generation(tmp_path, location):
    from task_gen.program.run_io import load_config
    from task_gen.program.utils.io import write_json

    source = str(tmp_path / "original.json")
    raw, overrides = {}, {}
    if location == "top_level":
        raw["hardening_source_path"] = source
    elif location == "paths":
        raw["paths"] = {"hardening_source_path": source}
    else:
        overrides["hardening_source_path"] = source
    config = tmp_path / "config.json"
    write_json(config, raw)
    with pytest.raises(ValueError, match="single_agent_hardening"):
        load_config(config, overrides)


def test_retired_hardening_run_cannot_resume_as_normal_generation(tmp_path, monkeypatch):
    from task_gen.program import pipeline
    from task_gen.program.utils.io import write_json

    write_json(tmp_path / "run.json", {"config": {"hardening_source_path": "original.json"}})
    monkeypatch.setattr(pipeline, "load_environment", lambda *_: pytest.fail("legacy run started"))
    with pytest.raises(ValueError, match="single_agent_hardening"):
        pipeline.run(resume=tmp_path)


def test_program_parser_no_longer_accepts_the_old_hardening_route():
    from task_gen.program.pipeline import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--harden-task", "original.json"])
