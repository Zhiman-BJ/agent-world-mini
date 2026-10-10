#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/new570_50x
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
SELECT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/new570-20261008.selection-summary.json
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$ROOT"
exec >>"$ROOT/run.log" 2>&1
cd "$REPO"

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

exec ionice -c 3 nice -n 10 env KIMI_STATE_COPY_IO_PRESSURE_LIMIT=35 \
  "$PYTHON" -u -m distill taskgen \
  --input-root "$INPUT" \
  --output-root "$ROOT" \
  --select-summary "$SELECT" \
  --runtime-backend python_profile \
  --max-concurrency 50 \
  --adaptive-concurrency \
  --min-concurrency 5 \
  --initial-concurrency 25 \
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
  --verifier-reasoning-effort high \
  --verifier-timeout 1800 \
  --verifier-attempts 3
