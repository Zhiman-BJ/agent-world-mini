#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
HISTORY=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006
CONTROL="$HISTORY/control"
OLD_SELECTION="$CONTROL/new570-20261008.selection-summary.json"
NEW_SELECTION="$CONTROL/incremental-20261008_220816.selection-summary.json"
OLD_BASE="$HISTORY/new570_50x/recovery_before_forced_30x/summary.json"
OLD_RUN="$HISTORY/new570_50x/resumed_forced_30x/20261008_171527_357903"
NEW_RUN="$HISTORY/incremental_autofollow_40x/20261008_220923_733320"
ABORTED_RUN="$HISTORY/pending_150x/20261008_233928_847355"
RECOVERY="$HISTORY/cutover_150x_recovery"
OLD_RECOVERY="$RECOVERY/fast_old570"
NEW_RECOVERY="$RECOVERY/fast_new320"
ASYNC_OLD_RECOVERY="$RECOVERY/async_old570"
ASYNC_NEW_RECOVERY="$RECOVERY/async_new320"
ABORTED_SNAPSHOT="$RECOVERY/aborted_serial_copy"
ASYNC_ABORTED_RECOVERY="$RECOVERY/async_aborted_serial_copy"
SELECTION="$CONTROL/pending-150x-20261008.selection-summary.json"
OUTPUT="$HISTORY/pending_150x"
LOG="$OUTPUT/cutover.log"
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$OLD_RECOVERY" "$NEW_RECOVERY" "$ASYNC_OLD_RECOVERY" "$ASYNC_NEW_RECOVERY" \
  "$ABORTED_SNAPSHOT" "$ASYNC_ABORTED_RECOVERY" "$OUTPUT"
exec >>"$LOG" 2>&1
cd "$REPO"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"

echo "[$(date -Iseconds)] snapshotting old570 and deleting interrupted state"
"$PYTHON" .tmp/prepare_interrupted_kimi_cutover.py \
  --run "$OLD_RUN" \
  --output "$OLD_RECOVERY"

echo "[$(date -Iseconds)] snapshotting new320 and deleting interrupted state"
"$PYTHON" .tmp/prepare_interrupted_kimi_cutover.py \
  --run "$NEW_RUN" \
  --output "$NEW_RECOVERY"

echo "[$(date -Iseconds)] building merged pending selection"
"$PYTHON" .tmp/build_incremental_kimi_selection.py \
  --input-root "$INPUT" \
  --history-root "$HISTORY" \
  --output "$SELECTION" \
  --runtime-backend python_profile \
  --include-selection "$OLD_SELECTION" \
  --include-selection "$NEW_SELECTION" \
  --processed-summary "$OLD_BASE" \
  --processed-summary "$OLD_RECOVERY/summary.json" \
  --processed-summary "$NEW_RECOVERY/summary.json" \
  --processed-summary "$ABORTED_SNAPSHOT/summary.json"

count=$(jq -r '.new_task_count' "$SELECTION")
echo "[$(date -Iseconds)] starting unified pending batch: $count tasks at concurrency 150"

echo "[$(date -Iseconds)] recovering pending verifier results asynchronously"
if ! pgrep -f -- "recover_interrupted_kimi_batch.py.*--output-root $ASYNC_OLD_RECOVERY" >/dev/null; then
  nohup setsid "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$ASYNC_OLD_RECOVERY" \
    --run "$OLD_RUN" \
    --retry-incomplete \
    --max-concurrency 20 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --timeout 1800 \
    --attempts 3 >"$ASYNC_OLD_RECOVERY/recovery.log" 2>&1 &
fi
if ! pgrep -f -- "recover_interrupted_kimi_batch.py.*--output-root $ASYNC_NEW_RECOVERY" >/dev/null; then
  nohup setsid "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$ASYNC_NEW_RECOVERY" \
    --run "$NEW_RUN" \
    --retry-incomplete \
    --max-concurrency 20 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --timeout 1800 \
    --attempts 3 >"$ASYNC_NEW_RECOVERY/recovery.log" 2>&1 &
fi
if ! pgrep -f -- "recover_interrupted_kimi_batch.py.*--output-root $ASYNC_ABORTED_RECOVERY" >/dev/null; then
  nohup setsid "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$ASYNC_ABORTED_RECOVERY" \
    --run "$ABORTED_RUN" \
    --retry-incomplete \
    --max-concurrency 20 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --timeout 1800 \
    --attempts 3 >"$ASYNC_ABORTED_RECOVERY/recovery.log" 2>&1 &
fi

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

if (( count > 0 )); then
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

echo "[$(date -Iseconds)] pending batch complete; entering incremental follow mode"
exec bash .tmp/run_kimi_incremental_autofollow.sh
