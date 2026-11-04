"""步骤间产物传递：OSS key 传递、元数据传递、类型转换"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from pathlib import Path

import app.config as config
from app.storage import ArtifactStore, get_store


@dataclass
class ArtifactSpec:
    """产物规格"""
    name: str
    type: str  # file/directory/model/dataset/metrics/json
    path: str  # OSS 路径或本地路径
    metadata: Dict[str, Any] = field(default_factory=dict)
    required: bool = True


class ArtifactPassing:
    """产物传递管理器"""
    
    def __init__(self, store: ArtifactStore = None):
        self.store = store or get_store()
    
    def prepare_inputs(
        self, 
        step_config: Dict, 
        upstream_artifacts: Dict[str, ArtifactSpec],
        work_dir: Path
    ) -> Dict[str, Path]:
        """
        准备步骤输入：从 OSS 下载到本地工作目录
        
        Returns:
            {artifact_key: local_path}
        """
        inputs = {}
        
        # 解析步骤配置中的输入引用
        input_refs = step_config.get("inputs", {})
        for local_name, ref in input_refs.items():
            # ref 格式: "step_id.artifact_name" 或 "oss://path"
            if ref.startswith("oss://") or ref.startswith("s3://"):
                # 直接 OSS 路径
                local_path = work_dir / local_name
                self.store.read_file(ref.replace("oss://", "").replace("s3://", ""), local_path)
                inputs[local_name] = local_path
            elif "." in ref:
                # 步骤间引用
                step_id, artifact_name = ref.split(".", 1)
                if step_id in upstream_artifacts and artifact_name in upstream_artifacts[step_id]:
                    spec = upstream_artifacts[step_id][artifact_name]
                    local_path = work_dir / local_name
                    self.store.read_file(spec.path, local_path)
                    inputs[local_name] = local_path
            else:
                raise ValueError(f"无效的输入引用格式: {ref}")
        
        return inputs
    
    def collect_outputs(
        self,
        step_config: Dict,
        work_dir: Path,
        run_id: str,
        step_id: str
    ) -> Dict[str, ArtifactSpec]:
        """
        收集步骤输出：上传到 OSS，返回产物规格
        """
        outputs = {}
        output_refs = step_config.get("outputs", {})
        
        for artifact_name, spec in output_refs.items():
            local_path = work_dir / spec.get("local_path", artifact_name)
            
            if not local_path.exists():
                if spec.get("required", True):
                    raise FileNotFoundError(f"输出产物不存在: {local_path}")
                continue
            
            # 确定 OSS 路径
            oss_prefix = spec.get("oss_prefix", f"pipelines/{run_id}/{step_id}/")
            oss_path = f"{oss_prefix}{artifact_name}"
            
            # 上传
            if local_path.is_dir():
                # 目录：打包上传
                import tarfile
                import io
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
                    tar.add(local_path, arcname=artifact_name)
                buffer.seek(0)
                self.store.write_bytes(oss_path, buffer.read())
            else:
                self.store.write_file(oss_path, local_path)
            
            outputs[artifact_name] = ArtifactSpec(
                name=artifact_name,
                type=spec.get("type", "file"),
                path=oss_path,
                metadata=spec.get("metadata", {}),
            )
        
        return outputs
    
    def pass_artifacts(
        self,
        source_step_id: str,
        target_step_id: str,
        artifact_mapping: Dict[str, str],
        run_id: str
    ) -> Dict[str, ArtifactSpec]:
        """
        步骤间直接传递产物（不重新上传，只传递 OSS key）
        
        artifact_mapping: {target_name: source_artifact_name}
        """
        # 这里只是逻辑传递，实际 OSS 对象已存在
        # 返回映射关系供下游使用
        passed = {}
        for target_name, source_name in artifact_mapping.items():
            source_key = f"pipelines/{run_id}/{source_step_id}/{source_name}"
            if self.store.exists(source_key):
                passed[target_name] = ArtifactSpec(
                    name=target_name,
                    type="reference",
                    path=source_key,
                    metadata={"source_step": source_step_id, "source_artifact": source_name},
                )
        return passed


class ArtifactTransformer:
    """产物类型转换器：不同步骤间的格式适配"""
    
    # 类型转换器注册表
    TRANSFORMERS: Dict[tuple, Callable] = {}
    
    @classmethod
    def register(cls, from_type: str, to_type: str):
        def decorator(func):
            cls.TRANSFORMERS[(from_type, to_type)] = func
            return func
        return decorator
    
    @classmethod
    def transform(cls, artifact: ArtifactSpec, target_type: str) -> ArtifactSpec:
        """转换产物类型"""
        if artifact.type == target_type:
            return artifact
        
        key = (artifact.type, target_type)
        if key not in cls.TRANSFORMERS:
            raise ValueError(f"不支持的类型转换: {artifact.type} -> {target_type}")
        
        return cls.TRANSFORMERS[key](artifact)


# 内置转换器
@ArtifactTransformer.register("model", "deployment")
def model_to_deployment(artifact: ArtifactSpec) -> ArtifactSpec:
    """模型 -> 部署包"""
    return ArtifactSpec(
        name=artifact.name,
        type="deployment",
        path=artifact.path,
        metadata={**artifact.metadata, "source_model": artifact.path},
    )


@ArtifactTransformer.register("dataset", "training_input")
def dataset_to_training_input(artifact: ArtifactSpec) -> ArtifactSpec:
    """数据集 -> 训练输入"""
    return ArtifactSpec(
        name=artifact.name,
        type="training_input",
        path=artifact.path,
        metadata={**artifact.metadata, "format": "csv"},
    )


@ArtifactTransformer.register("directory", "model")
def directory_to_model(artifact: ArtifactSpec) -> ArtifactSpec:
    """目录 -> 模型（假设目录包含 best.pt）"""
    return ArtifactSpec(
        name=artifact.name,
        type="model",
        path=f"{artifact.path}/best.pt",
        metadata=artifact.metadata,
    )


class ArtifactLineage:
    """产物血统追踪"""
    
    def __init__(self, store: ArtifactStore = None):
        self.store = store or get_store()
        self.lineage: Dict[str, Dict] = {}  # artifact_key -> {produced_by, consumed_by, ...}
    
    def record_production(
        self, 
        artifact_key: str, 
        step_id: str, 
        run_id: str,
        metadata: Dict = None
    ):
        """记录产物生产"""
        self.lineage[artifact_key] = {
            "produced_by": {"step_id": step_id, "run_id": run_id},
            "consumed_by": [],
            "metadata": metadata or {},
        }
        # 持久化到 OSS
        self.store.write_json(f"lineage/{artifact_key}.json", self.lineage[artifact_key])
    
    def record_consumption(self, artifact_key: str, step_id: str, run_id: str):
        """记录产物消费"""
        if artifact_key in self.lineage:
            self.lineage[artifact_key]["consumed_by"].append({
                "step_id": step_id,
                "run_id": run_id,
            })
            self.store.write_json(f"lineage/{artifact_key}.json", self.lineage[artifact_key])
    
    def get_lineage(self, artifact_key: str) -> Optional[Dict]:
        """获取产物血统"""
        if artifact_key in self.lineage:
            return self.lineage[artifact_key]
        
        # 尝试从 OSS 读取
        try:
            return self.store.read_json(f"lineage/{artifact_key}.json")
        except Exception:
            return None
    
    def get_downstream(self, artifact_key: str) -> List[Dict]:
        """获取下游消费者"""
        lineage = self.get_lineage(artifact_key)
        return lineage.get("consumed_by", []) if lineage else []
    
    def get_upstream(self, artifact_key: str) -> Optional[Dict]:
        """获取上游生产者"""
        lineage = self.get_lineage(artifact_key)
        return lineage.get("produced_by") if lineage else None