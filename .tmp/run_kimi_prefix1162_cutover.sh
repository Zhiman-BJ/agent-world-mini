#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/prefix1162_75x
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
SELECT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/prefix1162.selection-summary.json
DISPATCHED=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/control/main-first150.dispatched-summary.json
SOURCE_RUN=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/resumed100/20261006_194835_701075
SOURCE_UNIT=agent-world-kimi-k3-distill-fixed.service
SOURCE_MONITOR=agent-world-kimi-k3-monitor-fixed.service
RECOVERY="$ROOT/recovery"
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p "$ROOT/resumed75" "$RECOVERY"
exec >>"$ROOT/cutover.log" 2>&1
cd "$REPO"

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

echo "[$(date -Iseconds)] starting the 1012 prefix cases not dispatched by the source batch"
"$PYTHON" -m distill taskgen \
  --input-root "$INPUT" \
  --output-root "$ROOT/resumed75" \
  --select-summary "$SELECT" \
  --skip-processed-summary "$DISPATCHED" \
  --runtime-backend python_profile \
  --runtime-backend host_python \
  --max-concurrency 75 \
  --adaptive-concurrency \
  --min-concurrency 10 \
  --initial-concurrency 25 \
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
  --verifier-reasoning-effort high &
prefix_pid=$!

echo "[$(date -Iseconds)] waiting for the source batch to drain to ten active tasks"
while systemctl --user is-active --quiet "$SOURCE_UNIT"; do
  if ! kill -0 "$prefix_pid" 2>/dev/null; then
    wait "$prefix_pid"
    exit $?
  fi
  target=$(jq -r '.concurrency_target // 0' "$SOURCE_RUN/summary.json" 2>/dev/null || echo 0)
  active=$(jq -r '.in_progress_count // 999' "$SOURCE_RUN/summary.json" 2>/dev/null || echo 999)
  if [[ "$target" -le 10 && "$active" -le 10 ]]; then
    break
  fi
  sleep 10
done

if ! systemctl --user is-active --quiet "$SOURCE_UNIT"; then
  echo "[$(date -Iseconds)] source batch ended before cutover; recovering its final state"
else
  jq '{timestamp: now | todate, started_count, in_progress_count, case_count, concurrency_target}' \
    "$SOURCE_RUN/summary.json" >"$ROOT/cutover-state.json"
  echo "[$(date -Iseconds)] stopping the drained source batch"
  systemctl --user stop "$SOURCE_MONITOR" || true
  systemctl --user stop "$SOURCE_UNIT"
fi

echo "[$(date -Iseconds)] recovering completed source trajectories"
"$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
  --input-root "$INPUT" \
  --output-root "$RECOVERY" \
  --max-concurrency 10 \
  --model gpt-6.1-sol \
  --reasoning-effort high \
  --retry-incomplete \
  --run "$SOURCE_RUN"

echo "[$(date -Iseconds)] source recovery complete; waiting for the prefix queue"
wait "$prefix_pid"
