#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}

run_stage() {
  local name=$1
  shift
  local started
  started=$(date +%s)
  echo "[$(date --iso-8601=seconds)] START $name"
  "$@"
  echo "[$(date --iso-8601=seconds)] DONE  $name ($(( $(date +%s) - started )) seconds)"
}

cd "$REPO"
run_stage lora_tool_only "$REPO/training/run_sft.sh" training/configs/lora_tool_only.yaml
run_stage lora_mixed "$REPO/training/run_sft.sh" training/configs/lora_mixed.yaml
run_stage full_sft_mixed "$REPO/training/run_sft.sh" training/configs/full_sft_mixed.yaml
run_stage grpo_lora_mixed "$REPO/training/run_grpo.sh"
run_stage all_evaluations "$REPO/training/run_all_evals.sh"

echo "Training and evaluation finished. Results are under $ROOT/results."
