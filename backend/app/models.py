"""数据库模型定义（重构版：支持 Notebook、模板、模型注册表、Pipeline、分布式训练）"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Column,
    Index,
    Integer,
    String,
    Text,
    Float,
    DateTime,
    JSON,
    ForeignKey,
    Boolean,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


# =============================================================================
# 基础设施表（保留兼容）
# =============================================================================

class Dataset(Base):
    """数据集模型"""
    __tablename__ = "datasets"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)  # 标签列表
    rows = Column(Integer, nullable=False, default=0)
    columns = Column(JSON, nullable=False)  # 列名列表
    column_types = Column(JSON, nullable=True)  # 列类型映射
    file_size = Column(Integer, nullable=True)
    source = Column(String(32), nullable=True)  # local/huggingface/oss
    storage_mode = Column(String(32), nullable=True)  # local/oss
    status = Column(String(32), default="active")  # active/archived/error
    cache_status = Column(String(32), nullable=True)  # remote/downloading/ready/error
    last_error = Column(Text, nullable=True)
    extra_metadata = Column(JSON, nullable=True)  # 扩展元数据（metadata 为 SQLAlchemy 保留字）
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    processed_at = Column(DateTime, nullable=True)

    # 关联
    jobs = relationship("TrainingJob", back_populates="dataset")
    notebook_sessions = relationship("NotebookSession", back_populates="dataset")


class TrainingJob(Base):
    """训练任务模型（镜像表，权威源在对象存储 job.json）"""
    __tablename__ = "training_jobs"

    id = Column(String(64), primary_key=True, index=True)
    dataset_id = Column(String(64), ForeignKey("datasets.id"), nullable=False)
    dataset_name = Column(String(255), nullable=False)
    target = Column(String(255), nullable=True)
    task = Column(String(64), nullable=False)  # classification/regression/clustering/time_series/llm_*
    status = Column(String(32), default="queued")  # queued/running/paused/completed/failed/cancelled
    created_at = Column(DateTime, default=func.now())
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    device = Column(String(64), nullable=True)  # cpu/cuda:0 等
    worker_id = Column(String(64), nullable=True)
    machine_id = Column(String(64), nullable=True)
    slot = Column(String(32), nullable=True)
    params = Column(JSON, nullable=False)  # 训练超参
    resource_hint = Column(JSON, nullable=True)  # 资源提示
    summary = Column(JSON, nullable=True)  # 训练摘要
    metrics = Column(Text, nullable=True)  # metrics JSON 字符串
    retry_count = Column(Integer, default=0)
    manual_retry_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    segments = Column(JSON, nullable=True)  # 执行片段记录

    # 关联
    dataset = relationship("Dataset", back_populates="jobs")
    logs = relationship("JobLog", back_populates="job", cascade="all, delete-orphan")
    model_registrations = relationship("RegisteredModel", back_populates="source_job")


class JobLog(Base):
    """任务日志模型"""
    __tablename__ = "job_logs"
    __table_args__ = (Index("ix_job_logs_job_id_id", "job_id", "id"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String(64), ForeignKey("training_jobs.id"), nullable=False)
    timestamp = Column(DateTime, default=func.now())
    message = Column(Text, nullable=False)

    job = relationship("TrainingJob", back_populates="logs")


# =============================================================================
# 新增：Notebook 会话与单元格
# =============================================================================

class NotebookSession(Base):
    """交互式笔记本会话"""
    __tablename__ = "notebook_sessions"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    owner = Column(String(128), nullable=True)
    dataset_id = Column(String(64), ForeignKey("datasets.id"), nullable=True)
    bound_workers = Column(JSON, nullable=True)  # 绑定的 Worker IDs 列表
    status = Column(String(32), default="starting")  # starting/running/idle/busy/stopped/error
    kernel_type = Column(String(16), default="python")  # python/bash
    extra_metadata = Column(JSON, nullable=True)  # 环境变量、依赖、挂载、变量快照（metadata 为保留字）
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    last_activity = Column(DateTime, default=func.now())
    stopped_at = Column(DateTime, nullable=True)

    dataset = relationship("Dataset", back_populates="notebook_sessions")
    cells = relationship("NotebookCell", back_populates="session", cascade="all, delete-orphan", order_by="NotebookCell.cell_index")


class NotebookCell(Base):
    """笔记本单元格历史"""
    __tablename__ = "notebook_cells"

    id = Column(String(64), primary_key=True, index=True)
    session_id = Column(String(64), ForeignKey("notebook_sessions.id"), nullable=False)
    cell_index = Column(Integer, nullable=False)
    cell_type = Column(String(16), nullable=False)  # code/markdown
    source = Column(Text, nullable=False)
    outputs = Column(JSON, nullable=True)  # 执行输出列表
    execution_count = Column(Integer, nullable=True)
    status = Column(String(16), default="pending")  # pending/running/completed/error
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    variables_snapshot = Column(JSON, nullable=True)  # 执行后变量快照（可序列化部分）

    session = relationship("NotebookSession", back_populates="cells")


# =============================================================================
# 新增：脚本模板注册表
# =============================================================================

class ScriptTemplate(Base):
    """脚本模板"""
    __tablename__ = "script_templates"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(64), nullable=False)  # classification/regression/clustering/time_series/llm_pretrain/llm_sft/llm_dpo/custom
    task_type = Column(String(64), nullable=False)  # 对应 trainer task
    content = Column(Text, nullable=False)  # Jinja2 模板内容
    parameters = Column(JSON, nullable=False)  # 参数定义 [{name, type, label, default, required, options}]
    dataset_requirements = Column(JSON, nullable=True)  # 数据集字段要求
    is_builtin = Column(Boolean, default=False)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    instantiations = relationship("TemplateInstantiation", back_populates="template")


class TemplateInstantiation(Base):
    """模板实例化记录"""
    __tablename__ = "template_instantiations"

    id = Column(String(64), primary_key=True, index=True)
    template_id = Column(String(64), ForeignKey("script_templates.id"), nullable=False)
    instantiated_type = Column(String(32), nullable=False)  # training_job/notebook_session/pipeline_step
    instantiated_id = Column(String(64), nullable=False)  # 目标对象 ID
    parameters = Column(JSON, nullable=False)  # 实例化时的参数值
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=func.now())

    template = relationship("ScriptTemplate", back_populates="instantiations")


# =============================================================================
# 新增：模型注册表
# =============================================================================

class RegisteredModel(Base):
    """注册模型（模型注册表核心表）"""
    __tablename__ = "registered_models"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    version = Column(String(64), nullable=False)
    source_job_id = Column(String(64), ForeignKey("training_jobs.id"), nullable=True)
    task = Column(String(64), nullable=False)  # classification/regression/clustering/time_series/llm_*
    framework = Column(String(32), nullable=False)  # torch/sklearn/xgboost
    metrics = Column(JSON, nullable=True)  # 关键指标 {accuracy: 0.95, f1: 0.93, ...}
    artifacts = Column(JSON, nullable=False)  # OSS 路径 {best: "s3://...", preprocessor: "s3://...", summary: "s3://..."}
    tags = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(16), default="ready")  # ready/archived/deployed
    deployed_endpoint = Column(String(512), nullable=True)
    deployment_config = Column(JSON, nullable=True)  # 部署配置
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_registered_models_name_version", "name", "version", unique=True),
        Index("ix_registered_models_task", "task"),
    )

    source_job = relationship("TrainingJob", back_populates="model_registrations")
    deployments = relationship("ModelDeployment", back_populates="model")


class ModelDeployment(Base):
    """模型部署记录"""
    __tablename__ = "model_deployments"

    id = Column(String(64), primary_key=True, index=True)
    model_id = Column(String(64), ForeignKey("registered_models.id"), nullable=False)
    model_version = Column(String(64), nullable=False)
    endpoint_name = Column(String(255), nullable=True)
    endpoint_url = Column(String(512), nullable=True)
    status = Column(String(32), default="pending")  # pending/running/stopped/failed
    config = Column(JSON, nullable=True)  # 部署配置（副本数、资源限制、自动扩缩容等）
    worker_ids = Column(JSON, nullable=True)  # 部署的 Worker 列表
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    stopped_at = Column(DateTime, nullable=True)

    model = relationship("RegisteredModel", back_populates="deployments")


# =============================================================================
# 新增：Pipeline 编排
# =============================================================================

class Pipeline(Base):
    """Pipeline 定义"""
    __tablename__ = "pipelines"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    steps = Column(JSON, nullable=False)  # 步骤定义数组
    schedule = Column(String(128), nullable=True)  # cron 表达式
    status = Column(String(16), default="draft")  # draft/active/paused/archived
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    runs = relationship("PipelineRun", back_populates="pipeline", cascade="all, delete-orphan")


class PipelineRun(Base):
    """Pipeline 执行记录"""
    __tablename__ = "pipeline_runs"

    id = Column(String(64), primary_key=True, index=True)
    pipeline_id = Column(String(64), ForeignKey("pipelines.id"), nullable=False)
    trigger = Column(String(32), default="manual")  # manual/scheduled/event
    status = Column(String(16), default="running")  # running/completed/failed/cancelled
    step_states = Column(JSON, nullable=True)  # 每步状态 {step_id: {status, artifact_keys, error, started_at, finished_at}}
    artifact_map = Column(JSON, nullable=True)  # 步骤产物映射 {step_id: {key: oss_path}}
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, default=func.now())
    finished_at = Column(DateTime, nullable=True)

    pipeline = relationship("Pipeline", back_populates="runs")
    step_runs = relationship("PipelineStepRun", back_populates="run", cascade="all, delete-orphan")


class PipelineStepRun(Base):
    """Pipeline 步骤执行记录"""
    __tablename__ = "pipeline_step_runs"

    id = Column(String(64), primary_key=True, index=True)
    run_id = Column(String(64), ForeignKey("pipeline_runs.id"), nullable=False)
    step_id = Column(String(64), nullable=False)
    step_name = Column(String(255), nullable=False)
    step_type = Column(String(64), nullable=False)  # dataset_pull/dataset_process/training/model_upload/custom_script
    status = Column(String(16), default="pending")  # pending/running/completed/failed/skipped
    config = Column(JSON, nullable=True)  # 步骤配置快照
    input_artifacts = Column(JSON, nullable=True)  # 输入产物键
    output_artifacts = Column(JSON, nullable=True)  # 输出产物键
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    worker_id = Column(String(64), nullable=True)

    run = relationship("PipelineRun", back_populates="step_runs")


# =============================================================================
# 远程管理表（保留兼容）
# =============================================================================

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