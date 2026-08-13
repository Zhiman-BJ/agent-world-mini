# Pipeline Experiments

## Latest MCP catalogue batch

The latest end-to-end batch used environment descriptions from the prepared
local MCP catalogue, mined public records, regenerated tools from those
records, executed graph walks, and submitted the successful walks for semantic
task review.

| Environment | Public records | Retained tools | Executed walks | Tasks | Average task steps |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hugging Face | 11 | 4 | 16 | 16 | 2.31 |
| Medical Terminologies (RxNorm subset) | 14 | 4 | 16 | 14 | 2.00 |
| Context7 | 5 | 4 | 11 | 10 | 2.30 |

The batch produced 40 executable tasks from 43 executed walks. This is pipeline
evidence, not a claim that all 40 tasks are publication-quality. Hugging Face
had the strongest data coverage. The RxNorm subset repeats a small number of
drug concepts, and Context7 lacks documentation bodies, so both environments
produce several near-duplicate intents.

## Current Hugging Face graph experiment

The rebuilt Hugging Face snapshot contains 175 public JSON records across
models, datasets, Spaces, files, domains, and three real link tables. Those
records compile to 22 tested tools. Link tables are internal state and compile
to direct traversal tools such as model-to-files and Space-to-domains.

An ablation exposed the short-chain failure directly. Letting one LLM call
prune all candidate weak edges removed all 43 weak edges, leaving only 14
strong edges. The resulting 46 tasks averaged 1.72 steps and 45 of them had
only one or two steps. The graph now keeps schema-backed strong and weak data
flows and leaves semantic pruning to the post-execution task reviewer.

The old adaptive report summed per-batch deduplication and described 41 chains
as distinct. A global diagnostic corrected that number to 38. Global execution
deduplication is now shared across batches.

The fixed topology sampler no longer draws a target length from 1--8. It checks
whether concrete IDs can flow across an edge, keeps connected branches, retries
alternative IDs when a relation is empty, and stops expanding through terminal
lookup/comparison tools. On the same 175-record snapshot, 128 offline proposals
now produce 49 globally distinct executable dependency subgraphs:

| Useful calls | 2 | 3 | 4 | 5 | 6 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Executable subgraphs | 4 | 23 | 14 | 4 | 4 |

The mean is 3.61 useful calls and the maximum is 6. This is an implementation
improvement, not the final target distribution. The snapshot still connects
files to only one of three models and one of three Spaces, so it cannot support
natural two-model or two-Space branches. Reaching an overall 6--7 call mean
requires broader connected data rather than a larger forced walk length.

Semantic review handles four executed chains per request. Its integration path
is covered by a local model stub, but the final external review rerun is pending.
On 2026-08-11, a minimal request to the configured OpenRouter endpoint still
returned HTTP 401 `User not found`; the configured key has the expected format,
so this is an account/key authentication failure rather than evidence of a task
pipeline failure or an insufficient-balance response.

## Codex research-agent comparison

A read-only Codex subagent independently researched the Hugging Face theme. It
avoided weights and found a connected source plan spanning organizations,
members, models, datasets, Spaces, papers, commits, discussions, repository
files, dataset configs, splits, and small row previews. In contrast, the old
Research Agent noticed missing papers/authors but then added 74 files from one
model because record growth was its stopping signal.

The research loop now reports relationship coverage, unconnected IDs,
components, and relation depth; later enrichment rounds receive web search and
fetch tools; repeated leaf growth under one parent does not count as structural
progress. Parent-scoped child lists are capped at 20 representative records,
and path-like child IDs include their parent ID to avoid merging files from
different repositories. A fresh external research run is still pending the
OpenRouter credential fix.

### Independent environment builds

Codex subagents also built five isolated environment snapshots without changing
the shared pipeline: IBGE Brazil, OpenZeppelin, clinical genomics, aerospace,
and automotive power MOSFET selection. The retained reference chains averaged
between 5.47 and 7.92 calls, showing that connected real data can support the
intended depth without a fixed target length.

This experiment also exposed a stricter task-quality problem. A later audit of
the semiconductor snapshot found tasks whose text named a different part from
the reference calls, repeated the same business comparison under different
execution signatures, or joined branches that did not form one natural
question. Reference replay catches execution errors but not these semantic
mapping errors. Codex-assisted runs therefore remain an interactive research
workflow: the supervising agent must compare task text with call arguments and
deduplicate business intent before treating the tasks as training data.

## Earlier catalogue batch

Several MCP themes were rejected because research found documentation or
product descriptions but no operational records supporting the advertised
capabilities. Examples include VAT validation without live validation data,
market APIs without quotes or trades, and messaging products without users,
subscriptions, messages, or segments. Rejecting these environments is expected:
documentation helps interpret a theme but is not sufficient local state.

Across the three earlier environments, 192 raw walks led to 43 executable
unique walks. Those runs predate the current real-link expansion and compact
discovery outputs, so they remain useful as a baseline rather than the current
quality result.

## Five-run verification

Five-run verification is optional. A task passes after two grounded successful
solutions from up to five independent ReAct rollouts. Verification stops early
once the result is decided, and transient model or network errors are retried
without counting as solver failures.

The latest 40-task batch did not run this optional stage. It should be enabled
after data coverage, graph precision, and semantic deduplication improve, so
solver cost is spent on stronger candidates.

## Reproduce

```powershell
python -m unittest discover -s tests -v
python -m agent_world_mini --batch-size 3 --selection-seed 42 --dry-run
python -m agent_world_mini --batch-size 3 --selection-seed 42
```

Generated artifacts are written to `runs/`, which is intentionally excluded
from version control.
