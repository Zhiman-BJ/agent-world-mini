# Harness Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the 69-task pipeline consume only the new ToolGen `binding.json` delivery and execute every environment through the portable Harness contract.

**Architecture:** Vendor the upstream Harness and delivery/runtime modules into the repository using package-relative imports. Step 0 will require a binding and carry only private runtime metadata; TaskGen/MCP calls will use the shared Harness execution boundary, which selects the backend from `runtime.json`. Legacy `environment.json + workspace` loading is removed.

**Tech Stack:** Python 3.10+, stdlib, jsonschema, bubblewrap, optional Docker, existing MCP JSON-RPC server.

**Spec:** `ToolGen下游对接说明.md`, `TOOL_GEN 适配.md`, `HARNESS阶段接入说明.md`.

## Global Constraints

- No absolute machine paths in source code, task data, prompts, or portable run metadata.
- `binding.json` is the only environment entry point.
- Preserve input/output Schema validation, state transactions, resource boundaries, and failure classification.
- Do not expose execution-state, raw evidence, verifier, or other sessions through native model file tools.
- Do not change graph, sampling, objective, task-text, or task-summary behavior.

### Task 1: Vendor the upstream delivery and Harness modules

**Files:**
- Create/modify: `harness/`, `env_gen/tool_gen/{delivery_contract,runtime,resources,runtime_launch,docker_runtime,mcp_server}.py`
- Create/modify: `schemas/toolgen_runtime.schema.json`, related delivery schemas
- Test: existing delivery/runtime tests plus new import/contract checks

- [ ] Copy the upstream implementation without embedding its absolute source path.
- [ ] Run import and binding validation against one expanded delivery.
- [ ] Commit the vendored boundary.

### Task 2: Require binding-only Step 0 loading

**Files:**
- Modify: `task_gen/tool_graph/step_0_environment_load.py`
- Modify: `task_gen/tool_graph/contracts.py`, `run_io.py` only if runtime shape requires it
- Test: `tests/test_mcp_integration.py`

- [ ] Remove the legacy `environment.json + workspace` fallback.
- [ ] Use `harness.delivery.load_delivery` and preserve `runtime`, `software`, and initial state as private fields.
- [ ] Reject missing or invalid bindings with actionable errors.
- [ ] Verify runtime fields never enter public model inputs.

### Task 3: Route task-level MCP calls through Harness

**Files:**
- Modify: `task_gen/task_eval_mcp.py`
- Modify: `task_gen/tool_graph/step_3_chain_execute.py` only at the shared execution boundary
- Test: `tests/test_task_eval.py`, `tests/test_mcp_integration.py`, execution tests

- [ ] Replace direct private execution from the task MCP adapter with `harness.execution.call_environment_tool`.
- [ ] Preserve per-task state copy, commit-on-success, rollback-on-failure, Schema checks, and resource normalization.
- [ ] Pass the binding-selected runtime without exposing host paths to the model.

### Task 4: Add resource protocol and portable runtime failure handling

**Files:**
- Modify: `task_gen/task_eval_mcp.py`, `task_gen/task_eval_kimi_mcp.py`
- Modify: `task_gen/tool_graph/execution_agent.py` only for private runtime propagation
- Test: resource, backend, rollback, and infrastructure classification tests

- [ ] Expose `resources/list` and `resources/read` as protocol methods, not business tools.
- [ ] Keep business file arguments as Scope-relative paths and validate declared scopes.
- [ ] Preserve distinct `invalid_arguments`, `business failure`, `output schema`, `timeout`, and infrastructure failures.
- [ ] Record infrastructure failures once and do not retry them as task-quality failures.

### Task 5: End-to-end acceptance

**Files:**
- Test: one `python_profile` expanded environment and any available Docker environment
- Modify: no pipeline behavior unless an acceptance failure identifies a defect

- [ ] Load a real binding and call one read-only tool.
- [ ] Call one mutating tool and verify state commit and failure rollback.
- [ ] Verify runtime backend selection and portable run metadata.
- [ ] Run the focused test suite and one complete pipeline smoke test.

