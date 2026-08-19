#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
VERL_COMMIT=d4701e4eb50feeabc2781499c02f64793ed55461
VERL_PATCH=$REPO/training/patches/verl_fsdp_lora_disable_adapter.patch
PYTHON312=${PYTHON312:-$HOME/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu/bin/python3.12}
FLASH_ATTN_WHEEL=${FLASH_ATTN_WHEEL:-$ROOT/tmp/flash_attn-2.8.3-cp312-cp312-linux_x86_64.whl}
export HF_HOME=$ROOT/hf
export PIP_NO_CACHE_DIR=1
export TMPDIR=$ROOT/tmp
export PYTHONPATH=$REPO:${PYTHONPATH:-}
export UV_CACHE_DIR=$ROOT/uv-cache
export XDG_CACHE_HOME=$ROOT/cache
export TORCH_HOME=$ROOT/torch-cache
export TRITON_CACHE_DIR=$ROOT/triton-cache
mkdir -p "$ROOT" "$ROOT/models" "$ROOT/outputs" "$ROOT/results" "$ROOT/venvs" "$TMPDIR"
chmod +x "$REPO"/training/*.sh

available_kb=$(df --output=avail "$ROOT" | tail -n 1)
if [ "$available_kb" -lt 45000000 ]; then
  echo "Need at least 45 GB free under $ROOT before model and training environments are installed." >&2
  exit 2
fi

if [ ! -x "$PYTHON312" ]; then
  echo "Python 3.12 was not found at $PYTHON312" >&2
  exit 2
fi

"$PYTHON312" -m venv "$ROOT/venvs/bootstrap312"
"$ROOT/venvs/bootstrap312/bin/pip" install --upgrade pip uv huggingface_hub
export PATH=$ROOT/venvs/bootstrap312/bin:$PATH

"$PYTHON312" -m venv "$ROOT/venvs/sft312"
"$ROOT/venvs/sft312/bin/pip" install --upgrade pip setuptools wheel packaging hatchling
"$ROOT/venvs/sft312/bin/pip" install --no-build-isolation \
  "$ROOT/vendor/LLaMA-Factory" \
  deepspeed datasets tensorboard openai liger-kernel==0.8.1

if [ ! -f "$ROOT/verl/pyproject.toml" ]; then
  echo "The pinned veRL source must be uploaded to $ROOT/verl before setup." >&2
  exit 2
fi
if [ -d "$ROOT/verl/.git" ]; then
  actual_verl_commit=$(git -C "$ROOT/verl" rev-parse HEAD)
  if [ "$actual_verl_commit" != "$VERL_COMMIT" ]; then
    echo "Expected veRL $VERL_COMMIT, found $actual_verl_commit" >&2
    exit 2
  fi
else
  echo "Using the uploaded veRL archive for pinned commit $VERL_COMMIT."
fi
cd "$ROOT/verl"
if git apply --reverse --check --ignore-space-change "$VERL_PATCH" >/dev/null 2>&1; then
  echo "veRL FSDP LoRA patch is already applied."
else
  git apply --ignore-space-change "$VERL_PATCH"
fi
uv sync --frozen --all-packages --extra vllm --extra fsdp --no-install-package flash-attn
if [ ! -f "$FLASH_ATTN_WHEEL" ]; then
  echo "FlashAttention wheel was not found at $FLASH_ATTN_WHEEL" >&2
  exit 2
fi
uv pip install --python "$ROOT/verl/.venv/bin/python" --no-deps "$FLASH_ATTN_WHEEL"
"$ROOT/verl/.venv/bin/python" -c 'import flash_attn, torch, verl, vllm; print("veRL environment ready:", torch.__version__, vllm.__version__, flash_attn.__version__)'

if [ ! -f "$ROOT/models/Qwen3-8B/config.json" ]; then
  hf download Qwen/Qwen3-8B --local-dir "$ROOT/models/Qwen3-8B"
fi

if [ ! -s "$ROOT/data/agentworld_120/sft/general_replay_pool.jsonl" ] || \
   [ ! -s "$ROOT/data/agentworld_120/general_replay_report.json" ]; then
  "$ROOT/venvs/sft312/bin/python" -m training.fetch_general_replay --data-root "$ROOT/data/agentworld_120"
else
  echo "Using the existing general replay pool."
fi
"$ROOT/venvs/sft312/bin/python" -m training.build_mixed_sft \
  --data-root "$ROOT/data/agentworld_120" \
  --tokenizer "$ROOT/models/Qwen3-8B" \
  --replay-fraction 0.3
"$ROOT/venvs/sft312/bin/python" -m training.materialize_grpo_parquet --data-root "$ROOT/data/agentworld_120"

echo "Server dependencies, Qwen3-8B, replay data, and GRPO parquet are ready under $ROOT."
