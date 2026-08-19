#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
MODEL_NAME=${MODEL_NAME:?set MODEL_NAME to the name served by vLLM}
RESULT_TAG=${RESULT_TAG:?set RESULT_TAG}
PORT=${PORT:-8100}
BENCHMARK_MODE=${BENCHMARK_MODE:-formal}
SECRETS_FILE=${EVAL_SECRETS_FILE:-$ROOT/secrets/eval.env}

case "$BENCHMARK_MODE" in
  smoke)
    categories=${BFCL_CATEGORIES:-simple_python}
    runs=${BFCL_RUNS:-1}
    ;;
  one_run)
    # Agent-World's BFCL V4 suite, excluding only the two SerpAPI-backed
    # Web Search categories that cannot run without SERPAPI_API_KEY.
    categories=${BFCL_CATEGORIES:-simple_python,simple_java,simple_javascript,multiple,parallel,parallel_multiple,irrelevance,live_simple,live_multiple,live_parallel,live_parallel_multiple,live_irrelevance,live_relevance,multi_turn_base,multi_turn_miss_func,multi_turn_miss_param,multi_turn_long_context,memory_kv,memory_vector,memory_rec_sum}
    runs=${BFCL_RUNS:-1}
    ;;
  formal)
    categories=${BFCL_CATEGORIES:-all_scoring}
    runs=${BFCL_RUNS:-8}
    test -f "$SECRETS_FILE"
    set -a
    source "$SECRETS_FILE"
    set +a
    : "${SERPAPI_API_KEY:?SERPAPI_API_KEY is missing from the secrets file}"
    ;;
  *)
    echo "unknown BENCHMARK_MODE: $BENCHMARK_MODE" >&2
    exit 2
    ;;
esac

source "$ROOT/venvs/eval312/bin/activate"
for run in $(seq 1 "$runs"); do
  export BFCL_PROJECT_ROOT=$ROOT/results/official/bfcl_v4/$RESULT_TAG/run-$run
  export REMOTE_OPENAI_BASE_URL=http://127.0.0.1:$PORT/v1
  export REMOTE_OPENAI_API_KEY=EMPTY
  export REMOTE_OPENAI_TOKENIZER_PATH=$ROOT/models/Qwen3-8B
  mkdir -p "$BFCL_PROJECT_ROOT"
  if [ -f "$BFCL_PROJECT_ROOT/.complete" ]; then
    echo "[BFCL V4][$RESULT_TAG][run-$run] keeping completed result"
    continue
  fi
  bfcl generate \
    --model Qwen/Qwen3-8B-FC \
    --test-category "$categories" \
    --temperature 1.0 \
    --skip-server-setup \
    --num-threads 16
  bfcl evaluate --model Qwen/Qwen3-8B-FC --test-category "$categories"
  touch "$BFCL_PROJECT_ROOT/.complete"
done
