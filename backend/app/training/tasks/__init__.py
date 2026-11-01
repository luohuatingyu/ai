"""训练任务包：所有任务类型实现"""
from __future__ import annotations

from app.training.tasks.classification import ClassificationTask
from app.training.tasks.regression import RegressionTask
from app.training.tasks.clustering import ClusteringTask, run_kmeans_training
from app.training.tasks.time_series import TimeSeriesTask
from app.training.tasks.llm import LLMTask

__all__ = [
    "ClassificationTask",
    "RegressionTask",
    "ClusteringTask",
    "run_kmeans_training",
    "TimeSeriesTask",
    "LLMTask",
]