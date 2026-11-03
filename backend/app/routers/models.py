"""模型注册表 API：模型列表、详情、版本管理、部署"""
from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import app.config as config
from app.registry.model_registry import ModelRegistry, ModelVersionManager
from app.storage import get_store

router = APIRouter(prefix="/api/models", tags=["models"])

_registry = ModelRegistry()
_version_manager = ModelVersionManager(registry=_registry)


# 请求模型
class RegisterModelRequest(BaseModel):
    name: str
    version: str
    task: str
    framework: str
    metrics: dict
    artifacts: dict  # {best: "s3://...", preprocessor: "s3://..."}
    tags: List[str] = []
    description: str = ""
    source_job_id: Optional[str] = None


class DeployModelRequest(BaseModel):
    model_version: str
    endpoint_name: Optional[str] = None
    replicas: int = 1
    config: dict = {}


class UpdateModelRequest(BaseModel):
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    status: Optional[str] = None  # ready/archived


@router.get("")
def list_models(
    task: Optional[str] = None,
    framework: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出注册模型"""
    models, total = _registry.list_models(limit, offset, task, framework, status, search)
    return {
        "models": [
            {
                "id": m.id,
                "name": m.name,
                "version": m.version,
                "source_job_id": m.source_job_id,
                "task": m.task,
                "framework": m.framework,
                "metrics": m.metrics,
                "tags": m.tags,
                "status": m.status,
                "deployed_endpoint": m.deployed_endpoint,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
            }
            for m in models
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# 部署管理（全局路由必须注册在 /{model_id} 之前，避免被参数路由遮蔽）
# ---------------------------------------------------------------------------

@router.get("/deployments")
def list_all_deployments(
    model_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出全部部署记录（可按模型和状态过滤），供部署管理页浏览。"""
    deployments = _registry.list_deployments(model_id)
    if status:
        deployments = [d for d in deployments if d.status == status]
    total = len(deployments)
    deployments = deployments[offset:offset + limit]
    # 附带模型名称，避免页面逐条查询
    model_name_map: dict = {}
    for d in deployments:
        if d.model_id not in model_name_map:
            model = _registry.get_model(d.model_id)
            model_name_map[d.model_id] = model.name if model else d.model_id
    return {
        "deployments": [
            {
                "id": d.id,
                "model_id": d.model_id,
                "model_name": model_name_map.get(d.model_id, d.model_id),
                "model_version": d.model_version,
                "endpoint_name": d.endpoint_name,
                "endpoint_url": d.endpoint_url,
                "status": d.status,
                "config": d.config,
                "worker_ids": d.worker_ids,
                "created_at": d.created_at,
            }
            for d in deployments
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.delete("/deployments/{deployment_id}")
def stop_deployment(deployment_id: str) -> dict:
    """停止（终止）一个部署记录；不删除历史，便于追溯。"""
    deployment = _registry.update_deployment(deployment_id, status="stopped")
    if not deployment:
        raise HTTPException(404, "部署记录不存在")
    return {"ok": True, "deployment_id": deployment_id, "status": deployment.status}


@router.get("/{model_id}")
def get_model(model_id: str) -> dict:
    """获取模型详情"""
    model = _registry.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    
    # 获取部署信息
    deployments = _registry.list_deployments(model_id)
    
    return {
        "id": model.id,
        "name": model.name,
        "version": model.version,
        "source_job_id": model.source_job_id,
        "task": model.task,
        "framework": model.framework,
        "metrics": model.metrics,
        "artifacts": model.artifacts,
        "tags": model.tags,
        "description": model.description,
        "status": model.status,
        "deployed_endpoint": model.deployed_endpoint,
        "deployment_config": model.deployment_config,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
        "deployments": [
            {
                "id": d.id,
                "endpoint_name": d.endpoint_name,
                "endpoint_url": d.endpoint_url,
                "status": d.status,
                "config": d.config,
                "worker_ids": d.worker_ids,
                "created_at": d.created_at,
            }
            for d in deployments
        ],
    }


@router.get("/{model_id}/versions")
def get_model_versions(model_id: str) -> dict:
    """获取模型所有版本"""
    model = _registry.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    
    versions = _version_manager.get_versions(model.name)
    return {"name": model.name, "versions": versions, "latest": versions[0] if versions else None}


@router.get("/{model_id}/compare")
def compare_model_versions(model_id: str, version1: str, version2: str) -> dict:
    """对比两个版本"""
    model = _registry.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    
    comparison = _version_manager.compare_versions(model.name, version1, version2)
    return {"model_name": model.name, "version1": version1, "version2": version2, "comparison": comparison}


@router.post("")
def register_model(req: RegisterModelRequest, created_by: str = "") -> dict:
    """手动注册模型"""
    try:
        model = _registry.register_manual(
            name=req.name,
            version=req.version,
            task=req.task,
            framework=req.framework,
            metrics=req.metrics,
            artifacts=req.artifacts,
            tags=req.tags,
            description=req.description,
            source_job_id=req.source_job_id,
        )
        return {"id": model.id, "message": "模型注册成功"}
    except Exception as e:
        raise HTTPException(400, f"注册失败: {e}")


@router.put("/{model_id}")
def update_model(model_id: str, req: UpdateModelRequest) -> dict:
    """更新模型元数据"""
    updates = {}
    if req.tags is not None:
        updates["tags"] = req.tags
    if req.description is not None:
        updates["description"] = req.description
    if req.status is not None:
        updates["status"] = req.status
    
    model = _registry.update_model(model_id, **updates)
    if not model:
        raise HTTPException(404, "模型不存在")
    
    return {"message": "模型更新成功"}


@router.delete("/{model_id}")
def archive_model(model_id: str) -> dict:
    """归档模型"""
    success = _registry.archive_model(model_id)
    if not success:
        raise HTTPException(404, "模型不存在")
    return {"ok": True, "message": "模型已归档"}


@router.post("/{model_id}/deploy")
def deploy_model(model_id: str, req: DeployModelRequest) -> dict:
    """部署模型为推理服务"""
    model = _registry.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    
    if model.version != req.model_version:
        raise HTTPException(400, "版本不匹配")
    
    try:
        deployment = _registry.deploy(
            model_id=model_id,
            model_version=req.model_version,
            config={"replicas": req.replicas, "endpoint_name": req.endpoint_name, **req.config},
        )
        return {
            "deployment_id": deployment.id,
            "endpoint_url": deployment.endpoint_url,
            "status": deployment.status,
        }
    except Exception as e:
        raise HTTPException(500, f"部署失败: {e}")


@router.get("/{model_id}/deployments")
def list_deployments(model_id: str) -> dict:
    """获取模型部署列表"""
    model = _registry.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    
    deployments = _registry.list_deployments(model_id)
    return {
        "deployments": [
            {
                "id": d.id,
                "endpoint_name": d.endpoint_name,
                "endpoint_url": d.endpoint_url,
                "status": d.status,
                "config": d.config,
                "worker_ids": d.worker_ids,
                "created_at": d.created_at,
            }
            for d in deployments
        ]
    }


@router.get("/{model_id}/download")
def download_model(model_id: str, artifact: str = "best") -> dict:
    """获取模型下载链接"""
    model = _registry.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    
    artifact_path = model.artifacts.get(artifact)
    if not artifact_path:
        raise HTTPException(404, f"产物不存在: {artifact}")
    
    # 生成预签名 URL（如果是 S3）
    store = get_store()
    if hasattr(store, '_client'):  # S3ArtifactStore
        from minio import Minio
        try:
            url = store._client.presigned_get_object(
                store.bucket, artifact_path.replace(f"s3://{store.bucket}/", ""), 
                expires=3600
            )
            return {"download_url": url, "expires_in": 3600}
        except Exception:
            pass
    
    # 本地存储直接返回路径
    local_path = store.read_file(artifact_path, config.WORKER_SCRATCH_DIR / "serve" / model_id / artifact)
    return {"local_path": str(local_path), "note": "本地存储模式，路径为容器内路径"}