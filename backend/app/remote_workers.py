"""远程 worker 管理：校验接入参数、经 SSH 执行单个部署脚本并保存操作状态。"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List

from cryptography.fernet import Fernet, InvalidToken

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


def _credential_fernet() -> Fernet:
    """构造平台凭证加密器；密钥由运维通过环境变量提供并保持稳定。"""
    key = config.WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY
    if not key:
        raise RemoteWorkerError("页面配置 SSH 凭证前需设置 WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RemoteWorkerError("WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY 不是有效的 Fernet 密钥") from exc


def _platform_ssh_credentials() -> Dict[str, str]:
    """读取页面加密凭证；不存在时回退到历史环境变量和私钥挂载。"""
    encrypted = manager.lease.get_remote_ssh_credentials()
    if not encrypted:
        return {
            "source": "env",
            "auth_method": config.WORKER_SSH_AUTH_METHOD,
            "password": config.WORKER_SSH_PASSWORD,
            "passphrase": config.WORKER_SSH_KEY_PASSPHRASE,
            "private_key": "",
        }
    try:
        payload = _credential_fernet().decrypt(encrypted.encode("ascii"))
        credentials = json.loads(payload.decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteWorkerError(
            "页面保存的 SSH 凭证无法解密；请检查平台加密密钥并重新保存凭证"
        ) from exc
    if not isinstance(credentials, dict) or credentials.get("auth_method") not in {"key", "password"}:
        raise RemoteWorkerError("页面保存的 SSH 凭证格式无效，请重新配置")
    credentials.setdefault("password", "")
    credentials.setdefault("passphrase", "")
    credentials.setdefault("private_key", "")
    credentials["source"] = "page"
    return {str(key): str(value) for key, value in credentials.items()}


def remote_ssh_settings_status() -> Dict[str, object]:
    """返回页面安全设置所需的状态，不把任何凭证内容返回给浏览器。"""
    try:
        credentials = _platform_ssh_credentials()
    except RemoteWorkerError:
        if not manager.lease.get_remote_ssh_credentials():
            raise
        try:
            _credential_fernet()
            encryption_ready = True
        except RemoteWorkerError:
            encryption_ready = False
        return {
            "source": "page",
            "auth_method": None,
            "configured": False,
            "can_configure": bool(config.REDIS_URL and manager.distributed and encryption_ready),
            "status_error": "页面凭证无法解密；请恢复原加密密钥，或在设置有效密钥后重新保存/清除凭证。",
        }
    method = credentials["auth_method"]
    if credentials["source"] == "page":
        configured = bool(
            credentials.get("private_key") if method == "key" else credentials.get("password")
        )
    elif method == "key":
        key_path = Path(config.WORKER_SSH_KEY_PATH)
        configured = key_path.is_file() and not (key_path.stat().st_mode & 0o077)
    else:
        configured = bool(config.WORKER_SSH_PASSWORD)
    try:
        _credential_fernet()
        encryption_ready = True
    except RemoteWorkerError:
        encryption_ready = False
    return {
        "source": credentials["source"],
        "auth_method": method if method in {"key", "password"} else None,
        "configured": configured,
        "can_configure": bool(config.REDIS_URL and manager.distributed and encryption_ready),
    }


def store_platform_ssh_credentials(credentials: Dict[str, str]) -> Dict[str, object]:
    """加密页面凭证后持久化到共享 Redis，并返回不含秘密的状态。"""
    if not config.REDIS_URL or not manager.distributed:
        raise RemoteWorkerError("页面保存 SSH 凭证需要启用共享 Redis")
    fernet = _credential_fernet()
    serialized = json.dumps(credentials, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encrypted = fernet.encrypt(serialized).decode("ascii")
    manager.lease.set_remote_ssh_credentials(encrypted)
    return remote_ssh_settings_status()


def clear_platform_ssh_credentials() -> Dict[str, object]:
    """删除页面保存的凭证，使远程连接回退到原有环境配置。"""
    manager.lease.delete_remote_ssh_credentials()
    return remote_ssh_settings_status()


def remote_configuration_errors(credentials: Dict[str, str] | None = None) -> List[str]:
    """列出远程执行缺少的配置，不读取或返回平台 SSH 凭证内容。"""
    errors: List[str] = []
    if credentials is None:
        try:
            credentials = _platform_ssh_credentials()
        except RemoteWorkerError as exc:
            errors.append(str(exc))
            credentials = {
                "source": "env",
                "auth_method": "",
                "password": "",
                "passphrase": "",
                "private_key": "",
            }
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
    known_hosts_path = Path(config.WORKER_SSH_KNOWN_HOSTS_PATH)
    auth_method = credentials["auth_method"]
    if auth_method not in {"key", "password"}:
        errors.append("WORKER_SSH_AUTH_METHOD 只能是 key 或 password")
    elif auth_method == "key" and credentials["source"] == "page":
        if not credentials.get("private_key"):
            errors.append("页面未配置 SSH 私钥")
    elif auth_method == "key":
        key_path = Path(config.WORKER_SSH_KEY_PATH)
        if not key_path.is_file():
            errors.append("平台 SSH 私钥文件不存在")
        elif key_path.stat().st_mode & 0o077:
            errors.append("平台 SSH 私钥权限必须限制为 600 或更严格")
    elif not credentials.get("password"):
        errors.append("密码认证需要配置 WORKER_SSH_PASSWORD")
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
    ssh_secret = credentials.get("password", "") if auth_method == "password" else credentials.get("passphrase", "")
    if "\n" in ssh_secret or "\r" in ssh_secret:
        errors.append("SSH 凭证不能包含换行符")
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
    """用严格主机校验的 OpenSSH 执行脚本，并过滤输出中的平台 SSH 凭证。"""
    credentials = _platform_ssh_credentials()
    errors = remote_configuration_errors(credentials)
    if errors:
        raise RemoteWorkerError("；".join(errors))
    validate_server_record(server)
    ssh_path = shutil.which("ssh")
    if not ssh_path:
        raise RemoteWorkerError("平台后端容器未安装 OpenSSH 客户端")
    host = str(server["host"])
    ssh_host = f"[{host}]" if ":" in host else host
    target = f"{server['username']}@{ssh_host}"
    command = [ssh_path]
    temporary_key_path: Path | None = None
    if credentials["auth_method"] == "key":
        if credentials["source"] == "page":
            fd, temporary_key_name = tempfile.mkstemp(prefix="mtp-ssh-key-")
            temporary_key_path = Path(temporary_key_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as key_file:
                    key_file.write(credentials["private_key"])
                    if not credentials["private_key"].endswith("\n"):
                        key_file.write("\n")
                os.chmod(temporary_key_path, 0o600)
            except OSError:
                temporary_key_path.unlink(missing_ok=True)
                raise
            key_path = str(temporary_key_path)
        else:
            key_path = config.WORKER_SSH_KEY_PATH
        command.extend(["-i", key_path])
        command.extend(["-o", "IdentitiesOnly=yes"])
        command.extend(["-o", "PubkeyAuthentication=yes"])
        command.extend(["-o", "PasswordAuthentication=no"])
        command.extend(["-o", "KbdInteractiveAuthentication=no"])
        ssh_secret = credentials.get("passphrase", "")
        command.extend(["-o", "BatchMode=no" if ssh_secret else "BatchMode=yes"])
    else:
        command.extend(["-o", "PubkeyAuthentication=no"])
        command.extend(["-o", "PasswordAuthentication=yes"])
        command.extend(["-o", "KbdInteractiveAuthentication=no"])
        command.extend(["-o", "PreferredAuthentications=password"])
        command.extend(["-o", "NumberOfPasswordPrompts=1"])
        command.extend(["-o", "BatchMode=no"])
        ssh_secret = credentials.get("password", "")
    command.extend(
        [
            "-p",
            str(server["port"]),
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={config.WORKER_SSH_KNOWN_HOSTS_PATH}",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "LogLevel=ERROR",
        ]
    )
    command.extend([target, "bash -s"])
    timeout = 3600 if action == "install" else 120
    try:
        run_options = {
            "input": render_remote_script(server, action),
            "text": True,
            "capture_output": True,
            "timeout": timeout,
            "check": False,
        }
        if ssh_secret:
            askpass_path = Path(__file__).with_name("ssh_askpass.sh")
            if not askpass_path.is_file() or not os.access(askpass_path, os.X_OK):
                raise RemoteWorkerError("SSH askpass 辅助程序未打包或不可执行")
            environment = os.environ.copy()
            environment.update(
                {
                    "DISPLAY": environment.get("DISPLAY") or ":0",
                    "SSH_ASKPASS": str(askpass_path),
                    "SSH_ASKPASS_REQUIRE": "force",
                    "WORKER_SSH_ASKPASS_SECRET": ssh_secret,
                }
            )
            run_options["env"] = environment
        result = subprocess.run(command, **run_options)
    except subprocess.TimeoutExpired as exc:
        raise RemoteWorkerError("远程 SSH 操作超时") from exc
    except OSError as exc:
        raise RemoteWorkerError("无法启动 SSH 客户端") from exc
    finally:
        if temporary_key_path is not None:
            temporary_key_path.unlink(missing_ok=True)
    output = _redact_output(result.stdout, result.stderr, credentials)
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


def _redact_output(
    stdout: str, stderr: str, credentials: Dict[str, str] | None = None
) -> Dict[str, str]:
    """从 SSH 输出中删除远程配置和平台 SSH 凭证。"""
    credentials = credentials or {
        "password": config.WORKER_SSH_PASSWORD,
        "passphrase": config.WORKER_SSH_KEY_PASSPHRASE,
        "private_key": "",
    }
    secrets = (
        config.WORKER_REMOTE_REDIS_URL,
        config.S3_ACCESS_KEY,
        config.S3_SECRET_KEY,
        credentials.get("password", ""),
        credentials.get("passphrase", ""),
        credentials.get("private_key", ""),
    )
    for secret in secrets:
        if secret:
            stdout = stdout.replace(secret, "[redacted]")
            stderr = stderr.replace(secret, "[redacted]")
    return {"stdout": stdout[-4000:], "stderr": stderr[-4000:]}
