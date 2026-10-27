"""训练任务领域路由：提交/列表/详情/日志/控制/workers 面板/模型下载/预测。"""
from __future__ import annotations

import re
import time
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import FileResponse

import app.config as config
from app import jobstore
from app import remote_config_repository
from app.database import db_session
from app.db_utils import delete_job_logs, get_job_logs
from app.jobs import api_view
from app.state import manager
from app.storage import job_key
from app.trainer import predict_row
from app.routers.common import _check_id, _load_meta_or_404
from app.routers.schemas import (
    GenerateRequest,
    PredictRequest,
    RemoteServerRequest,
    RemoteServerUpdateRequest,
    RetryJobRequest,
    TrainRequest,
    WorkerSelectionRequest,
)
from app.remote_workers import (
    RemoteWorkerError,
    apply_remote_server_overrides,
    clear_remote_operation_log,
    execute_remote_operation,
    request_remote_operation_cancel,
    remote_configuration_errors,
    remote_server_is_deployed,
    remote_server_public_view,
    resolve_remote_server_credentials,
    validate_server_record,
)

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/train")
def start_training(req: TrainRequest) -> dict:
    llm_task = req.task.startswith("llm_")
    meta = _load_meta_or_404(req.dataset_id) if req.dataset_source == "local" else None
    target = "" if req.task == "clustering" or llm_task else (req.target or "")
    columns = (meta or {}).get("columns", [])
    if req.task in {"classification", "regression", "time_series_forecasting"}:
        if target not in columns:
            raise HTTPException(400, f"目标列 '{target}' 不在数据集列中")
    if req.task == "time_series_forecasting" and req.time_column not in columns:
        raise HTTPException(400, f"时间列 '{req.time_column}' 不在数据集列中")

    params = req.model_dump()
    dataset_id = params.pop("dataset_id")
    params.pop("target")
    task = params.pop("task")
    time_column = params.pop("time_column")
    n_clusters = params.pop("n_clusters")
    lookback = params.pop("lookback")
    forecast_horizon = params.pop("forecast_horizon")
    if task == "clustering":
        params["n_clusters"] = n_clusters
    if task == "time_series_forecasting":
        params["time_column"] = time_column
        params["lookback"] = lookback
        params["forecast_horizon"] = forecast_horizon
    if llm_task:
        llm_lr = float(params.pop("llm_lr"))
        if task != "llm_pretraining":
            params["lr"] = llm_lr
    if not llm_task:
        for name in (
            "dataset_source", "dataset_config", "dataset_split", "base_model", "reward_model",
            "text_column", "prompt_column", "completion_column", "chosen_column", "rejected_column",
            "max_samples", "max_seq_len", "vocab_size", "model_hidden_size", "model_layers",
            "model_heads", "ppo_updates", "generation_tokens", "llm_lr",
        ):
            params.pop(name, None)
    requested_gpu = bool(params.pop("require_gpu"))
    resource_hint = {
        "require_gpu": llm_task or requested_gpu,
        "min_vram_mb": int(params.pop("min_vram_mb") or 0),
    }

    job = manager.submit(
        {
            "dataset_id": dataset_id,
            "dataset_name": (meta or {}).get("name", dataset_id),
            "target": target,
            "task": task,
            "params": params,
            "resource_hint": resource_hint,
        }
    )
    return {"job_id": job["id"]}


@router.get("/jobs")
def list_jobs() -> list[dict]:
    return [
        api_view(record, include_metrics=True, store=manager.store)
        for record in manager.list()
    ]


@router.get("/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    _check_id(job_id, "任务")
    record = manager.get(job_id)
    if not record:
        raise HTTPException(404, "任务不存在")

    data = api_view(record, include_metrics=True, include_summary=True, store=manager.store)
    metrics = data.get("metrics") or []
    epochs_total = (record.get("spec") or {}).get("params", {}).get("epochs", 0)
    current_epoch = metrics[-1]["epoch"] if metrics else 0
    data["progress"] = {
        "epoch": current_epoch,
        "epochs_total": epochs_total,
        "percent": 100.0 if record.get("status") == "completed" else (100.0 * current_epoch / epochs_total if epochs_total else 0.0),
    }
    if record.get("status") in ("queued", "running") and epochs_total and metrics:
        data["progress"]["percent"] = max(data["progress"]["percent"] - 0.5, 0.0)
    return data


@router.get("/jobs/{job_id}/logs")
def job_logs(job_id: str, offset: int = 0) -> dict:
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")

    total = 0
    lines: list[str] = []
    # 状态平面日志文件为权威源（续训时各登录点自然衔接）
    text = jobstore.read_job_log(manager.store, job_id)
    if text:
        lines = text.splitlines()
        total = len(lines)

    # 数据库可用且已有日志时，与文件日志合并优先取数据库（训练线程镜像）
    if config.DB_AVAILABLE:
        try:
            with db_session() as db:
                db_logs = get_job_logs(db, job_id, 0, 1000000)
                if db_logs:
                    db_lines = [f"[{log.timestamp.strftime('%H:%M:%S')}] {log.message}" for log in db_logs]
                    lines = db_lines
                    total = len(db_lines)
        except Exception:
            pass

    offset = max(0, min(offset, total))
    batch = lines[offset : offset + 500]
    return {"lines": batch, "next_offset": offset + len(batch), "total": total}


@router.delete("/jobs/{job_id}/logs")
def clear_job_logs(job_id: str) -> dict:
    """清空任务文件日志和数据库镜像日志。"""
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")

    if config.DB_AVAILABLE:
        try:
            with db_session() as db:
                delete_job_logs(db, job_id)
        except Exception:
            raise HTTPException(503, "数据库日志清除失败，请稍后重试")
    record = jobstore.read_job(manager.store, job_id) or {}
    jobstore.update_job(manager.store, job_id, log_revision=int(record.get("log_revision", 0)) + 1)
    jobstore.clear_job_log(manager.store, job_id)
    return {"ok": True}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    _check_id(job_id, "任务")
    try:
        record = manager.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": record["status"]}


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str) -> dict:
    """暂停任务：训练中的任务保存断点后进入 paused（可跨机/卡恢复）。"""
    _check_id(job_id, "任务")
    try:
        record = manager.pause(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": record["status"]}


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict:
    """恢复暂停的任务：重新排队，任意可用卡/机器从 checkpoint 接续训练。"""
    _check_id(job_id, "任务")
    try:
        record = manager.resume(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": record["status"]}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, request: RetryJobRequest) -> dict:
    """失败任务重新排队；指定槽位时先验证在线、启用及资源要求。"""
    _check_id(job_id, "任务")
    if request.worker_id:
        worker = next(
            (
                item for item in manager.lease.list_workers()
                if str(item.get("worker_id", "")) == request.worker_id
            ),
            None,
        )
        if worker is None or not worker.get("active"):
            raise HTTPException(409, "所选计算槽不在线或已失效，请刷新资源列表")
        if not manager.lease.is_worker_enabled(
            request.worker_id, default=not bool(worker.get("managed", False))
        ):
            raise HTTPException(409, "所选计算槽已停用，不能接收重试任务")
        record = manager.get(job_id)
        if record is None:
            raise HTTPException(404, "任务不存在")
        hint = (record.get("spec") or {}).get("resource_hint") or {}
        gpu = worker.get("gpu") or {}
        min_vram = int(hint.get("min_vram_mb") or 0)
        if (hint.get("require_gpu") or min_vram > 0) and not gpu.get("available"):
            raise HTTPException(409, "任务要求 GPU，所选计算槽不含可用 GPU")
        if min_vram > 0 and int(gpu.get("vram_mb") or 0) < min_vram:
            raise HTTPException(409, "所选 GPU 显存不足以满足任务要求")

    try:
        record = manager.retry(job_id, worker_id=request.worker_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "ok": True,
        "job_id": job_id,
        "status": record["status"],
        "assignment_mode": record.get("retry_assignment_mode", "auto"),
        "worker_id": record.get("retry_worker_id"),
    }


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    _check_id(job_id, "任务")
    try:
        manager.delete(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.get("/workers")
def list_workers() -> dict:
    """列出全部在线计算槽（多机多卡，卡片级）：型号/显存/状态/当前任务。"""
    import time as _time

    workers: list[dict] = []
    for item in manager.lease.list_workers():
        info = dict(item)
        info["machine_id"] = info.get("machine_id") or str(info.get("worker_id", "")).rsplit("-", 1)[0] or None
        gpu = info.get("gpu") or {}
        info["device_label"] = (
            f"GPU: {gpu.get('name')}" if gpu.get("available") else "CPU"
        )
        state = "idle"
        current_job = None
        epoch = None
        if not info.get("active"):
            state = "offline"
        else:
            state = str(info.get("state") or "idle")
            current_job = info.get("current_job") or None
            epoch = info.get("epoch") if info.get("epoch") is not None else None
        info["state"] = state
        info["current_job"] = current_job
        info["epoch"] = epoch
        info["managed"] = bool(info.get("managed", False))
        info["enabled"] = manager.lease.is_worker_enabled(
            str(info.get("worker_id", "")), default=not info["managed"]
        )
        info["heartbeat_age_sec"] = (
            round(_time.time() - float(info.get("last_seen", _time.time())), 1)
            if info.get("last_seen") else None
        )
        workers.append(info)
    workers.sort(key=lambda x: (str(x.get("machine_id")), str(x.get("slot")), str(x.get("worker_id"))))
    return {
        "workers": workers,
        "queue_depth": manager.queue.size(),
        "ts": time.time(),
    }


@router.patch("/workers/{worker_id}")
def update_worker_selection(
    worker_id: str, request: WorkerSelectionRequest
) -> dict:
    """更新已发现外部计算槽的接单状态；正在执行的任务会正常完成。"""
    _check_id(worker_id, "计算槽")
    worker = next(
        (item for item in manager.lease.list_workers()
         if str(item.get("worker_id", "")) == worker_id),
        None,
    )
    if worker is None:
        raise HTTPException(404, "计算槽不存在或已清理")
    if not bool(worker.get("managed", False)):
        raise HTTPException(409, "本机内置计算槽由平台自动管理")

    manager.lease.set_worker_enabled(worker_id, request.enabled)
    return {
        "ok": True,
        "worker_id": worker_id,
        "enabled": request.enabled,
        "message": (
            "计算槽已加入资源池"
            if request.enabled
            else "已停止接收新任务；正在运行的任务会正常完成"
        ),
    }


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
    previous = manager.lease.get_remote_operation(str(server.get("operation_id", "")))
    if previous and previous.get("status") in {"queued", "running"}:
        raise HTTPException(409, "该服务器已有远程操作正在执行，暂时不能编辑")

    updates = request.model_dump(exclude_unset=True)
    if any(updates.get(key) is None for key in ("host", "port", "mode", "cpu_slots") if key in updates):
        raise HTTPException(422, "SSH 主机、端口、计算类型和 CPU 槽不能设为空")
    candidate = dict(server)
    for key in ("host", "port", "mode", "cpu_slots"):
        if key in updates:
            candidate[key] = updates.pop(key)
    if "username" in updates:
        candidate["username_override"] = updates.pop("username")
    if "ssh_credential_id" in updates:
        if not updates["ssh_credential_id"]:
            raise HTTPException(422, "必须选择 SSH 凭证")
        candidate["ssh_credential_id"] = updates.pop("ssh_credential_id")

    try:
        candidate = apply_remote_server_overrides(candidate, updates, is_update=True)
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
    errors = remote_configuration_errors(credentials, server)
    if errors:
        raise HTTPException(503, "远程管理配置不完整：" + "；".join(errors))
    previous = manager.lease.get_remote_operation(str(server.get("operation_id", "")))
    if previous and previous.get("status") in {"queued", "running"}:
        raise HTTPException(409, "该服务器已有远程操作正在执行")
    if action == "install" and remote_server_is_deployed(server):
        operation_action = "deploy"
    elif action == "deploy" and not remote_server_is_deployed(server):
        operation_action = "install"
    else:
        operation_action = action
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


@router.get("/jobs/{job_id}/model")
def download_model(job_id: str) -> FileResponse:
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")
    record = manager.get(job_id)
    is_llm = str((record.get("spec") or {}).get("task", "")).startswith("llm_")
    artifact_name = "best_model.zip" if is_llm else "best.pt"
    key = job_key(job_id, artifact_name)
    if not manager.store.exists(key):
        raise HTTPException(404, "模型文件尚未生成（任务可能仍在训练）")
    path = manager.store.read_file(key, config.WORKER_SCRATCH_DIR / "serve" / job_id / artifact_name)
    filename = f"{job_id}-model.zip" if is_llm else f"{job_id}-best.pt"
    return FileResponse(path, media_type="application/octet-stream", filename=filename)


@router.post("/jobs/{job_id}/predict")
def predict(job_id: str, body: PredictRequest) -> dict:
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")
    task = str(((manager.get(job_id) or {}).get("spec") or {}).get("task", ""))
    if task.startswith("llm_"):
        raise HTTPException(400, "LLM 任务请使用文本生成接口")
    key = job_key(job_id, "best.pt")
    if not manager.store.exists(key):
        raise HTTPException(400, "模型文件尚未生成（任务可能仍在训练）")
    local_ckpt = manager.store.read_file(
        key, config.WORKER_SCRATCH_DIR / "serve" / job_id / "best.pt"
    )
    try:
        return predict_row(local_ckpt, body.row, history=body.history)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"预测失败: {exc}") from exc


@router.post("/jobs/{job_id}/generate")
def generate(job_id: str, body: GenerateRequest) -> dict:
    """从已训练的 LLM 模型包生成文本，不执行任何远程代码。"""
    _check_id(job_id, "任务")
    record = manager.get(job_id)
    if not record:
        raise HTTPException(404, "任务不存在")
    task = str((record.get("spec") or {}).get("task", ""))
    if not task.startswith("llm_"):
        raise HTTPException(400, "该任务不是 LLM 任务")
    key = job_key(job_id, "best_model.zip")
    if not manager.store.exists(key):
        raise HTTPException(400, "LLM 模型文件尚未生成（任务可能仍在训练）")
    local_bundle = manager.store.read_file(
        key, config.WORKER_SCRATCH_DIR / "serve" / job_id / "best_model.zip"
    )
    try:
        from app.llm_trainer import generate_text

        generated = generate_text(
            local_bundle,
            body.prompt,
            max_new_tokens=body.max_new_tokens,
            temperature=body.temperature,
            top_p=body.top_p,
        )
        return {"prompt": body.prompt, "generated_text": generated}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"文本生成失败: {exc}") from exc
