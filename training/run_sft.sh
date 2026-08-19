#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
REPO=${AGENTWORLD_REPO:-$ROOT/repo}
CONFIG=${1:?usage: training/run_sft.sh CONFIG_YAML}
export HF_HOME=$ROOT/hf
export TMPDIR=$ROOT/tmp
export PYTHONPATH=$REPO:${PYTHONPATH:-}
export XDG_CACHE_HOME=$ROOT/cache
export TORCH_HOME=$ROOT/torch-cache
export TRITON_CACHE_DIR=$ROOT/triton-cache
export FORCE_TORCHRUN=1
source "$ROOT/venvs/sft312/bin/activate"
cd "$REPO"
llamafactory-cli train "$CONFIG"
