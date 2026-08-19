#!/usr/bin/env bash
set -euo pipefail

ROOT=${AGENTWORLD_ROOT:-/data1/models/sunhenghui/agentworld-training}
PYTHON312=${PYTHON312:-$HOME/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu/bin/python3.12}
export PIP_NO_CACHE_DIR=1
export HF_HOME=$ROOT/hf
export HF_DATASETS_CACHE=$ROOT/hf/datasets
export TMPDIR=$ROOT/tmp
export XDG_CACHE_HOME=$ROOT/cache
export NLTK_DATA=$ROOT/cache/nltk
"$PYTHON312" -m venv "$ROOT/venvs/eval312"
"$ROOT/venvs/eval312/bin/pip" install --upgrade pip
"$ROOT/venvs/eval312/bin/pip" install "lm_eval[hf,api,tasks]" evalplus "bfcl-eval==2026.3.23" openai soundfile
mkdir -p "$ROOT/results/bfcl" "$HF_DATASETS_CACHE" "$NLTK_DATA/tokenizers"
if [ ! -f "$NLTK_DATA/tokenizers/punkt_tab/english/abbrev_types.txt" ]; then
  curl -fL --retry 3 \
    https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip \
    -o "$TMPDIR/punkt_tab.zip"
  unzip -q -o "$TMPDIR/punkt_tab.zip" -d "$NLTK_DATA/tokenizers"
fi
