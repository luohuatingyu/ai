"""训练任务领域路由：提交/查询/控制/日志/预测/模型下载。

重构版统一训练 API：所有训练任务（单机/分布式/Notebook/模板/Pipeline）
的提交与控制入口。路径为 /api/training/jobs*。
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from fastapi.responses import FileResponse

import app.config as config
from app import jobstore
from app.database import db_session
from app.db_utils import count_job_logs, delete_job_logs, get_job_logs, get_job_logs_after
from app.jobs import api_view
from app.state import manager
from app.storage import ArtifactStoreUnavailableError, job_key
from app.trainer import predict_row
from app.routers.common import _check_id, _load_meta_or_404
from app.routers.schemas import (
    GenerateRequest,
    PredictRequest,
    RetryJobRequest,
    TrainRequest,
)
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.registry.model_registry import ModelRegistry

router = APIRouter(prefix="/api/training", tags=["training"])


# ---------------------------------------------------------------------------
# 任务提交与查询
# ---------------------------------------------------------------------------

@router.post("/jobs")
def create_training_job(req: TrainRequest) -> dict:
    """校验数据与计算资源后创建单节点或异构联合训练任务。"""
    llm_task = req.task.startswith("llm_")
    meta = _load_meta_or_404(req.dataset_id, materialize=True) if req.dataset_source == "local" else None
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
    params.pop("worker_id", None)
    requested_gpu = bool(params.pop("require_gpu"))
    requested_backend = str(params.pop("device_backend") or "auto")
    parallel_workers = params.pop("parallel_workers")
    parallel_timeout = params.pop("parallel_timeout")
    resource_hint = {
        "require_gpu": (llm_task and not parallel_workers) or requested_gpu,
        "min_vram_mb": int(params.pop("min_vram_mb") or 0),
    }
    if requested_backend != "auto":
        resource_hint["device_backend"] = requested_backend
    if parallel_workers:
        from app.parallel_worker import select_members
        try:
            members = select_members(manager.lease, parallel_workers, require_idle=False)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if resource_hint["require_gpu"] and any(member["device"] == "cpu" for member in members):
            raise HTTPException(400, "已选择 CPU 节点，请关闭仅 GPU 资源限制")
        if requested_backend != "auto" and any(
            str(member.get("device_backend", "cpu")) != requested_backend for member in members
        ):
            raise HTTPException(400, f"联合训练节点必须全部使用 {requested_backend} 后端")
        if resource_hint["min_vram_mb"] and any(member["device"] == "cpu" or member["vram_mb"] < resource_hint["min_vram_mb"] for member in members):
            raise HTTPException(400, "所选联合训练节点不满足最低显存限制")
        resource_hint.update({"parallel_workers": parallel_workers, "parallel_timeout": parallel_timeout})

    selected_worker = params.pop("worker_id", None) if False else None

    try:
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
    except ArtifactStoreUnavailableError as exc:
        raise HTTPException(503, "对象存储暂不可用，任务未创建，请稍后重试") from exc
    return {"job_id": job["id"]}


@router.get("/jobs")
def list_training_jobs() -> list[dict]:
    try:
        records = manager.list()
        return [
            api_view(record, include_metrics=True, store=manager.store)
            for record in records
        ]
    except ArtifactStoreUnavailableError as exc:
        raise HTTPException(503, "对象存储暂不可用，无法读取任务列表，请稍后重试") from exc


@router.get("/jobs/{job_id}")
def get_training_job(job_id: str) -> dict:
    _check_id(job_id, "任务")
    record = manager.get(job_id)
    if not record:
        raise HTTPException(404, "任务不存在")

    data = api_view(record, include_metrics=True, include_summary=True, store=manager.store)
    data["performance"] = jobstore.parse_metrics_text(
        manager.store.read_text(job_key(job_id, "performance.jsonl")) or ""
    )
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


# ---------------------------------------------------------------------------
# 任务控制
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/cancel")
def cancel_training_job(job_id: str) -> dict:
    _check_id(job_id, "任务")
    try:
        record = manager.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": record["status"]}


@router.post("/jobs/{job_id}/pause")
def pause_training_job(job_id: str) -> dict:
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
def resume_training_job(job_id: str) -> dict:
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
def retry_training_job(job_id: str, request: RetryJobRequest) -> dict:
    """失败任务重新排队；指定槽位时先验证在线、启用及资源要求。"""
    _check_id(job_id, "任务")
    existing = manager.get(job_id)
    if request.worker_id and existing and ((existing.get("spec") or {}).get("resource_hint") or {}).get("parallel_workers"):
        raise HTTPException(400, "联合训练按原成员整组重试，不能改为单个计算槽")
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
def delete_training_job(job_id: str) -> dict:
    _check_id(job_id, "任务")
    try:
        manager.delete(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# 同步运行（Notebook / Pipeline 直接调用入口）
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/run-sync")
def run_training_job_sync(job_id: str, background_tasks: BackgroundTasks) -> dict:
    """同步运行训练任务（用于 Notebook/Pipeline 直接调用）。"""
    import torch

    _check_id(job_id, "任务")
    record = manager.get(job_id)
    if not record:
        raise HTTPException(404, "任务不存在")
    if record.get("status") not in ("queued", "failed", "cancelled"):
        raise HTTPException(409, f"任务状态 {record.get('status')} 不可重新运行")

    spec_dict = record.get("spec", {})
    spec = TrainingSpec(**spec_dict)
    store = manager.store
    dataset_path = store.read_file(
        f"datasets/{spec.dataset_id}.csv",
        config.WORKER_SCRATCH_DIR / spec.dataset_id / f"{spec.dataset_id}.csv",
    )
    context = TrainingContext(
        job_id=job_id,
        job_dir=config.WORKER_SCRATCH_DIR / job_id,
        store=store,
        dataset_path=dataset_path,
        device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
    )

    def _run():
        try:
            run_training(spec, context)
        except Exception as exc:
            store.write_text(f"jobs/{job_id}/error.txt", str(exc))

    background_tasks.add_task(_run)
    return {"ok": True, "message": "训练已在后台启动"}


# ---------------------------------------------------------------------------
# 日志（从旧 jobs 路由移植）
# ---------------------------------------------------------------------------

def _decode_log_cursor(cursor: str, source: str, revision: int) -> tuple[int, bool]:
    """校验带来源和清空版本的日志游标，来源变化时从头读取。"""
    # 限定长度与整数位数，避免异常长游标消耗不必要的解析资源。
    if len(cursor) > 64:
        raise HTTPException(400, "日志游标格式无效")
    match = re.fullmatch(r"(file|database):(\d{1,10}):(\d{1,20})", cursor)
    if not match:
        if cursor == "":
            return 0, False
        raise HTTPException(400, "日志游标格式无效")
    cursor_source, cursor_revision, position = match.groups()
    if cursor_source != source or int(cursor_revision) != revision:
        return 0, True
    return int(position), False


def _read_log_file_batch(
    store, key: str, offset: int, file_size: int, allow_partial: bool = False
) -> tuple[list[str], int]:
    """从日志字节游标增量读取最多 500 行，并等待活动日志的尾行写完整。"""
    chunk_size = 64 * 1024
    chunks: list[bytes] = []
    read_position = offset
    newline_count = 0
    while read_position < file_size:
        chunk = store.read_bytes_range(key, read_position, min(chunk_size, file_size - read_position)) or b""
        if not chunk:
            break
        chunks.append(chunk)
        read_position += len(chunk)
        newline_count += chunk.count(b"\n")
        # 在达到 500 条后立即停止范围读取，避免将下一批内容一并下载。
        if newline_count >= 500:
            break

    data = b"".join(chunks)
    newline_positions = [match.start() for match in re.finditer(b"\n", data)]
    complete_lines = min(len(newline_positions), 500)
    if complete_lines:
        consumed = newline_positions[complete_lines - 1] + 1
    else:
        consumed = 0
    if complete_lines == 500:
        data = data[:consumed]
    elif allow_partial and consumed < len(data):
        consumed = len(data)
    else:
        data = data[:consumed]
    next_position = offset + consumed
    decoded = data.decode("utf-8", errors="replace")
    # 日志生产方以 LF 分隔；只移除 CRLF 的 CR，不把其他 Unicode 分隔符
    # 解释成额外日志行，保持字节游标与返回行数严格一致。
    lines = decoded.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    return lines[:500], next_position


@router.get("/jobs/{job_id}/logs")
def job_logs(
    job_id: str,
    offset: int = 0,
    cursor: Optional[str] = None,
    response: Response = None,
) -> dict:
    """增量读取权威文件日志，保留旧版行偏移分页和数据库日志回退。"""
    # 训练日志是持续变化的状态，禁止浏览器和反向代理复用旧响应。
    if response is not None:
        response.headers.update({
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")

    key = job_key(job_id, "log.txt")
    record = jobstore.read_job(manager.store, job_id) or {}
    revision = max(int(record.get("log_revision", 0)), 0)
    file_size = manager.store.get_size(key)
    if cursor is not None:
        source = "file" if file_size is not None else "database"
        position, reset = _decode_log_cursor(cursor, source, revision)
        if source == "file":
            if position > file_size:
                position, reset = 0, True
            lines, next_position = _read_log_file_batch(
                manager.store,
                key,
                position,
                file_size,
                allow_partial=record.get("status") in {"completed", "failed", "cancelled"},
            )
            return {
                "lines": lines,
                "next_cursor": f"file:{revision}:{next_position}",
                "has_more": next_position < file_size,
                "reset": reset,
            }

        if not config.DB_AVAILABLE:
            return {"lines": [], "next_cursor": f"database:{revision}:0", "has_more": False, "reset": reset}
        try:
            with db_session() as db:
                db_logs = get_job_logs_after(db, job_id, position, 501)
            has_more = len(db_logs) > 500
            batch = db_logs[:500]
            next_id = batch[-1].id if batch else position
            lines = [f"[{log.timestamp.strftime('%H:%M:%S')}] {log.message}" for log in batch]
            return {
                "lines": lines,
                "next_cursor": f"database:{revision}:{next_id}",
                "has_more": has_more,
                "reset": reset,
            }
        except Exception:
            return {
                "lines": [], "next_cursor": f"database:{revision}:{position}",
                "has_more": False, "reset": reset,
            }

    # 旧客户端仍按行偏移读取；文件读取保持原有 total/next_offset 响应形状。
    text = manager.store.read_text(key)
    if text is not None:
        lines = text.splitlines()
        total = len(lines)
    elif config.DB_AVAILABLE:
        try:
            with db_session() as db:
                total = count_job_logs(db, job_id)
                db_logs = get_job_logs(db, job_id, max(offset, 0), 500)
            lines = [f"[{log.timestamp.strftime('%H:%M:%S')}] {log.message}" for log in db_logs]
            safe_offset = max(0, min(offset, total))
            return {"lines": lines, "next_offset": safe_offset + len(lines), "total": total}
        except Exception:
            lines, total = [], 0
    else:
        lines, total = [], 0

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


# ---------------------------------------------------------------------------
# 模型下载 / 预测 / 文本生成（从旧 jobs 路由移植）
# ---------------------------------------------------------------------------

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
def predict(job_id: str, body: PredictRequest, response: Response = None) -> dict:
    """保留同步预测，指定 Worker 时提交独立异步预测任务。"""
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")
    task = str(((manager.get(job_id) or {}).get("spec") or {}).get("task", ""))
    if task.startswith("llm_"):
        raise HTTPException(400, "LLM 任务请使用文本生成接口")
    key = job_key(job_id, "best.pt")
    if not manager.store.exists(key):
        raise HTTPException(400, "模型文件尚未生成（任务可能仍在训练）")
    if body.worker_id:
        return _submit_inference(job_id, task, body, response)
    local_ckpt = manager.store.read_file(
        key, config.WORKER_SCRATCH_DIR / "serve" / job_id / "best.pt"
    )
    try:
        return predict_row(local_ckpt, body.row, history=body.history)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"预测失败: {exc}") from exc


@router.post("/jobs/{job_id}/generate")
def generate(job_id: str, body: GenerateRequest, response: Response = None) -> dict:
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
    if body.worker_id:
        return _submit_inference(job_id, task, body, response)
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


def _submit_inference(job_id: str, task: str, body, response: Response) -> dict:
    """拒绝不可用资源；请求体只保存预测参数，状态接口隐藏输入。"""
    try:
        result = manager.inference.submit(job_id, task, body.worker_id,
                                          body.model_dump(exclude={"worker_id"}, exclude_none=True))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if response is not None:
        response.status_code = 202
    return result


@router.get("/jobs/{job_id}/inferences/{inference_id}")
def inference_status(job_id: str, inference_id: str) -> dict:
    """按所属模型查询预测，拒绝跨任务读取及非法路径编号。"""
    _check_id(job_id, "任务")
    try:
        result = manager.inference.get(inference_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result or result["job_id"] != job_id or not manager.get(job_id):
        raise HTTPException(404, "预测任务不存在")
    return result