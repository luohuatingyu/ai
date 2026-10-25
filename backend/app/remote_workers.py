"""远程 worker 管理：校验接入参数、经 SSH 执行单个部署脚本并保存操作状态。"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List

import app.config as config
from app.state import manager


class RemoteWorkerError(RuntimeError):
    """远程配置或 SSH 操作失败。"""


_MACHINE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")
_ENVIRONMENT_KEYS = (
    "MTP_ACTION",
    "MTP_MACHINE_ID",
    "MTP_MODE",
    "MTP_CPU_SLOTS",
    "MTP_WORKER_IMAGE",
    "MTP_REDIS_URL",
    "MTP_S3_ENDPOINT",
    "MTP_S3_ACCESS_KEY",
    "MTP_S3_SECRET_KEY",
    "MTP_S3_BUCKET",
    "MTP_S3_SECURE",
)


def remote_configuration_errors() -> List[str]:
    """列出启用远程执行所需但尚未配置的项目，不读取或返回私钥内容。"""
    errors: List[str] = []
    if not config.WORKER_REMOTE_ENABLED:
        errors.append("远程服务器管理尚未启用")
    if len(config.WORKER_REMOTE_TOKEN) < 32:
        errors.append("WORKER_REMOTE_TOKEN 至少需要 32 个字符")
    if not config.REDIS_URL:
        errors.append("平台必须配置共享 REDIS_URL，远程 worker 才能加入同一任务队列")
    if config.STORAGE_BACKEND != "s3":
        errors.append("平台必须使用共享 S3 存储，远程 worker 才能读取数据集和写入产物")
    if not shutil.which("ssh"):
        errors.append("平台后端容器未安装 OpenSSH 客户端")
    key_path = Path(config.WORKER_SSH_KEY_PATH)
    known_hosts_path = Path(config.WORKER_SSH_KNOWN_HOSTS_PATH)
    if not key_path.is_file():
        errors.append("平台 SSH 私钥文件不存在")
    elif key_path.stat().st_mode & 0o077:
        errors.append("平台 SSH 私钥权限必须限制为 600 或更严格")
    if not known_hosts_path.is_file() or known_hosts_path.stat().st_size == 0:
        errors.append("SSH known_hosts 文件不存在；请先登记并核验目标主机指纹")
    image = config.WORKER_REMOTE_IMAGE
    if not image or "/" not in image or any(c.isspace() for c in image):
        errors.append("WORKER_IMAGE 必须是目标服务器可拉取的镜像仓库引用")
    if not config.WORKER_REMOTE_REDIS_URL.startswith(("redis://", "rediss://")):
        errors.append("WORKER_REMOTE_REDIS_URL 必须是目标服务器可访问的 Redis URL")
    if not config.WORKER_REMOTE_S3_ENDPOINT or any(
        c.isspace() for c in config.WORKER_REMOTE_S3_ENDPOINT
    ):
        errors.append("WORKER_REMOTE_S3_ENDPOINT 必须是目标服务器可访问的 S3 地址")
    values = (
        config.WORKER_REMOTE_IMAGE,
        config.WORKER_REMOTE_REDIS_URL,
        config.WORKER_REMOTE_S3_ENDPOINT,
        config.S3_ACCESS_KEY,
        config.S3_SECRET_KEY,
        config.S3_BUCKET,
    )
    if any("\n" in value or "\r" in value for value in values):
        errors.append("远程配置不能包含换行符")
    return errors


def validate_server_record(server: Dict[str, object]) -> None:
    """检查 Redis 中的服务器记录，避免损坏数据进入 SSH 参数或脚本。"""
    machine_id = str(server.get("machine_id", ""))
    host = str(server.get("host", ""))
    username = str(server.get("username", ""))
    try:
        port = int(server.get("port", 0))
    except (TypeError, ValueError) as exc:
        raise RemoteWorkerError("SSH 端口配置无效") from exc
    if not _MACHINE_ID_RE.fullmatch(machine_id):
        raise RemoteWorkerError("服务器标识格式无效")
    if not _HOST_RE.fullmatch(host):
        raise RemoteWorkerError("SSH 主机地址格式无效")
    if not _USERNAME_RE.fullmatch(username):
        raise RemoteWorkerError("SSH 用户名格式无效")
    if not 1 <= port <= 65535:
        raise RemoteWorkerError("SSH 端口必须在 1 到 65535 之间")


def render_remote_script(server: Dict[str, object], action: str) -> str:
    """把经过校验的配置作为 shell 字面量前缀，拼接随镜像打包的单文件脚本。"""
    validate_server_record(server)
    allowed_actions = {"install", "start", "stop", "restart", "status"}
    if action not in allowed_actions:
        raise RemoteWorkerError("不支持的远程操作")
    mode = str(server.get("mode", "cpu"))
    if mode not in {"cpu", "gpu"}:
        raise RemoteWorkerError("计算类型必须是 cpu 或 gpu")
    try:
        cpu_slots = int(server.get("cpu_slots", 2))
    except (TypeError, ValueError) as exc:
        raise RemoteWorkerError("CPU 执行槽数量无效") from exc
    if not 0 <= cpu_slots <= 256:
        raise RemoteWorkerError("CPU 执行槽数量必须在 0 到 256 之间")

    settings = {
        "MTP_ACTION": action,
        "MTP_MACHINE_ID": str(server["machine_id"]),
        "MTP_MODE": mode,
        "MTP_CPU_SLOTS": str(cpu_slots),
        "MTP_WORKER_IMAGE": config.WORKER_REMOTE_IMAGE,
        "MTP_REDIS_URL": config.WORKER_REMOTE_REDIS_URL,
        "MTP_S3_ENDPOINT": config.WORKER_REMOTE_S3_ENDPOINT,
        "MTP_S3_ACCESS_KEY": config.S3_ACCESS_KEY,
        "MTP_S3_SECRET_KEY": config.S3_SECRET_KEY,
        "MTP_S3_BUCKET": config.S3_BUCKET,
        "MTP_S3_SECURE": "true" if config.S3_SECURE else "false",
    }
    for key in _ENVIRONMENT_KEYS:
        value = settings[key]
        if "\n" in value or "\r" in value:
            raise RemoteWorkerError("远程配置不能包含换行符")
    script_path = Path(__file__).with_name("remote_worker.sh")
    try:
        script_body = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RemoteWorkerError("远程部署脚本未打包到后端镜像") from exc
    prelude = "\n".join(f"export {key}={shlex.quote(settings[key])}" for key in _ENVIRONMENT_KEYS)
    return f"{prelude}\n{script_body}"


def run_remote_action(server: Dict[str, object], action: str) -> Dict[str, str]:
    """用严格主机校验的 OpenSSH 执行脚本，并过滤输出中的连接密钥。"""
    errors = remote_configuration_errors()
    if errors:
        raise RemoteWorkerError("；".join(errors))
    validate_server_record(server)
    ssh_path = shutil.which("ssh")
    if not ssh_path:
        raise RemoteWorkerError("平台后端容器未安装 OpenSSH 客户端")
    host = str(server["host"])
    ssh_host = f"[{host}]" if ":" in host else host
    target = f"{server['username']}@{ssh_host}"
    command = [
        ssh_path,
        "-i",
        config.WORKER_SSH_KEY_PATH,
        "-p",
        str(server["port"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={config.WORKER_SSH_KNOWN_HOSTS_PATH}",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "LogLevel=ERROR",
        target,
        "bash -s",
    ]
    timeout = 3600 if action == "install" else 120
    try:
        result = subprocess.run(
            command,
            input=render_remote_script(server, action),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteWorkerError("远程 SSH 操作超时") from exc
    except OSError as exc:
        raise RemoteWorkerError("无法启动 SSH 客户端") from exc
    output = _redact_output(result.stdout, result.stderr)
    if result.returncode != 0:
        detail = (output["stderr"] or output["stdout"] or "远程脚本返回错误").strip()
        raise RemoteWorkerError(detail[-2000:])
    return output


def execute_remote_operation(
    operation_id: str, server: Dict[str, object], action: str
) -> None:
    """在后台执行 SSH 操作并将可轮询状态写入协调存储。"""
    now = time.time()
    operation = manager.lease.get_remote_operation(operation_id) or {
        "operation_id": operation_id,
        "machine_id": str(server.get("machine_id", "")),
        "action": action,
        "created_at": now,
    }
    operation.update({"status": "running", "updated_at": now, "error": None})
    manager.lease.set_remote_operation(operation_id, operation)
    server.update({"status": "running", "operation_id": operation_id})
    manager.lease.set_remote_server(str(server["machine_id"]), server)
    try:
        result = run_remote_action(server, action)
        operation.update(
            {
                "status": "completed",
                "updated_at": time.time(),
                "output": result.get("stdout", "")[-2000:],
            }
        )
        server["status"] = "online" if action in {"install", "start", "restart"} else "stopped"
    except RemoteWorkerError as exc:
        operation.update({"status": "failed", "updated_at": time.time(), "error": str(exc)})
        server["status"] = "error"
    except Exception:
        operation.update(
            {"status": "failed", "updated_at": time.time(), "error": "远程操作发生未预期错误"}
        )
        server["status"] = "error"
    manager.lease.set_remote_operation(operation_id, operation)
    manager.lease.set_remote_server(str(server["machine_id"]), server)


def _redact_output(stdout: str, stderr: str) -> Dict[str, str]:
    """从 SSH 输出中删除远程环境变量，防止服务端错误响应暴露存储凭据。"""
    secrets = (
        config.WORKER_REMOTE_REDIS_URL,
        config.S3_ACCESS_KEY,
        config.S3_SECRET_KEY,
    )
    for secret in secrets:
        if secret:
            stdout = stdout.replace(secret, "[redacted]")
            stderr = stderr.replace(secret, "[redacted]")
    return {"stdout": stdout[-4000:], "stderr": stderr[-4000:]}
