#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
MODEL_PATH=${MODEL_PATH:?set MODEL_PATH}
RESULT_TAG=${RESULT_TAG:?set RESULT_TAG}
PEFT_PATH=${PEFT_PATH:-}
EVAL_BATCH_SIZE=${EVAL_BATCH_SIZE:-4}
EVAL_TASKS=${EVAL_TASKS:-ifeval,mmlu_pro}
RESULT_GROUP=${RESULT_GROUP:-${EVAL_TASKS//,/_}}
SKIP_EXISTING=${SKIP_EXISTING:-0}
export HF_HOME=$ROOT/hf
export HF_DATASETS_CACHE=$ROOT/hf/datasets
export TMPDIR=$ROOT/tmp
export XDG_CACHE_HOME=$ROOT/cache
export TORCH_HOME=$ROOT/torch-cache
export TRITON_CACHE_DIR=$ROOT/triton-cache
export NLTK_DATA=$ROOT/cache/nltk
export HF_HUB_DOWNLOAD_TIMEOUT=120
export HF_HUB_ETAG_TIMEOUT=30
source "$ROOT/venvs/eval312/bin/activate"

model_args="pretrained=$MODEL_PATH,dtype=bfloat16,parallelize=True,max_memory_per_gpu=45GiB,enable_thinking=False"
if [ -n "$PEFT_PATH" ]; then
  model_args="$model_args,peft=$PEFT_PATH"
fi

limit_args=()
if [ -n "${EVAL_LIMIT:-}" ]; then
  limit_args+=(--limit "$EVAL_LIMIT")
fi

output_dir="$ROOT/results/${RESULT_TAG}_general/$RESULT_GROUP"
if [ "$SKIP_EXISTING" = 1 ] && find "$output_dir" -type f -name 'results_*.json' -print -quit 2>/dev/null | grep -q .; then
  echo "[$RESULT_TAG] keeping completed $EVAL_TASKS result"
  exit 0
fi

lm-eval run \
  --model hf \
  --model_args "$model_args" \
  --tasks "$EVAL_TASKS" \
  --apply_chat_template \
  --batch_size "$EVAL_BATCH_SIZE" \
  "${limit_args[@]}" \
  --seed 20260817 \
  --output_path "$output_dir" \
  --log_samples
