"""远程服务器管理路由：SSH 接入、凭证、部署与生命周期操作。

从旧 jobs.py 拆分而来的计算资源基础设施：远程服务器是「计算平面」的
接入通道（选择指定计算资源的来源之一），与训练任务本身解耦。
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.database import db_session
from app import remote_config_repository
from app.state import manager
from app.routers.common import _check_id
from app.routers.schemas import RemoteServerRequest, RemoteServerUpdateRequest
from app.remote_workers import (
    RemoteWorkerError,
    apply_remote_server_overrides,
    clear_remote_operation_log,
    execute_remote_operation,
    recover_stale_remote_operation,
    request_remote_operation_cancel,
    remote_configuration_errors,
    remote_effective_config,
    remote_server_is_deployed,
    remote_server_public_view,
    resolve_remote_server_credentials,
    validate_server_record,
)

router = APIRouter(prefix="/api", tags=["remote-servers"])


def _remote_config_db_call(function, *args):
    """将 MySQL 远程配置仓储错误转换为受控的 503 API 响应。"""
    try:
        return function(*args)
    except remote_config_repository.RemoteConfigPersistenceError as exc:
        raise HTTPException(503, str(exc)) from exc


def _start_remote_operation(
    server: dict, action: str, background_tasks: BackgroundTasks
) -> dict:
    """创建可轮询的远程操作记录，并交由 FastAPI 后台任务运行 SSH。"""
    operation_id = uuid.uuid4().hex
    now = time.time()
    operation = {
        "operation_id": operation_id,
        "machine_id": str(server["machine_id"]),
        "action": action,
        "previous_server_status": server.get("status"),
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "output": "",
        "error": None,
    }
    server = dict(server)
    server["deployed"] = remote_server_is_deployed(server)
    server["status"] = {
        "install": "provisioning",
        "config": "configuring",
        "deploy": "deploying",
        "update": "deploying",
        "start": "starting",
        "stop": "stopping",
        "restart": "restarting",
    }[action]
    server["operation_id"] = operation_id
    _remote_config_db_call(remote_config_repository.save_remote_server, server)
    manager.lease.set_remote_operation(operation_id, operation)
    background_tasks.add_task(execute_remote_operation, operation_id, server, action)
    return {"ok": True, "operation_id": operation_id, "status": "queued"}


@router.get("/remote-servers")
def list_remote_servers() -> dict:
    """列出经 SSH 接入的远程服务器管理元数据。"""
    servers = _remote_config_db_call(remote_config_repository.list_remote_servers)
    return {"servers": [remote_server_public_view(server) for server in servers]}


@router.get("/remote-servers/{machine_id}/effective-config")
def get_remote_server_effective_config(machine_id: str) -> dict:
    """返回平台解析出的远程配置摘要，不读取或返回任何配置密钥。"""
    _check_id(machine_id, "服务器")
    server = _remote_config_db_call(remote_config_repository.get_remote_server, machine_id)
    if server is None:
        raise HTTPException(404, "远程服务器不存在")
    try:
        return {"config": remote_effective_config(server)}
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/remote-ssh-settings")
def get_remote_ssh_settings() -> dict:
    """返回 SSH 认证方式与凭证状态，绝不向页面回传凭证明文。"""
    from app.remote_workers import RemoteWorkerError, remote_ssh_settings_status
    try:
        status = remote_ssh_settings_status()
        if status.get("status_error"):
            raise HTTPException(503, str(status["status_error"]))
        return status
    except HTTPException:
        raise
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "平台 SSH 凭证状态读取失败；请检查 MySQL 连接") from exc


@router.get("/remote-ssh-credentials")
def get_remote_ssh_credentials() -> dict:
    """列出平台 SSH 凭证目录元数据，不返回密码、私钥或私钥口令。"""
    from app.remote_workers import RemoteWorkerError, list_platform_ssh_credentials
    try:
        return list_platform_ssh_credentials()
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "SSH 凭证目录读取失败；请检查 MySQL 连接") from exc


@router.post("/remote-ssh-credentials")
async def post_remote_ssh_credential(
    http_request: Request,
) -> dict:
    """创建加密保存的 SSH 凭证；输入校验失败时不会回显用户提交的秘密。"""
    from app.remote_workers import (
        RemoteCredentialConflictError,
        RemoteWorkerError,
        create_platform_ssh_credential,
    )
    from app.routers.schemas import RemoteSSHCredentialRequest
    try:
        request = RemoteSSHCredentialRequest.model_validate(await http_request.json())
    except (TypeError, ValueError):
        raise HTTPException(422, "SSH 凭证输入无效；请检查名称、用户名和凭证内容") from None
    credentials = request.model_dump()
    credentials["private_key"] = (
        credentials.get("private_key") or ""
    ).replace("\r\n", "\n").replace("\r", "\n")
    if credentials["private_key"] and not credentials["private_key"].endswith("\n"):
        credentials["private_key"] += "\n"
    for key in ("password", "passphrase"):
        credentials[key] = credentials.get(key) or ""
    try:
        return create_platform_ssh_credential(credentials)
    except RemoteCredentialConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "SSH 凭证保存失败；请检查 MySQL 连接") from exc


@router.put("/remote-ssh-credentials/{credential_id}")
async def put_remote_ssh_credential(
    credential_id: str,
    http_request: Request,
) -> dict:
    """完整替换 SSH 凭证；出于安全要求，编辑时需要重新提交全部秘密字段。"""
    from app.remote_workers import (
        RemoteCredentialConflictError,
        RemoteCredentialNotFoundError,
        RemoteWorkerError,
        update_platform_ssh_credential,
    )
    from app.routers.schemas import RemoteSSHCredentialRequest
    try:
        request = RemoteSSHCredentialRequest.model_validate(await http_request.json())
    except (TypeError, ValueError):
        raise HTTPException(422, "SSH 凭证输入无效；请检查名称、用户名和凭证内容") from None
    credentials = request.model_dump()
    credentials["private_key"] = (
        credentials.get("private_key") or ""
    ).replace("\r\n", "\n").replace("\r", "\n")
    if credentials["private_key"] and not credentials["private_key"].endswith("\n"):
        credentials["private_key"] += "\n"
    for key in ("password", "passphrase"):
        credentials[key] = credentials.get(key) or ""
    try:
        return update_platform_ssh_credential(credential_id, credentials)
    except RemoteCredentialNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RemoteCredentialConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "SSH 凭证保存失败；请检查 MySQL 连接") from exc


@router.delete("/remote-ssh-credentials/{credential_id}")
def delete_remote_ssh_credential(credential_id: str) -> dict:
    """删除凭证；仍被服务器引用的凭证需先修改服务器关联后才能删除。"""
    from app.remote_workers import (
        RemoteCredentialConflictError,
        RemoteCredentialNotFoundError,
        RemoteWorkerError,
        delete_platform_ssh_credential,
    )
    try:
        return delete_platform_ssh_credential(credential_id)
    except RemoteCredentialNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RemoteCredentialConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "SSH 凭证删除失败；请检查 MySQL 连接") from exc


@router.put("/remote-ssh-settings")
async def put_remote_ssh_settings(http_request: Request) -> dict:
    """加密并保存平台统一 SSH 凭证；响应只包含认证方式和保存状态。"""
    from app.remote_workers import RemoteWorkerError, store_platform_ssh_credentials
    from app.routers.schemas import RemoteSSHCredentialsRequest
    try:
        request = RemoteSSHCredentialsRequest.model_validate(await http_request.json())
    except (TypeError, ValueError):
        # Pydantic 默认错误详情包含输入值；凭证接口统一返回脱敏错误。
        raise HTTPException(422, "SSH 凭证输入无效；请检查认证方式和凭证内容") from None
    credentials = {"auth_method": request.auth_method}
    if request.auth_method == "key":
        private_key = request.private_key.replace("\r\n", "\n").replace("\r", "\n").strip()
        credentials.update(
            {
                "private_key": private_key + ("\n" if not private_key.endswith("\n") else ""),
                "passphrase": request.passphrase or "",
            }
        )
    else:
        credentials["password"] = request.password
    try:
        return store_platform_ssh_credentials(credentials)
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "平台 SSH 凭证保存失败；请检查 MySQL 连接") from exc


@router.delete("/remote-ssh-settings")
def delete_remote_ssh_settings() -> dict:
    """清除通过旧版接口迁移的默认凭证；服务器登录凭证不再读取环境变量。"""
    from app.remote_workers import RemoteWorkerError, clear_platform_ssh_credentials
    try:
        return clear_platform_ssh_credentials()
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "平台 SSH 凭证清除失败；请检查 MySQL 连接") from exc


@router.post("/remote-servers", status_code=201)
def add_remote_server(
    request: RemoteServerRequest,
) -> dict:
    """登记远程服务器配置；部署由服务器卡片上的单独操作触发。"""
    from app.remote_workers import RemoteWorkerError

    server = request.model_dump()
    server["username_override"] = request.username
    try:
        server = apply_remote_server_overrides(server, server)
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        credentials = resolve_remote_server_credentials(server)
    except RemoteWorkerError as exc:
        raise HTTPException(422, str(exc)) from exc
    errors = remote_configuration_errors(credentials, server)
    if errors:
        raise HTTPException(503, "远程管理配置不完整：" + "；".join(errors))
    if _remote_config_db_call(remote_config_repository.get_remote_server, request.machine_id):
        raise HTTPException(409, "服务器标识已登记")
    server["username"] = credentials["username"]
    server["ssh_credential_id"] = credentials["id"]
    server.update({"status": "not_deployed", "deployed": False, "created_at": time.time()})
    _remote_config_db_call(remote_config_repository.save_remote_server, server)
    return {"server": remote_server_public_view(server)}


@router.patch("/remote-servers/{machine_id}")
def update_remote_server(
    machine_id: str,
    request: RemoteServerUpdateRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> dict:
    """保存服务器配置；已部署服务器异步覆盖目标机配置文件。"""
    _check_id(machine_id, "服务器")
    server = _remote_config_db_call(remote_config_repository.get_remote_server, machine_id)
    if server is None:
        raise HTTPException(404, "远程服务器不存在")
    previous_operation_id = str(server.get("operation_id", ""))
    previous = manager.lease.get_remote_operation(previous_operation_id)
    if previous:
        previous = recover_stale_remote_operation(previous_operation_id, previous)
    if previous and previous.get("status") in {"queued", "running"}:
        raise HTTPException(409, "该服务器已有远程操作正在执行，暂时不能编辑")

    updates = request.model_dump(exclude_unset=True)
    if any(updates.get(key) is None for key in ("host", "port", "mode", "cpu_slots", "device_backend") if key in updates):
        raise HTTPException(422, "SSH 主机、端口、计算类型和 CPU 槽不能设为空")
    candidate = dict(server)
    if "distributed_host" in updates:
        candidate["distributed_host"] = updates.pop("distributed_host") or ""
    for key in ("host", "port", "mode", "cpu_slots", "device_backend"):
        if key in updates:
            candidate[key] = updates.pop(key)
    if "username" in updates:
        candidate["username_override"] = updates.pop("username")
    if "ssh_credential_id" in updates:
        if not updates["ssh_credential_id"]:
            raise HTTPException(422, "必须选择 SSH 凭证")
        candidate["ssh_credential_id"] = updates.pop("ssh_credential_id")
    # 远程构建仓库源（镜像加速 / pip 源 / apt 源）：空值表示清除，回退平台默认。
    for key in ("docker_registry", "pip_index_url", "apt_mirror"):
        if key in updates:
            raw = updates.pop(key)
            candidate[key] = (str(raw).strip() or None) if raw is not None else None

    try:
        candidate = apply_remote_server_overrides(candidate, updates)
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        credentials = resolve_remote_server_credentials(candidate)
    except RemoteWorkerError as exc:
        raise HTTPException(422, str(exc)) from exc
    candidate["username"] = credentials["username"]
    candidate["deployed"] = remote_server_is_deployed(server)
    try:
        validate_server_record(candidate)
    except RemoteWorkerError as exc:
        raise HTTPException(422, str(exc)) from exc
    errors = remote_configuration_errors(credentials, candidate)
    if errors:
        raise HTTPException(503, "远程管理配置不完整：" + "；".join(errors))

    candidate["updated_at"] = time.time()
    _remote_config_db_call(remote_config_repository.save_remote_server, candidate)
    if candidate["deployed"]:
        response.status_code = 202
        return _start_remote_operation(candidate, "config", background_tasks)
    return {"server": remote_server_public_view(candidate)}


@router.post("/remote-servers/{machine_id}/{action}", status_code=202)
def manage_remote_server(
    machine_id: str,
    action: Literal["install", "deploy", "config", "start", "stop", "restart"],
    background_tasks: BackgroundTasks,
) -> dict:
    """经 SSH 首次部署、同步代码、覆盖配置或控制 worker 容器。"""
    _check_id(machine_id, "服务器")
    server = _remote_config_db_call(remote_config_repository.get_remote_server, machine_id)
    if server is None:
        raise HTTPException(404, "远程服务器不存在")
    from app.remote_workers import RemoteWorkerError

    try:
        credentials = resolve_remote_server_credentials(server)
    except RemoteWorkerError as exc:
        raise HTTPException(409, str(exc)) from exc
    if action == "install" and remote_server_is_deployed(server):
        operation_action = "deploy"
    elif action == "deploy" and not remote_server_is_deployed(server):
        operation_action = "install"
    else:
        operation_action = action
    candidate = dict(server)
    candidate["username"] = credentials["username"]
    try:
        validate_server_record(candidate)
    except RemoteWorkerError as exc:
        raise HTTPException(422, str(exc)) from exc
    # start/restart 会把最新配置下发并重建容器应用，需要 Redis/S3/镜像配置可用；
    # 只有 stop/status 是纯容器操作，不依赖部署配置。
    errors = remote_configuration_errors(
        credentials,
        server,
        require_platform_config=operation_action in {
            "install", "deploy", "config", "start", "restart"
        },
    )
    if errors:
        raise HTTPException(503, "远程管理配置不完整：" + "；".join(errors))
    previous_operation_id = str(server.get("operation_id", ""))
    previous = manager.lease.get_remote_operation(previous_operation_id)
    if previous:
        previous = recover_stale_remote_operation(previous_operation_id, previous)
    if previous and previous.get("status") in {"queued", "running"}:
        raise HTTPException(409, "该服务器已有远程操作正在执行")
    return _start_remote_operation(server, operation_action, background_tasks)


@router.get("/remote-operations/{operation_id}")
def get_remote_operation(
    operation_id: str,
) -> dict:
    """返回远程接入或生命周期操作的状态和经过凭证过滤的输出。"""
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise HTTPException(404, "远程操作不存在")
    operation = manager.lease.get_remote_operation(operation_id)
    if operation is None:
        raise HTTPException(404, "远程操作不存在")
    operation = recover_stale_remote_operation(operation_id, operation)
    operation["cancel_requested"] = manager.lease.is_remote_operation_cancel_requested(
        operation_id
    )
    return operation


@router.delete("/remote-operations/{operation_id}/logs")
def clear_remote_operation_logs(operation_id: str) -> dict:
    """清除远程操作日志输出，但保留操作状态和阶段信息。"""
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise HTTPException(404, "远程操作不存在")
    if not clear_remote_operation_log(operation_id):
        raise HTTPException(404, "远程操作不存在")
    return {"ok": True, "operation_id": operation_id}


@router.post("/remote-operations/{operation_id}/cancel", status_code=202)
def cancel_remote_operation(
    operation_id: str,
) -> dict:
    """向排队中或执行中的远程 SSH 操作发送中断请求。"""
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise HTTPException(404, "远程操作不存在")
    operation = manager.lease.get_remote_operation(operation_id)
    if operation is None:
        raise HTTPException(404, "远程操作不存在")
    operation = recover_stale_remote_operation(operation_id, operation)
    if operation.get("status") == "cancelled":
        return {"ok": True, "operation_id": operation_id, "status": "cancelled"}
    if operation.get("status") not in {"queued", "running"}:
        raise HTTPException(409, "远程操作已结束，无法中断")

    active = request_remote_operation_cancel(operation_id)
    if active:
        return {"ok": True, "operation_id": operation_id, "status": "cancelling"}

    latest = manager.lease.get_remote_operation(operation_id) or operation
    if latest.get("status") in {"completed", "failed", "cancelled"}:
        manager.lease.clear_remote_operation_cancel(operation_id)
        if latest.get("status") == "cancelled":
            return {"ok": True, "operation_id": operation_id, "status": "cancelled"}
        raise HTTPException(409, "远程操作已结束，无法中断")

    was_queued = latest.get("status") == "queued"
    latest.update(
        {
            "status": "cancelled",
            "updated_at": time.time(),
            "error": "操作已中断；远端可能已有部分变更",
        }
    )
    manager.lease.set_remote_operation(operation_id, latest)
    machine_id = str(latest.get("machine_id", ""))
    if machine_id:
        server = _remote_config_db_call(remote_config_repository.get_remote_server, machine_id)
        if server:
            server["status"] = "error"
            _remote_config_db_call(remote_config_repository.save_remote_server, server)
    if was_queued:
        manager.lease.clear_remote_operation_cancel(operation_id)
    return {"ok": True, "operation_id": operation_id, "status": "cancelled"}