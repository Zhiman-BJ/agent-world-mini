#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
MODEL_NAME=${MODEL_NAME:?set MODEL_NAME to the served name}
RESULT_TAG=${RESULT_TAG:?set RESULT_TAG}
PORT=${PORT:-8000}
BFCL_MODEL=${BFCL_MODEL:-Qwen/Qwen3-8B-FC}
CATEGORIES=${BFCL_CATEGORIES:-simple_python,multiple,parallel,parallel_multiple,irrelevance,multi_turn_base}
export BFCL_PROJECT_ROOT=$ROOT/results/bfcl/$RESULT_TAG
export REMOTE_OPENAI_BASE_URL=http://127.0.0.1:$PORT/v1
export REMOTE_OPENAI_API_KEY=EMPTY
export REMOTE_OPENAI_TOKENIZER_PATH=$ROOT/models/Qwen3-8B
mkdir -p "$BFCL_PROJECT_ROOT"
source "$ROOT/venvs/eval312/bin/activate"
bfcl generate --model "$BFCL_MODEL" --test-category "$CATEGORIES" --skip-server-setup --num-threads 16
bfcl evaluate --model "$BFCL_MODEL" --test-category "$CATEGORIES"
touch "$BFCL_PROJECT_ROOT/.complete"
