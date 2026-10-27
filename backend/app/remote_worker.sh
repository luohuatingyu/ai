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
APP_DIR="${CONFIG_DIR}/${MTP_MACHINE_ID}/app"

# 保存运行配置与 worker Python 代码副本到目标机用户目录。
write_worker_env() {
  local staging_env
  mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"
  umask 077
  staging_env="${ENV_FILE}.tmp-${BASHPID}"
  {
    printf 'REDIS_URL=%s\n' "$MTP_REDIS_URL"
    # Redis 密码可能单独配置（不内嵌在 URL 中），非空时才写入，避免留下空值。
    if [[ -n "${MTP_REDIS_PASSWORD:-}" ]]; then
      printf 'REDIS_PASSWORD=%s\n' "$MTP_REDIS_PASSWORD"
    fi
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
  } > "$staging_env"
  chmod 600 "$staging_env"
  mv -f "$staging_env" "$ENV_FILE"
}

# 输出目标机刚写入的安全配置摘要；秘密只显示是否已配置。
secret_state() {
  if [[ -n "$1" ]]; then
    printf '[已配置]'
  else
    printf '[未配置]'
  fi
}

# 从 Redis URL 中移除协议、认证信息和查询参数，只保留可诊断的目标地址。
redis_target() {
  local url="$1"
  local target="$url"
  if [[ "$target" == *://* ]]; then
    target="${target#*://}"
  fi
  target="${target#*@}"
  target="${target%%\?*}"
  printf '%s' "$target"
}

# 配置更新完成后输出实际写入文件对应的非敏感参数。
show_worker_config() {
  printf 'worker 配置摘要：\n'
  printf 'WORKER_MODE=%s\n' "$MODE"
  printf 'WORKER_MACHINE_ID=%s\n' "$MTP_MACHINE_ID"
  printf 'WORKER_CPU_SLOTS=%s\n' "$CPU_SLOTS"
  printf 'REDIS_TARGET=%s\n' "$(redis_target "$MTP_REDIS_URL")"
  printf 'S3_ENDPOINT=%s\n' "$MTP_S3_ENDPOINT"
  printf 'S3_BUCKET=%s\n' "$MTP_S3_BUCKET"
  printf 'S3_SECURE=%s\n' "$MTP_S3_SECURE"
  printf 'S3_ACCESS_KEY=%s\n' "$(secret_state "$MTP_S3_ACCESS_KEY")"
  printf 'S3_SECRET_KEY=%s\n' "$(secret_state "$MTP_S3_SECRET_KEY")"
}

# 将平台同步的 worker 源码解压到临时目录，尚不影响当前运行容器。
stage_worker_app() {
  if [[ -z "${MTP_WORKER_APP_ARCHIVE_B64:-}" ]]; then
    printf '缺少 worker 代码包。\n' >&2
    return 1
  fi
  APP_STAGING_DIR="${APP_DIR}.stage-${BASHPID}"
  mkdir -p "${APP_DIR%/*}"
  rm -rf "$APP_STAGING_DIR"
  mkdir -p "$APP_STAGING_DIR"
  if ! printf '%s' "$MTP_WORKER_APP_ARCHIVE_B64" | base64 --decode | tar -xzf - -C "$APP_STAGING_DIR"; then
    rm -rf "$APP_STAGING_DIR"
    printf '无法解包 worker 代码。\n' >&2
    return 1
  fi
  if [[ ! -d "$APP_STAGING_DIR/app" || ! -f "$APP_STAGING_DIR/app/worker.py" ]]; then
    rm -rf "$APP_STAGING_DIR"
    printf 'worker 代码包内容无效。\n' >&2
    return 1
  fi
}

# 原子切换源码目录；运行中的容器保持不变，下一次启动时使用新代码。
activate_worker_app() {
  rm -rf "${APP_DIR}.previous"
  if [[ -d "$APP_DIR" ]]; then
    mv "$APP_DIR" "${APP_DIR}.previous"
  fi
  mv "$APP_STAGING_DIR/app" "$APP_DIR"
  rmdir "$APP_STAGING_DIR"
  APP_STAGING_DIR=""
}

# 用指定的现有镜像和当前部署配置创建 worker 容器；start=0 时仅创建不启动，
# 用于保持"部署/更新配置不改变容器停止状态"的语义。
run_worker_container() {
  local image="$1"
  local start="${2:-1}"
  local launch=(run --detach)
  if [[ "$start" == "0" ]]; then
    launch=(create)
  fi
  args=("${launch[@]}" --name "$CONTAINER" --restart unless-stopped
    --env-file "$ENV_FILE" --volume "$CONFIG_DIR/data:/app/data")
  if [[ -d "$APP_DIR" ]]; then
    args+=(--volume "$APP_DIR:/app/app:ro")
  fi
  if [[ "$MODE" == gpu ]]; then
    args+=(--gpus all)
    args+=(--env WORKER_CPU_SLOTS=0)
  fi
  docker "${args[@]}" "$image" python -m app.worker
}

# 输出新容器快照和最近启动日志，便于 SSH 操作页面诊断启动失败。
show_worker_startup() {
  printf 'worker 容器启动快照：\n'
  docker inspect --format 'state={{.State.Status}} restart_count={{.RestartCount}} exit_code={{.State.ExitCode}} error={{.State.Error}}' "$CONTAINER" \
    || printf '无法读取 worker 容器状态。\n'
  printf 'worker 最近启动日志（最多 80 行）：\n'
  docker logs --tail=80 "$CONTAINER" 2>&1 || printf '暂时无法读取 worker 容器日志。\n'
}

# 首次部署时使用已构建镜像，写配置并启动容器。
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
    printf '目标服务器尚未构建 worker 镜像，请先构建镜像。\n' >&2
    exit 1
  fi
  if [[ -n "${MTP_WORKER_APP_ARCHIVE_B64:-}" ]]; then
    stage_worker_app
  fi
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    docker rm --force "$CONTAINER" >/dev/null
  fi
  if [[ -n "${APP_STAGING_DIR:-}" ]]; then
    activate_worker_app
  fi
  write_worker_env
  run_worker_container "$MTP_WORKER_IMAGE"
  printf 'worker 已安装并启动：%s\n' "$CONTAINER"
  show_worker_startup
}

# 使用当前保存的环境文件和现有镜像重建 worker 容器，保持重建前的运行状态
# （运行中的保持运行，停止的保持停止）；失败时回滚恢复旧容器。
recreate_worker_container() {
  if [[ "$MODE" != cpu && "$MODE" != gpu ]]; then
    printf '资源类型必须是 cpu 或 gpu。\n' >&2
    exit 2
  fi
  if ! command -v docker >/dev/null 2>&1; then
    printf '目标服务器未安装 Docker。\n' >&2
    exit 127
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    printf '目标服务器未找到 worker 配置文件，请先部署。\n' >&2
    exit 4
  fi
  if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    printf '目标服务器上未找到 worker 容器，请先部署。\n' >&2
    exit 4
  fi

  local existing_image container_state previous_container start_flag
  existing_image="$(docker inspect --format '{{.Image}}' "$CONTAINER")"
  if [[ -z "$existing_image" ]]; then
    printf '无法读取现有 worker 容器镜像。\n' >&2
    exit 1
  fi
  local previous_container="${CONTAINER}-previous"
  if docker inspect "$previous_container" >/dev/null 2>&1; then
    docker rm --force "$previous_container" >/dev/null
  fi
  container_state="$(docker inspect --format '{{.State.Status}}' "$CONTAINER")"
  if [[ "$container_state" == "running" ]]; then
    if ! docker stop --time 45 "$CONTAINER" >/dev/null; then
      printf '无法停止现有 worker 容器，最新配置尚未应用。\n' >&2
      exit 1
    fi
    start_flag=1
  else
    start_flag=0
  fi
  if ! docker rename "$CONTAINER" "$previous_container"; then
    printf '无法暂存现有 worker 容器，最新配置尚未应用。\n' >&2
    exit 1
  fi
  if ! run_worker_container "$existing_image" "$start_flag"; then
    docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
    docker rename "$previous_container" "$CONTAINER" >/dev/null 2>&1 || true
    if [[ "$start_flag" == "1" ]]; then
      docker start "$CONTAINER" >/dev/null 2>&1 || true
    fi
    printf '最新配置启动失败，已恢复旧容器；请修复后再次执行更新配置或部署。\n' >&2
    exit 1
  fi
  docker rm --force "$previous_container" >/dev/null
  if [[ "$start_flag" == "1" ]]; then
    printf 'worker 已重建并运行（保持原运行状态）：%s\n' "$CONTAINER"
  else
    printf 'worker 容器已重建（保持原停止状态，执行 start 启动）：%s\n' "$CONTAINER"
  fi
  show_worker_startup
}

# 部署已接入服务器：同步 worker 源码、重写配置并用现有镜像重建容器，立即生效。
deploy_worker() {
  if [[ -n "${MTP_WORKER_APP_ARCHIVE_B64:-}" ]]; then
    stage_worker_app
  fi
  write_worker_env
  if [[ -n "${APP_STAGING_DIR:-}" ]]; then
    activate_worker_app
  fi
  recreate_worker_container
  printf 'worker 代码与配置已部署并重建容器：%s\n' "$CONTAINER"
}

# 更新配置：覆盖环境文件并重建容器应用，不修改 worker 源码。
update_worker_config() {
  write_worker_env
  recreate_worker_container
  printf 'worker 配置已更新并应用：%s\n' "$CONTAINER"
  show_worker_config
}

# 对目标服务器现有 worker 容器执行生命周期操作；只操作容器，不修改配置或代码。
manage_worker() {
  if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    printf '目标服务器上未找到 worker 容器。\n' >&2
    exit 4
  fi
  case "$ACTION" in
    start) docker start "$CONTAINER" ;;
    restart) docker restart --time 45 "$CONTAINER" ;;
    stop) docker stop --time 45 "$CONTAINER" ;;
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
  config)
    update_worker_config
    ;;
  deploy|update)
    deploy_worker
    ;;
  start|stop|restart|status)
    manage_worker
    ;;
  *)
    printf '不支持的 worker 操作。\n' >&2
    exit 2
    ;;
esac
