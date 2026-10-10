#!/usr/bin/env bash
set -euo pipefail

REPO=/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006
INPUT=/data1/agent_world/task_program/complete_10_task_environments_20261005
RECOVERY="$ROOT/recovery"
INTERRUPTED_RUN="$ROOT/resumed100/20261006_122606_884861"
INTERRUPTED_RECOVERY="$ROOT/recovery_after_mcp_fix"
TERMINAL_SKIP="$ROOT/recovery_after_mcp_fix/terminal_skip.json"
PROFILE_SWITCH_RUN="$ROOT/resumed100/20261006_160111_341925"
PROFILE_SWITCH_RECOVERY="$ROOT/recovery_before_profile_only"
IO_PRESSURE_RUN="$ROOT/resumed100/20261006_171122_240254"
IO_PRESSURE_RECOVERY="$ROOT/recovery_after_io_guard"
COPY_PRESSURE_RUN="$ROOT/resumed100/20261006_185654_699241"
COPY_PRESSURE_RECOVERY="$ROOT/recovery_after_copy_throttle"
PYTHON=/home/sunshuo/miniconda3/bin/python

mkdir -p \
  "$RECOVERY" \
  "$INTERRUPTED_RECOVERY" \
  "$PROFILE_SWITCH_RECOVERY" \
  "$IO_PRESSURE_RECOVERY" \
  "$COPY_PRESSURE_RECOVERY" \
  "$ROOT/resumed100"
exec >>"$ROOT/recovery_resume.log" 2>&1
cd "$REPO"

echo "[$(date -Iseconds)] recovering completed trajectories"
if jq -e '.status == "completed" and .pending_verification == 0' \
  "$RECOVERY/summary.json" >/dev/null 2>&1; then
  echo "[$(date -Iseconds)] recovery already complete; keeping existing summary"
else
  "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$RECOVERY" \
    --max-concurrency 25 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --run "$ROOT/stage10/20261006_033458_818080" \
    --run "$ROOT/stage25/20261006_042200_391604" \
    --run "$ROOT/stage50/20261006_043056_207748" \
    --run "$ROOT/stage100/20261006_043859_750864" \
    --run "$ROOT/main100/20261006_050040_506389"
fi

echo "[$(date -Iseconds)] recovering the run stopped for sustained I/O pressure"
if jq -e '.status == "completed" and .pending_verification == 0' \
  "$IO_PRESSURE_RECOVERY/summary.json" >/dev/null 2>&1; then
  echo "[$(date -Iseconds)] I/O-pressure recovery already complete"
else
  "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$IO_PRESSURE_RECOVERY" \
    --max-concurrency 2 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --retry-incomplete \
    --run "$IO_PRESSURE_RUN"
fi

echo "[$(date -Iseconds)] recovering the run stopped for state-copy pressure"
if jq -e '.status == "completed" and .pending_verification == 0' \
  "$COPY_PRESSURE_RECOVERY/summary.json" >/dev/null 2>&1; then
  echo "[$(date -Iseconds)] state-copy-pressure recovery already complete"
else
  "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$COPY_PRESSURE_RECOVERY" \
    --max-concurrency 4 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --retry-incomplete \
    --run "$COPY_PRESSURE_RUN"
fi

echo "[$(date -Iseconds)] recovering completed trajectories from the stopped run"
if jq -e '.status == "completed" and .pending_verification == 0' \
  "$INTERRUPTED_RECOVERY/summary.json" >/dev/null 2>&1; then
  echo "[$(date -Iseconds)] stopped-run recovery already complete"
else
  "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$INTERRUPTED_RECOVERY" \
    --max-concurrency 25 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --run "$INTERRUPTED_RUN"
fi

# Completed trajectories and ordinary task failures are terminal. The five
# environment_stopped rows are deliberately excluded so the fixed MCP startup
# path retries them once.
jq '{results: [.results[] | select(.status != "environment_stopped")]}' \
  "$INTERRUPTED_RECOVERY/summary.json" >"$TERMINAL_SKIP"

echo "[$(date -Iseconds)] recovering completed trajectories before profile-only switch"
if jq -e '.status == "completed" and .pending_verification == 0' \
  "$PROFILE_SWITCH_RECOVERY/summary.json" >/dev/null 2>&1; then
  echo "[$(date -Iseconds)] profile-switch recovery already complete"
else
  "$PYTHON" .tmp/recover_interrupted_kimi_batch.py \
    --input-root "$INPUT" \
    --output-root "$PROFILE_SWITCH_RECOVERY" \
    --max-concurrency 25 \
    --model gpt-6.1-sol \
    --reasoning-effort high \
    --run "$PROFILE_SWITCH_RUN"
fi

echo "[$(date -Iseconds)] recovery complete; resuming non-Docker tasks"
set -a
source /home/sunshuo/.config/agent-world/kimi-k3.env
set +a

"$PYTHON" -m distill taskgen \
  --input-root "$INPUT" \
  --output-root "$ROOT/resumed100" \
  --skip-summary /data1/agent_world/task_program/complete_10_task_eval20_kimi_k3_20261005/20261005_172509_592920/summary.json \
  --skip-summary /data1/agent_world/task_program/complete_10_task_eval30_kimi_k3_20261005/20261005_232417_817011/summary.json \
  --skip-summary /data1/agent_world/task_program/complete_10_task_eval3_kimi_k3_threadfix_20261006/20261006_004816_756010/summary.json \
  --skip-summary /data1/agent_world/task_program/complete_10_task_eval50_kimi_k3_20261005/20261005_153325_637182/summary.json \
  --skip-processed-summary "$RECOVERY/summary.json" \
  --skip-summary "$ROOT/probe_mcp_serial/20261006_120337_355708/summary.json" \
  --skip-processed-summary "$TERMINAL_SKIP" \
  --skip-processed-summary "$PROFILE_SWITCH_RECOVERY/summary.json" \
  --skip-processed-summary "$IO_PRESSURE_RECOVERY/summary.json" \
  --skip-processed-summary "$COPY_PRESSURE_RECOVERY/summary.json" \
  --runtime-backend python_profile \
  --runtime-backend host_python \
  --max-concurrency 100 \
  --adaptive-concurrency \
  --min-concurrency 10 \
  --initial-concurrency 25 \
  --verify-after-each \
  --verifier-concurrency 25 \
  --delete-task-state \
  --kimi-bin /data1/agent_world/kimi_code/kimi \
  --model-id kimi-k3 \
  --provider-type kimi \
  --reasoning-effort max \
  --max-context-size 262144 \
  --max-tool-calls 65 \
  --max-environment-errors-per-task 1 \
  --max-upstream-retries 4 \
  --verifier-model gpt-6.1-sol \
  --verifier-reasoning-effort high

echo "[$(date -Iseconds)] all remaining tasks processed"
