#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/new570_50x
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
SELECT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/new570-20261008.selection-summary.json
SOURCE_RUN="$ROOT/20261008_111706_921581"
PREVIOUS_RUN="$ROOT/resumed_after_interrupt/20261008_131532_613553"
CURRENT_RUN="$ROOT/resumed_20x_after_speedup/20261008_150100_135609"
RECOVERY="$ROOT/recovery_before_forced_30x"
RESUME="$ROOT/resumed_forced_30x"
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$RECOVERY" "$RESUME"
exec >>"$ROOT/recover-and-resume.log" 2>&1
cd "$REPO"

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

echo "[$(date -Iseconds)] recovering complete trajectories and deleting interrupted state"
"$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
  --input-root "$INPUT" \
  --output-root "$RECOVERY" \
  --run "$SOURCE_RUN" \
  --run "$PREVIOUS_RUN" \
  --run "$CURRENT_RUN" \
  --retry-incomplete \
  --max-concurrency 10 \
  --model gpt-6.1-sol \
  --reasoning-effort high \
  --timeout 1800 \
  --attempts 3

echo "[$(date -Iseconds)] resuming unprocessed cases with adaptive concurrency 20..50"
exec ionice -c 3 nice -n 10 env KIMI_STATE_COPY_IO_PRESSURE_LIMIT=35 \
  "$PYTHON" -u -m distill taskgen \
  --input-root "$INPUT" \
  --output-root "$RESUME" \
  --select-summary "$SELECT" \
  --skip-processed-summary "$RECOVERY/summary.json" \
  --runtime-backend python_profile \
  --max-concurrency 50 \
  --adaptive-concurrency \
  --min-concurrency 20 \
  --initial-concurrency 40 \
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
