# Step 2-5 Objective Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Step 2 establish one business objective that Step 3 executes, Step 4 expresses, and Step 5 independently checks.

**Architecture:** Add one `objective` string to each accepted Step 2 candidate and carry it through the existing append-only task dictionaries. Keep the existing sampling, execution, composition, and validation modules; replace duplicate intent generation and over-specific prompts in place, and reuse the existing structural similarity calculation after review.

**Tech Stack:** Python 3.10, standard library, `unittest`, existing `jsonschema` and LLM helpers.

**Spec:** `docs/superpowers/specs/2026-09-04-step234-objective-pipeline-design.md`

## Global Constraints

- Do not change the Step 0-5 order, `pipeline.py`, `run_io.py`, or the final Task JSON Schema.
- Do not add `chain_plan`, `argument_sources`, `resolved_facts`, `state_changes`, or workspace-diff validation.
- Do not add dependencies, services, prompt slots, or environment-specific production rules.
- Keep `objective` internal to the pipeline candidate; do not place it in the final `task` object.
- Keep Step 4 reflection output as `analyze`, `need_revision`, and `task_text`; reflection failure must not reject a valid draft.
- Prefer deterministic structure checks in code and semantic judgments in LLM prompts.

---

### Task 1: Step 2 Produces Objectives and Rebalances Reviewed Chains

**Files:**
- Modify: `task_gen/tool_graph/step_2_chain_sample.py:1-633`
- Modify: `task_gen/tool_graph/contracts.py:125-174`
- Test: `tests/test_tool_graph_steps.py:162-336`

**Interfaces:**
- Consumes: `SampleChainsInput` with the existing config, environment, and tool graph.
- Produces: accepted candidates containing `objective: str`; `_review_chains(...) -> tuple[list[dict[str, Any]], int, int, int]`; `_select_final_chains(candidates: list[dict[str, Any]], count: int, diversity_lambda: float) -> list[dict[str, Any]]`.

- [ ] **Step 1: Replace fallback tests with objective/rejection contract tests**

Update the Step 2 fake review responses to use the exact accepted shape:

```python
{
    "accepted": True,
    "chain": ["a", "b"],
    "objective": "Create the requested business record from an available item.",
    "reason": "The calls jointly discover and create the result.",
}
```

Replace `test_bad_review_falls_back_to_original_chain` with assertions that an invalid review produces no task and increments `review_error_count`. Replace the out-of-range fallback test with the same rejection behavior. Add a rejected response test:

```python
response = {
    "accepted": False,
    "chain": [],
    "objective": None,
    "reason": "The calls cannot form one supported business objective.",
}
self.assertEqual(output["tasks"], [])
self.assertEqual(output["sampling_report"]["review_rejected_count"], 1)
```

Assert accepted tasks persist `objective`, logic-scoring prompts contain that objective, and review prompts contain the first-principles objective instructions rather than the old identifier-specific list.

- [ ] **Step 2: Add a failing post-review rebalance test**

Import `_select_final_chains` and add candidates where two equal-logic-score chains share most edges while a third is structurally distinct:

```python
candidates = [
    {"chain": ["a", "x", "y", "z"], "score": 10, "logic_score": 5},
    {"chain": ["b", "x", "y", "z"], "score": 9, "logic_score": 5},
    {"chain": ["a", "p", "q", "r"], "score": 1, "logic_score": 5},
    {"chain": ["m", "n"], "score": 100, "logic_score": 4},
]
selected = _select_final_chains(candidates, count=2, diversity_lambda=10)
self.assertEqual([item["chain"] for item in selected], [
    ["a", "x", "y", "z"],
    ["a", "p", "q", "r"],
])
```

This proves diversity affects equal-quality candidates without allowing a lower `logic_score` to jump the higher score group.

- [ ] **Step 3: Run Step 2 tests and verify the new expectations fail**

Run:

```bash
python -m unittest tests.test_tool_graph_steps.ChainSampleTest
```

Expected: failures because reviews do not parse `accepted/objective`, invalid reviews still fall back, and `_select_final_chains` does not exist.

- [ ] **Step 4: Implement strict review acceptance and objective persistence**

Change `_review_chains` so each response must have exactly four fields. Accepted responses require `accepted is True`, a non-empty valid chain, a non-empty stripped objective, and a non-empty reason. Rejected responses require `accepted is False`, `chain == []`, `objective is None`, and a non-empty reason. Invalid responses increment the error count and are omitted; explicit rejections increment a separate rejection count and are omitted.

Use a compact prompt built around these principles:

```text
判断候选链能否完成一个统一、自然、可验证的业务目标。
检查每次调用是否对目标有独立且不重复的贡献，以及所需信息是否能从环境或前序调用获得。
可以修订链；无法形成可信目标时拒绝。
同时给出修订后链对应的结果导向目标模板，不假设尚未观察到的运行时事实。
```

Include `objective` in the logic-score context and in every emitted task. Do not preserve the original chain after review failure.

- [ ] **Step 5: Implement deterministic post-review structural rebalance**

Add `_select_final_chains`. Process `logic_score` values from high to low. Within the current score group, greedily maximize the same normalized sampling score minus `diversity_lambda * maximum_shared_edge_similarity`, measuring similarity against all candidates already selected. Stop at `keep_top_count`.

Use it instead of the final `sorted(... )[:keep_count]`. Add these report values:

```python
"review_rejected_count": review_rejected,
"post_review_unique_chain_count": len(reviewed),
"selected_unique_edge_count": len(set().union(*(_chain_edges(item["chain"]) for item in selected))),
```

For an empty selection, report zero unique edges without calling `set.union` on an empty sequence.

- [ ] **Step 6: Update Step 2 documentation and run tests**

Document `objective` in `SampleChainsOutput`, describe explicit rejection and review errors, and describe the post-review rebalance metrics. Replace the module-level Step 2 specification so it no longer claims review failures preserve the original chain or that review returns only `chain/reason`.

Run:

```bash
python -m unittest tests.test_tool_graph_steps.ChainSampleTest
```

Expected: all `ChainSampleTest` tests pass.

- [ ] **Step 7: Commit Step 2**

```bash
git add task_gen/tool_graph/step_2_chain_sample.py task_gen/tool_graph/contracts.py tests/test_tool_graph_steps.py
git commit -m "feat: establish objectives during chain review"
```

---

### Task 2: Step 3 Executes the Frozen Objective

**Files:**
- Modify: `task_gen/tool_graph/step_3_chain_execute.py:1-584`
- Modify: `task_gen/tool_graph/contracts.py:177-252`
- Test: `tests/test_tool_graph_execution.py:1-420`

**Interfaces:**
- Consumes: each candidate with `task_id: str`, `chain: list[str]`, and non-empty `objective: str`.
- Produces: the existing `execution` structure; `_generate_arguments(..., objective: str) -> dict[str, Any]`; model output is exactly `{"arguments": {...}}` or `{"error": "..."}`.

- [ ] **Step 1: Change execution fixtures to provide objectives**

Add `"objective": "Write the requested value."` to every test candidate. Remove `is_intent_prompt` and make the inference helper return only argument responses:

```python
def inference(arguments: dict | None = None) -> InferenceResult:
    return InferenceResult(json.dumps({"arguments": arguments or {}}), {}, "test")
```

Replace the intent test with `test_uses_objective_without_generating_a_second_intent`. Capture prompts and assert there is exactly one LLM call per tool, every prompt contains the supplied objective, and the full chain remains present.

- [ ] **Step 2: Add failing semantic-error retry tests**

Add one test whose first parameter response is:

```json
{"error":"No observed object currently satisfies the objective."}
```

and whose second response supplies valid arguments. Assert the second prompt contains the first error and the tool executes once. Add an exhaustion case asserting `retry_count + 1` parameter calls, no tool call, `failure_kind == "llm"`, and no replacement objective.

- [ ] **Step 3: Run Step 3 tests and verify failures**

Run:

```bash
python -m unittest tests.test_tool_graph_execution.ExecuteChainsTest
```

Expected: failures because `objective` is not consumed, an extra intent call still occurs, and the error response is not accepted as a retryable shape.

- [ ] **Step 4: Remove temporary intent generation**

Validate the candidate objective during the existing preflight loop:

```python
objective = candidate.get("objective")
if not isinstance(objective, str) or not objective.strip():
    raise ValueError(f"tasks[{index}].objective 必须是非空字符串")
```

Delete `_intent_with_retry` and `_generate_intent`. In `_execute_candidate`, read `objective = candidate["objective"]` and pass it to `_arguments_with_retry`; do not make an LLM call before creating arguments for the first tool.

- [ ] **Step 5: Implement mutually exclusive argument/error output**

Replace the historical parameter prompt with the four responsibilities from the spec: honor the objective, use public/observed facts, create reasonable new content when needed, and report inability without changing the objective.

Parse responses as follows:

```python
if set(payload) == {"arguments"} and isinstance(payload["arguments"], dict):
    return payload["arguments"]
if set(payload) == {"error"} and isinstance(payload["error"], str) and payload["error"].strip():
    raise ValueError(payload["error"].strip())
raise ValueError("LLM 必须返回 arguments object 或非空 error")
```

The existing generic exception branch in `_arguments_with_retry` records this as an LLM failure and supplies it as `previous_failure` on the next parameter attempt. Keep Schema errors and transport/parse errors on the same retry path. Do not change whole-chain tool failure retries.

- [ ] **Step 6: Update Step 3 documentation and run tests**

Remove all `task_intent` documentation from `ExecuteChainsInput` and the Step 3 module specification. State that `objective`, full chain, current tool, bounded completed calls, remaining chain, and previous failure form the parameter context.

Run:

```bash
python -m unittest tests.test_tool_graph_execution.ExecuteChainsTest
```

Expected: all `ExecuteChainsTest` tests pass.

- [ ] **Step 7: Commit Step 3**

```bash
git add task_gen/tool_graph/step_3_chain_execute.py task_gen/tool_graph/contracts.py tests/test_tool_graph_execution.py
git commit -m "feat: execute chains against frozen objectives"
```

---

### Task 3: Step 4 Expresses the Objective with Bounded Reflection

**Files:**
- Modify: `task_gen/tool_graph/step_4_task_compose.py:1-324`
- Modify: `task_gen/tool_graph/contracts.py:257-308`
- Test: `tests/test_tool_graph_tasks.py:16-207`

**Interfaces:**
- Consumes: successful candidates with non-empty `objective`, `chain`, and real `execution.tool_calls`.
- Produces: unchanged `task_text`, `reference_answer`, `resource_constraints`, and `compose_error` fields; reflection still consumes/returns `analyze`, `need_revision`, and `task_text`.

- [ ] **Step 1: Add objective and per-round context assertions**

Add `"objective": "Update the requested business data."` to `successful_candidate`. In the composition success test assert:

```python
self.assertIn('"objective"', captured[0])
self.assertIn('"objective"', captured[1])
self.assertNotIn('"objective"', captured[2])
self.assertNotIn('"objective"', captured[3])
self.assertIn('"tool_calls"', captured[2])
self.assertNotIn('"tools"', captured[2])
self.assertIn('"resources"', captured[3])
self.assertNotIn('"tool_calls"', captured[3])
```

Keep the assertion that the internal tool body never appears.

- [ ] **Step 2: Replace the blocking-reflection test**

Rename it to `test_invalid_reflection_keeps_draft_and_continues`. Return an invalid reflection followed by a valid reference answer and resource constraints. Assert all four rounds run, the original draft is retained, downstream fields are populated, and `compose_error is None`.

Add a batch-reflection exception case with the same expected continuation. Keep the existing revision test to prove a valid `need_revision=true` response is adopted before later rounds.

- [ ] **Step 3: Run Step 4 tests and verify failures**

Run:

```bash
python -m unittest tests.test_tool_graph_tasks.ComposeTasksTest
```

Expected: failures because objective is absent from contexts, all rounds share excess context, and reflection failures currently stop composition.

- [ ] **Step 4: Build only the context needed by each Step 4 round**

Keep one internal base context per candidate, but make `_build_prompt` construct data by kind:

```python
if kind == "task_text":
    data = {"objective": ..., "environment": ..., "tools": ..., "chain": ..., "tool_calls": ...}
elif kind == "task_reflection":
    data = {"objective": ..., "task_text": ..., "chain": ..., "tool_calls": ...}
elif kind == "reference_answer":
    data = {"task_text": ..., "tool_calls": ...}
else:
    data = {"task_text": ..., "resources": ...}
```

Reject a missing/blank objective before adding the candidate to active composition. Do not add new context types or helper classes.

- [ ] **Step 5: Replace historical prompt rule lists with first principles**

The task draft prompt should state only: translate the fixed objective and successful trace into a natural result-oriented user request; include necessary pre-execution business information; preserve semantics and fact boundaries; return an error if the trace did not achieve the objective.

The reflection prompt should ask `analyze` to examine natural result orientation, necessary business information, preservation of objective/facts, and implementation-detail leakage. Keep the exact three-field response and limit revision to expression changes.

Reference-answer and resource-constraint prompts retain their existing output contracts while using only their reduced context.

- [ ] **Step 6: Make reflection best-effort and run tests**

On any whole-batch reflection failure, return the original `active` list unchanged. For per-candidate exceptions or invalid payloads, leave its draft untouched and keep its index active. Apply valid revisions in place. Never set `compose_error` from reflection.

Run:

```bash
python -m unittest tests.test_tool_graph_tasks.ComposeTasksTest
```

Expected: all `ComposeTasksTest` tests pass.

- [ ] **Step 7: Update Step 4 documentation and commit**

Document objective use, minimal per-round context, and non-blocking reflection in both the contract and the module-level specification. Remove the old claim that every round failure blocks later rounds.

```bash
git add task_gen/tool_graph/step_4_task_compose.py task_gen/tool_graph/contracts.py tests/test_tool_graph_tasks.py
git commit -m "feat: compose tasks from objectives and real traces"
```

---

### Task 4: Step 5 Checks Objective, Task, and Execution Independently

**Files:**
- Modify: `task_gen/tool_graph/step_5_task_validate.py:1-354`
- Modify: `task_gen/tool_graph/contracts.py:311-376`
- Test: `tests/test_tool_graph_tasks.py:209-334`

**Interfaces:**
- Consumes: candidate `objective`, final `task_text`, chain, and successful execution calls/results.
- Produces: unchanged final `task` plus validation fields `execution_matches_objective: bool`, `task_matches_objective: bool`, `task_is_usable: bool`, and `errors: list[str]`.

- [ ] **Step 1: Rewrite Step 5 success and failure fixtures**

Use this exact semantic response shape:

```python
{
    "execution_matches_objective": True,
    "task_matches_objective": True,
    "task_is_usable": True,
    "errors": [],
}
```

Assert the prompt contains `objective`, `task_text`, chain, and real results. Assert the assembled final `task` does not contain `objective`.

Replace the semantic mismatch test with separate cases for execution/objective mismatch and task/objective mismatch. Keep one invalid-shape test using a non-boolean value. Add a missing-objective test that rejects locally without calling the LLM.

- [ ] **Step 2: Run Step 5 tests and verify failures**

Run:

```bash
python -m unittest tests.test_tool_graph_tasks.ValidateTasksTest
```

Expected: failures because validation still uses the old two-field semantic contract and `_basic_errors` does not require `objective`.

- [ ] **Step 3: Implement the three independent semantic checks**

Initialize validation with all three booleans false. Update them from `_parse_review`, and pass only when all three are true and `errors` is empty:

```python
validation["passed"] = (
    review["execution_matches_objective"]
    and review["task_matches_objective"]
    and review["task_is_usable"]
    and not validation["errors"]
)
```

Require a non-empty candidate objective in `_basic_errors`. Keep existing execution, chain order, Step 4 fields, minimum chain length, and Schema checks unchanged. Do not add keyword lists for tool names, IDs, paths, or resources.

- [ ] **Step 4: Replace the Step 5 review prompt**

Ask the LLM only whether: real calls/results achieved the objective; final task faithfully instantiated the objective; final task is natural, result-oriented, and supplies necessary business information. State that these judgments are independent and that Step 5 does not repair data.

Declare all context text to be data. Require the exact three-boolean-plus-errors JSON shape.

- [ ] **Step 5: Update Step 5 documentation and run tests**

Update `ValidateTasksOutput` field names and description. Replace the Step 5 module specification's old two-boolean and lexical-leakage descriptions with the implemented three-way semantic check. Keep objective only on the outer pipeline candidate.

Run:

```bash
python -m unittest tests.test_tool_graph_tasks.ValidateTasksTest
```

Expected: all `ValidateTasksTest` tests pass.

- [ ] **Step 6: Commit Step 5**

```bash
git add task_gen/tool_graph/step_5_task_validate.py task_gen/tool_graph/contracts.py tests/test_tool_graph_tasks.py
git commit -m "feat: validate objective task and execution consistency"
```

---

### Task 5: Documentation and End-to-End Regression

**Files:**
- Modify: `task_gen/tool_graph/README.md:245-350`
- Verify: `task_gen/tool_graph/pipeline.py`
- Verify: `task_gen/tool_graph/run_io.py`
- Test: `tests/test_tool_graph_steps.py`
- Test: `tests/test_tool_graph_execution.py`
- Test: `tests/test_tool_graph_tasks.py`

**Interfaces:**
- Consumes: the completed Step 2-5 implementation.
- Produces: repository documentation matching the implemented candidate flow; no new runtime interface.

- [ ] **Step 1: Update the README data flow**

Document that Step 2 review returns accepted/rejected, revised chain, and objective; Step 2 rebalances revised chains within logic-score priority. Document that Step 3 directly consumes objective and has no intent-generation round. Document Step 4 minimal contexts and best-effort reflection. Document Step 5's three semantic booleans.

Remove the old claims that review failures fall back, every Step 4 round failure blocks, and task text is mechanically rejected whenever it contains a tool name or resource ID.

- [ ] **Step 2: Verify Bundle transport requires no code change**

Inspect `run_io.to_execute_chains_input`, `to_compose_tasks_input`, and `to_validate_tasks_input`. Confirm each passes the full candidate list and `merge_output` continues to permit only `tasks` replacement. Do not modify `run_io.py` or `pipeline.py` unless this inspection demonstrates a concrete transport failure.

- [ ] **Step 3: Run the focused regression suite**

Run:

```bash
python -m unittest \
  tests.test_tool_graph_steps \
  tests.test_tool_graph_execution \
  tests.test_tool_graph_tasks \
  tests.test_tool_graph_run_io \
  tests.test_tool_graph_integration
```

Expected: all tests pass.

- [ ] **Step 4: Run all Tool Graph tests discoverable under Python 3.10**

Run:

```bash
python -m unittest discover -s tests -p 'test_tool_graph_*.py'
```

Expected: all discovered Tool Graph tests pass. If an unrelated environment/import failure prevents collection, record the exact error and still retain the focused suite result.

- [ ] **Step 5: Check scope and formatting**

Run:

```bash
git diff --check
git status --short
git diff --stat a7fab11..HEAD
rg -n 'task_intent|chain_matches_task|task_has_required_information' task_gen/tool_graph tests/test_tool_graph_*.py
```

Expected: no whitespace errors; only intended Step 2-5, contracts, tests, README, spec, and plan changes; the final search returns no live old-contract references.

- [ ] **Step 6: Commit documentation**

```bash
git add task_gen/tool_graph/README.md
git commit -m "docs: describe objective-guided task pipeline"
```
