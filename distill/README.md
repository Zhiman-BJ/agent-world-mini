# Kimi K3 trajectory distillation

This module runs the official Kimi Code CLI in streaming mode against one task-scoped Agent World MCP server and exports a versioned trajectory.

## Policy boundary

The runner creates an isolated, temporary Kimi home for every task. Its agent profile uses the official `${base_prompt}` and exposes only:

- the task's `mcp__agent_world_distill__*` tools;
- native Kimi `Read`, `Grep`, and `Glob` for the model workspace and Kimi-owned tool-result files.

Official `Write`, `Edit`, `Bash`, `Agent`, `AgentSwarm`, network tools, other MCP servers, and `select_tools` are unavailable. Environment MCP results are returned in full; Kimi Code owns any externalization into its session-local `tool-results` directory. Native file tools are not an environment Scope browser.

Native file calls pass through a `PreToolUse` realpath allowlist. Only `model-workspace`, the current session's main-agent `tool-results` and `wire.jsonl`, and current-session `kimi-file://` attachments are allowed. Absolute paths into `execution-state`, raw evidence, another task/session, or any symlink escape are denied before the native tool runs. Decisions are retained in `raw/native_file_access.jsonl`.

The runner uses the official Kimi Code `kimi` provider (the validated installation is `0.43.0`) with K3 metadata: a 1,048,576-token default context window, an optional 262,144-token experimental cap, always-on thinking, and supported efforts `low/high/max`. Distillation runs default to `high`; select another supported level with `--reasoning-effort` (the public K3 API itself defaults to `max`). It does not override sampling, compaction thresholds, or loop limits. Model calls use streaming Chat Completions; the local relay rejects non-streaming requests and normalizes Kimi Code's provider fields to the public K3 API contract (`reasoning_effort`, `max_completion_tokens`, and explicit `parallel_tool_calls=true`). It retries transient upstream responses with exponential backoff and jitter. A 400 is retryable only when its JSON message explicitly reports rejection by an internal MaaS component; ordinary request/schema 400s are returned immediately. Use `--max-upstream-retries 0` to disable retries.

When a task has a ToolGen `binding_path`, the task MCP process uses that delivery's `runtime.json`: `host_python`, `python_profile`, and `docker` are selected through the same `stdio_launch()` path as the generic ToolGen MCP entry point. Docker images must include `bubblewrap`, because the task-local tool sandbox remains active inside the delivery container.

## Evidence

Kimi talks to a loopback streaming relay, which forwards bytes directly to the configured K3 gateway. The upstream API key stays in the parent process and is never written to Kimi configuration or logs.

Each case contains:

```text
case/
  trajectory.json
  execution-state/          # MCP backend state; not the model cwd
  model-workspace/          # empty Kimi cwd; environment state is not exposed here
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
    native_file_access.jsonl
    kimi_session.zip
    run_result.json
```

`model_requests.jsonl` contains the exact context and tool definitions sent on every model call. `model_io` retains exact request bytes and raw streamed SSE. `wire.jsonl` is the official Kimi Session Wire, including tool events and compaction boundaries. `trajectory.json` links these artifacts without discarding the originals.

## Run one case

Install the validated Kimi Code CLI `0.43.0` (or another version only after rerunning the compatibility tests) and expose the upstream credentials only through environment variables:

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
