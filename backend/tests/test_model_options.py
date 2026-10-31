"""模块 A/B 测试：分类/回归模型选择（sklearn 传统 ML）与 MLP 训练超参。

ML 平台特有场景：
- 训练链路：metrics 逐 epoch 产出、best.pt 产物断言、summary 超参记录；
- 预测一致性：同一 best.pt 重复预测结果稳定，分类返回概率、回归返回原量纲数值；
- 可复现性：sklearn 分阶段模型（随机森林/梯度提升）断点续训与一口气训练等价；
- 兼容性：不带新参数的历史请求走 mlp 路径，默认行为与旧版本完全一致。
"""
from __future__ import annotations

import json
import math
import threading

import numpy as np
import pytest

from app import trainer
from app.trainer import load_trainer_state, predict_row, run_training
from tests.conftest import job_epochs  # noqa: F401


def _run(ds_id: str, task: str, epochs: int, tmp_dir, resume_state=None,
         target="target", **extra_params):
    """统一参数的训练调用（与 worker 的调用方式一致）。"""
    from app import config

    return run_training(
        dataset_path=config.DATASETS_DIR / f"{ds_id}.csv",
        target=target,
        task=task,
        params={"epochs": epochs, "batch_size": 32, "lr": 0.01, "hidden_size": 16,
                "hidden_layers": 1, "test_split": 0.2, "seed": 42, **extra_params},
        cancel_event=threading.Event(),
        log_fn=lambda msg: None,
        job_dir=tmp_dir,
        resume_state=resume_state,
    )


def _metrics(tmp_dir):
    return [json.loads(l) for l in
            (tmp_dir / "metrics.jsonl").read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# 模块 A：sklearn 模型训练链路
# ---------------------------------------------------------------------------

CLASSIFICATION_MODELS = [
    "logistic_regression", "decision_tree", "random_forest",
    "gradient_boosting", "svm", "knn",
]
REGRESSION_MODELS = [
    "linear_regression", "decision_tree", "random_forest",
    "gradient_boosting", "svm", "knn",
]


@pytest.mark.parametrize("model_type", CLASSIFICATION_MODELS)
def test_sklearn_classification_models(store, make_dataset, tmp_path, model_type):
    """正常场景（分类）：每种 sklearn 模型完成训练并产出标准产物与指标。"""
    ds = make_dataset(kind="classification")
    summary = _run(ds, "classification", 3, tmp_path, model_type=model_type)
    assert summary["model_type"] == model_type
    assert summary["framework"] == "scikit-learn"
    assert summary["device"] == "cpu"
    assert "val_accuracy" in summary["final"]
    assert (tmp_path / "best.pt").exists()
    assert (tmp_path / "last.pt").exists()
    assert (tmp_path / "preprocessor.json").exists()
    assert (tmp_path / "summary.json").exists()
    assert len(_metrics(tmp_path)) >= 1


@pytest.mark.parametrize("model_type", REGRESSION_MODELS)
def test_sklearn_regression_models(store, make_dataset, tmp_path, model_type):
    """正常场景（回归）：每种 sklearn 模型产出 R²/RMSE 指标。"""
    ds = make_dataset(kind="regression")
    summary = _run(ds, "regression", 3, tmp_path, model_type=model_type)
    assert summary["model_type"] == model_type
    assert "val_r2" in summary["final"] and "val_rmse" in summary["final"]
    assert (tmp_path / "best.pt").exists()
    assert len(_metrics(tmp_path)) >= 1


def test_sklearn_prediction_consistency(store, make_dataset, tmp_path):
    """预测一致性：同一 best.pt 重复预测稳定；分类含概率、回归反归一化为有限数值。"""
    ds = make_dataset(kind="classification", name="pred-cls")
    _run(ds, "classification", 2, tmp_path, model_type="random_forest")
    row = {"f1": 0.5, "f2": -0.3, "f3": 0.2}
    first = predict_row(tmp_path / "best.pt", row=row)
    second = predict_row(tmp_path / "best.pt", row=row)
    assert first["task"] == "classification"
    assert first == second, "同一模型对同一输入的预测必须稳定"
    assert set(first["probabilities"]) == {"0", "1", "2"}
    assert math.isclose(sum(first["probabilities"].values()), 1.0, rel_tol=1e-6)

    ds2 = make_dataset(kind="regression", name="pred-reg")
    reg_dir = tmp_path / "reg"
    reg_dir.mkdir()
    _run(ds2, "regression", 2, reg_dir, model_type="gradient_boosting")
    value = predict_row(reg_dir / "best.pt", row={"f1": 0.1, "f2": 0.4, "f3": -0.7})
    assert value["task"] == "regression"
    assert isinstance(value["prediction"], float) and math.isfinite(value["prediction"])


def test_sklearn_staged_models_span_epochs(store, make_dataset, tmp_path):
    """分阶段模型（随机森林/梯度提升）按 epoch 增量拟合，metrics 逐轮产出。"""
    ds = make_dataset(kind="classification")
    summary = _run(ds, "classification", 4, tmp_path, model_type="random_forest")
    assert [m["epoch"] for m in _metrics(tmp_path)] == [1, 2, 3, 4]
    assert summary["epochs_total"] == 4 and summary["epochs_done"] == 4

    gb_dir = tmp_path / "gb"
    gb_dir.mkdir()
    _run(ds, "classification", 4, gb_dir, model_type="gradient_boosting")
    assert [m["epoch"] for m in _metrics(gb_dir)] == [1, 2, 3, 4]


def test_sklearn_oneshot_single_epoch(store, make_dataset, tmp_path):
    """一次性模型（决策树等）：无论传多少 epochs，只拟合 1 轮且如实上报。"""
    ds = make_dataset(kind="classification")
    summary = _run(ds, "classification", 5, tmp_path, model_type="decision_tree")
    assert summary["epochs_total"] == 1 and summary["epochs_done"] == 1
    assert [m["epoch"] for m in _metrics(tmp_path)] == [1]


def test_sklearn_resume_equals_full_run(store, make_dataset, tmp_path):
    """可复现性：梯度提升 3+3 续训与一口气 6 epoch 的指标轨迹完全一致。"""
    ds = make_dataset(kind="regression")
    full_dir = tmp_path / "full"
    full_dir.mkdir()
    full = _run(ds, "regression", 6, full_dir, model_type="gradient_boosting")

    part_dir = tmp_path / "part"
    part_dir.mkdir()
    _run(ds, "regression", 3, part_dir, model_type="gradient_boosting")
    state = load_trainer_state(part_dir / "checkpoints" / "ckpt-e0003.pt")
    assert state["epoch"] == 3 and state["framework"] == "sklearn"
    resumed = _run(ds, "regression", 6, part_dir,
                   resume_state=state, model_type="gradient_boosting")

    m_full = _metrics(full_dir)
    m_part = _metrics(part_dir)
    assert [m["epoch"] for m in m_part] == [1, 2, 3, 4, 5, 6]
    for a, b in zip(m_full, m_part):
        assert abs(a["train_loss"] - b["train_loss"]) < 1e-6
        assert abs(a["val_loss"] - b["val_loss"]) < 1e-6
    assert abs(full["final"]["val_r2"] - resumed["final"]["val_r2"]) < 1e-6


def test_sklearn_rejects_other_tasks(store, make_dataset, tmp_path):
    """异常场景：sklearn 模型仅支持分类/回归，其他任务必须走 mlp 路径。"""
    ds = make_dataset(kind="clustering")
    with pytest.raises(ValueError, match="联合训练|仅支持"):
        _run(ds, "clustering", 2, tmp_path, model_type="svm")
    ds_ts = make_dataset(kind="time_series")
    ts_dir = tmp_path / "ts"
    ts_dir.mkdir()
    with pytest.raises(ValueError, match="仅支持 mlp"):
        _run(ds_ts, "time_series_forecasting", 2, ts_dir,
             model_type="random_forest", time_column="timestamp")


# ---------------------------------------------------------------------------
# 模块 B：MLP 训练超参
# ---------------------------------------------------------------------------

def test_mlp_new_hyperparams_roundtrip(store, make_dataset, tmp_path):
    """正常场景：新超参生效、写入 summary 与 checkpoint，预测按 arch 重建成功。"""
    ds = make_dataset(kind="classification")
    summary = _run(ds, "classification", 3, tmp_path,
                   optimizer="sgd", lr_scheduler="cosine", dropout=0.3,
                   activation="gelu", weight_decay=0.0)
    assert summary["optimizer"] == "sgd"
    assert summary["lr_scheduler"] == "cosine"
    assert summary["dropout"] == 0.3
    assert summary["activation"] == "gelu"
    assert summary["weight_decay"] == 0.0
    # checkpoint 往返：predict_row 按 arch（含 dropout/activation）重建网络
    result = predict_row(tmp_path / "best.pt", row={"f1": 0.5, "f2": -0.3, "f3": 0.2})
    assert result["task"] == "classification"
    assert math.isclose(sum(result["probabilities"].values()), 1.0, rel_tol=1e-6)


def test_early_stopping_triggers_on_stale_metric(store, make_dataset, tmp_path, monkeypatch):
    """边界场景：验证指标停滞时按耐心值早停，epochs_done 小于 epochs_total。

    用固定 _evaluate 输出隔离数据随机性，专测早停分支逻辑本身。
    """
    ds = make_dataset(kind="classification")
    # _evaluate 返回合法数组，保证早停后的最终报告可用真实分类报告路径
    constant = ({"val_loss": 0.5, "val_accuracy": 0.8},
                np.zeros(1, dtype=int), np.zeros(1, dtype=int))
    monkeypatch.setattr(trainer, "_evaluate", lambda *args, **kwargs: constant)
    summary = _run(ds, "classification", 10, tmp_path,
                   early_stopping=True, patience=1)
    assert summary["early_stopping"] is True
    assert summary["epochs_done"] == 2, "第 1 轮建立 best，第 2 轮无提升应触发 patience=1 早停"
    assert summary["epochs_total"] == 10


def test_early_stopping_real_run_smoke(store, make_dataset, tmp_path):
    """正常场景（真实数据）：开启早停后完整训练不早停也不报错（指标持续提升时不触发）。"""
    ds = make_dataset(kind="classification")
    summary = _run(ds, "classification", 3, tmp_path, early_stopping=True, patience=2)
    assert summary["epochs_done"] == 3
    assert summary["early_stopping"] is True


def test_resume_rejects_changed_arch(store, make_dataset, tmp_path):
    """异常场景：续训 checkpoint 与当前网络结构（dropout）不一致时明确拒绝。"""
    ds = make_dataset(kind="classification")
    part_dir = tmp_path / "part"
    part_dir.mkdir()
    _run(ds, "classification", 2, part_dir, dropout=0.2)
    state = load_trainer_state(part_dir / "checkpoints" / "ckpt-e0002.pt")
    state["arch"]["dropout"] = 0.5
    with pytest.raises(ValueError, match="网络结构"):
        _run(ds, "classification", 4, part_dir, resume_state=state, dropout=0.2)


def test_default_params_preserve_legacy_behavior(store, make_dataset, tmp_path):
    """兼容性：不带新参数的历史请求走默认值，与旧版本行为完全一致。"""
    ds = make_dataset(kind="classification")
    summary = _run(ds, "classification", 2, tmp_path)
    assert summary["optimizer"] == "adamw"
    assert summary["lr_scheduler"] == "none"
    assert summary["early_stopping"] is False
    assert summary["dropout"] == 0.15
    assert summary["activation"] == "relu"
    assert summary["weight_decay"] == 0.01  # AdamW 的 PyTorch 默认值，历史任务一致
