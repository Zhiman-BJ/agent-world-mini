#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
BENCH_ROOT=${BENCHMARK_ROOT:-$ROOT/benchmarks}
MODEL_NAME=${MODEL_NAME:?set MODEL_NAME to the name served by vLLM}
RESULT_TAG=${RESULT_TAG:?set RESULT_TAG}
PORT=${PORT:-8100}
BENCHMARK_MODE=${BENCHMARK_MODE:-formal}
MCPMARK_ROOT=$BENCH_ROOT/mcpmark

case "$BENCHMARK_MODE" in
  smoke)
    services=${MCPMARK_SERVICES:-filesystem}
    tasks=${MCPMARK_TASKS:-file_property/size_classification}
    runs=${MCPMARK_RUNS:-1}
    ;;
  one_run)
    # GitHub and Notion require credentials that are not configured on the
    # evaluation server. Run every task from the three self-contained services.
    services=${MCPMARK_SERVICES:-filesystem playwright postgres}
    tasks=${MCPMARK_TASKS:-all}
    runs=${MCPMARK_RUNS:-1}
    ;;
  formal)
    services=${MCPMARK_SERVICES:-filesystem github notion playwright postgres}
    tasks=${MCPMARK_TASKS:-all}
    runs=${MCPMARK_RUNS:-8}
    ;;
  *)
    echo "unknown BENCHMARK_MODE: $BENCHMARK_MODE" >&2
    exit 2
    ;;
esac

export OPENAI_API_KEY=EMPTY
export OPENAI_BASE_URL=http://127.0.0.1:$PORT/v1
export OPENAI_API_BASE=$OPENAI_BASE_URL
export FILESYSTEM_TEST_ROOT=${FILESYSTEM_TEST_ROOT:-$MCPMARK_ROOT/test_environments}
export PLAYWRIGHT_HEADLESS=True
export MCPMARK_MAX_TOKENS=${MCPMARK_MAX_TOKENS:-8192}

source "$ROOT/venvs/mcpmark312/bin/activate"
cd "$MCPMARK_ROOT"
for service in $services; do
  python -m pipeline \
    --exp-name "agentworld-$RESULT_TAG" \
    --mcp "$service" \
    --models "openai/$MODEL_NAME" \
    --tasks "$tasks" \
    --task-suite standard \
    --k "$runs" \
    --compaction-token "${MCPMARK_COMPACTION_TOKEN:-32000}" \
    --output-dir "$ROOT/results/official/mcpmark"
done
