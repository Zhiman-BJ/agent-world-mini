#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
port="${1:-8765}"
url="http://localhost:${port}/scripts/tool_graph_results.html"
server_pid=""

cleanup() {
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid"
  fi
  [[ -z "$server_pid" ]] || wait "$server_pid" 2>/dev/null || true
}

trap cleanup EXIT
trap 'exit 0' INT TERM HUP

python -m http.server "$port" --bind 127.0.0.1 --directory "$repo_root" >/dev/null &
server_pid=$!

printf 'Viewer: %s\n' "$url"
printf 'Press Ctrl+C to stop and clean up.\n'
wait "$server_pid"
