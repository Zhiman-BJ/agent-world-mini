from __future__ import annotations

import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.task_eval import (
    DEFAULT_INPUT_ROOT,
    _TaskEvalCodexClient,
    _agent_prompt,
    _result_counts,
    _run_agent,
    evaluate_case,
    load_cases,
    main,
    run_evaluation,
)
from task_gen.task_eval_verifier import (
    _counterfactual_evidence,
    aggregate_results,
    build_evidence,
    calibrate_verifier,
    generate_verifier,
    prepare_verifier,
    run_verifier,
    validate_verifier,
)
from task_gen.task_eval_mcp import call_environment_tool, serve
from task_gen.tool_graph.llm import InferenceResult


ROOT = Path(__file__).resolve().parents[1]


class TaskEvalTest(unittest.TestCase):
    def test_build_evidence_contains_bounded_files_and_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial = root / "initial"
            final = root / "final"
            initial.mkdir()
            final.mkdir()
            (initial / "value.json").write_text('{"value": 7}', encoding="utf-8")
            (final / "value.json").write_text('{"value": 8}', encoding="utf-8")

            evidence = build_evidence(
                initial,
                final,
                [{"tool": "read_value", "result": {"success": True}, "error": None}],
                "The value is 8.",
                4096,
            )

            self.assertEqual(evidence["answer"], "The value is 8.")
            self.assertEqual(evidence["changed_paths"], [{"path": "value.json", "change": "modified"}])
            self.assertEqual(evidence["final_files"][0]["json"], {"value": 8})
            self.assertNotIn("mtime", json.dumps(evidence))

    def test_build_evidence_keeps_small_json_for_later_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial = root / "initial"
            final = root / "final"
            initial.mkdir()
            final.mkdir()
            for directory in (initial, final):
                (directory / "a-large.txt").write_text("x" * 100, encoding="utf-8")
                (directory / "z-state.json").write_text('{"state": "kept"}', encoding="utf-8")

            evidence = build_evidence(initial, final, [], "", 32)

            state = next(item for item in evidence["final_files"] if item["path"] == "z-state.json")
            self.assertEqual(state["json"], {"state": "kept"})

    def test_build_evidence_prioritizes_changed_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial = root / "initial"
            final = root / "final"
            initial.mkdir()
            final.mkdir()
            (initial / "a-state.json").write_text('{"padding":"xxxxxxxxxxxxxxxx"}', encoding="utf-8")
            (final / "a-state.json").write_text('{"padding":"xxxxxxxxxxxxxxxx"}', encoding="utf-8")
            (initial / "z-result.md").write_text("old", encoding="utf-8")
            (final / "z-result.md").write_text("final result", encoding="utf-8")

            evidence = build_evidence(initial, final, [], "", 16)

            result = next(item for item in evidence["final_files"] if item["path"] == "z-result.md")
            self.assertEqual(result["text"], "final result")
            self.assertFalse(result["truncated"])

    def test_verifier_runtime_executes_deterministic_requirement_in_sandbox(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "The answer reports 8.", "required": True,
                "evidence_channels": ["answer"],
                "pass_condition": "answer equals The value is 8.",
                "fail_condition": "answer is different.",
            }],
            "source": "def verify(ctx):\n"
            "    if ctx.answer() == 'The value is 8.':\n"
            "        ctx.pass_requirement('R1', 'answer matches', ['answer'])\n"
            "    else:\n"
            "        ctx.fail_requirement('R1', 'answer differs', ['answer'])\n",
        }
        validate_verifier(package)

        results = run_verifier(package, {"answer": "The value is 8.", "calls": [], "changed_paths": []})

        self.assertEqual(results[0]["status"], "pass")
        self.assertEqual(results[0]["requirement_id"], "R1")

    def test_final_read_reuses_unchanged_initial_content(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "content", "required": True,
                               "evidence_channels": ["workspace"], "pass_condition": "x", "fail_condition": "missing"}],
            "source": "def verify(ctx):\n"
            "    if ctx.read_text('source.txt', 'final') == 'content':\n"
            "        ctx.pass_requirement('R1', 'found', ['final:source.txt'])\n"
            "    else:\n"
            "        ctx.fail_requirement('R1', 'missing', ['final:source.txt'])\n",
        }
        evidence = {
            "answer": "", "calls": [], "changed_paths": [],
            "initial_files": [{"path": "source.txt", "sha256": "same", "text": "content"}],
            "final_files": [{"path": "source.txt", "sha256": "same"}],
        }

        self.assertEqual(run_verifier(package, evidence)[0]["status"], "pass")

    def test_semantic_prompt_contains_only_referenced_evidence(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "answer is clear", "required": True,
                               "evidence_channels": ["answer"], "pass_condition": "clear", "fail_condition": "unclear"}],
            "source": "def verify(ctx):\n    ctx.semantic_requirement('R1', 'answer is clear', ['answer'])\n",
        }
        prompts: list[str] = []

        def semantic_infer(prompt: str, **_: object) -> InferenceResult:
            prompts.append(prompt)
            return InferenceResult('{"status":"pass","reason":"clear"}', {}, "test")

        run_verifier(package, {
            "answer": "clear", "calls": [{"tool": "unused"}],
            "changed_paths": [], "initial_files": [], "final_files": [],
        }, semantic_infer_fn=semantic_infer)
        evidence = json.loads(prompts[0])["evidence"]
        role = json.loads(prompts[0])["role"]
        self.assertEqual(evidence["answer"], "clear")
        self.assertEqual(evidence["calls"], [])
        self.assertEqual(json.loads(prompts[0])["requirement"]["pass_condition"], "clear")
        self.assertIn("未明确要求落盘", role)
        self.assertIn("最终回答不能替代", role)
        self.assertIn("不得擅自要求该结果必须为 true", role)
        self.assertIn("不得要求引用中包含原文全文", role)

    def test_semantic_evidence_deduplicates_refs_and_reuses_initial_content(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "source is valid", "required": True,
                               "evidence_channels": ["workspace"], "pass_condition": "valid", "fail_condition": "invalid"}],
            "source": "def verify(ctx):\n"
            "    ctx.semantic_requirement('R1', 'source is valid', ['final:source.txt', 'final:source.txt'])\n",
        }
        prompts: list[str] = []

        def semantic_infer(prompt: str, **_: object) -> InferenceResult:
            prompts.append(prompt)
            return InferenceResult('{"status":"pass","reason":"valid"}', {}, "test")

        results = run_verifier(package, {
            "answer": "", "calls": [], "changed_paths": [],
            "initial_files": [{"path": "source.txt", "sha256": "same", "text": "content"}],
            "final_files": [{"path": "source.txt", "sha256": "same"}],
        }, semantic_infer_fn=semantic_infer)

        request = json.loads(prompts[0])
        self.assertEqual(results[0]["evidence_refs"], ["final:source.txt"])
        self.assertEqual(len(request["evidence"]["final_files"]), 1)
        self.assertEqual(request["evidence"]["final_files"][0]["text"], "content")

    def test_multiple_semantic_requirements_are_inferred_as_one_batch(self) -> None:
        requirements = [{
            "id": rid, "claim": rid, "required": True,
            "evidence_channels": ["answer"], "pass_condition": "yes", "fail_condition": "no",
        } for rid in ("R1", "R2")]
        package = {
            "schema_version": "1", "requirements": requirements,
            "source": "def verify(ctx):\n"
            "    ctx.semantic_requirement('R1', 'one', ['answer'])\n"
            "    ctx.semantic_requirement('R2', 'two', ['answer'])\n",
        }
        calls: list[object] = []

        def batch_infer(prompts: object, **_: object) -> list[InferenceResult]:
            calls.append(prompts)
            return [InferenceResult('{"status":"pass","reason":"ok"}', {}, "test") for _ in prompts]

        results = run_verifier(
            package,
            {"answer": "yes", "calls": [], "changed_paths": [], "initial_files": [], "final_files": []},
            semantic_infer_fn=batch_infer,
        )

        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0], list)
        self.assertEqual([item["status"] for item in results], ["pass", "pass"])

    def test_semantic_result_accepts_unambiguous_status_reason_text(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "answer is clear", "required": True,
                               "evidence_channels": ["answer"], "pass_condition": "clear", "fail_condition": "unclear"}],
            "source": "def verify(ctx):\n    ctx.semantic_requirement('R1', 'answer is clear', ['answer'])\n",
        }

        results = run_verifier(
            package,
            {"answer": "clear", "calls": [], "changed_paths": [], "initial_files": [], "final_files": []},
            semantic_infer_fn=lambda *_args, **_kwargs: InferenceResult(
                "status: pass reason: the answer is clear", {}, "test",
            ),
        )

        self.assertEqual(results[0]["status"], "pass")
        self.assertEqual(results[0]["reason"], "the answer is clear")

        colon_results = run_verifier(
            package,
            {"answer": "clear", "calls": [], "changed_paths": [], "initial_files": [], "final_files": []},
            semantic_infer_fn=lambda *_args, **_kwargs: InferenceResult("pass：clear answer", {}, "test"),
        )
        self.assertEqual(colon_results[0]["status"], "pass")

        reason_results = run_verifier(
            package,
            {"answer": "clear", "calls": [], "changed_paths": [], "initial_files": [], "final_files": []},
            semantic_infer_fn=lambda *_args, **_kwargs: InferenceResult("fail 理由：answer is unsupported", {}, "test"),
        )
        self.assertEqual(reason_results[0]["status"], "fail")

        chinese_results = run_verifier(
            package,
            {"answer": "clear", "calls": [], "changed_paths": [], "initial_files": [], "final_files": []},
            semantic_infer_fn=lambda *_args, **_kwargs: InferenceResult("状态：fail 理由：unsupported", {}, "test"),
        )
        self.assertEqual(chinese_results[0]["status"], "fail")

        translated_results = run_verifier(
            package,
            {"answer": "clear", "calls": [], "changed_paths": [], "initial_files": [], "final_files": []},
            semantic_infer_fn=lambda *_args, **_kwargs: InferenceResult("通过：answer is supported", {}, "test"),
        )
        self.assertEqual(translated_results[0]["status"], "pass")

    def test_verifier_runtime_does_not_use_process_working_directory_as_workspace(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "x", "required": True,
                               "evidence_channels": [], "pass_condition": "x", "fail_condition": "y"}],
            "source": "def verify(ctx):\n    ctx.pass_requirement('R1', 'ok', [])\n",
        }
        with patch("task_gen.task_eval_verifier._call_tool", return_value={
            "kind": None, "result": {"results": [{
                "requirement_id": "R1", "status": "pass", "reason": "ok", "evidence_refs": [],
            }]}, "error": None,
        }) as call_tool:
            run_verifier(package, {"answer": "", "calls": [], "changed_paths": []})

        workspace = call_tool.call_args.args[2]
        self.assertNotEqual(workspace, Path.cwd())
        self.assertTrue(workspace.name.startswith("task-verifier-"))

    def test_verifier_rejects_imports_and_missing_requirement_results(self) -> None:
        unsafe = {
            "schema_version": "1", "requirements": [],
            "source": "import os\ndef verify(ctx):\n    ctx.pass_requirement('R1', os.getcwd())",
        }
        with self.assertRaises(ValueError):
            validate_verifier(unsafe)

        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "x", "required": True,
                               "evidence_channels": [], "pass_condition": "x", "fail_condition": "y"}],
            "source": "def verify(ctx):\n    pass",
        }
        with self.assertRaises(ValueError):
            run_verifier(package, {"answer": "", "calls": [], "changed_paths": []})

    def test_verifier_rejects_context_internals_and_reflection(self) -> None:
        for source in (
            "def verify(ctx):\n    ctx.evidence['answer'] = 'fake'\n    ctx.pass_requirement('R1', 'x', [])\n",
            "def verify(ctx):\n    getattr(ctx, 'results').clear()\n    ctx.pass_requirement('R1', 'x', [])\n",
        ):
            package = {
                "schema_version": "1",
                "requirements": [{"id": "R1", "claim": "x", "required": True,
                                   "evidence_channels": [], "pass_condition": "x", "fail_condition": "y"}],
                "source": source,
            }
            with self.assertRaises(ValueError):
                validate_verifier(package)

    def test_verifier_allows_local_lambda_expressions(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "x", "required": True,
                               "evidence_channels": [], "pass_condition": "x", "fail_condition": "y"}],
            "source": "def verify(ctx):\n"
            "    value = next(filter(lambda item: item == 1, [1]), None)\n"
            "    ctx.pass_requirement('R1', str(value), [])\n",
        }

        validate_verifier(package)

    def test_verifier_allows_local_try_blocks(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "x", "required": True,
                               "evidence_channels": [], "pass_condition": "x", "fail_condition": "y"}],
            "source": "def verify(ctx):\n"
            "    try:\n        value = 1\n"
            "    except ValueError:\n        value = 0\n"
            "    ctx.pass_requirement('R1', str(value), [])\n",
        }

        validate_verifier(package)

    def test_verifier_allows_local_generator_helpers(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "x", "required": True,
                               "evidence_channels": [], "pass_condition": "x", "fail_condition": "y"}],
            "source": "def verify(ctx):\n"
            "    def values():\n        yield 1\n"
            "    ctx.pass_requirement('R1', str(list(values())), [])\n",
        }

        validate_verifier(package)

    def test_aggregate_results_preserves_indeterminate_and_required_semantics(self) -> None:
        requirements = [
            {"id": "R1", "required": True},
            {"id": "R2", "required": True},
            {"id": "R3", "required": False},
        ]
        result = aggregate_results(requirements, [
            {"requirement_id": "R1", "status": "pass"},
            {"requirement_id": "R2", "status": "indeterminate"},
            {"requirement_id": "R3", "status": "fail"},
        ])
        self.assertEqual(result["outcome"], "indeterminate")
        self.assertFalse(result["passed"])

    def test_generate_and_calibrate_verifier_uses_reference_then_rejects_empty(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "The answer reports 8.", "required": True,
                "evidence_channels": ["answer"],
                "pass_condition": "answer equals The value is 8.",
                "fail_condition": "answer is different.",
            }],
            "source": "def verify(ctx):\n"
            "    if ctx.answer() == 'The value is 8.':\n"
            "        ctx.pass_requirement('R1', 'answer matches', ['answer'])\n"
            "    else:\n"
            "        ctx.fail_requirement('R1', 'answer differs', ['answer'])\n",
        }

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            self.assertIn("Return the current value.", prompt)
            self.assertNotIn("internal", prompt)
            request = json.loads(prompt)
            self.assertEqual(set(request["response_contract"]["requirements"][0]), {
                "id", "claim", "required", "evidence_channels", "pass_condition", "fail_condition",
            })
            self.assertTrue(any(key.startswith("read_json") for key in request["verifier_context_api"]))
            self.assertTrue(any("硬编码" in rule and "参考执行" in rule for rule in request["rules"]))
            self.assertTrue(any("工具调用" in rule and "成功条件" in rule for rule in request["rules"]))
            self.assertTrue(any("最终业务状态" in rule and "新建" in rule for rule in request["rules"]))
            self.assertTrue(any("标点" in rule and "下划线" in rule for rule in request["rules"]))
            self.assertTrue(any("每项 requirement" in rule and "恰好一次" in rule for rule in request["rules"]))
            self.assertTrue(any("没有返回值" in rule and "最终回答" in rule for rule in request["rules"]))
            self.assertTrue(any("字段" in rule and "业务对象" in rule for rule in request["rules"]))
            self.assertTrue(any("多个业务标识" in rule and "分隔" in rule for rule in request["rules"]))
            self.assertTrue(any("检查结果" in rule and "true" in rule for rule in request["rules"]))
            self.assertTrue(any("可回溯" in rule and "原文全文" in rule for rule in request["rules"]))
            self.assertTrue(any("initial" in rule and "final" in rule for rule in request["rules"]))
            self.assertTrue(any("分句" in rule and "传播" in rule for rule in request["rules"]))
            self.assertTrue(any("自然语言内容" in rule and "固定短语" in rule for rule in request["rules"]))
            self.assertTrue(any("整个任务不得通过" in rule and "保持不变" in rule for rule in request["rules"]))
            return InferenceResult(json.dumps(package), {}, "test-model")

        generated = generate_verifier(
            {"task_text": "Return the current value."},
            {"tools": [{"name": "read_value"}]},
            {"answer": "The value is 8.", "calls": [], "changed_paths": []},
            {},
            infer_fn=fake_infer,
        )
        calibration = calibrate_verifier(
            generated,
            {"answer": "The value is 8.", "calls": [], "changed_paths": []},
            {"answer": "", "calls": [], "changed_paths": []},
        )
        self.assertEqual(calibration["status"], "calibrated")
        self.assertEqual(calibration["reference"]["outcome"], "pass")
        self.assertEqual(calibration["empty"]["outcome"], "fail")

    def test_calibration_rejects_any_required_criterion_that_passes_empty_evidence(self) -> None:
        requirements = [{
            "id": requirement_id, "claim": requirement_id, "required": True,
            "evidence_channels": ["answer"], "pass_condition": "present", "fail_condition": "absent",
        } for requirement_id in ("R1", "R2")]
        package = {
            "schema_version": "1",
            "requirements": requirements,
            "source": "def verify(ctx):\n"
            "    if ctx.answer():\n"
            "        ctx.pass_requirement('R1', 'present', ['answer'])\n"
            "    else:\n"
            "        ctx.fail_requirement('R1', 'absent', ['answer'])\n"
            "    ctx.pass_requirement('R2', 'always passes', [])\n",
        }

        with self.assertRaisesRegex(ValueError, "空证据.*R2"):
            calibrate_verifier(
                package,
                {"answer": "ok", "calls": [], "changed_paths": []},
                {"answer": "", "calls": [], "changed_paths": []},
            )

    def test_counterfactual_evidence_changes_technical_ids_and_reference_format(self) -> None:
        evidence = {
            "answer": "done", "calls": [{
                "tool": "post", "arguments": {
                    "account_id": "account_1", "entry_id": "je_1", "source_reference": "TX-1",
                    "description": "Accrual entry",
                },
            }], "changed_paths": [],
            "initial_files": [{"path": "ledger.json", "json": {"account_id": "account_1"}}],
            "final_files": [{
                "path": "ledger.json", "sha256": "x", "text": "old",
                "json": {
                    "account_id": "account_1", "entry_id": "je_1", "source_reference": "TX-1",
                    "description": "Accrual entry",
                },
            }],
        }

        changed = _counterfactual_evidence(evidence, "Post a balanced entry.")

        self.assertNotEqual(changed["calls"][0]["arguments"]["entry_id"], "je_1")
        self.assertEqual(changed["calls"][0]["arguments"]["account_id"], "account_1")
        self.assertEqual(changed["calls"][0]["arguments"]["source_reference"], "counterfactual_reference")
        self.assertIn("TX-1", changed["calls"][0]["arguments"]["description"])
        self.assertEqual(
            changed["calls"][0]["arguments"]["entry_id"],
            changed["final_files"][0]["json"]["entry_id"],
        )
        self.assertIn("counterfactual_reference", changed["final_files"][0]["text"])
        self.assertIn("TX-1", changed["final_files"][0]["json"]["description"])

    def test_calibration_rejects_verifier_bound_to_reference_metadata_format(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "balanced entry exists", "required": True,
                "evidence_channels": ["workspace"],
                "pass_condition": "entry exists", "fail_condition": "entry missing",
            }],
            "source": "def verify(ctx):\n"
            "    ledger = ctx.read_json('ledger.json') or {}\n"
            "    if ledger.get('source_reference') == 'TX-1':\n"
            "        ctx.pass_requirement('R1', 'found', ['final:ledger.json'])\n"
            "    else:\n"
            "        ctx.fail_requirement('R1', 'missing', ['final:ledger.json'])\n",
        }
        reference = {
            "answer": "done", "calls": [], "changed_paths": [], "initial_files": [],
            "final_files": [{
                "path": "ledger.json", "sha256": "x",
                "text": '{"source_reference":"TX-1","description":"entry"}',
                "json": {"source_reference": "TX-1", "description": "entry"},
            }],
        }
        empty = {
            "answer": "", "calls": [], "changed_paths": [], "initial_files": [],
            "final_files": [{"path": "ledger.json", "sha256": "x", "text": "{}", "json": {}}],
        }

        with self.assertRaisesRegex(ValueError, "反事实"):
            calibrate_verifier(package, reference, empty, task_text="Post a balanced entry.")

    def test_calibration_requires_semantic_reference_confirmation(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "brief has content", "required": True,
                "evidence_channels": ["answer"],
                "pass_condition": "content present", "fail_condition": "content absent",
            }],
            "source": "def verify(ctx):\n"
            "    if ctx.answer():\n"
            "        ctx.semantic_requirement('R1', 'brief has content', ['answer'])\n"
            "    else:\n"
            "        ctx.fail_requirement('R1', 'empty', ['answer'])\n",
        }
        responses = iter([
            InferenceResult('{"status":"pass","reason":"yes"}', {}, "test"),
            InferenceResult('{"status":"fail","reason":"no"}', {}, "test"),
        ])

        with self.assertRaisesRegex(ValueError, "二次确认"):
            calibrate_verifier(
                package,
                {"answer": "brief", "calls": [], "changed_paths": []},
                {"answer": "", "calls": [], "changed_paths": []},
                infer_fn=lambda *_args, **_kwargs: next(responses),
            )

    def test_calibration_can_confirm_deterministic_results_semantically(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "brief states no actions", "required": True,
                "evidence_channels": ["answer"],
                "pass_condition": "explicitly states none", "fail_condition": "empty section",
            }],
            "source": "def verify(ctx):\n"
            "    ctx.pass_requirement('R1', 'section exists', ['answer'])\n",
        }

        requests: list[dict[str, object]] = []

        def reject_empty_section(prompt: str, **_: object) -> InferenceResult:
            requests.append(json.loads(prompt))
            return InferenceResult('{"status":"fail","reason":"section is empty"}', {}, "test")

        with self.assertRaisesRegex(ValueError, "二次确认"):
            calibrate_verifier(
                package,
                {"answer": "## Actions", "calls": [], "changed_paths": []},
                {"answer": "", "calls": [], "changed_paths": []},
                infer_fn=reject_empty_section,
                task_text="The brief must contain organized action items.",
                confirm_all=True,
            )
        self.assertEqual(requests[0]["task"], "The brief must contain organized action items.")
        self.assertIn("原任务是唯一权威", requests[0]["role"])
        self.assertIn("原子子项", requests[0]["role"])
        self.assertIn("忽略这些加码", requests[0]["role"])

    def test_calibration_allows_empty_evidence_to_pass_only_preservation_subrequirement(self) -> None:
        requirements = [{
            "id": "R1", "claim": "record changed", "required": True,
            "evidence_channels": ["answer"], "pass_condition": "changed", "fail_condition": "unchanged",
        }, {
            "id": "R2", "claim": "source preserved", "required": True,
            "evidence_channels": ["workspace"], "pass_condition": "same", "fail_condition": "changed",
        }]
        package = {
            "schema_version": "1", "requirements": requirements,
            "source": "def verify(ctx):\n"
            "    if ctx.answer():\n"
            "        ctx.pass_requirement('R1', 'changed', ['answer'])\n"
            "    else:\n"
            "        ctx.fail_requirement('R1', 'unchanged', ['answer'])\n"
            "    ctx.pass_requirement('R2', 'preserved', ['initial:source.txt', 'final:source.txt'])\n",
        }
        file_evidence = [{"path": "source.txt", "sha256": "same"}]

        calibration = calibrate_verifier(
            package,
            {"answer": "done", "calls": [], "changed_paths": [],
             "initial_files": file_evidence, "final_files": file_evidence},
            {"answer": "", "calls": [], "changed_paths": [],
             "initial_files": file_evidence, "final_files": file_evidence},
        )

        self.assertEqual(calibration["empty"]["outcome"], "fail")
        self.assertEqual(calibration["empty"]["requirements"][1]["status"], "pass")

    def test_generate_verifier_prompt_omits_unchanged_file_content(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "result exists", "required": True,
                "evidence_channels": ["workspace"], "pass_condition": "exists", "fail_condition": "missing",
            }],
            "source": "def verify(ctx):\n    ctx.indeterminate_requirement('R1', 'not implemented')\n",
        }

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            evidence = json.loads(prompt)["reference_evidence"]
            unchanged = next(item for item in evidence["initial_files"] if item["path"] == "input.txt")
            changed = next(item for item in evidence["final_files"] if item["path"] == "result.txt")
            self.assertNotIn("text", unchanged)
            self.assertEqual(changed["text"], "result")
            return InferenceResult(json.dumps(package), {}, "test")

        generate_verifier(
            {"task_text": "create result"}, {}, {
                "answer": "done", "calls": [],
                "changed_paths": [{"path": "result.txt", "change": "added"}],
                "initial_files": [{"path": "input.txt", "text": "large input", "size": 11, "sha256": "x"}],
                "final_files": [
                    {"path": "input.txt", "text": "large input", "size": 11, "sha256": "x"},
                    {"path": "result.txt", "text": "result", "size": 6, "sha256": "y"},
                ],
            }, {}, infer_fn=fake_infer,
        )

    def test_generate_verifier_supplies_fixed_schema_version_when_model_omits_it(self) -> None:
        package = {
            "requirements": [{
                "id": "R1", "claim": "x", "required": True, "evidence_channels": ["answer"],
                "pass_condition": "x", "fail_condition": "not x",
            }],
            "source": "def verify(ctx):\n    ctx.indeterminate_requirement('R1', 'missing')\n",
        }
        generated = generate_verifier(
            {"task_text": "x"}, {}, {}, {},
            infer_fn=lambda *_args, **_kwargs: InferenceResult(json.dumps(package), {}, "test"),
        )
        self.assertEqual(generated["schema_version"], "1")

    def test_generate_verifier_normalizes_unambiguous_evidence_channel_aliases(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "x", "required": True,
                "evidence_channels": ["files", "tool_calls", "final_answer"],
                "pass_condition": "x", "fail_condition": "not x",
            }],
            "source": "def verify(ctx):\n    ctx.indeterminate_requirement('R1', 'missing')\n",
        }

        generated = generate_verifier(
            {"task_text": "x"}, {}, {}, {},
            infer_fn=lambda *_args, **_kwargs: InferenceResult(json.dumps(package), {}, "test"),
        )

        self.assertEqual(generated["requirements"][0]["evidence_channels"], [
            "workspace", "tool_trace", "answer",
        ])

    def test_generate_verifier_unwraps_response_contract(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "x", "required": True, "evidence_channels": ["answer"],
                "pass_condition": "x", "fail_condition": "not x",
            }],
            "source": "def verify(ctx):\n    ctx.indeterminate_requirement('R1', 'missing')\n",
        }
        generated = generate_verifier(
            {"task_text": "x"}, {}, {}, {},
            infer_fn=lambda *_args, **_kwargs: InferenceResult(
                json.dumps({"response_contract": package}), {}, "test",
            ),
        )

        self.assertEqual(generated, package)

    def test_prepare_verifier_retries_until_reference_calibrates(self) -> None:
        requirement = {
            "id": "R1", "claim": "answer is 8", "required": True,
            "evidence_channels": ["answer"], "pass_condition": "is 8", "fail_condition": "not 8",
        }
        bad = {
            "schema_version": "1", "requirements": [requirement],
            "source": "def verify(ctx):\n    ctx.fail_requirement('R1', 'wrong', ['answer'])\n",
        }
        good = {
            "schema_version": "1", "requirements": [requirement],
            "source": "def verify(ctx):\n"
            "    if ctx.answer():\n"
            "        ctx.pass_requirement('R1', 'ok', ['answer'])\n"
            "    else:\n"
            "        ctx.fail_requirement('R1', 'empty', ['answer'])\n",
        }
        responses = iter([bad, good])
        prompts: list[str] = []

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            if "verifier_context_api" not in request:
                return InferenceResult('{"status":"pass","reason":"reference satisfies requirement"}', {}, "test")
            prompts.append(prompt)
            return InferenceResult(json.dumps(next(responses)), {}, "test")

        package, calibration, attempts = prepare_verifier(
            {"task_text": "return 8"}, {"tools": [{"name": "read_value"}]},
            {"answer": "8", "calls": [], "changed_paths": []},
            {"answer": "", "calls": [], "changed_paths": []},
            {}, attempts=2,
            infer_fn=fake_infer,
        )

        self.assertEqual(package, good)
        self.assertEqual(calibration["status"], "calibrated")
        self.assertEqual(len(attempts), 2)
        self.assertIn("参考执行", attempts[0]["error"])
        self.assertIsNone(attempts[1]["error"])
        self.assertIn("previous_failure", json.loads(prompts[1]))
        self.assertNotIn("previous_verifier", json.loads(prompts[1]))

    def test_agent_prompt_states_the_tool_call_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = load_cases(self._write_case_at(root / "source"))[0]

            prompt = json.loads(_agent_prompt(case, 7))

            self.assertIn("7", prompt["role"])
            self.assertIn("tool calls", prompt["role"])

    def test_task_evaluation_uses_local_codex_and_defaults_to_fifty_calls(self) -> None:
        self.assertEqual(_TaskEvalCodexClient.__mro__[1].__module__, "task_gen.tool_graph.codex")
        self.assertEqual(evaluate_case.__kwdefaults__["max_tool_calls"], 50)
        self.assertEqual(run_evaluation.__kwdefaults__["max_tool_calls"], 50)

    def test_run_agent_propagates_llm_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            captured: dict[str, object] = {}

            class FakeClient:
                def __init__(self, *_args: object, **kwargs: object):
                    captured.update(kwargs)

                def run(self, _prompt: str, *, working_directory: Path) -> str:
                    return str(working_directory)

            with patch("task_gen.task_eval._TaskEvalCodexClient", FakeClient):
                _run_agent("task", root, root / "server.json", root / "trace", {"timeout_seconds": 321})

            self.assertEqual(captured["timeout_seconds"], 321)

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

    def test_eval_codex_client_auto_approves_isolated_workspace_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments_path = root / "arguments.txt"
            executable = root / "fake-codex"
            executable.write_text(
                "#!/usr/bin/env bash\nset -eu\n"
                f'printf "%s\\n" "$@" > {arguments_path}\n'
                'output=""\nwhile [ "$#" -gt 0 ]; do\n'
                '  if [ "$1" = "--output-last-message" ]; then shift; output="$1"; fi\n'
                '  shift\ndone\ncat >/dev/null\necho done > "$output"\n',
                encoding="utf-8",
            )
            executable.chmod(0o755)
            client = _TaskEvalCodexClient(
                root / "task_eval_mcp.py",
                root / "server.json",
                executable=str(executable),
                sandbox="workspace-write",
            )

            client.run("work", working_directory=root)

            arguments = arguments_path.read_text(encoding="utf-8").splitlines()
            self.assertIn("--approve-for-me", arguments)
            self.assertNotIn("--sandbox", arguments)
            self.assertIn("mcp_servers={}", arguments)

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

    def test_evaluate_case_runs_one_agent_in_workspace_with_mcp_tools_then_judges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial = root / "initial"
            initial.mkdir()
            (initial / "value.txt").write_text("7", encoding="utf-8")
            case = load_cases(self._write_case(root, initial))[0]
            agent_calls: list[tuple[str, Path]] = []
            judge_prompts: list[str] = []

            def fake_agent(prompt: str, working_directory: Path, server_config: Path, trace: Path) -> str:
                agent_calls.append((prompt, working_directory))
                self.assertTrue(server_config.is_file())
                self.assertEqual(json.loads(server_config.read_text())["workspace"], str(root / "evaluation"))
                trace.parent.mkdir(parents=True, exist_ok=True)
                trace.write_text(json.dumps({
                    "tool": "read_value",
                    "arguments": {},
                    "result": {"success": True, "data": {"value": 7}},
                    "error": None,
                }) + "\n", encoding="utf-8")
                return "The value is 7."

            def fake_judge(prompt: str, **_: object) -> InferenceResult:
                judge_prompts.append(prompt)
                return InferenceResult(
                    '{"passed":true,"score":100,"analysis":"Matches the reference and tool result."}',
                    {},
                    "test-model",
                )

            previous = Path.cwd()
            try:
                os.chdir(root)
                result = evaluate_case(
                    case,
                    Path("evaluation"),
                    {},
                    agent_run_fn=fake_agent,
                    judge_infer_fn=fake_judge,
                )
            finally:
                os.chdir(previous)

            self.assertEqual(result["agent_answer"], "The value is 7.")
            self.assertEqual(result["tool_calls"][0]["tool"], "read_value")
            self.assertTrue(result["evaluation"]["passed"])
            self.assertEqual(len(agent_calls), 1)
            self.assertEqual(agent_calls[0][1], root / "evaluation")
            self.assertIn("environment MCP tools", agent_calls[0][0])
            self.assertIn("429 or 503", agent_calls[0][0])
            self.assertIn("do not ask the user", agent_calls[0][0])
            judge = json.loads(judge_prompts[0])
            self.assertEqual(judge["workspace_changes"], [])
            self.assertEqual(judge["environment_resources"], [])
            self.assertEqual((root / "evaluation/value.txt").read_text(encoding="utf-8"), "7")

    def test_evaluate_case_attributes_actual_verifier_crash_to_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = self._write_case_at(root / "source")
            task_path = root / "source/tasks.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))[0]
            reference = root / "source/tasks/task1/reference"
            reference.mkdir()
            (reference / "value.txt").write_text("7", encoding="utf-8")
            task["reference"]["final_state"] = "tasks/task1/reference"
            task_path.write_text(json.dumps([task]), encoding="utf-8")
            case = load_cases(input_root)[0]
            package = {
                "schema_version": "1",
                "requirements": [{
                    "id": "R1", "claim": "value read", "required": True,
                    "evidence_channels": ["tool_trace"],
                    "pass_condition": "read call exists", "fail_condition": "read call missing",
                }],
                "source": "def verify(ctx):\n"
                "    ctx.pass_requirement('R1', 'read', ['tool_call:0'])\n",
            }

            with patch("task_gen.task_eval.prepare_verifier", return_value=(package, {}, [])):
                result = evaluate_case(
                    case,
                    root / "evaluation", {},
                    agent_run_fn=lambda *_args: "The value is 7.",
                )

            self.assertEqual(result["outcome"], "indeterminate")
            self.assertEqual(result["attribution"], "verifier")
            self.assertIn("无效 evidence_refs", result["error"])
            self.assertEqual(result["agent_answer"], "The value is 7.")

    def test_evaluate_case_semantically_reviews_only_failed_verifier_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = self._write_case_at(root / "source")
            task_path = root / "source/tasks.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))[0]
            reference = root / "source/tasks/task1/reference"
            reference.mkdir()
            (reference / "value.txt").write_text("7", encoding="utf-8")
            task["reference"]["final_state"] = "tasks/task1/reference"
            task_path.write_text(json.dumps([task]), encoding="utf-8")
            case = load_cases(input_root)[0]
            package = {
                "schema_version": "1",
                "requirements": [{
                    "id": "R1", "claim": "value returned", "required": True,
                    "evidence_channels": ["answer"],
                    "pass_condition": "answer contains value", "fail_condition": "answer omits value",
                }],
                "source": "def verify(ctx):\n"
                "    ctx.fail_requirement('R1', 'overfit check failed', ['answer'])\n",
            }
            prompts: list[dict[str, object]] = []

            def semantic_judge(prompt: str, **_: object) -> InferenceResult:
                prompts.append(json.loads(prompt))
                return InferenceResult('{"status":"pass","reason":"answer contains 7"}', {}, "test")

            with patch("task_gen.task_eval.prepare_verifier", return_value=(package, {}, [])):
                result = evaluate_case(
                    case, root / "evaluation", {},
                    agent_run_fn=lambda *_args: "The value is 7.", judge_infer_fn=semantic_judge,
                )

            self.assertEqual(result["outcome"], "pass")
            self.assertEqual(prompts[0]["task"], "Return the current value.")
            self.assertEqual(result["evaluation"]["requirements"][0]["reason"], "answer contains 7")

    def test_evaluate_case_retries_transient_503_before_any_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial = root / "initial"
            initial.mkdir()
            (initial / "value.txt").write_text("7", encoding="utf-8")
            case = load_cases(self._write_case(root, initial))[0]
            attempts = 0

            def flaky_agent(_prompt: str, _workspace: Path, _config: Path, trace: Path) -> str:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return "Approval service temporarily unavailable (503)."
                trace.write_text(json.dumps({
                    "tool": "read_value", "arguments": {},
                    "result": {"success": True, "data": {"value": 7}}, "error": None,
                }) + "\n", encoding="utf-8")
                return "The value is 7."

            result = evaluate_case(
                case, root / "evaluation", {}, agent_run_fn=flaky_agent,
                judge_infer_fn=lambda *_args, **_kwargs: InferenceResult(
                    '{"passed":true,"score":100,"analysis":"ok"}', {}, "test",
                ),
            )

            self.assertEqual(attempts, 2)
            self.assertEqual(result["agent_attempts"], 2)
            self.assertEqual(len(result["tool_calls"]), 1)

    def test_evaluate_case_raises_infrastructure_error_after_repeated_503(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial = root / "initial"
            initial.mkdir()
            (initial / "value.txt").write_text("7", encoding="utf-8")
            case = load_cases(self._write_case(root, initial))[0]

            with self.assertRaisesRegex(RuntimeError, "基础设施连续 2 次"):
                evaluate_case(
                    case, root / "evaluation", {}, agent_attempts=2,
                    agent_run_fn=lambda *_args: "环境服务暂时不可用（503）。",
                )

    def test_evaluate_case_reuses_calibrated_verifier_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = self._write_case_at(root / "source")
            task_path = root / "source/tasks.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))[0]
            reference = root / "source/tasks/task1/reference"
            reference.mkdir()
            (reference / "value.txt").write_text("7", encoding="utf-8")
            task["reference"]["final_state"] = "tasks/task1/reference"
            task_path.write_text(json.dumps([task]), encoding="utf-8")
            case = load_cases(input_root)[0]
            package = {
                "schema_version": "1",
                "requirements": [{
                    "id": "R1", "claim": "value returned", "required": True,
                    "evidence_channels": ["answer"],
                    "pass_condition": "answer exists", "fail_condition": "answer missing",
                }],
                "source": "def verify(ctx):\n"
                "    if ctx.answer():\n"
                "        ctx.pass_requirement('R1', 'present', ['answer'])\n"
                "    else:\n"
                "        ctx.fail_requirement('R1', 'missing', ['answer'])\n",
            }
            cache = root / "cache"

            with patch("task_gen.task_eval.prepare_verifier", return_value=(package, {"status": "calibrated"}, [])) as prepare:
                first = evaluate_case(
                    case, root / "evaluation-1", {}, verifier_cache=cache,
                    agent_run_fn=lambda *_args: "The value is 7.",
                )
            with patch("task_gen.task_eval.prepare_verifier", side_effect=AssertionError("cache miss")):
                second = evaluate_case(
                    case, root / "evaluation-2", {}, verifier_cache=cache,
                    agent_run_fn=lambda *_args: "The value is 7.",
                )

            prepare.assert_called_once()
            self.assertFalse(first["verifier_cache_hit"])
            self.assertTrue(second["verifier_cache_hit"])
            self.assertEqual(len(list(cache.glob("*.json"))), 1)

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
                response["result"]["structuredContent"]["tool_result"]["error"]["code"],
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
