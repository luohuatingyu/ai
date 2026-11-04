"""Training package - 统一训练入口与核心组件"""
from __future__ import annotations

from app.training.core import (
    TrainingSpec,
    TrainingContext,
    TrainingSummary,
    DistributedContext,
    run_training,
)
from app.training.tasks import (
    ClassificationTask,
    RegressionTask,
    ClusteringTask,
    TimeSeriesTask,
    LLMTask,
)
from app.training.checkpoint import (
    save_checkpoint,
    load_checkpoint,
    find_latest_checkpoint_local,
    prune_local_checkpoints,
    load_best_checkpoint,
    migrate_checkpoint_across_devices,
    validate_checkpoint_compatibility,
)

# 兼容别名：与 app.storage.prune_checkpoints 语义一致（OSS 版本化 checkpoint 裁剪）
from app.storage import prune_checkpoints as _oss_prune_checkpoints

prune_checkpoints = _oss_prune_checkpoints

__all__ = [
    "TrainingSpec",
    "TrainingContext",
    "TrainingSummary",
    "DistributedContext",
    "run_training",
    "ClassificationTask",
    "RegressionTask",
    "ClusteringTask",
    "TimeSeriesTask",
    "LLMTask",
    "save_checkpoint",
    "load_checkpoint",
    "find_latest_checkpoint_local",
    "prune_local_checkpoints",
    "prune_checkpoints",
    "load_best_checkpoint",
    "migrate_checkpoint_across_devices",
    "validate_checkpoint_compatibility",
]