#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
BENCH_ROOT=${BENCHMARK_ROOT:-$ROOT/benchmarks}
UV=${UV_BIN:-$ROOT/venvs/bootstrap312/bin/uv}
TARGET=${1:-all}

ensure_repo() {
  local path=$1
  local url=$2
  if [ ! -d "$path/.git" ]; then
    mkdir -p "$BENCH_ROOT"
    git clone "$url" "$path"
  fi
  printf '%s %s\n' "$(basename "$path")" "$(git -C "$path" rev-parse HEAD)"
}

install_mcpmark() {
  local repo=$BENCH_ROOT/mcpmark
  local venv=$ROOT/venvs/mcpmark312
  ensure_repo "$repo" https://github.com/eval-sys/mcpmark.git
  if ! grep -q MCPMARK_MAX_TOKENS "$repo/src/agents/mcpmark_agent.py"; then
    git -C "$repo" apply "$ROOT/repo/training/patches/mcpmark_configurable_output.patch"
  fi
  if [ ! -x "$venv/bin/python" ]; then
    "$UV" venv --python 3.12 "$venv"
  fi
  "$UV" pip install --python "$venv/bin/python" -e "$repo"
}

install_tau2() {
  local repo=$BENCH_ROOT/tau2-bench
  ensure_repo "$repo" https://github.com/sierra-research/tau2-bench.git
  if ! grep -q "stream_chunk_builder" "$repo/src/tau2/utils/llm_utils.py"; then
    git -C "$repo" apply "$ROOT/repo/training/patches/tau2_streaming_transport.patch"
  fi
  if ! grep -q "TAU2_NL_ASSERTIONS_MODEL" "$repo/src/tau2/config.py"; then
    git -C "$repo" apply "$ROOT/repo/training/patches/tau2_configurable_evaluator.patch"
  fi
  "$UV" sync --directory "$repo"
}

install_mcp_atlas() {
  local repo=$BENCH_ROOT/mcp-atlas
  local venv=$ROOT/venvs/mcp-atlas312
  ensure_repo "$repo" https://github.com/scaleapi/mcp-atlas.git
  if ! grep -q "errors and not final" "$repo/run_eval.py"; then
    git -C "$repo" apply "$ROOT/repo/training/patches/mcp_atlas_error_rows.patch"
  fi
  if ! grep -q '"mcp<2"' "$repo/services/agent-environment/src/agent_environment/mcp_server_template.json"; then
    git -C "$repo" apply "$ROOT/repo/training/patches/mcp_atlas_mcp1_uvx.patch"
  fi
  if ! grep -q "Streaming keeps the proxy connection active" "$repo/services/scoring/score_claims.py"; then
    git -C "$repo" apply "$ROOT/repo/training/patches/mcp_atlas_streaming_judge.patch"
  fi
  if [ ! -x "$venv/bin/python" ]; then
    "$UV" venv --python 3.12 "$venv"
  fi
  "$UV" pip install --python "$venv/bin/python" -r "$repo/requirements.txt"
  npm --prefix "$repo/services/agent-harness" ci
  npm --prefix "$repo/services/agent-harness" run build
  if ! docker image inspect ghcr.io/scaleapi/mcp-atlas:1.2.7 >/dev/null 2>&1; then
    docker pull ghcr.io/scaleapi/mcp-atlas:1.2.7
  fi
  docker tag ghcr.io/scaleapi/mcp-atlas:1.2.7 agent-environment:latest
}

install_vitabench() {
  local repo=$BENCH_ROOT/vitabench
  local venv=$ROOT/venvs/vitabench312
  ensure_repo "$repo" https://github.com/meituan-longcat/vitabench.git
  if ! grep -q "consume_streaming_response" "$repo/src/vita/utils/llm_utils.py"; then
    git -C "$repo" apply "$ROOT/repo/training/patches/vitabench_streaming_transport.patch"
  fi
  if [ ! -x "$venv/bin/python" ]; then
    "$UV" venv --python 3.12 "$venv"
  fi
  "$UV" pip install --python "$venv/bin/python" -e "$repo"
}

case "$TARGET" in
  mcpmark) install_mcpmark ;;
  tau2) install_tau2 ;;
  mcp-atlas) install_mcp_atlas ;;
  vitabench) install_vitabench ;;
  all)
    install_mcpmark
    install_tau2
    install_mcp_atlas
    install_vitabench
    ;;
  *)
    echo "usage: $0 [mcpmark|tau2|mcp-atlas|vitabench|all]" >&2
    exit 2
    ;;
esac
