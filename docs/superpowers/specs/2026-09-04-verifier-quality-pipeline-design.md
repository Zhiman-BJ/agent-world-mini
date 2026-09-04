# Verifier Quality Pipeline Design

## Goal

Generate task-specific verifiers that judge whether an Agent satisfied the task text, without
treating the reference execution as the only valid solution. A verifier may be frozen only when
its specification is complete, its implementation faithfully realizes that specification, and
calibration demonstrates that it distinguishes sufficient from insufficient evidence.

## Authority And Evidence

The task text is the sole authority for required outcomes. The environment contract defines what
can be observed and changed. Reference calls, answer, and final state are one known execution and
calibration fixture; they do not define additional requirements, required tools, call order,
internal identifiers, storage representation, or wording.

The verifier may inspect only three evidence classes from the actual run: tool calls and their
results, initial/final workspace state, and the Agent's final answer. Persistent outcomes require
state evidence. Query, computation, and presentation outcomes may use tool results and the final
answer. Missing or conflicting evidence produces `indeterminate`; contradictory evidence produces
`fail`.

## Staged Construction

### 1. Verification specification

The first model call receives the task text and public environment contract, but no reference
execution. It produces a specification containing atomic required outcomes. Each requirement has:

- a unique stable ID and one independently decidable claim;
- the exact task-text clauses it covers;
- admissible evidence channels;
- sufficient pass evidence, decisive failure evidence, and indeterminate conditions.

Together, the requirements must cover every independently testable task clause exactly once.
They must not prescribe implementation details absent from the task. One additional execution
integrity requirement checks that observed side effects are necessary for the task and do not
damage unrelated state.

### 2. Specification review

A separate model call receives the task, environment, and proposed specification. It returns a
structured review of:

- coverage: every task clause maps to a requirement;
- atomicity: independently falsifiable outcomes are not hidden inside one requirement;
- fidelity: no requirement adds a tool, order, identifier, representation, format, or constraint;
- verifiability: each pass/fail condition can be supported by an admitted evidence channel;
- classification: persistence, query, computation, presentation, and preservation are treated
  consistently with their evidence needs.

Any review issue rejects the specification. The next attempt receives only the structured issue
list, not the rejected specification, to avoid anchoring on it.

### 3. Verifier implementation

Only after the specification passes review does a model receive it together with the reference
evidence and runtime API. It generates Python `verify(ctx)` code implementing the frozen
requirements. It may not add, remove, merge, split, or reinterpret them.

### 4. Implementation review

A separate model call reviews the frozen specification and generated source before execution. It
checks requirement-to-code coverage, exactly-one-result control flow, evidence sufficiency,
accidental reference hard-coding, and paths that could pass without proving the claim. Static
schema and AST validation remain mandatory and run before this semantic review.

## Calibration

A verifier must pass all applicable checks before it is frozen:

1. **Reference:** the known-good reference execution passes every required outcome.
2. **Representation counterfactual:** incidental generated IDs and representation details may
   change without changing the business outcome; the verifier must still pass.
3. **Empty execution:** no-op evidence must not pass the task, except individual preservation
   requirements that are directly proven by equal initial and final state.
4. **Evidence ablation:** for each evidence channel used by a non-preservation requirement, remove
   that channel's supporting evidence. The affected requirement must not remain `pass` unless the
   remaining cited evidence independently proves the same claim.

Calibration verifies evidence dependence, not every possible wrong execution. It must never mutate
the reference state or lower a task requirement to make calibration pass.

## Conflict Classification

Failures are classified before retrying:

- `verifier_generation_error`: malformed specification or source, incomplete implementation, or
  failed calibration caused by the verifier candidate. Retry with structured issues.
- `task_reference_conflict`: the task text and reference evidence make at least one required
  outcome contradictory or unprovable even when inspected independently of candidate code. Stop
  before Agent execution and preserve the conflicting requirement and evidence.

A verifier candidate disagreeing with the reference is not by itself proof of a task-reference
conflict. The conflict decision must be made by a separate structured assessment grounded in the
task clause and reference evidence.

## Compatibility And Scope

The frozen verifier package remains `schema_version`, `requirements`, and `source`, so execution,
caching, and result aggregation remain compatible. Preparation diagnostics may add specification,
review, implementation-review, ablation, and conflict records.

This change is confined to `task_gen/task_eval_verifier.py` and its tests. It does not modify task
generation, contracts, pipeline stages, Agent execution, MCP behavior, or the definition of an
overall pass: every required outcome must pass.

## Acceptance

- Unit tests demonstrate rejection of incomplete and over-constrained specifications.
- Unit tests demonstrate rejection of source that does not faithfully implement the frozen spec.
- Unit tests demonstrate that evidence ablation catches an unsupported pass.
- Unit tests distinguish verifier generation failure from a task-reference conflict.
- Existing verifier execution and aggregation tests remain green.
- One complete Terra run in a non-FinStat environment produces reviewable preparation artifacts,
  executes the task, and reaches a justified final outcome without relaxing any requirement.
