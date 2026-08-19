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
POSTGRES_CONTAINER=${MCPMARK_POSTGRES_CONTAINER:-agentworld-mcpmark-postgres}
POSTGRES_PORT=${MCPMARK_POSTGRES_PORT:-5433}

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
gpus=(0 1 2 3 4)
ports=(8100 8101 8102 8103 8104)
server_pids=()
created_postgres=0

cleanup() {
  for pid in "${server_pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${server_pids[@]:-}"; do
    wait "$pid" 2>/dev/null || true
  done
  if [ "$created_postgres" = 1 ]; then
    docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for index in "${!tags[@]}"; do
  tag=${tags[$index]}
  serve_args=(
    --model "${models[$index]}"
    --served-model-name "$MODEL_NAME"
    --host 127.0.0.1
    --port "${ports[$index]}"
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
    CUDA_VISIBLE_DEVICES=${gpus[$index]} "$UV" run --no-sync vllm serve "${serve_args[@]}"
  ) >"$LOG_DIR/vllm_$tag.log" 2>&1 &
  server_pids+=("$!")
done

for index in "${!tags[@]}"; do
  for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:${ports[$index]}/v1/models" >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done
  curl -fsS "http://127.0.0.1:${ports[$index]}/v1/models" >/dev/null
done

run_parallel_phase() {
  local benchmark=$1
  local script=$2
  local extra_env=$3
  local pids=()
  local labels=()
  for index in "${!tags[@]}"; do
    local tag=${tags[$index]}
    local result_tag=onepass_$tag
    local marker=$MARKER_DIR/${benchmark}_$tag.complete
    if [ -f "$marker" ]; then
      echo "[$benchmark][$tag] keeping completed result"
      continue
    fi
    (
      export MODEL_NAME RESULT_TAG=$result_tag PORT=${ports[$index]} BENCHMARK_MODE=one_run
      eval "export $extra_env"
      bash "$REPO/training/$script"
      touch "$marker"
    ) >"$LOG_DIR/${benchmark}_$tag.log" 2>&1 &
    pids+=("$!")
    labels+=("$tag")
  done
  local failed=0
  for index in "${!pids[@]}"; do
    if wait "${pids[$index]}"; then
      echo "[$benchmark][${labels[$index]}] complete"
    else
      echo "[$benchmark][${labels[$index]}] failed; see $LOG_DIR/${benchmark}_${labels[$index]}.log" >&2
      failed=1
    fi
  done
  return "$failed"
}

# BFCL and tau2 keep independent state per process, so all five models can use
# separate GPUs at the same time. This is one complete run, not a task sample.
run_parallel_phase bfcl run_bfcl_official.sh "BFCL_RUNS=1" || true
run_parallel_phase tau2 run_tau2_official.sh "TAU2_TRIALS=1 TAU2_CONCURRENCY=1 TAU2_USER_STREAM=1" || true

# MCP-Mark mutates shared service state. Keep model evaluations sequential while
# still retaining all tasks from each locally runnable service.
if ! docker ps --format '{{.Names}}' | grep -Fxq "$POSTGRES_CONTAINER"; then
  docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$POSTGRES_CONTAINER" \
    -e POSTGRES_PASSWORD=password -p "$POSTGRES_PORT:5432" postgres:16-alpine >/dev/null
  created_postgres=1
fi
for _ in $(seq 1 60); do
  if docker exec "$POSTGRES_CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

for index in "${!tags[@]}"; do
  tag=${tags[$index]}
  marker=$MARKER_DIR/mcpmark_$tag.complete
  if [ -f "$marker" ]; then
    echo "[mcpmark][$tag] keeping completed result"
    continue
  fi
  if MODEL_NAME=$MODEL_NAME RESULT_TAG=onepass_$tag PORT=${ports[$index]} \
    BENCHMARK_MODE=one_run MCPMARK_RUNS=1 POSTGRES_PORT=$POSTGRES_PORT \
    POSTGRES_PASSWORD=password \
    bash "$REPO/training/run_mcpmark_official.sh" \
    >"$LOG_DIR/mcpmark_$tag.log" 2>&1; then
    touch "$marker"
    echo "[mcpmark][$tag] complete"
  else
    echo "[mcpmark][$tag] failed; see $LOG_DIR/mcpmark_$tag.log" >&2
  fi
done

# Each MCP-Atlas process gets its own sandbox container and ports, so the five
# keyless subsets are isolated and can run in parallel.
atlas_pids=()
atlas_labels=()
for index in "${!tags[@]}"; do
  tag=${tags[$index]}
  marker=$MARKER_DIR/mcp_atlas_$tag.complete
  if [ -f "$marker" ]; then
    echo "[mcp-atlas][$tag] keeping completed result"
    continue
  fi
  (
    MODEL_NAME=$MODEL_NAME RESULT_TAG=onepass_$tag PORT=${ports[$index]} \
      BENCHMARK_MODE=one_run MCP_ATLAS_CONCURRENCY=2 \
      MCP_ATLAS_CONTAINER=agentworld-mcp-atlas-$tag \
      MCP_ATLAS_SANDBOX_PORT=$((1984 + index)) \
      MCP_ATLAS_HARNESS_PORT=$((3001 + index)) \
      bash "$REPO/training/run_mcp_atlas_official.sh"
    touch "$marker"
  ) >"$LOG_DIR/mcp_atlas_$tag.log" 2>&1 &
  atlas_pids+=("$!")
  atlas_labels+=("$tag")
done
for index in "${!atlas_pids[@]}"; do
  if wait "${atlas_pids[$index]}"; then
    echo "[mcp-atlas][${atlas_labels[$index]}] complete"
  else
    echo "[mcp-atlas][${atlas_labels[$index]}] failed; see $LOG_DIR/mcp_atlas_${atlas_labels[$index]}.log" >&2
  fi
done

"$ROOT/venvs/sft312/bin/python" "$REPO/training/summarize_official_tools.py" \
  --results "$ROOT/results/official" \
  --tag-prefix onepass_ \
  --output "$ROOT/results/official/one_run_summary.md"
