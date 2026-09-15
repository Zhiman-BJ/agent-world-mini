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
        merge_output(bundle, {"environment": {}}, ProgramPipelineStep.SCORING_GENERATE)


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
