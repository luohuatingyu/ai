"""训练任务领域路由：提交/列表/详情/日志/控制/workers 面板/模型下载/预测。"""
from __future__ import annotations

import re
import secrets
import time
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import FileResponse

import app.config as config
from app import jobstore
from app.database import db_session
from app.db_utils import get_job_logs
from app.jobs import api_view
from app.state import manager
from app.storage import job_key
from app.trainer import predict_row
from app.routers.common import _check_id, _load_meta_or_404
from app.routers.schemas import (
    GenerateRequest,
    PredictRequest,
    RemoteServerRequest,
    TrainRequest,
    WorkerSelectionRequest,
)
from app.remote_workers import (
    execute_remote_operation,
    remote_configuration_errors,
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


def _require_remote_admin_token(
    x_worker_admin_token: Optional[str] = Header(default=None, alias="X-Worker-Admin-Token"),
) -> None:
    """仅在启用远程管理且令牌配置有效时接受受保护的 SSH 管理请求。"""
    if not config.WORKER_REMOTE_ENABLED:
        raise HTTPException(503, "远程服务器管理尚未启用")
    if len(config.WORKER_REMOTE_TOKEN) < 32:
        raise HTTPException(503, "平台未配置有效的远程管理令牌")
    if not x_worker_admin_token or not secrets.compare_digest(
        x_worker_admin_token, config.WORKER_REMOTE_TOKEN
    ):
        raise HTTPException(401, "远程管理令牌无效")


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
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "output": "",
        "error": None,
    }
    server = dict(server)
    server["status"] = {
        "install": "provisioning",
        "start": "starting",
        "stop": "stopping",
        "restart": "restarting",
    }[action]
    server["operation_id"] = operation_id
    manager.lease.set_remote_server(str(server["machine_id"]), server)
    manager.lease.set_remote_operation(operation_id, operation)
    background_tasks.add_task(execute_remote_operation, operation_id, server, action)
    return {"ok": True, "operation_id": operation_id, "status": "queued"}


@router.get("/remote-servers")
def list_remote_servers(
    x_worker_admin_token: Optional[str] = Header(default=None, alias="X-Worker-Admin-Token"),
) -> dict:
    """列出经 SSH 接入的远程服务器；管理元数据仅对令牌持有者可见。"""
    _require_remote_admin_token(x_worker_admin_token)
    servers = manager.lease.list_remote_servers()
    return {"servers": servers}


@router.get("/remote-ssh-settings")
def get_remote_ssh_settings(
    x_worker_admin_token: Optional[str] = Header(default=None, alias="X-Worker-Admin-Token"),
) -> dict:
    """返回 SSH 认证方式与凭证状态，绝不向页面回传凭证明文。"""
    from app.remote_workers import RemoteWorkerError, remote_ssh_settings_status

    _require_remote_admin_token(x_worker_admin_token)
    try:
        return remote_ssh_settings_status()
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "平台 SSH 凭证状态读取失败；请检查共享 Redis 连接") from exc


@router.put("/remote-ssh-settings")
async def put_remote_ssh_settings(
    http_request: Request,
    x_worker_admin_token: Optional[str] = Header(default=None, alias="X-Worker-Admin-Token"),
) -> dict:
    """加密并保存平台统一 SSH 凭证；响应只包含认证方式和保存状态。"""
    from app.remote_workers import RemoteWorkerError, store_platform_ssh_credentials
    from app.routers.schemas import RemoteSSHCredentialsRequest

    _require_remote_admin_token(x_worker_admin_token)
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
        raise HTTPException(503, "平台 SSH 凭证保存失败；请检查共享 Redis 连接") from exc


@router.delete("/remote-ssh-settings")
def delete_remote_ssh_settings(
    x_worker_admin_token: Optional[str] = Header(default=None, alias="X-Worker-Admin-Token"),
) -> dict:
    """清除页面保存的 SSH 凭证，并回退到平台 `.env` 配置。"""
    from app.remote_workers import RemoteWorkerError, clear_platform_ssh_credentials

    _require_remote_admin_token(x_worker_admin_token)
    try:
        return clear_platform_ssh_credentials()
    except RemoteWorkerError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "平台 SSH 凭证清除失败；请检查共享 Redis 连接") from exc


@router.post("/remote-servers", status_code=202)
def add_remote_server(
    request: RemoteServerRequest,
    background_tasks: BackgroundTasks,
    x_worker_admin_token: Optional[str] = Header(default=None, alias="X-Worker-Admin-Token"),
) -> dict:
    """经平台 SSH 远程安装 worker；目标主机只接收单文件脚本和容器镜像。"""
    _require_remote_admin_token(x_worker_admin_token)
    errors = remote_configuration_errors()
    if errors:
        raise HTTPException(503, "远程管理配置不完整：" + "；".join(errors))
    if manager.lease.get_remote_server(request.machine_id):
        raise HTTPException(409, "服务器标识已登记")
    server = request.model_dump()
    server.update({"status": "provisioning", "created_at": time.time()})
    return _start_remote_operation(server, "install", background_tasks)


@router.post("/remote-servers/{machine_id}/{action}", status_code=202)
def manage_remote_server(
    machine_id: str,
    action: Literal["install", "start", "stop", "restart"],
    background_tasks: BackgroundTasks,
    x_worker_admin_token: Optional[str] = Header(default=None, alias="X-Worker-Admin-Token"),
) -> dict:
    """经 SSH 重装、启动、优雅停止或重启目标服务器上的 worker 容器。"""
    _require_remote_admin_token(x_worker_admin_token)
    errors = remote_configuration_errors()
    if errors:
        raise HTTPException(503, "远程管理配置不完整：" + "；".join(errors))
    _check_id(machine_id, "服务器")
    server = manager.lease.get_remote_server(machine_id)
    if server is None:
        raise HTTPException(404, "远程服务器不存在")
    previous = manager.lease.get_remote_operation(str(server.get("operation_id", "")))
    if previous and previous.get("status") in {"queued", "running"}:
        raise HTTPException(409, "该服务器已有远程操作正在执行")
    return _start_remote_operation(server, action, background_tasks)


@router.get("/remote-operations/{operation_id}")
def get_remote_operation(
    operation_id: str,
    x_worker_admin_token: Optional[str] = Header(default=None, alias="X-Worker-Admin-Token"),
) -> dict:
    """返回远程接入或生命周期操作的状态和经过凭证过滤的输出。"""
    _require_remote_admin_token(x_worker_admin_token)
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise HTTPException(404, "远程操作不存在")
    operation = manager.lease.get_remote_operation(operation_id)
    if operation is None:
        raise HTTPException(404, "远程操作不存在")
    return operation


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
