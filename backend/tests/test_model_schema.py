"""模块 A/B 的 API 请求模型校验测试：模型选择与新训练超参。

覆盖场景：合法组合通过、非法任务-模型组合拒绝、sklearn 资源限制
（仅 CPU、不支持联合训练）、超参边界与默认值兼容性。
依赖 pydantic 运行期注解求值，需 Python 3.10+（容器环境）。
"""
from __future__ import annotations

import pytest

from app.routers.schemas import TrainRequest


def _valid(**overrides) -> dict:
    """最小合法分类请求；测试按需覆盖字段。"""
    base = {"dataset_id": "ds-1", "target": "label", "task": "classification"}
    base.update(overrides)
    return base


@pytest.mark.parametrize("model_type", [
    "mlp", "logistic_regression", "decision_tree",
    "random_forest", "gradient_boosting", "svm", "knn",
])
def test_classification_accepts_all_models(model_type):
    """正常场景：分类任务接受全部 7 种模型。"""
    request = TrainRequest(**_valid(model_type=model_type))
    assert request.model_type == model_type


@pytest.mark.parametrize("model_type", [
    "mlp", "linear_regression", "decision_tree",
    "random_forest", "gradient_boosting", "svm", "knn",
])
def test_regression_accepts_all_models(model_type):
    """正常场景：回归任务接受全部 7 种模型。"""
    request = TrainRequest(**_valid(task="regression", target="value", model_type=model_type))
    assert request.model_type == model_type


@pytest.mark.parametrize("task", ["classification", "regression"])
def test_xgboost_accepted_and_allows_gpu(task):
    """正常场景：xgboost 支持分类/回归，且允许 GPU 资源要求（区别于纯 sklearn）。"""
    payload = {"task": task, "model_type": "xgboost",
               "target": "label" if task == "classification" else "value"}
    request = TrainRequest(**_valid(**payload))
    assert request.model_type == "xgboost"
    gpu_request = TrainRequest(**_valid(**{**payload, "require_gpu": True}))
    assert gpu_request.require_gpu is True
    backend_request = TrainRequest(**_valid(**{**payload, "device_backend": "cuda"}))
    assert backend_request.device_backend == "cuda"
    with pytest.raises(ValueError, match="联合训练"):
        TrainRequest(**_valid(**{**payload, "parallel_workers": [
            {"worker_id": "slot-a", "batch_size": 32},
            {"worker_id": "slot-b", "batch_size": 32},
        ]}))


@pytest.mark.parametrize("task,model_type", [
    ("classification", "linear_regression"),   # 线性回归仅回归
    ("regression", "logistic_regression"),     # 逻辑回归仅分类
    ("clustering", "svm"),                     # 聚类仅 mlp
    ("clustering", "xgboost"),
    ("time_series_forecasting", "random_forest"),
    ("time_series_forecasting", "xgboost"),
    ("llm_sft", "random_forest"),
])
def test_invalid_task_model_combinations_rejected(task, model_type):
    """异常场景：任务与模型不匹配时拒绝并给出明确原因。"""
    overrides = {"task": task, "model_type": model_type}
    if task == "time_series_forecasting":
        overrides.update({"target": "value", "time_column": "ts"})
    if task == "llm_sft":
        overrides.update({"dataset_source": "hub", "dataset_id": "org/ds",
                          "base_model": "org/model", "text_column": "text"})
    if task == "clustering":
        overrides.pop("target", None)
    with pytest.raises(ValueError, match="不支持模型"):
        TrainRequest(**_valid(**overrides))


def test_sklearn_model_rejects_gpu_and_parallel():
    """权限与安全场景：sklearn 模型仅 CPU 且不支持联合训练。"""
    with pytest.raises(ValueError, match="仅支持 CPU"):
        TrainRequest(**_valid(model_type="svm", require_gpu=True))
    with pytest.raises(ValueError, match="仅支持 CPU"):
        TrainRequest(**_valid(model_type="svm", device_backend="cuda"))
    with pytest.raises(ValueError, match="联合训练"):
        TrainRequest(**_valid(model_type="knn", parallel_workers=[
            {"worker_id": "slot-a", "batch_size": 32},
            {"worker_id": "slot-b", "batch_size": 32},
        ]))
    # MLP 联合训练不受影响（回归既有行为）
    request = TrainRequest(**_valid(parallel_workers=[
        {"worker_id": "slot-a", "batch_size": 32},
        {"worker_id": "slot-b", "batch_size": 32},
    ]))
    assert request.model_type == "mlp"


def test_hyperparameter_defaults_preserve_legacy_behavior():
    """兼容性：不带新字段的历史请求得到与旧版本一致的默认值。"""
    request = TrainRequest(**_valid())
    assert request.model_type == "mlp"
    assert request.optimizer == "adamw"
    assert request.lr_scheduler == "none"
    assert request.early_stopping is False
    assert request.patience == 5
    assert request.dropout == 0.15
    assert request.weight_decay == 0.01  # AdamW 的 PyTorch 默认，历史行为一致
    assert request.activation == "relu"


@pytest.mark.parametrize("field,value", [
    ("dropout", 0.95), ("dropout", -0.1),
    ("weight_decay", 0.6), ("weight_decay", -0.01),
    ("patience", 0), ("patience", 101),
])
def test_hyperparameter_bounds_rejected(field, value):
    """边界场景：超参越界时 422 拒绝。"""
    with pytest.raises(ValueError):
        TrainRequest(**_valid(**{field: value}))


@pytest.mark.parametrize("field,value", [
    ("dropout", 0.0), ("dropout", 0.9),
    ("weight_decay", 0.0), ("weight_decay", 0.5),
    ("patience", 1), ("patience", 100),
])
def test_hyperparameter_bounds_accepted(field, value):
    """边界场景：超参取上/下边界值时接受。"""
    request = TrainRequest(**_valid(**{field: value}))
    assert getattr(request, field) == value


@pytest.mark.parametrize("field,value", [
    ("optimizer", "momentum"), ("lr_scheduler", "linear"),
    ("activation", "selu"), ("model_type", "lightgbm"),
])
def test_invalid_literal_choices_rejected(field, value):
    """异常场景：非法枚举值被 Literal 拒绝。"""
    with pytest.raises(ValueError):
        TrainRequest(**_valid(**{field: value}))
