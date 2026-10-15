#!/usr/bin/env bash
# Local Laya decision server (SPEC §20, §23): Jev-compatible POST /v1/systemone on http://127.0.0.1:8100.
# One-time setup (own venv; PyTorch stays out of the agent's environment):
#   uv venv services/laya/.venv --python 3.11
#   uv pip install --python services/laya/.venv/bin/python "laya[serve]==0.4.7"
# First start downloads the checkpoints (~2.3 GB) into services/laya/models/ (git-ignored).
set -euo pipefail
cd "$(dirname "$0")"
export HF_HOME="$PWD/models"
export LAYA_HOST="${LAYA_HOST:-127.0.0.1}"   # localhost only
export LAYA_PORT="${LAYA_PORT:-8100}"        # 8000 is reserved for the optional FastAPI service (§22)
export LAYA_DEVICE="${LAYA_DEVICE:-cuda}"    # set to cpu on machines without an NVIDIA GPU
export LAYA_MODELS="${LAYA_MODELS:-english,multilingual}"
export LAYA_DEFAULT_MODEL="${LAYA_DEFAULT_MODEL:-multilingual}"
export LAYA_PRELOAD=1
exec .venv/bin/laya-serve
