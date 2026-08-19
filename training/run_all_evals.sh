#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
BASE=$ROOT/models/Qwen3-8B

latest_adapter() {
  local root=$1
  if [ -f "$root/adapter_config.json" ]; then
    echo "$root"
    return
  fi
  find "$root" -name adapter_config.json -printf '%T@ %h\n' | sort -nr | head -n 1 | cut -d' ' -f2-
}

run_internal() {
  local tag=$1
  local model_path=$2
  local adapter=${3:-}
  MODEL_NAME=Qwen/Qwen3-8B MODEL_PATH="$model_path" LORA_PATH="$adapter" RESULT_TAG="$tag" RUN_BFCL=0 \
    "$REPO/training/serve_and_eval_internal.sh"
}

run_general() {
  local tag=$1
  local model_path=$2
  local adapter=${3:-}
  MODEL_PATH="$model_path" PEFT_PATH="$adapter" RESULT_TAG="$tag" "$REPO/training/run_general_eval.sh"
}

lora_tool=$(latest_adapter "$ROOT/outputs/lora_tool_only")
lora_mixed=$(latest_adapter "$ROOT/outputs/lora_mixed")
grpo="$ROOT/outputs/grpo_lora_mixed_hf"

run_internal base "$BASE"
run_internal lora_tool_only "$BASE" "$lora_tool"
run_internal lora_mixed "$BASE" "$lora_mixed"
run_internal lora_mixed_grpo "$grpo"
run_internal full_sft_mixed "$ROOT/outputs/full_sft_mixed"

run_general base "$BASE"
run_general lora_tool_only "$BASE" "$lora_tool"
run_general lora_mixed "$BASE" "$lora_mixed"
run_general lora_mixed_grpo "$grpo"
run_general full_sft_mixed "$ROOT/outputs/full_sft_mixed"

if [ "${RUN_OFFICIAL_TOOL_EVALS:-1}" = 1 ]; then
  BENCHMARK_MODE=formal "$REPO/training/run_tool_benchmark_matrix.sh"
fi

"$ROOT/venvs/sft312/bin/python" "$REPO/training/summarize_results.py" \
  --results "$ROOT/results" \
  --output "$ROOT/results/summary.md"
