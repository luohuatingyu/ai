"""Pipeline 核心数据结构"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import uuid


class StepType(Enum):
    """步骤类型"""
    DATASET_PULL = "dataset_pull"           # 数据集拉取（从 HF/本地/OSS）
    DATASET_PROCESS = "dataset_process"     # 数据预处理
    TRAINING = "training"                   # 模型训练
    MODEL_UPLOAD = "model_upload"           # 模型上传/注册
    MODEL_DEPLOY = "model_deploy"           # 模型部署
    CUSTOM_SCRIPT = "custom_script"         # 自定义脚本
    NOTEBOOK_RUN = "notebook_run"           # Notebook 执行
    INFERENCE = "inference"                 # 批量推理


class RunStatus(Enum):
    """运行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class TriggerType(Enum):
    """触发类型"""
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT = "event"           # 如：数据集更新、模型注册、Webhook
    API = "api"               # API 触发


@dataclass
class PipelineStep:
    """Pipeline 步骤定义"""
    id: str
    name: str
    type: StepType
    config: Dict[str, Any] = field(default_factory=dict)  # 步骤配置
    depends_on: List[str] = field(default_factory=list)   # 依赖的步骤 ID
    resource_hint: Dict[str, Any] = field(default_factory=dict)  # 计算资源提示
    retry_policy: Dict[str, Any] = field(default_factory=dict)   # 重试策略
    timeout: int = 3600  # 超时秒数
    continue_on_failure: bool = False  # 失败是否继续
    
    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = StepType(self.type)


@dataclass
class Pipeline:
    """Pipeline 定义"""
    id: str
    name: str
    description: str = ""
    steps: List[PipelineStep] = field(default_factory=list)
    schedule: Optional[str] = None  # cron 表达式
    tags: List[str] = field(default_factory=list)
    created_at: float = 0
    updated_at: float = 0
    status: str = "draft"  # draft/active/paused/archived
    
    def get_step(self, step_id: str) -> Optional[PipelineStep]:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None
    
    def get_execution_order(self) -> List[List[str]]:
        """获取拓扑排序执行层级（同层可并行）"""
        from collections import defaultdict, deque

        # in_degree[step_id] = 尚未完成的依赖数
        in_degree = {step.id: len(set(step.depends_on)) for step in self.steps}
        # dependents[dep_id] = 依赖 dep_id 的步骤列表
        dependents: Dict[str, List[str]] = defaultdict(list)
        for step in self.steps:
            for dep in set(step.depends_on):
                dependents[dep].append(step.id)

        # 初始入队：无依赖的步骤
        queue = deque([sid for sid, deg in in_degree.items() if deg == 0])

        levels: List[List[str]] = []
        while queue:
            current_level = list(queue)
            queue.clear()
            for node in current_level:
                for dependent in dependents[node]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)
            if current_level:
                levels.append(current_level)

        return levels


@dataclass
class PipelineRun:
    """Pipeline 执行实例"""
    id: str
    pipeline_id: str
    trigger: TriggerType = TriggerType.MANUAL
    status: RunStatus = RunStatus.PENDING
    step_states: Dict[str, RunStatus] = field(default_factory=dict)  # step_id -> status
    artifact_map: Dict[str, Dict[str, str]] = field(default_factory=dict)  # step_id -> {artifact_name: oss_path}
    error: Optional[str] = None
    started_at: float = 0
    finished_at: Optional[float] = None
    current_level: int = 0
    
    def __post_init__(self):
        if isinstance(self.trigger, str):
            self.trigger = TriggerType(self.trigger)
        if isinstance(self.status, str):
            self.status = RunStatus(self.status)


@dataclass
class PipelineStepRun:
    """步骤执行记录"""
    id: str
    run_id: str
    step_id: str
    step_name: str
    step_type: StepType
    status: RunStatus = RunStatus.PENDING
    config: Dict[str, Any] = field(default_factory=dict)
    input_artifacts: Dict[str, str] = field(default_factory=dict)   # 输入产物
    output_artifacts: Dict[str, str] = field(default_factory=dict)  # 输出产物
    error: Optional[str] = None
    retry_count: int = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    worker_id: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if isinstance(self.step_type, str):
            self.step_type = StepType(self.step_type)
        if isinstance(self.status, str):
            self.status = RunStatus(self.status)


@dataclass
class RetryPolicy:
    """重试策略"""
    max_retries: int = 3
    backoff_seconds: float = 5.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 300.0
    retry_on: List[str] = field(default_factory=lambda: ["timeout", "oom", "network", "unknown"])