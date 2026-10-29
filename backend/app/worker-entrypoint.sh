#!/usr/bin/env bash
# worker 容器入口：每次启动时用外部 .env 覆盖容器环境，再启动 worker。
#
# 设计目标（外部文件为唯一事实源）：平台通过 SSH 只更新目标机
# ~/.config/mtp-worker/<machine_id>.env 文件，不重建/重启容器；本脚本在每次
# 容器启动（含 docker restart、宿主机重启）时重新读取该文件并覆盖环境，
# 使配置在下次启动时自动生效。
#
# 安全说明：逐行解析 KEY=value 并 export，值作为字面量传入，不做 shell 求值，
# 避免值中的 $、反引号等特殊字符被当成命令执行（区别于 source）。
set -euo pipefail

env_file="${1:?缺少 worker 环境文件路径}"

# 受管理变量必须先清空，避免配置文件删除某个变量后继续沿用容器创建时的旧值。
unset REDIS_URL REDIS_PASSWORD STORAGE_BACKEND S3_ENDPOINT S3_ACCESS_KEY \
  S3_SECRET_KEY S3_BUCKET S3_SECURE WORKER_MACHINE_ID WORKER_MANAGED \
  WORKER_CPU_SLOTS USE_DATABASE DATA_DIR

if [[ ! -r "$env_file" ]]; then
  printf 'worker 配置文件不存在或不可读：%s\n' "$env_file" >&2
  exit 78
fi

while IFS= read -r line; do
  case "$line" in
    ''|'#'*) continue ;;
    *=*) export "${line%%=*}"="${line#*=}" ;;
  esac
done < "$env_file"

exec python -m app.worker
