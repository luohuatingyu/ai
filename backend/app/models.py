"""数据库模型定义"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, JSON, ForeignKey
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Dataset(Base):
    """数据集模型"""
    __tablename__ = "datasets"
    
    id = Column(String(50), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    rows = Column(Integer, nullable=False)
    columns = Column(JSON, nullable=False)  # 存储列名列表
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # 关联的训练任务
    jobs = relationship("TrainingJob", back_populates="dataset")


class TrainingJob(Base):
    """训练任务模型"""
    __tablename__ = "training_jobs"
    
    id = Column(String(50), primary_key=True, index=True)
    dataset_id = Column(String(50), ForeignKey("datasets.id"), nullable=False)
    dataset_name = Column(String(255), nullable=False)
    target = Column(String(255), nullable=False)
    task = Column(String(50), nullable=False)  # classification / regression / clustering / time_series_forecasting
    status = Column(String(50), default="queued")  # queued, running, completed, failed, cancelled
    created_at = Column(DateTime, default=func.now())
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    device = Column(String(50), nullable=True)  # cpu 或 gpu
    params = Column(JSON, nullable=False)  # 训练参数
    summary = Column(JSON, nullable=True)  # 训练摘要
    metrics = Column(Text, nullable=True)  # 存储metrics的JSON字符串
    
    # 关联的数据集
    dataset = relationship("Dataset", back_populates="jobs")


class JobLog(Base):
    """任务日志模型"""
    __tablename__ = "job_logs"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String(50), ForeignKey("training_jobs.id"), nullable=False)
    timestamp = Column(DateTime, default=func.now())
    message = Column(Text, nullable=False)
    
    # 关联的训练任务
    job = relationship("TrainingJob")


class RemoteServerConfig(Base):
    """MySQL 中持久化的远程服务器配置记录。"""
    __tablename__ = "remote_servers"

    machine_id = Column(String(64), primary_key=True)
    config_data = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class RemoteSSHCredentialStore(Base):
    """MySQL 中加密保存的平台 SSH 凭证目录。"""
    __tablename__ = "remote_ssh_credentials"

    store_id = Column(String(32), primary_key=True, default="platform")
    encrypted_credentials = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class RemoteConfigMigration(Base):
    """记录旧 Redis 远程配置是否已经导入 MySQL。"""
    __tablename__ = "remote_config_migrations"

    migration_id = Column(String(64), primary_key=True)
    completed_at = Column(DateTime, default=func.now(), nullable=False)
