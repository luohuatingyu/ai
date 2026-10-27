#!/bin/sh
# OpenSSH 的提示参数区分私钥口令与远端登录密码。
prompt=${1:-}
case "$prompt" in
  *passphrase*)
    secret=${WORKER_SSH_ASKPASS_PASSPHRASE:-}
    ;;
  *)
    secret=${WORKER_SSH_ASKPASS_PASSWORD:-}
    ;;
esac
if [ -z "$secret" ]; then
  exit 1
fi
printf '%s\n' "$secret"
