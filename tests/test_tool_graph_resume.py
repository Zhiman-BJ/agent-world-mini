"""检查阶段暂停、恢复、失败恢复及未完成执行产物的保留。"""
from contextlib import ExitStack
import json
import fcntl
import signal
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from task_gen.tool_graph import pipeline


def test_pause_every_step_then_resume_without_replaying():
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory)
        config = root / "config.yaml"
        config.write_text(f"paths:\n  output_root: {root}/runs\n")
        outputs = [
            ("load_environment", {"environment": {}}),
            ("build_graph", {"tool_graph": {"edges": [], "prerequisites": []}}),
            ("sample_chains", {"tasks": [], "sampling_report": {}}),
            ("execute_chains", {"tasks": []}),
            ("compose_tasks", {"tasks": []}),
            ("validate_tasks", {"tasks": []}),
        ]
        mocks = [stack.enter_context(patch.object(pipeline, name, return_value=value)) for name, value in outputs]
        assert pipeline.run(config, stop_after=0) is None
        run_dir = next((root / "runs").iterdir())
        with (run_dir / ".pipeline.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(ValueError, match="仍有 pipeline"):
                pipeline.run(resume=run_dir)
        # 恢复不能依赖原 YAML 仍存在，也不能被之后的配置修改影响。
        config.unlink()
        for step in (1, 2):
            assert pipeline.run(resume=run_dir, stop_after=step) is None
        before = (run_dir / "intermediate/step_2_bundle.json").read_bytes()
        def interrupted_execution(_):
            (run_dir / "tasks/partial").mkdir()
            (run_dir / "tasks/partial/evidence.txt").write_text("keep")
            raise RuntimeError("interrupted")
        mocks[3].side_effect = interrupted_execution
        with pytest.raises(RuntimeError, match="interrupted"):
            pipeline.run(resume=run_dir)
        assert json.loads((run_dir / "run.json").read_text())["status"] == "failed"
        mocks[3].side_effect = None
        for step in (3, 4, 5):
            assert pipeline.run(resume=run_dir, stop_after=step) is None
        assert next(run_dir.glob("tasks_interrupted_*/partial/evidence.txt")).read_text() == "keep"
        assert (run_dir / "intermediate/step_2_bundle.json").read_bytes() == before
        result = pipeline.run(resume=run_dir)
        assert result.task_count == 0
        assert [m.call_count for m in mocks] == [1, 1, 1, 2, 1, 1]
        assert json.loads((run_dir / "run.json").read_text())["status"] == "completed"
        with pytest.raises(ValueError, match="不能同时覆盖"):
            pipeline.run(resume=run_dir, overrides={"model": "different"})


def test_stop_request_does_not_start_next_stage():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / "config.yaml"
        config.write_text(f"paths:\n  output_root: {root}/runs\n")
        with patch.object(pipeline, "load_environment", return_value={"environment": {}}) as load:
            with patch.object(pipeline, "build_graph") as graph:
                pipeline.run(config, should_stop=lambda: load.called)
        graph.assert_not_called()
        run_dir = next((root / "runs").iterdir())
        assert (run_dir / "intermediate/step_0_bundle.json").exists()
        assert json.loads((run_dir / "run.json").read_text())["status"] == "paused"


def test_signal_requests_pause_and_restores_handlers():
    previous = signal.getsignal(signal.SIGTERM)
    with pipeline._stop_signals() as should_stop:
        assert not should_stop()
        signal.raise_signal(signal.SIGTERM)
        assert should_stop()
    assert signal.getsignal(signal.SIGTERM) == previous
