#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
VERL=${VERL_ROOT:-$ROOT/verl}
UV=${UV_BIN:-$ROOT/venvs/bootstrap312/bin/uv}
BASE=$ROOT/models/Qwen3-8B
MODEL_NAME=Qwen/Qwen3-8B
LOG_DIR=$ROOT/results/official/one_run_logs
MARKER_DIR=$ROOT/results/official/one_run_markers

mkdir -p "$LOG_DIR" "$MARKER_DIR"

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
tags=(base lora_tool_only lora_mixed full_sft_mixed lora_mixed_grpo)
models=("$BASE" "$BASE" "$BASE" "$ROOT/outputs/full_sft_mixed" "$ROOT/outputs/grpo_lora_mixed_hf")
adapters=("" "$lora_tool" "$lora_mixed" "" "")

run_wave() {
  local start=$1
  local end=$2
  local server_pids=()
  local eval_pids=()
  local wave_indices=()

  for index in $(seq "$start" "$end"); do
    local tag=${tags[$index]}
    if [ -f "$MARKER_DIR/vitabench_$tag.complete" ]; then
      continue
    fi
    local slot=${#wave_indices[@]}
    local gpu=$((5 + slot))
    local port=$((8140 + slot))
    local serve_args=(
      --model "${models[$index]}"
      --served-model-name "$MODEL_NAME"
      --host 127.0.0.1
      --port "$port"
      --tensor-parallel-size 1
      --gpu-memory-utilization 0.85
      --max-model-len 40960
      --generation-config vllm
      --enable-auto-tool-choice
      --tool-call-parser hermes
      --reasoning-parser deepseek_r1
    )
    if [ -n "${adapters[$index]}" ]; then
      serve_args+=(--enable-lora --lora-modules "$MODEL_NAME=${adapters[$index]}")
    fi
    (
      cd "$VERL"
      CUDA_VISIBLE_DEVICES=$gpu "$UV" run --no-sync vllm serve "${serve_args[@]}"
    ) >"$LOG_DIR/vitabench_vllm_$tag.log" 2>&1 &
    server_pids+=("$!")
    wave_indices+=("$index")
  done

  for slot in "${!wave_indices[@]}"; do
    local port=$((8140 + slot))
    for _ in $(seq 1 120); do
      if curl -fsS "http://127.0.0.1:$port/v1/models" >/dev/null 2>&1; then
        break
      fi
      sleep 5
    done
    curl -fsS "http://127.0.0.1:$port/v1/models" >/dev/null
  done

  for slot in "${!wave_indices[@]}"; do
    local index=${wave_indices[$slot]}
    local tag=${tags[$index]}
    (
      if MODEL_NAME=$MODEL_NAME RESULT_TAG=onepass_$tag PORT=$((8140 + slot)) \
        BENCHMARK_MODE=one_run VITA_CONCURRENCY=2 \
        bash "$REPO/training/run_vitabench_official.sh"; then
        touch "$MARKER_DIR/vitabench_$tag.complete"
      fi
    ) >"$LOG_DIR/vitabench_$tag.log" 2>&1 &
    eval_pids+=("$!")
  done
  for pid in "${eval_pids[@]}"; do
    wait "$pid" || true
  done
  for pid in "${server_pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${server_pids[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}

run_wave 0 2
run_wave 3 4
touch "$MARKER_DIR/vitabench_all.complete"
"$ROOT/venvs/sft312/bin/python" "$REPO/training/summarize_official_tools.py" \
  --results "$ROOT/results/official" \
  --tag-prefix onepass_ \
  --output "$ROOT/results/official/one_run_summary.md"
