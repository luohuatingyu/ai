"""数据库操作工具函数"""
from __future__ import annotations

import json
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Dataset, TrainingJob, JobLog


def save_dataset_to_db(
    db: Session, 
    ds_id: str, 
    name: str, 
    rows: int, 
    columns: list[str],
) -> Dataset:
    """将数据集信息保存到数据库"""
    dataset = Dataset(
        id=ds_id,
        name=name,
        rows=rows,
        columns=columns,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


def get_dataset_from_db(db: Session, ds_id: str) -> Optional[Dataset]:
    """从数据库获取数据集信息"""
    return db.query(Dataset).filter(Dataset.id == ds_id).first()


def save_job_to_db(
    db: Session,
    job_id: str,
    dataset_id: str,
    dataset_name: str,
    target: str,
    task: str,
    params: dict,
) -> TrainingJob:
    """将训练任务保存到数据库。

    training_jobs.dataset_id 外键关联 datasets.id：若数据集行不存在
    （例如数据集在数据库不可用期间创建），先补一行占位数据集，保证外键成立。
    """
    if get_dataset_from_db(db, dataset_id) is None:
        db.add(Dataset(id=dataset_id, name=dataset_name, rows=0, columns=[]))
        db.flush()

    job = TrainingJob(
        id=job_id,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        target=target,
        task=task,
        params=params,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_job_status(
    db: Session, 
    job_id: str, 
    status: str, 
    error: Optional[str] = None,
    device: Optional[str] = None,
    summary: Optional[dict] = None,
    metrics: Optional[list] = None,
) -> Optional[TrainingJob]:
    """更新任务状态"""
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if job:
        job.status = status
        if error:
            job.error = error
        if device:
            job.device = device
        if summary:
            job.summary = summary
        if metrics is not None:
            job.metrics = json.dumps(metrics, ensure_ascii=False)
        
        if status == "running" and not job.started_at:
            from datetime import datetime
            job.started_at = datetime.now()
        elif status in ("completed", "failed", "cancelled") and not job.finished_at:
            from datetime import datetime
            job.finished_at = datetime.now()
        
        db.commit()
        db.refresh(job)
    return job


def get_job_from_db(db: Session, job_id: str) -> Optional[TrainingJob]:
    """从数据库获取任务信息"""
    return db.query(TrainingJob).filter(TrainingJob.id == job_id).first()


def get_all_jobs_from_db(db: Session) -> list[TrainingJob]:
    """获取所有任务"""
    return db.query(TrainingJob).order_by(TrainingJob.created_at.desc()).all()


def add_job_log(db: Session, job_id: str, message: str) -> JobLog:
    """添加任务日志"""
    from datetime import datetime
    log = JobLog(
        job_id=job_id,
        message=message,
        timestamp=datetime.now(),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_job_logs(db: Session, job_id: str, offset: int = 0, limit: int = 500) -> list[JobLog]:
    """获取任务日志（时间戳秒级重复，按 (timestamp, id) 保证顺序稳定）"""
    return (
        db.query(JobLog)
        .filter(JobLog.job_id == job_id)
        .order_by(JobLog.timestamp, JobLog.id)
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_job_logs_after(db: Session, job_id: str, after_id: int = 0, limit: int = 501) -> list[JobLog]:
    """按递增主键读取后续日志，避免轮询时重复扫描旧页。"""
    return (
        db.query(JobLog)
        .filter(JobLog.job_id == job_id, JobLog.id > max(int(after_id), 0))
        .order_by(JobLog.id)
        .limit(limit)
        .all()
    )


def count_job_logs(db: Session, job_id: str) -> int:
    """仅统计旧版偏移分页所需的行数，不加载日志实体。"""
    return int(db.query(JobLog.id).filter(JobLog.job_id == job_id).count())


def delete_job_logs(db: Session, job_id: str) -> int:
    """删除指定任务的数据库日志，并返回删除条数。"""
    deleted = db.query(JobLog).filter(JobLog.job_id == job_id).delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)
