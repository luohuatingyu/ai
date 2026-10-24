"""CSV 表格训练器：分类 / 回归（PyTorch MLP）。

- 设备自动探测：有 NVIDIA GPU（torch.cuda.is_available()）则用 cuda，否则退回 cpu。
- 产物：日志(job manager 写)、metrics.jsonl（逐 epoch 指标）、best.pt / last.pt、
  preprocessor.json、summary.json。
- 新增训练类型时：在本文件增加 run_* 函数，并在 app/main.py 的 /api/train 中路由。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset

LogFn = Callable[[str], None]


class JobCancelled(Exception):
    """用户请求取消训练（由 app.jobs 捕获并以 cancelled 状态收尾）。"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _py(value: Any) -> Any:
    """递归把 numpy 标量转原生类型，保证可 JSON 序列化。"""
    if isinstance(value, dict):
        return {str(k): _py(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_py(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_model(
    in_dim: int, out_dim: int, hidden_size: int, hidden_layers: int, device: torch.device
) -> nn.Module:
    dims = [in_dim] + [hidden_size] * hidden_layers + [out_dim]
    layers: list[nn.Module] = []
    for i, (a, b) in enumerate(zip(dims, dims[1:])):
        layers.append(nn.Linear(a, b))
        if i < len(dims) - 2:  # 非最后一层
            layers += [nn.ReLU(), nn.Dropout(0.15)]
    return nn.Sequential(*layers).to(device)


def _pick_device(log_fn: LogFn) -> torch.device:
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        log_fn(f"使用 GPU: {name} (CUDA {torch.version.cuda})")
        return torch.device("cuda")
    log_fn("未检测到 GPU，使用 CPU 训练（速度较慢，可补充 GPU 环境后复用本镜像）")
    return torch.device("cpu")


# ---------------------------------------------------------------------------
# 数据预处理
# ---------------------------------------------------------------------------

def _prepare_features(df: pd.DataFrame, target: str) -> tuple[pd.DataFrame, dict]:
    """数值列中位数填充；类别列众数填充并 one-hot 展开；返回特征表与元信息。"""
    X = df.drop(columns=[target]).copy()
    cat_cols = [c for c in X.columns if str(X[c].dtype) in ("object", "category", "bool")]
    num_cols = [c for c in X.columns if c not in cat_cols]

    num_medians: dict[str, float] = {}
    for c in num_cols:
        col = pd.to_numeric(X[c], errors="coerce")
        col = col.replace([np.inf, -np.inf], np.nan)
        median = col.median()
        num_medians[c] = 0.0 if pd.isna(median) else float(median)
        X[c] = col.fillna(num_medians[c]).astype(np.float64)

    cat_modes: dict[str, str] = {}
    for c in cat_cols:
        X[c] = X[c].astype("string").fillna("__缺失__").astype(str)
        mode = X[c].mode()
        cat_modes[c] = str(mode.iloc[0]) if len(mode) else ""

    if cat_cols:
        dummies = pd.get_dummies(
            X[cat_cols], columns=cat_cols, prefix=cat_cols, prefix_sep="=", dtype=float
        )
        X = pd.concat([X[num_cols], dummies], axis=1)

    meta = {
        "num_cols": num_cols,
        "cat_cols": cat_cols,
        "num_medians": num_medians,
        "cat_modes": cat_modes,
    }
    return X, meta


def _encode_target(y: pd.Series, task: str) -> tuple[np.ndarray, list[str] | None]:
    if task == "classification":
        labels = sorted(map(str, y.dropna().unique().tolist()))
        index = {label: i for i, label in enumerate(labels)}
        y_enc = y.map(lambda v: index[str(v)]).to_numpy(dtype=np.int64)
        return y_enc, labels
    return pd.to_numeric(y, errors="raise").to_numpy(dtype=np.float32), None


def _featurize_row(row: dict, pre: dict) -> np.ndarray:
    """单条 JSON 行 -> 模型输入向量（与训练期预处理一致，含标准化）。"""
    numeric: dict[str, float] = {}
    for c in pre["num_cols"]:
        try:
            v = float(row.get(c))
        except (TypeError, ValueError):
            v = float(pre["num_medians"].get(c, 0.0))
        numeric[c] = v

    onehot: set[str] = set()
    for c in pre["cat_cols"]:
        v = row.get(c)
        onehot.add(f"{c}={pre['cat_modes'].get(c, '') if v is None else str(v)}")

    x = np.zeros((1, len(pre["feature_names"])), dtype=np.float32)
    for i, name in enumerate(pre["feature_names"]):
        if name in numeric:
            x[0, i] = numeric[name]
        elif name in onehot:
            x[0, i] = 1.0
    x = (x - np.asarray(pre["mean"], dtype=np.float32)) / np.asarray(pre["std"], dtype=np.float32)
    return x


# ---------------------------------------------------------------------------
# 训练 / 评估
# ---------------------------------------------------------------------------

def _evaluate(
    model: nn.Module,
    loader: DataLoader,
    task: str,
    device: torch.device,
) -> tuple[dict, np.ndarray, np.ndarray]:
    """返回 ({val_loss, val_accuracy | val_mse/val_r2/val_mae}, y_pred, y_true)。"""
    loss_fn = nn.CrossEntropyLoss() if task == "classification" else nn.MSELoss()
    model.eval()
    total, seen = 0.0, 0
    preds: list[np.ndarray] = []
    trues: list[np.ndarray] = []
    with torch.no_grad():
        for X_batch, y_batch in loader:
            out = model(X_batch.to(device))
            pred = out if task == "classification" else out.squeeze(-1)
            total += loss_fn(pred, y_batch.to(device)).item() * len(y_batch)
            seen += len(y_batch)
            if task == "classification":
                preds.append(out.argmax(dim=1).cpu().numpy())
            else:
                preds.append(pred.cpu().numpy())
            trues.append(y_batch.numpy())

    y_pred = np.concatenate(preds)
    y_true = np.concatenate(trues)
    result = {"val_loss": total / max(seen, 1)}
    if task == "classification":
        result["val_accuracy"] = float(accuracy_score(y_true, y_pred))
    else:
        result["val_mse"] = float(mean_squared_error(y_true, y_pred))
        result["val_rmse"] = float(np.sqrt(result["val_mse"]))
        result["val_mae"] = float(mean_absolute_error(y_true, y_pred))
        result["val_r2"] = float(r2_score(y_true, y_pred))
    return result, y_pred, y_true


def _save_checkpoint(
    path: Path,
    model: nn.Module,
    arch: dict,
    preprocessor: dict,
    task: str,
    kind: str,
    record: dict | None,
) -> None:
    torch.save(
        {
            "state_dict": model.state_dict(),
            "arch": arch,
            "task": task,
            "kind": kind,
            "best_record": record or {},
            "preprocessor": preprocessor,
        },
        path,
    )


# ---------------------------------------------------------------------------
# 入口（由 app.jobs.JobManager 在工作线程中调用）
# ---------------------------------------------------------------------------

def run_training(
    dataset_path: Path,
    target: str,
    task: str,
    params: dict,
    cancel_event: threading.Event,
    log_fn: LogFn,
    job_dir: Path,
) -> dict:
    t0 = time.time()
    if cancel_event.is_set():
        raise JobCancelled()
    if not dataset_path.exists():
        raise ValueError("数据集文件不存在（可能已被删除）")
    if task not in ("classification", "regression"):
        raise ValueError(f"未知任务类型: {task}")

    # ---- 超参 ----
    epochs = int(params["epochs"])
    batch_size = int(params["batch_size"])
    lr = float(params["lr"])
    hidden_size = int(params["hidden_size"])
    hidden_layers = int(params["hidden_layers"])
    test_split = float(params["test_split"])
    seed = int(params["seed"])

    # ---- 环境 ----
    device = _pick_device(log_fn)
    torch.manual_seed(seed)
    np.random.seed(seed)
    log_fn(
        f"环境: torch {torch.__version__} / 设备 {device} / 显存 "
        f"{torch.cuda.get_device_properties(0).total_memory // 2**20} MiB"
        if device.type == "cuda"
        else f"环境: torch {torch.__version__} / 设备 {device}"
    )

    # ---- 数据 ----
    log_fn(f"读取数据集 {dataset_path.name} …")
    df = pd.read_csv(dataset_path)
    if target not in df.columns:
        raise ValueError(f"目标列 '{target}' 不在数据集中")
    df = df.dropna(subset=[target]).reset_index(drop=True)
    if len(df) < 10:
        raise ValueError(f"有效样本过少（{len(df)} 行，需要 >= 10 行）")

    try:
        X_df, feat_meta = _prepare_features(df, target)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"特征预处理失败：{exc}") from exc
    try:
        y_enc, classes = _encode_target(df[target], task)
    except ValueError as exc:
        raise ValueError("回归任务要求目标列全部为数值") from exc

    X_all = X_df.to_numpy(dtype=np.float32)
    y_all = y_enc
    n_features = X_all.shape[1]
    log_fn(
        f"样本 {len(X_all)} 条；特征 {n_features} 个"
        f"（数值 {len(feat_meta['num_cols'])}，one-hot 展开 {n_features - len(feat_meta['num_cols'])}）"
    )
    if classes:
        log_fn(f"分类类别: {classes}")

    stratify = None
    if task == "classification":
        min_count = int(np.bincount(y_all).min())
        if min_count >= 2:
            stratify = y_all
        else:
            log_fn("存在仅 1 个样本的类别，改为普通随机划分")

    X_train, X_val, y_train, y_val = train_test_split(
        X_all, y_all, test_size=test_split, random_state=seed, stratify=stratify
    )
    log_fn(f"划分: 训练 {X_train.shape[0]} 条 / 验证 {X_val.shape[0]} 条")

    mean = np.asarray(X_train.mean(axis=0), dtype=np.float32)
    std = np.asarray(X_train.std(axis=0), dtype=np.float32)
    std[std < 1e-8] = 1.0
    X_train = (X_train - mean) / std
    X_val = (X_val - mean) / std

    preprocessor = {
        "feature_names": [str(c) for c in X_df.columns],
        "num_cols": feat_meta["num_cols"],
        "cat_cols": feat_meta["cat_cols"],
        "num_medians": feat_meta["num_medians"],
        "cat_modes": feat_meta["cat_modes"],
        "mean": mean.tolist(),
        "std": std.tolist(),
        "classes": classes,
        "target": target,
        "task": task,
    }
    (job_dir / "preprocessor.json").write_text(
        json.dumps(_py(preprocessor), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- 模型 ----
    out_dim = len(classes) if task == "classification" else 1
    arch = {
        "in_dim": n_features,
        "out_dim": out_dim,
        "hidden_size": hidden_size,
        "hidden_layers": hidden_layers,
    }
    model = _build_model(n_features, out_dim, hidden_size, hidden_layers, device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    log_fn(f"模型: MLP({'+'.join(str(d) for d in [n_features] + [hidden_size] * hidden_layers + [out_dim])})")

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train)),
        batch_size=batch_size,
        shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val)),
        batch_size=512,
    )

    # ---- 训练循环 ----
    metrics_path = job_dir / "metrics.jsonl"
    best_record: dict | None = None
    best_score: float | None = None
    best_epoch = 0

    for epoch in range(1, epochs + 1):
        if cancel_event.is_set():
            raise JobCancelled()

        model.train()
        running, seen = 0.0, 0
        for X_batch, y_batch in train_loader:
            if cancel_event.is_set():
                raise JobCancelled()
            optimizer.zero_grad()
            out = model(X_batch.to(device))
            pred = out if task == "classification" else out.squeeze(-1)
            loss = nn.functional.cross_entropy(out, y_batch.to(device)) if task == "classification" \
                else nn.functional.mse_loss(pred, y_batch.to(device))
            loss.backward()
            optimizer.step()
            running += loss.item() * len(y_batch)
            seen += len(y_batch)
        train_loss = running / max(seen, 1)

        val_result, _, _ = _evaluate(model, val_loader, task, device)
        record: dict[str, Any] = {"epoch": epoch, "train_loss": round(train_loss, 6),
                                  "val_loss": round(val_result["val_loss"], 6)}
        if task == "classification":
            record["val_accuracy"] = round(val_result["val_accuracy"], 6)
        else:
            record["val_mse"] = round(val_result["val_mse"], 6)
            record["val_r2"] = round(val_result["val_r2"], 6)
        _append_jsonl(metrics_path, record)

        if task == "classification":
            log_fn(
                f"[epoch {epoch}/{epochs}] train_loss={train_loss:.4f} "
                f"val_loss={val_result['val_loss']:.4f} val_acc={val_result['val_accuracy']:.4f}"
            )
            score = record["val_accuracy"]
        else:
            log_fn(
                f"[epoch {epoch}/{epochs}] train_loss={train_loss:.4f} "
                f"val_loss={val_result['val_loss']:.4f} val_r2={val_result['val_r2']:.4f}"
            )
            score = -record["val_loss"]

        # 以验证集指标保留最佳权重（分类看 acc；回归看最小 val_loss）
        if best_record is None or score > best_score:
            best_score, best_epoch, best_record = score, epoch, dict(record)
            _save_checkpoint(job_dir / "best.pt", model, arch, preprocessor, task, "best", record)
        if epoch % 5 == 0 or epoch == epochs:
            _save_checkpoint(job_dir / "last.pt", model, arch, preprocessor, task, "last", record)

    # ---- 最终报告（用最佳权重）----
    state = torch.load(job_dir / "best.pt", map_location=device, weights_only=True)
    model.load_state_dict(state["state_dict"])
    final_result, y_pred, y_true = _evaluate(model, val_loader, task, device)

    summary: dict[str, Any] = {
        "task": task,
        "target": target,
        "device": str(device),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "epochs_total": epochs,
        "epochs_done": epochs,
        "best_epoch": best_epoch,
        "best": best_record,
        "final": _py(final_result),
        "rows": {"train": int(X_train.shape[0]), "val": int(X_val.shape[0])},
        "features": int(n_features),
        "classes": classes,
        "duration_sec": round(time.time() - t0, 1),
    }
    if task == "classification":
        report = classification_report(
            y_true, y_pred,
            labels=list(range(len(classes))),
            target_names=[str(c) for c in classes],
            output_dict=True,
            zero_division=0,
        )
        summary["report"] = _py(report)
        log_fn(f"最终验证准确率 {final_result['val_accuracy']:.4f}")
    else:
        log_fn(
            f"最终验证 MSE={final_result['val_mse']:.4f} RMSE={final_result['val_rmse']:.4f} "
            f"R²={final_result['val_r2']:.4f}"
        )

    (job_dir / "summary.json").write_text(
        json.dumps(_py(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log_fn(f"训练完成，用时 {summary['duration_sec']}s")
    return _py(summary)


# ---------------------------------------------------------------------------
# 在线预测（加载 best.pt）
# ---------------------------------------------------------------------------

def predict_row(ckpt_path: Path, row: dict) -> dict:
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    pre: dict = ckpt["preprocessor"]
    arch: dict = ckpt["arch"]
    task = ckpt["task"]

    x = _featurize_row(row, pre)
    model = _build_model(
        arch["in_dim"], arch["out_dim"], arch["hidden_size"], arch["hidden_layers"],
        torch.device("cpu"),
    )
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    with torch.no_grad():
        out = model(torch.from_numpy(x))

    if task == "classification":
        prob = torch.softmax(out, dim=1)[0]
        top = int(prob.argmax())
        classes = list(pre["classes"])
        return {
            "task": task,
            "prediction": classes[top],
            "probabilities": {str(c): float(p) for c, p in zip(classes, prob)},
        }
    return {"task": task, "prediction": float(out[0, 0])}
