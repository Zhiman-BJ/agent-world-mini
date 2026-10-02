import json
from pathlib import Path
import subprocess
import sys
import time

from types import SimpleNamespace


def _jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_environment_errors_exclude_business_failures(tmp_path):
    from distill.__main__ import _environment_errors

    trace = tmp_path / "calls.jsonl"
    _jsonl(trace, [
        {
            "tool": "lookup",
            "result": {"success": False, "error": {"code": "not_found", "message": "missing"}},
            "error": "tool returned success=false",
        },
        {
            "tool": "compute",
            "result": {
                "success": False,
                "error": {"code": "backend_unavailable", "message": "backend is offline"},
            },
            "error": "tool returned success=false",
        },
        {
            "tool": "simulate",
            "result": {"success": False, "error": {"code": "runtime_error", "message": "crashed"}},
            "runtime_error": "RuntimeError: crashed",
        },
        {
            "tool": "modes",
            "result": None,
            "error": "ImportError: libtorch_cpu.so: failed to map segment from shared object",
        },
        {
            "tool": "slow",
            "result": None,
            "error": "工具调用超过 300 秒",
            "failure_kind": "timeout",
        },
    ])

    errors = _environment_errors(trace)

    assert [error["tool"] for error in errors] == ["compute", "simulate", "modes"]
    assert [error["code"] for error in errors] == [
        "backend_unavailable", "runtime_error", "runtime_error",
    ]

    from distill.environment_errors import tool_timeouts
    assert tool_timeouts(trace) == [{
        "line": 5,
        "tool": "slow",
        "code": "timeout",
        "message": "工具调用超过 300 秒",
        "retryable": True,
    }]


def test_per_task_monitor_stops_process_after_its_error_limit(tmp_path):
    from distill.runner import _monitor_environment_errors

    trace = tmp_path / "calls.jsonl"
    _jsonl(trace, [
        {
            "tool": "first",
            "result": {"success": False, "error": {
                "code": "backend_unavailable", "message": "missing backend",
            }},
        },
        {
            "tool": "second",
            "result": {"success": False, "error": {
                "code": "runtime_error", "message": "runtime failed",
            }},
        },
    ])
    marker = tmp_path / "environment_stop.json"
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        _monitor_environment_errors(process, trace, 2, marker, poll_seconds=0.01)
        process.wait(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    stop = json.loads(marker.read_text(encoding="utf-8"))
    assert stop["reason"] == "environment_error_limit_reached"
    assert stop["observed_count"] == 2
    assert [error["tool"] for error in stop["errors"]] == ["first", "second"]


def test_bounded_results_stops_dispatching_at_environment_error_limit():
    from distill.__main__ import _bounded_results

    started = []

    def run(case):
        started.append(case)
        if case != 0:
            time.sleep(0.05)
        return {"case": str(case), "environment_error_count": 2 if case == 0 else 0}

    results, count, reason = _bounded_results(
        list(range(10)), run, max_workers=3, max_environment_errors=2
    )

    assert sorted(started) == [0, 1, 2]
    assert [result["case"] for result in results] == ["0", "1", "2"]
    assert count == 2
    assert reason == "environment_error_limit_reached: 2 >= 2"


def test_completed_case_names_only_skips_completed_results(tmp_path):
    from distill.__main__ import _completed_case_names

    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "results": [
            {"case": "done", "status": "completed"},
            {"case": "retry", "status": "failed"},
        ]
    }), encoding="utf-8")

    assert _completed_case_names([summary]) == {"done"}


def test_select_summary_case_names_preserves_the_original_batch_order(tmp_path):
    from distill.__main__ import _select_summary_case_names

    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "results": [
            {"case": "case-b", "status": "completed"},
            {"case": "case-a", "status": "failed"},
        ]
    }), encoding="utf-8")

    assert _select_summary_case_names(summary) == ["case-b", "case-a"]


def test_delivery_override_rebinds_tools_without_changing_task_state(tmp_path, monkeypatch):
    from task_gen.task_eval import EvalCase
    from distill.__main__ import _override_case_deliveries

    binding = tmp_path / "environments" / "example" / "binding.json"
    binding.parent.mkdir(parents=True)
    binding.write_text("{}", encoding="utf-8")
    initial_state = tmp_path / "task-initial"
    reference_state = tmp_path / "task-reference"
    package_root = tmp_path / "delivery-environment"
    package_root.mkdir()
    tool = {
        "name": "fixed_tool",
        "description": "fixed",
        "usageConditions": {},
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"},
        "internal": {"code": "def run(arguments, context): return {'success': True}"},
    }
    delivery = SimpleNamespace(
        binding_path=binding.resolve(),
        package=SimpleNamespace(
            environment={"schema_version": "2.0", "environment_id": "example"},
            tools=(tool,),
            package_root=package_root,
        ),
        software_root=tmp_path / "software",
        python_path=tmp_path / "software" / "bin" / "python",
    )
    monkeypatch.setattr("harness.delivery.load_delivery", lambda path: delivery)
    case = EvalCase(
        source_run=tmp_path / "source",
        task={"task_id": "task1", "environment_id": "example", "task_text": "keep me"},
        environment={"environment_id": "example", "tools": [{"name": "old"}]},
        initial_state=initial_state,
        reference_state=reference_state,
        reference_calls=[{"tool": "old"}],
        runtime={"binding_path": "/old/binding.json"},
    )

    result = _override_case_deliveries([case], tmp_path)[0]

    assert result.task is case.task
    assert result.initial_state == initial_state
    assert result.reference_state == reference_state
    assert result.reference_calls is case.reference_calls
    assert result.environment["tools"] == [tool]
    assert result.runtime["binding_path"] == str(binding.resolve())
    assert result.runtime["initial_state"] == str(package_root / "state")


def test_delivery_override_resolves_binding_environment_id_alias(tmp_path, monkeypatch):
    from task_gen.task_eval import EvalCase
    from distill.__main__ import _override_case_deliveries

    binding = tmp_path / "environments" / "scenario-package" / "binding.json"
    binding.parent.mkdir(parents=True)
    binding.write_text(json.dumps({"environment_id": "tool-environment"}), encoding="utf-8")
    package_root = tmp_path / "delivery-environment"
    package_root.mkdir()
    delivery = SimpleNamespace(
        binding_path=binding.resolve(),
        package=SimpleNamespace(
            environment={"schema_version": "2.0", "environment_id": "tool-environment"},
            tools=(),
            package_root=package_root,
        ),
        software_root=None,
        python_path=None,
    )
    monkeypatch.setattr("harness.delivery.load_delivery", lambda path: delivery)
    case = EvalCase(
        source_run=tmp_path / "source",
        task={"task_id": "task1", "environment_id": "tool-environment", "task_text": "keep me"},
        environment={"environment_id": "tool-environment", "tools": []},
        initial_state=tmp_path / "task-initial",
        reference_state=tmp_path / "task-reference",
        reference_calls=[],
        runtime={},
    )

    result = _override_case_deliveries([case], tmp_path)[0]

    assert result.runtime["binding_path"] == str(binding.resolve())
    assert result.environment["environment_id"] == "tool-environment"


def test_load_cases_skips_unselected_bundle_before_reading_it(tmp_path):
    from task_gen.task_eval import load_cases

    ignored = tmp_path / "ignored" / "intermediate"
    ignored.mkdir(parents=True)
    (ignored.parent / "tasks.json").write_text("[]", encoding="utf-8")
    (ignored / "step_5_bundle.json").write_text("not json", encoding="utf-8")

    assert load_cases(
        tmp_path,
        selected_case_names={"selected_environment__task1"},
    ) == []
