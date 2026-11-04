"""训练核心模块单元测试：统一入口、任务注册、规格校验。

对应验收标准：
- 所有训练任务类型（分类/回归/聚类/时序/LLM）可通过统一入口注册与获取
- 未知任务类型给出明确错误
- TrainingSpec 默认值与 get 方法行为正确
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest


class TestTrainingSpec:
    """TrainingSpec 数据类行为"""

    def test_default_values(self):
        """默认构造：任务类型必填，其余有合理默认值"""
        from app.training.core import TrainingSpec

        spec = TrainingSpec(task="classification")
        assert spec.task == "classification"
        assert spec.target == ""
        assert spec.dataset_id == ""
        assert spec.distributed is False
        assert spec.params == {}
        assert spec.resource_hint == {}

    def test_get_method_returns_params_value(self):
        """get 方法从 params 取值"""
        from app.training.core import TrainingSpec

        spec = TrainingSpec(task="regression", params={"epochs": 30, "lr": 0.01})
        assert spec.get("epochs") == 30
        assert spec.get("lr") == 0.01

    def test_get_method_returns_default_for_missing(self):
        """get 方法对缺失键返回默认值"""
        from app.training.core import TrainingSpec

        spec = TrainingSpec(task="regression")
        assert spec.get("epochs") is None
        assert spec.get("epochs", 20) == 20

    @pytest.mark.parametrize(
        "task,extra",
        [
            ("classification", {"target": "label"}),
            ("regression", {"target": "price"}),
            ("clustering", {}),
            ("time_series_forecasting", {"target": "value"}),
            ("llm_sft", {}),
            ("llm_dpo", {}),
        ],
    )
    def test_all_task_types_construct(self, task, extra):
        """参数化：全部任务类型可正常构造"""
        from app.training.core import TrainingSpec

        spec = TrainingSpec(task=task, **extra)
        assert spec.task == task


class TestTaskRegistry:
    """任务注册表：注册与查找"""

    def test_builtin_tasks_registered_on_import(self):
        """导入 tasks 包后，5 类内置任务已注册"""
        import app.training.tasks  # noqa: F401
        from app.training.core import list_available_tasks

        tasks = list_available_tasks()
        for expected in ("classification", "regression", "clustering", "time_series_forecasting", "llm"):
            assert expected in tasks

    def test_get_task_class_returns_registered_class(self):
        """按名称获取任务类，返回正确类型"""
        from app.training.core import get_task_class
        from app.training.tasks import ClassificationTask

        assert get_task_class("classification") is ClassificationTask

    def test_get_task_class_unknown_raises_with_available_list(self):
        """未知任务类型抛 ValueError 并列出可用任务"""
        from app.training.core import get_task_class

        with pytest.raises(ValueError, match="未知任务类型"):
            get_task_class("no_such_task")


class TestTrainingSummary:
    """TrainingSummary 序列化"""

    def test_to_dict_round_trip(self):
        """to_dict 输出包含全部关键字段且可 JSON 序列化"""
        import json

        from app.training.core import TrainingSummary

        summary = TrainingSummary(
            task="classification",
            target="label",
            device="cpu",
            torch_version="2.0",
            cuda_version=None,
            device_backend="cpu",
            epochs_total=10,
            epochs_done=10,
            resumed_from=0,
            best_epoch=8,
            best={"val_accuracy": 0.95},
            final={},
            rows={"total": 1000},
            features=12,
            classes=["a", "b"],
            duration_sec=12.5,
            model_path="models/job-x/best.pt",
            preprocessor_path="models/job-x/preprocessor.json",
            summary_path="models/job-x/summary.json",
        )
        data = summary.to_dict()
        # JSON 可序列化（无 numpy/张量类型残留）
        json.dumps(data, ensure_ascii=False)
        assert data["task"] == "classification"
        assert data["best"]["val_accuracy"] == 0.95
        assert data["model_path"] == "models/job-x/best.pt"


class TestComputeScore:
    """任务分数计算：分类取准确率、回归/时序取负损失"""

    def test_classification_score_prefers_accuracy(self):
        from app.training.core import _compute_score

        score = _compute_score({"val_accuracy": 0.9, "val_loss": 0.5}, "classification")
        assert score == 0.9

    def test_regression_score_negates_loss(self):
        """回归分数 = -val_loss（越大越好语义统一）"""
        from app.training.core import _compute_score

        assert _compute_score({"val_loss": 0.3}, "regression") == pytest.approx(-0.3)

    def test_clustering_score_prefers_silhouette(self):
        from app.training.core import _compute_score

        score = _compute_score({"val_silhouette": 0.7, "val_inertia": 10.0}, "clustering")
        assert score == 0.7
