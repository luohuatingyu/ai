"""FastAPI 后端入口：数据集管理、训练任务管理、预测、静态页面。"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from typing import Any, Literal

import numpy as np
import pandas as pd
import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import app.config as config
from app.jobs import JobManager, read_metrics
from app.trainer import predict_row

app = FastAPI(title="可视化模型训练平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = JobManager()

_SAFE_ID = re.compile(r"^[\w.\-]+$")
_NAME_RE = re.compile(r"^[^/\\]+$")


def _check_id(value: str, what: str) -> str:
    if not _SAFE_ID.match(value):
        raise HTTPException(400, f"非法 {what} ID")
    return value


# ---------------------------------------------------------------------------
# 系统信息
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/system")
def system() -> dict:
    cuda = torch.cuda.is_available()
    return {
        "device": "cuda" if cuda else "cpu",
        "device_name": torch.cuda.get_device_name(0) if cuda else "未检测到 GPU",
        "cuda_available": cuda,
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "gpu_count": torch.cuda.device_count(),
    }


# ---------------------------------------------------------------------------
# 数据集
# ---------------------------------------------------------------------------

def _dataset_csv(ds_id: str) -> Any:
    return config.DATASETS_DIR / f"{ds_id}.csv"


def _dataset_meta_path(ds_id: str) -> Any:
    return config.DATASETS_DIR / f"{ds_id}.meta.json"


def _load_meta_or_404(ds_id: str) -> dict:
    _check_id(ds_id, "数据集")
    path = _dataset_meta_path(ds_id)
    if not path.exists():
        raise HTTPException(404, "数据集不存在")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"数据集元信息损坏: {exc}") from exc


def _save_dataset_from_frame(df: pd.DataFrame, name: str) -> dict:
    ds_id = f"ds-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    df.to_csv(_dataset_csv(ds_id), index=False)
    meta = {
        "id": ds_id,
        "name": name,
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "created_at": time.time(),
    }
    _dataset_meta_path(ds_id).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


@app.post("/api/datasets")
async def upload_dataset(file: UploadFile = File(...)) -> dict:
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        raise HTTPException(400, "请上传 .csv 文件")
    if not _NAME_RE.match(filename):
        raise HTTPException(400, "文件名非法")

    try:
        df = pd.read_csv(file.file)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"CSV 解析失败: {exc}") from exc
    if df.empty or len(df.columns) < 2:
        raise HTTPException(400, "数据集为空或列数不足（至少 2 列）")
    if len(df) > 2_000_000:
        raise HTTPException(400, "数据集过大（> 200 万行），请先采样")

    meta = _save_dataset_from_frame(df, filename)
    return meta


@app.post("/api/datasets/sample/{filename}")
def import_sample(filename: str) -> dict:
    _check_id(filename, "示例数据")
    src = config.SAMPLE_DIR / filename
    if not src.exists():
        raise HTTPException(404, "示例数据不存在")
    df = pd.read_csv(src)
    return _save_dataset_from_frame(df, filename)


@app.get("/api/samples")
def samples() -> list[dict]:
    return [
        {"name": p.name}
        for p in sorted(config.SAMPLE_DIR.glob("*.csv"))
    ]


@app.get("/api/datasets")
def list_datasets() -> list[dict]:
    out: list[dict] = []
    for path in sorted(config.DATASETS_DIR.glob("*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


@app.get("/api/datasets/{ds_id}")
def dataset_detail(ds_id: str, rows: int = 5) -> dict:
    meta = _load_meta_or_404(ds_id)
    df = pd.read_csv(_dataset_csv(ds_id), nrows=max(1, min(rows, 50)))
    preview = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
    return {
        **meta,
        "preview": preview,
        "column_types": {str(c): str(t) for c, t in df.dtypes.items()},
    }


@app.delete("/api/datasets/{ds_id}")
def delete_dataset(ds_id: str) -> dict:
    _load_meta_or_404(ds_id)
    _dataset_csv(ds_id).unlink(missing_ok=True)
    _dataset_meta_path(ds_id).unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 训练任务
# ---------------------------------------------------------------------------

class TrainRequest(BaseModel):
    dataset_id: str
    target: str
    task: Literal["classification", "regression"] = "classification"
    epochs: int = Field(20, ge=1, le=500)
    batch_size: int = Field(32, ge=1, le=4096)
    lr: float = Field(1e-3, gt=0, le=1.0)
    hidden_layers: int = Field(2, ge=0, le=8)
    hidden_size: int = Field(64, ge=1, le=2048)
    test_split: float = Field(0.2, ge=0.05, le=0.5)
    seed: int = 42


class PredictRequest(BaseModel):
    row: dict[str, Any]


@app.post("/api/train")
def start_training(req: TrainRequest) -> dict:
    meta = _load_meta_or_404(req.dataset_id)
    if req.target not in meta["columns"]:
        raise HTTPException(400, f"目标列 '{req.target}' 不在数据集列中")

    params = req.model_dump()
    dataset_id = params.pop("dataset_id")
    target = params.pop("target")
    task = params.pop("task")

    job = manager.submit(
        {
            "dataset_id": dataset_id,
            "dataset_name": meta.get("name", dataset_id),
            "target": target,
            "task": task,
            "params": params,
        }
    )
    return {"job_id": job.id}


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [j.api_view(include_metrics=True) for j in manager.list()]


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    _check_id(job_id, "任务")
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")

    data = job.api_view(include_metrics=True, include_summary=True)
    metrics = data.get("metrics") or []
    epochs_total = job.spec.get("params", {}).get("epochs", 0)
    current_epoch = metrics[-1]["epoch"] if metrics else 0
    data["progress"] = {
        "epoch": current_epoch,
        "epochs_total": epochs_total,
        "percent": 100.0 if job.status == "completed" else (100.0 * current_epoch / epochs_total if epochs_total else 0.0),
    }
    if job.status in ("queued", "running") and epochs_total and metrics:
        data["progress"]["percent"] = max(data["progress"]["percent"] - 0.5, 0.0)
    return data


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str, offset: int = 0) -> dict:
    _check_id(job_id, "任务")
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    lines: list[str] = []
    if job.log_path.exists():
        lines = job.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    offset = max(0, min(offset, len(lines)))
    batch = lines[offset : offset + 500]
    return {"lines": batch, "next_offset": offset + len(batch), "total": len(lines)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    _check_id(job_id, "任务")
    try:
        job = manager.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": job.status}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    _check_id(job_id, "任务")
    try:
        manager.delete(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/jobs/{job_id}/model")
def download_model(job_id: str) -> FileResponse:
    _check_id(job_id, "任务")
    path = config.JOBS_DIR / job_id / "best.pt"
    if not path.exists():
        raise HTTPException(404, "模型文件尚未生成（任务可能仍在训练）")
    return FileResponse(path, media_type="application/octet-stream", filename=f"{job_id}-best.pt")


@app.post("/api/jobs/{job_id}/predict")
def predict(job_id: str, body: PredictRequest) -> dict:
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")
    ckpt = config.JOBS_DIR / job_id / "best.pt"
    if not ckpt.exists():
        raise HTTPException(400, "模型文件尚未生成（任务可能仍在训练）")
    try:
        return predict_row(ckpt, body.row)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"预测失败: {exc}") from exc


# ---------------------------------------------------------------------------
# 静态页面（单文件前端）
# ---------------------------------------------------------------------------

STATIC_MOUNT = StaticFiles(directory=config.STATIC_DIR)
app.mount("/static", STATIC_MOUNT, name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")
