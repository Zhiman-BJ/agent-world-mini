#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
BENCH_ROOT=${BENCHMARK_ROOT:-$ROOT/benchmarks}
MODEL_NAME=${MODEL_NAME:?set MODEL_NAME to the name served by vLLM}
RESULT_TAG=${RESULT_TAG:?set RESULT_TAG}
PORT=${PORT:-8100}
BENCHMARK_MODE=${BENCHMARK_MODE:-formal}
SECRETS_FILE=${EVAL_SECRETS_FILE:-$ROOT/secrets/eval.env}
TAU_ROOT=$BENCH_ROOT/tau2-bench

test -f "$SECRETS_FILE"
set -a
source "$SECRETS_FILE"
set +a

user_key=${OPENROUTER_API_KEY:?OPENROUTER_API_KEY is missing from the secrets file}
user_model=${TAU2_USER_MODEL:-openai/${OPENROUTER_MODEL:?OPENROUTER_MODEL is missing from the secrets file}}
user_base=${TAU2_USER_BASE_URL:-${OPENROUTER_BASE_URL%/chat/completions}}
export OPENAI_API_KEY=$user_key
export OPENAI_API_BASE=$user_base
export TAU2_NL_ASSERTIONS_MODEL=$user_model
export TAU2_NL_ASSERTIONS_STREAM=${TAU2_USER_STREAM:-1}
request_timeout=${TAU2_REQUEST_TIMEOUT:-180}
export TAU2_NL_ASSERTIONS_TIMEOUT=$request_timeout

case "$BENCHMARK_MODE" in
  smoke)
    domains=${TAU2_DOMAINS:-retail}
    trials=${TAU2_TRIALS:-1}
    task_limit=(--num-tasks "${TAU2_NUM_TASKS:-1}")
    ;;
  one_run)
    domains=${TAU2_DOMAINS:-retail telecom airline}
    trials=${TAU2_TRIALS:-1}
    task_limit=()
    ;;
  formal)
    domains=${TAU2_DOMAINS:-retail telecom airline}
    trials=${TAU2_TRIALS:-8}
    task_limit=()
    ;;
  *)
    echo "unknown BENCHMARK_MODE: $BENCHMARK_MODE" >&2
    exit 2
    ;;
esac

agent_args=$(printf '{"temperature":1.0,"top_p":1.0,"api_base":"http://127.0.0.1:%s/v1","api_key":"EMPTY"}' "$PORT")
if [ "${TAU2_USER_STREAM:-1}" = 1 ]; then
  user_args=$(printf '{"temperature":0.0,"stream":true,"timeout":%s}' "$request_timeout")
else
  user_args=$(printf '{"temperature":0.0,"timeout":%s}' "$request_timeout")
fi

for domain in $domains; do
  "$ROOT/venvs/bootstrap312/bin/uv" run --directory "$TAU_ROOT" tau2 run \
    --domain "$domain" \
    --task-split-name base \
    --agent-llm "openai/$MODEL_NAME" \
    --agent-llm-args "$agent_args" \
    --user-llm "$user_model" \
    --user-llm-args "$user_args" \
    --num-trials "$trials" \
    --max-concurrency "${TAU2_CONCURRENCY:-3}" \
    --max-steps 200 \
    --seed 300 \
    --auto-resume \
    --save-to "$ROOT/results/official/tau2/$RESULT_TAG/$domain" \
    "${task_limit[@]}"
done
