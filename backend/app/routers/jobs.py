"""训练任务领域路由：提交/列表/详情/日志/控制/workers 面板/模型下载/预测。"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
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
from app.routers.schemas import PredictRequest, TrainRequest

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/train")
def start_training(req: TrainRequest) -> dict:
    meta = _load_meta_or_404(req.dataset_id)
    if req.target not in meta["columns"]:
        raise HTTPException(400, f"目标列 '{req.target}' 不在数据集列中")

    params = req.model_dump()
    dataset_id = params.pop("dataset_id")
    target = params.pop("target")
    task = params.pop("task")
    resource_hint = {
        "require_gpu": bool(params.pop("require_gpu")),
        "min_vram_mb": int(params.pop("min_vram_mb") or 0),
    }

    job = manager.submit(
        {
            "dataset_id": dataset_id,
            "dataset_name": meta.get("name", dataset_id),
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
        info["heartbeat_age_sec"] = (
            round(_time.time() - float(info.get("last_seen", _time.time())), 1)
            if info.get("last_seen") else None
        )
        workers.append(info)
    workers.sort(key=lambda x: (str(x.get("machine_id")), str(x.get("worker_id"))))
    return {
        "workers": workers,
        "queue_depth": manager.queue.size(),
        "ts": time.time(),
    }


@router.get("/jobs/{job_id}/model")
def download_model(job_id: str) -> FileResponse:
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")
    key = job_key(job_id, "best.pt")
    if not manager.store.exists(key):
        raise HTTPException(404, "模型文件尚未生成（任务可能仍在训练）")
    path = manager.store.read_file(key, config.WORKER_SCRATCH_DIR / "serve" / job_id / "best.pt")
    return FileResponse(path, media_type="application/octet-stream", filename=f"{job_id}-best.pt")


@router.post("/jobs/{job_id}/predict")
def predict(job_id: str, body: PredictRequest) -> dict:
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")
    key = job_key(job_id, "best.pt")
    if not manager.store.exists(key):
        raise HTTPException(400, "模型文件尚未生成（任务可能仍在训练）")
    local_ckpt = manager.store.read_file(
        key, config.WORKER_SCRATCH_DIR / "serve" / job_id / "best.pt"
    )
    try:
        return predict_row(local_ckpt, body.row)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"预测失败: {exc}") from exc
