from __future__ import annotations

import ast
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.task_eval import (
    DEFAULT_INPUT_ROOT,
    VERIFIER_CACHE_VERSION,
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
    _assemble_verifier,
    _assemble_witness_verifier,
    _component_function,
    _counterfactual_evidence,
    _infer_component_batch,
    _witness_groups,
    _select_witness_group,
    assess_task_reference_conflict,
    aggregate_results,
    build_evidence,
    calibrate_verifier,
    generate_evidence_plan,
    generate_planned_verifier,
    generate_proof_plan,
    generate_verification_spec,
    generate_verifier,
    prepare_verifier,
    review_and_revise_verifier,
    review_proof_plan,
    review_verifier_implementation,
    review_verification_spec,
    run_verifier,
    ReferenceCalibrationError,
    TaskReferenceConflictError,
    VerifierPreparationError,
    validate_proof_plan,
    validate_verification_spec,
    validate_verifier,
)
from task_gen.task_eval_mcp import call_environment_tool, serve
from task_gen.tool_graph.llm import BatchInferenceError, InferenceResult


ROOT = Path(__file__).resolve().parents[1]


class TaskEvalTest(unittest.TestCase):
    def test_staged_verifier_pipeline_invalidates_legacy_cache(self) -> None:
        self.assertGreaterEqual(VERIFIER_CACHE_VERSION, 15)

    def test_generate_verification_spec_has_no_reference_evidence(self) -> None:
        previous_issues = [{
            "code": "non_atomic", "task_clause_ids": ["C1"],
            "requirement_ids": ["R1"], "message": "Split independent outcomes.",
        }]
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return the current value."}],
            "requirements": [{
                "id": "R1",
                "claim": "The current value is returned.",
                "required": True,
                "task_clause_ids": ["C1"],
                "outcome_type": "query",
                "evidence_channels": ["answer", "tool_trace"],
                "pass_condition": "The returned value is supported by a read result.",
                "fail_condition": "The answer gives a value contradicted by the read result.",
                "indeterminate_condition": "No readable value or answer is available.",
            }, {
                "id": "R2",
                "claim": "Execution has no unrelated side effects.",
                "required": True,
                "task_clause_ids": [],
                "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace", "tool_trace"],
                "pass_condition": "All observed changes are necessary for the task.",
                "fail_condition": "An observed change is unrelated or destructive.",
                "indeterminate_condition": "Observed changes cannot be attributed.",
            }],
        }

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertNotIn("reference_evidence", request)
            self.assertEqual(request["task"], "Return the current value.")
            self.assertIn("task_clauses", request["response_contract"])
            self.assertTrue(any("共同对象" in item and "同一" in item for item in request["principles"]))
            self.assertTrue(any("并列" in item and "分别" in item for item in request["principles"]))
            integrity_rule = next(item for item in request["principles"] if "execution_integrity" in item)
            self.assertIn("风险较大的破坏性或冲突性副作用", integrity_rule)
            self.assertIn("没有此类风险则不添加", integrity_rule)
            self.assertNotIn("必要", integrity_rule)
            self.assertEqual(request["previous_issues"], previous_issues)
            self.assertIn("逐项解决", request["revision_instruction"])
            return InferenceResult(json.dumps({"response_contract": specification}), {}, "test")

        generated = generate_verification_spec(
            {"task_text": "Return the current value."},
            {"tools": [{"name": "read_value"}]},
            {},
            infer_fn=fake_infer,
            previous_issues=previous_issues,
        )

        self.assertEqual(generated, specification)

    def test_generate_evidence_plan_maps_each_subtask_without_freezing_reference_path(self) -> None:
        specification = {
            "schema_version": "1", "task_clauses": [],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": [], "outcome_type": "query",
                "evidence_channels": ["answer", "tool_trace"],
                "pass_condition": "The returned value is supported.",
                "fail_condition": "The returned value is contradicted.",
                "indeterminate_condition": "No conclusive evidence is available.",
            }],
        }
        plan = {
            "schema_version": "1",
            "requirements": [{
                "requirement_id": "R1",
                "sources": ["answer", "tool_trace"],
                "strategy": "Compare the stated value with any authoritative read result.",
                "alternatives": ["Equivalent read tools or persistent state may support the same value."],
            }],
        }

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertEqual(request["specification"], specification)
            self.assertIn("reference_evidence", request)
            self.assertTrue(any("不是白名单" in item for item in request["principles"]))
            self.assertIn("JSON", request["output_instruction"])
            return InferenceResult(json.dumps(plan), {}, "test")

        self.assertEqual(generate_evidence_plan(
            {"task_text": "Return the value."}, {}, specification, {}, {}, infer_fn=fake_infer,
        ), plan)

    def test_evidence_plan_may_add_a_valid_channel_missed_during_decomposition(self) -> None:
        specification = {
            "requirements": [{
                "id": "R1", "claim": "A result exists.", "required": True,
                "evidence_channels": ["workspace"],
                "pass_condition": "The result exists.", "fail_condition": "It is absent.",
            }],
        }
        plan = {"schema_version": "1", "requirements": [{
            "requirement_id": "R1",
            "sources": ["workspace", "tool_trace"],
            "strategy": "Use final state or an authoritative tool result.",
            "alternatives": [],
        }]}

        from task_gen.task_eval_verifier import validate_evidence_plan
        validate_evidence_plan(plan, specification)

        package = generate_planned_verifier(
            {"task_text": "Create result."}, {}, specification, plan, {}, {},
            infer_fn=lambda *_a, **_k: InferenceResult(json.dumps({
                "source": "def verify(ctx):\n    ctx.indeterminate_requirement('R1', 'unknown')\n",
            }), {}, "test"),
        )
        self.assertEqual(package["requirements"][0]["evidence_channels"], [
            "workspace", "tool_trace",
        ])

    def test_evidence_plan_ignores_harmless_model_explanation_fields(self) -> None:
        specification = {"requirements": [{"id": "R1", "evidence_channels": ["answer"]}]}
        plan = {
            "schema_version": "1",
            "analysis": "The answer is the direct observable result.",
            "requirements": [{
                "requirement_id": "R1", "sources": ["answer"],
                "strategy": "Inspect the answer.", "alternatives": [],
                "reasoning": "No persistent state is requested.",
            }],
        }

        from task_gen.task_eval_verifier import validate_evidence_plan
        validate_evidence_plan(plan, specification)

    def test_generate_evidence_plan_unwraps_a_named_contract(self) -> None:
        specification = {"requirements": [{"id": "R1", "evidence_channels": ["answer"]}]}
        plan = {"schema_version": "1", "requirements": [{
            "requirement_id": "R1", "sources": ["answer"],
            "strategy": "Inspect the answer.", "alternatives": [],
        }]}

        generated = generate_evidence_plan(
            {"task_text": "Return it."}, {}, specification, {}, {},
            infer_fn=lambda *_a, **_k: InferenceResult(
                json.dumps({"evidence_plan": plan, "analysis": "done"}), {}, "test",
            ),
        )

        self.assertEqual(generated, plan)

    def test_generate_evidence_plan_skips_an_explanatory_json_example(self) -> None:
        specification = {"requirements": [{"id": "R1", "evidence_channels": ["answer"]}]}
        plan = {"schema_version": "1", "requirements": [{
            "requirement_id": "R1", "sources": ["answer"],
            "strategy": "Inspect the answer.", "alternatives": [],
        }]}
        text = 'Example: {"requirement_id":"R1"}\nFinal: ' + json.dumps(plan)

        generated = generate_evidence_plan(
            {"task_text": "Return it."}, {}, specification, {}, {},
            infer_fn=lambda *_a, **_k: InferenceResult(text, {}, "test"),
        )

        self.assertEqual(generated, plan)

    def test_review_and_revise_verifier_directly_returns_final_code(self) -> None:
        specification = {
            "schema_version": "1", "task_clauses": [],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": [], "outcome_type": "query",
                "evidence_channels": ["answer"],
                "pass_condition": "The answer contains the value.",
                "fail_condition": "The answer contradicts the value.",
                "indeterminate_condition": "The answer is unavailable.",
            }],
        }
        evidence_plan = {
            "schema_version": "1", "requirements": [{
                "requirement_id": "R1", "sources": ["answer"],
                "strategy": "Read the answer.", "alternatives": [],
            }],
        }
        generated = generate_planned_verifier(
            {"task_text": "Return the value."}, {}, specification, evidence_plan, {}, {},
            infer_fn=lambda *_a, **_k: InferenceResult(json.dumps({
                "source": "def verify(ctx):\n    ctx.fail_requirement('R1', 'wrong', ['answer'])\n",
            }), {}, "test"),
        )

        def fake_review(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertEqual(request["current_verifier"]["source"], generated["source"])
            self.assertNotIn("approved", request["response_contract"])
            self.assertIn("JSON", request["output_instruction"])
            self.assertIn("Import", request["code_constraints"]["forbidden_syntax_nodes"])
            return InferenceResult(json.dumps({
                "source": "def verify(ctx):\n    ctx.semantic_requirement('R1', 'answer returns the requested value', ['answer'])\n",
            }), {}, "test")

        reviewed = review_and_revise_verifier(
            {"task_text": "Return the value."}, {}, specification, evidence_plan,
            generated, {}, {}, infer_fn=fake_review,
        )

        self.assertIn("semantic_requirement", reviewed["source"])
        self.assertEqual(reviewed["requirements"], generated["requirements"])

    def test_initial_verifier_generation_has_no_static_code_restrictions(self) -> None:
        specification = {
            "requirements": [{
                "id": "R1", "claim": "A result exists.", "required": True,
                "evidence_channels": ["answer"],
                "pass_condition": "The result exists.", "fail_condition": "It is absent.",
            }],
        }
        evidence_plan = {"schema_version": "1", "requirements": [{
            "requirement_id": "R1", "sources": ["answer"],
            "strategy": "Inspect the answer.", "alternatives": [],
        }]}

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertNotIn("runtime_rules", request)
            self.assertNotIn("code_constraints", request)
            return InferenceResult(json.dumps({
                "source": (
                    "def verify(ctx):\n"
                    "    getattr(ctx, 'pass_requirement')('R1', 'present', ['answer'])\n"
                ),
            }), {}, "test")

        package = generate_planned_verifier(
            {"task_text": "Return it."}, {}, specification, evidence_plan, {}, {},
            infer_fn=fake_infer,
        )

        self.assertIn("getattr", package["source"])

    def test_review_receives_full_code_contract_and_draft_violation(self) -> None:
        specification = {
            "requirements": [{
                "id": "R1", "claim": "A result exists.", "required": True,
                "evidence_channels": ["answer"],
                "pass_condition": "The result exists.", "fail_condition": "It is absent.",
            }],
        }
        evidence_plan = {"schema_version": "1", "requirements": [{
            "requirement_id": "R1", "sources": ["answer"],
            "strategy": "Inspect the answer.", "alternatives": [],
        }]}
        generated = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "A result exists.", "required": True,
                "evidence_channels": ["answer"],
                "pass_condition": "The result exists.", "fail_condition": "It is absent.",
            }],
            "source": (
                "def verify(ctx):\n"
                "    getattr(ctx, 'pass_requirement')('R1', 'present', ['answer'])\n"
            ),
        }

        def fake_review(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertFalse({
                "task", "environment", "specification", "evidence_plan", "reference_evidence",
                "verifier_context_api", "principles",
            } & request.keys())
            constraints = request["code_constraints"]
            self.assertIn("getattr", constraints["forbidden_calls"])
            self.assertIn("Import", constraints["forbidden_syntax_nodes"])
            self.assertIn("pass_requirement", constraints["allowed_context_methods"])
            self.assertIn("getattr", request["draft_validation_error"])
            return InferenceResult(json.dumps({
                "source": "def verify(ctx):\n    ctx.pass_requirement('R1', 'present', ['answer'])\n",
            }), {}, "test")

        reviewed = review_and_revise_verifier(
            {"task_text": "Return it."}, {}, specification, evidence_plan,
            generated, {}, {}, infer_fn=fake_review,
        )

        self.assertNotIn("getattr", reviewed["source"])

    def test_generate_planned_verifier_accepts_source_with_model_metadata(self) -> None:
        specification = {
            "schema_version": "1", "task_clauses": [],
            "requirements": [{
                "id": "R1", "claim": "A result exists.", "required": True,
                "task_clause_ids": [], "outcome_type": "persistent_state",
                "evidence_channels": ["workspace"],
                "pass_condition": "The result exists.",
                "fail_condition": "The result is absent.",
                "indeterminate_condition": "State is unavailable.",
            }],
        }
        evidence_plan = {"schema_version": "1", "requirements": [{
            "requirement_id": "R1", "sources": ["workspace"],
            "strategy": "Inspect final state.", "alternatives": [],
        }]}
        response = {
            "schema_version": "1",
            "requirements": [{"id": "R1"}],
            "source": "def verify(ctx):\n    ctx.indeterminate_requirement('R1', 'unknown')\n",
        }

        package = generate_planned_verifier(
            {"task_text": "Create result."}, {}, specification, evidence_plan, {}, {},
            infer_fn=lambda *_a, **_k: InferenceResult(json.dumps(response), {}, "test"),
        )

        self.assertEqual(package["source"], response["source"])

    def test_generate_planned_verifier_accepts_fenced_source_for_direct_review(self) -> None:
        specification = {
            "requirements": [{
                "id": "R1", "claim": "A result exists.", "required": True,
                "evidence_channels": ["workspace"],
                "pass_condition": "The result exists.", "fail_condition": "It is absent.",
            }],
        }
        evidence_plan = {"schema_version": "1", "requirements": [{
            "requirement_id": "R1", "sources": ["workspace"],
            "strategy": "Inspect final state.", "alternatives": [],
        }]}
        source = "def verify(ctx):\n    ctx.indeterminate_requirement('R1', 'unknown')\n"

        package = generate_planned_verifier(
            {"task_text": "Create result."}, {}, specification, evidence_plan, {}, {},
            infer_fn=lambda *_a, **_k: InferenceResult(f"```python\n{source}```", {}, "test"),
        )

        self.assertEqual(package["source"], source.rstrip())

    def test_validate_verification_spec_requires_exact_clause_coverage(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Create report."}],
            "requirements": [{
                "id": "R1", "claim": "No unrelated changes.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "No unrelated change.",
                "fail_condition": "Unrelated change exists.",
                "indeterminate_condition": "Change attribution is unavailable.",
            }],
        }

        with self.assertRaisesRegex(ValueError, "C1"):
            validate_verification_spec(specification)

    def test_review_verification_spec_returns_structured_issues(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Create report."}],
            "requirements": [{
                "id": "R1", "claim": "Create report with tool X.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "persistent_state",
                "evidence_channels": ["workspace"], "pass_condition": "Tool X creates it.",
                "fail_condition": "Tool X was not called.",
                "indeterminate_condition": "Tool trace is unavailable.",
            }, {
                "id": "R2", "claim": "No unrelated changes.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "No unrelated change.",
                "fail_condition": "Unrelated change exists.",
                "indeterminate_condition": "Change attribution is unavailable.",
            }],
        }
        review = {
            "approved": False,
            "issues": [{
                "code": "added_constraint",
                "task_clause_ids": ["C1"],
                "requirement_ids": ["R1"],
                "message": "The task does not require tool X.",
            }],
        }

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertEqual(request["specification"], specification)
            self.assertNotIn("reference_evidence", request)
            self.assertIn("execution_integrity", request["role"])
            self.assertIn("系统级", request["role"])
            return InferenceResult(json.dumps(review), {}, "test")

        self.assertEqual(review_verification_spec(
            {"task_text": "Create report."}, {}, specification, {}, infer_fn=fake_infer,
        ), review)

    def test_generate_proof_plan_defines_conclusive_and_unknown_absence(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return the value."}],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "query",
                "evidence_channels": ["answer", "tool_trace"], "pass_condition": "The answer contains the value.",
                "fail_condition": "The answer contradicts the value.",
                "indeterminate_condition": "The answer is absent.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "No unrelated change.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Change attribution is unavailable.",
            }],
        }
        plan = {
            "schema_version": "1", "bindings": [],
            "requirements": [{
                "requirement_id": "R1", "binding_ids": [],
                "evidence_sources": [{
                    "channel": "verifier_tool_call", "locator": "Agent final answer",
                    "provenance": "verifier_observation",
                    "use": "criterion", "completeness": "partial",
                    "absence_is_conclusive": False, "basis": "An absent answer proves no value.",
                }],
                "proof": "The answer states the requested value.",
                "disproof": "The answer states a contradictory value.",
                "indeterminate": "No answer or readable value is available.",
            }],
            "integrity": {
                "requirement_id": "R2", "observable_scope": "Workspace changes",
                "evidence_sources": [{
                    "channel": "workspace", "locator": "Workspace diff",
                    "provenance": "environment_contract", "use": "criterion",
                    "completeness": "complete", "absence_is_conclusive": False,
                    "basis": "The complete diff exposes persistent changes.",
                }],
                "proof": "All observed changes serve the task.",
                "explicit_violations": ["An observed change is demonstrably unrelated."],
                "indeterminate": "A change cannot be classified.",
            },
        }
        reference = {"answer": "8"}
        batch_rounds = 0

        def fake_infer(prompt: str | list[str], **_: object) -> InferenceResult | list[InferenceResult]:
            nonlocal batch_rounds
            requests = [json.loads(item) for item in prompt] if isinstance(prompt, list) else [json.loads(prompt)]
            for request in requests:
                self.assertEqual(request["reference_evidence"]["answer"], "8")
                self.assertIn("不等于任务失败", " ".join(request["principles"]))
                self.assertTrue(any("数量" in item and "上限" in item for item in request["principles"]))
                self.assertTrue(any(
                    "environment_contract" in item and "路径" in item and "枚举" in item and "稳定标识" in item
                    for item in request["principles"]
                ))
            if not isinstance(prompt, list):
                self.assertEqual(requests[0]["component"], "binding_design")
                return InferenceResult(json.dumps({
                    "bindings": plan["bindings"],
                    "requirement_bindings": {"R1": []},
                }), {}, "test")
            batch_rounds += 1
            expected = ["requirement_proof", "integrity_proof"]
            self.assertEqual([item["component"] for item in requests], expected)
            self.assertIn("call_tool", requests[0]["response_contract"]["evidence_sources"][0]["channel"])
            requirement = {key: value for key, value in plan["requirements"][0].items() if key != "requirement_id"}
            integrity = {key: value for key, value in plan["integrity"].items() if key != "requirement_id"}
            if batch_rounds == 1:
                return [
                    InferenceResult("not json", {}, "test"),
                    InferenceResult(json.dumps({**integrity, "explicit_violations": []}), {}, "test"),
                ]
            return [
                InferenceResult(json.dumps({"requirement_id": "ignored", **requirement}), {}, "test"),
                InferenceResult(json.dumps(integrity), {}, "test"),
            ]

        generated = generate_proof_plan(
            {"task_text": "Return the value."}, {}, specification, reference, {},
            infer_fn=fake_infer,
        )
        plan["requirements"][0]["evidence_sources"][0]["channel"] = "tool_trace"
        plan["requirements"][0]["evidence_sources"][0]["provenance"] = "environment_contract"
        self.assertEqual(generated, plan)
        self.assertEqual(batch_rounds, 2)

    def test_validate_proof_plan_rejects_conclusive_absence_from_partial_evidence(self) -> None:
        specification = {
            "schema_version": "1", "task_clauses": [{"id": "C1", "text": "Return x."}],
            "requirements": [{
                "id": "R1", "claim": "x", "required": True, "task_clause_ids": ["C1"],
                "outcome_type": "query", "evidence_channels": ["answer"],
                "pass_condition": "x", "fail_condition": "not x", "indeterminate_condition": "unknown",
            }, {
                "id": "R2", "claim": "safe", "required": True, "task_clause_ids": [],
                "outcome_type": "execution_integrity", "evidence_channels": ["workspace"],
                "pass_condition": "safe", "fail_condition": "damage", "indeterminate_condition": "unknown",
            }],
        }
        source = {
            "channel": "answer", "locator": "answer", "provenance": "task",
            "use": "criterion", "completeness": "partial", "absence_is_conclusive": True,
            "basis": "No answer was found.",
        }
        plan = {
            "schema_version": "1", "bindings": [],
            "requirements": [{
                "requirement_id": "R1", "binding_ids": [], "evidence_sources": [source],
                "proof": "x", "disproof": "not x", "indeterminate": "unknown",
            }],
            "integrity": {
                "requirement_id": "R2", "observable_scope": "workspace",
                "evidence_sources": [{**source, "channel": "workspace", "absence_is_conclusive": False}],
                "proof": "safe", "explicit_violations": ["damage"], "indeterminate": "unknown",
            },
        }

        with self.assertRaisesRegex(ValueError, "partial"):
            validate_proof_plan(plan, specification)

    def test_review_proof_plan_checks_alternative_implementations(self) -> None:
        review = {"approved": False, "issues": [{
            "code": "reference_path_required", "task_clause_ids": ["C1"],
            "requirement_ids": ["R1"], "message": "A reference tool is incorrectly required.",
        }]}

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertIn("没按参考方式执行", request["role"])
            self.assertIn("数量上限", request["role"])
            self.assertIn("环境契约", request["role"])
            self.assertIn("reference_overfit", request["role"])
            self.assertEqual(request["reference_evidence"]["calls"], [])
            return InferenceResult(json.dumps(review), {}, "test")

        with patch("task_gen.task_eval_verifier.validate_proof_plan"):
            self.assertEqual(review_proof_plan(
                {"task_text": "x"}, {}, {"requirements": []}, {"plan": True},
                {"calls": []}, {}, infer_fn=fake_infer,
            ), review)

    def test_generate_verifier_projects_frozen_requirements_and_only_generates_source(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return the value."}],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "query",
                "evidence_channels": ["answer", "tool_trace"],
                "pass_condition": "The answer and read result agree.",
                "fail_condition": "They contradict.",
                "indeterminate_condition": "Either is absent.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace", "tool_trace"],
                "pass_condition": "All changes are necessary.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Change attribution is unavailable.",
            }],
        }
        proof_plan = {"schema_version": "1", "bindings": [], "requirements": [], "integrity": {}}

        def fake_infer(prompt: str | list[str], **_: object) -> InferenceResult | list[InferenceResult]:
            if isinstance(prompt, list):
                requests = [json.loads(item) for item in prompt]
                self.assertEqual([item["requirement"]["id"] for item in requests], ["R1", "R2"])
                self.assertTrue(all("verifier_context_api" not in item for item in requests))
                self.assertTrue(all("shared_contract" in item for item in requests))
                source = (
                    "def check(shared, assignment):\n"
                    "    return {'status': 'indeterminate', 'reason': 'not enough evidence', "
                    "'evidence_refs': []}\n"
                )
                return [InferenceResult(json.dumps({"source": source}), {}, "test") for _ in requests]
            request = json.loads(prompt)
            self.assertEqual(request["specification"], specification)
            self.assertEqual(request["proof_plan"], proof_plan)
            self.assertNotIn("reference_evidence", request)
            self.assertEqual(request["component"], "shared_preparation")
            self.assertTrue(any("prepare" in item and "check" in item for item in request["implementation_principles"]))
            self.assertTrue(any("check(shared, assignment)" in item and "ctx" in item for item in request["implementation_principles"]))
            self.assertTrue(any(
                "环境契约" in item and "直接使用" in item and "枚举" in item
                for item in request["implementation_principles"]
            ))
            self.assertTrue(any("Schema" in item and "猜测" in item for item in request["implementation_principles"]))
            self.assertTrue(any(
                "明确反驳" in item and "indeterminate" in item
                for item in request["implementation_principles"]
            ))
            self.assertTrue(any(
                "重复" in item and "额外副作用" in item and "无关、破坏性或冲突" in item
                for item in request["implementation_principles"]
            ))
            self.assertEqual(set(request["response_contract"]), {"source"})
            return InferenceResult(json.dumps({
                "source": "def prepare(ctx):\n    return {}\n", "notes": "ignored",
            }), {}, "test")

        with patch("task_gen.task_eval_verifier.validate_proof_plan"):
            package = generate_verifier(
                {"task_text": "Return the value."}, {}, {"secret_reference": 8}, {},
                infer_fn=fake_infer, specification=specification, proof_plan=proof_plan,
            )

        self.assertIn("def prepare(ctx):", package["source"])
        self.assertIn("def check_1(shared, assignment):", package["source"])
        self.assertEqual(package["requirements"], [{
            "id": item["id"], "claim": item["claim"], "required": item["required"],
            "evidence_channels": item["evidence_channels"],
            "pass_condition": item["pass_condition"], "fail_condition": item["fail_condition"],
        } for item in specification["requirements"]])

    def test_generate_verifier_builds_shared_preparation_and_batched_checks(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return the value."}],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "query",
                "evidence_channels": ["answer"], "pass_condition": "The value is present.",
                "fail_condition": "The value is absent.",
                "indeterminate_condition": "The answer cannot be read.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "No changes exist.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Changes cannot be read.",
            }],
        }
        proof_plan = {"schema_version": "1", "bindings": [], "requirements": [], "integrity": {}}
        rounds: list[str] = []

        def fake_infer(prompt: str | list[str], **_: object) -> InferenceResult | list[InferenceResult]:
            if isinstance(prompt, str):
                request = json.loads(prompt)
                rounds.append(request["component"])
                self.assertEqual(request["component"], "shared_preparation")
                source = (
                    "def prepare(ctx):\n"
                    "    return {'answer': ctx.answer(), 'changes': ctx.changed_paths()}\n"
                )
                return InferenceResult(json.dumps({"source": source}), {}, "test")
            rounds.append("requirement_checks")
            requests = [json.loads(item) for item in prompt]
            self.assertEqual([item["requirement"]["id"] for item in requests], ["R1", "R2"])
            sources = [
                "def check(shared, assignment):\n"
                "    if shared['answer']:\n"
                "        return {'status': 'pass', 'reason': 'answer exists', 'evidence_refs': ['answer']}\n"
                "    return {'status': 'fail', 'reason': 'answer is empty', 'evidence_refs': ['answer']}\n",
                "def check(shared, assignment):\n"
                "    if shared['changes']:\n"
                "        return {'status': 'fail', 'reason': 'workspace changed', 'evidence_refs': []}\n"
                "    return {'status': 'pass', 'reason': 'workspace unchanged', 'evidence_refs': []}\n",
            ]
            return [InferenceResult(json.dumps({"source": source}), {}, "test") for source in sources]

        with patch("task_gen.task_eval_verifier.validate_proof_plan"):
            package = generate_verifier(
                {"task_text": "Return the value."}, {}, {}, {}, infer_fn=fake_infer,
                specification=specification, proof_plan=proof_plan,
            )

        results = run_verifier(package, {"answer": "8", "changed_paths": []})
        self.assertEqual(rounds, ["shared_preparation", "requirement_checks"])
        self.assertEqual([item["status"] for item in results], ["pass", "pass"])

    def test_generated_checker_cannot_access_context(self) -> None:
        response = InferenceResult(
            "def check(shared, assignment):\n"
            "    return {'status': 'pass', 'reason': ctx.answer(), 'evidence_refs': []}\n",
            {}, "test",
        )

        with self.assertRaisesRegex(ValueError, "ctx"):
            _component_function(response, "check", ("shared", "assignment"))

    def test_component_function_strips_safe_standard_library_imports(self) -> None:
        response = InferenceResult(
            "import re\nimport html\n"
            "def check(shared, assignment):\n"
            "    return {'status': 'pass', 'reason': re.sub('x', 'y', html.escape('x')), 'evidence_refs': []}\n",
            {}, "test",
        )

        function = _component_function(response, "check", ("shared", "assignment"))

        self.assertEqual(function.name, "check")
        self.assertFalse(any(isinstance(node, ast.Import) for node in ast.walk(function)))

    def test_component_function_rejects_from_import_that_would_lose_its_binding(self) -> None:
        response = InferenceResult(
            "from re import sub\n"
            "def check(shared, assignment):\n"
            "    return {'status': 'pass', 'reason': sub('x', 'y', 'x'), 'evidence_refs': []}\n",
            {}, "test",
        )

        with self.assertRaises((ValueError, json.JSONDecodeError)):
            _component_function(response, "check", ("shared", "assignment"))

    def test_verifier_may_raise_and_catch_a_local_error(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "Result exists.", "required": True,
                "evidence_channels": ["workspace"],
                "pass_condition": "Result exists.", "fail_condition": "Result is absent.",
            }],
            "source": (
                "def verify(ctx):\n"
                "    try:\n"
                "        raise ValueError('missing evidence')\n"
                "    except ValueError as error:\n"
                "        ctx.indeterminate_requirement('R1', str(error))\n"
            ),
        }

        validate_verifier(package)

    def test_assembled_checker_failure_does_not_stop_other_requirements(self) -> None:
        prepare = ast.parse("def prepare(ctx):\n    return {}\n").body[0]
        failing = ast.parse(
            "def check_1(shared):\n    return shared['missing']\n"
        ).body[0]
        passing = ast.parse(
            "def check_2(shared):\n"
            "    return {'status': 'pass', 'reason': 'supported', 'evidence_refs': []}\n"
        ).body[0]
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": requirement_id, "claim": requirement_id, "required": True,
                "evidence_channels": [], "pass_condition": "yes", "fail_condition": "no",
            } for requirement_id in ("R1", "R2")],
            "source": _assemble_verifier(prepare, [failing, passing], ["R1", "R2"]),
        }

        results = run_verifier(package, {"answer": "", "calls": [], "changed_paths": []})

        self.assertEqual([item["status"] for item in results], ["indeterminate", "pass"])

    def test_infer_component_batch_retries_only_failed_items(self) -> None:
        calls: list[list[str]] = []

        def fake_infer(prompt: str | list[str], **_: object) -> list[InferenceResult]:
            self.assertIsInstance(prompt, list)
            calls.append(prompt)
            if len(calls) == 1:
                raise BatchInferenceError([
                    InferenceResult("first", {}, "test"), RuntimeError("temporary failure"),
                ])
            return [InferenceResult("second", {}, "test")]

        responses = _infer_component_batch(["one", "two"], {}, fake_infer)

        self.assertEqual([item.text for item in responses], ["first", "second"])
        self.assertEqual(calls, [["one", "two"], ["two"]])

    def test_generate_verifier_keeps_dict_literals_inside_raw_source(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return the value."}],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "query",
                "evidence_channels": ["answer"],
                "pass_condition": "The answer contains the value.",
                "fail_condition": "The answer contradicts the value.",
                "indeterminate_condition": "The answer is absent.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"],
                "pass_condition": "No unrelated changes exist.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Changes cannot be attributed.",
            }],
        }
        def fake_infer(prompt: str | list[str], **_: object) -> InferenceResult | list[InferenceResult]:
            if isinstance(prompt, str):
                return InferenceResult(
                    "def prepare(ctx):\n    evidence = {\"kind\": \"answer\"}\n    return evidence\n",
                    {}, "test",
                )
            source = (
                "def check(shared, assignment):\n"
                "    return {'status': 'indeterminate', 'reason': shared['kind'], 'evidence_refs': []}\n"
            )
            return [InferenceResult(source, {}, "test") for _ in prompt]

        with patch("task_gen.task_eval_verifier.validate_proof_plan"):
            package = generate_verifier(
                {"task_text": "Return the value."}, {}, {}, {},
                infer_fn=fake_infer,
                specification=specification,
                proof_plan={"schema_version": "1"},
            )

        self.assertIn("evidence = {'kind': 'answer'}", package["source"])

    def test_generate_verifier_accepts_fenced_python_source(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return the value."}],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "query",
                "evidence_channels": ["answer"], "pass_condition": "The value is present.",
                "fail_condition": "The value is absent.",
                "indeterminate_condition": "The answer cannot be read.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "No unrelated changes exist.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Changes cannot be attributed.",
            }],
        }
        def fake_infer(prompt: str | list[str], **_: object) -> InferenceResult | list[InferenceResult]:
            if isinstance(prompt, str):
                source = "def prepare(ctx):\n    return {}"
                return InferenceResult(f"```python\n{source}\n```", {}, "test")
            source = (
                "def check(shared, assignment):\n"
                "    return {'status': 'indeterminate', 'reason': 'unknown', 'evidence_refs': []}"
            )
            return [InferenceResult(f"```python\n{source}\n```", {}, "test") for _ in prompt]

        with patch("task_gen.task_eval_verifier.validate_proof_plan"):
            package = generate_verifier(
                {"task_text": "Return the value."}, {}, {}, {},
                infer_fn=fake_infer,
                specification=specification,
                proof_plan={"schema_version": "1"},
            )

        self.assertIn("def prepare(ctx):", package["source"])
        self.assertIn("def check_2(shared, assignment):", package["source"])

    def test_review_verifier_implementation_rejects_unproven_pass_path(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Create report."}],
            "requirements": [{
                "id": "R1", "claim": "The report exists.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "persistent_state",
                "evidence_channels": ["workspace"], "pass_condition": "Final report exists.",
                "fail_condition": "Final report is absent.",
                "indeterminate_condition": "Final state cannot be read.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "All changes are necessary.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Change attribution is unavailable.",
            }],
        }
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": item["id"], "claim": item["claim"], "required": True,
                "evidence_channels": item["evidence_channels"],
                "pass_condition": item["pass_condition"], "fail_condition": item["fail_condition"],
            } for item in specification["requirements"]],
            "source": (
                "def verify(ctx):\n"
                "    ctx.pass_requirement('R1', 'assumed', [])\n"
                "    ctx.pass_requirement('R2', 'assumed', [])\n"
            ),
        }
        expected = {"approved": False, "issues": [{
            "code": "unsupported_pass",
            "task_clause_ids": ["C1"],
            "requirement_ids": ["R1"],
            "message": "R1 passes without reading final workspace evidence.",
        }]}

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertEqual(request["specification"], specification)
            self.assertEqual(request["verifier"], package)
            self.assertIn("exactly once", " ".join(request["review_dimensions"]))
            self.assertTrue(any(
                "environment contract" in item.lower() and "not reference overfitting" in item.lower()
                for item in request["review_dimensions"]
            ))
            return InferenceResult(json.dumps(expected), {}, "test")

        self.assertEqual(review_verifier_implementation(
            specification, package, {}, {}, {}, infer_fn=fake_infer,
        ), expected)

    def test_calibration_ablation_rejects_pass_using_an_undeclared_channel(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return the value."}],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "query",
                "evidence_channels": ["answer"], "pass_condition": "Answer contains the value.",
                "fail_condition": "Answer contradicts the value.",
                "indeterminate_condition": "Answer is absent.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "No unrelated change.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Change attribution is unavailable.",
            }],
        }
        package = {
            "schema_version": "1", "requirements": [{
                "id": item["id"], "claim": item["claim"], "required": True,
                "evidence_channels": item["evidence_channels"],
                "pass_condition": item["pass_condition"], "fail_condition": item["fail_condition"],
            } for item in specification["requirements"]],
            "source": (
                "def verify(ctx):\n"
                "    if ctx.answer() or ctx.calls():\n"
                "        refs = ['answer'] if ctx.answer() else ['tool_call:0']\n"
                "        ctx.pass_requirement('R1', 'present', refs)\n"
                "    else:\n"
                "        ctx.indeterminate_requirement('R1', 'absent')\n"
                "    if ctx.file('state.json', 'initial') and ctx.file('state.json', 'final'):\n"
                "        ctx.pass_requirement('R2', 'unchanged', ['initial:state.json', 'final:state.json'])\n"
                "    else:\n"
                "        ctx.indeterminate_requirement('R2', 'state unavailable')\n"
            ),
        }
        files = [{"path": "state.json", "sha256": "same"}]
        reference = {
            "answer": "8", "calls": [{"tool": "read_value", "result": {"value": 8}}],
            "changed_paths": [], "initial_files": files, "final_files": files,
        }
        empty = {"answer": "", "calls": [], "changed_paths": [], "initial_files": files, "final_files": files}

        with self.assertRaisesRegex(ValueError, "证据消融.*R1"):
            calibrate_verifier(package, reference, empty, specification=specification)

    def test_calibration_ablation_accepts_independently_sufficient_remaining_channel(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return the value."}],
            "requirements": [{
                "id": "R1", "claim": "The value is returned.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "query",
                "evidence_channels": ["answer", "tool_trace"],
                "pass_condition": "Either direct evidence independently proves value 8.",
                "fail_condition": "Available evidence contradicts value 8.",
                "indeterminate_condition": "Neither evidence class is available.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "No unrelated change.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Change attribution is unavailable.",
            }],
        }
        package = {
            "schema_version": "1", "requirements": [{
                "id": item["id"], "claim": item["claim"], "required": True,
                "evidence_channels": item["evidence_channels"],
                "pass_condition": item["pass_condition"], "fail_condition": item["fail_condition"],
            } for item in specification["requirements"]],
            "source": (
                "def verify(ctx):\n"
                "    if ctx.answer():\n"
                "        ctx.pass_requirement('R1', 'answer proves 8', ['answer'])\n"
                "    elif ctx.calls():\n"
                "        ctx.pass_requirement('R1', 'read result proves 8', ['tool_call:0'])\n"
                "    else:\n"
                "        ctx.indeterminate_requirement('R1', 'absent')\n"
                "    if ctx.file('state.json', 'initial') and ctx.file('state.json', 'final'):\n"
                "        ctx.pass_requirement('R2', 'unchanged', ['initial:state.json', 'final:state.json'])\n"
                "    else:\n"
                "        ctx.indeterminate_requirement('R2', 'state unavailable')\n"
            ),
        }
        files = [{"path": "state.json", "sha256": "same"}]
        reference = {
            "answer": "8", "calls": [{"tool": "read_value", "result": {"value": 8}}],
            "changed_paths": [], "initial_files": files, "final_files": files,
        }
        empty = {"answer": "", "calls": [], "changed_paths": [], "initial_files": files, "final_files": files}

        calibration = calibrate_verifier(
            package, reference, empty, specification=specification,
            infer_fn=lambda *_args, **_kwargs: InferenceResult(
                '{"status":"pass","reason":"remaining evidence independently proves value 8"}', {}, "test",
            ),
        )

        self.assertEqual({item["channel"] for item in calibration["ablations"]}, {
            "answer", "tool_trace", "workspace",
        })

    def test_task_reference_conflict_is_assessed_without_candidate_verifier(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Use transaction ABC."}],
            "requirements": [{
                "id": "R1", "claim": "Transaction ABC is used.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "persistent_state",
                "evidence_channels": ["workspace"], "pass_condition": "Final state uses ABC.",
                "fail_condition": "Final state uses a different transaction.",
                "indeterminate_condition": "Final state is unavailable.",
            }, {
                "id": "R2", "claim": "No unrelated side effects.", "required": True,
                "task_clause_ids": [], "outcome_type": "execution_integrity",
                "evidence_channels": ["workspace"], "pass_condition": "No unrelated change.",
                "fail_condition": "An unrelated change exists.",
                "indeterminate_condition": "Change attribution is unavailable.",
            }],
        }
        assessment = {"conflict": True, "issues": [{
            "requirement_id": "R1", "task_clause_ids": ["C1"],
            "claim": "Transaction ABC is used.", "evidence_refs": ["final:ledger.json"],
            "message": "Reference final state uses transaction ABCD instead of ABC.",
        }]}

        def fake_infer(prompt: str, **_: object) -> InferenceResult:
            request = json.loads(prompt)
            self.assertNotIn("verifier", request)
            self.assertNotIn("candidate", json.dumps(request))
            return InferenceResult(json.dumps(assessment), {}, "test")

        self.assertEqual(assess_task_reference_conflict(
            {"task_text": "Use transaction ABC."}, specification,
            {"final_files": [{"path": "ledger.json"}]}, {}, infer_fn=fake_infer,
        ), assessment)

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

    def test_verifier_tool_call_returns_stable_evidence_reference(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{
                "id": "R1", "claim": "value is readable", "required": True,
                "evidence_channels": ["tool_trace"], "pass_condition": "read succeeds",
                "fail_condition": "read fails",
            }],
            "source": (
                "def verify(ctx):\n"
                "    observation = ctx.call_tool('read_value', {})\n"
                "    ctx.pass_requirement('R1', 'read completed', [observation['evidence_ref']])\n"
            ),
        }
        tool = {
            "name": "read_value",
            "internal": {"code": "def run(arguments, context):\n    return {'value': 8}\n"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial = root / "initial"
            final = root / "final"
            initial.mkdir()
            final.mkdir()

            results = run_verifier(
                package,
                {"answer": "", "calls": [], "changed_paths": []},
                initial_state=initial,
                final_state=final,
                tools=[tool],
            )

        self.assertEqual(results[0]["evidence_refs"], ["verifier_call:0"])

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
        self.assertIn("按任务动词要求的状态变化", role)
        self.assertIn("最终回答不能补足状态证据", role)
        self.assertIn("技术 ID、路径、序列化格式和措辞不是业务结果", role)

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

    def test_witness_group_does_not_splice_requirements_across_assignments(self) -> None:
        results = _select_witness_group({
            "group_id": "G1",
            "requirement_ids": ["R1", "R2"],
            "complete": True,
            "reason": "all candidate records were inspected",
            "evidence_refs": [],
            "rows": [{
                "assignment_id": "first",
                "results": [
                    {"requirement_id": "R1", "status": "pass", "reason": "title matches", "evidence_refs": []},
                    {"requirement_id": "R2", "status": "fail", "reason": "severity differs", "evidence_refs": []},
                ],
            }, {
                "assignment_id": "second",
                "results": [
                    {"requirement_id": "R1", "status": "fail", "reason": "title differs", "evidence_refs": []},
                    {"requirement_id": "R2", "status": "pass", "reason": "severity matches", "evidence_refs": []},
                ],
            }],
        })

        self.assertIn("fail", {item["status"] for item in results})

    def test_witness_group_selects_one_consistent_assignment(self) -> None:
        results = _select_witness_group({
            "group_id": "G1",
            "requirement_ids": ["R1", "R2"],
            "complete": True,
            "reason": "complete",
            "evidence_refs": [],
            "rows": [{
                "assignment_id": "first",
                "results": [
                    {"requirement_id": requirement_id, "status": "pass", "reason": "matched", "evidence_refs": []}
                    for requirement_id in ("R1", "R2")
                ],
            }],
        })

        self.assertEqual([item["status"] for item in results], ["pass", "pass"])

    def test_incomplete_witness_domain_cannot_prove_failure(self) -> None:
        results = _select_witness_group({
            "group_id": "G1",
            "requirement_ids": ["R1", "R2"],
            "complete": False,
            "reason": "only a partial listing was available",
            "evidence_refs": [],
            "rows": [],
        })

        self.assertEqual([item["status"] for item in results], ["indeterminate", "indeterminate"])

    def test_witness_groups_are_derived_from_shared_bindings(self) -> None:
        groups = _witness_groups({
            "requirements": [
                {"requirement_id": "R1", "binding_ids": ["B1"]},
                {"requirement_id": "R2", "binding_ids": ["B1", "B2"]},
                {"requirement_id": "R3", "binding_ids": ["B3"]},
            ],
            "integrity": {"requirement_id": "R4"},
        })

        self.assertEqual(groups, [
            {"group_id": "G1", "requirement_ids": ["R1", "R2"], "binding_ids": ["B1", "B2"]},
            {"group_id": "G2", "requirement_ids": ["R3"], "binding_ids": ["B3"]},
        ])

    def test_assembled_witness_verifier_rejects_split_success(self) -> None:
        prepare = ast.parse(
            "def prepare(ctx):\n"
            "    return {'groups': {'G1': {'assignments': "
            "[{'B1': {'title': 'right', 'severity': 'low'}}, "
            " {'B1': {'title': 'wrong', 'severity': 'high'}}], "
            "'complete': True, 'reason': 'complete', 'evidence_refs': []}}}\n"
        ).body[0]
        title_check = ast.parse(
            "def check_1(shared, assignment):\n"
            "    matched = assignment['B1']['title'] == 'right'\n"
            "    return {'status': 'pass' if matched else 'fail', 'reason': 'title checked', 'evidence_refs': []}\n"
        ).body[0]
        severity_check = ast.parse(
            "def check_2(shared, assignment):\n"
            "    matched = assignment['B1']['severity'] == 'high'\n"
            "    return {'status': 'pass' if matched else 'fail', 'reason': 'severity checked', 'evidence_refs': []}\n"
        ).body[0]
        integrity_check = ast.parse(
            "def check_3(shared, assignment):\n"
            "    return {'status': 'pass', 'reason': 'no unrelated effects', 'evidence_refs': []}\n"
        ).body[0]
        requirements = [{
            "id": requirement_id, "claim": requirement_id, "required": True,
            "evidence_channels": [], "pass_condition": "yes", "fail_condition": "no",
        } for requirement_id in ("R1", "R2", "R3")]
        package = {
            "schema_version": "1",
            "requirements": requirements,
            "source": _assemble_witness_verifier(
                prepare,
                [title_check, severity_check, integrity_check],
                [{"group_id": "G1", "requirement_ids": ["R1", "R2"], "binding_ids": ["B1"]}],
                "R3",
            ),
        }

        results = run_verifier(package, {"answer": "", "calls": [], "changed_paths": []})

        self.assertIn("fail", {item["status"] for item in results[:2]})
        self.assertEqual(results[2]["status"], "pass")

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
            }], "verifier_calls": []}, "error": None,
        }) as call_tool:
            run_verifier(package, {"answer": "", "calls": [], "changed_paths": []})

        workspace = call_tool.call_args.args[2]
        self.assertNotEqual(workspace, Path.cwd())
        self.assertTrue(workspace.parent.name.startswith("task-verifier-"))

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

    def test_verifier_allows_deleting_local_data(self) -> None:
        package = {
            "schema_version": "1",
            "requirements": [{"id": "R1", "claim": "x", "required": True,
                               "evidence_channels": [], "pass_condition": "x", "fail_condition": "y"}],
            "source": "def verify(ctx):\n"
            "    local = {'unused': 1}\n"
            "    del local['unused']\n"
            "    ctx.pass_requirement('R1', 'ok', [])\n",
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
            principles = request["judging_principles"]
            requirements = request["construction_requirements"]
            self.assertTrue(any("唯一成功标准" in rule and "参考回答" in rule for rule in principles))
            self.assertTrue(any("工具、顺序、次数" in rule for rule in principles))
            self.assertTrue(any("同一业务对象" in rule and "字段" in rule for rule in principles))
            self.assertTrue(any("业务身份" in rule and "动态 ID" in rule for rule in principles))
            self.assertTrue(any("每项 requirement" in rule and "恰好" in rule for rule in requirements))
            self.assertTrue(any("没有返回值" in rule for rule in requirements))
            self.assertTrue(any("initial" in rule and "final" in rule for rule in requirements))
            self.assertTrue(any("任务不得整体通过" in rule and "保持不变" in rule for rule in requirements))
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
        self.assertIn("原任务是成功标准的唯一权威", requests[0]["role"])
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

    def test_prepare_verifier_runs_each_lightweight_stage_once(self) -> None:
        specification = {"schema_version": "1", "task_clauses": [], "requirements": []}
        evidence_plan = {"schema_version": "1", "requirements": []}
        generated = {"schema_version": "1", "requirements": [], "source": "generated"}
        reviewed = {"schema_version": "1", "requirements": [], "source": "reviewed"}
        order: list[str] = []

        with patch("task_gen.task_eval_verifier.generate_verification_spec", side_effect=lambda *_a, **_k: order.append("spec") or specification), \
             patch("task_gen.task_eval_verifier.generate_evidence_plan", side_effect=lambda *_a, **_k: order.append("evidence") or evidence_plan), \
             patch("task_gen.task_eval_verifier.generate_planned_verifier", side_effect=lambda *_a, **_k: order.append("implementation") or generated), \
             patch("task_gen.task_eval_verifier.review_and_revise_verifier", side_effect=lambda *_a, **_k: order.append("review") or reviewed), \
             patch("task_gen.task_eval_verifier.validate_verifier"):
            actual, metadata, history = prepare_verifier(
                {"task_text": "return 8"}, {}, {}, {}, {}, attempts=5,
            )

        self.assertEqual(order, ["spec", "evidence", "implementation", "review"])
        self.assertEqual(actual, reviewed)
        self.assertEqual(metadata["specification"], specification)
        self.assertEqual(metadata["evidence_plan"], evidence_plan)
        self.assertEqual(metadata["status"], "reviewed")
        self.assertEqual([item["stage"] for item in history], [
            "subtask_plan", "evidence_plan", "implementation", "implementation_review",
        ])

    def test_lightweight_pipeline_accepts_spec_without_integrity(self) -> None:
        specification = {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "Return 8."}],
            "requirements": [{
                "id": "R1", "claim": "Return 8.", "required": True,
                "task_clause_ids": ["C1"], "outcome_type": "query",
                "evidence_channels": ["answer"],
                "pass_condition": "The answer is 8.",
                "fail_condition": "The answer is another value.",
                "indeterminate_condition": "No answer is available.",
            }],
        }
        plan = {"schema_version": "1", "requirements": [{
            "requirement_id": "R1", "sources": ["answer"],
            "strategy": "Read the answer.", "alternatives": [],
        }]}
        code = {"source": "def verify(ctx):\n    ctx.pass_requirement('R1', 'Answer is 8.', ['answer'])\n"}
        responses = iter([specification, plan, code, code])

        package, metadata, history = prepare_verifier(
            {"task_text": "Return 8."}, {}, {}, {}, {},
            infer_fn=lambda *_a, **_k: InferenceResult(json.dumps(next(responses)), {}, "test"),
        )

        self.assertEqual([item["id"] for item in package["requirements"]], ["R1"])
        self.assertEqual(metadata["specification"], specification)
        self.assertEqual(len(history), 4)
        results = run_verifier(package, {"answer": "8", "calls": [], "changed_paths": []})
        self.assertEqual(aggregate_results(package["requirements"], results)["outcome"], "pass")

    def test_prepare_verifier_does_not_retry_a_failed_stage(self) -> None:
        specification = {"schema_version": "1", "task_clauses": [], "requirements": []}
        with patch("task_gen.task_eval_verifier.generate_verification_spec", return_value=specification), \
             patch("task_gen.task_eval_verifier.generate_evidence_plan", side_effect=ValueError("invalid plan")) as generate:
            with self.assertRaises(VerifierPreparationError) as raised:
                prepare_verifier({"task_text": "return 8"}, {}, {}, {}, {}, attempts=5)

        generate.assert_called_once()
        self.assertEqual(raised.exception.attempts[-1]["stage"], "evidence_plan")
        self.assertIn("invalid plan", raised.exception.attempts[-1]["error"])

    def test_agent_prompt_states_the_tool_call_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = load_cases(self._write_case_at(root / "source"))[0]

            prompt = json.loads(_agent_prompt(case, 7))

            self.assertIn("7", prompt["role"])
            self.assertIn("tool calls", prompt["role"])

    def test_task_evaluation_uses_isolated_codex_and_defaults_to_fifty_calls(self) -> None:
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
                _run_agent("task", root, root / "server.json", root / "trace", {
                    "timeout_seconds": 321,
                    "codex_home": "~/.codex-task-eval",
                })

            self.assertEqual(captured["timeout_seconds"], 321)
            self.assertEqual(captured["codex_home"], "~/.codex-task-eval")

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

    def test_eval_codex_client_bypasses_approval_for_isolated_workspace_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments_path = root / "arguments.txt"
            codex_home_path = root / "codex-home.txt"
            executable = root / "fake-codex"
            executable.write_text(
                "#!/usr/bin/env bash\nset -eu\n"
                f'printf "%s\\n" "$@" > {arguments_path}\n'
                f'printf "%s" "$CODEX_HOME" > {codex_home_path}\n'
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
                codex_home=root / "isolated-codex",
            )

            client.run("work", working_directory=root)

            arguments = arguments_path.read_text(encoding="utf-8").splitlines()
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", arguments)
            self.assertNotIn("--approve-for-me", arguments)
            self.assertNotIn("--sandbox", arguments)
            self.assertIn("mcp_servers={}", arguments)
            self.assertEqual(codex_home_path.read_text(), str((root / "isolated-codex").resolve()))

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

    def test_evaluate_case_does_not_rejudge_completed_verifier_results(self) -> None:
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
            def unexpected_judge(*_args: object, **_kwargs: object) -> InferenceResult:
                raise AssertionError("deterministic verifier result must not be reviewed again")

            with patch("task_gen.task_eval.prepare_verifier", return_value=(package, {}, [])):
                result = evaluate_case(
                    case, root / "evaluation", {},
                    agent_run_fn=lambda *_args: "The value is 7.", judge_infer_fn=unexpected_judge,
                )

            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["evaluation"]["requirements"][0]["reason"], "overfit check failed",
            )

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
