"""预测入口：传统机器学习产物不加载 torch。

背景：预测子进程在计算槽上运行，torch 导入实测常驻约 490 MB；小内存机器上
（例如 2 vCPU / 1.7 GB 且与 Redis/MySQL 共机的远程槽）一次预测就会触发整机
内存耗尽，连带把协调面拖垮。因此：

- 新增 sklearn/xgboost 产物使用标准 pickle 保存（``format=pickle-v1``），
  读取时只依赖 numpy/sklearn；
- 历史 torch.save 产物（MLP / KMeans / 旧版传统模型）保持兼容，仅在确认是
  torch 产物时才惰性导入 ``app.trainer``（从而加载 torch）；
- 本模块在导入阶段不得引入 torch，否则优化失效。

产物格式判定：torch.save 使用 zip，文件头为 ``PK``；pickle 协议以 ``0x80``
或文本操作码开头，两者不会冲突。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

PICKLE_FORMAT = "pickle-v1"
_TORCH_ZIP_MAGIC = b"PK"


def featurize_row(row: dict, pre: dict) -> np.ndarray:
    """单条 JSON 行 -> 模型输入向量（与训练期预处理一致，含标准化）。"""
    numeric: dict[str, float] = {}
    for column in pre["num_cols"]:
        try:
            value = float(row.get(column))
        except (TypeError, ValueError):
            value = float(pre["num_medians"].get(column, 0.0))
        numeric[column] = value

    onehot: set[str] = set()
    for column in pre["cat_cols"]:
        value = row.get(column)
        onehot.add(f"{column}={pre['cat_modes'].get(column, '') if value is None else str(value)}")

    features = np.zeros((1, len(pre["feature_names"])), dtype=np.float32)
    for index, name in enumerate(pre["feature_names"]):
        if name in numeric:
            features[0, index] = numeric[name]
        elif name in onehot:
            features[0, index] = 1.0
    return (features - np.asarray(pre["mean"], dtype=np.float32)) / np.asarray(
        pre["std"], dtype=np.float32
    )


def is_torch_artifact(path: Path) -> bool:
    """torch.save 产物是 zip（以 PK 开头）；pickle 产物不会以该字节序列开头。"""
    try:
        with Path(path).open("rb") as handle:
            return handle.read(2) == _TORCH_ZIP_MAGIC
    except OSError:
        return False


def load_checkpoint(path: Path) -> dict:
    """读取平台产物：pickle 直接反序列化，torch 产物交给 trainer 惰性加载。"""
    target = Path(path)
    if is_torch_artifact(target):
        from app.trainer import load_torch_checkpoint

        return load_torch_checkpoint(target)
    with target.open("rb") as handle:
        payload = pickle.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("模型文件格式无效")
    return payload


def predict_row(
    ckpt_path: Path, row: dict | None = None, history: list | None = None,
    device: str = "cpu",
) -> dict:
    """按产物格式分派预测；pickle 传统模型不导入 torch。"""
    target = Path(ckpt_path)
    if is_torch_artifact(target):
        # 仅 torch 产物才加载 trainer（含 torch）；旧版 sklearn/xgboost 产物
        # 由 trainer 按 framework 标记走完整反序列化。
        from app.trainer import predict_torch_artifact

        return predict_torch_artifact(target, row=row, history=history, device=device)

    checkpoint = load_checkpoint(target)
    framework = checkpoint.get("framework")
    if framework not in ("sklearn", "xgboost"):
        raise ValueError("不支持的模型文件格式")
    if str(device).split(":")[0] != "cpu":
        raise ValueError("传统机器学习模型预测仅支持 CPU")
    if row is None:
        raise ValueError("表格预测需要提供 row 对象")
    pre: dict = checkpoint["preprocessor"]
    task = checkpoint["task"]
    estimator = checkpoint["estimator"]
    features = featurize_row(row, pre)
    if task == "classification":
        probabilities = estimator.predict_proba(features)[0]
        classes = list(pre["classes"])
        top = int(np.argmax(probabilities))
        return {
            "task": task,
            "prediction": classes[top],
            "probabilities": {str(c): float(p) for c, p in zip(classes, probabilities)},
        }
    value = float(np.asarray(estimator.predict(features)).ravel()[0])
    scale = pre.get("target_scale")
    if scale:
        value = value * float(scale[1]) + float(scale[0])
    return {"task": task, "prediction": value}
