#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
BENCH_ROOT=${BENCHMARK_ROOT:-$ROOT/benchmarks}
MODEL_NAME=${MODEL_NAME:?set MODEL_NAME to the name served by vLLM}
RESULT_TAG=${RESULT_TAG:?set RESULT_TAG}
PORT=${PORT:-8100}
BENCHMARK_MODE=${BENCHMARK_MODE:-formal}
SECRETS_FILE=${EVAL_SECRETS_FILE:-$ROOT/secrets/eval.env}
ATLAS_ROOT=$BENCH_ROOT/mcp-atlas
ATLAS_PYTHON=$ROOT/venvs/mcp-atlas312/bin/python
RESULT_DIR=$ROOT/results/official/mcp_atlas/$RESULT_TAG
SUBSET=$ROOT/data/benchmarks/mcp_atlas_envfactory_291.csv
SMOKE_SUBSET=$ROOT/data/benchmarks/mcp_atlas_smoke_memory_filesystem.csv
KEYLESS_SUBSET=$ROOT/data/benchmarks/mcp_atlas_keyless.csv

test -f "$SECRETS_FILE"
set -a
source "$SECRETS_FILE"
set +a

judge_key=${OPENROUTER_API_KEY:?OPENROUTER_API_KEY is missing from the secrets file}
judge_model=${MCP_ATLAS_JUDGE_MODEL:-${OPENROUTER_MODEL:?OPENROUTER_MODEL is missing from the secrets file}}
judge_base=${MCP_ATLAS_JUDGE_BASE_URL:-${OPENROUTER_BASE_URL%/v1/chat/completions}}

mkdir -p "$RESULT_DIR" "$ROOT/data/benchmarks"
if [ ! -s "$SUBSET" ] || [ ! -s "$SMOKE_SUBSET" ]; then
  "$ATLAS_PYTHON" "$ROOT/repo/training/prepare_mcp_atlas_subset.py" \
    --output "$SUBSET" \
    --smoke-output "$SMOKE_SUBSET"
fi

keyless_servers=${MCP_ATLAS_KEYLESS_SERVERS:-arxiv calculator cli-mcp-server context7 ddg-search desktop-commander fetch filesystem git mcp-code-executor mcp-server-code-runner memory met-museum open-library osm-mcp-server pubmed weather whois}
case "$BENCHMARK_MODE" in
  smoke)
    input=$SMOKE_SUBSET
    concurrency=1
    ;;
  one_run)
    input=$KEYLESS_SUBSET
    concurrency=${MCP_ATLAS_CONCURRENCY:-5}
    if [ ! -s "$input" ]; then
      "$ATLAS_PYTHON" "$ROOT/repo/training/prepare_mcp_atlas_keyless_subset.py" \
        --input "$SUBSET" \
        --output "$input" \
        --servers $keyless_servers
    fi
    ;;
  formal)
    input=$SUBSET
    concurrency=${MCP_ATLAS_CONCURRENCY:-5}
    ;;
  *)
    echo "unknown BENCHMARK_MODE: $BENCHMARK_MODE" >&2
    exit 2
    ;;
esac
temperature=${MCP_ATLAS_TEMPERATURE:-0.7}

container=${MCP_ATLAS_CONTAINER:-agentworld-mcp-atlas}
sandbox_port=${MCP_ATLAS_SANDBOX_PORT:-1984}
harness_port=${MCP_ATLAS_HARNESS_PORT:-3001}
docker_args=(
  --name "$container"
  -p "$sandbox_port:1984"
  -e "UV_INDEX_URL=${MCP_ATLAS_UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}"
  -e UV_LINK_MODE=copy
  --mount source=agentworld-mcp-atlas-uv-cache,target=/root/.cache/uv
  --mount type=bind,source="$ATLAS_ROOT/services/agent-environment/src/agent_environment/mcp_server_template.json",target=/agent-environment/src/agent_environment/mcp_server_template.json,readonly
)
if [ "$BENCHMARK_MODE" = formal ]; then
  atlas_env=${MCP_ATLAS_ENV_FILE:-$ROOT/secrets/mcp-atlas.env}
  test -f "$atlas_env"
  health_servers=${MCP_ATLAS_SERVERS:-airtable alchemy arxiv calculator cli-mcp-server clinicaltrialsgov-mcp-server context7 ddg-search desktop-commander e2b-server exa fetch filesystem git github google-maps lara-translate mcp-code-executor mcp-server-code-runner memory met-museum national-parks notion open-library osm-mcp-server pubmed twelvedata weather weather-data whois}
  docker_args+=(--env-file "$atlas_env" -e "ENABLED_SERVERS=${health_servers// /,}")
  health_env_link=0
  if [ ! -e "$ATLAS_ROOT/.env" ]; then
    ln -s "$atlas_env" "$ATLAS_ROOT/.env"
    health_env_link=1
  fi
elif [ "$BENCHMARK_MODE" = one_run ]; then
  docker_args+=(-e "ENABLED_SERVERS=${keyless_servers// /,}")
  health_servers=$keyless_servers
  health_env_link=0
else
  docker_args+=(-e ENABLED_SERVERS=filesystem,memory)
  health_servers="filesystem memory"
  health_env_link=0
fi
cleanup() {
  docker logs "$container" >"$RESULT_DIR/sandbox.log" 2>&1 || true
  kill "${harness_pid:-}" 2>/dev/null || true
  wait "${harness_pid:-}" 2>/dev/null || true
  docker rm -f "$container" >/dev/null 2>&1 || true
  if [ "$health_env_link" = 1 ]; then
    rm -f "$ATLAS_ROOT/.env"
  fi
}
trap cleanup EXIT

docker rm -f "$container" >/dev/null 2>&1 || true
docker run -d "${docker_args[@]}" agent-environment:latest >/dev/null

for _ in $(seq 1 600); do
  if curl -fsS "http://127.0.0.1:$sandbox_port/enabled-servers" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS "http://127.0.0.1:$sandbox_port/enabled-servers" >/dev/null

export LLM_API_KEY=EMPTY
export LLM_BASE_URL=http://127.0.0.1:$PORT
export MCP_SANDBOX_URL=http://127.0.0.1:$sandbox_port
export PORT=$harness_port
export HARNESS_URL=http://127.0.0.1:$harness_port
export EVAL_LLM_API_KEY=$judge_key
export EVAL_LLM_BASE_URL=$judge_base
export EVAL_LLM_MODEL=$judge_model

node "$ATLAS_ROOT/services/agent-harness/dist/index.js" >"$RESULT_DIR/harness.log" 2>&1 &
harness_pid=$!
for _ in $(seq 1 60); do
  if curl -fsS "$HARNESS_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

cd "$ATLAS_ROOT"
for server in $health_servers; do
  "$ATLAS_PYTHON" services/mcp_eval/test_servers.py --server "$server"
done
"$ATLAS_PYTHON" run_eval.py \
  --model "$MODEL_NAME" \
  --input "$input" \
  --output "$RESULT_DIR/outputs.csv" \
  --concurrency "$concurrency" \
  --extra-llm-params "$(printf '{\"temperature\":%s,\"top_p\":1.0}' "$temperature")" \
  --skip-health-check

"$ATLAS_PYTHON" services/scoring/score_claims.py \
  --groundtruth-file "$input" \
  --model-file "$RESULT_DIR/outputs.csv" \
  --model-name "$RESULT_TAG" \
  --output-dir "$RESULT_DIR/scored" \
  --evaluator-model "$judge_model" \
  --base-url "$judge_base"
