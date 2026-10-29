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

# 重写目标机 worker 环境文件。配置有变化时覆盖文件；配置一致时跳过写入。
# 外部文件是唯一事实源：本函数只落盘，绝不操作运行中的容器。
write_worker_env() {
  local staging_env cpu_slots_value
  mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"
  umask 077
  staging_env="${ENV_FILE}.tmp-${BASHPID}"
  # GPU 模式只提供 GPU 槽，不提供 CPU 槽（原 run 阶段用 --env 覆盖，现统一写进文件）。
  cpu_slots_value="$CPU_SLOTS"
  if [[ "$MODE" == gpu ]]; then
    cpu_slots_value=0
  fi
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
    printf 'WORKER_CPU_SLOTS=%s\n' "$cpu_slots_value"
    if [[ -n "${MTP_DISTRIBUTED_HOST:-}" ]]; then
      printf 'WORKER_DISTRIBUTED_HOST=%s\n' "$MTP_DISTRIBUTED_HOST"
    fi
    printf 'USE_DATABASE=false\n'
    printf 'DATA_DIR=/app/data\n'
  } > "$staging_env"
  chmod 600 "$staging_env"
  # 配置与现网一致时不做覆盖，避免无谓写盘。
  if [[ -f "$ENV_FILE" ]] && cmp -s "$staging_env" "$ENV_FILE"; then
    rm -f "$staging_env"
    return 0
  fi
  mv -f "$staging_env" "$ENV_FILE"
  return 0
}

# 检查现有容器是否由当前外部配置方案创建；Docker start/restart 不会重新读取
# --env-file、挂载或 GPU DeviceRequests，因此旧容器必须先执行部署代码重建。
validate_worker_container_compatibility() {
  local command mounts device_requests network_mode
  # 容器继承镜像标签；旧镜像没有标签时沿用既有的兼容性检查。
  local image_mode
  image_mode="$(docker inspect --format '{{index .Config.Labels "org.mtp.worker.mode"}}' "$CONTAINER")" || return 1
  if [[ "$MODE" == gpu && "$image_mode" == cpu ]]; then
    printf 'CPU 版 worker 镜像不支持 GPU，请先重新构建 GPU 版镜像并替换容器。\n' >&2
    return 1
  fi
  command="$(docker inspect --format '{{json .Config.Cmd}}' "$CONTAINER")" || {
    printf '无法读取 worker 容器启动命令，请先执行部署代码重建容器。\n' >&2
    return 1
  }
  mounts="$(docker inspect --format '{{range .Mounts}}{{.Source}}|{{.Destination}}|{{.RW}}{{"\n"}}{{end}}' "$CONTAINER")" || {
    printf '无法读取 worker 容器挂载配置，请先执行部署代码重建容器。\n' >&2
    return 1
  }
  if [[ "$command" != *worker-entrypoint.sh* ]] ||
    [[ "$mounts" != *"${CONFIG_DIR}|/app/config|false"* ]]; then
    printf '当前 worker 容器不支持外部配置文件，请先点击“部署代码”重建容器。\n' >&2
    return 1
  fi
  device_requests="$(docker inspect --format '{{json .HostConfig.DeviceRequests}}' "$CONTAINER")" || {
    printf '无法读取 worker 容器 GPU 配置，请先执行部署代码重建容器。\n' >&2
    return 1
  }
  if [[ "$MODE" == gpu && "$device_requests" != *gpu* ]]; then
    printf '当前 worker 容器未启用 GPU，计算类型已改为 GPU；请先点击“部署代码”重建容器。\n' >&2
    return 1
  fi
  if [[ "$MODE" == cpu && "$device_requests" == *gpu* ]]; then
    printf '当前 worker 容器仍启用 GPU，计算类型已改为 CPU；请先点击“部署代码”重建容器。\n' >&2
    return 1
  fi
  # Docker restart 不会改变容器网络；配置互联地址前必须先按新配置部署。
  if [[ -n "${MTP_DISTRIBUTED_HOST:-}" ]]; then
    network_mode="$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$CONTAINER")" || return 1
    if [[ "$network_mode" != host ]]; then
      printf '当前 worker 容器未启用联合训练所需的 host 网络，请先点击“部署代码”重建容器。\n' >&2
      return 1
    fi
  fi
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
  local cpu_slots_value="$CPU_SLOTS"
  if [[ "$MODE" == gpu ]]; then
    cpu_slots_value=0
  fi
  printf 'worker 配置摘要：\n'
  printf 'WORKER_MODE=%s\n' "$MODE"
  printf 'WORKER_MACHINE_ID=%s\n' "$MTP_MACHINE_ID"
  printf 'WORKER_CPU_SLOTS=%s\n' "$cpu_slots_value"
  printf 'REDIS_TARGET=%s\n' "$(redis_target "$MTP_REDIS_URL")"
  printf 'REDIS_PASSWORD=%s\n' "$(secret_state "${MTP_REDIS_PASSWORD:-}")"
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

# 校验镜像硬件类型，在停止旧容器之前拒绝不兼容的 CPU → GPU 切换。
validate_worker_image() {
  local image="$1"
  # 禁止将精简 CPU 镜像当成 GPU 镜像启动；不对无标签的历史镜像作错误推断。
  local image_mode
  image_mode="$(docker image inspect --format '{{index .Config.Labels "org.mtp.worker.mode"}}' "$image")" || return 1
  if [[ "$MODE" == gpu && "$image_mode" == cpu ]]; then
    printf 'CPU 版 worker 镜像不支持 GPU，请先重新构建 GPU 版镜像并替换容器。\n' >&2
    return 1
  fi
}

# 用指定的现有镜像和当前部署配置创建 worker 容器；start=0 时仅创建不启动，
# 用于保持"部署/更新配置不改变容器停止状态"的语义。
run_worker_container() {
  local image="$1"
  local start="${2:-1}"
  validate_worker_image "$image" || return 1
  local launch=(run --detach)
  if [[ "$start" == "0" ]]; then
    launch=(create)
  fi
  # 挂载整个配置目录（而非单个 .env 文件）：入口脚本每次启动时从目录内读取
  # 最新环境文件；单文件挂载在 write_worker_env 的原子替换后会指向旧 inode。
  args=("${launch[@]}" --name "$CONTAINER" --restart unless-stopped
    --env-file "$ENV_FILE"
    --volume "$CONFIG_DIR:/app/config:ro"
    --volume "$CONFIG_DIR/data:/app/data")
  if [[ -d "$APP_DIR" ]]; then
    args+=(--volume "$APP_DIR:/app/app:ro")
  fi
  if [[ "$MODE" == gpu ]]; then
    args+=(--gpus all)
  fi
  # 显式配置节点互联地址后使用 Linux host 网络，允许 Gloo/NCCL 节点间直连。
  # 仅打开 rendezvous 端口不足以支持后续动态通信端口，节点应位于可信互通网络。
  if [[ -n "${MTP_DISTRIBUTED_HOST:-}" ]]; then
    args+=(--network host)
  fi
  # 入口脚本在每次容器启动时用外部 .env 覆盖环境，保证配置、宿主机重启后生效。
  docker "${args[@]}" "$image" bash /app/app/worker-entrypoint.sh "/app/config/${MTP_MACHINE_ID}.env"
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
  # 安装必须无条件写配置；配置一致时函数会跳过无谓覆盖。
  write_worker_env
  run_worker_container "$MTP_WORKER_IMAGE"
  printf 'worker 已安装并启动：%s\n' "$CONTAINER"
  show_worker_startup
}

# 用现有镜像重建 worker 容器（部署切换代码后需重新挂载），保持重建前的运行状态
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
  validate_worker_image "$existing_image" || return 1
  local previous_container="${CONTAINER}-previous"
  if docker inspect "$previous_container" >/dev/null 2>&1; then
    docker rm --force "$previous_container" >/dev/null
  fi
  container_state="$(docker inspect --format '{{.State.Status}}' "$CONTAINER")"
  if [[ "$container_state" == "running" ]]; then
    if ! docker stop --time 45 "$CONTAINER" >/dev/null; then
      printf '无法停止现有 worker 容器，最新代码尚未应用。\n' >&2
      exit 1
    fi
    start_flag=1
  else
    start_flag=0
  fi
  if ! docker rename "$CONTAINER" "$previous_container"; then
    printf '无法暂存现有 worker 容器，最新代码尚未应用。\n' >&2
    exit 1
  fi
  if ! run_worker_container "$existing_image" "$start_flag"; then
    docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
    docker rename "$previous_container" "$CONTAINER" >/dev/null 2>&1 || true
    if [[ "$start_flag" == "1" ]]; then
      docker start "$CONTAINER" >/dev/null 2>&1 || true
    fi
    printf '最新代码启动失败，已恢复旧容器；请修复后再次执行部署。\n' >&2
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

# 部署已接入服务器：只同步 worker 源码并用现有镜像重建容器，不上传新配置。
deploy_worker() {
  if [[ -n "${MTP_WORKER_APP_ARCHIVE_B64:-}" ]]; then
    stage_worker_app
  fi
  if [[ -n "${APP_STAGING_DIR:-}" ]]; then
    activate_worker_app
  fi
  recreate_worker_container
  printf 'worker 代码已部署并重建容器：%s\n' "$CONTAINER"
}

# 更新配置：只覆盖目标机 worker 环境文件，不重建、不重启运行中的容器。
# 新配置在容器下次启动/重启时由入口脚本读取外部 .env 自动生效。
update_worker_config() {
  write_worker_env
  printf 'worker 配置已更新，将在下次启动/重启时生效：%s\n' "$CONTAINER"
  show_worker_config
}

# 对目标服务器现有 worker 容器执行生命周期操作。启动和重启会先把平台最新
# 配置下发到目标机（仅写文件，不重建容器）；容器启动/重启时由入口脚本读取
# 外部 .env 应用最新配置。停止和状态查询只操作容器，不修改配置或代码。
manage_worker() {
  if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    printf '目标服务器上未找到 worker 容器。\n' >&2
    exit 4
  fi
  case "$ACTION" in
    start|restart)
      # 先把平台最新配置落到外部 .env（安全网），容器启动时自动读取生效。
      validate_worker_container_compatibility
      write_worker_env
      if [[ "$ACTION" == "restart" ]]; then
        docker restart --time 45 "$CONTAINER"
      else
        docker start "$CONTAINER"
      fi
      ;;
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
