"""CSV 表格训练器：分类 / 回归（PyTorch MLP）。

- 设备自动探测：有 NVIDIA GPU（torch.cuda.is_available()）则用 cuda，否则退回 cpu。
- 产物：日志(job manager 写)、metrics.jsonl（逐 epoch 指标）、best.pt / last.pt、
  preprocessor.json、summary.json、checkpoints/ckpt-e*.pt。
- **训练上下文与 GPU 解耦**：
  * save_trainer_state / load_trainer_state 保存"完整可迁移训练状态"——
    模型权重 + 优化器状态 + 随机数状态 + epoch 进度 + best 追踪 + 预处理器；
  * 保存时全部 CPU 化（设备无关），任意服务器/任意显卡（甚至 CPU）可接手续训；
  * run_training 支持 resume_state 断点续训与 pause_event 优雅暂停。
- 新增训练类型时：在本文件增加 run_* 函数，并在 app/main.py 的 /api/train 中路由。
"""
from __future__ import annotations

import json
import random
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

import app.config as config

LogFn = Callable[[str], None]


class JobCancelled(Exception):
    """用户请求取消训练（由 app.jobs 捕获并以 cancelled 状态收尾）。"""


class JobPaused(Exception):
    """训练被优雅暂停（租约丢失 / 用户暂停 / 服务器释放前 SIGTERM）。

    由 app.worker 捕获：暂停前训练器已保存完整 trainer-state checkpoint，
    任务进入 paused 状态，可被任意其它服务器/显卡接手续训。
    """


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


def _to_cpu(obj: Any) -> Any:
    """递归把张量/嵌套结构搬到 CPU：保证 checkpoint 与设备无关（可跨显卡/跨机迁移）。"""
    if torch.is_tensor(obj):
        return obj.detach().to("cpu")
    if isinstance(obj, dict):
        return {k: _to_cpu(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        converted = [_to_cpu(v) for v in obj]
        return type(obj)(converted) if isinstance(obj, tuple) else converted
    return obj


def _rng_snapshot() -> dict:
    """打包 torch / numpy / python 三套随机数状态。

    numpy 与 python 的状态转为纯 JSON 类型（列表/数字），保证能通过
    torch.load(weights_only=True) 的安全加载（跨服务器续训不降级安全性）。
    """
    np_state = np.random.get_state()  # ('MT19937', keys, pos, has_gauss, cached)
    py_state = random.getstate()     # (version, tuple_of_ints, gauss_next)
    snapshot = {
        "torch": torch.get_rng_state(),
        "numpy": {
            "op": str(np_state[0]),
            "keys": np.asarray(np_state[1]).tolist(),
            "pos": int(np_state[2]),
            "has_gauss": int(np_state[3]),
            "cached_gaussian": float(np_state[4]),
        },
        "python": {"version": int(py_state[0]), "state": list(py_state[1]), "gauss": py_state[2]},
    }
    # CUDA 随机数状态一并无损保存（GPU 上 Dropout 等算子使用设备侧 RNG）；
    # 恢复时设备数不同则跳过（跨卡续训本就不承诺逐位一致）
    if torch.cuda.is_available():
        snapshot["cuda"] = torch.cuda.get_rng_state_all()
    return snapshot


def _rng_restore(snapshot: dict, log_fn: LogFn | None = None) -> None:
    """恢复随机数状态：使续训的数据 shuffle / Dropout 序列与未中断时一致。"""
    try:
        torch.set_rng_state(snapshot["torch"].cpu())
        np_state = snapshot["numpy"]
        np.random.set_state((
            np_state["op"], np.asarray(np_state["keys"], dtype=np.int64),
            int(np_state["pos"]), int(np_state["has_gauss"]),
            float(np_state["cached_gaussian"]),
        ))
        py_state = snapshot["python"]
        random.setstate((int(py_state["version"]), tuple(py_state["state"]), py_state["gauss"]))
    except (KeyError, TypeError, ValueError) as exc:
        if log_fn:
            log_fn(f"随机数状态恢复失败（不影响续训，仅 shuffle 序列变化）: {exc}")
    cuda_states = snapshot.get("cuda")
    if cuda_states and torch.cuda.is_available() and torch.cuda.device_count() >= len(cuda_states):
        try:
            torch.cuda.set_rng_state_all([s.cpu() for s in cuda_states])
        except (RuntimeError, ValueError) as exc:
            if log_fn:
                log_fn(f"CUDA 随机数状态恢复跳过: {exc}")


def _version_tuple(version: str) -> tuple:
    """torch 版号转可比较元组（忽略 2.8.0+cu126 之类的后缀）。"""
    digits = version.split("+")[0].split(".")
    out = []
    for d in digits[:3]:
        try:
            out.append(int(d))
        except ValueError:
            break
    return tuple(out)


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
    # 首先尝试初始化GPU
    try:
        if torch.cuda.is_available():
            # 尝试获取GPU设备名称，如果失败则抛出异常
            name = torch.cuda.get_device_name(0)
            # 尝试创建一个张量来测试GPU是否真正可用
            test_tensor = torch.tensor([1.0], device='cuda')
            log_fn(f"成功启动 GPU: {name} (CUDA {torch.version.cuda})")
            return torch.device("cuda")
        else:
            raise RuntimeError("CUDA 不可用")
    except Exception as e:
        # GPU初始化失败，回退到CPU
        log_fn(f"GPU 初始化失败: {str(e)}，回退到 CPU 训练（速度较慢，可检查 GPU 驱动和 CUDA 版本）")
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
    y_scale: tuple[float, float] | None = None,
) -> tuple[dict, np.ndarray, np.ndarray]:
    """返回指标字典 + (y_pred, y_true)。

    y_scale=(mean, std) 时为回归任务：网络自己在标准化空间训练，
    返回的 val_loss/mse/rmse/mae 已反归一化到目标原始量纲。
    """
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
    k = (y_scale[1] ** 2) if y_scale else 1.0
    k_mae = y_scale[1] if y_scale else 1.0
    result = {"val_loss": (total / max(seen, 1)) * k}
    if task == "classification":
        result["val_accuracy"] = float(accuracy_score(y_true, y_pred))
    else:
        result["val_mse"] = float(mean_squared_error(y_true, y_pred)) * k
        result["val_rmse"] = float(np.sqrt(result["val_mse"]))
        result["val_mae"] = float(mean_absolute_error(y_true, y_pred)) * k_mae
        # R² 对线性变换尺度不变，可直接用网络输出计算
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
            # 权重 CPU 化：best.pt 可在任意服务器/任意设备上加载推理
            "state_dict": _to_cpu(model.state_dict()),
            "arch": arch,
            "task": task,
            "kind": kind,
            "best_record": record or {},
            "preprocessor": preprocessor,
        },
        path,
    )


# ---------------------------------------------------------------------------
# 完整可迁移训练状态（trainer state）
# ---------------------------------------------------------------------------

def save_trainer_state(
    path: Path,
    *,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    arch: dict,
    preprocessor: dict,
    task: str,
    params: dict,
    epoch: int,
    best_score: float | None,
    best_epoch: int,
    best_record: dict | None,
    device: torch.device,
) -> dict:
    """保存"完整训练上下文"——续训所需的全部状态，设备无关。

    包含：模型权重、优化器状态（AdamW 动量）、三套随机数状态、
    epoch 进度、best 追踪、预处理器、关键超参与版本元信息。
    张量全部 CPU 化；numpy/python RNG 转 JSON 安全类型，
    整个文件可通过 torch.load(weights_only=True) 安全加载。
    """
    state = {
        "kind": "trainer_state",
        "task": task,
        "arch": arch,
        "params": params,
        "epoch": int(epoch),
        "best_score": best_score,
        "best_epoch": int(best_epoch),
        "best_record": best_record or {},
        "state_dict": _to_cpu(model.state_dict()),
        "optimizer": _to_cpu(optimizer.state_dict()),
        "rng": _rng_snapshot(),
        "preprocessor": preprocessor,
        "meta": {
            "torch_version": str(torch.__version__),
            "saved_device": str(device),
            "saved_at": time.time(),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    torch.save(state, tmp)
    tmp.replace(path)  # 原子落盘：中断不会留下半写的 checkpoint
    return state


def load_trainer_state(path: Path) -> dict:
    """加载训练上下文；文件缺失/损坏/不兼容时抛出带明确指引的异常。"""
    if not path.exists():
        raise FileNotFoundError(f"训练状态文件不存在: {path}")
    try:
        state = torch.load(path, map_location="cpu", weights_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"训练状态文件损坏或由不兼容版本保存: {exc}") from exc
    if not isinstance(state, dict) or state.get("kind") != "trainer_state":
        raise ValueError(f"不是有效的训练状态文件（kind 不匹配）: {path}")
    return state


def _validate_resume_state(
    state: dict, arch: dict, task: str, log_fn: LogFn
) -> None:
    """续训前校验 checkpoint 与当前任务/超参/环境兼容，失败给出明确原因。"""
    if state.get("task") != task:
        raise ValueError(f"checkpoint 任务类型不匹配: {state.get('task')!r} != {task!r}")
    if state.get("arch") != arch:
        raise ValueError(
            f"checkpoint 网络结构与当前超参不一致（arch {state.get('arch')} != {arch}），"
            "续训要求 hidden_size/hidden_layers/特征数与原训练完全一致"
        )
    saved_tv = str(state.get("meta", {}).get("torch_version", ""))
    if saved_tv and _version_tuple(saved_tv) > _version_tuple(str(torch.__version__)):
        raise ValueError(
            f"当前 torch {torch.__version__} 低于 checkpoint 保存版本 {saved_tv}，"
            "为避免静默出错请升级 torch 后再续训"
        )
    log_fn(f"checkpoint 兼容性校验通过（保存于 torch {saved_tv}）")


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
    pause_event: threading.Event | None = None,
    resume_state: dict | None = None,
    on_epoch_end: Callable[[dict], None] | None = None,
    on_checkpoint: Callable[[int], None] | None = None,
) -> dict:
    """执行（或续训执行）一次训练，返回 summary。

    参数说明（多机无状态 worker 架构）：
    - pause_event: 优雅暂停信号；触发时先保存完整 trainer-state 再抛 JobPaused；
    - resume_state: load_trainer_state 的返回值，跨服务器/跨显卡续训的完整上下文；
    - on_epoch_end: 每 epoch 结束回调（worker 借此把 metrics/日志同步到状态平面）；
    - on_checkpoint: trainer-state 存档回调（worker 上传版本化 checkpoint 到对象存储）。
    """
    # 添加数据库日志功能（数据库关闭/不可用时不做任何 DB 操作，避免连接阻塞）
    def db_log_fn(msg: str):
        log_fn(msg)
        if not config.DB_AVAILABLE:
            return
        try:
            from app.db_utils import add_job_log
            from app.database import SessionLocal
            # 从job_dir获取job_id（worker 的 scratch 目录名即 job_id）
            job_id = job_dir.name
            with SessionLocal() as db:
                add_job_log(db, job_id, msg)
        except Exception:
            # 如果数据库操作失败，忽略错误，继续使用文件日志
            pass
    t0 = time.time()
    if cancel_event.is_set():
        raise JobCancelled()
    if pause_event is None:
        pause_event = threading.Event()
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
    device = _pick_device(db_log_fn)
    torch.manual_seed(seed)
    np.random.seed(seed)
    db_log_fn(
        f"环境: torch {torch.__version__} / 设备 {device} / 显存 "
        f"{torch.cuda.get_device_properties(0).total_memory // 2**20} MiB"
        if device.type == "cuda"
        else f"环境: torch {torch.__version__} / 设备 {device}"
    )

    # ---- 数据 ----
    db_log_fn(f"读取数据集 {dataset_path.name} …")
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
    db_log_fn(
        f"样本 {len(X_all)} 条；特征 {n_features} 个"
        f"（数值 {len(feat_meta['num_cols'])}，one-hot 展开 {n_features - len(feat_meta['num_cols'])}）"
    )
    if classes:
        db_log_fn(f"分类类别: {classes}")

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
    db_log_fn(f"划分: 训练 {X_train.shape[0]} 条 / 验证 {X_val.shape[0]} 条")

    mean = np.asarray(X_train.mean(axis=0), dtype=np.float32)
    std = np.asarray(X_train.std(axis=0), dtype=np.float32)
    std[std < 1e-8] = 1.0
    X_train = (X_train - mean) / std
    X_val = (X_val - mean) / std

    # 回归目标标准化（避免大数值目标导致 loss 梯度过小、训练不动）
    y_scale: tuple[float, float] | None = None
    if task == "regression":
        y_mean = float(np.asarray(y_train, dtype=np.float64).mean())
        y_std = float(np.asarray(y_train, dtype=np.float64).std())
        if y_std < 1e-8:
            y_std = 1.0
        y_scale = (y_mean, y_std)
        log_fn(f"回归目标标准化: mean={y_mean:.2f}, std={y_std:.2f}（评估与预测时反归一化）")
        y_train = ((y_train - y_mean) / y_std).astype(np.float32)
        y_val = ((y_val - y_mean) / y_std).astype(np.float32)

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
        "target_scale": y_scale,
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
    db_log_fn(f"模型: MLP({'+'.join(str(d) for d in [n_features] + [hidden_size] * hidden_layers + [out_dim])})")

    # ---- 恢复训练上下文（跨服务器/跨显卡续训） ----
    start_epoch = 0
    best_record: dict | None = None
    best_score: float | None = None
    best_epoch = 0
    if resume_state is not None:
        # 校验 checkpoint 与当前任务/超参/环境兼容，不兼容直接拒绝（不静默出错）
        _validate_resume_state(resume_state, arch, task, db_log_fn)
        model.load_state_dict(resume_state["state_dict"])
        # 优化器状态（AdamW 动量）：缺失会导致续训等效于学习率冷启动、loss 跳变
        optimizer.load_state_dict(resume_state["optimizer"])
        start_epoch = int(resume_state.get("epoch", 0))
        best_score = resume_state.get("best_score")
        best_epoch = int(resume_state.get("best_epoch", 0))
        best_record = resume_state.get("best_record") or None
        saved_device = resume_state.get("meta", {}).get("saved_device", "未知")
        db_log_fn(
            f"续训恢复: 从第 {start_epoch + 1} 个 epoch 继续（原设备 {saved_device}，"
            f"本机设备 {device}；优化器/随机数状态已还原）"
        )
        # 随机数状态恢复必须在模型构建之后：续训的数据 shuffle 序列与未中断时完全一致
        _rng_restore(resume_state.get("rng") or {}, db_log_fn)

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
    # trainer-state 存档间隔（epoch 数）：进度兜底线，暂停/崩溃时可回退到最近存档
    ckpt_interval = max(int(getattr(config, "WORKER_CHECKPOINT_INTERVAL", 5)), 1)

    def _persist_state(finished_epoch: int) -> None:
        """保存完整训练上下文到本地 scratch，并回调 worker 上传到状态平面。"""
        save_trainer_state(
            job_dir / "checkpoints" / f"ckpt-e{finished_epoch:04d}.pt",
            model=model,
            optimizer=optimizer,
            arch=arch,
            preprocessor=preprocessor,
            task=task,
            params={
                "epochs": epochs, "batch_size": batch_size, "lr": lr,
                "hidden_size": hidden_size, "hidden_layers": hidden_layers,
                "test_split": test_split, "seed": seed,
            },
            epoch=finished_epoch,
            best_score=best_score,
            best_epoch=best_epoch,
            best_record=best_record,
            device=device,
        )
        if on_checkpoint is not None:
            on_checkpoint(finished_epoch)

    for epoch in range(start_epoch + 1, epochs + 1):
        if cancel_event.is_set():
            raise JobCancelled()
        if pause_event.is_set():
            # 优雅暂停：先存档到最近完成的 epoch，再交由 worker 标记 paused
            _persist_state(epoch - 1)
            raise JobPaused(f"训练已在第 {epoch - 1} 个 epoch 后暂停")

        model.train()
        running, seen = 0.0, 0
        for X_batch, y_batch in train_loader:
            if cancel_event.is_set():
                raise JobCancelled()
            if pause_event.is_set():
                _persist_state(epoch - 1)
                raise JobPaused(f"训练已在第 {epoch - 1} 个 epoch 后暂停（epoch {epoch} 中途）")
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
        if y_scale:  # 反归一化到目标原始量纲，保持 train/val 曲线单位一致
            train_loss *= y_scale[1] ** 2

        val_result, _, _ = _evaluate(model, val_loader, task, device, y_scale=y_scale)
        record: dict[str, Any] = {"epoch": epoch, "train_loss": round(train_loss, 6),
                                  "val_loss": round(val_result["val_loss"], 6)}
        if task == "classification":
            record["val_accuracy"] = round(val_result["val_accuracy"], 6)
        else:
            record["val_mse"] = round(val_result["val_mse"], 6)
            record["val_r2"] = round(val_result["val_r2"], 6)
        _append_jsonl(metrics_path, record)

        if task == "classification":
            db_log_fn(
                f"[epoch {epoch}/{epochs}] train_loss={train_loss:.4f} "
                f"val_loss={val_result['val_loss']:.4f} val_acc={val_result['val_accuracy']:.4f}"
            )
            score = record["val_accuracy"]
        else:
            db_log_fn(
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

        # 周期性保存完整训练上下文（断点续训依据），并同步 metrics/日志到状态平面
        if epoch % ckpt_interval == 0 or epoch == epochs:
            _persist_state(epoch)
        if on_epoch_end is not None:
            on_epoch_end(record)

    # ---- 最终报告（用最佳权重）----
    best_path = job_dir / "best.pt"
    if not best_path.exists():
        raise ValueError(
            "缺少 best.pt 权重产物，无法生成最终报告（续训时由接手 worker 从状态平面拉取；"
            "若任务从未训练过任何 epoch 请检查数据与超参）"
        )
    state = torch.load(best_path, map_location=device, weights_only=True)
    model.load_state_dict(state["state_dict"])
    final_result, y_pred, y_true = _evaluate(model, val_loader, task, device, y_scale=y_scale)

    summary: dict[str, Any] = {
        "task": task,
        "target": target,
        "device": str(device),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "epochs_total": epochs,
        "epochs_done": epochs,
        "resumed_from": start_epoch,          # 本次运行起始 epoch（0 表示全新训练）
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
        db_log_fn(f"最终验证准确率 {final_result['val_accuracy']:.4f}")
    else:
        db_log_fn(
            f"最终验证 MSE={final_result['val_mse']:.4f} RMSE={final_result['val_rmse']:.4f} "
            f"R²={final_result['val_r2']:.4f}"
        )

    (job_dir / "summary.json").write_text(
        json.dumps(_py(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    db_log_fn(f"训练完成，用时 {summary['duration_sec']}s")
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
    value = float(out[0, 0])
    scale = pre.get("target_scale")
    if scale:
        value = value * float(scale[1]) + float(scale[0])
    return {"task": task, "prediction": value}
