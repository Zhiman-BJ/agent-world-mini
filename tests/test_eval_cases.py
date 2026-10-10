import json

import pytest

from task_gen.eval_cases import load_cases


def _write_case(root, *, initial_state="tasks/task1/initial"):
    (root / "intermediate").mkdir(parents=True)
    state = root / "tasks/task1/initial"
    state.mkdir(parents=True)
    (root / "tasks.json").write_text(json.dumps([{
        "task_id": "task1",
        "environment_id": "env1",
        "task_text": "Return the current value.",
        "initial_state": initial_state,
        "available_tools": [],
        "reference": {"answer": "7", "tool_calls": []},
    }]), encoding="utf-8")
    (root / "intermediate/step_5_bundle.json").write_text(json.dumps({
        "environment": {"environment_id": "env1", "tools": []},
    }), encoding="utf-8")
    return root


def test_load_cases_reads_the_task_environment_and_initial_state(tmp_path):
    run = _write_case(tmp_path / "source-run")

    cases = load_cases(tmp_path)

    assert len(cases) == 1
    assert cases[0].task["task_id"] == "task1"
    assert cases[0].environment["environment_id"] == "env1"
    assert cases[0].initial_state == run / "tasks/task1/initial"


def test_load_cases_rejects_initial_state_outside_the_source_run(tmp_path):
    run = _write_case(tmp_path / "source-run")
    outside = tmp_path / "outside"
    outside.mkdir()
    tasks = json.loads((run / "tasks.json").read_text(encoding="utf-8"))
    tasks[0]["initial_state"] = "../outside"
    (run / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")

    with pytest.raises(ValueError, match="初态路径越界"):
        load_cases(tmp_path)


def test_load_cases_rejects_symlinks_in_initial_state(tmp_path):
    run = _write_case(tmp_path / "source-run")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (run / "tasks/task1/initial/link.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="符号链接"):
        load_cases(tmp_path)
