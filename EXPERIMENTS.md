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

## What failed and why

Several MCP themes were rejected because research found documentation or
product descriptions but no operational records supporting the advertised
capabilities. Examples include VAT validation without live validation data,
market APIs without quotes or trades, and messaging products without users,
subscriptions, messages, or segments. Rejecting these environments is expected:
documentation helps interpret a theme but is not sufficient local state.

The remaining bottleneck is graph precision. Across the three retained
environments, 192 raw walks led to 43 executable unique walks. LLM refinement
can still propose plausible-sounding edges that lack a concrete parameter
binding. The next graph experiment should compare schema-only edges,
schema-plus-LLM edges, and LLM edges retained only after successful binding.

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
