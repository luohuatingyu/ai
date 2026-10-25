#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

# 把相对路径统一解析到仓库根目录，避免从其它工作目录执行时找错配置。
resolve_project_path() {
  local path="$1"
  if [[ "$path" != /* ]]; then
    path="$PROJECT_DIR/$path"
  fi
  printf '%s\n' "$path"
}

ENV_FILE="$(resolve_project_path "${WORKER_ENV_FILE:-.env.worker}")"
COMPOSE_CHOICE="${WORKER_COMPOSE_FILE:-}"
if [[ -z "$COMPOSE_CHOICE" && -f "$ENV_FILE" ]]; then
  COMPOSE_CHOICE="$(awk -F= '$1 == "WORKER_COMPOSE_FILE" {print substr($0, index($0, "=") + 1); exit}' "$ENV_FILE")"
  COMPOSE_CHOICE="${COMPOSE_CHOICE%\"}"
  COMPOSE_CHOICE="${COMPOSE_CHOICE#\"}"
fi
COMPOSE_FILE="$(resolve_project_path "${COMPOSE_CHOICE:-docker-compose.yml}")"

# 输出脚本用法与可用的服务器本地生命周期操作。
usage() {
  cat <<'EOF'
用法: scripts/worker-node.sh {add|start|stop|restart|status}

  add      首次运行时收集连接配置，构建并添加本机 worker 到计算集群
  start    启动本机 worker
  stop     优雅停止本机 worker
  restart  优雅重启本机 worker
  status   查看本机 worker 容器状态

可选环境变量:
  WORKER_COMPOSE_FILE  Compose 文件，默认 docker-compose.yml
  WORKER_ENV_FILE      Worker 专用环境文件，默认仓库根目录的 .env.worker
EOF
}

# 统一调用 Compose，并显式固定项目目录及配置文件位置。
compose() {
  local args=(compose --project-directory "$PROJECT_DIR" --file "$COMPOSE_FILE")
  if [[ -f "$ENV_FILE" ]]; then
    args+=(--env-file "$ENV_FILE")
  fi
  docker "${args[@]}" "$@"
}

# 检查变量来自进程环境或 Compose 环境文件，绝不把环境文件当 shell 脚本执行。
require_setting() {
  local key="$1"
  local env_value
  env_value="$(printenv "$key" 2>/dev/null || true)"
  if [[ -n "$env_value" ]]; then
    return 0
  fi
  if [[ -f "$ENV_FILE" ]] &&
    grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=[[:space:]]*[^[:space:]#]" "$ENV_FILE"; then
    return 0
  fi
  printf '缺少必需配置 %s，请在 %s 或进程环境中设置。\n' "$key" "$ENV_FILE" >&2
  return 1
}

# Compose .env 文件使用双引号值；转义插值符、反斜线与双引号，避免配置被二次解析。
compose_quote() {
  local value="$1"
  value="${value//\$/\$\$}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

# 收集首次接入所需的协调面、对象存储和稳定服务器标识，并以仅当前用户可读的权限保存。
prepare_worker_settings() {
  local missing=()
  local redis_url="${REDIS_URL:-}"
  local s3_endpoint="${S3_ENDPOINT:-}"
  local machine_id="${WORKER_MACHINE_ID:-}"
  local access_key="${S3_ACCESS_KEY:-minioadmin}"
  local secret_key="${S3_SECRET_KEY:-minioadmin}"
  local bucket="${S3_BUCKET:-mtp-artifacts}"
  local secure="${S3_SECURE:-false}"
  local cpu_slots="${WORKER_CPU_SLOTS:-1}"
  local compose_choice="${WORKER_COMPOSE_FILE:-docker-compose.yml}"
  local default_machine_id
  local tmp_file

  [[ -n "$redis_url" ]] || missing+=(REDIS_URL)
  [[ -n "$s3_endpoint" ]] || missing+=(S3_ENDPOINT)
  [[ -n "$machine_id" ]] || missing+=(WORKER_MACHINE_ID)

  if [[ -f "$ENV_FILE" ]]; then
    require_setting REDIS_URL || return 1
    require_setting S3_ENDPOINT || return 1
    require_setting WORKER_MACHINE_ID || return 1
    return 0
  fi

  if ((${#missing[@]} > 0)) && [[ ! -t 0 ]]; then
    printf '首次接入需要交互输入 %s；请在终端运行 add，或通过环境变量提供完整配置。\n' "${missing[*]}" >&2
    return 1
  fi

  if [[ -z "$redis_url" ]]; then
    read -r -p '主节点 Redis URL（例如 redis://10.0.0.8:6379/0）: ' redis_url || return 1
  fi
  if [[ -z "$s3_endpoint" ]]; then
    read -r -p '主节点 S3 地址（例如 10.0.0.8:9000）: ' s3_endpoint || return 1
  fi
  if [[ -z "$machine_id" ]]; then
    default_machine_id="$(hostname | tr -c 'A-Za-z0-9_.-' '-')"
    read -r -p "服务器标识 [${default_machine_id}]: " machine_id || return 1
    machine_id="${machine_id:-$default_machine_id}"
  fi
  if [[ -t 0 && -z "${S3_ACCESS_KEY:-}" ]]; then
    read -r -p 'S3 Access Key [minioadmin]: ' access_key || return 1
    access_key="${access_key:-minioadmin}"
  fi
  if [[ -t 0 && -z "${S3_SECRET_KEY:-}" ]]; then
    read -r -s -p 'S3 Secret Key [minioadmin]: ' secret_key || return 1
    printf '\n'
    secret_key="${secret_key:-minioadmin}"
  fi
  if [[ -t 0 && -z "${S3_BUCKET:-}" ]]; then
    read -r -p 'S3 Bucket [mtp-artifacts]: ' bucket || return 1
    bucket="${bucket:-mtp-artifacts}"
  fi
  if [[ -t 0 && -z "${S3_SECURE:-}" ]]; then
    read -r -p 'S3 使用 HTTPS？[y/N]: ' secure_answer || return 1
    secure="false"
    [[ "$secure_answer" =~ ^[Yy]$ ]] && secure="true"
  fi
  if [[ -t 0 && -z "${WORKER_CPU_SLOTS:-}" ]]; then
    read -r -p '额外 CPU 执行槽数量 [1]: ' cpu_slots || return 1
    cpu_slots="${cpu_slots:-1}"
  fi

  if [[ ! "$redis_url" =~ ^rediss?://[^[:space:]]+$ ]]; then
    printf 'Redis URL 格式无效，需以 redis:// 或 rediss:// 开头。\n' >&2
    return 2
  fi
  if [[ -z "$s3_endpoint" || "$s3_endpoint" =~ [[:space:]] ]]; then
    printf 'S3 地址不能为空或包含空白字符。\n' >&2
    return 2
  fi
  if [[ ! "$machine_id" =~ ^[A-Za-z0-9_.-]{1,128}$ ]]; then
    printf '服务器标识仅允许字母、数字、下划线、点和横线（最多 128 位）。\n' >&2
    return 2
  fi
  if [[ ! "$cpu_slots" =~ ^[0-9]+$ ]] || ((cpu_slots > 256)); then
    printf 'CPU 执行槽数量必须是 0 到 256 的整数。\n' >&2
    return 2
  fi
  if [[ "$secure" != true && "$secure" != false ]]; then
    printf 'S3_SECURE 只能为 true 或 false。\n' >&2
    return 2
  fi
  for setting_value in "$redis_url" "$s3_endpoint" "$access_key" "$secret_key" "$bucket" "$machine_id" "$cpu_slots" "$compose_choice"; do
    if [[ "$setting_value" == *$'\n'* || "$setting_value" == *$'\r'* ]]; then
      printf '连接配置不得包含换行符。\n' >&2
      return 2
    fi
  done
  mkdir -p "$(dirname -- "$ENV_FILE")"
  umask 077
  tmp_file="${ENV_FILE}.tmp.$$"
  {
    printf 'REDIS_URL=%s\n' "$(compose_quote "$redis_url")"
    printf 'S3_ENDPOINT=%s\n' "$(compose_quote "$s3_endpoint")"
    printf 'S3_ACCESS_KEY=%s\n' "$(compose_quote "$access_key")"
    printf 'S3_SECRET_KEY=%s\n' "$(compose_quote "$secret_key")"
    printf 'S3_BUCKET=%s\n' "$(compose_quote "$bucket")"
    printf 'S3_SECURE=%s\n' "$(compose_quote "$secure")"
    printf 'WORKER_MACHINE_ID=%s\n' "$(compose_quote "$machine_id")"
    printf 'WORKER_CPU_SLOTS=%s\n' "$(compose_quote "$cpu_slots")"
    printf 'WORKER_COMPOSE_FILE=%s\n' "$(compose_quote "$compose_choice")"
    printf 'WORKER_MANAGED=true\n'
  } > "$tmp_file"
  chmod 600 "$tmp_file"
  mv -- "$tmp_file" "$ENV_FILE"
  printf '接入配置已保存到 %s（权限 600）。\n' "$ENV_FILE"
}

# 首次添加或启动前确认 worker 能连接协调面和共享对象存储。
check_worker_settings() {
  require_setting REDIS_URL || return 1
  require_setting S3_ENDPOINT || return 1
  require_setting WORKER_MACHINE_ID || return 1
  if [[ ! -f "$COMPOSE_FILE" ]]; then
    printf '找不到 Compose 文件: %s\n' "$COMPOSE_FILE" >&2
    return 1
  fi
  compose config --quiet
}

if [[ "$#" -ne 1 ]]; then
  usage >&2
  exit 2
fi

ACTION="$1"
case "$ACTION" in
  add|start|stop|restart|status)
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    printf '未知操作: %s\n' "$ACTION" >&2
    usage >&2
    exit 2
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  printf '未找到 docker 命令，请先安装 Docker Compose v2。\n' >&2
  exit 127
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  printf '找不到 Compose 文件: %s\n' "$COMPOSE_FILE" >&2
  exit 2
fi

case "$ACTION" in
  add)
    prepare_worker_settings
    check_worker_settings
    export WORKER_MANAGED=true
    compose up --detach --build --no-deps worker
    printf '本机 worker 已添加并启动，等待主节点接收心跳。\n'
    ;;
  start)
    check_worker_settings
    export WORKER_MANAGED=true
    compose up --detach --no-deps worker
    printf '本机 worker 已启动。\n'
    ;;
  stop)
    compose stop --timeout 45 worker
    printf '本机 worker 已停止。\n'
    ;;
  restart)
    compose restart --timeout 45 worker
    printf '本机 worker 已重启。\n'
    ;;
  status)
    compose ps worker
    ;;
esac
