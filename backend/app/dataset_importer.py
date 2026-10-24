"""数据集导入工具，支持批量导入和多种数据源"""
import os
import json
import time
import zipfile
import tarfile
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
import pandas as pd
from fastapi import HTTPException, UploadFile
import logging
from app.dataset_manager import DatasetManager
from app.utils import generate_id, safe_filename, validate_csv_structure

logger = logging.getLogger(__name__)

class DatasetImporter:
    """数据集导入器，支持多种数据源和批量导入"""
    
    def __init__(self, datasets_dir: Path):
        self.datasets_dir = datasets_dir
        self.supported_formats = ['.csv', '.xlsx', '.xls', '.json', '.zip', '.tar', '.tar.gz', '.tgz']
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
            file_ext = Path(file.filename or "").suffix.lower()
            if file_ext not in self.supported_formats:
                raise HTTPException(400, f"不支持文件格式: {file_ext}")
            
            # 保存临时文件
            temp_file_path = self.temp_dir / safe_filename(file.filename or "")
            with open(temp_file_path, "wb") as buffer:
                buffer.write(file.file.read())
            
            # 处理文件
            if file_ext in ['.zip', '.tar', '.tar.gz', '.tgz']:
                # 压缩文件，解压后导入
                result = self._import_from_archive(temp_file_path, description, tags, auto_validate)
            else:
                # 单个文件
                result = self._import_single_file(temp_file_path, description, tags, auto_validate)
            
            # 清理临时文件
            temp_file_path.unlink()
            
            return result
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"导入数据集失败: {e}")
            raise HTTPException(500, f"导入数据集失败: {e}")
    
    def _import_from_archive(self, archive_path: Path, description: str, 
                           tags: List[str], auto_validate: bool) -> Dict[str, Any]:
        """从压缩文件导入数据集"""
        extracted_files = []
        datasets = []
        
        try:
            # 解压文件
            if archive_path.suffix == '.zip':
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(self.temp_dir)
            elif archive_path.suffix in ['.tar', '.tar.gz', '.tgz']:
                with tarfile.open(archive_path, 'r:*') as tar_ref:
                    tar_ref.extractall(self.temp_dir)
            
            # 查找数据文件
            for file_path in self.temp_dir.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in ['.csv', '.xlsx', '.xls']:
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
            raise HTTPException(400, f"解压文件失败: {e}")
        finally:
            # 清理解压的文件
            self._clean_extracted_files()
    
    def _import_single_file(self, file_path: Path, description: str, 
                          tags: List[str], auto_validate: bool) -> Dict[str, Any]:
        """导入单个数据文件"""
        # 读取数据
        if file_path.suffix.lower() == '.csv':
            df = pd.read_csv(file_path)
        elif file_path.suffix.lower() in ['.xlsx', '.xls']:
            df = pd.read_excel(file_path)
        else:
            raise HTTPException(400, f"不支持文件格式: {file_path.suffix}")
        
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
    
    def _clean_extracted_files(self):
        """清理解压的文件"""
        for file_path in self.temp_dir.rglob("*"):
            if file_path.is_file():
                try:
                    file_path.unlink()
                except Exception:
                    continue
    
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
        file_ext = file_path.suffix.lower()
        
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
                data_files = [f for f in file_list if f.lower().endswith(('.csv', '.xlsx', '.xls'))]
                
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