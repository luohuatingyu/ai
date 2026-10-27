"""远程 worker 管理：校验接入参数、经 SSH 执行单个部署脚本并保存操作状态。"""
from __future__ import annotations

import http.client
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken

import app.config as config
from app import remote_config_repository
from app.state import manager


class RemoteWorkerError(RuntimeError):
    """远程配置或 SSH 操作失败。"""


class RemoteWorkerCancelledError(RemoteWorkerError):
    """远程 SSH 操作已被用户中断。"""


class RemoteCredentialNotFoundError(RemoteWorkerError):
    """页面指定的 SSH 凭证不存在。"""


class RemoteCredentialConflictError(RemoteWorkerError):
    """SSH 凭证名称冲突，或仍被服务器引用而不能删除。"""


_ACTIVE_REMOTE_OPERATIONS: Dict[str, threading.Event] = {}
_ACTIVE_REMOTE_OPERATIONS_LOCK = threading.Lock()
_REMOTE_OPERATION_LOG_LOCK = threading.Lock()
_REMOTE_OPERATION_LOG_LIMIT = 24_000
_WORKER_STARTUP_HEARTBEAT_TIMEOUT = 15.0


def _append_remote_operation_log(
    operation_id: str | None,
    phase: str,
    message: str,
    *,
    credentials: Dict[str, str] | None = None,
    server: Dict[str, object] | None = None,
) -> None:
    """追加有界且脱敏的远程操作日志，并同步更新界面展示阶段。"""
    if not operation_id:
        return
    try:
        with _REMOTE_OPERATION_LOG_LOCK:
            safe_message = _redact_output(str(message), "", credentials, server)["stdout"].strip()
            if not safe_message:
                return
            operation = manager.lease.get_remote_operation(operation_id)
            if operation is None:
                return
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            entry = f"[{timestamp}] [{phase}] {safe_message}"
            current = str(operation.get("log", ""))
            updated = f"{current}\n{entry}" if current else entry
            if len(updated) > _REMOTE_OPERATION_LOG_LIMIT:
                marker = "[较早日志已截断]\n"
                updated = marker + updated[-(_REMOTE_OPERATION_LOG_LIMIT - len(marker)):]
            now = time.time()
            if operation.get("phase") != phase:
                operation["phase_started_at"] = now
            operation.update({"phase": phase, "log": updated, "updated_at": now})
            manager.lease.set_remote_operation(operation_id, operation)
    except Exception:
        # 诊断日志失败不能中断实际 SSH 部署。
        return


def _emit_progress(
    callback: Callable[[str, str], None] | None,
    phase: str,
    message: str,
) -> None:
    """向操作记录发送进度；日志存储异常不影响远程命令执行。"""
    if callback is None:
        return
    try:
        callback(phase, message)
    except Exception:
        return


def request_remote_operation_cancel(operation_id: str) -> bool:
    """登记远程操作的中断请求，并唤醒当前进程中的执行线程。"""
    manager.lease.request_remote_operation_cancel(operation_id)
    with _ACTIVE_REMOTE_OPERATIONS_LOCK:
        cancellation_event = _ACTIVE_REMOTE_OPERATIONS.get(operation_id)
        if cancellation_event is not None:
            cancellation_event.set()
            return True
    return False


def is_remote_operation_active(operation_id: str) -> bool:
    """检查当前 API 进程是否正在执行指定的远程操作。"""
    with _ACTIVE_REMOTE_OPERATIONS_LOCK:
        return operation_id in _ACTIVE_REMOTE_OPERATIONS


def _register_remote_operation(operation_id: str) -> threading.Event:
    """登记远程操作执行上下文，并接收此前已经到达的中断请求。"""
    cancellation_event = threading.Event()
    with _ACTIVE_REMOTE_OPERATIONS_LOCK:
        _ACTIVE_REMOTE_OPERATIONS[operation_id] = cancellation_event
        if manager.lease.is_remote_operation_cancel_requested(operation_id):
            cancellation_event.set()
    return cancellation_event


def _unregister_remote_operation(operation_id: str, cancellation_event: threading.Event) -> None:
    """移除已结束操作的执行上下文，并清除中断信号。"""
    with _ACTIVE_REMOTE_OPERATIONS_LOCK:
        if _ACTIVE_REMOTE_OPERATIONS.get(operation_id) is cancellation_event:
            _ACTIVE_REMOTE_OPERATIONS.pop(operation_id, None)
    manager.lease.clear_remote_operation_cancel(operation_id)


def _remote_operation_cancel_requested(operation_id: str | None) -> bool:
    """检查进程内事件和共享协调存储中的远程操作中断信号。"""
    if not operation_id:
        return False
    with _ACTIVE_REMOTE_OPERATIONS_LOCK:
        cancellation_event = _ACTIVE_REMOTE_OPERATIONS.get(operation_id)
        if cancellation_event is not None and cancellation_event.is_set():
            return True
    return manager.lease.is_remote_operation_cancel_requested(operation_id)


def _terminate_remote_process(process: subprocess.Popen) -> None:
    """终止本机 SSH 客户端并收集管道输出，避免遗留子进程。"""
    if process.poll() is not None:
        process.communicate()
        return
    process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise RemoteWorkerError("无法停止本机 SSH 客户端") from exc


def _communicate_remote_process(
    process: subprocess.Popen,
    operation_id: str | None,
    timeout: int | None,
    input_data: str | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    progress_phase: str = "远程命令",
):
    """轮询 SSH 子进程，实时转发输出并保留中断与超时语义。"""
    started_at = time.monotonic()
    pending_input = input_data
    seen_output = {"stdout": "", "stderr": ""}
    pending_lines = {"stdout": "", "stderr": ""}

    def consume_output(name: str, value: object, flush: bool = False) -> None:
        """仅将新增完整行发送给进度回调，避免重复显示 communicate 缓冲区。"""
        if value is None:
            return
        if isinstance(value, bytes):
            current = value.decode("utf-8", "replace")
        else:
            current = str(value)
        previous = seen_output[name]
        if current.startswith(previous):
            delta = current[len(previous):]
            seen_output[name] = current
        elif previous.startswith(current):
            # 某些 Python 版本在重复 communicate 超时时只返回本轮新增字节。
            delta = current
            seen_output[name] = previous + current
        else:
            delta = current
            seen_output[name] = current
        pending_lines[name] += delta
        chunks = pending_lines[name].split("\n")
        pending_lines[name] = chunks.pop()
        for line in chunks:
            line = line.rstrip("\r")
            if line:
                _emit_progress(progress_callback, progress_phase, f"{name}: {line}")
        if flush and pending_lines[name]:
            line = pending_lines[name].rstrip("\r")
            pending_lines[name] = ""
            if line:
                _emit_progress(progress_callback, progress_phase, f"{name}: {line}")

    while True:
        if _remote_operation_cancel_requested(operation_id):
            _terminate_remote_process(process)
            raise RemoteWorkerCancelledError("操作已中断；远端可能已有部分变更")
        remaining = None if timeout is None else timeout - (time.monotonic() - started_at)
        if remaining is not None and remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            consume_output("stdout", stdout, flush=True)
            consume_output("stderr", stderr, flush=True)
            raise subprocess.TimeoutExpired(
                process.args, timeout, output=stdout, stderr=stderr
            )
        poll_interval = 0.25 if remaining is None else min(0.25, remaining)
        try:
            output = process.communicate(input=pending_input, timeout=poll_interval)
            consume_output("stdout", output[0], flush=True)
            consume_output("stderr", output[1], flush=True)
            return output
        except subprocess.TimeoutExpired as exc:
            consume_output("stdout", exc.output)
            consume_output("stderr", exc.stderr)
            pending_input = None


def _run_remote_subprocess(
    command: List[str],
    *,
    input_data: str,
    timeout: int,
    environment: Dict[str, str] | None,
    operation_id: str | None,
    progress_callback: Callable[[str, str], None] | None = None,
    progress_phase: str = "远程命令",
) -> subprocess.CompletedProcess:
    """启动可中断的 SSH 命令，实时转发 stdout/stderr 并返回结果对象。"""
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        stdout, stderr = _communicate_remote_process(
            process,
            operation_id,
            timeout,
            input_data=input_data,
            progress_callback=progress_callback,
            progress_phase=progress_phase,
        )
    except BaseException:
        if process.poll() is None:
            _terminate_remote_process(process)
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


class _DockerSocketHTTPConnection(http.client.HTTPConnection):
    """通过 Unix socket 访问平台 Docker Engine API。"""

    def __init__(self, socket_path: str, timeout: float = 60.0):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        """建立到宿主 Docker socket 的 HTTP 连接。"""
        docker_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        docker_socket.settimeout(self.timeout)
        try:
            docker_socket.connect(self.socket_path)
        except OSError:
            docker_socket.close()
            raise
        self.sock = docker_socket


def _local_docker_image_platform(image: str) -> str:
    """读取平台 Docker 中镜像的操作系统与 CPU 架构。"""
    socket_path = config.WORKER_DOCKER_SOCKET_PATH
    if not socket_path or not Path(socket_path).exists():
        raise RemoteWorkerError(
            "无法访问平台 Docker socket；请使用远程管理 Compose 配置启动 trainer"
        )
    version_connection = _DockerSocketHTTPConnection(socket_path)
    try:
        version_connection.request("GET", "/version")
        version_response = version_connection.getresponse()
        if version_response.status != 200:
            raise RemoteWorkerError("无法读取平台 Docker Engine API 版本")
        version_data = json.loads(version_response.read().decode("utf-8"))
        api_version = str(version_data.get("ApiVersion", ""))
    except RemoteWorkerError:
        raise
    except (OSError, http.client.HTTPException, ValueError, UnicodeError) as exc:
        raise RemoteWorkerError("无法连接平台 Docker Engine；请检查 Docker socket 挂载") from exc
    finally:
        version_connection.close()
    if not re.fullmatch(r"\d+\.\d+", api_version):
        raise RemoteWorkerError("平台 Docker Engine 返回了无效的 API 版本")

    image_connection = _DockerSocketHTTPConnection(socket_path)
    target = f"/v{api_version}/images/{quote(image, safe='')}/json"
    try:
        image_connection.request("GET", target)
        image_response = image_connection.getresponse()
        if image_response.status == 404:
            raise RemoteWorkerError(f"平台 Docker 中不存在镜像标签 {image}")
        if image_response.status != 200:
            raise RemoteWorkerError("无法读取平台 worker 镜像的平台信息")
        image_data = json.loads(image_response.read().decode("utf-8"))
        operating_system = str(image_data.get("Os", ""))
        architecture = str(image_data.get("Architecture", ""))
        if not operating_system or not architecture:
            raise RemoteWorkerError("平台 worker 镜像缺少操作系统或 CPU 架构信息")
        return f"{operating_system}/{architecture}"
    except RemoteWorkerError:
        raise
    except (OSError, http.client.HTTPException, ValueError, UnicodeError) as exc:
        raise RemoteWorkerError("读取平台 worker 镜像平台信息失败") from exc
    finally:
        image_connection.close()


def _canonical_docker_platform(platform: str) -> str:
    """规范化 Docker 操作系统和架构名称，兼容常见系统别名。"""
    parts = platform.strip().lower().split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[a-z0-9_.-]+", part) for part in parts):
        raise RemoteWorkerError("Docker 返回了无效的平台标识")
    operating_system, architecture = parts
    architecture_aliases = {
        "x86_64": "amd64",
        "x64": "amd64",
        "aarch64": "arm64",
        "armv7l": "arm",
    }
    architecture = architecture_aliases.get(architecture, architecture)
    return f"{operating_system}/{architecture}"


def _worker_build_context(context_roots: List[Path] | None = None) -> bytes:
    """从可信后端目录打包 worker 构建上下文，拒绝同名的错误 Dockerfile。"""
    import io
    import tarfile

    context_paths = (
        "Dockerfile",
        "requirements.txt",
        "app",
        "static",
        "sample_data",
        "init_db.sh",
        "start.sh",
    )
    roots = context_roots or [
        config.APP_ROOT.parent,
        Path("/app"),
        Path(__file__).resolve().parents[1],
    ]
    context_root = None
    for candidate in dict.fromkeys(Path(root).resolve() for root in roots):
        dockerfile = candidate / "Dockerfile"
        try:
            dockerfile_text = dockerfile.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if (
            "dict(os.environ)" in dockerfile_text
            or not re.search(r"(?m)^FROM\s+\S+", dockerfile_text)
            or "COPY app ./app" not in dockerfile_text
            or "COPY Dockerfile ./Dockerfile" not in dockerfile_text
        ):
            continue
        if all((candidate / relative_path).exists() for relative_path in context_paths):
            context_root = candidate
            break
    if context_root is None:
        raise RemoteWorkerError(
            "无法自动构建 worker 镜像：未找到完整且有效的后端 Docker 构建目录"
        )

    archive_buffer = io.BytesIO()
    try:
        # Docker Engine 需要直接识别 tar 上下文；Python 默认 PAX 扩展头会被部分
        # BuildKit API 路径当作 Dockerfile 文本解析，继而误读 app/worker.py 内容。
        with tarfile.open(
            fileobj=archive_buffer, mode="w", format=tarfile.GNU_FORMAT
        ) as archive:
            for relative_path in context_paths:
                source = context_root / relative_path
                if not source.exists():
                    raise RemoteWorkerError(
                        f"无法自动构建 worker 镜像：构建文件 {relative_path} 不存在"
                    )
                archive.add(
                    source,
                    arcname=relative_path,
                    recursive=True,
                    filter=lambda item: None
                    if Path(item.name).name in {"__pycache__", ".pytest_cache"}
                    or item.name.endswith((".pyc", ".pyo"))
                    else item,
                )
    except RemoteWorkerError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise RemoteWorkerError("打包 worker 镜像构建上下文失败") from exc
    return archive_buffer.getvalue()


def _build_local_docker_image_for_platform(
    image: str,
    platform: str,
    operation_id: str | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> None:
    """通过平台 Docker Engine 为目标架构重建 worker 镜像并校验结果。"""
    canonical_platform = _canonical_docker_platform(platform)
    if not canonical_platform.startswith("linux/"):
        raise RemoteWorkerError(f"远程 worker 暂不支持目标镜像平台 {platform}")

    socket_path = config.WORKER_DOCKER_SOCKET_PATH
    if not socket_path or not Path(socket_path).exists():
        raise RemoteWorkerError(
            "无法访问平台 Docker socket；请使用远程管理 Compose 配置启动 trainer"
        )
    version_connection = _DockerSocketHTTPConnection(socket_path)
    try:
        version_connection.request("GET", "/version")
        version_response = version_connection.getresponse()
        if version_response.status != 200:
            raise RemoteWorkerError("无法读取平台 Docker Engine API 版本")
        version_data = json.loads(version_response.read().decode("utf-8"))
        api_version = str(version_data.get("ApiVersion", ""))
    except RemoteWorkerError:
        raise
    except (OSError, http.client.HTTPException, ValueError, UnicodeError) as exc:
        raise RemoteWorkerError("无法连接平台 Docker Engine；请检查 Docker socket 挂载") from exc
    finally:
        version_connection.close()
    if not re.fullmatch(r"\d+\.\d+", api_version):
        raise RemoteWorkerError("平台 Docker Engine 返回了无效的 API 版本")

    query = urlencode(
        {"t": image, "dockerfile": "Dockerfile", "platform": canonical_platform, "version": "2"}
    )
    build_connection = _DockerSocketHTTPConnection(socket_path, timeout=3600)

    build_result: Dict[str, Exception] = {}
    build_finished = threading.Event()

    def build_image() -> None:
        """在独立线程读取 Docker 构建流，供取消操作关闭底层连接。"""
        build_response = None
        try:
            build_connection.request(
                "POST",
                f"/v{api_version}/build?{query}",
                body=_worker_build_context(),
                headers={"Content-Type": "application/x-tar"},
            )
            build_response = build_connection.getresponse()
            if build_response.status != 200:
                detail = build_response.read(2048).decode("utf-8", "replace").strip()
                raise RemoteWorkerError(
                    f"平台 Docker 自动构建 {canonical_platform} worker 镜像失败：{detail[:1000]}"
                )
            errors = []
            while True:
                line = build_response.readline()
                if not line:
                    break
                try:
                    event = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeError):
                    continue
                stream = str(event.get("stream") or "").strip()
                if stream:
                    _emit_progress(progress_callback, "构建目标架构镜像", stream)
                if event.get("error"):
                    error_detail = event.get("errorDetail") or {}
                    detail = str(error_detail.get("message") or event["error"])
                    errors.append(detail.strip())
                    _emit_progress(progress_callback, "构建目标架构镜像", f"Docker 构建错误：{detail.strip()}")
            if errors:
                detail = errors[-1]
                raise RemoteWorkerError(
                    f"平台 Docker 自动构建 {canonical_platform} worker 镜像失败：{detail[-1000:]}"
                )
        except RemoteWorkerError as exc:
            build_result["error"] = exc
        except (OSError, http.client.HTTPException, ValueError, UnicodeError) as exc:
            build_result["error"] = RemoteWorkerError(
                f"平台 Docker 自动构建 {canonical_platform} worker 镜像失败"
            )
            build_result["cause"] = exc
        except Exception as exc:
            build_result["error"] = RemoteWorkerError(
                f"平台 Docker 自动构建 {canonical_platform} worker 镜像失败"
            )
            build_result["cause"] = exc
        finally:
            if build_response is not None:
                build_response.close()
            build_connection.close()
            build_finished.set()

    build_thread = threading.Thread(target=build_image, name="remote-worker-image-build", daemon=True)
    build_thread.start()
    while not build_finished.wait(timeout=0.25):
        if _remote_operation_cancel_requested(operation_id):
            docker_socket = getattr(build_connection, "sock", None)
            if docker_socket is not None:
                try:
                    docker_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            build_connection.close()
            build_thread.join(timeout=5)
            raise RemoteWorkerCancelledError("操作已中断；远端尚未收到本次镜像部署")
    build_thread.join()
    if _remote_operation_cancel_requested(operation_id):
        raise RemoteWorkerCancelledError("操作已中断；远端尚未收到本次镜像部署")
    if "error" in build_result:
        raise build_result["error"] from build_result.get("cause")

    built_platform = _canonical_docker_platform(_local_docker_image_platform(image))
    if built_platform != canonical_platform:
        raise RemoteWorkerError(
            f"平台 Docker 构建后的 worker 镜像架构 {built_platform} 与目标架构 "
            f"{canonical_platform} 不一致"
        )


def _remote_docker_image_platform(
    ssh_command: List[str],
    environment: Dict[str, str] | None,
    credentials: Dict[str, str],
    server: Dict[str, object],
    timeout: int,
    operation_id: str | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> str:
    """通过 SSH 读取目标 Docker 平台，并对错误输出做凭证脱敏。"""
    command = ssh_command[:-1] + ["docker info --format '{{.OSType}}/{{.Architecture}}'"]
    try:
        progress_options = (
            {"progress_callback": progress_callback, "progress_phase": "检查远端 Docker 平台"}
            if progress_callback is not None else {}
        )
        result = _run_remote_subprocess(
            command,
            input_data="",
            timeout=min(timeout, 60),
            environment=environment,
            operation_id=operation_id,
            **progress_options,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteWorkerError("读取目标 Docker 平台信息超时") from exc
    except OSError as exc:
        raise RemoteWorkerError("无法启动 SSH 客户端读取目标 Docker 平台") from exc

    output = _redact_output(result.stdout, result.stderr, credentials, server)
    if result.returncode != 0:
        detail = (output["stderr"] or output["stdout"] or "目标 Docker 平台检查失败").strip()
        raise RemoteWorkerError(detail[-1000:])
    platform = output["stdout"].strip()
    _canonical_docker_platform(platform)
    return platform


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
    try:
        encrypted = remote_config_repository.get_ssh_credentials_ciphertext()
    except remote_config_repository.RemoteConfigPersistenceError as exc:
        raise RemoteWorkerError(str(exc)) from exc
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
    """加密完整凭证目录后写入 MySQL 专用密文记录。"""
    serialized = json.dumps(store, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encrypted = _credential_fernet().encrypt(serialized).decode("ascii")
    try:
        remote_config_repository.save_ssh_credentials_ciphertext(encrypted)
    except remote_config_repository.RemoteConfigPersistenceError as exc:
        raise RemoteWorkerError(str(exc)) from exc


def _credentials_configurable() -> bool:
    """判断 MySQL 持久化与 Fernet 加密主密钥是否已启用。"""
    if not config.USE_DATABASE:
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
    if not config.USE_DATABASE:
        raise RemoteWorkerError("页面保存 SSH 凭证需要启用 MySQL（USE_DATABASE=true）")
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
    try:
        remote_config_repository.save_ssh_credentials_ciphertext(
            fernet.encrypt(serialized).decode("ascii")
        )
    except remote_config_repository.RemoteConfigPersistenceError as exc:
        raise RemoteWorkerError(str(exc)) from exc
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
    if not config.USE_DATABASE:
        raise RemoteWorkerError("页面管理 SSH 凭证需要启用 MySQL（USE_DATABASE=true）")
    _credential_fernet()
    store = _credential_store()
    credentials = store["credentials"]
    if not any(item["id"] == credential_id for item in credentials):
        raise RemoteCredentialNotFoundError("SSH 凭证不存在")
    try:
        servers = remote_config_repository.list_remote_servers()
    except remote_config_repository.RemoteConfigPersistenceError as exc:
        raise RemoteWorkerError(str(exc)) from exc
    references = [
        str(server.get("machine_id", ""))
        for server in servers
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
        try:
            remote_config_repository.delete_ssh_credentials()
        except remote_config_repository.RemoteConfigPersistenceError as exc:
            raise RemoteWorkerError(str(exc)) from exc
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


def apply_remote_server_overrides(
    server: Dict[str, object], overrides: Dict[str, object], *, is_update: bool = False
) -> Dict[str, object]:
    """应用服务器级部署覆盖值，并将可能含密码的 Redis URL 加密后保存。"""
    updated = dict(server)
    for request_key, stored_key in (
        ("worker_image", "worker_image_override"),
        ("remote_s3_endpoint", "remote_s3_endpoint_override"),
    ):
        if request_key not in overrides:
            continue
        value = str(overrides.get(request_key) or "").strip()
        updated[stored_key] = value or None

    redis_was_sent = "remote_redis_url" in overrides
    redis_url = str(overrides.get("remote_redis_url") or "").strip()
    clear_redis = bool(overrides.get("clear_remote_redis_url_override"))
    if clear_redis:
        updated.pop("remote_redis_url_encrypted", None)
    elif redis_was_sent and redis_url:
        updated["remote_redis_url_encrypted"] = _credential_fernet().encrypt(
            redis_url.encode("utf-8")
        ).decode("ascii")
    elif redis_was_sent and not is_update:
        updated.pop("remote_redis_url_encrypted", None)

    # 兼容请求构造时的临时字段，绝不把 Redis URL 明文留在服务器记录中。
    updated.pop("remote_redis_url", None)
    updated.pop("clear_remote_redis_url_override", None)
    return updated


def remote_server_public_view(server: Dict[str, object]) -> Dict[str, object]:
    """返回服务器可公开配置，隐藏加密 Redis URL 并只暴露是否设置覆盖值。"""
    public = dict(server)
    encrypted_redis_url = public.pop("remote_redis_url_encrypted", None)
    public.pop("remote_redis_url", None)
    public.pop("clear_remote_redis_url_override", None)
    public["has_remote_redis_url_override"] = bool(encrypted_redis_url)
    return public


def remote_deployment_settings(server: Dict[str, object] | None = None) -> Dict[str, str]:
    """解析单机覆盖值与平台默认值，返回远程 worker 所需的部署配置。"""
    server = server or {}
    encrypted_redis_url = str(server.get("remote_redis_url_encrypted", "") or "")
    redis_override = ""
    if encrypted_redis_url:
        try:
            redis_override = _credential_fernet().decrypt(
                encrypted_redis_url.encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise RemoteWorkerError("服务器 Redis 覆盖配置无法解密；请检查平台加密主密钥") from exc
    return {
        "worker_image": str(
            server.get("worker_image_override") or config.WORKER_REMOTE_IMAGE or ""
        ).strip(),
        "redis_url": str(
            redis_override or config.WORKER_REMOTE_REDIS_URL or config.REDIS_URL or ""
        ).strip(),
        "s3_endpoint": str(
            server.get("remote_s3_endpoint_override")
            or config.WORKER_REMOTE_S3_ENDPOINT
            or config.S3_ENDPOINT
            or ""
        ).strip(),
    }


def _open_local_docker_image_archive(
    image: str,
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    """通过宿主 Docker Engine API 打开指定本地镜像的 tar 流。"""
    socket_path = config.WORKER_DOCKER_SOCKET_PATH
    if not socket_path or not Path(socket_path).exists():
        raise RemoteWorkerError(
            "无法访问平台 Docker socket；请使用远程管理 Compose 配置启动 trainer"
        )

    version_connection = _DockerSocketHTTPConnection(socket_path)
    try:
        version_connection.request("GET", "/version")
        version_response = version_connection.getresponse()
        if version_response.status != 200:
            detail = version_response.read(1024).decode("utf-8", "replace").strip()
            raise RemoteWorkerError(f"无法读取平台 Docker Engine API 版本：{detail[:300]}")
        version_data = json.loads(version_response.read().decode("utf-8"))
        api_version = str(version_data.get("ApiVersion", ""))
    except RemoteWorkerError:
        raise
    except (OSError, http.client.HTTPException, ValueError, UnicodeError) as exc:
        raise RemoteWorkerError("无法连接平台 Docker Engine；请检查 Docker socket 挂载") from exc
    finally:
        version_connection.close()
    if not re.fullmatch(r"\d+\.\d+", api_version):
        raise RemoteWorkerError("平台 Docker Engine 返回了无效的 API 版本")

    archive_connection = _DockerSocketHTTPConnection(socket_path)
    target = f"/v{api_version}/images/{quote(image, safe='')}/get"
    try:
        archive_connection.request("GET", target)
        archive_response = archive_connection.getresponse()
        if archive_response.status != 200:
            detail = archive_response.read(2048).decode("utf-8", "replace").strip()
            archive_connection.close()
            if archive_response.status == 404:
                raise RemoteWorkerError(f"平台 Docker 中不存在镜像标签 {image}")
            raise RemoteWorkerError(
                f"平台 Docker 无法导出镜像标签 {image}：{detail[:500]}"
            )
        return archive_connection, archive_response
    except RemoteWorkerError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        archive_connection.close()
        raise RemoteWorkerError("从平台 Docker 导出 worker 镜像失败") from exc


def _stream_worker_image_to_remote(
    image: str,
    ssh_command: List[str],
    environment: Dict[str, str] | None,
    credentials: Dict[str, str],
    server: Dict[str, object],
    timeout: int,
    operation_id: str | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> Dict[str, str]:
    """把 Docker Engine 镜像 tar 流经 SSH stdin 传给目标机 docker load。"""
    docker_connection, image_response = _open_local_docker_image_archive(image)
    read_fd = -1
    write_fd = -1
    process: subprocess.Popen[bytes] | None = None
    writer: threading.Thread | None = None
    writer_errors: List[Exception] = []
    transferred_bytes = 0
    next_progress_bytes = 64 * 1024 * 1024
    try:
        content_length = int(image_response.getheader("Content-Length") or 0)
    except (AttributeError, TypeError, ValueError):
        content_length = 0
    try:
        read_fd, write_fd = os.pipe()
        try:
            process = subprocess.Popen(
                ssh_command,
                stdin=read_fd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
        except OSError as exc:
            raise RemoteWorkerError("无法启动 SSH 镜像传输") from exc
        os.close(read_fd)
        read_fd = -1

        def pump_image_archive(pipe_fd: int) -> None:
            """按块复制 Docker Engine 响应到 SSH 管道，避免缓存整个镜像。"""
            nonlocal transferred_bytes, next_progress_bytes
            try:
                with os.fdopen(pipe_fd, "wb") as destination:
                    while True:
                        chunk = image_response.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        transferred_bytes += len(chunk)
                        if transferred_bytes >= next_progress_bytes:
                            transferred_mb = transferred_bytes / (1024 * 1024)
                            if content_length:
                                total_mb = content_length / (1024 * 1024)
                                message = f"已发送 {transferred_mb:.0f}/{total_mb:.0f} MiB"
                            else:
                                message = f"已发送 {transferred_mb:.0f} MiB"
                            _emit_progress(progress_callback, "传输并加载 worker 镜像", message)
                            next_progress_bytes += 64 * 1024 * 1024
            except (OSError, http.client.HTTPException, ValueError) as exc:
                writer_errors.append(exc)

        writer = threading.Thread(
            target=pump_image_archive, args=(write_fd,), name="remote-image-stream", daemon=True
        )
        writer.start()
        write_fd = -1
        try:
            _emit_progress(progress_callback, "传输并加载 worker 镜像", "远端正在执行 docker load。")
            stdout, stderr = _communicate_remote_process(
                process,
                operation_id,
                timeout,
                progress_callback=progress_callback,
                progress_phase="远端 docker load",
            )
        except subprocess.TimeoutExpired as exc:
            raise RemoteWorkerError("经 SSH 传输 worker 镜像超时") from exc
        writer.join(timeout=65)
        if writer.is_alive():
            image_response.close()
            docker_connection.close()
            writer.join(timeout=5)

        output = _redact_output(
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
            credentials,
            server,
        )
        if process.returncode != 0:
            detail = (output["stderr"] or output["stdout"] or "远端 docker load 失败").strip()
            raise RemoteWorkerError(detail[-2000:])
        if writer_errors:
            raise RemoteWorkerError("读取平台 worker 镜像归档失败")
        _emit_progress(
            progress_callback,
            "传输并加载 worker 镜像",
            f"镜像传输完成，共 {transferred_bytes / (1024 * 1024):.0f} MiB。",
        )
        return output
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        image_response.close()
        docker_connection.close()
        if writer is not None:
            writer.join(timeout=5)


def _wait_for_remote_worker_heartbeat(
    operation_id: str,
    machine_id: str,
    progress_callback: Callable[[str, str], None],
) -> None:
    """短暂等待远程 worker 注册，并记录有无心跳供安装诊断。"""
    _emit_progress(progress_callback, "等待资源心跳", "等待远程 worker 注册资源槽（最多 15 秒）。")
    deadline = time.monotonic() + _WORKER_STARTUP_HEARTBEAT_TIMEOUT
    while True:
        if _remote_operation_cancel_requested(operation_id):
            raise RemoteWorkerCancelledError("操作已中断；worker 容器可能已经启动")
        try:
            workers = manager.lease.list_workers()
        except Exception as exc:
            _emit_progress(
                progress_callback,
                "等待资源心跳",
                f"无法读取平台 worker 心跳：{exc}",
            )
            return
        matches = [
            item for item in workers
            if str(item.get("machine_id", "")) == machine_id and item.get("active")
        ]
        if matches:
            worker_ids = ", ".join(str(item.get("worker_id", "未知槽位")) for item in matches)
            _emit_progress(
                progress_callback,
                "等待资源心跳",
                f"已收到 {len(matches)} 个资源槽心跳：{worker_ids}。",
            )
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _emit_progress(
                progress_callback,
                "等待资源心跳",
                "15 秒内未收到资源心跳。请查看下方容器启动日志，并检查目标机到 Redis、S3 的连通性。",
            )
            return
        time.sleep(min(1.0, remaining))


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
    except RemoteWorkerError as exc:
        return {
            "source": "page" if "无法解密" in str(exc) else "none",
            "auth_method": None,
            "configured": False,
            "can_configure": _credentials_configurable(),
            "status_error": str(exc),
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


def remote_configuration_errors(
    credentials: Dict[str, str] | None = None,
    server: Dict[str, object] | None = None,
) -> List[str]:
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
    if not config.REDIS_URL:
        errors.append("平台必须配置共享 REDIS_URL，远程 worker 才能加入同一任务队列")
    if config.STORAGE_BACKEND != "s3":
        errors.append("平台必须使用共享 S3 存储，远程 worker 才能读取数据集和写入产物")
    if not shutil.which("ssh"):
        errors.append("平台后端容器未安装 OpenSSH 客户端")
    auth_method = credentials["auth_method"]
    if auth_method not in {"key", "password", "key_password"}:
        errors.append("请先在页面配置 SSH 凭证")
    if auth_method in {"key", "key_password"}:
        if not credentials.get("private_key"):
            errors.append("页面未配置 SSH 私钥")
    if auth_method in {"password", "key_password"} and not credentials.get("password"):
        errors.append("页面凭证缺少 SSH 登录密码")
    try:
        settings = remote_deployment_settings(server)
    except RemoteWorkerError as exc:
        errors.append(str(exc))
        settings = {"worker_image": "", "redis_url": "", "s3_endpoint": ""}
    image = settings["worker_image"]
    if not image or any(c.isspace() for c in image):
        errors.append("WORKER_IMAGE 或服务器镜像覆盖值必须是平台本地 Docker 镜像标签")
    if not settings["redis_url"].startswith(("redis://", "rediss://")):
        errors.append("平台或服务器必须配置目标服务器可访问的 Redis URL")
    if not settings["s3_endpoint"] or any(
        c.isspace() for c in settings["s3_endpoint"]
    ):
        errors.append("平台或服务器必须配置目标服务器可访问的 S3 地址")
    values = (
        settings["worker_image"],
        settings["redis_url"],
        settings["s3_endpoint"],
        config.S3_ACCESS_KEY,
        config.S3_SECRET_KEY,
        config.S3_BUCKET,
    )
    if any(any(ord(char) < 32 for char in value) for value in values):
        errors.append("远程配置不能包含控制字符")
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

    deployment = remote_deployment_settings(server)
    settings = {
        "MTP_ACTION": action,
        "MTP_MACHINE_ID": str(server["machine_id"]),
        "MTP_MODE": mode,
        "MTP_CPU_SLOTS": str(cpu_slots),
        "MTP_WORKER_IMAGE": deployment["worker_image"],
        "MTP_REDIS_URL": deployment["redis_url"],
        "MTP_S3_ENDPOINT": deployment["s3_endpoint"],
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


def run_remote_action(
    server: Dict[str, object], action: str, operation_id: str | None = None
) -> Dict[str, str]:
    """用 OpenSSH 执行远程脚本，记录可读阶段与脱敏命令输出。"""
    credentials = _ssh_credentials_for_server(server)
    resolved_server = dict(server)
    resolved_server["username"] = credentials["username"]

    def report(phase: str, message: str) -> None:
        """保存经过 SSH 凭证和部署密钥过滤的阶段进度。"""
        _append_remote_operation_log(
            operation_id,
            phase,
            message,
            credentials=credentials,
            server=resolved_server,
        )

    progress_callback = report if operation_id else None
    _emit_progress(progress_callback, "检查部署配置", "检查 SSH、Redis、S3 和本地 worker 镜像配置。")
    errors = remote_configuration_errors(credentials, server)
    if errors:
        raise RemoteWorkerError("；".join(errors))
    validate_server_record(resolved_server)
    ssh_path = shutil.which("ssh")
    if not ssh_path:
        raise RemoteWorkerError("平台后端容器未安装 OpenSSH 客户端")
    try:
        Path(config.WORKER_SSH_KNOWN_HOSTS_PATH).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RemoteWorkerError("无法创建持久化 SSH 主机密钥目录") from exc
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
            "StrictHostKeyChecking=accept-new",
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
        environment: Dict[str, str] | None = None
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
        if action == "install":
            image_command = command[:-1] + ["docker load"]
            image = remote_deployment_settings(server)["worker_image"]
            _emit_progress(progress_callback, "检查远端 Docker 平台", "读取目标机 Docker 操作系统和 CPU 架构。")
            local_platform = _local_docker_image_platform(image)
            progress_options = (
                {"progress_callback": progress_callback}
                if progress_callback is not None else {}
            )
            remote_platform = _remote_docker_image_platform(
                command, environment, credentials, server, timeout, operation_id,
                **progress_options,
            )
            target_platform = _canonical_docker_platform(remote_platform)
            _emit_progress(
                progress_callback,
                "检查镜像架构",
                f"平台镜像架构：{local_platform}；目标 Docker 架构：{target_platform}。",
            )
            if _canonical_docker_platform(local_platform) != target_platform:
                _emit_progress(
                    progress_callback,
                    "构建目标架构镜像",
                    f"架构不一致，开始构建 {target_platform} worker 镜像。",
                )
                _build_local_docker_image_for_platform(
                    image, target_platform, operation_id, **progress_options
                )
                _emit_progress(progress_callback, "构建目标架构镜像", "目标架构镜像构建完成。")
            else:
                _emit_progress(progress_callback, "构建目标架构镜像", "架构一致，跳过重新构建。")
            _emit_progress(progress_callback, "传输并加载 worker 镜像", "开始从平台 Docker 导出镜像并经 SSH 发送。")
            _stream_worker_image_to_remote(
                image, image_command, environment, credentials, server, timeout,
                operation_id, **progress_options,
            )
        _emit_progress(progress_callback, "执行远程部署脚本", f"开始执行远端 {action} 脚本。")
        progress_options = (
            {
                "progress_callback": progress_callback,
                "progress_phase": "执行远程部署脚本",
            }
            if progress_callback is not None else {}
        )
        result = _run_remote_subprocess(
            command,
            input_data=str(run_options["input"]),
            timeout=int(run_options["timeout"]),
            environment=run_options.get("env"),
            operation_id=operation_id,
            **progress_options,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteWorkerError("远程 SSH 操作超时") from exc
    except OSError as exc:
        raise RemoteWorkerError("无法启动 SSH 客户端") from exc
    finally:
        if temporary_key_path is not None:
            temporary_key_path.unlink(missing_ok=True)
    output = _redact_output(result.stdout, result.stderr, credentials, server)
    if result.returncode != 0:
        detail = (output["stderr"] or output["stdout"] or "远程脚本返回错误").strip()
        raise RemoteWorkerError(detail[-2000:])
    _emit_progress(progress_callback, "执行远程部署脚本", "远端脚本已成功返回。")
    if action == "install" and operation_id:
        _wait_for_remote_worker_heartbeat(
            operation_id,
            str(server.get("machine_id", "")),
            progress_callback or report,
        )
    return output


def execute_remote_operation(
    operation_id: str, server: Dict[str, object], action: str
) -> None:
    """在后台执行 SSH 操作并将可轮询状态写入协调存储。"""
    cancellation_event = _register_remote_operation(operation_id)
    try:
        operation = manager.lease.get_remote_operation(operation_id) or {
            "operation_id": operation_id,
            "machine_id": str(server.get("machine_id", "")),
            "action": action,
            "created_at": time.time(),
        }
        if operation.get("status") in {"completed", "failed", "cancelled"}:
            return
        try:
            if _remote_operation_cancel_requested(operation_id):
                raise RemoteWorkerCancelledError("操作已中断；远端可能已有部分变更")
            now = time.time()
            operation.update({
                "status": "running",
                "started_at": now,
                "updated_at": now,
                "error": None,
            })
            manager.lease.set_remote_operation(operation_id, operation)
            _append_remote_operation_log(
                operation_id,
                "准备远程操作",
                f"开始执行 {action} 操作。",
                server=server,
            )
            server.update({"status": "running", "operation_id": operation_id})
            remote_config_repository.save_remote_server(server)
            result = run_remote_action(server, action, operation_id)
            if _remote_operation_cancel_requested(operation_id):
                raise RemoteWorkerCancelledError("操作已中断；远端可能已有部分变更")
            terminal_update = {
                "status": "completed",
                "updated_at": time.time(),
                "output": result.get("stdout", "")[-2000:],
                "stderr": result.get("stderr", "")[-2000:],
                "phase": "操作完成",
            }
            server["status"] = "online" if action in {"install", "start", "restart"} else "stopped"
            terminal_log = ("操作完成", "远程操作已成功完成。")
        except RemoteWorkerCancelledError as exc:
            terminal_update = {
                "status": "cancelled",
                "updated_at": time.time(),
                "error": str(exc),
                "phase": "操作已中断",
            }
            terminal_log = ("操作已中断", str(exc))
            server["status"] = "error"
        except RemoteWorkerError as exc:
            terminal_update = {
                "status": "failed",
                "updated_at": time.time(),
                "error": str(exc),
                "phase": "操作失败",
            }
            terminal_log = ("操作失败", str(exc))
            server["status"] = "error"
        except Exception as exc:
            terminal_update = {
                "status": "failed",
                "updated_at": time.time(),
                "error": "远程操作发生未预期错误",
                "phase": "操作失败",
            }
            terminal_log = ("操作失败", f"远程操作发生未预期错误（{type(exc).__name__}）")
            server["status"] = "error"
        elapsed = max(0.0, time.time() - float(operation.get("started_at", time.time())))
        terminal_log = (terminal_log[0], f"{terminal_log[1]} 总耗时 {elapsed:.1f} 秒。")
        terminal_update["duration_sec"] = round(elapsed, 1)
        _append_remote_operation_log(
            operation_id,
            terminal_log[0],
            terminal_log[1],
            server=server,
        )
        operation = manager.lease.get_remote_operation(operation_id) or operation
        operation.update(terminal_update)
        manager.lease.set_remote_operation(operation_id, operation)
        remote_config_repository.save_remote_server(server)
    finally:
        _unregister_remote_operation(operation_id, cancellation_event)


def _redact_output(
    stdout: str,
    stderr: str,
    credentials: Dict[str, str] | None = None,
    server: Dict[str, object] | None = None,
) -> Dict[str, str]:
    """从 SSH 输出中删除远程配置和平台 SSH 凭证。"""
    credentials = credentials or {}
    try:
        redis_url = remote_deployment_settings(server).get("redis_url", "")
    except RemoteWorkerError:
        redis_url = ""
    secrets = (
        redis_url,
        config.WORKER_REMOTE_REDIS_URL,
        config.REDIS_URL,
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
            if "\n" in secret or "\r" in secret:
                for secret_line in secret.splitlines():
                    if len(secret_line) >= 8:
                        stdout = stdout.replace(secret_line, "[redacted]")
                        stderr = stderr.replace(secret_line, "[redacted]")
    return {"stdout": stdout[-4000:], "stderr": stderr[-4000:]}
