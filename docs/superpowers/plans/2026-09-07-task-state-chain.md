# Task-State-Chain Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; the stages share contracts and are implemented together in the existing step234 worktree.

**Goal:** Generate natural tasks whose applicable requirements are completed by the sampled-and-reviewed chain in the given initial state.

**Architecture:** Keep the existing stages and review reason. Pass review_guidance to every downstream model stage. Composition produces text, reflection improves expression, answers report evidence and gaps, and Step 5 alone decides semantic acceptance.

**Tech Stack:** Python, unittest/pytest, JSON Schema, existing Codex client.

**Spec:** The user-approved decisions in this conversation, restated below.

## Global Constraints

- A task may be a conditional tree; initial state selects the applicable path, which the chain implements. No requirement that task wording enumerate calls or that calls cover inactive branches.
- Review reason explains initial facts, objective applicability, chain responsibilities, evidence and uncertainty. It is not new requirements or proof of execution.
- Draft and answer have no semantic rejection field. Runtime/format errors remain failures. Reflection failure retains the draft.
- Final task text is the sole requirements baseline for answer and validation; validation receives all public tools, not only chain tools.
- Remove generated resource constraints and dependent export/schema/evaluator rules; retain environment permissions and execution isolation.
- Do not add resolved_facts, state-diff auditing, domain-specific patches, retries, dependencies, or sampler changes.
- Model timeout stays 600 seconds; concurrency 4; same graph, sample count 10000, seed 42, review 20, execute up to 10.
- Preserve user edits, retain branch and worktree, no push/merge, no additional approval pauses.

## Task 1: Checkpoint and Regression Tests

- [x] Snapshot current changes: 1f96f62.
- [x] Run existing pipeline tests as baseline: 54 passed.
- [x] Update tests/test_tool_graph_tasks.py to exercise three-round composition, exact review_guidance propagation, final-task-only answer/validation inputs, public tool availability, partial answers reaching validation, strict malformed-output rejection, and export without resource_constraints.
- [x] Run `python -m pytest tests/test_tool_graph_tasks.py -q`: 11 expected failures, then 16 passed after implementation. Evaluator dependency test also failed before its fix.

## Task 2: Contracts, Prompts and Consumers

- [x] step_2_chain_sample.py: concise objective/review prompts preserving sampler and scoring; reason explains task-state-chain fit; valid JSON response example.
- [x] step_3_chain_execute.py: clarify plan/evidence precedence and query scope in parameter prompt; keep executor behavior.
- [x] step_4_task_compose.py: three rounds; reuse llm_review.reason as review_guidance; single-field draft/answer output; remove resource parser/round; preserve reflection and batch/runtime recovery.
- [x] step_5_task_validate.py: use execution_matches_task, answer_matches_task, task_is_usable; remove objective requirement and resource fields; all public tools and guidance; task-based evidence-grounded judgments.
- [x] contracts.py, task schema/example, task_eval.py and docs: synchronize removed field and renamed internal validation flags.
- [x] Run focused tests, then all tool_graph, task_eval, schema and Codex client tests: 111 passed; schema/example valid via repository validator selection.
- [x] Independent code review: no blockers. Partial-answer integration assertion added, 111 tests passed again. Implementation commit: 9659cb5.

## Task 3: Full Experiment and Evidence Review

- [x] Run `python scripts/run_initial_probe_experiment.py --graph runs/step234_e2e/20260904_150721_128846_bugagent_gpt-5.6-terra/intermediate/step_1_bundle.json --output-root runs/task_state_chain_e2e`.
- [x] Record implementation/config/source signatures before and after; do not change code or configuration mid-run.
- [x] Wait for all stages and inspect every task, answer, validation and model failure. Separate runtime success from semantic quality.
- [x] Write reports/2026-09-07-task-state-chain-e2e.md with exact run path, counts, timing and remaining issues; update this checklist.

## Progress

Implementation done; independent read-only review found no blocking issues. Added its suggested composition-to-validation partial-answer assertion. Existing review scoring experiment is the comparison baseline, not a paired fixed-objective experiment. No legacy artifact migration: new exports omit resource_constraints; historical run files stay untouched.

Run completed: runs/task_state_chain_e2e/20260907_211604_455750_bugagent_gpt-5.6-terra, exit 0, approximately 63.6 minutes. Objective generation 20/20; review 19 accepted plus one timeout; execution 9/10; all nine drafts, reflections and answers succeeded; seven exports. Implementation/config/source signatures unchanged. Remaining findings are documented in reports/2026-09-07-task-state-chain-e2e.md; failed candidates were not rerun and no acceptance rules were relaxed.
