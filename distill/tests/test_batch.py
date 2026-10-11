import json
from pathlib import Path
import subprocess
import sys
import threading
import time

from types import SimpleNamespace


def _jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_cli_passes_explicit_output_limit_to_runner(tmp_path, monkeypatch):
    from distill.__main__ import build_parser, _common

    monkeypatch.setenv("KIMI_API_KEY", "test-key")
    arguments = build_parser().parse_args([
        "taskgen", "--input-root", str(tmp_path), "--output-root", str(tmp_path / "runs"),
        "--max-context-size", "262144", "--max-output-size", "131072",
    ])
    options = _common(arguments)

    assert options["max_context_size"] == 262144
    assert options["max_output_size"] == 131072


def test_completed_task_with_tool_timeout_is_still_verified(tmp_path, monkeypatch):
    from distill import __main__ as cli
    from task_gen.eval_cases import EvalCase

    case = EvalCase(tmp_path / "source", {"task_id": "t1", "task_text": "Return 8.",
                                         "environment_id": "example"},
                    {}, tmp_path / "initial", None, [])
    monkeypatch.setenv("KIMI_API_KEY", "test-key")
    monkeypatch.setattr("task_gen.eval_cases.load_cases", lambda *a, **k: [case])
    monkeypatch.setattr(cli, "make_server_config", lambda *a, **k: {})

    def run(prompt, initial, config, output, **kwargs):
        (output / "raw").mkdir(parents=True)
        _jsonl(output / "raw/environment_tool_calls.jsonl", [{
            "tool": "slow", "error": "tool timed out", "failure_kind": "timeout",
        }])
        return {"steps": [], "compactions": []}

    monkeypatch.setattr(cli, "run_k3_distillation", run)
    monkeypatch.setattr("distill.taskgen_verifier.verify_distilled_case", lambda *a, **k: {
        "status": "verified", "outcome": "pass",
    })
    args = cli.build_parser().parse_args([
        "taskgen", "--input-root", str(tmp_path), "--output-root", str(tmp_path / "runs"),
        "--base-url", "http://localhost/v1", "--verify-after-each",
    ])
    cli._run_taskgen(args)
    summary = json.loads(next((tmp_path / "runs").glob("*/summary.json")).read_text())
    result = summary["results"][0]

    assert result["status"] == "completed"
    assert result["tool_timeout_count"] == 1
    assert result["verification"]["outcome"] == "pass"


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
            "tool": "threaded_analysis",
            "result": {
                "success": False,
                "error": {
                    "code": "analysis_failed",
                    "message": "can't start new thread",
                },
            },
            "error": "tool returned success=false",
        },
        {
            "tool": "slow",
            "result": None,
            "error": "工具调用超过 300 秒",
            "failure_kind": "timeout",
        },
    ])

    errors = _environment_errors(trace)

    assert [error["tool"] for error in errors] == [
        "compute", "simulate", "modes", "threaded_analysis",
    ]
    assert [error["code"] for error in errors] == [
        "backend_unavailable", "runtime_error", "runtime_error", "analysis_failed",
    ]

    from distill.environment_errors import tool_timeouts
    assert tool_timeouts(trace) == [{
        "line": 6,
        "tool": "slow",
        "code": "timeout",
        "message": "工具调用超过 300 秒",
        "retryable": True,
    }]


def test_case_runtime_backend_uses_bundle_declaration(tmp_path):
    from distill.__main__ import _case_runtime_backend

    source_run = tmp_path / "run"
    intermediate = source_run / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "step_5_bundle.json").write_text(json.dumps({
        "toolgen_delivery": {"runtime": {
            "schema_version": "1.0",
            "backend": "python_profile",
            "profile_id": "py-test",
        }}
    }), encoding="utf-8")

    case = SimpleNamespace(source_run=source_run, runtime={"binding_path": "/unused"})

    assert _case_runtime_backend(case) == "python_profile"


def test_case_runtime_backend_prefers_override_metadata(tmp_path):
    from distill.__main__ import _case_runtime_backend

    case = SimpleNamespace(
        source_run=tmp_path / "missing-run",
        runtime={"runtime_backend": "docker"},
    )

    assert _case_runtime_backend(case) == "docker"


def test_case_runtime_backend_caches_source_run_bundle(tmp_path):
    from distill.__main__ import _case_runtime_backend, _source_run_runtime_backend

    source_run = tmp_path / "run"
    intermediate = source_run / "intermediate"
    intermediate.mkdir(parents=True)
    bundle = intermediate / "step_5_bundle.json"
    bundle.write_text(json.dumps({
        "runtime": {"schema_version": "1.0", "backend": "python_profile"}
    }), encoding="utf-8")
    case = SimpleNamespace(source_run=source_run, runtime={})
    _source_run_runtime_backend.cache_clear()

    assert _case_runtime_backend(case) == "python_profile"
    bundle.unlink()
    assert _case_runtime_backend(case) == "python_profile"
    _source_run_runtime_backend.cache_clear()


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


def test_batch_circuit_breaker_stops_all_active_processes(tmp_path):
    from distill.runner import EnvironmentErrorCircuitBreaker, _monitor_environment_errors

    breaker = EnvironmentErrorCircuitBreaker(2)
    processes = []
    monitors = []
    for index in range(2):
        trace = tmp_path / f"calls-{index}.jsonl"
        _jsonl(trace, [{
            "tool": f"tool-{index}",
            "result": {
                "success": False,
                "error": {
                    "code": "analysis_failed",
                    "message": "can't start new thread",
                },
            },
        }])
        marker = tmp_path / f"stop-{index}.json"
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        monitor = threading.Thread(
            target=_monitor_environment_errors,
            args=(process, trace, None, marker),
            kwargs={"poll_seconds": 0.01, "circuit_breaker": breaker},
        )
        processes.append(process)
        monitors.append((monitor, marker))
        monitor.start()
    try:
        for monitor, _ in monitors:
            monitor.join(timeout=3)
        for process in processes:
            process.wait(timeout=3)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert breaker.event.is_set()
    assert breaker.snapshot()["observed_count"] == 2
    for _, marker in monitors:
        stop = json.loads(marker.read_text(encoding="utf-8"))
        assert stop["reason"] == "batch_environment_error_limit_reached"


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


def test_adaptive_concurrency_halves_after_repeated_upstream_errors(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 0.0)
    controller = _AdaptiveConcurrencyController(maximum=100, minimum=25)

    for _ in range(3):
        controller.observe({"upstream_final_error_count": 1})

    assert controller.current == 50
    assert controller.history[-1]["action"] == "decrease"
    assert controller.history[-1]["from"] == 100
    assert controller.history[-1]["to"] == 50


def test_adaptive_concurrency_does_not_throttle_for_terminal_tokenhub_504(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 0.0)
    controller = _AdaptiveConcurrencyController(maximum=150, minimum=20)
    timeout = {
        "upstream_final_error_count": 1,
        "upstream_stop": {
            "stream_error": {
                "type": "timeout_error",
                "code": "504001",
                "source": "upstream",
            },
        },
    }

    for _ in range(20):
        controller.observe(timeout)

    assert controller.current == 150
    assert controller.history == []


def test_adaptive_concurrency_halves_after_repeated_mcp_startup_failures(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 0.0)
    controller = _AdaptiveConcurrencyController(maximum=100, minimum=25)

    for _ in range(3):
        controller.observe({"mcp_startup_failure_count": 1})

    assert controller.current == 50
    assert controller.history[-1]["reason"] == "3 MCP startup failures in recent tasks"


def test_model_transport_stats_excludes_local_harness_rejections(tmp_path):
    from distill.__main__ import _model_transport_stats

    responses = tmp_path / "model_responses.jsonl"
    _jsonl(responses, [
        {
            "status": 503,
            "error": "mcp_tools_missing_from_first_model_request",
            "origin": "local_harness",
        },
        {"status": 200},
        {"status": 503, "error": "upstream unavailable", "attempts": [{"attempt": 1}]},
    ])

    assert _model_transport_stats(responses) == (3, 1, 1)


def test_model_transport_stats_counts_error_inside_successful_sse(tmp_path):
    from distill.__main__ import _model_transport_stats

    responses = tmp_path / "model_responses.jsonl"
    _jsonl(responses, [{"request_id": 1, "status": 200, "attempts": []}])
    model_io = tmp_path / "model_io"
    model_io.mkdir()
    (model_io / "000001.response.sse").write_text(
        'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        'event: error\n'
        'data: {"error":{"type":"upstream_error",'
        '"code":"upstream_stream_truncated"}}\n\n',
        encoding="utf-8",
    )

    assert _model_transport_stats(responses) == (1, 1, 0)


def test_adaptive_concurrency_respects_its_floor(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 0.0)
    controller = _AdaptiveConcurrencyController(maximum=100, minimum=25)

    for _ in range(20):
        controller.observe({"environment_error_count": 1})

    assert controller.current == 25
    assert all(item["to"] >= 25 for item in controller.history)


def test_adaptive_concurrency_error_at_floor_restarts_stable_recovery(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 0.0)
    controller = _AdaptiveConcurrencyController(maximum=75, minimum=10, initial=10)

    for _ in range(49):
        controller.observe({})
    for _ in range(3):
        controller.observe({"upstream_final_error_count": 1})
    for _ in range(66):
        controller.observe({})

    assert controller.current == 10

    controller.observe({})

    assert controller.current == 15


def test_adaptive_concurrency_recovers_after_50_stable_results(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 0.0)
    controller = _AdaptiveConcurrencyController(maximum=100, minimum=25)
    controller.current = 25

    for _ in range(50):
        controller.observe({})

    assert controller.current == 37
    assert controller.history[-1]["action"] == "increase"
    assert controller.history[-1]["from"] == 25
    assert controller.history[-1]["to"] == 37


def test_adaptive_concurrency_recovers_conservatively_from_low_target(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 0.0)
    controller = _AdaptiveConcurrencyController(maximum=75, minimum=10, initial=25)
    controller.current = 10

    for _ in range(50):
        controller.observe({})

    assert controller.current == 15
    assert controller.history[-1]["from"] == 10
    assert controller.history[-1]["to"] == 15


def test_adaptive_concurrency_steps_down_under_io_pressure(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 31.5)
    controller = _AdaptiveConcurrencyController(maximum=100, minimum=25, initial=75)

    controller.observe({})

    assert controller.current == 50
    assert controller.history[-1]["reason"] == "full I/O pressure avg10 31.5% >= 25.0%"


def test_adaptive_concurrency_does_not_increase_under_moderate_io_pressure(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 12.0)
    controller = _AdaptiveConcurrencyController(maximum=100, minimum=25, initial=50)

    for _ in range(50):
        controller.observe({})

    assert controller.current == 50
    assert controller.history == []


def test_adaptive_concurrency_can_start_below_its_maximum():
    from distill.__main__ import _AdaptiveConcurrencyController

    controller = _AdaptiveConcurrencyController(maximum=100, minimum=25, initial=50)

    assert controller.current == 50


def test_bounded_results_obeys_adaptive_dispatch_target(monkeypatch):
    from distill.__main__ import _AdaptiveConcurrencyController, _bounded_results

    monkeypatch.setattr("distill.__main__.os.getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr("distill.__main__._available_memory_bytes", lambda: 512 * 1024 ** 3)
    monkeypatch.setattr("distill.__main__._io_full_pressure_avg10", lambda: 0.0)
    controller = _AdaptiveConcurrencyController(maximum=4, minimum=1)
    controller.current = 2
    lock = threading.Lock()
    active = 0
    peak = 0

    def run(case):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {"case": str(case), "environment_error_count": 0}

    results, count, reason = _bounded_results(
        list(range(8)),
        run,
        max_workers=4,
        max_environment_errors=None,
        concurrency_controller=controller,
    )

    assert len(results) == 8
    assert peak == 2
    assert count == 0
    assert reason is None


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


def test_processed_case_names_skips_every_recorded_result(tmp_path):
    from distill.__main__ import _processed_case_names

    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "results": [
            {"case": "done", "status": "completed"},
            {"case": "failed", "status": "failed"},
            {"case": "environment", "status": "environment_stopped"},
        ]
    }), encoding="utf-8")

    assert _processed_case_names([summary]) == {"done", "failed", "environment"}


def test_selected_case_names_skips_every_dispatched_case(tmp_path):
    from distill.__main__ import _selected_case_names

    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({
        "cases": [
            {"case_name": "first"},
            {"case_name": "second"},
        ]
    }), encoding="utf-8")

    assert _selected_case_names([selection]) == {"first", "second"}


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


def test_distillation_prompt_removes_exact_environment_summary_prefix():
    from distill.__main__ import _distillation_prompt

    case = SimpleNamespace(
        task={"task_text": "固定环境摘要。\n\n完成这项具体任务。"},
        environment={"summary": "固定环境摘要。"},
    )

    assert _distillation_prompt(case) == "完成这项具体任务。"


def test_distillation_prompt_preserves_nonmatching_or_summary_only_tasks():
    from distill.__main__ import _distillation_prompt

    different = SimpleNamespace(
        task={"task_text": "任务自己的背景。\n\n完成任务。"},
        environment={"summary": "固定环境摘要。"},
    )
    summary_only = SimpleNamespace(
        task={"task_text": "固定环境摘要。"},
        environment={"summary": "固定环境摘要。"},
    )

    assert _distillation_prompt(different) == "任务自己的背景。\n\n完成任务。"
    assert _distillation_prompt(summary_only) == "固定环境摘要。"


def test_environment_setup_error_recognizes_invalid_mcp_schema():
    from distill.__main__ import _is_environment_setup_error

    assert _is_environment_setup_error(ValueError(
        "工具 create 不符合 Schema：pattern 使用了 Python 内联正则标志；"
        "MCP JSON Schema 必须使用 ECMA-262 兼容表达式"
    ))
    assert not _is_environment_setup_error(RuntimeError("model request failed"))


def test_delivery_override_rebinds_tools_without_changing_task_state(tmp_path, monkeypatch):
    from task_gen.eval_cases import EvalCase
    from distill.__main__ import _override_case_deliveries

    binding = tmp_path / "environments" / "example" / "binding.json"
    binding.parent.mkdir(parents=True)
    binding.write_text("{}", encoding="utf-8")
    repaired_tools = binding.parent / "tools/tools_repaired.json"
    repaired_tools.parent.mkdir()
    repaired_tools.write_text('{"tools": []}', encoding="utf-8")
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
    assert result.runtime["tools_path"] == str(repaired_tools.resolve())
    assert result.runtime["initial_state"] == str(package_root / "state")

    repaired_tools.unlink()
    import pytest
    with pytest.raises(ValueError, match="delivery is missing tools_repaired.json"):
        _override_case_deliveries([case], tmp_path)


def test_delivery_override_resolves_binding_environment_id_alias(tmp_path, monkeypatch):
    from task_gen.eval_cases import EvalCase
    from distill.__main__ import _override_case_deliveries

    binding = tmp_path / "environments" / "scenario-package" / "binding.json"
    binding.parent.mkdir(parents=True)
    binding.write_text(json.dumps({"environment_id": "tool-environment"}), encoding="utf-8")
    repaired_tools = binding.parent / "tools/tools_repaired.json"
    repaired_tools.parent.mkdir()
    repaired_tools.write_text('{"tools": []}', encoding="utf-8")
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
    from task_gen.eval_cases import load_cases

    ignored = tmp_path / "ignored" / "intermediate"
    ignored.mkdir(parents=True)
    (ignored.parent / "tasks.json").write_text("[]", encoding="utf-8")
    (ignored / "step_5_bundle.json").write_text("not json", encoding="utf-8")

    assert load_cases(
        tmp_path,
        selected_case_names={"selected_environment__task1"},
    ) == []


def test_delete_task_state_preserves_trajectory_and_raw_evidence(tmp_path):
    from distill.taskgen_verifier import delete_task_state

    task = tmp_path / "case"
    workspace = task / "descriptive-task-workspace"
    private = task / "private-state"
    raw = task / "raw"
    workspace.mkdir(parents=True)
    private.mkdir()
    raw.mkdir()
    (workspace / "result.txt").write_text("result", encoding="utf-8")
    (private / "records.sqlite").write_text("database", encoding="utf-8")
    (private / "path-map.json").write_text(json.dumps({
        "workspace_root": str(workspace),
        "record_database": str(private / "records.sqlite"),
        "scope_mounts": {"files": "."},
    }), encoding="utf-8")
    (task / "trajectory.json").write_text("{}", encoding="utf-8")
    (raw / "wire.jsonl").write_text("{}\n", encoding="utf-8")

    removed = delete_task_state(task)

    assert set(removed) == {str(workspace), str(private)}
    assert not workspace.exists()
    assert not private.exists()
    assert (task / "trajectory.json").is_file()
    assert (raw / "wire.jsonl").is_file()


def test_state_cleanup_is_deferred_only_for_missing_verifier_verdict():
    from distill.__main__ import _defer_state_cleanup

    assert _defer_state_cleanup({
        "status": "completed",
        "verification": {"status": "verification_error"},
    }, True)
    assert not _defer_state_cleanup({
        "status": "completed",
        "verification": {"status": "verified", "outcome": "fail"},
    }, True)
    assert not _defer_state_cleanup({"status": "failed"}, True)
    assert not _defer_state_cleanup({"status": "completed"}, False)
