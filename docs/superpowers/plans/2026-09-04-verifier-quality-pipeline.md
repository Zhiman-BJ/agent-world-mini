# Verifier Quality Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a staged verifier preparation pipeline whose frozen requirements come only from the task, whose proof logic and code are independently reviewed, and whose evidence dependence is calibrated.

**Architecture:** Split preparation into specification generation/review, proof-plan generation/review, and source generation/review, then reuse the existing runtime for reference, counterfactual, empty, and channel-ablation calibration. Keep the final verifier package unchanged and attach richer diagnostics only to preparation history and calibration.

**Tech Stack:** Python 3.10 standard library, existing Codex inference adapter, existing verifier sandbox, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-04-verifier-quality-pipeline-design.md`

## Global Constraints

- Task text is the only authority for success requirements.
- Specification generation must not receive reference evidence.
- Reference evidence may guide the proof plan but must not be sent to source generation.
- Failure requires explicit contradiction or conclusive absence in a complete authoritative source; otherwise the result is `indeterminate`.
- Existing `schema_version`/`requirements`/`source` verifier packages remain executable.
- No pipeline, contract, Agent-execution, or MCP changes.
- No new dependencies or files beyond this plan and existing verifier tests.

---

### Task 1: Specification Generation And Review

**Files:**
- Modify: `task_gen/task_eval_verifier.py`
- Test: `tests/test_task_eval.py`

**Interfaces:**
- Produces: `generate_verification_spec(task, environment, llm_config, infer_fn, previous_issues=None) -> dict`
- Produces: `review_verification_spec(task, environment, specification, llm_config, infer_fn) -> dict`
- Produces: `validate_verification_spec(specification) -> None`

- [ ] Add tests proving specification generation receives no reference evidence and emits task-clause coverage, atomic requirements, evidence channels, and three-state conditions.
- [ ] Add tests proving review rejects missing coverage and extra constraints through a structured `approved/issues` response.
- [ ] Run the focused tests and confirm failure because the interfaces do not exist.
- [ ] Implement strict specification and review schemas plus first-principles prompts.
- [ ] Run the focused tests and the existing verifier suite.
- [ ] Commit the independently testable specification stage.

### Task 2: Frozen-Spec Implementation And Review

**Files:**
- Modify: `task_gen/task_eval_verifier.py`
- Test: `tests/test_task_eval.py`

**Interfaces:**
- Changes: `generate_verifier(...)` accepts a reviewed specification and copies its requirements exactly.
- Produces: `review_verifier_implementation(specification, package, environment, reference_evidence, llm_config, infer_fn) -> dict`

- [ ] Add tests proving generated requirements equal the frozen specification and the implementation prompt cannot reinterpret them.
- [ ] Add tests proving implementation review rejects missing result paths, insufficient evidence, and reference-specific constraints.
- [ ] Run focused tests and confirm the old one-shot generator fails them.
- [ ] Implement source-only generation, package assembly, and structured implementation review.
- [ ] Run focused and full verifier tests.
- [ ] Commit the independently testable implementation-review stage.

### Task 3: Evidence Ablation And Conflict Classification

**Files:**
- Modify: `task_gen/task_eval_verifier.py`
- Test: `tests/test_task_eval.py`

**Interfaces:**
- Produces: channel-ablation evidence derived from actual cited evidence.
- Changes: `calibrate_verifier(...)` rejects non-preservation requirements that remain supported only by removed evidence.
- Produces: structured conflict assessment used only after reference calibration failure.

- [ ] Add a failing test where a verifier passes from an answer, then incorrectly keeps passing after its only answer evidence is removed.
- [ ] Add tests showing independently sufficient remaining evidence is accepted and preservation requirements are unaffected.
- [ ] Add tests distinguishing `task_reference_conflict` from `verifier_generation_error` without inferring conflict from candidate disagreement alone.
- [ ] Run focused tests and confirm the missing behavior.
- [ ] Implement minimal channel ablation, calibration diagnostics, and structured conflict assessment.
- [ ] Run focused and full verifier tests.
- [ ] Commit the calibration and classification stage.

### Task 4: Preparation Orchestration And Real Terra Run

**Files:**
- Modify: `task_gen/task_eval_verifier.py`
- Test: `tests/test_task_eval.py`

**Interfaces:**
- Changes: `prepare_verifier(...)` performs spec generation, spec review, implementation generation, static validation, implementation review, calibration, and conflict classification.
- Preserves: return shape `(package, calibration, history)` and frozen verifier cache compatibility.

- [ ] Add orchestration tests for successful stage order, review retry using only issue summaries, and terminal task-reference conflict.
- [ ] Run focused tests and confirm failure against old orchestration.
- [ ] Implement orchestration and rich history records without changing callers.
- [ ] Run `python -m unittest tests.test_task_eval` and `git diff --check`.
- [ ] Run one complete Terra task from BugAgent or HappyScribe and inspect the saved specification, both reviews, calibration results, Agent evidence, and final requirement outcomes.
- [ ] Fix only demonstrated defects by adding a failing regression test first, then rerun the real task.
- [ ] Commit the verified implementation.

### Task 5: Reviewed Proof Plan

**Files:**
- Modify: `task_gen/task_eval_verifier.py`
- Modify: `task_gen/task_eval.py`
- Test: `tests/test_task_eval.py`

**Interfaces:**
- Produces: `generate_proof_plan(task, environment, specification, reference_evidence, llm_config, infer_fn, previous_issues=None) -> dict`
- Produces: `validate_proof_plan(plan, specification) -> None`
- Produces: `review_proof_plan(task, environment, specification, plan, reference_evidence, llm_config, infer_fn) -> dict`
- Changes: `generate_verifier(...)` consumes a frozen proof plan and does not receive raw reference evidence in its prompt.

- [ ] Add a failing test proving a proof plan records shared business bindings, evidence completeness, conclusive absence, and three-state decision rules.
- [ ] Run the focused test and confirm failure because proof-plan interfaces do not exist.
- [ ] Implement strict proof-plan validation plus the approved generation and review prompts.
- [ ] Run focused and full verifier tests.
- [ ] Add a failing orchestration test proving proof-plan review occurs before implementation and rejected plans retry from issue summaries.
- [ ] Implement the proof-plan stage in `prepare_verifier` and preserve the final verifier package shape.
- [ ] Add a failing test proving the source-generation prompt contains the frozen plan and omits raw reference evidence.
- [ ] Change source generation to implement the plan without seeing raw reference evidence; keep reference evidence only for plan review, implementation review, and calibration.
- [ ] Bump the verifier cache version, run `python -m unittest tests.test_task_eval`, and run `git diff --check`.
- [ ] Run one BugAgent Terra task in a fresh output directory and inspect the plan, both reviews, calibration, Agent evidence, and final outcome.
