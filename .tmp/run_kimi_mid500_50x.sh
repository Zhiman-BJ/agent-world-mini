#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/mid500_50x
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
SELECT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/mid500.selection-summary.json
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$ROOT/resumed100"
exec >>"$ROOT/run.log" 2>&1
cd "$REPO"

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

exec "$PYTHON" -m distill taskgen \
  --input-root "$INPUT" \
  --output-root "$ROOT/resumed100" \
  --select-summary "$SELECT" \
  --runtime-backend python_profile \
  --runtime-backend host_python \
  --max-concurrency 50 \
  --adaptive-concurrency \
  --min-concurrency 10 \
  --initial-concurrency 50 \
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
