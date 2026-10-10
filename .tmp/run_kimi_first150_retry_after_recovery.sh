#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
SELECT="$ROOT/control/main-first150.dispatched-summary.json"
RECOVERY="$ROOT/prefix1162_75x/recovery/summary.json"
OUTPUT="$ROOT/first150_retry_10x/resumed10"
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$OUTPUT"
exec >>"$ROOT/first150_retry_10x/run.log" 2>&1
cd "$REPO"

echo "[$(date -Iseconds)] waiting for source recovery to finish"
until jq -e '.status == "completed" and .pending_verification == 0' \
  "$RECOVERY" >/dev/null 2>&1; do
  sleep 10
done

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

echo "[$(date -Iseconds)] retrying interrupted cases from the first 150"
exec "$PYTHON" -m distill taskgen \
  --input-root "$INPUT" \
  --output-root "$OUTPUT" \
  --select-summary "$SELECT" \
  --skip-processed-summary "$RECOVERY" \
  --runtime-backend python_profile \
  --runtime-backend host_python \
  --max-concurrency 10 \
  --adaptive-concurrency \
  --min-concurrency 5 \
  --initial-concurrency 10 \
  --verify-after-each \
  --verifier-concurrency 10 \
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
