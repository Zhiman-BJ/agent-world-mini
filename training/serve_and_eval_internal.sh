#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
VERL=${VERL_ROOT:-$ROOT/verl}
BASE_MODEL=${BASE_MODEL:-$ROOT/models/Qwen3-8B}
MODEL_NAME=${MODEL_NAME:?set MODEL_NAME}
MODEL_PATH=${MODEL_PATH:-$BASE_MODEL}
LORA_PATH=${LORA_PATH:-}
PORT=${PORT:-8000}
TP_SIZE=${TP_SIZE:-1}
RESULT_TAG=${RESULT_TAG:-$MODEL_NAME}
UV=${UV_BIN:-$ROOT/venvs/bootstrap312/bin/uv}
SKIP_EXISTING=${SKIP_EXISTING:-0}
RUN_PASS1=${RUN_PASS1:-1}
RUN_PASS5=${RUN_PASS5:-1}
export HF_HOME=$ROOT/hf
export TMPDIR=$ROOT/tmp
export PYTHONPATH=$REPO:${PYTHONPATH:-}
export UV_CACHE_DIR=$ROOT/uv-cache
export XDG_CACHE_HOME=$ROOT/cache
export TORCH_HOME=$ROOT/torch-cache
export TRITON_CACHE_DIR=$ROOT/triton-cache

eval_limit_args=()
if [ -n "${EVAL_LIMIT:-}" ]; then
  eval_limit_args+=(--limit "$EVAL_LIMIT")
fi

serve_args=(
  --model "$MODEL_PATH"
  --served-model-name "$MODEL_NAME"
  --host 127.0.0.1
  --port "$PORT"
  --tensor-parallel-size "$TP_SIZE"
  --gpu-memory-utilization 0.85
  --max-model-len 32768
  --enable-auto-tool-choice
  --tool-call-parser hermes
  --reasoning-parser deepseek_r1
)
if [ -n "$LORA_PATH" ]; then
  serve_args+=(--enable-lora --lora-modules "$MODEL_NAME=$LORA_PATH")
fi

cd "$VERL"
"$UV" run --no-sync vllm serve "${serve_args[@]}" \
  >"$ROOT/results/${RESULT_TAG}_vllm.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$PORT/v1/models" >/dev/null; then
    break
  fi
  sleep 5
done
curl -fsS "http://127.0.0.1:$PORT/v1/models" >/dev/null

pass1_output="$ROOT/results/${RESULT_TAG}_internal_pass1.json"
if [ "$RUN_PASS1" = 1 ]; then
  if [ "$SKIP_EXISTING" = 1 ] && [ -s "$pass1_output" ]; then
    echo "[$RESULT_TAG] keeping completed internal pass@1"
  else
    "$ROOT/venvs/sft312/bin/python" "$REPO/training/evaluate_internal.py" \
      --data-root "$ROOT/data/agentworld_120" \
      --model "$MODEL_NAME" \
      --base-url "http://127.0.0.1:$PORT/v1" \
      --runs 1 \
      --temperature 0 \
      "${eval_limit_args[@]}" \
      --output "$pass1_output"
  fi
fi

pass5_output="$ROOT/results/${RESULT_TAG}_internal_pass5.json"
if [ "$RUN_PASS5" = 1 ]; then
  if [ "$SKIP_EXISTING" = 1 ] && [ -s "$pass5_output" ]; then
    echo "[$RESULT_TAG] keeping completed internal pass@5"
  else
    "$ROOT/venvs/sft312/bin/python" "$REPO/training/evaluate_internal.py" \
      --data-root "$ROOT/data/agentworld_120" \
      --model "$MODEL_NAME" \
      --base-url "http://127.0.0.1:$PORT/v1" \
      --runs 5 \
      --temperature 0.7 \
      "${eval_limit_args[@]}" \
      --output "$pass5_output"
  fi
fi

if [ "${RUN_BFCL:-0}" = 1 ]; then
  bfcl_marker="$ROOT/results/bfcl/$RESULT_TAG/.complete"
  if [ "$SKIP_EXISTING" = 1 ] && [ -f "$bfcl_marker" ]; then
    echo "[$RESULT_TAG] keeping completed BFCL result"
  else
    MODEL_NAME="$MODEL_NAME" RESULT_TAG="$RESULT_TAG" PORT="$PORT" "$REPO/training/run_bfcl.sh"
  fi
fi
