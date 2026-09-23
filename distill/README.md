# Kimi K3 trajectory distillation

This module runs the official Kimi Code CLI in streaming mode against one task-scoped Agent World MCP server and exports a versioned trajectory.

## Policy boundary

The runner creates an isolated, temporary Kimi home for every task. Its agent profile uses the official `${base_prompt}` and exposes only:

- the task's `mcp__agent_world_distill__*` tools;
- `read_tool_result`, supplied by the same MCP server, which accepts only random result IDs created in the current session.

Official `Read`, `Write`, `Bash`, `Grep`, `Agent`, other MCP servers, and `select_tools` are unavailable. Long environment results are converted to an initial page before Kimi sees them. The original result is retained in the environment trace, and later pages are read by result ID rather than file path.

The runner uses Kimi Code's official `kimi` provider with the K3 model metadata declared by Kimi Code 2.0.2: a 1,048,576-token context window, always-on thinking, and supported efforts `low/high/max`. Distillation runs default to `high`; select another supported level with `--reasoning-effort` (the public K3 API itself defaults to `max`). It does not override sampling, compaction thresholds, loop limits, or retry limits. Model calls use streaming Chat Completions; the local relay rejects non-streaming requests and normalizes Kimi Code's provider fields to the public K3 API contract (`reasoning_effort` and `max_completion_tokens`).

## Evidence

Kimi talks to a loopback streaming relay, which forwards bytes directly to the configured K3 gateway. The upstream API key stays in the parent process and is never written to Kimi configuration or logs.

Each case contains:

```text
case/
  trajectory.json
  workspace/
  raw/
    cli_stream.jsonl
    cli_stderr.txt
    wire.jsonl
    session_state.json
    model_requests.jsonl
    model_responses.jsonl
    model_io/*.request.json
    model_io/*.response.sse
    environment_tool_calls.jsonl
    result_reads.jsonl
    result_index.jsonl
    kimi_session.zip
    run_result.json
```

`model_requests.jsonl` contains the exact context and tool definitions sent on every model call. `model_io` retains exact request bytes and raw streamed SSE. `wire.jsonl` is the official Kimi Session Wire, including tool events and compaction boundaries. `trajectory.json` links these artifacts without discarding the originals.

## Run one case

Install Kimi Code CLI 2.0.2 or later and expose the upstream credentials only through environment variables:

```bash
export KIMI_CODE_BIN=/absolute/path/to/kimi
export KIMI_BASE_URL=https://gateway.example/v1
export KIMI_API_KEY='...'
export KIMI_MAX_CONTEXT_SIZE=1048576

cd /home/sunshuo/AgenticDataGeneration/agent-world-mini
python -m distill run \
  --prompt-file /path/to/task.txt \
  --initial-state /path/to/initial_state \
  --server-config /path/to/task_mcp_server.json \
  --output distill/.runs/example
```

The server configuration uses the task-scoped `task_gen.task_eval_mcp` format: tools, environment, tool budget, timeout, memory limit, process limit, write limit, and optional ToolGen `binding_path`. TaskGen batch runs default to a 128 GiB virtual-address limit and sets the tool subprocess's `RLIMIT_NPROC` ceiling to 1024; override them with `--tool-memory-limit` and `--tool-process-limit` when a delivery needs a different budget. The address-space value is a ceiling on virtual mappings, not reserved physical RAM; the default is sized for the tested JAX/XLA and Torch runtimes. Linux accounts `RLIMIT_NPROC` across the real user, so it is not a per-sandbox quota.

## Run TaskGen bundles

```bash
python -m distill taskgen \
  --input-root /path/to/taskgen/root \
  --output-root distill/.runs \
  --task-id task4 \
  --provider-type kimi \
  --reasoning-effort high \
  --max-concurrency 1
```

Every task gets a separate workspace, Kimi home, MCP process, result-ID namespace, session ZIP, and evidence directory. A failed task retains partial evidence and does not stop later tasks; the batch command exits nonzero after all cases finish if any failed.

To resume after a completed batch without rerunning its successful cases, pass its summary with `--skip-summary`. Use `--select-summary` to restrict a run to the exact ordered case list from an earlier batch, which is useful when resuming a prefix without selecting later cases from the input root. `--max-environment-errors-per-task N` stops only the affected task as soon as it reaches `N` non-recoverable execution-layer errors, records it as `environment_stopped`, and continues dispatching other cases. `--max-environment-errors N` instead stops all new batch dispatch after the aggregate count reaches the threshold. Execution-layer errors include `backend_unavailable`, `runtime_error`, sandbox startup/exit failures, and resource failures; business failures and invalid task arguments do not count. Tool deadlines are returned separately as `timeout` with `retryable=true`, leave task state unchanged, and are recorded without consuming either environment-error threshold.

`--input-root` accepts either one environment directory containing run directories or the parent directory containing multiple environment directories.

## Re-export

Rebuild the normalized record without calling the model:

```bash
python -m distill export \
  --raw distill/.runs/example/raw \
  --output distill/.runs/example/trajectory.json \
  --task task.json \
  --environment environment.json
```

## Visualize

Generate one offline HTML file beside the trajectory:

```bash
python -m distill visualize \
  --trajectory distill/.runs/example/trajectory.json
```

Generate every trajectory viewer in a completed batch plus a searchable `index.html`:

```bash
python -m distill visualize \
  --run-directory distill/.runs/20260921_013441_436933
```

Merge the complete viewers into one self-contained, searchable HTML file:

```bash
python -m distill visualize \
  --run-directory distill/.runs/20260921_013441_436933 \
  --combined
```

Each viewer includes the step timeline, reasoning, the complete first model request and system prompt, environment and support tool calls, final answer, verifier requirements, token usage, compactions, and the hashed artifact manifest.
