# Step3 input optimization experiment

Goal: best observed cost/quality tradeoff, not claim a universal optimum. No production restart or main edits.

Baseline: 514331a plus copied current dirty Step3 runtime/repair fixes; preserve hashes via commit. Historical flat baseline: execution_prompt and selection instructions from 899e49d (same old prompt as 47c205d); single-session historical architecture is a disclosed confound, minimum20 vs current minimum10 also disclosed. Use modern sandbox/runtime only to run new-schema environments.

Quality: complete goal, real parameter/object semantics, final artifact content/version correct, natural user objective with necessary conditions, no hidden identifiers in user text. Program accept is not human quality pass. Partial scientific result must not silently replace required artifacts. Do not loosen for acceptance.

Scope ledger:
- Static reference: remove duplicated description/input schema; retain usageConditions, output contract semantics and full contract lookup if needed.
- Dynamic evidence: full immutable result, bounded marked preview, JSON-path paging; no rerunning writes; no cross-phase fact leakage; evidence reads excluded from business length.
- Handoff: incremental records plus accessible old evidence; not optimistic reliance on context memory.
- Control: precise failed predicates, shared remaining-time check, bounded repairs, budget awareness. No field-only objective change triggering blind restart.
- Keep current structured quality prompts; no example-specific patches.

Plan (execute inline, no external messaging):
1. Freeze baseline; run existing focused tests. Retrieve old function byte-for-byte and record hash.
2. Reuse original Step2 candidate and initial state for DOS 01_06_01/task6 (historical15M). Run current and thin in parallel, then old-flat. sol/low, seed42, toolcalls60, session600s, rounds2, repairs2, retries1, tooltimeout120s, memory4GiB, concurrent2. Shared external deadline1500s per experiment; usage stop dispatch threshold12M total input or300k output, review in-flight rather than abandon results. Threshold is a dispatch gate, not a guaranteed bill cap.
3. Review actual quality; implement/result-preview only if evidence supports need. Unit-check paging losslessness, reference isolation, error/truncation metadata and business counting before online trial.
4. Second sample from a different historical high-cost family, preferably layout03_11_03/task5, at same budgets. Compare baseline vs best candidate; include old-flat if first result informative and budget allows. If budgets cause only incomplete results, use a disclosed additional small executor-level fixed-objective pair instead of claiming success.
5. Count per session cumulativeusage once. Separate cached/uncached/output, wall time, calls, initial bytes, evidence retransmission, read-more usage, no-progress. Review scientific and artifact correctness manually; make no unsupported token/byte equivalence.
6. Final report: concern→change→measured effect→quality→limits. Save configs/prompts/traces locally; commit code and reports in experiment branch, not main. Mark goal complete only after required trials and report.

Do not spend tokens retesting deterministic staticbyte comparisons already available; use prior analysis as design input. Cases may select different goals despite seed; report route differences and avoid causal exaggeration.
