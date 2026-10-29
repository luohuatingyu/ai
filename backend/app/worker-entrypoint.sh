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

if [[ -f "$env_file" ]]; then
  set -a
  while IFS= read -r line; do
    case "$line" in
      ''|'#'*) continue ;;
      *=*) export "${line%%=*}"="${line#*=}" ;;
    esac
  done < "$env_file"
  set +a
fi

exec python -m app.worker
