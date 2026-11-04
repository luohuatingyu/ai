"""模型注册表：训练产物自动注册、版本管理、部署"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import app.config as config
from app.storage import ArtifactStore, get_store
from app.db_utils import (
    register_model,
    get_registered_model,
    get_registered_model_by_name_version,
    list_registered_models,
    update_registered_model,
    delete_registered_model,
    create_model_deployment,
    get_model_deployment,
    list_model_deployments,
    update_model_deployment,
)
from app.database import db_session


@dataclass
class RegisteredModel:
    """注册模型"""
    id: str
    name: str
    version: str
    source_job_id: Optional[str]
    task: str
    framework: str
    metrics: Dict[str, float]
    artifacts: Dict[str, str]  # {best: "s3://...", preprocessor: "s3://...", summary: "s3://..."}
    tags: List[str]
    description: str
    status: str  # ready/archived/deployed
    deployed_endpoint: Optional[str]
    deployment_config: Optional[Dict]
    created_at: float
    updated_at: float


@dataclass
class ModelDeployment:
    """模型部署"""
    id: str
    model_id: str
    model_version: str
    endpoint_name: Optional[str]
    endpoint_url: Optional[str]
    status: str  # pending/running/stopped/failed
    config: Dict
    worker_ids: List[str]
    error: Optional[str]
    created_at: float
    updated_at: float
    stopped_at: Optional[float]


class ModelRegistry:
    """模型注册表核心"""
    
    def __init__(self, store: ArtifactStore = None, db = None):
        self.store = store or get_store()
        self.db = db
    
    def register_from_training(
        self,
        job_id: str,
        summary: Any,  # TrainingSummary
        name: str = None,
        tags: List[str] = None,
        description: str = "",
    ) -> RegisteredModel:
        """
        从训练任务自动注册模型
        """
        # 读取训练摘要
        if hasattr(summary, 'to_dict'):
            summary_dict = summary.to_dict()
        elif isinstance(summary, dict):
            summary_dict = summary
        else:
            # 从 OSS 读取
            summary_path = f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/summary.json"
            summary_text = self.store.read_text(summary_path)
            summary_dict = json.loads(summary_text) if summary_text else {}
        
        # 生成模型名称
        model_name = name or f"{summary_dict.get('task', 'model')}-{job_id}"
        
        # 生成版本号
        version = self._generate_version(model_name)
        
        # 提取关键指标
        metrics = {}
        best = summary_dict.get("best", {})
        if best:
            metrics = {k: v for k, v in best.items() if isinstance(v, (int, float))}
        
        # 产物路径
        artifacts = {
            "best": summary_dict.get("model_path", f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/best.pt"),
            "preprocessor": summary_dict.get("preprocessor_path", f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/preprocessor.json"),
            "summary": summary_dict.get("summary_path", f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/summary.json"),
        }
        
        # 注册到数据库
        with db_session() as db:
            model = register_model(
                db=db,
                name=model_name,
                version=version,
                source_job_id=job_id,
                task=summary_dict.get("task", "unknown"),
                framework=summary_dict.get("framework", "torch"),
                metrics=metrics,
                artifacts=artifacts,
                tags=tags or [],
                description=description,
            )
        
        # 复制产物到模型注册表命名空间
        self._copy_artifacts_to_registry(model.id, version, artifacts)
        
        return RegisteredModel(
            id=model.id,
            name=model.name,
            version=model.version,
            source_job_id=model.source_job_id,
            task=model.task,
            framework=model.framework,
            metrics=model.metrics or {},
            artifacts=model.artifacts or {},
            tags=model.tags or [],
            description=model.description or "",
            status=model.status,
            deployed_endpoint=model.deployed_endpoint,
            deployment_config=model.deployment_config,
            created_at=model.created_at.timestamp() if model.created_at else time.time(),
            updated_at=model.updated_at.timestamp() if model.updated_at else time.time(),
        )
    
    def _generate_version(self, model_name: str) -> str:
        """生成版本号"""
        scheme = config.MODEL_VERSION_SCHEME
        
        if scheme == "timestamp":
            return time.strftime("%Y%m%d-%H%M%S")
        elif scheme == "auto_increment":
            # 查找现有版本
            with db_session() as db:
                from app.models import RegisteredModel as DBModel
                existing = db.query(DBModel).filter(DBModel.name == model_name).all()
                if not existing:
                    return "1.0.0"
                # 简单递增
                versions = [m.version for m in existing]
                # 尝试解析语义化版本
                return self._increment_version(max(versions))
        else:  # semver
            with db_session() as db:
                from app.models import RegisteredModel as DBModel
                existing = db.query(DBModel).filter(DBModel.name == model_name).all()
                if not existing:
                    return "1.0.0"
                return self._increment_version(max(m.version for m in existing))
    
    def _increment_version(self, version: str) -> str:
        """语义化版本递增（补丁版本+1）"""
        try:
            parts = version.split(".")
            if len(parts) == 3:
                major, minor, patch = parts
                return f"{major}.{minor}.{int(patch) + 1}"
        except Exception:
            pass
        return f"{version}.1"
    
    def _copy_artifacts_to_registry(self, model_id: str, version: str, artifacts: Dict[str, str]):
        """将产物复制到模型注册表命名空间"""
        target_prefix = f"{config.MODEL_REGISTRY_OSS_PREFIX}{model_id}/v{version}/"
        
        for name, source_path in artifacts.items():
            target_path = f"{target_prefix}{name}"
            try:
                # 通过流式复制避免下载到本地
                data = self.store.read_bytes(source_path)
                if data:
                    self.store.write_bytes(target_path, data)
            except Exception as e:
                print(f"复制产物 {name} 失败: {e}")
    
    def register_manual(
        self,
        name: str,
        version: str,
        task: str,
        framework: str,
        metrics: Dict[str, float],
        artifacts: Dict[str, str],
        tags: List[str] = None,
        description: str = "",
        source_job_id: str = None,
    ) -> RegisteredModel:
        """手动注册模型"""
        with db_session() as db:
            model = register_model(
                db=db,
                name=name,
                version=version,
                source_job_id=source_job_id,
                task=task,
                framework=framework,
                metrics=metrics,
                artifacts=artifacts,
                tags=tags or [],
                description=description,
            )
        return self._to_registered_model(model)
    
    def get_model(self, model_id: str) -> Optional[RegisteredModel]:
        with db_session() as db:
            model = get_registered_model(db, model_id)
            return self._to_registered_model(model) if model else None
    
    def get_model_by_name_version(self, name: str, version: str) -> Optional[RegisteredModel]:
        with db_session() as db:
            model = get_registered_model_by_name_version(db, name, version)
            return self._to_registered_model(model) if model else None
    
    def list_models(
        self,
        limit: int = 50,
        offset: int = 0,
        task: str = None,
        framework: str = None,
        status: str = None,
        search: str = None,
    ) -> tuple[List[RegisteredModel], int]:
        with db_session() as db:
            models, total = list_registered_models(db, limit, offset, task, framework, status, search)
            return [self._to_registered_model(m) for m in models], total
    
    def update_model(self, model_id: str, **updates) -> Optional[RegisteredModel]:
        with db_session() as db:
            model = update_registered_model(db, model_id, **updates)
            return self._to_registered_model(model) if model else None
    
    def archive_model(self, model_id: str) -> bool:
        with db_session() as db:
            return delete_registered_model(db, model_id)
    
    def deploy(
        self,
        model_id: str,
        model_version: str,
        config: Dict = None,
        worker_ids: List[str] = None,
    ) -> ModelDeployment:
        """部署模型为推理服务"""
        with db_session() as db:
            deployment = create_model_deployment(
                db=db,
                model_id=model_id,
                model_version=model_version,
                endpoint_name=config.get("endpoint_name") if config else None,
                config=config or {},
                worker_ids=worker_ids or [],
            )
        
        # TODO: 实际启动推理 Worker、注册到服务发现
        # 这里仅创建记录
        return self._to_deployment(deployment)
    
    def get_deployment(self, deployment_id: str) -> Optional[ModelDeployment]:
        with db_session() as db:
            dep = get_model_deployment(db, deployment_id)
            return self._to_deployment(dep) if dep else None
    
    def list_deployments(self, model_id: str = None) -> List[ModelDeployment]:
        with db_session() as db:
            deployments, _ = list_model_deployments(db, model_id)
            return [self._to_deployment(d) for d in deployments]
    
    def update_deployment(self, deployment_id: str, **updates) -> Optional[ModelDeployment]:
        with db_session() as db:
            dep = update_model_deployment(db, deployment_id, **updates)
            return self._to_deployment(dep) if dep else None
    
    def _to_registered_model(self, model) -> RegisteredModel:
        return RegisteredModel(
            id=model.id,
            name=model.name,
            version=model.version,
            source_job_id=model.source_job_id,
            task=model.task,
            framework=model.framework,
            metrics=model.metrics or {},
            artifacts=model.artifacts or {},
            tags=model.tags or [],
            description=model.description or "",
            status=model.status,
            deployed_endpoint=model.deployed_endpoint,
            deployment_config=model.deployment_config,
            created_at=model.created_at.timestamp() if model.created_at else 0,
            updated_at=model.updated_at.timestamp() if model.updated_at else 0,
        )
    
    def _to_deployment(self, dep) -> ModelDeployment:
        return ModelDeployment(
            id=dep.id,
            model_id=dep.model_id,
            model_version=dep.model_version,
            endpoint_name=dep.endpoint_name,
            endpoint_url=dep.endpoint_url,
            status=dep.status,
            config=dep.config or {},
            worker_ids=dep.worker_ids or [],
            error=dep.error,
            created_at=dep.created_at.timestamp() if dep.created_at else 0,
            updated_at=dep.updated_at.timestamp() if dep.updated_at else 0,
            stopped_at=dep.stopped_at.timestamp() if dep.stopped_at else None,
        )


class ModelVersionManager:
    """模型版本管理器"""
    
    def __init__(self, registry: ModelRegistry):
        self.registry = registry
    
    def get_versions(self, model_name: str) -> List[str]:
        """获取模型的所有版本"""
        models, _ = self.registry.list_models(limit=1000, search=model_name)
        return sorted([m.version for m in models if m.name == model_name], reverse=True)
    
    def get_latest_version(self, model_name: str) -> Optional[str]:
        versions = self.get_versions(model_name)
        return versions[0] if versions else None
    
    def promote_version(self, model_name: str, version: str, target_stage: str = "production") -> bool:
        """推广版本到指定阶段"""
        model = self.registry.get_model_by_name_version(model_name, version)
        if model:
            self.registry.update_model(model.id, metadata={"stage": target_stage})
            return True
        return False
    
    def compare_versions(self, model_name: str, version1: str, version2: str) -> Dict:
        """对比两个版本的指标"""
        v1 = self.registry.get_model_by_name_version(model_name, version1)
        v2 = self.registry.get_model_by_name_version(model_name, version2)
        
        if not v1 or not v2:
            return {}
        
        all_keys = set(v1.metrics.keys()) | set(v2.metrics.keys())
        comparison = {}
        for key in all_keys:
            val1 = v1.metrics.get(key)
            val2 = v2.metrics.get(key)
            if val1 is not None and val2 is not None:
                diff = val2 - val1
                pct = (diff / abs(val1) * 100) if val1 != 0 else 0
                comparison[key] = {"v1": val1, "v2": val2, "diff": diff, "pct_change": pct}
        
        return comparison