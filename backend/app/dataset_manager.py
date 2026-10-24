"""数据集管理模块，提供数据集上传、验证、预处理等功能"""
import os
import json
import time
import uuid
import shutil
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
            "memory_usage": df.memory_usage(deep=True).sum(),
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
        
        # 更新元数据
        metadata.update({
            "id": ds_id,
            "name": metadata.get("original_filename", f"dataset_{ds_id}"),
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
        获取数据集信息
        
        Args:
            ds_id: 数据集ID
            
        Returns:
            Dict: 数据集信息，如果不存在返回None
        """
        metadata_path = self.metadata_dir / f"{ds_id}.json"
        if not metadata_path.exists():
            return None
        
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            return metadata
        except Exception as e:
            logger.error(f"读取数据集 {ds_id} 元数据失败: {e}")
            return None
    
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
        
        # 获取所有元数据文件
        metadata_files = list(self.metadata_dir.glob("*.json"))
        
        # 读取并过滤数据集
        for metadata_file in metadata_files:
            try:
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                
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
            # 删除文件
            files_to_delete = [
                self.raw_dir / f"{ds_id}.csv",
                self.processed_dir / f"{ds_id}.csv",
                self.metadata_dir / f"{ds_id}.json",
                self.datasets_dir / ds_id / f"{ds_id}.csv",
                self.datasets_dir / ds_id / f"{ds_id}.meta.json"
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
    
    def get_dataset_preview(self, ds_id: str, rows: int = 5) -> Dict[str, Any]:
        """
        获取数据集预览
        
        Args:
            ds_id: 数据集ID
            rows: 预览行数
            
        Returns:
            Dict: 预览数据
        """
        metadata = self.get_dataset(ds_id)
        if not metadata:
            raise HTTPException(404, "数据集不存在")
        
        try:
            df = pd.read_csv(self.processed_dir / f"{ds_id}.csv", nrows=max(1, min(rows, 50)))
            preview = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
            
            return {
                **metadata,
                "preview": preview,
                "column_types": {str(c): str(t) for c, t in df.dtypes.items()},
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
        metadata = self.get_dataset(ds_id)
        if not metadata:
            raise HTTPException(404, "数据集不存在")
        
        try:
            df = pd.read_csv(self.processed_dir / f"{ds_id}.csv")
            
            # 计算统计信息
            stats = {
                "total_rows": len(df),
                "total_columns": len(df.columns),
                "memory_usage_mb": df.memory_usage(deep=True).sum() / 1024 / 1024,
                "column_stats": {}
            }
            
            # 每列的统计信息
            for col in df.columns:
                col_data = df[col]
                col_type = str(col_data.dtype)
                
                if col_type in ['int64', 'float64']:
                    stats["column_stats"][col] = {
                        "type": col_type,
                        "min": col_data.min(),
                        "max": col_data.max(),
                        "mean": col_data.mean(),
                        "std": col_data.std(),
                        "null_count": col_data.isnull().sum()
                    }
                else:
                    stats["column_stats"][col] = {
                        "type": col_type,
                        "unique_count": col_data.nunique(),
                        "top_values": col_data.value_counts().head(5).to_dict(),
                        "null_count": col_data.isnull().sum()
                    }
            
            return stats
        except Exception as e:
            logger.error(f"获取数据集 {ds_id} 统计信息失败: {e}")
            raise HTTPException(500, f"获取数据集统计信息失败: {e}")