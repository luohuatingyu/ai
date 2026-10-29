#!/usr/bin/env bash
# macOS 原生 worker 启动器：Apple Silicon 使用 PyTorch MPS，避免 Linux Docker
# 无法透传 Apple GPU。Redis/S3 仍与 Linux worker 共用，支持跨系统联合训练。
set -Eeuo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'worker-native.sh 仅支持 macOS；Linux/Windows 请使用 Docker 或 WSL2。\n' >&2
  exit 2
fi

export WORKER_DEVICE="${WORKER_DEVICE:-mps}"
export USE_DATABASE="${USE_DATABASE:-false}"
export PYTHONUNBUFFERED=1

if [[ "$WORKER_DEVICE" != "mps" && "$WORKER_DEVICE" != "cpu" ]]; then
  printf 'macOS 原生 worker 的 WORKER_DEVICE 必须是 mps 或 cpu。\n' >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT/backend"

exec python3 -m app.worker "$@"
