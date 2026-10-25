"""FastAPI 后端入口：数据集管理、训练任务管理、预测、静态页面。"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Literal

import pandas as pd
import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import app.config as config
from app.utils import safe_filename
from app import jobstore
from app.jobs import JobManager, api_view
from app.storage import get_store, job_key
from app.trainer import predict_row
from app.database import db_session
from app.db_utils import (
    save_dataset_to_db,
    get_dataset_from_db,
    get_job_logs,
    add_job_log,
)
from app.dataset_manager import DatasetManager
from app.data_processor import DataProcessor
from app.dataset_importer import DatasetImporter

app = FastAPI(title="可视化模型训练平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = JobManager()
dataset_manager = DatasetManager(config.DATASETS_DIR)
data_processor = DataProcessor()
dataset_importer = DatasetImporter(config.DATASETS_DIR)

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

def _dataset_meta_path(ds_id: str) -> Any:
    """旧扁平格式的数据集元信息路径：{ds_id}.meta.json"""
    return config.DATASETS_DIR / f"{ds_id}.meta.json"


def _load_meta_or_404(ds_id: str) -> dict:
    """加载数据集元信息，兼容旧扁平格式与新结构；两种格式均不存在时返回 404"""
    _check_id(ds_id, "数据集")
    # 优先读取旧扁平格式：{ds_id}.meta.json
    path = _dataset_meta_path(ds_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(500, f"数据集元信息损坏: {exc}") from exc
    # 回退读取新结构：metadata/{ds_id}.json（由 DatasetManager 统一管理）
    meta = dataset_manager.get_dataset(ds_id)
    if meta is None:
        raise HTTPException(404, "数据集不存在")
    return meta


def _save_dataset_from_frame(df: pd.DataFrame, name: str) -> dict:
    """将 DataFrame 保存为新数据集（统一走 DatasetManager 新存储结构，保证列表/预览/训练全链路兼容）"""
    # 构造 DatasetManager 所需的元信息
    metadata = {
        "original_filename": name,
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "column_types": {str(c): str(t) for c, t in df.dtypes.items()},
        "created_at": time.time(),
    }
    
    # 通过 DatasetManager 保存（写入 raw/processed/metadata 及数据集目录）
    meta = dataset_manager.save_dataset(df, metadata, description="内置示例数据集")
    
    # 镜像保存到数据库（数据库不可用时仅保存文件，不影响主流程）
    if config.DB_AVAILABLE:
        try:
            with db_session() as db:
                save_dataset_to_db(
                    db=db,
                    ds_id=meta["id"],
                    name=meta["name"],
                    rows=int(len(df)),
                    columns=[str(c) for c in df.columns],
                )
        except Exception:
            pass
    
    return meta


@app.post("/api/datasets")
async def upload_dataset(
    file: UploadFile = File(...),
    description: str = "",
    tags: list[str] = []
) -> dict:
    """上传数据集"""
    try:
        # 验证数据集
        df, metadata = dataset_manager.validate_dataset(file)
        
        # 保存数据集
        result = dataset_manager.save_dataset(df, metadata, description, tags)
        
        # 保存到数据库（如果可用）
        if config.DB_AVAILABLE:
            try:
                with db_session() as db:
                    save_dataset_to_db(
                        db=db,
                        ds_id=result["id"],
                        name=result["name"],
                        rows=result["rows"],
                        columns=result["columns"],
                    )
            except Exception as e:
                # 如果数据库保存失败，只保存到文件
                pass
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"上传数据集失败: {e}")


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
def list_datasets(
    limit: int = 100,
    offset: int = 0,
    tags: list[str] = None,
    search: str = None
) -> dict:
    """获取数据集列表"""
    try:
        datasets, total = dataset_manager.list_datasets(limit, offset, tags, search)
        return {
            "datasets": datasets,
            "total": total,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(500, f"获取数据集列表失败: {e}")


@app.get("/api/datasets/{ds_id}")
def dataset_detail(ds_id: str, rows: int = 5) -> dict:
    """获取数据集详情"""
    try:
        return dataset_manager.get_dataset_preview(ds_id, rows)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取数据集详情失败: {e}")


@app.get("/api/datasets/{ds_id}/stats")
def dataset_stats(ds_id: str) -> dict:
    """获取数据集统计信息"""
    try:
        return dataset_manager.get_dataset_stats(ds_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取数据集统计信息失败: {e}")


@app.delete("/api/datasets/{ds_id}")
def delete_dataset(ds_id: str) -> dict:
    """删除数据集"""
    try:
        success = dataset_manager.delete_dataset(ds_id)
        if success:
            # 从数据库删除（如果可用）
            if config.DB_AVAILABLE:
                try:
                    with db_session() as db:
                        # 这里可以添加从数据库删除数据集的逻辑
                        pass
                except Exception:
                    pass
            return {"ok": True}
        else:
            raise HTTPException(404, "数据集不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"删除数据集失败: {e}")


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
    # 资源提示（任务分发匹配到卡级）：要求在 GPU 卡上训练 / 显存下限
    require_gpu: bool = False
    min_vram_mb: int = Field(0, ge=0, le=1024 * 1024)


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


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [
        api_view(record, include_metrics=True, store=manager.store)
        for record in manager.list()
    ]


@app.get("/api/jobs/{job_id}")
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


@app.get("/api/jobs/{job_id}/logs")
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


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    _check_id(job_id, "任务")
    try:
        record = manager.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": record["status"]}


@app.post("/api/jobs/{job_id}/pause")
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


@app.post("/api/jobs/{job_id}/resume")
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


@app.get("/api/workers")
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


@app.get("/api/jobs/{job_id}/model")
def download_model(job_id: str) -> FileResponse:
    _check_id(job_id, "任务")
    if not manager.get(job_id):
        raise HTTPException(404, "任务不存在")
    key = job_key(job_id, "best.pt")
    if not manager.store.exists(key):
        raise HTTPException(404, "模型文件尚未生成（任务可能仍在训练）")
    path = manager.store.read_file(key, config.WORKER_SCRATCH_DIR / "serve" / job_id / "best.pt")
    return FileResponse(path, media_type="application/octet-stream", filename=f"{job_id}-best.pt")


@app.post("/api/jobs/{job_id}/predict")
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


# ---------------------------------------------------------------------------
# 数据集管理增强功能
# ---------------------------------------------------------------------------

@app.get("/api/datasets/{ds_id}/tags")
def get_dataset_tags(ds_id: str) -> dict:
    """获取数据集标签"""
    try:
        dataset = dataset_manager.get_dataset(ds_id)
        if not dataset:
            raise HTTPException(404, "数据集不存在")
        return {"tags": dataset.get("tags", [])}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取数据集标签失败: {e}")


@app.post("/api/datasets/{ds_id}/tags")
def add_dataset_tags(ds_id: str, tags: list[str]) -> dict:
    """添加数据集标签"""
    try:
        dataset = dataset_manager.get_dataset(ds_id)
        if not dataset:
            raise HTTPException(404, "数据集不存在")
        
        # 更新标签
        existing_tags = set(dataset.get("tags", []))
        new_tags = list(existing_tags.union(set(tags)))
        
        # 更新元数据
        dataset["tags"] = new_tags
        metadata_path = config.DATASETS_DIR / "metadata" / f"{ds_id}.json"
        metadata_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2))
        
        return {"tags": new_tags}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"添加数据集标签失败: {e}")


@app.delete("/api/datasets/{ds_id}/tags")
def remove_dataset_tags(ds_id: str, tags: list[str]) -> dict:
    """删除数据集标签"""
    try:
        dataset = dataset_manager.get_dataset(ds_id)
        if not dataset:
            raise HTTPException(404, "数据集不存在")
        
        # 更新标签
        existing_tags = set(dataset.get("tags", []))
        new_tags = list(existing_tags - set(tags))
        
        # 更新元数据
        dataset["tags"] = new_tags
        metadata_path = config.DATASETS_DIR / "metadata" / f"{ds_id}.json"
        metadata_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2))
        
        return {"tags": new_tags}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"删除数据集标签失败: {e}")


@app.get("/api/datasets/tags")
def get_all_tags() -> dict:
    """获取所有标签"""
    try:
        all_tags = set()
        _, total = dataset_manager.list_datasets(limit=10000)
        
        # 获取所有数据集的标签
        for offset in range(0, total, 100):
            datasets, _ = dataset_manager.list_datasets(limit=100, offset=offset)
            for dataset in datasets:
                all_tags.update(dataset.get("tags", []))
        
        return {"tags": sorted(list(all_tags))}
    except Exception as e:
        raise HTTPException(500, f"获取所有标签失败: {e}")


@app.post("/api/datasets/{ds_id}/description")
def update_dataset_description(ds_id: str, description: str) -> dict:
    """更新数据集描述"""
    try:
        dataset = dataset_manager.get_dataset(ds_id)
        if not dataset:
            raise HTTPException(404, "数据集不存在")
        
        # 更新描述
        dataset["description"] = description
        metadata_path = config.DATASETS_DIR / "metadata" / f"{ds_id}.json"
        metadata_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2))
        
        return {"description": description}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"更新数据集描述失败: {e}")


# ---------------------------------------------------------------------------
# 数据预处理
# ---------------------------------------------------------------------------

class ProcessRequest(BaseModel):
    operations: list[dict[str, Any]]

@app.get("/api/datasets/{ds_id}/profile")
def get_data_profile(ds_id: str) -> dict:
    """获取数据概览"""
    try:
        dataset = dataset_manager.get_dataset(ds_id)
        if not dataset:
            raise HTTPException(404, "数据集不存在")
        
        df = pd.read_csv(config.DATASETS_DIR / "processed" / f"{ds_id}.csv")
        return data_processor.get_data_profile(df)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取数据概览失败: {e}")


@app.post("/api/datasets/{ds_id}/process")
def process_dataset(ds_id: str, request: ProcessRequest) -> dict:
    """处理数据集"""
    try:
        dataset = dataset_manager.get_dataset(ds_id)
        if not dataset:
            raise HTTPException(404, "数据集不存在")
        
        # 读取原始数据
        df = pd.read_csv(config.DATASETS_DIR / "raw" / f"{ds_id}.csv")
        
        # 处理数据
        processed_df = data_processor.process_dataset(df, request.operations)
        
        # 保存处理后的数据
        processed_path = config.DATASETS_DIR / "processed" / f"{ds_id}.csv"
        processed_df.to_csv(processed_path, index=False)
        
        # 更新元数据
        dataset["rows"] = len(processed_df)
        dataset["columns"] = list(processed_df.columns)
        dataset["column_types"] = {col: str(dtype) for col, dtype in processed_df.dtypes.items()}
        dataset["processed_at"] = time.time()
        
        metadata_path = config.DATASETS_DIR / "metadata" / f"{ds_id}.json"
        metadata_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2))
        
        return {
            "original_rows": len(df),
            "processed_rows": len(processed_df),
            "operations": request.operations,
            "dataset": dataset
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"处理数据集失败: {e}")


@app.get("/api/processing/operations")
def get_processing_operations() -> dict:
    """获取可用的数据处理操作"""
    return {
        "operations": [
            {
                "type": "remove_duplicates",
                "name": "去除重复行",
                "description": "删除数据中的重复行",
                "params": {
                    "subset": {"type": "list", "description": "指定列，默认所有列"},
                    "keep": {"type": "str", "description": "保留策略: 'first', 'last', False"}
                }
            },
            {
                "type": "fill_missing",
                "name": "填充缺失值",
                "description": "使用指定方法填充缺失值",
                "params": {
                    "strategy": {"type": "str", "description": "策略: 'mean', 'median', 'mode', 'forward', 'backward', 'constant', 'drop'"},
                    "columns": {"type": "list", "description": "指定列，默认所有列"},
                    "value": {"type": "any", "description": "常数值（当strategy为constant时使用）"}
                }
            },
            {
                "type": "normalize",
                "name": "数值归一化",
                "description": "对数值列进行归一化处理",
                "params": {
                    "method": {"type": "str", "description": "方法: 'minmax', 'zscore'"},
                    "columns": {"type": "list", "description": "指定列，默认所有数值列"}
                }
            },
            {
                "type": "encode_categorical",
                "name": "分类变量编码",
                "description": "对分类变量进行编码",
                "params": {
                    "method": {"type": "str", "description": "方法: 'onehot', 'label'"},
                    "columns": {"type": "list", "description": "指定列，默认所有分类列"}
                }
            },
            {
                "type": "remove_outliers",
                "name": "去除异常值",
                "description": "使用统计方法去除异常值",
                "params": {
                    "method": {"type": "str", "description": "方法: 'iqr', 'zscore'"},
                    "columns": {"type": "list", "description": "指定列，默认所有数值列"},
                    "factor": {"type": "float", "description": "异常值系数"}
                }
            },
            {
                "type": "select_columns",
                "name": "选择列",
                "description": "选择指定的列",
                "params": {
                    "columns": {"type": "list", "description": "要保留的列列表"}
                }
            },
            {
                "type": "filter_rows",
                "name": "过滤行",
                "description": "根据条件过滤行",
                "params": {
                    "conditions": {
                        "type": "list", 
                        "description": "过滤条件列表",
                        "items": {
                            "column": {"type": "str", "description": "列名"},
                            "operator": {"type": "str", "description": "操作符: ==, !=, >, >=, <, <=, contains, in"},
                            "value": {"type": "any", "description": "值"}
                        }
                    }
                }
            },
            {
                "type": "transform_column",
                "name": "列转换",
                "description": "对指定列进行转换",
                "params": {
                    "column": {"type": "str", "description": "列名"},
                    "operation": {"type": "str", "description": "操作: log, sqrt, square, abs, round, datetime, extract"},
                    "params": {"type": "dict", "description": "操作参数"}
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# 数据集导入
# ---------------------------------------------------------------------------

@app.post("/api/datasets/import")
async def import_dataset(
    file: UploadFile = File(...),
    description: str = "",
    tags: list[str] = None,
    auto_validate: bool = True
) -> dict:
    """导入数据集（支持多种格式）"""
    try:
        return dataset_importer.import_dataset(file, description, tags, auto_validate)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"导入数据集失败: {e}")


@app.post("/api/datasets/batch-import")
async def batch_import_datasets(
    files: list[UploadFile] = File(...),
    description: str = "",
    tags: list[str] = None
) -> dict:
    """批量导入数据集"""
    try:
        return dataset_importer.batch_import(files, description, tags)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"批量导入数据集失败: {e}")


@app.post("/api/datasets/validate")
async def validate_dataset_file(file: UploadFile = File(...)) -> dict:
    """验证数据集文件"""
    try:
        # 保存临时文件
        temp_file_path = dataset_importer.temp_dir / safe_filename(file.filename or "")
        with open(temp_file_path, "wb") as buffer:
            buffer.write(file.file.read())
        
        # 验证文件
        result = dataset_importer.validate_file_structure(temp_file_path)
        
        # 清理临时文件
        temp_file_path.unlink()
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"验证数据集文件失败: {e}")


@app.get("/api/datasets/import-history")
def get_import_history(limit: int = 100) -> dict:
    """获取导入历史"""
    try:
        history = dataset_importer.get_import_history(limit)
        return {
            "history": history,
            "total": len(history)
        }
    except Exception as e:
        raise HTTPException(500, f"获取导入历史失败: {e}")


# ---------------------------------------------------------------------------
# 静态页面（单文件前端）
# ---------------------------------------------------------------------------

STATIC_MOUNT = StaticFiles(directory=config.STATIC_DIR)
app.mount("/static", STATIC_MOUNT, name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")
