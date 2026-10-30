"""数据集管理模块，提供数据集上传、验证、预处理等功能"""
import os
import json
import time
import uuid
import shutil
import csv
import io
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
from fastapi import HTTPException, UploadFile
import logging

logger = logging.getLogger(__name__)

class DatasetManager:
    """数据集管理器，负责数据集的存储、验证和管理"""
    
    def __init__(self, datasets_dir: Path, max_file_size: int = 500 * 1024 * 1024, 
                 max_rows: int = 2_000_000, max_columns: int = 100):
        """
        初始化数据集管理器
        
        Args:
            datasets_dir: 数据集存储目录
            max_file_size: 最大文件大小（字节）
            max_rows: 最大行数
            max_columns: 最大列数
        """
        self.datasets_dir = datasets_dir
        self.max_file_size = max_file_size
        self.max_rows = max_rows
        self.max_columns = max_columns
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建子目录
        self.raw_dir = datasets_dir / "raw"  # 原始上传文件
        self.processed_dir = datasets_dir / "processed"  # 处理后的数据集
        self.temp_dir = datasets_dir / "temp"  # 临时文件
        self.metadata_dir = datasets_dir / "metadata"  # 元数据存储
        
        for dir_path in [self.raw_dir, self.processed_dir, self.temp_dir, self.metadata_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _dataset_lock(self, ds_id: str):
        """用文件锁串行化同一数据集的下载和删除，兼容多个 API 进程。"""
        import fcntl

        if not re.fullmatch(r"[A-Za-z0-9_-]+", ds_id):
            raise HTTPException(400, "非法数据集 ID")
        lock_dir = self.temp_dir / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / f"{ds_id}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _write_hub_metadata(self, metadata: dict) -> None:
        """通过临时文件原子发布 Hub 状态，列表始终读到完整元数据。"""
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        path = self.metadata_dir / f"{metadata['id']}.json"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.temp_dir,
                                         delete=False) as handle:
            temp_path = Path(handle.name)
            try:
                json.dump(metadata, handle, ensure_ascii=False, indent=2)
                handle.close()
                os.replace(temp_path, path)
            finally:
                temp_path.unlink(missing_ok=True)

    def register_hub_dataset(self, repo_id: str, revision: str, subset: Optional[str],
                             split: str, storage_mode: str, description: str = "",
                             tags: Optional[List[str]] = None) -> dict:
        """仅查询公开仓库元信息并固定提交版本，不在保存引用时下载数据。"""
        from huggingface_hub import HfApi
        from huggingface_hub.utils import validate_repo_id

        try:
            validate_repo_id(repo_id)
        except ValueError as exc:
            raise HTTPException(400, "请填写有效的 Hugging Face 仓库 ID") from exc
        try:
            info = HfApi().dataset_info(repo_id, revision=revision, token=False, timeout=20)
        except Exception as exc:
            raise self._hub_exception(exc) from exc
        if info.private or info.gated:
            raise HTTPException(403, "当前仅支持无需授权的公开 Hub 数据集")
        if not re.fullmatch(r"[0-9a-f]{40,64}", info.sha or ""):
            raise HTTPException(502, "Hub 未返回有效的固定版本，请稍后重试")
        metadata = {
            "id": f"ds-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "name": repo_id, "source": "huggingface", "storage_mode": storage_mode,
            "hub": {"repo_id": repo_id, "revision": info.sha, "requested_revision": revision,
                    "subset": subset, "split": split},
            "rows": 0, "columns": [], "column_types": {}, "file_size": 0,
            "description": description, "tags": tags or [], "created_at": time.time(),
            "status": "active", "cache_status": "remote", "last_error": None,
        }
        self._write_hub_metadata(metadata)
        return metadata

    @staticmethod
    def _hub_exception(exc: Exception) -> HTTPException:
        """对外返回稳定且不包含远端响应、令牌或本地路径的失败信息。"""
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {401, 403}:
            return HTTPException(403, "Hub 数据集需要授权；当前仅支持公开数据集")
        if status == 404:
            return HTTPException(404, "Hub 仓库或版本不存在")
        if isinstance(exc, ValueError):
            return HTTPException(400, "Hub 子集或 split 无效，或数据格式不受支持")
        return HTTPException(503, "Hub 拉取失败，请检查网络连接后重试")

    def _stream_hub_csv(self, metadata: dict, output: Path) -> pd.DataFrame:
        """逐行读取固定版本的公开表格/文本数据，超限失败而不静默截断。"""
        from datasets import load_dataset

        source = metadata["hub"]
        data = load_dataset(source["repo_id"], name=source["subset"], split=source["split"],
                            revision=source["revision"], streaming=True, token=False,
                            trust_remote_code=False)
        features = getattr(data, "features", None)
        schema = features.to_dict() if features is not None else {}
        if re.search(r'"_type":\s*"(?:Image|Audio|Video|Pdf)"', json.dumps(schema)):
            raise HTTPException(400, "当前仅支持表格和文本数据集，暂不支持图像、音频等媒体字段")
        count, byte_count, columns = 0, 0, []
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        with output.open("w", encoding="utf-8", newline="") as handle:
            for row in data:
                if count >= self.max_rows:
                    raise HTTPException(400, f"数据集行数过多（最大 {self.max_rows} 行）")
                if not columns:
                    columns = list(row)
                    if not columns or len(columns) > self.max_columns:
                        raise HTTPException(400, f"数据集列数须为 1～{self.max_columns} 列")
                    if any(not isinstance(col, str) or not col.strip() for col in columns):
                        raise HTTPException(400, "列名必须为非空字符串")
                    writer.writerow(columns)
                if set(row) != set(columns):
                    raise HTTPException(400, "Hub 数据字段不一致，无法转换为表格")
                values = []
                for col in columns:
                    value = row[col]
                    if isinstance(value, (list, dict)):
                        try:
                            value = json.dumps(value, ensure_ascii=False, allow_nan=False)
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(400, "数据包含不支持的媒体或嵌套字段") from exc
                    elif value is not None and not isinstance(value, (str, int, float, bool)):
                        raise HTTPException(400, "数据包含不支持的媒体字段")
                    values.append(value)
                writer.writerow(values)
                line = buffer.getvalue()
                byte_count += len(line.encode("utf-8"))
                if byte_count > self.max_file_size:
                    raise HTTPException(400, f"数据过大（最大 {self.max_file_size // 1024 // 1024}MB）")
                handle.write(line)
                buffer.seek(0)
                buffer.truncate(0)
                count += 1
        if not count:
            raise HTTPException(400, "Hub 数据集为空")
        return pd.read_csv(output)

    def ensure_local(self, ds_id: str) -> dict:
        """按需下载并同步共享存储，成功后才发布就绪状态，失败保留引用供重试。"""
        metadata = self.get_dataset(ds_id)
        if not metadata:
            raise HTTPException(404, "数据集不存在")
        if metadata.get("source") != "huggingface":
            return metadata
        with self._dataset_lock(ds_id):
            metadata = self.get_dataset(ds_id)
            if not metadata:
                raise HTTPException(404, "数据集不存在")
            if metadata.get("cache_status") == "ready" and self._resolve_csv(ds_id):
                return metadata
            metadata.update(cache_status="downloading", last_error=None)
            self._write_hub_metadata(metadata)
            published = []
            synced = False
            try:
                with tempfile.TemporaryDirectory(prefix=f"hub-{ds_id}-", dir=self.temp_dir) as work:
                    staged = Path(work) / "dataset.csv"
                    frame = self._stream_hub_csv(metadata, staged)
                    from app.storage import sync_dataset_to_store

                    try:
                        synced = sync_dataset_to_store(ds_id, staged)
                    except Exception as exc:
                        raise HTTPException(503, "对象存储暂不可用，引用已保留，请重试") from exc
                    for directory in (self.raw_dir, self.processed_dir):
                        directory.mkdir(parents=True, exist_ok=True)
                        target = directory / f"{ds_id}.csv"
                        pending = Path(work) / f"{directory.name}.csv"
                        shutil.copyfile(staged, pending)
                        os.replace(pending, target)
                        published.append(target)
                    metadata.update(rows=len(frame), columns=list(frame.columns),
                                    column_types={str(col): str(dtype) for col, dtype in frame.dtypes.items()},
                                    file_size=staged.stat().st_size, cache_status="ready",
                                    last_error=None, updated_at=time.time())
                    self._write_hub_metadata(metadata)
                return metadata
            except Exception as exc:
                for path in published:
                    path.unlink(missing_ok=True)
                if synced:
                    try:
                        from app.storage import get_store

                        get_store().delete(f"datasets/{ds_id}.csv")
                    except Exception:
                        pass
                error = exc if isinstance(exc, HTTPException) else self._hub_exception(exc)
                metadata.update(cache_status="error", last_error=error.detail)
                self._write_hub_metadata(metadata)
                raise error from exc
    
    def validate_dataset(self, file: UploadFile) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        验证上传的数据集文件
        
        Args:
            file: 上传的文件
            
        Returns:
            Tuple[DataFrame, metadata]: 验证后的数据框和元数据
            
        Raises:
            HTTPException: 验证失败时抛出
        """
        # 检查文件类型
        if not file.filename or not file.filename.lower().endswith('.csv'):
            raise HTTPException(400, "请上传 .csv 文件")
        
        # 检查文件名安全性
        if not self._is_safe_filename(file.filename):
            raise HTTPException(400, "文件名包含非法字符")
        
        # 检查文件大小
        file.file.seek(0, 2)  # 移动到文件末尾
        file_size = file.file.tell()
        file.file.seek(0)  # 重置指针
        
        if file_size > self.max_file_size:
            raise HTTPException(400, f"文件过大（最大 {self.max_file_size // 1024 // 1024}MB）")
        
        # 读取并验证数据
        try:
            df = pd.read_csv(file.file)
        except Exception as e:
            raise HTTPException(400, f"CSV 解析失败: {str(e)}")
        
        # 检查数据集是否为空
        if df.empty:
            raise HTTPException(400, "数据集为空")
        
        # 检查行数
        if len(df) > self.max_rows:
            raise HTTPException(400, f"数据集行数过多（最大 {self.max_rows} 行）")
        
        # 检查列数
        if len(df.columns) > self.max_columns:
            raise HTTPException(400, f"数据集列数过多（最大 {self.max_columns} 列）")
        
        # 检查列名
        for col in df.columns:
            if not isinstance(col, str) or col.strip() == "":
                raise HTTPException(400, "列名必须为非空字符串")
        
        # 检查数据类型
        for col in df.columns:
            if df[col].dtype == 'object':
                # 检查是否为字符串类型
                try:
                    df[col] = df[col].astype(str)
                except Exception:
                    raise HTTPException(400, f"列 '{col}' 包含无法转换为字符串的数据")
        
        # 生成元数据
        metadata = {
            "original_filename": file.filename,
            "rows": len(df),
            "columns": list(df.columns),
            "column_types": {col: str(dtype) for col, dtype in df.dtypes.items()},
            # Pandas 返回 numpy 整数，保存 JSON 前转换为原生 int。
            "memory_usage": int(df.memory_usage(deep=True).sum()),
            "created_at": time.time(),
            "file_size": file_size,
            "status": "validated"
        }
        
        return df, metadata
    
    def save_dataset(self, df: pd.DataFrame, metadata: Dict[str, Any], 
                    description: str = "", tags: List[str] = None) -> Dict[str, Any]:
        """
        保存数据集到存储系统
        
        Args:
            df: 数据框
            metadata: 元数据
            description: 数据集描述
            tags: 标签列表
            
        Returns:
            Dict: 数据集信息
        """
        # 生成数据集ID
        ds_id = f"ds-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        
        # 统一补齐数据规模、字段和类型，保证不同导入方式返回相同的数据集摘要。
        metadata.update({
            "id": ds_id,
            "name": metadata.get("original_filename", f"dataset_{ds_id}"),
            "rows": int(len(df)),
            "columns": [str(column) for column in df.columns],
            "column_types": {
                str(column): str(dtype) for column, dtype in df.dtypes.items()
            },
            "description": description,
            "tags": tags or [],
            "status": "active",
            "processed_at": time.time()
        })
        
        # 保存原始文件
        raw_path = self.raw_dir / f"{ds_id}.csv"
        df.to_csv(raw_path, index=False)
        
        # 保存处理后的文件（可以添加预处理步骤）
        processed_path = self.processed_dir / f"{ds_id}.csv"
        df.to_csv(processed_path, index=False)
        
        # 保存元数据
        metadata_path = self.metadata_dir / f"{ds_id}.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        
        # 创建数据集目录
        dataset_dir = self.datasets_dir / ds_id
        dataset_dir.mkdir(exist_ok=True)
        
        # 复制文件到数据集目录
        shutil.copy2(raw_path, dataset_dir / f"{ds_id}.csv")
        shutil.copy2(metadata_path, dataset_dir / f"{ds_id}.meta.json")
        
        logger.info(f"数据集 {ds_id} 保存成功")
        return metadata
    
    def get_dataset(self, ds_id: str) -> Optional[Dict[str, Any]]:
        """
        获取数据集信息（兼容新旧两种存储格式）

        Args:
            ds_id: 数据集ID
            
        Returns:
            Dict: 数据集信息，如果不存在返回None
        """
        metadata_path = self.metadata_dir / f"{ds_id}.json"
        
        try:
            # 优先读取新格式：metadata/{ds_id}.json
            if metadata_path.exists():
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            # 回退读取旧扁平格式：{ds_id}.meta.json
            flat_path = self.datasets_dir / f"{ds_id}.meta.json"
            if flat_path.exists():
                with open(flat_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            return None
        except Exception as e:
            logger.error(f"读取数据集 {ds_id} 元数据失败: {e}")
            return None

    def _resolve_csv(self, ds_id: str) -> Optional[Path]:
        """
        按新旧两种存储布局查找数据集 CSV 文件

        Args:
            ds_id: 数据集ID

        Returns:
            Path: 找到的 CSV 文件路径；两种格式都不存在时返回 None
        """
        candidates = [
            self.processed_dir / f"{ds_id}.csv",      # 新结构：processed 目录
            self.raw_dir / f"{ds_id}.csv",            # 新结构：raw 目录
            self.datasets_dir / ds_id / f"{ds_id}.csv",  # 新结构：数据集目录副本
            self.datasets_dir / f"{ds_id}.csv",      # 旧扁平格式
        ]
        return next((p for p in candidates if p.exists()), None)
    
    def list_datasets(self, limit: int = 100, offset: int = 0, 
                     tags: List[str] = None, search: str = None) -> Tuple[List[Dict[str, Any]], int]:
        """
        列出数据集
        
        Args:
            limit: 限制数量
            offset: 偏移量
            tags: 标签过滤
            search: 搜索关键词
            
        Returns:
            Tuple[datasets, total]: 数据集列表和总数
        """
        datasets = []
        seen_ids = set()
        
        # 元数据来源：新结构 metadata/*.json 优先，随后补充旧扁平格式 *.meta.json（按 id 去重）
        sources = list(self.metadata_dir.glob("*.json")) + list(self.datasets_dir.glob("*.meta.json"))
        
        # 读取并过滤数据集
        for metadata_file in sources:
            try:
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                
                # 按 id 去重：同一数据集在新旧两种格式同时存在时，仅保留新格式的记录
                ds_id = metadata.get("id") or metadata_file.name.replace(".meta.json", "").replace(".json", "")
                if ds_id in seen_ids:
                    continue
                seen_ids.add(ds_id)
                
                # 标签过滤
                if tags:
                    if not any(tag in metadata.get("tags", []) for tag in tags):
                        continue
                
                # 搜索过滤
                if search:
                    search_lower = search.lower()
                    search_in_name = search_lower in metadata.get("name", "").lower()
                    search_in_desc = search_lower in metadata.get("description", "").lower()
                    search_in_cols = search_lower in " ".join(metadata.get("columns", [])).lower()
                    
                    if not (search_in_name or search_in_desc or search_in_cols):
                        continue
                
                datasets.append(metadata)
            except Exception as e:
                logger.error(f"读取数据集元数据失败 {metadata_file}: {e}")
                continue
        
        # 按创建时间排序
        datasets.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        
        # 分页
        total = len(datasets)
        datasets = datasets[offset:offset + limit]
        
        return datasets, total
    
    def delete_dataset(self, ds_id: str) -> bool:
        """删除 Hub 引用前等待下载退出，防止已删除的数据被下载线程重新写回。"""
        metadata = self.get_dataset(ds_id)
        if metadata and metadata.get("source") == "huggingface":
            with self._dataset_lock(ds_id):
                return self._delete_dataset_files(ds_id)
        return self._delete_dataset_files(ds_id)

    def _delete_dataset_files(self, ds_id: str) -> bool:
        """
        删除数据集
        
        Args:
            ds_id: 数据集ID
            
        Returns:
            bool: 是否删除成功
        """
        metadata = self.get_dataset(ds_id)
        if not metadata:
            return False
        
        try:
            # 删除文件（同时覆盖新旧两种存储格式的文件，避免残留）
            files_to_delete = [
                self.raw_dir / f"{ds_id}.csv",
                self.processed_dir / f"{ds_id}.csv",
                self.metadata_dir / f"{ds_id}.json",
                self.datasets_dir / ds_id / f"{ds_id}.csv",
                self.datasets_dir / ds_id / f"{ds_id}.meta.json",
                self.datasets_dir / f"{ds_id}.csv",        # 旧扁平格式
                self.datasets_dir / f"{ds_id}.meta.json",  # 旧扁平格式
            ]
            
            for file_path in files_to_delete:
                if file_path.exists():
                    file_path.unlink()
            
            # 删除数据集目录
            dataset_dir = self.datasets_dir / ds_id
            if dataset_dir.exists():
                shutil.rmtree(dataset_dir)
            
            logger.info(f"数据集 {ds_id} 删除成功")
            return True
        except Exception as e:
            logger.error(f"删除数据集 {ds_id} 失败: {e}")
            return False
    
    def get_dataset_preview(self, ds_id: str, rows: int = 5, offset: int = 0) -> Dict[str, Any]:
        """
        获取数据集指定页的预览数据。
        
        Args:
            ds_id: 数据集ID
            rows: 当前页行数
            offset: 当前页起始行（从 0 开始）
            
        Returns:
            Dict: 预览数据
        """
        page_size = max(1, min(int(rows), 50))
        page_offset = max(0, int(offset))
        metadata = self.ensure_local(ds_id)
        if not metadata:
            raise HTTPException(404, "数据集不存在")
        
        try:
            # 兼容新旧格式查找 CSV 文件
            csv_path = self._resolve_csv(ds_id)
            if csv_path is None:
                raise HTTPException(404, f"数据集文件不存在: {ds_id}.csv")
            total_rows = max(0, int(metadata.get("rows", 0) or 0))
            # 超出数据集末尾时返回空页，避免读取无意义的文件内容。
            if page_offset >= total_rows:
                preview = []
                column_types = metadata.get("column_types") or {}
            else:
                df = pd.read_csv(
                    csv_path,
                    skiprows=range(1, page_offset + 1),
                    nrows=page_size,
                )
                preview = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
                column_types = {str(c): str(t) for c, t in df.dtypes.items()}

            page_count = max(1, (total_rows + page_size - 1) // page_size)
            
            return {
                **metadata,
                "preview": preview,
                "column_types": column_types,
                "preview_offset": page_offset,
                "preview_limit": page_size,
                "preview_page": min(page_offset // page_size + 1, page_count),
                "preview_page_count": page_count,
                "preview_has_previous": page_offset > 0,
                "preview_has_next": page_offset + len(preview) < total_rows,
            }
        except Exception as e:
            logger.error(f"获取数据集 {ds_id} 预览失败: {e}")
            raise HTTPException(500, f"获取数据集预览失败: {e}")
    
    def _is_safe_filename(self, filename: str) -> bool:
        """检查文件名是否安全"""
        import re
        safe_pattern = re.compile(r'^[\w\-\.\s]+$', re.UNICODE)
        return bool(safe_pattern.match(filename)) and not filename.startswith('.')
    
    def get_dataset_stats(self, ds_id: str) -> Dict[str, Any]:
        """
        获取数据集统计信息
        
        Args:
            ds_id: 数据集ID
            
        Returns:
            Dict: 统计信息
        """
        metadata = self.ensure_local(ds_id)
        if not metadata:
            raise HTTPException(404, "数据集不存在")
        
        try:
            # 兼容新旧格式查找 CSV 文件
            csv_path = self._resolve_csv(ds_id)
            if csv_path is None:
                raise HTTPException(404, f"数据集文件不存在: {ds_id}.csv")
            df = pd.read_csv(csv_path)
            
            # 计算统计信息
            stats = {
                "total_rows": int(len(df)),
                "total_columns": int(len(df.columns)),
                "memory_usage_mb": float(df.memory_usage(deep=True).sum() / 1024 / 1024),
                "column_stats": {}
            }
            
            # 每列的统计信息
            for col in df.columns:
                col_data = df[col]
                col_type = str(col_data.dtype)
                
                if col_type in ['int64', 'float64']:
                    stats["column_stats"][col] = {
                        "type": col_type,
                        "min": float(col_data.min()),
                        "max": float(col_data.max()),
                        "mean": float(col_data.mean()),
                        "std": float(col_data.std()),
                        "null_count": int(col_data.isnull().sum())
                    }
                else:
                    stats["column_stats"][col] = {
                        "type": col_type,
                        "unique_count": int(col_data.nunique()),
                        "top_values": {str(k): int(v) for k, v in col_data.value_counts().head(5).items()},
                        "null_count": int(col_data.isnull().sum())
                    }
            
            return stats
        except Exception as e:
            logger.error(f"获取数据集 {ds_id} 统计信息失败: {e}")
            raise HTTPException(500, f"获取数据集统计信息失败: {e}")
