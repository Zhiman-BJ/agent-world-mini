#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
BASE=$ROOT/models/Qwen3-8B
LOG_DIR=$ROOT/results/logs
RUN_RETENTION=${RUN_RETENTION:-0}
mkdir -p "$LOG_DIR"

latest_adapter() {
  local root=$1
  if [ -f "$root/adapter_config.json" ]; then
    echo "$root"
    return
  fi
  find "$root" -name adapter_config.json -printf '%T@ %h\n' | sort -nr | head -n 1 | cut -d' ' -f2-
}

refresh_summary() {
  "$ROOT/venvs/sft312/bin/python" "$REPO/training/summarize_results.py" \
    --results "$ROOT/results" \
    --output "$ROOT/results/summary.md"
}

wait_for_jobs() {
  local phase=$1
  shift
  local failed=0
  local job
  for job in "$@"; do
    if ! wait "$job"; then
      failed=1
    fi
  done
  if [ "$failed" != 0 ]; then
    echo "[$phase] one or more model evaluations failed; inspect $LOG_DIR" >&2
    return 1
  fi
  echo "[$phase] all five models completed"
}

lora_tool=$(latest_adapter "$ROOT/outputs/lora_tool_only")
lora_mixed=$(latest_adapter "$ROOT/outputs/lora_mixed")

tags=(base lora_tool_only lora_mixed lora_mixed_grpo full_sft_mixed)
models=("$BASE" "$BASE" "$BASE" "$ROOT/outputs/grpo_lora_mixed_hf" "$ROOT/outputs/full_sft_mixed")
adapters=("" "$lora_tool" "$lora_mixed" "" "")
gpus=(0 1 2 3 4)
ports=(8000 8001 8002 8003 8004)

echo "[internal] starting five models in parallel"
pids=()
for index in "${!tags[@]}"; do
  tag=${tags[$index]}
  CUDA_VISIBLE_DEVICES=${gpus[$index]} \
    MODEL_NAME=Qwen/Qwen3-8B \
    MODEL_PATH=${models[$index]} \
    LORA_PATH=${adapters[$index]} \
    RESULT_TAG=$tag \
    PORT=${ports[$index]} \
    RUN_BFCL=0 \
    SKIP_EXISTING=1 \
    "$REPO/training/serve_and_eval_internal.sh" \
    >"$LOG_DIR/${tag}_internal.log" 2>&1 &
  pids+=("$!")
done
wait_for_jobs internal "${pids[@]}"
refresh_summary

if [ "$RUN_RETENTION" != 1 ]; then
  echo "Tool-use evaluations completed. Retention checks were not requested."
  exit 0
fi

benchmarks=(ifeval mmlu_pro)
for benchmark in "${benchmarks[@]}"; do
  echo "[$benchmark] starting five models in parallel"
  pids=()
  for index in "${!tags[@]}"; do
    tag=${tags[$index]}
    CUDA_VISIBLE_DEVICES=${gpus[$index]} \
      MODEL_PATH=${models[$index]} \
      PEFT_PATH=${adapters[$index]} \
      RESULT_TAG=$tag \
      EVAL_TASKS=$benchmark \
      RESULT_GROUP=$benchmark \
      SKIP_EXISTING=1 \
      "$REPO/training/run_general_eval.sh" \
      >"$LOG_DIR/${tag}_${benchmark}.log" 2>&1 &
    pids+=("$!")
  done
  wait_for_jobs "$benchmark" "${pids[@]}"
  refresh_summary
done

echo "All evaluations completed. Summary: $ROOT/results/summary.md"
