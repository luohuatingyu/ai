"""数据集导入工具，支持批量导入和多种数据源"""
import json
import shutil
import stat
import tempfile
import time
import zipfile
import tarfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Dict, List, Any
import pandas as pd
from fastapi import HTTPException, UploadFile
import logging
from app.dataset_manager import DatasetManager
from app.utils import safe_filename, validate_csv_structure

logger = logging.getLogger(__name__)

class DatasetImporter:
    """数据集导入器，支持多种数据源和批量导入"""
    
    def __init__(self, datasets_dir: Path):
        self.datasets_dir = datasets_dir
        self.supported_formats = ['.csv', '.xlsx', '.xls', '.json', '.jsonl', '.ndjson', '.zip', '.tar', '.tar.gz', '.tgz']
        self.temp_dir = datasets_dir / "temp" / "import"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    def import_dataset(self, file: UploadFile, description: str = "", 
                      tags: List[str] = None, auto_validate: bool = True) -> Dict[str, Any]:
        """
        导入数据集
        
        Args:
            file: 上传的文件
            description: 数据集描述
            tags: 标签列表
            auto_validate: 是否自动验证数据
            
        Returns:
            Dict: 导入结果
        """
        try:
            # 检查文件格式
            filename = file.filename or ""
            file_ext = self._file_extension(filename)
            if file_ext not in self.supported_formats:
                raise HTTPException(400, f"不支持文件格式: {file_ext}")
            
            # 每次导入使用独立临时目录，避免并发覆盖和清理时误删上传文件。
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="upload-", dir=self.temp_dir) as work_dir:
                temp_file_path = Path(work_dir) / safe_filename(filename)
                with open(temp_file_path, "wb") as buffer:
                    buffer.write(file.file.read())

                if file_ext in {".zip", ".tar", ".tar.gz", ".tgz"}:
                    return self._import_from_archive(
                        temp_file_path, description, tags, auto_validate
                    )
                return self._import_single_file(
                    temp_file_path, description, tags, auto_validate
                )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"导入数据集失败: {e}")
            raise HTTPException(500, f"导入数据集失败: {e}")

    def _file_extension(self, filename: str) -> str:
        """获取后缀并保留 .tar.gz 复合扩展名。"""
        lowered = (filename or "").lower()
        if lowered.endswith(".tar.gz"):
            return ".tar.gz"
        return Path(lowered).suffix

    def _safe_member_path(self, destination: Path, member_name: str) -> Path:
        """校验压缩包成员路径，拒绝绝对路径、盘符和目录穿越。"""
        normalized = (member_name or "").replace("\\", "/")
        member = PurePosixPath(normalized)
        if (not normalized or normalized.startswith("/") or
                any(part == ".." for part in member.parts) or
                (member.parts and ":" in member.parts[0])):
            raise HTTPException(400, f"压缩包包含危险路径: {member_name}")

        root = destination.resolve()
        target = (root / Path(*member.parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(400, f"压缩包包含危险路径: {member_name}") from exc
        return target

    def _extract_archive_safely(self, archive_path: Path, destination: Path) -> None:
        """只解出普通文件和目录，避免 ZIP/TAR 路径穿越及链接攻击。"""
        destination.mkdir(parents=True, exist_ok=True)
        file_ext = self._file_extension(archive_path.name)

        if file_ext == ".zip":
            with zipfile.ZipFile(archive_path, "r") as archive:
                for item in archive.infolist():
                    target = self._safe_member_path(destination, item.filename)
                    mode = (item.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise HTTPException(400, "压缩包不能包含符号链接")
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item, "r") as source, open(target, "wb") as output:
                        shutil.copyfileobj(source, output)
            return

        with tarfile.open(archive_path, "r:*") as archive:
            for item in archive.getmembers():
                target = self._safe_member_path(destination, item.name)
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not item.isfile():
                    raise HTTPException(400, "压缩包不能包含链接或特殊文件")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(item)
                if source is None:
                    raise HTTPException(400, f"压缩包成员无法读取: {item.name}")
                with source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output)
    
    def _import_from_archive(self, archive_path: Path, description: str, 
                           tags: List[str], auto_validate: bool) -> Dict[str, Any]:
        """从压缩文件导入数据集"""
        extracted_files = []
        datasets = []
        extraction_dir = archive_path.parent / "extracted"
        
        try:
            # 安全解压到当前上传的私有临时目录。
            self._extract_archive_safely(archive_path, extraction_dir)
            
            # 查找数据文件
            supported_data = {".csv", ".xlsx", ".xls", ".json", ".jsonl", ".ndjson"}
            for file_path in sorted(extraction_dir.rglob("*")):
                if file_path.is_file() and self._file_extension(file_path.name) in supported_data:
                    extracted_files.append(file_path)
            
            if not extracted_files:
                raise HTTPException(400, "压缩文件中未找到支持的数据文件")
            
            # 导入每个数据文件
            for file_path in extracted_files:
                try:
                    dataset_info = self._import_single_file(
                        file_path, 
                        f"{description} (来自 {archive_path.name})" if description else f"来自 {archive_path.name}",
                        tags or [],
                        auto_validate
                    )
                    datasets.append(dataset_info)
                except Exception as e:
                    logger.error(f"导入文件 {file_path.name} 失败: {e}")
                    continue
            
            if not datasets:
                raise HTTPException(400, "没有成功导入的数据集")
            
            return {
                "success": True,
                "imported_count": len(datasets),
                "datasets": datasets,
                "archive_name": archive_path.name,
                "extracted_files": len(extracted_files)
            }
            
        except Exception as e:
            logger.error(f"解压文件失败: {e}")
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(400, f"解压文件失败: {e}")
    
    def _import_single_file(self, file_path: Path, description: str, 
                          tags: List[str], auto_validate: bool) -> Dict[str, Any]:
        """导入单个数据文件"""
        # 读取数据
        file_ext = self._file_extension(file_path.name)
        try:
            if file_ext == ".csv":
                df = pd.read_csv(file_path)
            elif file_ext == ".json":
                df = pd.read_json(file_path)
            elif file_ext in {".jsonl", ".ndjson"}:
                # LLM 常见的 JSON Lines 语料逐行解析，导入后由 DatasetManager 统一存储。
                df = pd.read_json(file_path, lines=True)
            elif file_ext in {".xlsx", ".xls"}:
                df = pd.read_excel(file_path)
            else:
                raise HTTPException(400, f"不支持文件格式: {file_ext}")
        except HTTPException:
            raise
        except (ValueError, ImportError, OSError) as exc:
            raise HTTPException(400, f"数据文件解析失败: {exc}") from exc
        
        # 验证数据
        if auto_validate:
            validation_result = validate_csv_structure(df)
            if not validation_result["valid"]:
                logger.warning(f"数据验证发现问题: {validation_result['issues']}")
        
        # 创建数据集管理器
        dataset_manager = DatasetManager(self.datasets_dir)
        
        # 保存数据集
        metadata = {
            "original_filename": file_path.name,
            "imported_from": str(file_path),
            "imported_at": time.time()
        }
        
        result = dataset_manager.save_dataset(df, metadata, description, tags)
        
        # 保存导入记录
        self._save_import_record(file_path, result)
        
        return result
    
    def _save_import_record(self, source_path: Path, dataset_info: Dict[str, Any]):
        """保存导入记录"""
        import_records_dir = self.datasets_dir / "import_records"
        import_records_dir.mkdir(parents=True, exist_ok=True)
        
        record = {
            "dataset_id": dataset_info["id"],
            "source_file": str(source_path),
            "source_filename": source_path.name,
            "imported_at": time.time(),
            "dataset_info": dataset_info
        }
        
        record_path = import_records_dir / f"import_{dataset_info['id']}.json"
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    
    def get_import_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取导入历史"""
        import_records_dir = self.datasets_dir / "import_records"
        if not import_records_dir.exists():
            return []
        
        records = []
        for record_file in sorted(import_records_dir.glob("import_*.json"), 
                                key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(record_file, 'r', encoding='utf-8') as f:
                    records.append(json.load(f))
            except Exception:
                continue
        
        return records[:limit]
    
    def validate_file_structure(self, file_path: Path) -> Dict[str, Any]:
        """验证文件结构"""
        file_ext = self._file_extension(file_path.name)
        
        if file_ext not in self.supported_formats:
            return {
                "valid": False,
                "error": f"不支持文件格式: {file_ext}"
            }
        
        if file_ext in ['.zip', '.tar', '.tar.gz', '.tgz']:
            # 验证压缩文件
            try:
                if file_ext == '.zip':
                    with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        file_list = zip_ref.namelist()
                else:
                    with tarfile.open(file_path, 'r:*') as tar_ref:
                        file_list = tar_ref.getnames()
                
                # 查找数据文件
                data_files = [f for f in file_list if self._file_extension(f) in {".csv", ".xlsx", ".xls", ".json"}]
                
                return {
                    "valid": True,
                    "file_type": "archive",
                    "total_files": len(file_list),
                    "data_files": data_files,
                    "data_file_count": len(data_files)
                }
            except Exception as e:
                return {
                    "valid": False,
                    "error": f"压缩文件损坏: {str(e)}"
                }
        else:
            # 验证单个数据文件
            try:
                if file_ext == '.csv':
                    df = pd.read_csv(file_path)
                elif file_ext == '.json':
                    df = pd.read_json(file_path)
                elif file_ext in ['.xlsx', '.xls']:
                    df = pd.read_excel(file_path)
                else:
                    return {
                        "valid": False,
                        "error": f"不支持文件格式: {file_ext}"
                    }
                
                validation_result = validate_csv_structure(df)
                
                return {
                    "valid": True,
                    "file_type": "data",
                    "validation": validation_result,
                    "preview": df.head().to_dict('records')
                }
            except Exception as e:
                return {
                    "valid": False,
                    "error": f"数据文件解析失败: {str(e)}"
                }
    
    def batch_import(self, files: List[UploadFile], description: str = "", 
                    tags: List[str] = None) -> Dict[str, Any]:
        """批量导入数据集"""
        results = {
            "success": [],
            "failed": [],
            "total": len(files)
        }
        
        for file in files:
            try:
                result = self.import_dataset(file, description, tags)
                results["success"].append({
                    "filename": file.filename,
                    "dataset_id": result["id"]
                })
            except Exception as e:
                results["failed"].append({
                    "filename": file.filename,
                    "error": str(e)
                })
        
        return results
