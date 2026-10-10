#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
HISTORY=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006
CONTROL="$HISTORY/control"
OUTPUT="$HISTORY/incremental_autofollow_150x"
LOG="$OUTPUT/coordinator.log"
PYTHON=/home/sunshuo/miniconda3/bin/python
IDLE_CHECK_SECONDS=660

mkdir -p "$CONTROL" "$OUTPUT"
exec >>"$LOG" 2>&1
cd "$REPO"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"

set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

idle_passes=0
while true; do
  stamp=$(date +%Y%m%d_%H%M%S)
  selection="$CONTROL/incremental-${stamp}.selection-summary.json"
  echo "[$(date -Iseconds)] building selection $selection"
  "$PYTHON" .tmp/build_incremental_kimi_selection.py \
    --input-root "$INPUT" \
    --history-root "$HISTORY" \
    --output "$selection" \
    --runtime-backend python_profile

  count=$(jq -r '.new_task_count' "$selection")
  if (( count > 0 )); then
    idle_passes=0
    echo "[$(date -Iseconds)] starting $count newly discovered tasks"
    ionice -c 3 nice -n 10 env \
      KIMI_STATE_COPY_CONCURRENCY=8 \
      KIMI_STATE_COPY_IO_PRESSURE_LIMIT=35 \
      "$PYTHON" -u -m distill taskgen \
      --input-root "$INPUT" \
      --output-root "$OUTPUT" \
      --select-summary "$selection" \
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
    echo "[$(date -Iseconds)] batch finished"
    continue
  fi

  running_generators=$(pgrep -fc '^/home/sunshuo/miniconda3/bin/python -m task_gen\.program\.run_pipeline ' || true)
  if (( running_generators > 0 )); then
    idle_passes=0
    echo "[$(date -Iseconds)] no new tasks; waiting for $running_generators generators"
    sleep "$IDLE_CHECK_SECONDS"
    continue
  fi

  idle_passes=$((idle_passes + 1))
  if (( idle_passes >= 2 )); then
    echo "[$(date -Iseconds)] no generators and no new tasks in two checks; complete"
    break
  fi
  echo "[$(date -Iseconds)] generators ended; waiting for final manifest refresh"
  sleep "$IDLE_CHECK_SECONDS"
done
