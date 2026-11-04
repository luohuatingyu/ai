"""统一检查点管理：保存/加载/裁剪/跨设备迁移"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch

from app.storage import ArtifactStore, get_store, checkpoint_key, find_latest_checkpoint
from app.config import WORKER_CHECKPOINT_KEEP
from app.trainer import _to_cpu, _rng_snapshot, _rng_restore, _py


def save_checkpoint(
    path: Path,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Optional[torch.optim.lr_scheduler.LRScheduler],
    epoch: int,
    best_score: Optional[float],
    best_epoch: int,
    best_record: Dict,
    preprocessor: Dict,
    spec: Dict,
    rng_state: Dict = None,
    meta: Dict = None,
):
    """保存完整训练状态（模型+优化器+调度器+RNG+元数据）"""
    state = {
        "kind": "trainer_state",
        "epoch": epoch,
        "best_score": best_score,
        "best_epoch": best_epoch,
        "best_record": best_record or {},
        "state_dict": _to_cpu(model.state_dict()),
        "optimizer": _to_cpu(optimizer.state_dict()) if optimizer else None,
        "scheduler": _to_cpu(scheduler.state_dict()) if scheduler else None,
        "rng": rng_state or _rng_snapshot(),
        "preprocessor": preprocessor,
        "spec": spec,
        "meta": meta or {
            "torch_version": torch.__version__,
            "saved_device": str(next(model.parameters()).device),
            "saved_at": __import__("time").time(),
        },
    }
    
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    torch.save(state, tmp)
    tmp.replace(path)  # 原子写入


def load_checkpoint(path: Path, map_location: str = "cpu") -> Dict:
    """加载检查点，自动兼容 weights_only"""
    try:
        return torch.load(path, map_location=map_location, weights_only=True)
    except Exception:
        # sklearn/XGBoost 模型包含估计器对象，无法进入 weights_only 白名单
        state = torch.load(path, map_location=map_location, weights_only=False)
        if not isinstance(state, dict) or state.get("framework") not in ("sklearn", "xgboost"):
            raise ValueError(f"检查点文件损坏或格式不兼容: {path}")
        return state


def find_latest_checkpoint_local(checkpoint_dir: Path) -> Optional[Tuple[int, Path]]:
    """在本地目录查找最新的版本化 checkpoint"""
    import re
    ckpt_pattern = re.compile(r"ckpt-e(\d+)\.pt$")
    best: Optional[Tuple[int, Path]] = None
    
    for path in checkpoint_dir.glob("ckpt-e*.pt"):
        m = ckpt_pattern.search(path.name)
        if m:
            epoch = int(m.group(1))
            if best is None or epoch > best[0]:
                best = (epoch, path)
    
    return best


def prune_local_checkpoints(checkpoint_dir: Path, keep: int = WORKER_CHECKPOINT_KEEP):
    """只保留最近 keep 个本地 checkpoint"""
    import re
    ckpt_pattern = re.compile(r"ckpt-e(\d+)\.pt$")
    items = []
    
    for path in checkpoint_dir.glob("ckpt-e*.pt"):
        m = ckpt_pattern.search(path.name)
        if m:
            items.append((int(m.group(1)), path))
    
    items.sort()
    for _, path in items[:-keep] if keep > 0 else []:
        try:
            path.unlink()
        except OSError:
            pass


def sync_checkpoints_to_oss(
    store: ArtifactStore,
    job_id: str,
    checkpoint_dir: Path,
    keep: int = WORKER_CHECKPOINT_KEEP,
):
    """同步本地 checkpoint 到 OSS"""
    import re
    ckpt_pattern = re.compile(r"ckpt-e(\d+)\.pt$")
    
    # 上传所有本地 checkpoint
    for path in checkpoint_dir.glob("ckpt-e*.pt"):
        m = ckpt_pattern.search(path.name)
        if m:
            epoch = int(m.group(1))
            oss_key = checkpoint_key(job_id, epoch)
            try:
                store.write_file(oss_key, path)
            except Exception as e:
                print(f"Checkpoint {epoch} 同步到 OSS 失败: {e}")
    
    # 上传 best.pt 和 last.pt
    for name in ("best.pt", "last.pt"):
        local_path = checkpoint_dir / name
        if local_path.exists():
            try:
                store.write_file(f"jobs/{job_id}/{name}", local_path)
            except Exception:
                pass
    
    # OSS 端清理（保留最近 keep 个）
    prune_oss_checkpoints(store, job_id, keep)


def prune_oss_checkpoints(store: ArtifactStore, job_id: str, keep: int):
    """清理 OSS 上过期的 checkpoint"""
    from app.storage import prune_checkpoints
    prune_checkpoints(store, job_id, keep)


def load_best_checkpoint(
    store: ArtifactStore,
    job_id: str,
    checkpoint_dir: Path,
    map_location: str = "cpu",
) -> Optional[Dict]:
    """加载最佳 checkpoint（优先本地，回退 OSS）"""
    # 1. 尝试本地 best.pt
    local_best = checkpoint_dir / "best.pt"
    if local_best.exists():
        return load_checkpoint(local_best, map_location)
    
    # 2. 尝试 OSS best.pt
    try:
        oss_best = store.read_file(f"jobs/{job_id}/best.pt", checkpoint_dir / "best.pt")
        return load_checkpoint(oss_best, map_location)
    except Exception:
        pass
    
    # 3. 尝试最新的版本化 checkpoint
    latest_local = find_latest_checkpoint_local(checkpoint_dir)
    if latest_local:
        epoch, path = latest_local
        return load_checkpoint(path, map_location)
    
    # 4. 从 OSS 查找最新
    try:
        from app.storage import find_latest_checkpoint
        latest_oss = find_latest_checkpoint(store, job_id)
        if latest_oss:
            epoch, key = latest_oss
            local_path = store.read_file(key, checkpoint_dir / f"ckpt-e{epoch:04d}.pt")
            return load_checkpoint(local_path, map_location)
    except Exception:
        pass
    
    return None


def migrate_checkpoint_across_devices(
    state: Dict,
    target_device: torch.device,
) -> Dict:
    """
    跨设备迁移 checkpoint：
    - 模型参数移动到目标设备
    - 优化器状态移动到目标设备
    - RNG 状态适配（CUDA RNG 不同设备不可直接迁移，记录警告）
    """
    # 模型参数
    if "state_dict" in state:
        state["state_dict"] = {k: v.to(target_device) for k, v in state["state_dict"].items()}
    
    # 优化器状态
    if "optimizer" in state and state["optimizer"]:
        optimizer_state = state["optimizer"]
        if "state" in optimizer_state:
            for param_id, param_state in optimizer_state["state"].items():
                for key, value in param_state.items():
                    if torch.is_tensor(value):
                        param_state[key] = value.to(target_device)
    
    # 调度器状态
    if "scheduler" in state and state["scheduler"]:
        scheduler_state = state["scheduler"]
        for key, value in scheduler_state.items():
            if torch.is_tensor(value):
                scheduler_state[key] = value.to(target_device)
    
    # 更新元数据
    if "meta" not in state:
        state["meta"] = {}
    state["meta"]["migrated_to"] = str(target_device)
    state["meta"]["migrated_at"] = __import__("time").time()
    
    return state


def validate_checkpoint_compatibility(
    state: Dict,
    expected_spec: Dict,
) -> Tuple[bool, List[str]]:
    """验证 checkpoint 与当前任务规格兼容"""
    errors = []
    
    # 检查任务类型
    if state.get("spec", {}).get("task") != expected_spec.get("task"):
        errors.append(f"任务类型不匹配: checkpoint={state.get('spec', {}).get('task')}, expected={expected_spec.get('task')}")
    
    # 检查模型架构关键参数
    state_spec = state.get("spec", {})
    for key in ("hidden_size", "hidden_layers", "model_type"):
        if key in expected_spec.get("params", {}) and key in state_spec.get("params", {}):
            if expected_spec["params"][key] != state_spec["params"][key]:
                errors.append(f"关键超参 {key} 不匹配: checkpoint={state_spec['params'][key]}, expected={expected_spec['params'][key]}")
    
    # 检查预处理器兼容性
    if "preprocessor" in state and "preprocessor" in expected_spec:
        # 简单检查特征名是否一致
        state_features = state["preprocessor"].get("feature_names", [])
        expected_features = expected_spec.get("preprocessor", {}).get("feature_names", [])
        if state_features != expected_features:
            errors.append("预处理器特征列表不匹配，无法直接恢复")
    
    return len(errors) == 0, errors