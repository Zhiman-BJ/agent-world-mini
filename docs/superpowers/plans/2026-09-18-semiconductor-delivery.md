# Semiconductor delivery implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the independent upstream preparation; controller implements the tightly coupled pipeline/runtime adaptation and runs end-to-end verification.

**Goal:** Correct an isolated delivery copy, adapt existing execution paths, and produce real new-environment tasks with agent and verifier results.

**Architecture:** Reuse `load_delivery` and the shared sandbox. Resolve delivery state/software once into internal stage metadata; never expose local runtime paths to task prompts. Mount a compatible Python runtime without borrowing incompatible host packages.

**Tech Stack:** Python, existing JSON Schema/MCP/bubblewrap, existing Codex execution stage and Kimi evaluation.

**Spec:** `task_gen/SEMICONDUCTOR_DELIVERY_AUDIT_20260918.md`, approved by the user with authority to implement and continue through real results.

## Global Constraints

- Work on existing `kimi` worktree; preserve original upstream delivery and unrelated `KIMI_WALKTHROUGH_ZH.md`.
- Preserve graph, objective, execution, task-quality and verifier semantics. No lowering quality gates.
- Preserve sandboxing, per-task initial state, write permissions and failure rollback.
- Use existing pipeline-only credentials without printing secrets or altering conversation credentials.
- Finish only when selected new environments have complete task, independent agent and verifier artifacts; document failures honestly.

### Task 1: Prepare corrected upstream copy and delivery requirements

Own only `scripts/prepare_semiconductor_delivery.py`, `task_gen/UPSTREAM_DELIVERY_REQUIREMENTS.md`, its focused test if needed, and generated ignored `artifacts/semiconductor_delivery_20260918/`. Do not edit pipeline/runtime files.

Input: `/data1/agent_world/toolgen_semiconductor_0917_rich/delivery` and corresponding generation roots. Output: a separate working delivery with corrected interpreter mappings and a machine-readable change manifest. Preserve tool definitions, implementation code and state content. Avoid hardlinking mutable files to the original. Runtime base dependencies may remain declared external dependencies on this host; do not claim the copy is portable.

- [x] Inspect actual source software environments and each published profile. Check disk capacity before copying; use reflink/copy rather than hardlinks.
- [x] Record a failing import probe for an incorrectly mapped profile, e.g. mapped Python `-I -c 'import numpy'`.
- [x] Implement an explicit preparation script with source/destination arguments and fail on existing destination, using original mappings plus verified source metadata to select venv launchers. No silent guessing at execution time.
- [x] Run preparation; verify all eight via `load_delivery` and actual imports, including `jsonschema` for state runtime. Report missing packages to controller, do not install into original profiles.
- [x] Document expected layout, interpreter/venv semantics, dependencies and deployment contract, schema/receipt consistency, initial-state integrity, tool-level checks, and actual local corrections.
- [x] Commit only owned source/docs/tests. Write detailed report to `.superpowers/sdd/2026-09-18-semiconductor-delivery/task-1-report.md` with commands, checks, corrected root and concerns. Do not spawn agents.

### Task 2: Adapt pipeline and shared sandbox

- [x] Add regression tests reproducing delivery-root loading, per-stage runtime propagation and real copied-venv sandbox startup; observe failures.
- [x] Step 0 reuse binding loader and produce private runtime metadata alongside environment. Run IO sends it only to execution consumers. Keep legacy paths compatible.
- [x] Shared executor mounts configured launcher/base/site-packages compatibly, avoids foreign host site-packages, retains existing limits and isolation.
- [x] Execution/review and final evaluation use the resolved software; no full binding equality checks on a readonly tool subset. Existing formal evaluation checks remain.
- [x] Run focused existing execution/state/MCP/pipeline tests and representative real tool calls across all eight; fix clear implementation defects and commit.

### Task 3: Real end-to-end verification and report

- [x] Select at least two strong environments based on usable tools, data and successful probes, not solely few tools. Save run configs and rationale.
- [x] Run ordinary pipeline from Step 0 through Step 5 and verifier generation. Use dedicated credentials, preserve complete logs. Run independent Kimi agent on resulting tasks and existing verifiers.
- [x] Diagnose failures at responsible boundary; fix deterministic implementation errors without changing prompt semantics or weakening checks. Retry relevant stages without hiding earlier artifacts.
- [x] Save final task text, source run paths, agent/verifier outcomes, durations and usage, limitations and upstream guidance; review changes and commit. Do not merge or push.

## Progress and decisions

- Baseline: `95fe49b`. Existing audit report is uncommitted; preserve it in a documentation checkpoint before implementation.
- User approved the audit's adaptation scope; no additional design approval is needed.
- Runtime/data copies and private metadata are implementation choices within that scope. Original upstream and main remain untouched.
- Completed: calibration, eigenmode, cloud have five final tasks and independent verdicts (three passes after calibration answer recovery, two failures); generic FEM completed all generation stages but produced zero accepted tasks. Evidence and remaining upstream defects: `task_gen/SEMICONDUCTOR_E2E_RESULTS_20260918.md`.
