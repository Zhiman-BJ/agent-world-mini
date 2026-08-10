# Tool Design and Filtering Specification

## Decision

Do not ask an LLM to freely invent Python tools from a theme and then trust its
descriptions.  A reliable environment needs one source of truth: a typed,
resettable state model.  A tool is accepted only when its implementation,
schema, description, tests, observed effects, and graph edges all agree with
that state model.

The implementation order for this project is:

```text
mined snapshot -> state contract -> operation intent -> executable tool
-> independent tests -> filtering -> data-flow graph -> executable chain
-> task wording
```

Natural-language tool descriptions and task wording are intentionally late
artifacts.  They describe code that has already run; they must never be the
authority for what the code is meant to do.

## Problems Found in the Initial Prototype

The initial `ToolDesigner` kept five fixed names and only allowed the LLM to
replace their descriptions. `LocalToolRuntime` contained the corresponding
fixed implementations. This section records the problems that motivated the
current data-driven tool generator and shared runtime.

The practical failures are:

1. `list_operational_entities`, `filter_entities`, and `compare_entities` are
   generic data-browser operators, not domain operations derived from the
   researched business state.
2. The tool schema is not compiled into runtime behavior.  A generated input
   or output field can be documented while the runtime ignores it.
3. `reads`, `produces`, and `requires_tools` are coarse strings.  They do not
   prove that a particular output field satisfies a particular later input.
4. There are no tool-level generated test cases, no independent test agent,
   and no state-transition or reset tests.
5. The runtime is read-only.  This is valid for a purely public lookup world,
   but it must not be presented as a stateful business environment.  A command
   must be supported by a modeled local state and a documented workflow, not
   invented solely to make a longer chain.
6. The existing project tests only check graph/task shape.  They do not test
   that a tool contract, its code, and its claimed output are consistent.

## What the Literature Actually Requires

Agent-World generates a candidate Python function together with a one-to-many
set of unit tests, then retains it only if it compiles and has test accuracy
above 0.5.  It does not claim that a tool description alone establishes tool
quality.  [Agent-World](https://arxiv.org/abs/2604.18292)

EnvFactory makes the coupling explicit: metadata contains tools and schemas;
the database model supplies entities, relations, and mutable state; code
implements the tool interface; a separate Test Agent checks metadata-interface
agreement, import/execution, expected behavior, and database-state transition,
then returns error reports for revision.  [EnvFactory](https://arxiv.org/abs/2605.18703)

AWM provides a complementary constraint: it constructs database tables only
when required by its tasks, uses a SQLite-backed state, exposes Python tools,
and verifies execution.  Its scale should not be copied before correctness:
its own analysis still reports implementation bugs.  [AWM](https://arxiv.org/abs/2602.10090)

Mature evaluation environments reinforce the same point.  AppWorld evaluates
the resulting state and unintended collateral changes; ToolSandbox exercises
implicit state dependency; tau-bench compares the terminal database state,
not a preferred tool-call string.  [AppWorld](https://arxiv.org/abs/2407.18901),
[ToolSandbox](https://arxiv.org/abs/2408.04682), and
[$tau$-bench](https://arxiv.org/abs/2406.12045).

## Target Artifacts

### 1. State contract

The Research Agent must emit a `StateContract` before tool design.  It is a
machine-readable summary of the local snapshot, not a prose database summary.

```text
entities: entity name, primary key, typed fields, source provenance
relations: source entity.field -> target entity.primary key, cardinality
indexes: fields that can actually be searched, sorted, grouped, or joined
invariants: uniqueness, allowed values, numeric ranges, referential integrity
state_classes: immutable_source | local_overlay | derived_view
workflow_evidence: source-backed business actions and their allowed transitions
```

`immutable_source` is the mined factual snapshot.  `local_overlay` is optional
session state such as a draft, booking request, or issue status.  It must be
kept separate from the source facts, resettable per rollout, and introduced
only when public documentation supports the underlying workflow.  This avoids
silently rewriting real web data or inventing unsupported actions.

### 2. Operation intent, before code

The LLM may propose operations only as a constrained `ToolIntent` object.  It
must cite the state fields and workflow evidence on which it relies.

```json
{
  "name": "search_bicycle_stations",
  "kind": "query",
  "target_entity": "station",
  "inputs": [{"name": "query", "type": "string", "source": "user"}],
  "outputs": [{"name": "stations", "type": "StationSummary[]"}],
  "read_paths": ["station.name", "station.city"],
  "write_paths": [],
  "preconditions": [],
  "effects": [],
  "evidence_ids": ["source-17"],
  "rationale": "Supports station discovery using fields present in the snapshot."
}
```

Allowed `kind` values in the first implementation are:

| Kind | Purpose | Admissibility rule |
| --- | --- | --- |
| `query` | Search/filter a real entity collection | At least one indexed or searchable field has data |
| `lookup` | Retrieve a record by canonical ID | Primary key and record type exist |
| `relation` | Traverse an explicit entity relation | The relation is materialized and referentially valid |
| `aggregate` | Count, group, rank, or summarize | Every field and aggregation is type-valid and has meaningful variation |
| `create` | Create an overlay record | An overlay schema and documented workflow exist |
| `transition` | Change local-overlay state | A permitted transition and its precondition are explicit |
| `delete` | Remove/cancel an overlay record | Ownership, transition, and reset semantics are explicit |

No `compare` or `recommend` intent is admitted merely because the words sound
business-like.  A comparison requires a shared typed field; a recommendation
requires an explicit deterministic criterion supplied by the caller or a
documented policy.  Otherwise it is an unverifiable LLM judgment disguised as
a tool.

### 3. Compile the intent, do not interpret it with an LLM at runtime

For the mini reproduction, use one shared trusted compiler from `ToolIntent`
to Python functions over a resettable typed in-memory store. The compiler, not
the LLM, implements search, relation traversal, aggregates, and overlay
transitions. This gives identical semantics on every rollout and avoids writing
a separate service implementation for every theme.

An LLM-generated Python implementation can be allowed later, matching
Agent-World more closely, but only in a sandbox and only after the same static
binding and tests below.  Starting with a constrained operation DSL reduces
the failure surface while still giving each researched theme a different,
data-grounded toolset.

### 4. Derive the agent-facing contract from code

After compilation, emit a `ToolSpec` with:

```text
name, description, kind
input JSON Schema, output JSON Schema
input sources: user | constant | tool.output path
reads, writes, preconditions, effects, error contract
determinism and pagination/sort policy
provenance/evidence IDs
implementation ID and test IDs
```

Generate the description from this schema and test it for completeness.  It
should state what the tool does, required input, returned values, and any
state change.  It must not reveal a hidden task answer or claim capabilities
not present in the intent.

## Candidate Generation Protocol

1. Infer and validate `StateContract` from the normalized research bundle.
   Reject malformed records and mark every relation as verified or absent.
2. Give the LLM only the contract, source excerpts, and the permitted intent
   vocabulary.  Ask for a modest candidate set, for example 3--12 tools for a
   small environment, rather than a fixed generic list.
3. Statically bind every claimed table, field, relation, transition, and
   evidence ID.  Any unbound reference is rejected before code is produced.
4. Compile accepted intents into executable functions and derive schemas.
5. Have an independent test-generation pass create tests from the contract and
   concrete fixture IDs.  It must not reuse the candidate tool's expected
   output without recomputing it from the database.
6. Execute tests on a clean snapshot, report failures in a structured form,
   revise the intent/code, and repeat within a fixed budget.
7. Only after tools pass do we construct the tool graph and sample chains.

The LLM has useful roles in steps 2 and 5: recognizing domain semantics and
proposing coverage.  It should not be the sole executor, compiler, oracle, and
judge of the same tool.

## Tool Filtering Protocol

All gates below must pass.  A numerical score cannot compensate for a failed
correctness gate.

| Gate | Test | Reject when |
| --- | --- | --- |
| Static grounding | All entities, fields, relations, evidence, reads/writes, and types bind to `StateContract` | Any name, field, or business rule is invented |
| Compilability | Generated module imports and tool registers | Import, schema, or registration fails |
| Contract fidelity | Valid calls match output schema; invalid calls return declared errors | Runtime accepts/returns an undocumented shape |
| Behavioral tests | Typical, empty, boundary, invalid-input, and not-found cases | Any deterministic expected result differs |
| Invariants | Read calls do not mutate state; write calls preserve foreign keys, allowed transitions, and idempotency rules | State violates an invariant or cannot reset |
| Data usefulness | Query results are non-degenerate; aggregations have valid typed fields and at least two meaningful values | Tool always returns the same trivial value or empty data |
| Graph usefulness | At least one input/output path is type-compatible and a chain can execute | Tool is unreachable, dead, or has an unsatisfied required input |
| Redundancy | Canonical signature and behavior are compared with retained tools | It is an alias with no new operation, scope, or effect |
| Integration | A sampled chain uses real outputs to satisfy downstream inputs on a fresh state | Chain works only with hand-injected hidden arguments |

For a small demo, require 100% passing deterministic tests, not Agent-World's
`Acc > 0.5` admission threshold.  The latter is a pragmatic large-scale filter;
at this scale a tool that fails one known deterministic test is cheap to fix
and should not be retained.

The environment itself is retained only if it has at least one valid tool and
one valid test, as in Agent-World, but we should normally require at least one
nontrivial executable chain as well.  A read-only environment may qualify; it
does not need a fabricated mutation just to appear harder.

## Graph Construction Must Use Typed Data Flow

Replace the current artifact-name-only graph with a bipartite graph:

```text
Tool -> OutputField/StateEffect -> InputField/Precondition -> Tool
```

Each edge needs one of these reasons:

- `strong`: an output field exactly provides a required downstream input;
- `weak`: a relation or lookup can derive the needed value, but another source
  is possible;
- `independent`: a tool has only user/constant inputs and is a valid new start.

An input must declare its origin.  A graph walk is executable only if every
required input is either a user-providable value, a typed prior output, or a
safe constant.  This avoids the present situation where a chain looks valid
because two tools mention `operational_entities`, although the second call is
not actually parameterized by the first result.

Semantic matching and LLM refinement may propose `weak` edges, as EnvFactory
does, but the runtime must prove every `strong` edge by executing a concrete
binding.  Do not use a random walk as evidence of dependency correctness.

## Tests to Add Before More Experiments

1. `test_intent_rejects_unknown_field`: an LLM may not filter or sort on a
   field absent from the state contract.
2. `test_tool_schema_matches_runtime`: every valid sample call conforms to the
   advertised output schema; every invalid sample yields the advertised error.
3. `test_read_tool_preserves_snapshot`: a query/lookup cannot change state.
4. `test_transition_respects_preconditions_and_reset`: permitted overlay state
   changes work, invalid transitions fail, and reset restores the snapshot.
5. `test_graph_edge_has_executable_binding`: each strong edge has a concrete
   upstream value that the downstream tool accepts.
6. `test_generated_chain_runs_without_hidden_values`: instantiate all declared
   user inputs, then execute the full chain without oracle-only parameters.
7. `test_duplicate_tool_is_pruned`: semantically identical tools are not both
   exposed.

## Concrete Implementation Sequence

1. Add `StateContract`, `ToolIntent`, `ToolTestCase`, and typed input/output
   bindings to `models.py`.
2. Replace the fixed fallback in `tools.py` with configuration generation,
   static binding, and a constrained compiler. Keep generic operators
   internally; expose domain-named tools only when the data supports them.
3. Replace the name-switch runtime with a state backend and a registry of
   compiled tools.  Add snapshot/reset support.
4. Add the filter/test/revision loop and persist a `tool_validation.json`
   artifact containing every candidate, rejection reason, test trace, and
   retained contract.
5. Rebuild `graph.py` around typed parameter bindings and execute each sampled
   chain before task synthesis.
6. Keep the existing task reference execution and 5-run test, but run them
   only after this tool-level validation has passed.

This sequence deliberately improves validity before seeking longer chains or
more tasks.  For the current bicycle-sharing data, it may yield only a small
read-oriented set such as search station, fetch station, list a proven related
record, and compute an evidenced aggregate.  That is the correct outcome if
the mined data does not support a real transaction workflow.
