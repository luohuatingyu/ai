"""路由公共工具：ID 校验、数据集元信息加载、数据集保存与 S3 同步。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import pandas as pd
from fastapi import HTTPException

import app.config as config
from app.state import dataset_manager

_SAFE_ID = re.compile(r"^[\w.\-]+$")


def _check_id(value: str, what: str) -> str:
    """ID 安全校验：只允许字母数字/下划线/点/横线（防路径穿越与注入）。"""
    if not _SAFE_ID.match(value):
        raise HTTPException(400, f"非法 {what} ID")
    return value


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


def _sync_dataset_csv_to_store(ds_id: str) -> None:
    """S3 模式下把数据集 CSV 同步到状态平面（多机 worker 自行拉取）。

    尽力而为：local 模式无需同步（本地即权威）；S3 同步失败抛异常，
    由调用方决定是否回滚本地副本（上传路径需要原子性，示例导入不需要）。
    """
    from app.storage import resolve_dataset_csv_local, sync_dataset_to_store

    csv_path = resolve_dataset_csv_local(ds_id)
    if csv_path.exists():
        sync_dataset_to_store(ds_id, csv_path)


def _save_dataset_from_frame(df: pd.DataFrame, name: str) -> dict:
    """将 DataFrame 保存为新数据集（统一走 DatasetManager 新存储结构，保证列表/预览/训练全链路兼容）"""
    from app.database import db_session
    from app.db_utils import save_dataset_to_db

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
    _sync_dataset_csv_to_store(str(meta["id"]))

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


def _get_dataset_or_404(ds_id: str) -> dict:
    """读取数据集元信息（新结构），不存在返回 404。"""
    dataset = dataset_manager.get_dataset(ds_id)
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    return dataset


def _write_metadata(ds_id: str, dataset: dict) -> None:
    """把数据集元信息写回新结构 metadata/{ds_id}.json。"""
    metadata_path = config.DATASETS_DIR / "metadata" / f"{ds_id}.json"
    metadata_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2))
