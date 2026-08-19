#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
VERL=${VERL_ROOT:-$ROOT/verl}
UV=${UV_BIN:-$ROOT/venvs/bootstrap312/bin/uv}
BENCHMARK=${BENCHMARK:?set BENCHMARK to bfcl, mcpmark, tau2, mcp-atlas, or vitabench}
RESULT_TAG=${RESULT_TAG:?set RESULT_TAG}
MODEL_PATH=${MODEL_PATH:?set MODEL_PATH}
LORA_PATH=${LORA_PATH:-}
PORT=${PORT:-8100}
MODEL_NAME=agentworld-$RESULT_TAG

# BFCL's Qwen FC handler sends the registry model name in each request.
# Keep that expected alias while result directories still use RESULT_TAG.
if [ "$BENCHMARK" = bfcl ] || [ "$BENCHMARK" = vitabench ]; then
  MODEL_NAME=Qwen/Qwen3-8B
fi

export HF_HOME=$ROOT/hf
export TMPDIR=$ROOT/tmp
export UV_CACHE_DIR=$ROOT/uv-cache
export XDG_CACHE_HOME=$ROOT/cache
export TORCH_HOME=$ROOT/torch-cache
export TRITON_CACHE_DIR=$ROOT/triton-cache

serve_args=(
  --model "$MODEL_PATH"
  --served-model-name "$MODEL_NAME"
  --host 127.0.0.1
  --port "$PORT"
  --tensor-parallel-size 1
  --gpu-memory-utilization 0.85
  --max-model-len 40960
  --generation-config vllm
  --enable-auto-tool-choice
  --tool-call-parser hermes
  --reasoning-parser deepseek_r1
)
if [ -n "$LORA_PATH" ]; then
  serve_args+=(--enable-lora --lora-modules "$MODEL_NAME=$LORA_PATH")
fi

mkdir -p "$ROOT/results/official/logs"
cd "$VERL"
"$UV" run --no-sync vllm serve "${serve_args[@]}" \
  >"$ROOT/results/official/logs/${BENCHMARK}_${RESULT_TAG}_vllm.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
curl -fsS "http://127.0.0.1:$PORT/v1/models" >/dev/null

export MODEL_NAME RESULT_TAG PORT
case "$BENCHMARK" in
  bfcl) bash "$REPO/training/run_bfcl_official.sh" ;;
  mcpmark) bash "$REPO/training/run_mcpmark_official.sh" ;;
  tau2) bash "$REPO/training/run_tau2_official.sh" ;;
  mcp-atlas) bash "$REPO/training/run_mcp_atlas_official.sh" ;;
  vitabench) bash "$REPO/training/run_vitabench_official.sh" ;;
  *) echo "unknown benchmark: $BENCHMARK" >&2; exit 2 ;;
esac
