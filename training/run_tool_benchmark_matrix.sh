#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
BASE=$ROOT/models/Qwen3-8B
BENCHMARKS=${BENCHMARKS:-bfcl mcpmark tau2 mcp-atlas vitabench}
GPU=${BENCHMARK_GPU:-5}
PORT=${PORT:-8100}

latest_adapter() {
  local root=$1
  if [ -f "$root/adapter_config.json" ]; then
    echo "$root"
    return
  fi
  find "$root" -name adapter_config.json -printf '%T@ %h\n' | sort -nr | head -n 1 | cut -d' ' -f2-
}

lora_tool=$(latest_adapter "$ROOT/outputs/lora_tool_only")
lora_mixed=$(latest_adapter "$ROOT/outputs/lora_mixed")
tags=(base lora_tool_only lora_mixed lora_mixed_grpo full_sft_mixed)
models=("$BASE" "$BASE" "$BASE" "$ROOT/outputs/grpo_lora_mixed_hf" "$ROOT/outputs/full_sft_mixed")
adapters=("" "$lora_tool" "$lora_mixed" "" "")

for benchmark in $BENCHMARKS; do
  for index in "${!tags[@]}"; do
    tag=${tags[$index]}
    echo "[$benchmark] evaluating $tag"
    CUDA_VISIBLE_DEVICES=$GPU \
      BENCHMARK=$benchmark \
      RESULT_TAG=$tag \
      MODEL_PATH=${models[$index]} \
      LORA_PATH=${adapters[$index]} \
      PORT=$PORT \
      bash "$REPO/training/serve_and_run_tool_benchmark.sh"
  done
  "$ROOT/venvs/sft312/bin/python" "$REPO/training/summarize_official_tools.py" \
    --results "$ROOT/results/official" \
    --output "$ROOT/results/official/summary.md"
done
