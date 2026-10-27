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

# 容器停止后切换源码目录，并保留旧目录供失败回滚。
activate_worker_app() {
  rm -rf "${APP_DIR}.previous"
  if [[ -d "$APP_DIR" ]]; then
    mv "$APP_DIR" "${APP_DIR}.previous"
  fi
  mv "$APP_STAGING_DIR/app" "$APP_DIR"
  rmdir "$APP_STAGING_DIR"
  APP_STAGING_DIR=""
}

# 用指定的现有镜像和当前部署配置创建 worker 容器。
run_worker_container() {
  local image="$1"
  args=(run --detach --name "$CONTAINER" --restart unless-stopped
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

# 已部署服务器同步源码并复用现有镜像重建容器，不运行 Docker build。
update_worker() {
  if [[ "$MODE" != cpu && "$MODE" != gpu ]]; then
    printf '资源类型必须是 cpu 或 gpu。\n' >&2
    exit 2
  fi
  if ! command -v docker >/dev/null 2>&1; then
    printf '目标服务器未安装 Docker。\n' >&2
    exit 127
  fi
  local existing_tag existing_image image previous_container has_existing
  local previous_env had_previous_env
  has_existing=false
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    has_existing=true
    existing_tag="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER")"
    existing_image="$(docker inspect --format '{{.Image}}' "$CONTAINER")"
    if [[ "$MTP_WORKER_IMAGE" == "$existing_tag" ]]; then
      image="$existing_image"
    elif ! image="$(docker image inspect --format '{{.Id}}' "$MTP_WORKER_IMAGE")"; then
      printf '目标服务器上没有配置的新 worker 镜像；请先加载该镜像。\n' >&2
      exit 1
    fi
  elif ! image="$(docker image inspect --format '{{.Id}}' "$MTP_WORKER_IMAGE")"; then
    printf '目标服务器上没有可复用的 worker 镜像；请先部署镜像。\n' >&2
    exit 1
  fi

  stage_worker_app
  previous_env="${ENV_FILE}.previous"
  had_previous_env=false
  if [[ -f "$ENV_FILE" ]]; then
    cp -p "$ENV_FILE" "$previous_env"
    had_previous_env=true
  else
    rm -f "$previous_env"
  fi
  # 先持久化新配置，让远端明确进入配置已更新、等待容器切换的状态。
  write_worker_env
  previous_container="${CONTAINER}-previous"
  if [[ "$has_existing" == true ]]; then
    if docker inspect "$previous_container" >/dev/null 2>&1; then
      docker rm --force "$previous_container" >/dev/null
    fi
    docker stop --time 45 "$CONTAINER" >/dev/null || true
    docker rename "$CONTAINER" "$previous_container"
  fi
  activate_worker_app
  if ! run_worker_container "$image"; then
    docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$APP_DIR"
    if [[ -d "${APP_DIR}.previous" ]]; then
      mv "${APP_DIR}.previous" "$APP_DIR"
    fi
    if [[ "$had_previous_env" == true ]]; then
      mv "$previous_env" "$ENV_FILE"
    else
      rm -f "$ENV_FILE"
    fi
    if [[ "$has_existing" == true ]]; then
      docker rename "$previous_container" "$CONTAINER"
      docker start "$CONTAINER" >/dev/null || true
    fi
    printf '更新后的 worker 容器启动失败，已恢复原容器。\n' >&2
    exit 1
  fi
  if [[ "$has_existing" == true ]]; then
    docker rm --force "$previous_container" >/dev/null
  fi
  rm -rf "${APP_DIR}.previous"
  rm -f "$previous_env"
  printf 'worker 代码已更新并重启：%s\n' "$CONTAINER"
  show_worker_startup
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
  update)
    update_worker
    ;;
  start|stop|restart|status)
    manage_worker
    ;;
  *)
    printf '不支持的 worker 操作。\n' >&2
    exit 2
    ;;
esac
