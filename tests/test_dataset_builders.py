"""Dataset entry points, verdict selection and trajectory pairing."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from distill.dataset.append import indexed_passes
from distill.dataset.training import paired_jsonl, portable_runtime_value
from distill.dataset.windows import validate_conversation


@pytest.mark.parametrize("module", ["training", "paths", "merge", "windows", "append"])
def test_dataset_module_has_portable_cli(module):
    command = [sys.executable, "-m", f"distill.dataset.{module}"]
    help_result = subprocess.run([*command, "--help"], capture_output=True, text=True)
    assert help_result.returncode == 0, help_result.stderr
    assert "--output" in help_result.stdout
    empty = subprocess.run(command, capture_output=True, text=True)
    assert empty.returncode != 0
    assert "required" in empty.stderr


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_pairing_rejects_different_ids_and_lengths(tmp_path):
    left, right = tmp_path / "left.jsonl", tmp_path / "right.jsonl"
    write_rows(left, [{"trajectory_id": "one"}])
    write_rows(right, [{"trajectory_id": "one"}])
    assert len(list(paired_jsonl(left, right))) == 1
    write_rows(right, [{"trajectory_id": "two"}])
    with pytest.raises(ValueError):
        list(paired_jsonl(left, right))
    write_rows(right, [])
    with pytest.raises(ValueError):
        list(paired_jsonl(left, right))


def test_pass_index_uses_canonical_verified_success_only(tmp_path):
    path = tmp_path / "index.jsonl"
    raw = tmp_path / "raw"
    raw.mkdir()
    write_rows(raw / "model_requests.jsonl", [{"request": {}}])
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text("{}")
    verdict = tmp_path / "verdict.json"
    verdict.write_text('{"outcome":"pass"}')
    rows = [{"case": "good", "canonical": True, "label": "verifier_pass"},
            {"case": "failed", "canonical": True, "label": "verifier_fail"},
            {"case": "attempt", "canonical": False, "label": "verifier_pass"}]
    for row in rows:
        row.update(trajectory_path=str(trajectory), verification={"verdict": str(verdict)})
    write_rows(path, rows)
    assert set(indexed_passes(path, set())) == {"good"}
    assert indexed_passes(path, {"good"}) == {}
    verdict.write_text('{"outcome":"fail"}')
    with pytest.raises(ValueError, match="non-PASS"):
        indexed_passes(path, set())


def test_training_window_preserves_call_result_pairing():
    record = {"trajectory_id": "test", "tools": [{"function": {"name": "lookup"}}],
              "messages": [{"role": "assistant", "tool_calls": [
                  {"id": "c1", "function": {"name": "lookup", "arguments": "{}"}}]},
                  {"role": "tool", "tool_call_id": "c1", "content": "{}"}]}
    validate_conversation(record)
    for mutation in ("undefined", "unresolved", "duplicate"):
        changed = deepcopy(record)
        if mutation == "undefined":
            changed["messages"][0]["tool_calls"][0]["function"]["name"] = "other"
        elif mutation == "unresolved":
            changed["messages"].pop()
        else:
            changed["messages"].append(deepcopy(changed["messages"][-1]))
        with pytest.raises(ValueError):
            validate_conversation(changed)


def test_host_paths_are_portable_without_rewriting_business_values():
    value = {"path": "/data1/agent_world/toolgen_semiconductor_expanded/runtime/python",
             "answer": {"gap": 1.0, "id": "TiS3"}}
    result = portable_runtime_value(value)
    assert result["path"] == "/runtime/python"
    assert result["answer"] == value["answer"]


@pytest.mark.parametrize("module", ["retry_zero_task_environments", "follow_zero_task_recovery"])
def test_recovery_commands_require_deployment_roots(module):
    from importlib import import_module
    parser = import_module(f"task_gen.program.{module}").build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--batch-root", "/fixture/batch"])
    parsed = parser.parse_args(["--batch-root", "/fixture/batch", "--delivery-root", "/fixture/delivery"])
    assert parsed.delivery_root == [Path("/fixture/delivery")]


@pytest.mark.parametrize("module", ["repair_tool_descriptions", "update_cleaned_tool_definitions"])
def test_maintenance_command_help_imports_installed_dependencies(module):
    result = subprocess.run([sys.executable, "-m", f"scripts.{module}", "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
