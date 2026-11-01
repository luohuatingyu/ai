"""聚类任务实现"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import silhouette_score
from torch.utils.data import DataLoader, TensorDataset

from app.training.core import BaseTask, register_task, TrainingSpec, TrainingContext
from app.trainer import _prepare_features, _initialize_kmeans, _assign_kmeans, _KMeansCenters, _save_checkpoint
from app.predictor import PICKLE_FORMAT
from app.device import select_device


@register_task
class ClusteringTask(BaseTask):
    task_name = "clustering"
    supported_models = ["kmeans"]
    
    def prepare_data(self) -> tuple:
        spec = self.spec
        context = self.context
        log = self.log
        
        dataset_path = context.dataset_path
        params = spec.params
        
        clusters = int(params.get("n_clusters", 4))
        seed = int(params.get("seed", 42))
        max_iter = int(params.get("epochs", 100))
        
        if clusters < 2 or clusters > 64:
            raise ValueError("聚类数必须在 2 到 64 之间")
        if max_iter < 1 or max_iter > 500:
            raise ValueError("KMeans 最大迭代次数必须在 1 到 500 之间")
        
        df = pd.read_csv(dataset_path)
        if len(df) < 2:
            raise ValueError("KMeans 聚类至少需要 2 行数据")
        
        X_df, feat_meta = _prepare_features(df, None)
        if X_df.empty or X_df.shape[1] == 0:
            raise ValueError("数据集没有可用于聚类的特征列")
        
        feature_names = [str(c) for c in X_df.columns]
        raw = X_df.to_numpy(dtype=np.float32, copy=True)
        
        unique_rows = np.unique(raw, axis=0).shape[0]
        if clusters >= len(raw):
            raise ValueError(f"聚类数必须小于样本数（{len(raw)}）")
        if clusters > unique_rows:
            raise ValueError(f"聚类数 {clusters} 超过不同特征样本数 {unique_rows}")
        
        mean = raw.mean(axis=0).astype(np.float32)
        std = raw.std(axis=0).astype(np.float32)
        std[std < 1e-8] = 1.0
        np.subtract(raw, mean, out=raw)
        np.divide(raw, std, out=raw)
        values = raw
        n_features = values.shape[1]
        
        log(f"KMeans 聚类: 样本 {len(values)} 条，特征 {n_features} 个，簇数 {clusters}")
        
        # 聚类使用全量数据，不分 train/val
        ds = TensorDataset(torch.from_numpy(values), torch.zeros(len(values), dtype=torch.long))
        batch_size = int(params.get("batch_size", 1024))
        loader = DataLoader(ds, batch_size=batch_size, shuffle=False)
        
        preprocessor = {
            "feature_names": feature_names,
            "num_cols": feat_meta["num_cols"],
            "cat_cols": feat_meta["cat_cols"],
            "num_medians": feat_meta["num_medians"],
            "cat_modes": feat_meta["cat_modes"],
            "mean": mean.tolist(),
            "std": std.tolist(),
            "classes": None,
            "target": None,
            "task": "clustering",
        }
        
        metadata = {
            "rows": {"total": len(values)},
            "features": n_features,
            "classes": None,
            "n_clusters": clusters,
        }
        
        return loader, None, preprocessor, metadata
    
    def build_model(self, preprocessor: Dict, metadata: Dict) -> torch.nn.Module:
        spec = self.spec
        params = spec.params
        clusters = int(params.get("n_clusters", 4))
        seed = int(params.get("seed", 42))
        
        n_features = metadata["features"]
        centers = _initialize_kmeans(
            np.zeros((1, n_features), dtype=np.float32),  # 占位，实际在 train_step 中初始化
            clusters, seed
        )
        return _KMeansCenters(centers)
    
    def build_optimizer(self, model: torch.nn.Module) -> torch.optim.Optimizer:
        return torch.optim.SGD(model.parameters(), lr=0.0)
    
    def train_step(self, model: torch.nn.Module, batch, optimizer) -> Dict[str, float]:
        # KMeans 训练逻辑在 run_training 中处理（迭代式）
        # 这里返回占位
        return {"train_loss": 0.0}
    
    def validate(self, model: torch.nn.Module, val_loader: DataLoader) -> Dict[str, float]:
        return {"val_inertia": 0.0, "val_silhouette": None}
    
    def save_model(self, model: torch.nn.Module, preprocessor: Dict, path: Path, is_best: bool = False):
        _save_checkpoint(path, model, {"model_type": "kmeans", "n_clusters": preprocessor.get("n_clusters", 4)},
                       preprocessor, "clustering", kind="best" if is_best else "last", record={})
    
    def load_model(self, path: Path) -> tuple:
        import torch
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        return checkpoint.get("state_dict"), checkpoint.get("preprocessor", {}), {}


# 重写 run_training 以支持 KMeans 迭代式训练
from app.training.core import run_training as base_run_training
import threading
import time
from pathlib import Path


def run_kmeans_training(
    spec: TrainingSpec,
    context: TrainingContext,
    on_epoch_end: callable = None,
    on_checkpoint: callable = None,
):
    """KMeans 专用训练循环（迭代式）"""
    from app.trainer import _append_jsonl, _py, _rng_snapshot, _rng_restore, _to_cpu, save_trainer_state
    from app.storage import checkpoint_key, prune_checkpoints
    import app.config as config
    
    log = context.log_fn
    store = context.store
    job_id = context.job_id
    started_at = time.time()
    
    params = spec.params
    clusters = int(params.get("n_clusters", 4))
    max_iter = int(params.get("epochs", 100))
    seed = int(params.get("seed", 42))
    
    # 获取数据
    train_loader, _, preprocessor, metadata = ClusteringTask(spec, context).prepare_data()
    values = train_loader.dataset.tensors[0].numpy()
    
    device = torch.device("cpu")
    torch.manual_seed(seed)
    np.random.seed(seed)
    
    # 初始化中心
    centers = _initialize_kmeans(values, clusters, seed)
    model = _KMeansCenters(centers)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.0)
    
    # 恢复状态
    start_epoch = 0
    best_score = None
    best_epoch = 0
    best_record = None
    
    if context.resume_state:
        _rng_restore(context.resume_state.get("rng", {}), log)
        centers = context.resume_state["state_dict"]["centers"].cpu().numpy().astype(np.float32)
        model = _KMeansCenters(centers)
        model.load_state_dict(context.resume_state["state_dict"])
        start_epoch = int(context.resume_state.get("epoch", 0))
        best_score = context.resume_state.get("best_score")
        best_epoch = int(context.resume_state.get("best_epoch", 0))
        best_record = context.resume_state.get("best_record")
    
    metrics_path = context.metrics_path
    previous_labels = None
    
    if start_epoch > 0:
        previous_labels, _ = _assign_kmeans(values, centers)
    
    for iteration in range(start_epoch + 1, max_iter + 1):
        if context.cancel_event.is_set():
            raise Exception("JobCancelled")
        if context.pause_event.is_set():
            # 保存 checkpoint
            save_trainer_state(
                context.checkpoint_dir / f"ckpt-e{iteration-1:04d}.pt",
                model=model, optimizer=optimizer,
                arch={"model_type": "kmeans", "in_dim": metadata["features"], "n_clusters": clusters},
                preprocessor=preprocessor, task="clustering",
                params={"epochs": max_iter, "n_clusters": clusters, "seed": seed},
                epoch=iteration-1, best_score=best_score, best_epoch=best_epoch,
                best_record=best_record, device=device,
            )
            raise Exception("JobPaused")
        
        current = model.centers.detach().cpu().numpy().astype(np.float32)
        labels, min_distances = _assign_kmeans(values, current)
        updated = current.copy()
        
        for cluster_index in range(clusters):
            members = values[labels == cluster_index]
            if len(members):
                updated[cluster_index] = members.mean(axis=0)
            else:
                candidates = np.argsort(min_distances)[::-1]
                fallback = next((int(idx) for idx in candidates if int(idx) not in set()), int(candidates[0]))
                updated[cluster_index] = values[fallback]
        
        final_labels, updated_min_distances = _assign_kmeans(values, updated)
        inertia = float(updated_min_distances.sum())
        shift = float(np.sqrt(np.sum((updated - current) ** 2)))
        
        with torch.no_grad():
            model.centers.copy_(torch.as_tensor(updated, dtype=torch.float32))
        
        record = {
            "epoch": iteration,
            "train_loss": round(inertia / len(values), 6),
            "val_loss": round(inertia / len(values), 6),
            "inertia": round(inertia, 6),
        }
        _append_jsonl(metrics_path, record)
        
        score = -inertia
        if best_score is None or score > best_score:
            best_score = score
            best_epoch = iteration
            best_record = dict(record)
            _save_checkpoint(
                context.job_dir / "best.pt", model,
                {"model_type": "kmeans", "in_dim": metadata["features"], "n_clusters": clusters},
                preprocessor, "clustering", "best", record
            )
        
        _save_checkpoint(
            context.job_dir / "last.pt", model,
            {"model_type": "kmeans", "in_dim": metadata["features"], "n_clusters": clusters},
            preprocessor, "clustering", "last", record
        )
        
        # 保存 checkpoint
        save_trainer_state(
            context.checkpoint_dir / f"ckpt-e{iteration:04d}.pt",
            model=model, optimizer=optimizer,
            arch={"model_type": "kmeans", "in_dim": metadata["features"], "n_clusters": clusters},
            preprocessor=preprocessor, task="clustering",
            params={"epochs": max_iter, "n_clusters": clusters, "seed": seed},
            epoch=iteration, best_score=best_score, best_epoch=best_epoch,
            best_record=best_record, device=device,
        )
        prune_checkpoints(store, job_id, config.WORKER_CHECKPOINT_KEEP)
        
        if on_epoch_end:
            on_epoch_end(record)
        if on_checkpoint:
            on_checkpoint(iteration)
        
        log(f"[迭代 {iteration}/{max_iter}] inertia={inertia:.4f} 平均惯性={inertia/len(values):.6f}")
        
        converged = previous_labels is not None and np.array_equal(final_labels, previous_labels)
        previous_labels = final_labels.copy()
        if converged or shift < 1e-4:
            break
    
    # 最终评估
    final_centers = model.centers.detach().cpu().numpy()
    final_labels, final_min_distances = _assign_kmeans(values, final_centers)
    inertia = float(final_min_distances.sum())
    silhouette = None
    label_count = np.unique(final_labels).size
    if 2 <= label_count < len(values):
        sample_size = min(len(values), 1000)
        try:
            silhouette = float(silhouette_score(values, final_labels, sample_size=sample_size, random_state=seed))
        except ValueError:
            pass
    
    final_result = {"val_inertia": inertia, "val_silhouette": silhouette}
    final_record = dict(best_record or {})
    final_record["val_silhouette"] = silhouette
    final_record["val_inertia"] = inertia
    
    # 保存最终最佳模型
    _save_checkpoint(
        context.job_dir / "best.pt", model,
        {"model_type": "kmeans", "in_dim": metadata["features"], "n_clusters": clusters},
        preprocessor, "clustering", "best", final_record
    )
    
    # 同步到 OSS
    from app.training.core import _save_model_artifact, _finalize_training
    
    duration = time.time() - started_at
    
    summary = _finalize_training(
        job_id, spec, context, model, preprocessor, metadata,
        best_epoch, best_record, duration, log
    )
    summary.final = _py(final_result)
    summary.best = _py(best_record or {})
    summary.n_clusters = clusters
    summary.cluster_counts = {str(c): int((final_labels == c).sum()) for c in range(clusters)}
    
    # 更新 summary.json
    store.write_json(f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/summary.json", _py(summary.to_dict()))
    
    return summary