# Agent-World Mini

A small, inspectable reproduction of the Agent-World environment construction loop:

> Research prototype: the pipeline runs end to end, but graph precision and
> task deduplication are still active work. See `EXPERIMENTS.md` for measured
> results and current limitations.

1. an offline job collects complete MCP descriptions and tool schemas into a local prepared environment catalogue;
2. a batch run selects only unseen entries from that local catalogue;
3. a research agent autonomously searches and fetches real public data for the selected environment;
4. the mined data generates executable candidate tools, while an environment-level agent uses the MCP capabilities only as context to retain a coherent toolset;
5. every retained tool must pass execution tests against the local data;
6. a sparse strong/weak dependency graph supports topology-aware exploratory walks, with unrelated transitions kept as an implicit fallback;
7. candidates are generated in batches until new useful tasks plateau or the absolute budget is reached; and
8. an optional five-run ReAct solver check retains tasks with at least two grounded correct solutions.

The environment is logically separate per theme but does not require a custom
service implementation per theme. A shared runtime supports search, lookup,
relation traversal, and ranking. The per-theme compiler selects the real entity
types, text fields, relations, numeric measures, schemas, and tests that are
exposed to the agent. At rollout time the agent sees the task, all retained
tool schemas, and prior observations, not the full database, graph, reference
trace, or evaluator. See `THEME_SOURCES.md` and
`TOOL_DESIGN_AND_FILTERING.md` for the source and validation policy.

## Run

The project uses only the Python standard library and requires Python 3.10 or
newer. Install it in editable mode, then create a local `.env` from
`.env.example` and add your OpenRouter key.

```powershell
python -m pip install -e .
Copy-Item .env.example .env
```

```powershell
$env:OPENROUTER_API_KEY = "..."
$env:OPENROUTER_MODEL = "deepseek/deepseek-v4-flash"
python -m agent_world_mini --theme "Find public bicycle-sharing data useful for a city mobility service" --slug city-mobility

# Curated theme sources with public structured-data adapters
python -m agent_world_mini --theme-id world-bank-development --slug world-bank
python -m agent_world_mini --theme-id github-issue-tracking --slug github-issues
python -m agent_world_mini --theme-id bicycle-sharing --slug bicycle-sharing
python -m agent_world_mini --theme-id open-library-subject-catalog --slug open-library
python -m agent_world_mini --theme-id openalex-publication-research --slug openalex
python -m agent_world_mini --theme-id github-community-triage --slug github-triage

# Start from one concrete MCP or tool page
python -m agent_world_mini --source-url "https://example.com/mcp-server" --slug mcp-source

# Periodically prepare MCP descriptions. This is the only step that reads Smithery.
python -m agent_world_mini --prepare-catalog

# Select ten unseen environments from the prepared local catalogue and build them.
# Add --dry-run to inspect local selection without research or model calls.
python -m agent_world_mini --batch-size 10
python -m agent_world_mini --batch-size 10 --selection-seed 42 --dry-run

# Agent-World-style consistency filter (requires OPENROUTER_API_KEY)
python -m agent_world_mini --theme "bicycle sharing systems" --slug city-mobility-verified --verify-five-runs
```

Without `OPENROUTER_API_KEY`, the pipeline still collects and executes graph
walk evidence but emits no training tasks. This avoids treating mechanical
templates as natural user requests.

Outputs are written to `runs/<slug>/`:

- `research_bundle.json`: normalized operational data and source provenance
- `theme_registry.json`: selected seed, source type, URL, and access note
- `environment_manifest.json`: state contract, agent-visible contract, and reset policy
- `tool_specs.json`: compiled, domain-specific tool contracts
- `tool_validation.json`: candidate tool tests, failures, and retained tools
- `tool_graph.json`: weighted graph edges and raw walks
- `walk_synthesis.json`: raw, executed, and causal-core walk evidence
- `tasks.json`: non-leaking task statements with hidden reference paths
- `summary.json`: counts and validation status

The reusable small data adapters live in `agent_world_mini/theme_sources.json`.
Prepared MCP environments live in `agent_world_mini/prepared_environments.json`.
Batch runs never revisit the MCP catalogue; their local selection and results
are recorded in `runs/catalog_batch.json`. MCP tool definitions are capability
evidence, not a required final tool list. Final tools are regenerated from the
mined data, selected for the environment, and execution-tested before graph
construction.

## Design boundary

The generated environment is a local, resettable representation of public
facts. It does not call a live third-party business system during agent
training. Every source URL, retrieval time and data item are preserved in the
research bundle. Read-only source data stays immutable; future local workflow
state must be modeled as a separate resettable overlay. The current new API
themes intentionally remain read-only because their sources do not evidence a
local write workflow.
