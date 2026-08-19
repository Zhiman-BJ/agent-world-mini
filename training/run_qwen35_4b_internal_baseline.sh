#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
VERL=${VERL_ROOT:-$ROOT/verl}
UV=${UV_BIN:-$ROOT/venvs/bootstrap312/bin/uv}
MODEL=${QWEN35_MODEL_PATH:-$ROOT/models/Qwen3.5-4B}
MODEL_NAME=${QWEN35_MODEL_NAME:-Qwen/Qwen3.5-4B}
GPU=${QWEN35_BASELINE_GPU:-0}
PORT=${QWEN35_BASELINE_PORT:-8157}
LOG_DIR=$ROOT/results/logs

mkdir -p "$LOG_DIR"

cleanup() {
  fuser -k -TERM "${PORT}/tcp" >/dev/null 2>&1 || true
}
trap cleanup EXIT

test -s "$MODEL/config.json"
test -s "$MODEL/model.safetensors.index.json"

while nvidia-smi -i "$GPU" --query-compute-apps=pid --format=csv,noheader | grep -q '[0-9]'; do
  sleep 30
done

cleanup
(
  cd "$VERL"
  CUDA_VISIBLE_DEVICES="$GPU" "$UV" run --no-sync vllm serve \
    --model "$MODEL" \
    --served-model-name "$MODEL_NAME" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.85 \
    --max-model-len 40960 \
    --generation-config vllm \
    --language-model-only \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --reasoning-parser qwen3
) >"$LOG_DIR/qwen35_4b_vllm.log" 2>&1 &

for _ in $(seq 1 180); do
  models=$(curl -fsS "http://127.0.0.1:${PORT}/v1/models" 2>/dev/null || true)
  if [[ "$models" == *"$MODEL"* ]]; then
    break
  fi
  sleep 5
done
models=$(curl -fsS "http://127.0.0.1:${PORT}/v1/models")
[[ "$models" == *"$MODEL"* ]]

PYTHONPATH=$REPO "$ROOT/venvs/sft312/bin/python" "$REPO/training/evaluate_internal.py" \
  --data-root "$ROOT/data/agentworld_120" \
  --split test \
  --base-url "http://127.0.0.1:${PORT}/v1" \
  --model "$MODEL_NAME" \
  --runs 1 \
  --workers 1 \
  --temperature 0 \
  --limit 1 \
  --max-steps 16 \
  --output "$ROOT/results/qwen35_4b_internal_preflight1.json" \
  >"$LOG_DIR/qwen35_4b_internal_preflight.log" 2>&1

PYTHONPATH=$REPO "$ROOT/venvs/sft312/bin/python" "$REPO/training/evaluate_internal.py" \
  --data-root "$ROOT/data/agentworld_120" \
  --split test \
  --base-url "http://127.0.0.1:${PORT}/v1" \
  --model "$MODEL_NAME" \
  --runs 1 \
  --workers 4 \
  --temperature 0 \
  --max-steps 16 \
  --output "$ROOT/results/qwen35_4b_internal_pass1.json" \
  >"$LOG_DIR/qwen35_4b_internal.log" 2>&1
