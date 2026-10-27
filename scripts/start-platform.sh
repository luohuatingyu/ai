#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

# 未显式选择硬件时默认使用 CPU Compose，避免在无 GPU 主机上启动失败。
PLATFORM_MODE="${PLATFORM_MODE:-cpu}"
# 远程管理需要 trainer 访问宿主 Docker socket，因此默认关闭。
REMOTE_MANAGEMENT="${REMOTE_MANAGEMENT:-false}"

case "$PLATFORM_MODE" in
  gpu)
    BASE_COMPOSE_FILE="docker-compose.yml"
    ;;
  cpu)
    BASE_COMPOSE_FILE="docker-compose.cpu.yml"
    ;;
  *)
    printf 'PLATFORM_MODE 必须是 gpu 或 cpu，收到：%s\n' "$PLATFORM_MODE" >&2
    exit 2
    ;;
esac

case "$REMOTE_MANAGEMENT" in
  true|false)
    ;;
  *)
    printf 'REMOTE_MANAGEMENT 必须是 true 或 false，收到：%s\n' "$REMOTE_MANAGEMENT" >&2
    exit 2
    ;;
esac

COMPOSE_ARGS=(
  compose
  --project-directory "$PROJECT_DIR"
  --file "$PROJECT_DIR/$BASE_COMPOSE_FILE"
)
if [[ "$REMOTE_MANAGEMENT" == true ]]; then
  COMPOSE_ARGS+=(--file "$PROJECT_DIR/docker-compose.remote.yml")
fi

printf '启动平台：mode=%s remote_management=%s\n' "$PLATFORM_MODE" "$REMOTE_MANAGEMENT"
docker "${COMPOSE_ARGS[@]}" up --build -d
