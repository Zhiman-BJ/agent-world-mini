#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
MODEL_NAME=${MODEL_NAME:?set MODEL_NAME to the name served by vLLM}
RESULT_TAG=${RESULT_TAG:?set RESULT_TAG}
PORT=${PORT:-8100}
BENCHMARK_MODE=${BENCHMARK_MODE:-one_run}
VITA_ROOT=$ROOT/benchmarks/vitabench

set -a
source "$ROOT/secrets/eval.env"
set +a
user_model=${VITA_USER_MODEL:-${OPENROUTER_MODEL:?OPENROUTER_MODEL is missing from eval.env}}
export VITA_AGENT_ENDPOINT=http://127.0.0.1:$PORT/v1/chat/completions
export VITA_MODEL_CONFIG_PATH=$REPO/training/vitabench_models.yaml

case "$BENCHMARK_MODE" in
  smoke)
    domains=${VITA_DOMAINS:-delivery}
    task_limit=(--num-tasks "${VITA_NUM_TASKS:-1}")
    ;;
  one_run)
    domains=${VITA_DOMAINS:-delivery instore ota}
    task_limit=()
    ;;
  *)
    echo "unknown BENCHMARK_MODE: $BENCHMARK_MODE" >&2
    exit 2
    ;;
esac

mkdir -p "$ROOT/results/official/vitabench/$RESULT_TAG"
source "$ROOT/venvs/vitabench312/bin/activate"
cd "$VITA_ROOT"
for domain in $domains; do
  vita run \
    --domain "$domain" \
    --agent-llm "$MODEL_NAME" \
    --user-llm "$user_model" \
    --evaluator-llm "$user_model" \
    --enable-think \
    --evaluation-type trajectory \
    --num-trials 1 \
    --max-steps 300 \
    --max-concurrency "${VITA_CONCURRENCY:-2}" \
    --language english \
    --save-to "$ROOT/results/official/vitabench/$RESULT_TAG/$domain/results.json" \
    --csv-output "$ROOT/results/official/vitabench/$RESULT_TAG/summary.csv" \
    "${task_limit[@]}"
done
