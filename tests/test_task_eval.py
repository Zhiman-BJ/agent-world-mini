"""Task execution and MCP gateway tests. Static-verifier tests are archived at dd43b76."""
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from task_gen.task_eval import DEFAULT_INPUT_ROOT, _agent_prompt, _result_counts, evaluate_case, load_cases, main, run_evaluation
from task_gen.task_eval_mcp import call_environment_tool, serve
ROOT = Path(__file__).resolve().parents[1]

class TaskEvalTest(unittest.TestCase):
    def test_agent_prompt_is_the_original_task_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = load_cases(self._write_case_at(root / "source"))[0]

            prompt = _agent_prompt(case, 7)

            self.assertEqual(prompt, "Return the current value.")

    def test_task_evaluation_uses_backend_budget_and_explicit_override(self) -> None:
        for backend, override, expected in [('react', None, 50), ('kimi', None, 65), ('kimi', 7, 7)]:
            with self.subTest(backend=backend, override=override), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                case = load_cases(self._write_case_at(root / 'source'))[0]
                observed = []

                def agent(prompt, workspace, config, trace):
                    observed.append(json.loads(config.read_text())['max_tool_calls'])
                    self.assertEqual(prompt, "Return the current value.")
                    return 'Done.'

                result = evaluate_case(case, root / 'evaluation', {'agent_backend': backend},
                                       max_tool_calls=override, agent_run_fn=agent,
                                       verifier_run_fn=lambda **kwargs: {'outcome': 'pass'})
                self.assertIsNone(result['agent_error'])
                self.assertEqual(observed, [expected])

    def test_cli_rejects_nonpositive_max_tool_calls(self) -> None:
        with patch("sys.argv", ["task-eval", "--max-tool-calls", "0"]):
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_nonpositive_llm_timeout(self) -> None:
        with patch("sys.argv", ["task-eval", "--llm-timeout-seconds", "0"]):
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(raised.exception.code, 2)

    def test_defaults_to_this_repository_task_runs(self) -> None:
        self.assertEqual(DEFAULT_INPUT_ROOT, ROOT / "runs/taskgen")

    def test_result_counts_do_not_treat_error_null_as_infrastructure_failure(self) -> None:
        counts = _result_counts([
            {"outcome": "pass", "error": None},
            {"outcome": "fail", "error": None},
            {"outcome": "indeterminate", "error": None},
            {"outcome": "infrastructure_error", "error": "boom"},
        ])
        self.assertEqual(counts, {
            "passed_count": 1,
            "failed_count": 1,
            "indeterminate_count": 1,
            "verification_error_count": 0,
            "infrastructure_error_count": 1,
        })
    def test_load_cases_reads_tasks_with_their_environment_and_initial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "source_run"
            (run / "intermediate").mkdir(parents=True)
            (run / "tasks/task1/initial").mkdir(parents=True)
            (run / "tasks.json").write_text(json.dumps([{
                "task_id": "task1",
                "environment_id": "env1",
                "task_text": "Return the current value.",
                "initial_state": "tasks/task1/initial",
                "available_tools": [],
                "reference": {"answer": "The value is 7.", "tool_calls": []},
            }]), encoding="utf-8")
            (run / "intermediate/step_5_bundle.json").write_text(json.dumps({
                "environment": {"environment_id": "env1", "tools": []},
            }), encoding="utf-8")

            cases = load_cases(root)

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].task["task_id"], "task1")
            self.assertEqual(cases[0].environment["environment_id"], "env1")
            self.assertEqual(cases[0].initial_state, run / "tasks/task1/initial")

    def test_load_cases_reads_toolgen_delivery_binding_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_case_at(root / "environments/source_run")
            bundle_path = root / "environments/source_run/intermediate/step_5_bundle.json"
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            package = root / "delivery/env"
            (package / "tools").mkdir(parents=True)
            repaired = package / "tools/tools_repaired.json"
            repaired.write_text("{}", encoding="utf-8")
            bundle["toolgen_delivery"] = {"binding_path": str(package / "binding.json")}
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

            cases = load_cases(root)

            self.assertEqual(cases[0].runtime, {
                "binding_path": str(package / "binding.json"),
                "tools_path": str(repaired),
            })

    def test_run_evaluation_samples_reproducibly_and_writes_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input/source_run"
            self._write_case_at(source)
            task = json.loads((source / "tasks.json").read_text(encoding="utf-8"))[0]
            tasks = []
            for index in range(4):
                candidate = dict(task, task_id=f"task{index}")
                candidate["initial_state"] = f"tasks/task{index}/initial"
                state = source / candidate["initial_state"]
                state.mkdir(parents=True, exist_ok=True)
                (state / "value.txt").write_text("7", encoding="utf-8")
                tasks.append(candidate)
            (source / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")

            with patch("task_gen.task_eval.evaluate_case") as evaluate:
                evaluate.side_effect = lambda case, workspace, config, **kwargs: {
                    "source_run": case.source_run.name,
                    "task_id": case.task["task_id"],
                    "environment_id": case.task["environment_id"],
                    "task_text": case.task["task_text"],
                    "outcome": "pass",
                    "error": None,
                }
                run_dir = run_evaluation(
                    root / "input", root / "output", {}, {},
                    sample_size=2, sample_seed=17,
                )

            selection = json.loads((run_dir / "selection.json").read_text(encoding="utf-8"))
            self.assertEqual(selection["population_size"], 4)
            self.assertEqual(selection["sample_size"], 2)
            self.assertEqual(selection["sample_seed"], 17)
            self.assertEqual(selection["selected_count"], 2)
            self.assertEqual(len({item["task_id"] for item in selection["cases"]}), 2)

    def test_load_cases_rejects_initial_state_outside_source_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "intermediate").mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (source / "tasks.json").write_text(json.dumps([{
                "task_id": "task1",
                "initial_state": "../outside",
            }]), encoding="utf-8")
            (source / "intermediate/step_5_bundle.json").write_text(json.dumps({
                "environment": {"environment_id": "env1", "tools": []},
            }), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "初态路径"):
                load_cases(root)

    def test_load_cases_keeps_only_latest_run_per_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            older = root / "20260830_000000_env_model"
            newer = root / "20260831_000000_env_model"
            self._write_case_at(older)
            self._write_case_at(newer)
            (newer / "tasks.json").write_text("[]", encoding="utf-8")

            cases = load_cases(root)

            self.assertEqual(cases, [])

    def test_load_cases_preserves_collection_entries_with_shared_environment_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environments = root / "environments"
            first = environments / "scenario_0422"
            second = environments / "scenario_0423"
            self._write_case_at(first)
            self._write_case_at(second)
            (root / "manifest.json").write_text(json.dumps({
                "environment_count": 2,
                "task_count": 2,
                "environments": [
                    {"link": "environments/scenario_0422", "task_count": 1},
                    {"link": "environments/scenario_0423", "task_count": 1},
                ],
            }), encoding="utf-8")

            cases = load_cases(root)

            self.assertEqual(
                [case.source_run.name for case in cases],
                ["scenario_0422", "scenario_0423"],
            )

    def test_evaluate_case_rejects_source_state_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = self._write_case_at(root / "source")
            initial = root / "source/tasks/task1/initial"
            source_file = initial / "value.txt"
            case = load_cases(input_root)[0]
            def tampering_agent(_prompt: str, _workspace: Path, _config: Path, _trace: Path) -> str:
                source_file.write_text("changed", encoding="utf-8")
                return "The value is 7."

            with self.assertRaisesRegex(ValueError, "来源初态.*修改"):
                evaluate_case(case, root / "evaluation", {}, agent_run_fn=tampering_agent)

    def test_evaluate_case_rejects_tool_set_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = self._write_case_at(root / "source")
            tasks_path = root / "source/tasks.json"
            task = json.loads(tasks_path.read_text())[0]
            task["available_tools"] = []
            tasks_path.write_text(json.dumps([task]), encoding="utf-8")
            case = load_cases(input_root)[0]

            with self.assertRaisesRegex(ValueError, "available_tools"):
                evaluate_case(case, root / "evaluation", {})

    def test_mcp_gateway_reports_invalid_tool_output_as_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = self._write_case_at(root / "source")
            case = load_cases(input_root)[0]
            state = root / "source/tasks/task1/initial/value.txt"

            def failing_call(
                _code: str,
                _arguments: dict[str, object],
                tool_workspace: Path,
                *_: object,
            ) -> dict[str, object]:
                (tool_workspace / "value.txt").write_text("corrupted", encoding="utf-8")
                return {"kind": None, "result": {"success": False}, "error": None}

            result = call_environment_tool(
                "read_value",
                {},
                {tool["name"]: tool for tool in case.environment["tools"]},
                root / "source/tasks/task1/initial",
                timeout=10,
                memory_limit=1024 * 1024,
                write_limit=1024 * 1024,
                call_tool_fn=failing_call,
            )

            self.assertIn("success=true", result["error"])
            self.assertEqual(state.read_text(encoding="utf-8"), "7")

    def test_mcp_gateway_returns_tool_timeout_as_retryable(self) -> None:
        from task_gen.task_eval_mcp import TaskEvalMcpServer

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = {
                "name": "slow_tool",
                "description": "A slow fixture.",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
                "internal": {"code": ""},
            }
            server = TaskEvalMcpServer({
                "workspace": str(root),
                "trace": str(root / "trace.jsonl"),
                "max_tool_calls": 1,
                "timeout": 1,
                "memory_limit": 1024 * 1024,
                "write_limit": 1024 * 1024,
                "tools": [tool],
            })
            record = {
                "tool": "slow_tool",
                "arguments": {},
                "result": None,
                "error": "工具调用超过 1 秒",
                "failure_kind": "timeout",
            }
            with patch("task_gen.task_eval_mcp.call_environment_tool", return_value=record):
                response = server.handle({
                    "method": "tools/call",
                    "params": {"name": "slow_tool", "arguments": {}},
                })

            error = response["structuredContent"]["error"]
            self.assertEqual(error["code"], "timeout")
            self.assertTrue(error["retryable"])
            self.assertTrue(response["isError"])

    def test_mcp_gateway_marks_tool_budget_exhaustion_for_hard_stop(self) -> None:
        from harness.mcp_protocol import RpcError
        from task_gen.task_eval_mcp import TaskEvalMcpServer

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "tool_budget_stop.json"
            tool = {
                "name": "ok_tool",
                "description": "A fixture.",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
                "internal": {"code": ""},
            }
            server = TaskEvalMcpServer({
                "workspace": str(root),
                "trace": str(root / "trace.jsonl"),
                "budget_stop_marker": str(marker),
                "max_tool_calls": 1,
                "timeout": 1,
                "memory_limit": 1024 * 1024,
                "write_limit": 1024 * 1024,
                "tools": [tool],
            })
            record = {
                "tool": "ok_tool", "arguments": {}, "result": {}, "error": None,
            }
            request = {
                "method": "tools/call",
                "params": {"name": "ok_tool", "arguments": {}},
            }
            with patch("task_gen.task_eval_mcp.call_environment_tool", return_value=record):
                server.handle(request)
                with self.assertRaises(RpcError):
                    server.handle(request)

            stop = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(stop["reason"], "tool_call_limit_exceeded")
            self.assertEqual(stop["limit"], 1)
            exhausted = json.loads((root / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(exhausted["failure_kind"], "tool_budget_exceeded")

    def test_workspace_mode_uses_direct_tool_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "task"
            workspace.mkdir()
            database = root / "private-records.sqlite"
            database.touch()
            tool = {
                "name": "read_value",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
                "usageConditions": {"sideEffects": []},
                "internal": {"code": "unused"},
            }
            direct_result = {
                "kind": None,
                "result": {"success": True},
                "error": None,
            }

            with patch(
                "harness.execution._sandbox_call_tool",
                side_effect=AssertionError("bubblewrap path must not be used"),
            ), patch("harness.execution._direct_call_tool", return_value=direct_result) as direct:
                result = call_environment_tool(
                    "read_value", {}, {"read_value": tool}, workspace,
                    timeout=10, memory_limit=1024 * 1024, write_limit=1024 * 1024,
                    record_database=database, scope_mounts={},
                )

            self.assertIsNone(result["error"])
            direct.assert_called_once()

    def test_mcp_gateway_rejects_symlink_before_a_second_call_can_follow_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            secret = root / "host-secret.txt"
            secret.write_text("HOST_SECRET", encoding="utf-8")
            create_link = {
                "name": "create_link",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
                "internal": {"code": f"""
def run(arguments, context):
    (context.workspace_root / 'link.txt').symlink_to({str(secret)!r})
    return {{'success': True, 'data': {{}}}}
"""},
            }
            read_link = {
                "name": "read_link",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
                "internal": {"code": """
def run(arguments, context):
    return {'success': True, 'data': {'content': (context.workspace_root / 'link.txt').read_text()}}
"""},
            }
            tools = {tool["name"]: tool for tool in (create_link, read_link)}

            created = call_environment_tool(
                "create_link", {}, tools, workspace,
                timeout=2, memory_limit=256 * 1024 * 1024, write_limit=1024 * 1024,
            )
            read = call_environment_tool(
                "read_link", {}, tools, workspace,
                timeout=2, memory_limit=256 * 1024 * 1024, write_limit=1024 * 1024,
            )

            self.assertIsNotNone(created["error"])
            self.assertFalse((workspace / "link.txt").exists())
            self.assertNotIn("HOST_SECRET", json.dumps(read, ensure_ascii=False))

    def test_mcp_server_uses_supported_protocol_and_rejects_unknown_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({
                "workspace": str(root),
                "trace": str(root / "trace.jsonl"),
                "max_tool_calls": 1,
                "timeout": 10,
                "memory_limit": 1024 * 1024,
                "write_limit": 1024 * 1024,
                "tools": [],
            }), encoding="utf-8")
            stdin = io.StringIO("\n".join([
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "invalid"},
                }),
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "missing", "arguments": {}},
                }),
            ]))
            stdout = io.StringIO()

            serve(config, stdin, stdout)

            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
            self.assertEqual(responses[1]["error"]["code"], -32602)

    def test_mcp_server_preserves_business_error_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({
                "workspace": str(root),
                "trace": str(root / "trace.jsonl"),
                "max_tool_calls": 1,
                "timeout": 10,
                "memory_limit": 1024 * 1024,
                "write_limit": 1024 * 1024,
                "tools": [{
                    "name": "get_person",
                    "description": "Get a person.",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object"},
                    "internal": {"code": "unused"},
                }],
            }), encoding="utf-8")
            stdin = io.StringIO(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_person", "arguments": {"person_id": 1}},
            }))
            stdout = io.StringIO()
            record = {
                "tool": "get_person",
                "arguments": {"person_id": 1},
                "result": {
                    "success": False,
                    "error": {"code": "not_found", "message": "Person not found."},
                },
                "error": "工具返回值必须包含 success=true",
            }

            with patch(
                "task_gen.task_eval_mcp.call_environment_tool",
                return_value=record,
            ):
                serve(config, stdin, stdout)

            response = json.loads(stdout.getvalue())
            self.assertTrue(response["result"]["isError"])
            self.assertEqual(
                response["result"]["structuredContent"]["error"]["code"],
                "not_found",
            )

    @staticmethod
    def _write_case(root: Path, initial: Path) -> Path:
        source = root / "source"
        TaskEvalTest._write_case_at(source, initial)
        return root

    @staticmethod
    def _write_case_at(source: Path, initial: Path | None = None) -> Path:
        (source / "intermediate").mkdir(parents=True)
        target = source / "tasks/task1/initial"
        target.parent.mkdir(parents=True, exist_ok=True)
        if initial is None:
            target.mkdir(exist_ok=True)
            (target / "value.txt").write_text("7", encoding="utf-8")
        elif not target.exists():
            import shutil
            shutil.copytree(initial, target)
        task = {
            "task_id": "task1",
            "environment_id": "env1",
            "task_text": "Return the current value.",
            "initial_state": "tasks/task1/initial",
            "available_tools": [{
                "name": "read_value",
                "description": "Read the current value.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "outputSchema": {"type": "object"},
            }],
            "reference": {"answer": "The value is 7.", "tool_calls": []},
        }
        (source / "tasks.json").write_text(json.dumps([task]), encoding="utf-8")
        (source / "intermediate/step_5_bundle.json").write_text(json.dumps({
            "environment": {
                "environment_id": "env1",
                "name": "Value environment",
                "description": "Contains a value.",
                "resources": [],
                "rules": [],
                "tools": [{**task["available_tools"][0], "internal": {"code": "unused"}}],
            },
        }), encoding="utf-8")
        return source.parent


if __name__ == "__main__":
    unittest.main()
