#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
DATA=${AGENTWORLD_DATA_ROOT:-$ROOT/data/agentworld_120}
MODEL=${MODEL_PATH:-$ROOT/models/Qwen3-8B}
ADAPTER=${LORA_ADAPTER_PATH:-$ROOT/outputs/lora_mixed}
VERL=${VERL_ROOT:-$ROOT/verl}
OUTPUT=${GRPO_OUTPUT_DIR:-$ROOT/outputs/grpo_lora_mixed}
EXPORT=${GRPO_EXPORT_DIR:-$ROOT/outputs/grpo_lora_mixed_hf}
UV=${UV_BIN:-$ROOT/venvs/bootstrap312/bin/uv}

export AGENTWORLD_DATA_ROOT=$DATA
export PYTHONPATH=$REPO:$VERL:${PYTHONPATH:-}
export HF_HOME=$ROOT/hf
export TMPDIR=${AGENTWORLD_TMPDIR:-/data1/models/sunhenghui/tmp}
export RAY_TMPDIR=${AGENTWORLD_RAY_TMPDIR:-/data1/models/sunhenghui/ray}
export UV_CACHE_DIR=$ROOT/uv-cache
export XDG_CACHE_HOME=$ROOT/cache
export TORCH_HOME=$ROOT/torch-cache
export TRITON_CACHE_DIR=$ROOT/triton-cache
mkdir -p "$OUTPUT" "$TMPDIR" "$RAY_TMPDIR"
cd "$VERL"

# Tool schemas are selected per sample inside ToolAgentLoop. The dataset-level
# length filter only sees the global registry and would count all tools.
"$UV" run --no-sync python3 -m verl.trainer.main_ppo \
  algorithm.adv_estimator=grpo \
  algorithm.use_kl_in_reward=False \
  data.train_files="$DATA/grpo/agentworld_train.parquet" \
  data.val_files="$DATA/grpo/agentworld_dev.parquet" \
  data.train_batch_size=16 \
  data.max_prompt_length=8192 \
  data.max_response_length=16384 \
  data.filter_overlong_prompts=False \
  data.truncation=error \
  actor_rollout_ref.model.path="$MODEL" \
  actor_rollout_ref.model.lora_adapter_path="$ADAPTER" \
  actor_rollout_ref.model.use_remove_padding=True \
  actor_rollout_ref.model.use_fused_kernels=True \
  actor_rollout_ref.model.enable_gradient_checkpointing=True \
  actor_rollout_ref.actor.optim.lr=5e-7 \
  actor_rollout_ref.actor.ppo_mini_batch_size=16 \
  actor_rollout_ref.actor.use_dynamic_bsz=True \
  actor_rollout_ref.actor.ppo_max_token_len_per_gpu=24576 \
  actor_rollout_ref.actor.use_kl_loss=True \
  actor_rollout_ref.actor.kl_loss_coef=0.01 \
  actor_rollout_ref.actor.kl_loss_type=low_var_kl \
  actor_rollout_ref.actor.entropy_coeff=0 \
  actor_rollout_ref.actor.fsdp_config.param_offload=False \
  actor_rollout_ref.actor.fsdp_config.optimizer_offload=False \
  'actor_rollout_ref.actor.checkpoint.save_contents=[model]' \
  'actor_rollout_ref.actor.checkpoint.load_contents=[model]' \
  actor_rollout_ref.rollout.name=vllm \
  actor_rollout_ref.rollout.mode=async \
  actor_rollout_ref.rollout.tensor_model_parallel_size=2 \
  actor_rollout_ref.rollout.gpu_memory_utilization=0.55 \
  actor_rollout_ref.rollout.n=8 \
  actor_rollout_ref.rollout.multi_turn.enable=True \
  actor_rollout_ref.rollout.multi_turn.format=hermes \
  actor_rollout_ref.rollout.multi_turn.tool_config_path="$DATA/grpo/tool_config.json" \
  actor_rollout_ref.rollout.multi_turn.max_assistant_turns=16 \
  actor_rollout_ref.rollout.multi_turn.max_user_turns=15 \
  actor_rollout_ref.rollout.multi_turn.max_parallel_calls=1 \
  actor_rollout_ref.rollout.multi_turn.max_tool_response_length=2048 \
  actor_rollout_ref.rollout.agent.default_agent_loop=tool_agent \
  actor_rollout_ref.rollout.agent.num_workers=32 \
  actor_rollout_ref.rollout.log_prob_use_dynamic_bsz=True \
  actor_rollout_ref.rollout.log_prob_max_token_len_per_gpu=24576 \
  actor_rollout_ref.ref.log_prob_use_dynamic_bsz=True \
  actor_rollout_ref.ref.log_prob_max_token_len_per_gpu=24576 \
  actor_rollout_ref.ref.fsdp_config.param_offload=True \
  reward.custom_reward_function.path="$REPO/training/reward.py" \
  reward.custom_reward_function.name=compute_score \
  reward.reward_manager.name=naive \
  reward.num_workers=8 \
  trainer.balance_batch=True \
  trainer.logger='["console","tensorboard"]' \
  trainer.project_name=agentworld_qwen3_8b \
  trainer.experiment_name=lora_mixed_grpo \
  trainer.n_gpus_per_node=8 \
  trainer.nnodes=1 \
  trainer.save_freq=999999 \
  trainer.test_freq=999999 \
  trainer.total_epochs=1 \
  trainer.default_local_dir="$OUTPUT" \
  trainer.rollout_data_dir="$OUTPUT/rollouts" \
  ray_kwargs.ray_init.runtime_env.py_executable="$UV run --no-sync" \
  "$@"

actor_checkpoint=$(find "$OUTPUT" -path '*/actor/fsdp_config.json' -printf '%T@ %h\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
if [ -z "$actor_checkpoint" ]; then
  echo "GRPO finished without a saved actor checkpoint." >&2
  exit 2
fi
"$UV" run --no-sync python3 -m verl.model_merger merge \
  --backend fsdp \
  --local_dir "$actor_checkpoint" \
  --target_dir "$EXPORT"
