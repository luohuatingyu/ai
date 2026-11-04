"""统一训练入口：所有训练任务（单机/分布式/Notebook/模板/Pipeline步骤）的唯一执行路径"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

import app.config as config
from app.device import backend_from_device, device_rng_state, restore_device_rng_state, seed_device, select_device
from app.storage import ArtifactStore, get_store, job_key, checkpoint_key, parse_checkpoint_epoch, find_latest_checkpoint
from app.performance import EpochPerformance, configure_torch_threads
from app.trainer import JobCancelled, JobPaused, _rng_snapshot, _rng_restore, _to_cpu, _py


LogFn = Callable[[str], None]


@dataclass
class TrainingSpec:
    """统一训练规格：描述训练任务的所有参数"""
    task: str                              # classification/regression/clustering/time_series/llm_*
    target: str = ""                       # 目标列名（分类/回归/时序）
    dataset_id: str = ""                   # 数据集 ID
    dataset_name: str = ""                 # 数据集名称
    params: Dict[str, Any] = field(default_factory=dict)  # 超参数
    resource_hint: Dict[str, Any] = field(default_factory=dict)  # 资源提示
    
    # 分布式训练相关
    distributed: bool = False              # 是否启用分布式
    parallel_strategy: Dict[str, Any] = field(default_factory=dict)  # 并行策略配置
    
    # 检查点恢复
    resume_from: Optional[str] = None      # 从哪个 checkpoint 恢复（OSS key 或 epoch 号）
    
    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


@dataclass
class DistributedContext:
    """分布式训练上下文"""
    rank: int = 0
    world_size: int = 1
    local_rank: int = 0
    backend: str = "nccl"
    strategy: Dict[str, Any] = field(default_factory=dict)
    process_group: Any = None
    pipeline_stages: List[List[int]] = field(default_factory=list)  # 每阶段的层索引
    dp_groups: List[List[int]] = field(default_factory=list)        # 数据并行组
    device_mesh: Any = None


@dataclass
class TrainingContext:
    """训练运行时上下文：包含执行所需的所有资源"""
    job_id: str
    job_dir: Path                          # 本地工作目录
    store: ArtifactStore                   # 产物存储
    dataset_path: Path                     # 已解析的数据集本地路径
    device: torch.device                   # 当前进程设备
    log_fn: LogFn = print
    distributed: Optional[DistributedContext] = None
    checkpoint_dir: Optional[Path] = None
    metrics_path: Optional[Path] = None
    performance_path: Optional[Path] = None
    cancel_event: Optional[threading.Event] = None
    pause_event: Optional[threading.Event] = None
    resume_state: Optional[Dict] = None
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    
    def __post_init__(self):
        if self.checkpoint_dir is None:
            self.checkpoint_dir = self.job_dir / "checkpoints"
        if self.metrics_path is None:
            self.metrics_path = self.job_dir / "metrics.jsonl"
        if self.performance_path is None:
            self.performance_path = self.job_dir / "performance.jsonl"
        if self.cancel_event is None:
            self.cancel_event = threading.Event()
        if self.pause_event is None:
            self.pause_event = threading.Event()
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class TrainingSummary:
    """训练结果摘要"""
    task: str
    target: Optional[str]
    device: str
    torch_version: str
    cuda_version: Optional[str]
    device_backend: str
    epochs_total: int
    epochs_done: int
    resumed_from: int
    best_epoch: int
    best: Dict[str, Any]
    final: Dict[str, Any]
    rows: Dict[str, int]
    features: int
    classes: Optional[List[str]]
    duration_sec: float
    model_path: str = ""                   # OSS 中的模型路径
    preprocessor_path: str = ""            # OSS 中的预处理器路径
    summary_path: str = ""                 # OSS 中的摘要路径
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "target": self.target,
            "device": self.device,
            "torch_version": self.torch_version,
            "cuda_version": self.cuda_version,
            "device_backend": self.device_backend,
            "epochs_total": self.epochs_total,
            "epochs_done": self.epochs_done,
            "resumed_from": self.resumed_from,
            "best_epoch": self.best_epoch,
            "best": self.best,
            "final": self.final,
            "rows": self.rows,
            "features": self.features,
            "classes": self.classes,
            "duration_sec": self.duration_sec,
            "model_path": self.model_path,
            "preprocessor_path": self.preprocessor_path,
            "summary_path": self.summary_path,
        }


# =============================================================================
# 任务基类与注册表
# =============================================================================

class BaseTask:
    """训练任务基类：各任务类型实现 run 方法"""
    
    task_name: str = "base"
    supported_models: List[str] = []
    
    def __init__(self, spec: TrainingSpec, context: TrainingContext):
        self.spec = spec
        self.context = context
        self.log = context.log_fn
        
    def prepare_data(self) -> tuple:
        """准备数据：返回 (train_loader, val_loader, preprocessor, metadata)"""
        raise NotImplementedError
    
    def build_model(self, preprocessor: Dict, metadata: Dict) -> nn.Module:
        """构建模型"""
        raise NotImplementedError
    
    def build_optimizer(self, model: nn.Module) -> torch.optim.Optimizer:
        """构建优化器"""
        raise NotImplementedError
    
    def build_scheduler(self, optimizer: torch.optim.Optimizer) -> Optional[torch.optim.lr_scheduler.LRScheduler]:
        """构建学习率调度器"""
        return None
    
    def train_step(self, model: nn.Module, batch, optimizer) -> Dict[str, float]:
        """单步训练：返回 loss dict"""
        raise NotImplementedError
    
    def validate(self, model: nn.Module, val_loader: DataLoader) -> Dict[str, float]:
        """验证：返回指标 dict"""
        raise NotImplementedError
    
    def save_model(self, model: nn.Module, preprocessor: Dict, path: Path, is_best: bool = False):
        """保存模型"""
        raise NotImplementedError
    
    def load_model(self, path: Path) -> tuple:
        """加载模型：返回 (model, preprocessor, metadata)"""
        raise NotImplementedError


# 任务注册表
_TASK_REGISTRY: Dict[str, type] = {}


def register_task(task_class: type) -> type:
    """注册任务类"""
    _TASK_REGISTRY[task_class.task_name] = task_class
    return task_class


def get_task_class(task_name: str) -> type:
    """获取任务类"""
    if task_name not in _TASK_REGISTRY:
        raise ValueError(f"未知任务类型: {task_name}，可用: {list(_TASK_REGISTRY.keys())}")
    return _TASK_REGISTRY[task_name]


def list_available_tasks() -> List[str]:
    return list(_TASK_REGISTRY.keys())


# =============================================================================
# 核心训练循环
# =============================================================================

def run_training(
    spec: TrainingSpec,
    context: TrainingContext,
    on_epoch_end: Optional[Callable[[Dict], None]] = None,
    on_checkpoint: Optional[Callable[[int], None]] = None,
) -> TrainingSummary:
    """
    统一训练执行流程：
    1. 初始化分布式环境
    2. 准备数据
    3. 构建模型/优化器/调度器
    4. 恢复 checkpoint（如果有）
    5. 训练循环
    6. 保存最终产物、上传到 OSS
    7. 返回 TrainingSummary
    """
    started_at = time.time()
    log = context.log_fn
    store = context.store
    job_id = context.job_id
    
    log(f"[{job_id}] 开始训练: task={spec.task}, target={spec.target}, device={context.device}")
    
    # 1. 初始化分布式环境
    if context.distributed and context.distributed.world_size > 1:
        _init_distributed(context.distributed, context.device)
        log(f"[{job_id}] 分布式初始化完成: rank={context.distributed.rank}, world_size={context.distributed.world_size}")
    
    # 2. 准备数据
    task_class = get_task_class(spec.task)
    task = task_class(spec, context)
    
    log(f"[{job_id}] 准备数据...")
    train_loader, val_loader, preprocessor, data_meta = task.prepare_data()
    
    # 3. 构建模型/优化器/调度器
    log(f"[{job_id}] 构建模型...")
    model = task.build_model(preprocessor, data_meta).to(context.device)
    optimizer = task.build_optimizer(model)
    scheduler = task.build_scheduler(optimizer)
    
    # 分布式模型包装
    if context.distributed and context.distributed.world_size > 1:
        model = _wrap_model_for_distributed(model, context.distributed)
    
    # 4. 恢复 checkpoint
    start_epoch = 0
    best_score = None
    best_epoch = 0
    best_record = None
    
    if context.resume_state:
        log(f"[{job_id}] 恢复 checkpoint...")
        start_epoch, best_score, best_epoch, best_record = _load_trainer_state(
            context.resume_state, model, optimizer, scheduler, log
        )
    
    # 5. 训练循环
    epochs = int(spec.params.get("epochs", 20))
    log(f"[{job_id}] 开始训练循环: epochs={epochs}, start_epoch={start_epoch}")
    
    metrics_path = context.metrics_path
    performance = EpochPerformance(start_epoch)
    
    for epoch in range(start_epoch + 1, epochs + 1):
        # 检查取消/暂停
        if context.cancel_event.is_set():
            raise JobCancelled()
        if context.pause_event.is_set():
            _save_checkpoint(epoch - 1, model, optimizer, scheduler, preprocessor, spec, context, best_score, best_epoch, best_record)
            raise JobPaused(f"训练已在第 {epoch - 1} 个 epoch 后暂停")
        
        performance = EpochPerformance(epoch)
        
        # 训练一个 epoch
        model.train()
        train_metrics = {"train_loss": 0.0}
        num_batches = 0
        
        for batch in train_loader:
            if context.cancel_event.is_set():
                raise JobCancelled()
            
            batch_metrics = task.train_step(model, batch, optimizer)
            for k, v in batch_metrics.items():
                train_metrics[k] = train_metrics.get(k, 0) + v
            num_batches += 1
        
        # 平均训练指标
        for k in train_metrics:
            train_metrics[k] /= max(num_batches, 1)
        
        # 验证
        val_metrics = {}
        if val_loader:
            model.eval()
            val_metrics = task.validate(model, val_loader)
        
        # 合并指标
        epoch_metrics = {"epoch": epoch, **train_metrics, **val_metrics}
        
        # 记录指标
        _append_jsonl(metrics_path, epoch_metrics)
        
        # 更新最佳模型
        score = _compute_score(val_metrics, spec.task)
        is_best = best_score is None or score > best_score
        if is_best:
            best_score = score
            best_epoch = epoch
            best_record = epoch_metrics.copy()
        
        # 保存 checkpoint
        _save_checkpoint(epoch, model, optimizer, scheduler, preprocessor, spec, context, best_score, best_epoch, best_record)
        
        # 回调
        if on_epoch_end:
            on_epoch_end(epoch_metrics)
        if on_checkpoint:
            on_checkpoint(epoch)
        
        # 学习率调度
        if scheduler:
            scheduler.step()
        
        performance.write(context.job_dir, len(train_loader.dataset) if hasattr(train_loader, 'dataset') else 0, 1)
        
        log(f"[{job_id}] Epoch {epoch}/{epochs}: {epoch_metrics}")
    
    # 6. 保存最终产物
    duration = time.time() - started_at
    summary = _finalize_training(
        job_id, spec, context, model, preprocessor, data_meta,
        best_epoch, best_record, duration, log
    )
    
    log(f"[{job_id}] 训练完成: duration={duration:.1f}s, best_epoch={best_epoch}")
    return summary


# =============================================================================
# 内部辅助函数
# =============================================================================

def _init_distributed(dist_ctx: DistributedContext, device: torch.device):
    """初始化分布式环境"""
    if not torch.distributed.is_initialized():
        torch.distributed.init_process_group(
            backend=dist_ctx.backend,
            init_method=config.DISTRIBUTED_INIT_METHOD,
            world_size=dist_ctx.world_size,
            rank=dist_ctx.rank,
        )
    dist_ctx.process_group = torch.distributed.group.WORLD


def _wrap_model_for_distributed(model: nn.Module, dist_ctx: DistributedContext) -> nn.Module:
    """为分布式训练包装模型（DDP/FSDP/流水线并行）"""
    strategy = dist_ctx.strategy.get("mode", "dp")
    
    if strategy == "dp" or strategy == "ddp":
        from torch.nn.parallel import DistributedDataParallel as DDP
        return DDP(model, device_ids=[dist_ctx.local_rank] if dist_ctx.local_rank >= 0 else None)
    elif strategy == "fsdp":
        try:
            from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
            return FSDP(model)
        except ImportError:
            raise RuntimeError("FSDP 需要 PyTorch 1.11+")
    elif strategy == "pipeline":
        # 流水线并行由专门的 coordinator 处理
        return model
    return model


def _compute_score(metrics: Dict[str, float], task: str) -> float:
    """计算用于模型选择的分数（越大越好）"""
    if task == "classification":
        return metrics.get("val_accuracy", metrics.get("val_acc", -metrics.get("val_loss", 0)))
    elif task == "regression":
        return -metrics.get("val_loss", metrics.get("val_mse", 0))
    elif task == "clustering":
        return metrics.get("val_silhouette", -metrics.get("val_inertia", 0))
    return -metrics.get("val_loss", 0)


def _save_checkpoint(
    epoch: int,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Optional[torch.optim.lr_scheduler.LRScheduler],
    preprocessor: Dict,
    spec: TrainingSpec,
    context: TrainingContext,
    best_score: float,
    best_epoch: int,
    best_record: Dict,
):
    """保存完整训练状态（模型+优化器+调度器+RNG+元数据）"""
    state = {
        "kind": "trainer_state",
        "task": spec.task,
        "epoch": epoch,
        "best_score": best_score,
        "best_epoch": best_epoch,
        "best_record": best_record or {},
        "state_dict": _to_cpu(model.state_dict()),
        "optimizer": _to_cpu(optimizer.state_dict()),
        "scheduler": _to_cpu(scheduler.state_dict()) if scheduler else None,
        "rng": _rng_snapshot(),
        "preprocessor": preprocessor,
        "spec": {
            "task": spec.task,
            "target": spec.target,
            "params": spec.params,
            "resource_hint": spec.resource_hint,
        },
        "meta": {
            "torch_version": torch.__version__,
            "saved_device": str(context.device),
            "saved_at": time.time(),
        },
    }
    
    ckpt_path = context.checkpoint_dir / f"ckpt-e{epoch:04d}.pt"
    torch.save(state, ckpt_path)
    
    # 同步到 OSS
    try:
        context.store.write_file(checkpoint_key(context.job_id, epoch), ckpt_path)
    except Exception as e:
        context.log_fn(f"[{context.job_id}] checkpoint 同步到 OSS 失败: {e}")
    
    # 清理旧 checkpoint
    from app.storage import prune_checkpoints
    prune_checkpoints(context.store, context.job_id, config.WORKER_CHECKPOINT_KEEP)


def _load_trainer_state(
    state: Dict,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Optional[torch.optim.lr_scheduler.LRScheduler],
    log: LogFn,
) -> tuple:
    """加载训练状态"""
    model.load_state_dict(state["state_dict"])
    optimizer.load_state_dict(state["optimizer"])
    if scheduler and state.get("scheduler"):
        scheduler.load_state_dict(state["scheduler"])
    _rng_restore(state.get("rng", {}), log)
    
    epoch = state.get("epoch", 0)
    best_score = state.get("best_score")
    best_epoch = state.get("best_epoch", 0)
    best_record = state.get("best_record")
    
    return epoch, best_score, best_epoch, best_record


def _finalize_training(
    job_id: str,
    spec: TrainingSpec,
    context: TrainingContext,
    model: nn.Module,
    preprocessor: Dict,
    data_meta: Dict,
    best_epoch: int,
    best_record: Dict,
    duration: float,
    log: LogFn,
) -> TrainingSummary:
    """训练结束：保存最佳模型、预处理器、摘要到 OSS"""
    store = context.store
    
    # 保存最佳模型
    model_path = f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/best.pt"
    _save_model_artifact(store, model_path, model, preprocessor, spec, best_record, "best")
    
    # 保存预处理器
    preprocessor_path = f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/preprocessor.json"
    store.write_json(preprocessor_path, _py(preprocessor))
    
    # 保存摘要
    summary_path = f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/summary.json"
    
    summary = TrainingSummary(
        task=spec.task,
        target=spec.target or None,
        device=str(context.device),
        torch_version=torch.__version__,
        cuda_version=torch.version.cuda,
        device_backend=backend_from_device(context.device),
        epochs_total=int(spec.params.get("epochs", 20)),
        epochs_done=best_epoch,
        resumed_from=context.resume_state.get("epoch", 0) if context.resume_state else 0,
        best_epoch=best_epoch,
        best=best_record or {},
        final={},
        rows=data_meta.get("rows", {"total": 0}),
        features=data_meta.get("features", 0),
        classes=data_meta.get("classes"),
        duration_sec=round(duration, 1),
        model_path=model_path,
        preprocessor_path=preprocessor_path,
        summary_path=summary_path,
    )
    
    store.write_json(summary_path, _py(summary.to_dict()))
    
    return summary


def _save_model_artifact(
    store: ArtifactStore,
    path: str,
    model: nn.Module,
    preprocessor: Dict,
    spec: TrainingSpec,
    record: Dict,
    kind: str,
):
    """保存模型产物到 OSS"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as tmp:
        torch.save({
            "state_dict": _to_cpu(model.state_dict()),
            "preprocessor": preprocessor,
            "task": spec.task,
            "target": spec.target,
            "params": spec.params,
            "kind": kind,
            "best_record": record,
        }, tmp.name)
        store.write_file(path, Path(tmp.name))
    os.unlink(tmp.name)


def _append_jsonl(path: Path, record: Dict):
    """追加 JSONL 记录"""
    import json
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")