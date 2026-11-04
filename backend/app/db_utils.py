"""数据库操作工具函数（重构版：支持 Notebook、模板、模型注册表、Pipeline）"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, func

from app.database import SessionLocal
from app.models import (
    Dataset,
    TrainingJob,
    JobLog,
    NotebookSession,
    NotebookCell,
    ScriptTemplate,
    TemplateInstantiation,
    RegisteredModel,
    ModelDeployment,
    Pipeline,
    PipelineRun,
    PipelineStepRun,
)


# =============================================================================
# 数据集操作
# =============================================================================

def save_dataset_to_db(
    db: Session,
    ds_id: str,
    name: str,
    rows: int,
    columns: list[str],
    description: str = "",
    tags: list[str] = None,
    column_types: dict = None,
    file_size: int = None,
    source: str = "local",
    storage_mode: str = "local",
    metadata: dict = None,
) -> Dataset:
    """将数据集信息保存到数据库"""
    dataset = Dataset(
        id=ds_id,
        name=name,
        description=description,
        tags=tags or [],
        rows=rows,
        columns=columns,
        column_types=column_types or {},
        file_size=file_size,
        source=source,
        storage_mode=storage_mode,
        extra_metadata=metadata or {},
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


def get_dataset_from_db(db: Session, ds_id: str) -> Optional[Dataset]:
    """从数据库获取数据集信息"""
    return db.query(Dataset).filter(Dataset.id == ds_id).first()


def update_dataset_in_db(
    db: Session,
    ds_id: str,
    **updates
) -> Optional[Dataset]:
    """更新数据集信息"""
    dataset = get_dataset_from_db(db, ds_id)
    if dataset:
        for key, value in updates.items():
            if hasattr(dataset, key):
                setattr(dataset, key, value)
        dataset.updated_at = datetime.now()
        db.commit()
        db.refresh(dataset)
    return dataset


def list_datasets_from_db(
    db: Session,
    limit: int = 100,
    offset: int = 0,
    tags: list[str] = None,
    search: str = None,
) -> tuple[list[Dataset], int]:
    """从数据库列出数据集"""
    query = db.query(Dataset)
    
    if tags:
        # 简单的标签过滤：至少包含一个指定标签
        for tag in tags:
            query = query.filter(Dataset.tags.contains([tag]))
    
    if search:
        search_lower = f"%{search.lower()}%"
        query = query.filter(
            (Dataset.name.ilike(search_lower)) |
            (Dataset.description.ilike(search_lower))
        )
    
    total = query.count()
    datasets = query.order_by(desc(Dataset.created_at)).offset(offset).limit(limit).all()
    return datasets, total


def delete_dataset_from_db(db: Session, ds_id: str) -> bool:
    """从数据库删除数据集"""
    dataset = get_dataset_from_db(db, ds_id)
    if dataset:
        db.delete(dataset)
        db.commit()
        return True
    return False


# =============================================================================
# 训练任务操作
# =============================================================================

def save_job_to_db(
    db: Session,
    job_id: str,
    dataset_id: str,
    dataset_name: str,
    target: str,
    task: str,
    params: dict,
    resource_hint: dict = None,
) -> TrainingJob:
    """将训练任务保存到数据库。"""
    if get_dataset_from_db(db, dataset_id) is None:
        db.add(Dataset(id=dataset_id, name=dataset_name, rows=0, columns=[]))
        db.flush()

    job = TrainingJob(
        id=job_id,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        target=target or "",
        task=task,
        params=params,
        resource_hint=resource_hint or {},
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
    worker_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    slot: Optional[str] = None,
    summary: Optional[dict] = None,
    metrics: Optional[list] = None,
    retry_count: Optional[int] = None,
    manual_retry_count: Optional[int] = None,
    last_error: Optional[str] = None,
    segments: Optional[list] = None,
) -> Optional[TrainingJob]:
    """更新任务状态"""
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if job:
        job.status = status
        if error is not None:
            job.error = error
        if device is not None:
            job.device = device
        if worker_id is not None:
            job.worker_id = worker_id
        if machine_id is not None:
            job.machine_id = machine_id
        if slot is not None:
            job.slot = slot
        if summary is not None:
            job.summary = summary
        if metrics is not None:
            job.metrics = json.dumps(metrics, ensure_ascii=False)
        if retry_count is not None:
            job.retry_count = retry_count
        if manual_retry_count is not None:
            job.manual_retry_count = manual_retry_count
        if last_error is not None:
            job.last_error = last_error
        if segments is not None:
            job.segments = segments

        if status == "running" and not job.started_at:
            job.started_at = datetime.now()
        elif status in ("completed", "failed", "cancelled") and not job.finished_at:
            job.finished_at = datetime.now()

        db.commit()
        db.refresh(job)
    return job


def get_job_from_db(db: Session, job_id: str) -> Optional[TrainingJob]:
    """从数据库获取任务信息"""
    return db.query(TrainingJob).filter(TrainingJob.id == job_id).first()


def get_all_jobs_from_db(db: Session, limit: int = 100, offset: int = 0) -> list[TrainingJob]:
    """获取所有任务"""
    return db.query(TrainingJob).order_by(desc(TrainingJob.created_at)).offset(offset).limit(limit).all()


def count_jobs_in_db(db: Session) -> int:
    """统计任务总数"""
    return db.query(TrainingJob).count()


# =============================================================================
# 任务日志操作
# =============================================================================

def add_job_log(db: Session, job_id: str, message: str) -> JobLog:
    """添加任务日志"""
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


# =============================================================================
# Notebook 会话操作
# =============================================================================

def create_notebook_session(
    db: Session,
    name: str,
    owner: str = None,
    dataset_id: str = None,
    bound_workers: list[str] = None,
    kernel_type: str = "python",
    metadata: dict = None,
    session_id: str = None,
) -> NotebookSession:
    """创建 Notebook 会话（session_id 缺省时自动生成）"""
    session = NotebookSession(
        id=session_id or f"nb-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
        name=name,
        owner=owner,
        dataset_id=dataset_id,
        bound_workers=bound_workers or [],
        kernel_type=kernel_type,
        metadata=metadata or {},
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_notebook_session(db: Session, session_id: str) -> Optional[NotebookSession]:
    """获取 Notebook 会话"""
    return db.query(NotebookSession).filter(NotebookSession.id == session_id).first()


def update_notebook_session(
    db: Session,
    session_id: str,
    **updates
) -> Optional[NotebookSession]:
    """更新 Notebook 会话"""
    session = get_notebook_session(db, session_id)
    if session:
        for key, value in updates.items():
            if hasattr(session, key):
                setattr(session, key, value)
        session.updated_at = datetime.now()
        db.commit()
        db.refresh(session)
    return session


def list_notebook_sessions(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    owner: str = None,
    status: str = None,
) -> tuple[list[NotebookSession], int]:
    """列出 Notebook 会话"""
    query = db.query(NotebookSession)
    if owner:
        query = query.filter(NotebookSession.owner == owner)
    if status:
        query = query.filter(NotebookSession.status == status)
    total = query.count()
    sessions = query.order_by(desc(NotebookSession.last_activity)).offset(offset).limit(limit).all()
    return sessions, total


def delete_notebook_session(db: Session, session_id: str) -> bool:
    """删除 Notebook 会话（级联删除单元格）"""
    session = get_notebook_session(db, session_id)
    if session:
        db.delete(session)
        db.commit()
        return True
    return False


# =============================================================================
# Notebook 单元格操作
# =============================================================================

def save_notebook_cell(
    db: Session,
    session_id: str,
    cell_index: int,
    cell_type: str,
    source: str,
    outputs: list = None,
    execution_count: int = None,
    status: str = "pending",
    variables_snapshot: dict = None,
) -> NotebookCell:
    """保存/更新单元格"""
    cell_id = f"cell-{uuid.uuid4().hex[:12]}"
    cell = NotebookCell(
        id=cell_id,
        session_id=session_id,
        cell_index=cell_index,
        cell_type=cell_type,
        source=source,
        outputs=outputs or [],
        execution_count=execution_count,
        status=status,
        started_at=datetime.now() if status == "running" else None,
        variables_snapshot=variables_snapshot,
    )
    db.add(cell)
    db.commit()
    db.refresh(cell)
    return cell


def update_notebook_cell(
    db: Session,
    cell_id: str,
    **updates
) -> Optional[NotebookCell]:
    """更新单元格"""
    cell = db.query(NotebookCell).filter(NotebookCell.id == cell_id).first()
    if cell:
        for key, value in updates.items():
            if hasattr(cell, key):
                setattr(cell, key, value)
        if "status" in updates and updates["status"] == "completed":
            cell.finished_at = datetime.now()
        db.commit()
        db.refresh(cell)
    return cell


def get_notebook_cells(db: Session, session_id: str) -> list[NotebookCell]:
    """获取会话的所有单元格（按索引排序）"""
    return db.query(NotebookCell).filter(
        NotebookCell.session_id == session_id
    ).order_by(NotebookCell.cell_index).all()


def delete_notebook_cells(db: Session, session_id: str) -> int:
    """删除会话的所有单元格"""
    deleted = db.query(NotebookCell).filter(
        NotebookCell.session_id == session_id
    ).delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


# =============================================================================
# 脚本模板操作
# =============================================================================

def create_script_template(
    db: Session,
    name: str,
    category: str,
    task_type: str,
    content: str,
    parameters: list[dict],
    description: str = "",
    dataset_requirements: dict = None,
    is_builtin: bool = False,
    created_by: str = None,
) -> ScriptTemplate:
    """创建脚本模板"""
    template_id = f"tpl-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}" if not is_builtin else f"builtin-{category}"
    template = ScriptTemplate(
        id=template_id,
        name=name,
        description=description,
        category=category,
        task_type=task_type,
        content=content,
        parameters=parameters,
        dataset_requirements=dataset_requirements or {},
        is_builtin=is_builtin,
        created_by=created_by,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def get_script_template(db: Session, template_id: str) -> Optional[ScriptTemplate]:
    """获取脚本模板"""
    return db.query(ScriptTemplate).filter(ScriptTemplate.id == template_id).first()


def list_script_templates(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    category: str = None,
    is_builtin: bool = None,
) -> tuple[list[ScriptTemplate], int]:
    """列出脚本模板"""
    query = db.query(ScriptTemplate)
    if category:
        query = query.filter(ScriptTemplate.category == category)
    if is_builtin is not None:
        query = query.filter(ScriptTemplate.is_builtin == is_builtin)
    total = query.count()
    templates = query.order_by(desc(ScriptTemplate.created_at)).offset(offset).limit(limit).all()
    return templates, total


def update_script_template(
    db: Session,
    template_id: str,
    **updates
) -> Optional[ScriptTemplate]:
    """更新脚本模板"""
    template = get_script_template(db, template_id)
    if template:
        for key, value in updates.items():
            if hasattr(template, key):
                setattr(template, key, value)
        template.updated_at = datetime.now()
        db.commit()
        db.refresh(template)
    return template


def delete_script_template(db: Session, template_id: str) -> bool:
    """删除脚本模板"""
    template = get_script_template(db, template_id)
    if template:
        db.delete(template)
        db.commit()
        return True
    return False


def record_template_instantiation(
    db: Session,
    template_id: str,
    instantiated_type: str,
    instantiated_id: str,
    parameters: dict,
    created_by: str = None,
) -> TemplateInstantiation:
    """记录模板实例化"""
    instantiation = TemplateInstantiation(
        id=f"inst-{uuid.uuid4().hex[:12]}",
        template_id=template_id,
        instantiated_type=instantiated_type,
        instantiated_id=instantiated_id,
        parameters=parameters,
        created_by=created_by,
    )
    db.add(instantiation)
    db.commit()
    db.refresh(instantiation)
    return instantiation


# =============================================================================
# 模型注册表操作
# =============================================================================

def register_model(
    db: Session,
    name: str,
    version: str,
    source_job_id: str = None,
    task: str = "",
    framework: str = "torch",
    metrics: dict = None,
    artifacts: dict = None,
    tags: list[str] = None,
    description: str = "",
) -> RegisteredModel:
    """注册模型"""
    model = RegisteredModel(
        id=f"model-{uuid.uuid4().hex[:12]}",
        name=name,
        version=version,
        source_job_id=source_job_id,
        task=task,
        framework=framework,
        metrics=metrics or {},
        artifacts=artifacts or {},
        tags=tags or [],
        description=description,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def get_registered_model(db: Session, model_id: str) -> Optional[RegisteredModel]:
    """获取注册模型"""
    return db.query(RegisteredModel).filter(RegisteredModel.id == model_id).first()


def get_registered_model_by_name_version(
    db: Session,
    name: str,
    version: str
) -> Optional[RegisteredModel]:
    """按名称和版本获取模型"""
    return db.query(RegisteredModel).filter(
        RegisteredModel.name == name,
        RegisteredModel.version == version
    ).first()


def list_registered_models(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    task: str = None,
    framework: str = None,
    status: str = None,
    search: str = None,
) -> tuple[list[RegisteredModel], int]:
    """列出注册模型"""
    query = db.query(RegisteredModel)
    if task:
        query = query.filter(RegisteredModel.task == task)
    if framework:
        query = query.filter(RegisteredModel.framework == framework)
    if status:
        query = query.filter(RegisteredModel.status == status)
    if search:
        search_lower = f"%{search.lower()}%"
        query = query.filter(
            (RegisteredModel.name.ilike(search_lower)) |
            (RegisteredModel.description.ilike(search_lower))
        )
    total = query.count()
    models = query.order_by(desc(RegisteredModel.created_at)).offset(offset).limit(limit).all()
    return models, total


def update_registered_model(
    db: Session,
    model_id: str,
    **updates
) -> Optional[RegisteredModel]:
    """更新注册模型"""
    model = get_registered_model(db, model_id)
    if model:
        for key, value in updates.items():
            if hasattr(model, key):
                setattr(model, key, value)
        model.updated_at = datetime.now()
        db.commit()
        db.refresh(model)
    return model


def delete_registered_model(db: Session, model_id: str) -> bool:
    """删除/归档注册模型"""
    model = get_registered_model(db, model_id)
    if model:
        model.status = "archived"
        db.commit()
        return True
    return False


# =============================================================================
# 模型部署操作
# =============================================================================

def create_model_deployment(
    db: Session,
    model_id: str,
    model_version: str,
    endpoint_name: str = None,
    config: dict = None,
    worker_ids: list[str] = None,
) -> ModelDeployment:
    """创建模型部署记录"""
    deployment = ModelDeployment(
        id=f"deploy-{uuid.uuid4().hex[:12]}",
        model_id=model_id,
        model_version=model_version,
        endpoint_name=endpoint_name,
        config=config or {},
        worker_ids=worker_ids or [],
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return deployment


def get_model_deployment(db: Session, deployment_id: str) -> Optional[ModelDeployment]:
    """获取部署记录"""
    return db.query(ModelDeployment).filter(ModelDeployment.id == deployment_id).first()


def list_model_deployments(
    db: Session,
    model_id: str = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ModelDeployment], int]:
    """列出部署记录"""
    query = db.query(ModelDeployment)
    if model_id:
        query = query.filter(ModelDeployment.model_id == model_id)
    total = query.count()
    deployments = query.order_by(desc(ModelDeployment.created_at)).offset(offset).limit(limit).all()
    return deployments, total


def update_model_deployment(
    db: Session,
    deployment_id: str,
    **updates
) -> Optional[ModelDeployment]:
    """更新部署记录"""
    deployment = get_model_deployment(db, deployment_id)
    if deployment:
        for key, value in updates.items():
            if hasattr(deployment, key):
                setattr(deployment, key, value)
        deployment.updated_at = datetime.now()
        if "status" in updates and updates["status"] in ("stopped", "failed"):
            deployment.stopped_at = datetime.now()
        db.commit()
        db.refresh(deployment)
    return deployment


# =============================================================================
# Pipeline 操作
# =============================================================================

def create_pipeline(
    db: Session,
    name: str,
    steps: list[dict],
    description: str = "",
    schedule: str = None,
    pipeline_id: str = None,
) -> Pipeline:
    """创建 Pipeline（pipeline_id 缺省时自动生成）"""
    pipeline = Pipeline(
        id=pipeline_id or f"pipe-{uuid.uuid4().hex[:12]}",
        name=name,
        description=description,
        steps=steps,
        schedule=schedule,
    )
    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)
    return pipeline


def get_pipeline(db: Session, pipeline_id: str) -> Optional[Pipeline]:
    """获取 Pipeline"""
    return db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()


def list_pipelines(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    status: str = None,
) -> tuple[list[Pipeline], int]:
    """列出 Pipeline"""
    query = db.query(Pipeline)
    if status:
        query = query.filter(Pipeline.status == status)
    total = query.count()
    pipelines = query.order_by(desc(Pipeline.created_at)).offset(offset).limit(limit).all()
    return pipelines, total


def update_pipeline(
    db: Session,
    pipeline_id: str,
    **updates
) -> Optional[Pipeline]:
    """更新 Pipeline"""
    pipeline = get_pipeline(db, pipeline_id)
    if pipeline:
        for key, value in updates.items():
            if hasattr(pipeline, key):
                setattr(pipeline, key, value)
        pipeline.updated_at = datetime.now()
        db.commit()
        db.refresh(pipeline)
    return pipeline


def delete_pipeline(db: Session, pipeline_id: str) -> bool:
    """删除 Pipeline（级联删除执行记录）"""
    pipeline = get_pipeline(db, pipeline_id)
    if pipeline:
        db.delete(pipeline)
        db.commit()
        return True
    return False


def create_pipeline_run(
    db: Session,
    pipeline_id: str,
    trigger: str = "manual",
) -> PipelineRun:
    """创建 Pipeline 执行记录"""
    run = PipelineRun(
        id=f"run-{uuid.uuid4().hex[:12]}",
        pipeline_id=pipeline_id,
        trigger=trigger,
        step_states={},
        artifact_map={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_pipeline_run(db: Session, run_id: str) -> Optional[PipelineRun]:
    """获取 Pipeline 执行记录"""
    return db.query(PipelineRun).filter(PipelineRun.id == run_id).first()


def update_pipeline_run(
    db: Session,
    run_id: str,
    **updates
) -> Optional[PipelineRun]:
    """更新 Pipeline 执行记录"""
    run = get_pipeline_run(db, run_id)
    if run:
        for key, value in updates.items():
            if hasattr(run, key):
                setattr(run, key, value)
        if "status" in updates and updates["status"] in ("completed", "failed", "cancelled"):
            run.finished_at = datetime.now()
        db.commit()
        db.refresh(run)
    return run


def list_pipeline_runs(
    db: Session,
    pipeline_id: str = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PipelineRun], int]:
    """列出 Pipeline 执行记录"""
    query = db.query(PipelineRun)
    if pipeline_id:
        query = query.filter(PipelineRun.pipeline_id == pipeline_id)
    total = query.count()
    runs = query.order_by(desc(PipelineRun.started_at)).offset(offset).limit(limit).all()
    return runs, total


def create_pipeline_step_run(
    db: Session,
    run_id: str,
    step_id: str,
    step_name: str,
    step_type: str,
    config: dict = None,
    input_artifacts: dict = None,
) -> PipelineStepRun:
    """创建步骤执行记录"""
    step_run = PipelineStepRun(
        id=f"steprun-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        step_id=step_id,
        step_name=step_name,
        step_type=step_type,
        config=config or {},
        input_artifacts=input_artifacts or {},
    )
    db.add(step_run)
    db.commit()
    db.refresh(step_run)
    return step_run


def update_pipeline_step_run(
    db: Session,
    step_run_id: str,
    **updates
) -> Optional[PipelineStepRun]:
    """更新步骤执行记录"""
    step_run = db.query(PipelineStepRun).filter(PipelineStepRun.id == step_run_id).first()
    if step_run:
        for key, value in updates.items():
            if hasattr(step_run, key):
                setattr(step_run, key, value)
        if "status" in updates and updates["status"] in ("completed", "failed"):
            step_run.finished_at = datetime.now()
        db.commit()
        db.refresh(step_run)
    return step_run


def get_pipeline_step_runs(db: Session, run_id: str) -> list[PipelineStepRun]:
    """获取 Pipeline 执行的所有步骤记录"""
    return db.query(PipelineStepRun).filter(
        PipelineStepRun.run_id == run_id
    ).order_by(PipelineStepRun.started_at).all()