"""Pipeline 编排 API：定义、执行、监控"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.orchestration.engine import PipelineEngine, PipelineScheduler
from app.orchestration.models import Pipeline, PipelineRun, PipelineStep, StepType, RunStatus, TriggerType
from app.db_utils import (
    create_pipeline as _create_pipeline_row,
    get_pipeline as _get_pipeline_row,
    list_pipelines as _list_pipeline_rows,
    update_pipeline as _update_pipeline_row,
    delete_pipeline as _delete_pipeline_row,
    create_pipeline_run as _create_run_row,
    get_pipeline_run as _get_run_row,
    list_pipeline_runs as _list_run_rows,
    get_pipeline_step_runs as _get_step_run_rows,
)
from app.database import db_session

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

_engine = PipelineEngine()
_scheduler = PipelineScheduler(_engine)


# 请求模型
class CreatePipelineRequest(BaseModel):
    name: str
    description: str = ""
    steps: List[dict]
    schedule: Optional[str] = None


class UpdatePipelineRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[List[dict]] = None
    schedule: Optional[str] = None
    status: Optional[str] = None


class RunPipelineRequest(BaseModel):
    trigger: str = "manual"
    parameters: dict = {}


class PipelineStepRequest(BaseModel):
    id: str
    name: str
    type: str
    config: dict = {}
    depends_on: List[str] = []
    resource_hint: dict = {}
    retry_policy: dict = {}
    timeout: int = 3600
    continue_on_failure: bool = False


# Pipeline 定义管理

@router.post("")
def create_pipeline(req: CreatePipelineRequest) -> dict:
    """创建 Pipeline"""
    # 验证步骤
    steps = []
    for s in req.steps:
        step = PipelineStep(**s)
        steps.append(step)

    # 检查依赖有效性
    step_ids = {s.id for s in steps}
    for step in steps:
        for dep in step.depends_on:
            if dep not in step_ids:
                raise HTTPException(400, f"步骤 {step.id} 依赖不存在的步骤: {dep}")

    # 检查类型合法性（PipelineStep 字符串转枚举时校验）
    for step in steps:
        try:
            step.type.value
        except ValueError as exc:
            raise HTTPException(400, f"步骤 {step.id} 类型非法: {exc}") from exc

    pipeline_id = f"pipe-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    now = datetime.now()
    steps_data = [s.__dict__ for s in steps]
    steps_data = [
        {**s, "type": (s["type"].value if hasattr(s["type"], "value") else s["type"])}
        for s in steps_data
    ]

    with db_session() as db:
        from app.db_utils import create_pipeline as _create_pipeline_row

        _create_pipeline_row(
            db,
            name=req.name,
            steps=steps_data,
            description=req.description,
            schedule=req.schedule,
            pipeline_id=pipeline_id,
        )

    pipeline = Pipeline(
        id=pipeline_id,
        name=req.name,
        description=req.description,
        steps=steps,
        schedule=req.schedule,
        created_at=now.timestamp(),
        updated_at=now.timestamp(),
    )

    # 如果有调度，注册调度器
    if req.schedule:
        _scheduler.schedule_pipeline(pipeline)

    return {"id": pipeline_id, "message": "Pipeline 创建成功"}


@router.get("")
def list_pipelines(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出 Pipeline"""
    with db_session() as db:
        pipelines, total = _list_pipeline_rows(db, limit, offset, status)
    
    return {
        "pipelines": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "steps_count": len(p.steps),
                "schedule": p.schedule,
                "status": p.status,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in pipelines
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{pipeline_id}")
def get_pipeline(pipeline_id: str) -> dict:
    """获取 Pipeline 详情（含 DAG 图）"""
    with db_session() as db:
        pipeline = _get_pipeline_row(db, pipeline_id)
    
    if not pipeline:
        raise HTTPException(404, "Pipeline 不存在")
    
    # DB 行的 steps 是 JSON 列表（dict）；统一转 PipelineStep 对象后再序列化
    try:
        step_objects = [PipelineStep(**s) for s in (pipeline.steps or [])]
    except (TypeError, ValueError) as exc:
        raise HTTPException(500, f"Pipeline 步骤数据损坏: {exc}") from exc

    # 构建 DAG 图数据
    nodes = []
    edges = []
    for step in step_objects:
        nodes.append({
            "id": step.id,
            "label": step.name,
            "type": step.type.value if hasattr(step.type, "value") else str(step.type),
            "config": step.config,
        })
        for dep in step.depends_on:
            edges.append({"source": dep, "target": step.id})
    
    return {
        "id": pipeline.id,
        "name": pipeline.name,
        "description": pipeline.description,
        "steps": [
            {
                "id": s.id,
                "name": s.name,
                "type": s.type.value if hasattr(s.type, "value") else str(s.type),
                "config": s.config,
                "depends_on": s.depends_on,
                "resource_hint": s.resource_hint,
                "retry_policy": s.retry_policy,
                "timeout": s.timeout,
                "continue_on_failure": s.continue_on_failure,
            }
            for s in step_objects
        ],
        "dag": {"nodes": nodes, "edges": edges},
        "schedule": pipeline.schedule,
        "status": pipeline.status,
        "created_at": pipeline.created_at,
        "updated_at": pipeline.updated_at,
    }


@router.put("/{pipeline_id}")
def update_pipeline(pipeline_id: str, req: UpdatePipelineRequest) -> dict:
    """更新 Pipeline"""
    updates = {}
    if req.name is not None:
        updates["name"] = req.name
    if req.description is not None:
        updates["description"] = req.description
    if req.steps is not None:
        steps = []
        for s in req.steps:
            step = PipelineStep(**s)
            steps.append(step)
        # 验证依赖
        step_ids = {s.id for s in steps}
        for step in steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    raise HTTPException(400, f"步骤 {step.id} 依赖不存在的步骤: {dep}")
        updates["steps"] = [s.__dict__ for s in steps]
    if req.schedule is not None:
        updates["schedule"] = req.schedule
    if req.status is not None:
        updates["status"] = req.status
    
    with db_session() as db:
        pipeline = _update_pipeline_row(db, pipeline_id, **updates)
    
    if not pipeline:
        raise HTTPException(404, "Pipeline 不存在")
    
    # 更新调度
    if req.schedule is not None:
        if req.schedule:
            _scheduler.schedule_pipeline(pipeline)
        else:
            _scheduler.unschedule_pipeline(pipeline_id)
    
    return {"message": "Pipeline 更新成功"}


@router.delete("/{pipeline_id}")
def delete_pipeline(pipeline_id: str) -> dict:
    """删除 Pipeline"""
    _scheduler.unschedule_pipeline(pipeline_id)
    
    with db_session() as db:
        success = _delete_pipeline_row(db, pipeline_id)
    
    if not success:
        raise HTTPException(404, "Pipeline 不存在")
    return {"ok": True}


# Pipeline 执行

@router.post("/{pipeline_id}/run")
def run_pipeline(pipeline_id: str, req: RunPipelineRequest) -> dict:
    """手动触发 Pipeline 执行"""
    with db_session() as db:
        pipeline = _get_pipeline_row(db, pipeline_id)
    
    if not pipeline:
        raise HTTPException(404, "Pipeline 不存在")
    
    if pipeline.status != "active":
        raise HTTPException(409, "Pipeline 未激活，无法执行")
    
    trigger = TriggerType(req.trigger) if req.trigger in [t.value for t in TriggerType] else TriggerType.MANUAL
    
    run = _engine.execute(pipeline, trigger, req.parameters)
    
    return {
        "run_id": run.id,
        "status": run.status.value,
        "message": "Pipeline 执行已启动",
    }


@router.get("/{pipeline_id}/runs")
def list_pipeline_runs(
    pipeline_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出 Pipeline 执行历史"""
    with db_session() as db:
        runs, total = _list_run_rows(db, pipeline_id, limit, offset)
    
    return {
        "runs": [
            {
                "id": r.id,
                "trigger": r.trigger.value,
                "status": r.status.value,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "error": r.error,
            }
            for r in runs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{pipeline_id}/runs/{run_id}")
def get_pipeline_run(pipeline_id: str, run_id: str) -> dict:
    """获取单次执行详情"""
    with db_session() as db:
        run = _get_run_row(db, run_id)
    
    if not run or run.pipeline_id != pipeline_id:
        raise HTTPException(404, "执行记录不存在")
    
    with db_session() as db:
        step_runs = _get_step_run_rows(db, run_id)
    
    return {
        "id": run.id,
        "pipeline_id": run.pipeline_id,
        "trigger": run.trigger.value,
        "status": run.status.value,
        "step_states": {k: v.value for k, v in run.step_states.items()},
        "artifact_map": run.artifact_map,
        "error": run.error,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "current_level": run.current_level,
        "steps": [
            {
                "id": sr.id,
                "step_id": sr.step_id,
                "step_name": sr.step_name,
                "step_type": sr.step_type.value,
                "status": sr.status.value,
                "config": sr.config,
                "input_artifacts": sr.input_artifacts,
                "output_artifacts": sr.output_artifacts,
                "error": sr.error,
                "retry_count": sr.retry_count,
                "started_at": sr.started_at,
                "finished_at": sr.finished_at,
                "worker_id": sr.worker_id,
                "logs": sr.logs,
            }
            for sr in step_runs
        ],
    }


@router.post("/{pipeline_id}/runs/{run_id}/cancel")
def cancel_pipeline_run(pipeline_id: str, run_id: str) -> dict:
    """取消 Pipeline 执行"""
    success = _engine.cancel_run(run_id)
    if not success:
        raise HTTPException(404, "执行记录不存在或已完成")
    return {"ok": True, "message": "取消请求已发送"}


# 导入依赖
import time
import uuid