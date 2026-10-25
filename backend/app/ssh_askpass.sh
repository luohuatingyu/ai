#!/bin/sh
# OpenSSH 调用此程序读取平台统一配置的密码或私钥口令。
if [ "${WORKER_SSH_ASKPASS_SECRET+x}" != x ]; then
  exit 1
fi
printf '%s\n' "$WORKER_SSH_ASKPASS_SECRET"
