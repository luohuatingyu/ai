"""工具函数模块"""
import os
import json
import time
import uuid
import hashlib
from pathlib import Path
from typing import Dict, List, Any, Optional
import pandas as pd

def generate_id(prefix: str = "") -> str:
    """生成唯一ID"""
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    random_part = uuid.uuid4().hex[:6]
    return f"{prefix}-{timestamp}-{random_part}" if prefix else f"{timestamp}-{random_part}"

def calculate_file_hash(file_path: Path) -> str:
    """计算文件哈希值"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def safe_filename(filename: str) -> str:
    """生成安全的文件名"""
    import re
    # 移除或替换不安全的字符
    safe_pattern = re.compile(r'[^\w\-\.\s]', re.UNICODE)
    safe_name = safe_pattern.sub('_', filename)
    
    # 确保文件名不以点开头
    if safe_name.startswith('.'):
        safe_name = safe_name[1:]
    
    # 限制文件名长度
    max_length = 255
    if len(safe_name) > max_length:
        name, ext = os.path.splitext(safe_name)
        safe_name = name[:max_length - len(ext)] + ext
    
    return safe_name

def get_file_info(file_path: Path) -> Dict[str, Any]:
    """获取文件信息"""
    if not file_path.exists():
        return {}
    
    stat = file_path.stat()
    return {
        "size": stat.st_size,
        "created_at": stat.st_ctime,
        "modified_at": stat.st_mtime,
        "hash": calculate_file_hash(file_path) if stat.st_size > 0 else ""
    }

def validate_csv_structure(df: pd.DataFrame) -> Dict[str, Any]:
    """验证CSV文件结构"""
    issues = []
    
    # 检查列名
    for col in df.columns:
        if not isinstance(col, str) or col.strip() == "":
            issues.append(f"列名 '{col}' 无效")
    
    # 检查数据类型
    for col in df.columns:
        if df[col].dtype == 'object':
            # 检查是否可以转换为数值
            try:
                pd.to_numeric(df[col], errors='raise')
            except:
                pass
            else:
                issues.append(f"列 '{col}' 包含可以转换为数值的字符串数据")
    
    # 检查缺失值
    missing_cols = df.columns[df.isnull().any()].tolist()
    if missing_cols:
        issues.append(f"以下列包含缺失值: {', '.join(missing_cols)}")
    
    # 检查重复行
    duplicates = df.duplicated().sum()
    if duplicates > 0:
        issues.append(f"发现 {duplicates} 行重复数据")
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "summary": {
            "rows": len(df),
            "columns": len(df.columns),
            "memory_usage_mb": df.memory_usage(deep=True).sum() / 1024 / 1024
        }
    }

def format_bytes(size: int) -> str:
    """格式化字节大小"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

def format_duration(seconds: float) -> str:
    """格式化持续时间"""
    if seconds < 60:
        return f"{seconds:.2f} 秒"
    elif seconds < 3600:
        return f"{seconds / 60:.2f} 分钟"
    else:
        return f"{seconds / 3600:.2f} 小时"

def create_backup(source_path: Path, backup_dir: Path) -> Path:
    """创建文件备份"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成备份文件名
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_name = f"{source_path.stem}_{timestamp}{source_path.suffix}"
    backup_path = backup_dir / backup_name
    
    # 复制文件
    import shutil
    shutil.copy2(source_path, backup_path)
    
    return backup_path

def clean_temp_files(temp_dir: Path, max_age_hours: int = 24) -> int:
    """清理临时文件"""
    if not temp_dir.exists():
        return 0
    
    current_time = time.time()
    max_age_seconds = max_age_hours * 3600
    cleaned_count = 0
    
    for file_path in temp_dir.glob("*"):
        if file_path.is_file():
            file_age = current_time - file_path.stat().st_mtime
            if file_age > max_age_seconds:
                try:
                    file_path.unlink()
                    cleaned_count += 1
                except Exception:
                    continue
    
    return cleaned_count

def get_system_info() -> Dict[str, Any]:
    """获取系统信息"""
    import platform
    import psutil
    
    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "memory_total": psutil.virtual_memory().total,
        "memory_available": psutil.virtual_memory().available,
        "disk_usage": {
            "total": psutil.disk_usage('/').total,
            "used": psutil.disk_usage('/').used,
            "free": psutil.disk_usage('/').free
        }
    }