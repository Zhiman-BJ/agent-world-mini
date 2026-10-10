#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/prefix1162_75x
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
SELECT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/prefix1162.selection-summary.json
DISPATCHED=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/main-first150.dispatched-summary.json
SOURCE_RUN="$ROOT/resumed75/20261006_234332_363446"
RECOVERY="$ROOT/recovery_after_mcp_cluster"
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$ROOT/resumed_after_mcp10" "$RECOVERY"
exec >>"$ROOT/resume-after-mcp-cluster.log" 2>&1
cd "$REPO"

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

echo "[$(date -Iseconds)] recovering the prefix shard after clustered MCP startup failures"
"$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
  --input-root "$INPUT" \
  --output-root "$RECOVERY" \
  --max-concurrency 10 \
  --model gpt-6.1-sol \
  --reasoning-effort high \
  --retry-incomplete \
  --run "$SOURCE_RUN"

echo "[$(date -Iseconds)] resuming the unprocessed prefix cases at concurrency 10"
exec "$PYTHON" -m distill taskgen \
  --input-root "$INPUT" \
  --output-root "$ROOT/resumed_after_mcp10" \
  --select-summary "$SELECT" \
  --skip-processed-summary "$DISPATCHED" \
  --skip-processed-summary "$RECOVERY/summary.json" \
  --runtime-backend python_profile \
  --runtime-backend host_python \
  --max-concurrency 75 \
  --adaptive-concurrency \
  --min-concurrency 10 \
  --initial-concurrency 10 \
  --verify-after-each \
  --verifier-concurrency 25 \
  --delete-task-state \
  --kimi-bin /data1/agent_world/kimi_code/kimi \
  --model-id kimi-k3 \
  --provider-type kimi \
  --reasoning-effort max \
  --max-context-size 262144 \
  --max-tool-calls 65 \
  --max-environment-errors-per-task 1 \
  --max-upstream-retries 4 \
  --verifier-model gpt-6.1-sol \
  --verifier-reasoning-effort high
