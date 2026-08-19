#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}

cd "$REPO"
echo "[$(date --iso-8601=seconds)] SFT preflight"
"$REPO/training/run_sft.sh" training/configs/preflight_lora.yaml

echo "[$(date --iso-8601=seconds)] GRPO preflight"
LORA_ADAPTER_PATH="$ROOT/outputs/preflight_lora" \
GRPO_OUTPUT_DIR="$ROOT/outputs/preflight_grpo" \
GRPO_EXPORT_DIR="$ROOT/outputs/preflight_grpo_hf" \
  "$REPO/training/run_grpo.sh" \
  data.train_batch_size=2 \
  actor_rollout_ref.actor.ppo_mini_batch_size=8 \
  actor_rollout_ref.rollout.n=2 \
  actor_rollout_ref.rollout.agent.num_workers=4 \
  trainer.total_training_steps=1 \
  trainer.val_before_train=False \
  trainer.test_freq=-1

echo "[$(date --iso-8601=seconds)] Preflight finished. These checkpoints are not used by the formal run."
