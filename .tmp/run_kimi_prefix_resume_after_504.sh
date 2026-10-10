#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/prefix1162_75x
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
SELECT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/prefix1162.selection-summary.json
DISPATCHED=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/main-first150.dispatched-summary.json
BASE_RECOVERY="$ROOT/recovery_after_mcp_cluster/summary.json"
SOURCE_RUN="$ROOT/resumed_after_mcp10/20261007_035430_860542"
RECOVERY="$ROOT/recovery_after_504_v2"
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$ROOT/resumed_after_504_v2" "$RECOVERY"
exec >>"$ROOT/resume-after-504-v2.log" 2>&1
cd "$REPO"

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

echo "[$(date -Iseconds)] recovering terminal prefix results and deleting interrupted task state"
"$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
  --input-root "$INPUT" \
  --output-root "$RECOVERY" \
  --max-concurrency 10 \
  --model gpt-6.1-sol \
  --reasoning-effort high \
  --retry-incomplete \
  --run "$SOURCE_RUN"

echo "[$(date -Iseconds)] resuming unprocessed prefix cases at adaptive concurrency 10..75"
exec "$PYTHON" -m distill taskgen \
  --input-root "$INPUT" \
  --output-root "$ROOT/resumed_after_504_v2" \
  --select-summary "$SELECT" \
  --skip-processed-summary "$DISPATCHED" \
  --skip-processed-summary "$BASE_RECOVERY" \
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
