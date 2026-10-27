#!/usr/bin/env bash
set -Eeuo pipefail

# 服务器标识只允许安全字符，避免生成容器名和配置路径时发生路径穿越。
if [[ ! "${MTP_MACHINE_ID:-}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]]; then
  printf '服务器标识无效。\n' >&2
  exit 2
fi

ACTION="${MTP_ACTION:-}"
MODE="${MTP_MODE:-}"
CPU_SLOTS="${MTP_CPU_SLOTS:-2}"
CONTAINER="mtp-worker-${MTP_MACHINE_ID}"
CONFIG_DIR="${HOME}/.config/mtp-worker"
ENV_FILE="${CONFIG_DIR}/${MTP_MACHINE_ID}.env"

# 保存运行配置到目标机用户目录，不保存或复制任何项目源码。
write_worker_env() {
  mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"
  umask 077
  {
    printf 'REDIS_URL=%s\n' "$MTP_REDIS_URL"
    printf 'STORAGE_BACKEND=s3\n'
    printf 'S3_ENDPOINT=%s\n' "$MTP_S3_ENDPOINT"
    printf 'S3_ACCESS_KEY=%s\n' "$MTP_S3_ACCESS_KEY"
    printf 'S3_SECRET_KEY=%s\n' "$MTP_S3_SECRET_KEY"
    printf 'S3_BUCKET=%s\n' "$MTP_S3_BUCKET"
    printf 'S3_SECURE=%s\n' "$MTP_S3_SECURE"
    printf 'WORKER_MACHINE_ID=%s\n' "$MTP_MACHINE_ID"
    printf 'WORKER_MANAGED=true\n'
    printf 'WORKER_CPU_SLOTS=%s\n' "$CPU_SLOTS"
    printf 'USE_DATABASE=false\n'
    printf 'DATA_DIR=/app/data\n'
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

# 使用平台经 SSH 流式加载的镜像启动单台远程 worker。
install_worker() {
  if [[ "$MODE" != cpu && "$MODE" != gpu ]]; then
    printf '资源类型必须是 cpu 或 gpu。\n' >&2
    exit 2
  fi
  if ! command -v docker >/dev/null 2>&1; then
    printf '目标服务器未安装 Docker。\n' >&2
    exit 127
  fi
  if ! docker image inspect "$MTP_WORKER_IMAGE" >/dev/null 2>&1; then
    printf '目标服务器尚未构建 worker 镜像，请从平台重新部署。\n' >&2
    exit 1
  fi
  write_worker_env
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    docker rm --force "$CONTAINER" >/dev/null
  fi
  args=(run --detach --name "$CONTAINER" --restart unless-stopped
    --env-file "$ENV_FILE" --volume "$CONFIG_DIR/data:/app/data")
  if [[ "$MODE" == gpu ]]; then
    args+=(--gpus all)
    args+=(--env WORKER_CPU_SLOTS=0)
  fi
  docker "${args[@]}" "$MTP_WORKER_IMAGE" python -m app.worker
  printf 'worker 已安装并启动：%s\n' "$CONTAINER"
  printf 'worker 容器启动快照：\n'
  docker inspect --format 'state={{.State.Status}} restart_count={{.RestartCount}} exit_code={{.State.ExitCode}} error={{.State.Error}}' "$CONTAINER" \
    || printf '无法读取 worker 容器状态。\n'
  printf 'worker 最近启动日志（最多 80 行）：\n'
  docker logs --tail=80 "$CONTAINER" 2>&1 || printf '暂时无法读取 worker 容器日志。\n'
}

# 对目标服务器现有 worker 容器执行有限生命周期操作。
manage_worker() {
  if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    printf '目标服务器上未找到 worker 容器。\n' >&2
    exit 4
  fi
  case "$ACTION" in
    start) docker start "$CONTAINER" ;;
    stop) docker stop --time 45 "$CONTAINER" ;;
    restart) docker restart --time 45 "$CONTAINER" ;;
    status) docker inspect --format '{{.State.Status}}' "$CONTAINER" ;;
    *)
      printf '不支持的 worker 操作。\n' >&2
      exit 2
      ;;
  esac
}

case "$ACTION" in
  install)
    install_worker
    ;;
  start|stop|restart|status)
    manage_worker
    ;;
  *)
    printf '不支持的 worker 操作。\n' >&2
    exit 2
    ;;
esac
