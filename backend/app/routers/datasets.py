"""数据集领域路由：上传/导入/列表/详情/统计/删除/标签/描述/画像/处理。"""
from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile

import app.config as config
from app.db_utils import get_dataset_from_db, save_dataset_to_db
from app.database import db_session
from app.state import data_processor, dataset_importer, dataset_manager
from app.utils import safe_filename
from app.routers.common import (
    _check_id,
    _get_dataset_or_404,
    _save_dataset_from_frame,
    _sync_dataset_csv_to_store,
    _write_metadata,
)
from app.routers.schemas import ProcessRequest

router = APIRouter(prefix="/api", tags=["datasets"])


@router.post("/datasets")
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
        try:
            _sync_dataset_csv_to_store(str(result["id"]))
        except Exception as exc:
            # 与示例导入路径保持相同的原子性：远端状态平面失败时回滚本地副本。
            dataset_manager.delete_dataset(str(result["id"]))
            raise HTTPException(503, "对象存储暂不可用，数据集未保存") from exc

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


@router.post("/datasets/sample/{filename}")
def import_sample(filename: str) -> dict:
    _check_id(filename, "示例数据")
    src = config.SAMPLE_DIR / filename
    if not src.exists():
        raise HTTPException(404, "示例数据不存在")
    df = pd.read_csv(src)
    return _save_dataset_from_frame(df, filename)


@router.get("/samples")
def samples() -> list[dict]:
    return [
        {"name": p.name}
        for p in sorted(config.SAMPLE_DIR.glob("*.csv"))
    ]


@router.get("/datasets")
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


@router.get("/datasets/{ds_id}")
def dataset_detail(ds_id: str, rows: int = 5) -> dict:
    """获取数据集详情"""
    try:
        return dataset_manager.get_dataset_preview(ds_id, rows)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取数据集详情失败: {e}")


@router.get("/datasets/{ds_id}/stats")
def dataset_stats(ds_id: str) -> dict:
    """获取数据集统计信息"""
    try:
        return dataset_manager.get_dataset_stats(ds_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取数据集统计信息失败: {e}")


@router.delete("/datasets/{ds_id}")
def delete_dataset(ds_id: str) -> dict:
    """删除数据集（本地文件 + S3 状态平面同步副本 + DB 镜像一并清理）"""
    try:
        success = dataset_manager.delete_dataset(ds_id)
        if success:
            # S3 模式下尽力清理状态平面的数据集副本（local 模式为 no-op）
            try:
                from app.storage import get_store

                store = get_store()
                key = f"datasets/{ds_id}.csv"
                if store.exists(key):
                    store.delete(key)
            except Exception:
                pass
            # 从数据库删除（如果可用；仍有任务引用该数据集时 FK 约束会失败，
            # 镜像行保留不影响主流程——任务删除时会同步清理自身镜像）
            if config.DB_AVAILABLE:
                try:
                    from app.database import SessionLocal

                    with SessionLocal() as db:
                        row = get_dataset_from_db(db, ds_id)
                        if row is not None:
                            db.delete(row)
                            db.commit()
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
# 数据集管理增强功能（标签 / 描述 / 画像 / 处理）
# ---------------------------------------------------------------------------

@router.get("/datasets/{ds_id}/tags")
def get_dataset_tags(ds_id: str) -> dict:
    """获取数据集标签"""
    try:
        dataset = _get_dataset_or_404(ds_id)
        return {"tags": dataset.get("tags", [])}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取数据集标签失败: {e}")


@router.post("/datasets/{ds_id}/tags")
def add_dataset_tags(ds_id: str, tags: list[str]) -> dict:
    """添加数据集标签"""
    try:
        dataset = _get_dataset_or_404(ds_id)
        existing_tags = set(dataset.get("tags", []))
        new_tags = list(existing_tags.union(set(tags)))
        dataset["tags"] = new_tags
        _write_metadata(ds_id, dataset)
        return {"tags": new_tags}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"添加数据集标签失败: {e}")


@router.delete("/datasets/{ds_id}/tags")
def remove_dataset_tags(ds_id: str, tags: list[str]) -> dict:
    """删除数据集标签"""
    try:
        dataset = _get_dataset_or_404(ds_id)
        existing_tags = set(dataset.get("tags", []))
        new_tags = list(existing_tags - set(tags))
        dataset["tags"] = new_tags
        _write_metadata(ds_id, dataset)
        return {"tags": new_tags}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"删除数据集标签失败: {e}")


@router.get("/datasets/tags")
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


@router.post("/datasets/{ds_id}/description")
def update_dataset_description(ds_id: str, description: str) -> dict:
    """更新数据集描述"""
    try:
        dataset = _get_dataset_or_404(ds_id)
        dataset["description"] = description
        _write_metadata(ds_id, dataset)
        return {"description": description}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"更新数据集描述失败: {e}")


# ---------------------------------------------------------------------------
# 数据预处理
# ---------------------------------------------------------------------------

@router.get("/datasets/{ds_id}/profile")
def get_data_profile(ds_id: str) -> dict:
    """获取数据概览"""
    try:
        _get_dataset_or_404(ds_id)
        df = pd.read_csv(config.DATASETS_DIR / "processed" / f"{ds_id}.csv")
        return data_processor.get_data_profile(df)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取数据概览失败: {e}")


@router.post("/datasets/{ds_id}/process")
def process_dataset(ds_id: str, request: ProcessRequest) -> dict:
    """处理数据集"""
    try:
        dataset = _get_dataset_or_404(ds_id)

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
        _write_metadata(ds_id, dataset)

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


@router.get("/processing/operations")
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
# 数据集导入（多格式/批量/校验/历史）
# ---------------------------------------------------------------------------

@router.post("/datasets/import")
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


@router.post("/datasets/batch-import")
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


@router.post("/datasets/validate")
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


@router.get("/datasets/import-history")
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
