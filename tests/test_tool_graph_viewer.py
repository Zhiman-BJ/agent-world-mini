from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/export_tool_graph_viewer.py"
OPEN_SCRIPT = ROOT / "scripts/open_tool_graph_viewer.sh"


def write_run(root: Path, name: str, *, status: str = "completed") -> Path:
    run_dir = root / name
    intermediate = run_dir / "intermediate"
    intermediate.mkdir(parents=True)
    environment_dir = root / "private-environment"
    tool = {
        "name": "list_items",
        "description": "List available items.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {"items": {"type": "array"}},
            "required": ["items"],
        },
        "internal": {"code": "SECRET_INTERNAL"},
    }
    inspect_tool = {
        "name": "inspect_item",
        "description": "Inspect one item.",
        "inputSchema": {
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
        "outputSchema": {"type": "object"},
        "internal": {"code": "ANOTHER_SECRET"},
    }
    environment = {
        "environment_id": "example_env",
        "name": "Example environment",
        "description": "Fixture environment.",
        "tools": [tool, inspect_tool],
    }
    edge = {
        "from_tool": "list_items",
        "to_tool": "inspect_item",
        "weight": 3,
        "reason": "The list yields identifiers.",
        "parameter_evidence": [],
        "state_evidence": [],
    }
    task_text = "Inspect the item. </script><script>bad()</script>"
    task = {
        "schema_version": "1.0",
        "task_id": "task1",
        "environment_id": "example_env",
        "task_text": task_text,
        "difficulty": {"tool_calls": 1},
        "initial_state": "tasks/task1/initial",
        "available_tools": [{key: tool[key] for key in ("name", "description", "inputSchema", "outputSchema")}],
        "reference": {
            "tool_calls": [{"tool": "list_items", "arguments": {}}],
            "answer": "One item was found.",
            "final_state": "tasks/task1/final",
        },
    }
    candidate = {
        "task_id": "task1",
        "chain": ["list_items"],
        "score": 3,
        "llm_review": {"original_chain": ["list_items"], "reason": "Already valid.", "error": None},
        "execution": {
            "success": True,
            "tool_calls": [{"tool": "list_items", "arguments": {}, "result": {"items": ["item-1"]}}],
            "initial_state": "tasks/task1/initial",
            "final_state": "tasks/task1/final",
            "error": None,
            "attempts": [],
        },
        "task_text": task_text,
        "reference_answer": "One item was found.",
        "compose_error": None,
        "task": task,
        "validation": {"passed": True, "errors": []},
    }
    (run_dir / "run.json").write_text(json.dumps({
        "status": status,
        "created_at": "2026-08-28T10:00:00+08:00",
        "run_dir": str(run_dir),
        "config": {
            "environment_dir": str(environment_dir),
            "llm": {"backend": "codex", "model": "test-model"},
        },
        "task_count": 1,
        "rejected_count": 0,
        "stage_timings_seconds": {"step_1_graph_build": 1.25},
    }), encoding="utf-8")
    (intermediate / "step_1_bundle.json").write_text(json.dumps({
        "environment": environment,
        "_step": "step_1_graph_build",
        "tool_graph": [edge],
    }), encoding="utf-8")
    (intermediate / "step_5_bundle.json").write_text(json.dumps({
        "environment": environment,
        "_step": "step_5_task_validate",
        "tool_graph": [edge],
        "tasks": [candidate],
    }), encoding="utf-8")
    return run_dir


class ToolGraphViewerExportTest(unittest.TestCase):
    def test_exports_multiple_runs_without_private_data_or_script_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_run(root, "run-one")
            write_run(root, "run-two")
            output = root / "viewer.html"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(root), "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            self.assertIn("run-one", html)
            self.assertIn("run-two", html)
            self.assertIn("task1", html)
            self.assertNotIn("SECRET_INTERNAL", html)
            self.assertNotIn(str(root / "private-environment"), html)
            self.assertNotIn("</script><script>bad()", html)
            self.assertIn("\\u003c/script", html)
            self.assertIn('"opacity": .32', html)
            self.assertIn('"text-overflow-wrap": "anywhere"', html)
            self.assertIn("function layoutDirectedComponent", html)
            self.assertIn("const xGap = 180, yGap = 64", html)
            self.assertIn("function layoutConnectedComponents", html)
            self.assertIn("baseEdges.connectedNodes()", html)
            self.assertIn("connectedElements.components()", html)
            self.assertIn("component.nodes().positions", html)
            self.assertIn("function placeIsolatedNodes", html)
            self.assertIn("state.cy.nodes().not(connected)", html)
            self.assertIn('id="inspector-splitter"', html)
            self.assertIn("function setupSplitter", html)
            self.assertIn("线越粗，直接依赖越强", html)
            self.assertIn("weight 2 条件依赖", html)
            self.assertIn("weight 1 辅助依赖", html)
            self.assertIn("黑色箭头 = 候选链调用顺序", html)
            self.assertIn("绿色底轨 = 已执行成功", html)
            self.assertIn("红色底轨 = 失败及后续步骤", html)
            self.assertIn("LLM 新增工具", html)
            self.assertIn('selector: ".llm-added-node.chain-node-failed"', html)
            self.assertIn("再次点击可取消选择", html)
            self.assertIn('id="environment-summary"', html)
            self.assertIn("Step 5 未通过原因", html)
            self.assertIn("工具详情", html)
            self.assertIn("直接边详情", html)
            self.assertIn("连边原因", html)
            self.assertIn("function focusNode", html)
            self.assertIn('state.cy.on("dbltap", "node"', html)
            self.assertIn("focus-unrelated", html)
            self.assertIn("node.animate", html)
            self.assertIn("const focusId = state.focused;", html)
            self.assertIn("if (state.focused === focusId)", html)
            self.assertIn('document.addEventListener("keydown"', html)
            self.assertIn('event.key === "Escape"', html)
            self.assertNotIn('id="focus-layout-button"', html)
            self.assertIn("function layoutSelectedTask", html)
            self.assertIn("const chainNames = [...new Set(task.chain)]", html)
            self.assertIn("const arcSpan = Math.PI * 1.5", html)
            self.assertIn("const relatedRadius", html)
            self.assertIn("const relatedInsideCount", html)
            self.assertIn("const innerRelated", html)
            self.assertIn("const outerRadius", html)
            self.assertIn("item.animate({ position", html)
            self.assertIn("layoutSelectedTask(task)", html)
            self.assertIn(".chain-item.active { outline: 2px solid var(--chain); outline-offset: -2px; }", html)
            self.assertNotIn(".chain-item.active { border-color: var(--accent)", html)
            self.assertIn(".chain-item:hover { outline: 1px solid #aebbb7; outline-offset: -1px; }", html)
            self.assertNotIn(".chain-item:hover { border-color:", html)

    def test_exports_last_execution_failure_for_chain_visualization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = write_run(root, "failed-task")
            bundle = run_dir / "intermediate/step_5_bundle.json"
            source = json.loads(bundle.read_text(encoding="utf-8"))
            candidate = source["tasks"][0]
            candidate["chain"] = ["list_items", "inspect_item"]
            candidate["llm_review"] = {
                "original_chain": ["inspect_item"],
                "reason": "Added list_items to discover item_id.",
                "error": None,
            }
            candidate["execution"] = {
                "success": False,
                "tool_calls": [{"tool": "list_items", "arguments": {}, "result": {"items": ["item-1"]}}],
                "initial_state": None,
                "final_state": None,
                "error": "item_id is required",
                "attempts": [{
                    "attempt": 1,
                    "success": False,
                    "tool_calls": [{"tool": "list_items", "arguments": {}, "result": {"items": ["item-1"]}}],
                    "failed_tool": "inspect_item",
                    "failed_arguments": {},
                    "failure_kind": "input_schema",
                    "failed_result": None,
                    "error": "item_id is required",
                }],
            }
            candidate["validation"] = {"passed": False, "errors": ["execution 未成功"]}
            bundle.write_text(json.dumps(source), encoding="utf-8")
            output = root / "viewer.html"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(run_dir), "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            payload = html.split('<script id="run-data" type="application/json">', 1)[1].split("</script>", 1)[0]
            task = json.loads(payload)[0]["tasks"][0]
            self.assertEqual(task["execution"]["failed_tool"], "inspect_item")
            self.assertEqual(task["execution"]["failure_kind"], "input_schema")

    def test_rejects_a_direct_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = write_run(Path(temporary), "failed-run", status="failed")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(run_dir)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(run_dir), result.stderr)
            self.assertIn("completed", result.stderr)

    def test_exports_a_step_one_bundle_without_completed_run_or_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = write_run(root, "running", status="running")
            bundle = run_dir / "intermediate/step_1_bundle.json"
            output = root / "graph.html"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(bundle), "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            payload = html.split('<script id="run-data" type="application/json">', 1)[1].split("</script>", 1)[0]
            exported = json.loads(payload)[0]
            self.assertEqual(exported["stage"], "step_1_graph_build")
            self.assertEqual(exported["counts"]["edges"], 1)
            self.assertEqual(exported["tasks"], [])

    def test_exports_unvalidated_candidates_from_an_intermediate_bundle_as_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = write_run(root, "running", status="running")
            source = json.loads((run_dir / "intermediate/step_1_bundle.json").read_text(encoding="utf-8"))
            source["_step"] = "step_2_chain_sample"
            source["tasks"] = [{"task_id": "task1", "chain": ["list_items"], "score": 3}]
            bundle = run_dir / "intermediate/step_2_bundle.json"
            bundle.write_text(json.dumps(source), encoding="utf-8")
            output = root / "planning.html"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(bundle), "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("rejected=0 pending=1", result.stdout)
            html = output.read_text(encoding="utf-8")
            payload = html.split('<script id="run-data" type="application/json">', 1)[1].split("</script>", 1)[0]
            exported = json.loads(payload)[0]
            self.assertIsNone(exported["tasks"][0]["passed"])
            self.assertEqual(exported["counts"]["pending"], 1)
            self.assertIn("const executionState", html)
            self.assertIn("not run", html)

    def test_open_script_stops_its_server_when_the_script_ends(self) -> None:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]

        process = subprocess.Popen(
            ["bash", str(OPEN_SCRIPT), str(port)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{port}/scripts/export_tool_graph_viewer.py"
        try:
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    self.fail(process.communicate()[0])
                try:
                    with urlopen(url, timeout=0.2) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("交互式静态 HTML", response.read().decode("utf-8"))
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        self.fail("viewer server did not become ready")
                    time.sleep(0.05)

            process.terminate()
            process.communicate(timeout=5)

            with self.assertRaises(URLError):
                urlopen(url, timeout=0.2)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
