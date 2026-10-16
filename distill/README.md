# Kimi K3 trajectory distillation

This module runs the official Kimi Code CLI in streaming mode against one task-scoped Agent World MCP server and exports a versioned trajectory.

## Policy boundary

The runner creates an isolated, temporary Kimi home for every task. Its agent profile uses the official `${base_prompt}` and exposes only:

- the task's `mcp__agent_world_distill__*` tools;
- native Kimi `Read`, `Grep`, and `Glob` for the model workspace and Kimi-owned tool-result files.

For new evaluations, select `--mcp-server-name workspace` to expose
`mcp__workspace__<business_tool>`. The runner changes MCP registration, the
agent allowlist, and request validation together, and records the namespace
in `raw/harness_policy.json`. The default remains `agent_world_distill` for
existing runs. Native file-tool names and the independent model-provider
configuration name do not change. Use a separate output root when changing
the namespace; this option does not repair omitted prefixes in model output.

Official `Write`, `Edit`, `Bash`, `Agent`, `AgentSwarm`, network tools, other MCP servers, and `select_tools` are unavailable. Environment MCP results are returned in full; Kimi Code owns any externalization into its session-local `tool-results` directory. Native file tools are not an environment Scope browser.

Native file calls pass through a `PreToolUse` realpath allowlist. Only the task's model workspace, the current session's main-agent `tool-results` and `wire.jsonl`, and current-session `kimi-file://` attachments are allowed. Absolute paths into `private-state`, raw evidence, another task/session, or any symlink escape are denied before the native tool runs. Decisions are retained in `raw/native_file_access.jsonl`.

The runner uses the official Kimi Code `kimi` provider (the validated installation is `0.43.0`) with K3 metadata: a 1,048,576-token default context window, an optional 262,144-token experimental cap, a separate 131,072-token output cap, always-on thinking, and supported efforts `low/high/max`. The separate output cap prevents the prompt plus `max_completion_tokens` from exceeding the upstream context window. Distillation runs default to `max`; select another supported level with `--reasoning-effort`. It does not override sampling, compaction thresholds, or loop limits. Model calls use streaming Chat Completions; the local relay rejects non-streaming requests and normalizes Kimi Code's provider fields to the public K3 API contract (`reasoning_effort`, `max_completion_tokens`, and explicit `parallel_tool_calls=true`). It retries transient upstream responses with exponential backoff and jitter. A 400 is retryable only when its JSON message explicitly reports rejection by an internal MaaS component; ordinary request/schema 400s are returned immediately. Use `--max-upstream-retries 0` to disable retries.

When a task has a ToolGen `binding_path`, the task MCP process uses that delivery's `runtime.json`: `host_python`, `python_profile`, and `docker` are selected through the same `stdio_launch()` path as the generic ToolGen MCP entry point. Task workspaces execute validated delivery code in a child process without a nested bubblewrap layer; path authorization and transaction rollback remain in the task MCP gateway.

Before Kimi starts, the runner launches that exact MCP command and performs
`initialize`, `notifications/initialized`, and `tools/list`. It requires the
returned tool names to match the task contract and records the result in
`raw/mcp_preflight.json` plus the unmodified stdout/stderr. A failed preflight
ends the task before any model request. Docker-backed MCP tasks additionally
share a four-slot session limit that covers preflight and the complete Kimi
process lifetime; `--max-concurrency` still applies normally to non-Docker
tasks. Kimi's own MCP startup timeout is 120 seconds, and the relay continues to
reject a first model request that omits any expected MCP tool.

## Evidence

Kimi talks to a loopback streaming relay, which forwards bytes directly to the configured K3 gateway. The upstream API key stays in the parent process and is never written to Kimi configuration or logs.

Each case contains:

```text
case/
  trajectory.json
  <environment-id>_<task-id>/ # Kimi cwd and mounted Filesystem Scopes
  private-state/            # records.sqlite and path-map.json; model-invisible
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
    mcp_preflight.json
    mcp_preflight.stdout.jsonl
    mcp_preflight.stderr.txt
    native_file_access.jsonl
    kimi_session.zip
    run_result.json
```

`model_requests.jsonl` contains the exact context and tool definitions sent on every model call, including compaction calls and the first inference call after each compaction. `model_io` retains exact request bytes, the pre-normalization Kimi Code request, and raw streamed SSE. `wire.jsonl` is the official Kimi Session Wire, including tool events, compaction boundaries, before/after token counts, compacted message counts, and recovery summaries. `trajectory.json` links the exact pre-compaction request and post-compaction inference context while retaining all originals.

Immediate TaskGen verification combines original MCP records with completed native
`Read`, `Grep`, and `Glob` results from the exported trajectory, including EOF notes
and failed calls. Every raw MCP record is retained unchanged, with duplicate
counts preserved, even when path normalization prevents its attachment to an
exported call. Attached records are checked against the raw trace. MCP results
stay in raw completion order; native results follow separately, not as a claimed
joint timeline. `actual/execution.json` includes the CLI invocation manifest with
original arguments and event sequences. Incomplete native results or conflicting
attached records are verification errors, not task FAILs.
The state-mutation count still uses only raw environment calls. Older verdicts
created with MCP-only evidence require separate review; this change does not
overwrite them or update an already-running batch's imported verifier.
New immediate verifications also enable submission-format preflight: the grader
checks exact task excerpts and evidence references before submitting its JSON.
The host still validates every verdict and rejects invalid submissions; this
does not relax any task requirement or change PASS/FAIL acceptance rules.

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

The server configuration uses the task-scoped `task_gen.task_eval_mcp` format: tools, environment, tool budget, timeout, memory limit, process limit, write limit, and optional ToolGen `binding_path`. TaskGen batch runs default to a 128 GiB virtual-address limit. The address-space value is a ceiling on virtual mappings, not reserved physical RAM; the default is sized for the tested JAX/XLA and Torch runtimes. Direct task-workspace execution deliberately does not apply `RLIMIT_NPROC`: Linux accounts it across the real user rather than per task, so a nominal per-tool ceiling can reject thread creation solely because unrelated jobs share the UID. `--tool-process-limit` remains for legacy isolated-tool execution only.

## Run TaskGen bundles

```bash
python -m distill taskgen \
  --input-root /path/to/taskgen/root \
  --output-root distill/.runs \
  --task-id task4 \
  --provider-type kimi \
  --reasoning-effort max \
  --max-concurrency 1
```

Every task gets a separate workspace, Kimi home, MCP process, result-ID namespace, session ZIP, and evidence directory. A failed task retains partial evidence and does not stop later tasks; the batch command exits nonzero after all cases finish if any failed.

For TaskGen batches, the original published `task_text` remains in trajectory metadata while the actual Kimi user prompt removes an initial paragraph only when it exactly equals `environment.summary`. MCP tool calls expose the complete business result once through `structuredContent`; the backwards-compatible `content` array is empty because every public tool has an `outputSchema`.

To resume after a completed batch without rerunning its successful cases, pass its summary with `--skip-summary`. Use `--select-summary` to restrict a run to the exact ordered case list from an earlier batch, which is useful when resuming a prefix without selecting later cases from the input root. Both environment-error thresholds default to 2. `--max-environment-errors-per-task N` stops the affected task as soon as it reaches `N` non-recoverable execution-layer errors. `--max-environment-errors N` is a live batch circuit breaker: after the aggregate reaches the threshold, it terminates all active Kimi sessions, preserves their evidence, and stops new dispatch. Execution-layer errors include `backend_unavailable`, `runtime_error`, process/thread resource failures, and startup/exit failures; business failures and invalid task arguments do not count. Tool deadlines are returned separately as `timeout` with `retryable=true`, leave task state unchanged, and are recorded without consuming either environment-error threshold.

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

## 原生 Read 输出限制

新会话默认使用 `--read-default-max-chars 80000 --read-max-chars 128000`。
两项限制按 UTF-16 码元计数，不是 token 数；写入官方 CLI 的 `[read]` 配置，
并记录在运行策略中，确保工具说明与实际截断上限一致。
超大请求会被截断，并返回续读偏移参数；已有会话需要重启才能应用新配置。
这些字符限制不会动态控制剩余 token 预算，也不会汇总并行工具返回预算，仍然需要自动压缩。
