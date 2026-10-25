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
import uuid
from pathlib import Path
from typing import Dict, List

from cryptography.fernet import Fernet, InvalidToken

import app.config as config
from app.state import manager


class RemoteWorkerError(RuntimeError):
    """远程配置或 SSH 操作失败。"""


class RemoteCredentialNotFoundError(RemoteWorkerError):
    """页面指定的 SSH 凭证不存在。"""


class RemoteCredentialConflictError(RemoteWorkerError):
    """SSH 凭证名称冲突，或仍被服务器引用而不能删除。"""


_MACHINE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")
_CREDENTIAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LEGACY_CREDENTIAL_ID = "legacy-default"
_CREDENTIAL_STORE_VERSION = 2
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


def _credential_store() -> Dict[str, object]:
    """解密页面凭证目录，并把旧版单凭证格式映射为默认目录项。"""
    encrypted = manager.lease.get_remote_ssh_credentials()
    if not encrypted:
        return {"version": _CREDENTIAL_STORE_VERSION, "default_credential_id": None, "credentials": []}
    try:
        payload = _credential_fernet().decrypt(encrypted.encode("ascii"))
        decoded = json.loads(payload.decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteWorkerError(
            "页面保存的 SSH 凭证无法解密；请检查平台加密主密钥"
        ) from exc

    # 兼容旧版加密对象：旧服务器记录没有凭证 ID，继续把该项作为默认凭证使用。
    if isinstance(decoded, dict) and decoded.get("auth_method") in {"key", "password"}:
        legacy = {
            "id": _LEGACY_CREDENTIAL_ID,
            "name": "旧版平台凭证",
            "username": "",
            "auth_method": decoded["auth_method"],
            "private_key": str(decoded.get("private_key", "")),
            "password": str(decoded.get("password", "")),
            "passphrase": str(decoded.get("passphrase", "")),
        }
        return {
            "version": _CREDENTIAL_STORE_VERSION,
            "default_credential_id": _LEGACY_CREDENTIAL_ID,
            "credentials": [legacy],
        }

    if not isinstance(decoded, dict) or decoded.get("version") != _CREDENTIAL_STORE_VERSION:
        raise RemoteWorkerError("页面 SSH 凭证目录格式无效")
    credentials = decoded.get("credentials")
    if not isinstance(credentials, list):
        raise RemoteWorkerError("页面 SSH 凭证目录格式无效")
    normalized: List[Dict[str, str]] = []
    for entry in credentials:
        if not isinstance(entry, dict):
            raise RemoteWorkerError("页面 SSH 凭证目录格式无效")
        credential_id = str(entry.get("id", ""))
        method = str(entry.get("auth_method", ""))
        if not _CREDENTIAL_ID_RE.fullmatch(credential_id) or method not in {
            "password", "key", "key_password"
        }:
            raise RemoteWorkerError("页面 SSH 凭证目录格式无效")
        normalized.append(
            {
                "id": credential_id,
                "name": str(entry.get("name", "")),
                "username": str(entry.get("username", "")),
                "auth_method": method,
                "private_key": str(entry.get("private_key", "")),
                "password": str(entry.get("password", "")),
                "passphrase": str(entry.get("passphrase", "")),
            }
        )
    default_id = str(decoded.get("default_credential_id") or "") or None
    if default_id and not any(item["id"] == default_id for item in normalized):
        default_id = normalized[0]["id"] if normalized else None
    if default_id is None and normalized:
        default_id = normalized[0]["id"]
    return {
        "version": _CREDENTIAL_STORE_VERSION,
        "default_credential_id": default_id,
        "credentials": normalized,
    }


def _save_credential_store(store: Dict[str, object]) -> None:
    """加密完整凭证目录后写入专用共享 Redis key。"""
    if not config.REDIS_URL or not manager.distributed:
        raise RemoteWorkerError("页面保存 SSH 凭证需要启用共享 Redis")
    serialized = json.dumps(store, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encrypted = _credential_fernet().encrypt(serialized).decode("ascii")
    manager.lease.set_remote_ssh_credentials(encrypted)


def _credentials_configurable() -> bool:
    """判断共享 Redis 与加密主密钥是否齐备。"""
    if not config.REDIS_URL or not manager.distributed:
        return False
    try:
        _credential_fernet()
        return True
    except RemoteWorkerError:
        return False


def _credential_metadata(credential: Dict[str, str], default_id: str | None) -> Dict[str, object]:
    """只生成凭证目录公开元数据，避免密钥、密码和口令进入 API 响应。"""
    return {
        "id": credential["id"],
        "name": credential["name"],
        "username": credential["username"],
        "auth_method": credential["auth_method"],
        "is_default": credential["id"] == default_id,
        "source": "page",
    }


def list_platform_ssh_credentials() -> Dict[str, object]:
    """列出凭证名称、用户名和认证类型，不返回任何凭证明文。"""
    store = _credential_store()
    credentials = store["credentials"]
    return {
        "credentials": [
            _credential_metadata(item, store["default_credential_id"])
            for item in credentials
        ],
        "default_credential_id": store["default_credential_id"],
        "can_configure": _credentials_configurable(),
    }


def _write_page_ssh_credential(
    credential: Dict[str, str], credential_id: str | None = None, make_default: bool = False
) -> Dict[str, object]:
    """新增或替换凭证目录项，存储时只写入 Fernet 密文。"""
    if not config.REDIS_URL or not manager.distributed:
        raise RemoteWorkerError("页面保存 SSH 凭证需要启用共享 Redis")
    fernet = _credential_fernet()
    store = _credential_store()
    current = store["credentials"]
    selected_id = credential_id or uuid.uuid4().hex
    if not _CREDENTIAL_ID_RE.fullmatch(selected_id):
        raise RemoteCredentialNotFoundError("SSH 凭证不存在")
    for item in current:
        if item["id"] != selected_id and item["name"].casefold() == credential["name"].casefold():
            raise RemoteCredentialConflictError("SSH 凭证名称已存在")
    replacement = {
        "id": selected_id,
        "name": credential["name"].strip(),
        "username": credential["username"].strip(),
        "auth_method": credential["auth_method"],
        "private_key": credential.get("private_key", ""),
        "password": credential.get("password", ""),
        "passphrase": credential.get("passphrase", ""),
    }
    found = False
    updated: List[Dict[str, str]] = []
    for item in current:
        if item["id"] == selected_id:
            updated.append(replacement)
            found = True
        else:
            updated.append(item)
    if credential_id and not found and selected_id != _LEGACY_CREDENTIAL_ID:
        raise RemoteCredentialNotFoundError("SSH 凭证不存在")
    if not found:
        updated.append(replacement)
    default_id = store["default_credential_id"] or selected_id
    if make_default:
        default_id = selected_id
    serialized = json.dumps(
        {
            "version": _CREDENTIAL_STORE_VERSION,
            "default_credential_id": default_id,
            "credentials": updated,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    manager.lease.set_remote_ssh_credentials(fernet.encrypt(serialized).decode("ascii"))
    return list_platform_ssh_credentials()


def create_platform_ssh_credential(credential: Dict[str, str]) -> Dict[str, object]:
    """添加一条具名页面 SSH 凭证，并在首条凭证时将其设为默认。"""
    return _write_page_ssh_credential(
        credential, make_default=bool(credential.get("make_default", False))
    )


def update_platform_ssh_credential(
    credential_id: str, credential: Dict[str, str]
) -> Dict[str, object]:
    """完整替换指定页面凭证；密钥与密码不会从目录接口回传。"""
    return _write_page_ssh_credential(
        credential,
        credential_id=credential_id,
        make_default=bool(credential.get("make_default", False)),
    )


def delete_platform_ssh_credential(credential_id: str) -> Dict[str, object]:
    """删除未被服务器显式引用的凭证，并在必要时重新选择默认项。"""
    if not config.REDIS_URL or not manager.distributed:
        raise RemoteWorkerError("页面管理 SSH 凭证需要启用共享 Redis")
    _credential_fernet()
    store = _credential_store()
    credentials = store["credentials"]
    if not any(item["id"] == credential_id for item in credentials):
        raise RemoteCredentialNotFoundError("SSH 凭证不存在")
    references = [
        str(server.get("machine_id", ""))
        for server in manager.lease.list_remote_servers()
        if str(server.get("ssh_credential_id", "")) == credential_id
        or (
            not server.get("ssh_credential_id")
            and store["default_credential_id"] == credential_id
        )
    ]
    if references:
        raise RemoteCredentialConflictError(
            "SSH 凭证仍被服务器引用：" + ", ".join(references[:8])
        )
    remaining = [item for item in credentials if item["id"] != credential_id]
    default_id = store["default_credential_id"]
    if default_id == credential_id:
        default_id = remaining[0]["id"] if remaining else None
    if remaining:
        _save_credential_store(
            {
                "version": _CREDENTIAL_STORE_VERSION,
                "default_credential_id": default_id,
                "credentials": remaining,
            }
        )
    else:
        manager.lease.delete_remote_ssh_credentials()
    return list_platform_ssh_credentials()


def _get_ssh_credential(credential_id: str | None = None) -> Dict[str, str]:
    """按 ID 或默认项读取页面凭证；不存在时明确要求先在页面配置。"""
    store = _credential_store()
    selected_id = credential_id or store["default_credential_id"]
    if not selected_id:
        raise RemoteWorkerError("请先在页面配置 SSH 凭证")
    for item in store["credentials"]:
        if item["id"] == selected_id:
            return {**item, "source": "page"}
    raise RemoteCredentialNotFoundError("服务器选择的 SSH 凭证不存在，请重新选择凭证")


def _ssh_credentials_for_server(server: Dict[str, object]) -> Dict[str, str]:
    """解析服务器指定的凭证和用户名覆盖规则，兼容旧服务器记录。"""
    credentials = _get_ssh_credential(
        str(server.get("ssh_credential_id", "")) or None
    )
    if "username_override" in server:
        username = str(server.get("username_override") or credentials.get("username", ""))
    else:
        # 旧服务器已有的 username 代表显式配置，升级后继续优先使用它。
        username = str(server.get("username") or credentials.get("username", ""))
    credentials["username"] = username
    if not _USERNAME_RE.fullmatch(username):
        raise RemoteWorkerError("SSH 用户名无效；请在服务器配置用户名或选择带用户名的凭证")
    return credentials


def resolve_remote_server_credentials(server: Dict[str, object]) -> Dict[str, str]:
    """为服务器解析页面凭证，并按服务器用户名覆盖凭证用户名。"""
    return _ssh_credentials_for_server(server)


def _platform_ssh_credentials() -> Dict[str, str]:
    """兼容旧调用方，读取页面凭证目录的默认凭证。"""
    return _get_ssh_credential()


def remote_ssh_settings_status() -> Dict[str, object]:
    """兼容旧单凭证接口，只返回默认凭证状态和认证方式。"""
    try:
        store = _credential_store()
        default_id = store["default_credential_id"]
        credentials = next(
            (item for item in store["credentials"] if item["id"] == default_id), None
        )
    except RemoteWorkerError:
        return {
            "source": "page" if manager.lease.get_remote_ssh_credentials() else "none",
            "auth_method": None,
            "configured": False,
            "can_configure": _credentials_configurable(),
            "status_error": "页面凭证无法解密；请检查平台加密主密钥。",
        }
    if credentials is None:
        return {
            "source": "none",
            "auth_method": None,
            "configured": False,
            "can_configure": _credentials_configurable(),
        }
    method = credentials["auth_method"]
    configured = bool(
        (credentials.get("private_key") if method in {"key", "key_password"} else True)
        and (credentials.get("password") if method in {"password", "key_password"} else True)
    )
    return {
        "source": "page",
        "auth_method": method,
        "configured": configured,
        "can_configure": _credentials_configurable(),
    }


def store_platform_ssh_credentials(credentials: Dict[str, str]) -> Dict[str, object]:
    """兼容旧单凭证 API，将输入加密保存为页面默认凭证。"""
    legacy = {
        "name": "旧版平台凭证",
        "username": str(credentials.get("username", "")),
        "auth_method": credentials["auth_method"],
        "private_key": str(credentials.get("private_key", "")),
        "password": str(credentials.get("password", "")),
        "passphrase": str(credentials.get("passphrase", "")),
    }
    _write_page_ssh_credential(legacy, credential_id=_LEGACY_CREDENTIAL_ID, make_default=True)
    return remote_ssh_settings_status()


def clear_platform_ssh_credentials() -> Dict[str, object]:
    """兼容旧单凭证接口，只清除迁移自旧版的默认凭证。"""
    store = _credential_store()
    credentials = store["credentials"]
    legacy_exists = any(item["id"] == _LEGACY_CREDENTIAL_ID for item in credentials)
    if legacy_exists:
        delete_platform_ssh_credential(_LEGACY_CREDENTIAL_ID)
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
                "source": "page",
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
    if auth_method not in {"key", "password", "key_password"}:
        errors.append("请先在页面配置 SSH 凭证")
    if auth_method in {"key", "key_password"}:
        if not credentials.get("private_key"):
            errors.append("页面未配置 SSH 私钥")
    if auth_method in {"password", "key_password"} and not credentials.get("password"):
        errors.append("页面凭证缺少 SSH 登录密码")
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
    ssh_secrets = (credentials.get("password", ""), credentials.get("passphrase", ""))
    if any("\n" in secret or "\r" in secret or "\x00" in secret for secret in ssh_secrets):
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
    credentials = _ssh_credentials_for_server(server)
    errors = remote_configuration_errors(credentials)
    if errors:
        raise RemoteWorkerError("；".join(errors))
    resolved_server = dict(server)
    resolved_server["username"] = credentials["username"]
    validate_server_record(resolved_server)
    ssh_path = shutil.which("ssh")
    if not ssh_path:
        raise RemoteWorkerError("平台后端容器未安装 OpenSSH 客户端")
    host = str(server["host"])
    ssh_host = f"[{host}]" if ":" in host else host
    target = f"{resolved_server['username']}@{ssh_host}"
    command = [ssh_path]
    temporary_key_path: Path | None = None
    auth_method = credentials["auth_method"]
    if auth_method in {"key", "key_password"}:
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
        command.extend(["-i", str(temporary_key_path)])
        command.extend(["-o", "IdentitiesOnly=yes"])
        command.extend(["-o", "PubkeyAuthentication=yes"])
        command.extend(
            ["-o", "PasswordAuthentication=yes" if auth_method == "key_password" else "PasswordAuthentication=no"]
        )
        command.extend(["-o", "KbdInteractiveAuthentication=no"])
        if auth_method == "key_password":
            command.extend(["-o", "PreferredAuthentications=publickey,password"])
            command.extend(["-o", "NumberOfPasswordPrompts=1"])
            command.extend(["-o", "BatchMode=no"])
        else:
            command.extend(["-o", "PreferredAuthentications=publickey"])
            command.extend(["-o", "BatchMode=no" if credentials.get("passphrase") else "BatchMode=yes"])
    else:
        command.extend(["-o", "PubkeyAuthentication=no"])
        command.extend(["-o", "PasswordAuthentication=yes"])
        command.extend(["-o", "KbdInteractiveAuthentication=no"])
        command.extend(["-o", "PreferredAuthentications=password"])
        command.extend(["-o", "NumberOfPasswordPrompts=1"])
        command.extend(["-o", "BatchMode=no"])
    ssh_password = credentials.get("password", "")
    ssh_passphrase = credentials.get("passphrase", "")
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
            "input": render_remote_script(resolved_server, action),
            "text": True,
            "capture_output": True,
            "timeout": timeout,
            "check": False,
        }
        if ssh_password or ssh_passphrase:
            askpass_path = Path(__file__).with_name("ssh_askpass.sh")
            if not askpass_path.is_file() or not os.access(askpass_path, os.X_OK):
                raise RemoteWorkerError("SSH askpass 辅助程序未打包或不可执行")
            environment = os.environ.copy()
            for legacy_key in (
                "WORKER_SSH_AUTH_METHOD",
                "WORKER_SSH_PASSWORD",
                "WORKER_SSH_KEY_PATH",
                "WORKER_SSH_KEY_PASSPHRASE",
                "WORKER_SSH_ASKPASS_SECRET",
            ):
                environment.pop(legacy_key, None)
            environment.update(
                {
                    "DISPLAY": environment.get("DISPLAY") or ":0",
                    "SSH_ASKPASS": str(askpass_path),
                    "SSH_ASKPASS_REQUIRE": "force",
                    "WORKER_SSH_ASKPASS_PASSWORD": ssh_password,
                    "WORKER_SSH_ASKPASS_PASSPHRASE": ssh_passphrase,
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
    credentials = credentials or {}
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
