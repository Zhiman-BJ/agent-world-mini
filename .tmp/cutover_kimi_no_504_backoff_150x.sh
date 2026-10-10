#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
HISTORY=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006
SOURCE_RUN="$HISTORY/pending_150x/20261008_234833_559243"
SOURCE_SELECTION="$HISTORY/control/pending-150x-20261008.selection-summary.json"
RECOVERY="$HISTORY/cutover_no_504_backoff_150x"
SNAPSHOT="$RECOVERY/source_snapshot"
ASYNC_RECOVERY="$RECOVERY/async_verifier_recovery"
SELECTION="$HISTORY/control/pending-no-504-backoff-150x.selection-summary.json"
OUTPUT="$HISTORY/pending_no_504_backoff_150x"
LOG="$OUTPUT/cutover.log"
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$SNAPSHOT" "$ASYNC_RECOVERY" "$OUTPUT"
exec >>"$LOG" 2>&1
cd "$REPO"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"

echo "[$(date -Iseconds)] snapshotting terminal and verifier-pending tasks"
"$PYTHON" .tmp/prepare_interrupted_kimi_cutover.py \
  --run "$SOURCE_RUN" \
  --output "$SNAPSHOT"

echo "[$(date -Iseconds)] building remaining selection"
"$PYTHON" .tmp/build_incremental_kimi_selection.py \
  --input-root "$INPUT" \
  --history-root "$HISTORY" \
  --output "$SELECTION" \
  --runtime-backend python_profile \
  --include-selection "$SOURCE_SELECTION" \
  --processed-summary "$SNAPSHOT/summary.json"

count=$(jq -r '.new_task_count' "$SELECTION")
echo "[$(date -Iseconds)] remaining tasks: $count"

if (( $(jq -r '.pending_verification' "$SNAPSHOT/summary.json") > 0 )); then
  echo "[$(date -Iseconds)] recovering completed trajectories asynchronously"
  nohup setsid "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$ASYNC_RECOVERY" \
    --run "$SOURCE_RUN" \
    --retry-incomplete \
    --max-concurrency 25 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --timeout 1800 \
    --attempts 3 >"$ASYNC_RECOVERY/recovery.log" 2>&1 &
fi

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

if (( count > 0 )); then
  echo "[$(date -Iseconds)] starting 150 concurrency; terminal 504 stays task-local"
  ionice -c 3 nice -n 10 env \
    KIMI_STATE_COPY_CONCURRENCY=8 \
    KIMI_STATE_COPY_IO_PRESSURE_LIMIT=35 \
    "$PYTHON" -u -m distill taskgen \
    --input-root "$INPUT" \
    --output-root "$OUTPUT" \
    --select-summary "$SELECTION" \
    --runtime-backend python_profile \
    --max-concurrency 150 \
    --adaptive-concurrency \
    --min-concurrency 20 \
    --initial-concurrency 150 \
    --verify-after-each \
    --verifier-concurrency 50 \
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
fi

echo "[$(date -Iseconds)] remaining batch complete; entering incremental follow mode"
exec bash .tmp/run_kimi_incremental_autofollow.sh
