"""数据集迁移脚本，用于将现有数据集迁移到新的目录结构"""
import os
import json
import shutil
from pathlib import Path
import logging
from app.dataset_manager import DatasetManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_datasets(old_datasets_dir: Path, new_datasets_dir: Path):
    """迁移数据集到新的目录结构"""
    logger.info(f"开始迁移数据集从 {old_datasets_dir} 到 {new_datasets_dir}")
    
    # 创建新的数据集管理器
    dataset_manager = DatasetManager(new_datasets_dir)
    
    # 统计信息
    stats = {
        "total_datasets": 0,
        "migrated": 0,
        "failed": 0,
        "errors": []
    }
    
    # 遍历旧数据集目录
    for meta_file in old_datasets_dir.glob("*.meta.json"):
        stats["total_datasets"] += 1
        
        try:
            # 读取元数据
            with open(meta_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            ds_id = metadata["id"]
            logger.info(f"处理数据集: {ds_id}")
            
            # 检查对应的CSV文件
            csv_file = old_datasets_dir / f"{ds_id}.csv"
            if not csv_file.exists():
                error_msg = f"数据集 {ds_id} 的CSV文件不存在"
                logger.error(error_msg)
                stats["failed"] += 1
                stats["errors"].append(error_msg)
                continue
            
            # 读取数据
            import pandas as pd
            df = pd.read_csv(csv_file)
            
            # 保存到新结构
            new_metadata = dataset_manager.save_dataset(
                df, 
                metadata, 
                metadata.get("description", ""),
                metadata.get("tags", [])
            )
            
            logger.info(f"成功迁移数据集: {ds_id}")
            stats["migrated"] += 1
            
        except Exception as e:
            error_msg = f"迁移数据集 {meta_file.name} 失败: {str(e)}"
            logger.error(error_msg)
            stats["failed"] += 1
            stats["errors"].append(error_msg)
    
    # 迁移作业目录
    jobs_dir = old_datasets_dir.parent / "jobs"
    if jobs_dir.exists():
        new_jobs_dir = new_datasets_dir.parent / "jobs"
        if new_jobs_dir.exists():
            logger.info(f"合并作业目录: {jobs_dir} -> {new_jobs_dir}")
            for item in jobs_dir.iterdir():
                if item.is_dir():
                    dest = new_jobs_dir / item.name
                    if dest.exists():
                        logger.warning(f"作业目录 {item.name} 已存在，跳过")
                    else:
                        shutil.copytree(item, dest)
        else:
            logger.info(f"复制作业目录: {jobs_dir} -> {new_jobs_dir}")
            shutil.copytree(jobs_dir, new_jobs_dir)
    
    logger.info(f"迁移完成: {stats}")
    return stats

def verify_migration(old_datasets_dir: Path, new_datasets_dir: Path):
    """验证迁移结果"""
    logger.info("验证迁移结果...")
    
    old_count = sum(1 for _ in old_datasets_dir.glob("*.meta.json"))
    new_count = sum(1 for _ in (new_datasets_dir / "metadata").glob("*.json"))
    
    logger.info(f"旧数据集数量: {old_count}")
    logger.info(f"新数据集数量: {new_count}")
    
    if old_count == new_count:
        logger.info("数据集数量匹配，迁移成功")
    else:
        logger.warning("数据集数量不匹配，迁移可能存在问题")
    
    # 检查数据文件
    old_csv_count = sum(1 for _ in old_datasets_dir.glob("*.csv"))
    new_csv_count = sum(1 for _ in (new_datasets_dir / "processed").glob("*.csv"))
    
    logger.info(f"旧CSV文件数量: {old_csv_count}")
    logger.info(f"新CSV文件数量: {new_csv_count}")
    
    return old_count == new_count and old_csv_count == new_csv_count

if __name__ == "__main__":
    # 设置路径
    project_root = Path(__file__).resolve().parent.parent.parent
    old_datasets_dir = project_root / "data" / "datasets"
    new_datasets_dir = project_root / "data" / "datasets"
    
    # 执行迁移
    stats = migrate_datasets(old_datasets_dir, new_datasets_dir)
    
    # 验证迁移
    verify = verify_migration(old_datasets_dir, new_datasets_dir)
    
    # 输出结果
    print("\n迁移结果:")
    print(f"总数据集数: {stats['total_datasets']}")
    print(f"成功迁移: {stats['migrated']}")
    print(f"迁移失败: {stats['failed']}")
    print(f"验证结果: {'通过' if verify else '失败'}")
    
    if stats['errors']:
        print("\n错误信息:")
        for error in stats['errors']:
            print(f"- {error}")