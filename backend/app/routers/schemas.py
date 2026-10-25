"""API 请求模型（pydantic），从 main.py 抽出以拆分路由。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, model_validator


class TrainRequest(BaseModel):
    dataset_id: str
    target: str | None = None
    task: Literal["classification", "regression", "clustering", "time_series_forecasting"] = "classification"
    time_column: str | None = None
    n_clusters: int = Field(4, ge=2, le=64)
    lookback: int = Field(12, ge=2, le=512)
    forecast_horizon: int = Field(1, ge=1, le=64)
    epochs: int = Field(20, ge=1, le=500)
    batch_size: int = Field(32, ge=1, le=4096)
    lr: float = Field(1e-3, gt=0, le=1.0)
    hidden_layers: int = Field(2, ge=0, le=8)
    hidden_size: int = Field(64, ge=1, le=2048)
    test_split: float = Field(0.2, ge=0.05, le=0.5)
    seed: int = 42
    # 资源提示（任务分发匹配到卡级）：要求在 GPU 卡上训练 / 显存下限
    require_gpu: bool = False
    min_vram_mb: int = Field(0, ge=0, le=1024 * 1024)

    @model_validator(mode="after")
    def validate_task_fields(self) -> "TrainRequest":
        """按任务类型校验目标列、时间列和资源参数。"""
        if self.task in {"classification", "regression", "time_series_forecasting"} and not self.target:
            raise ValueError("该任务类型必须指定目标列")
        if self.task == "time_series_forecasting":
            if not self.time_column:
                raise ValueError("时间序列预测必须指定时间列")
            if self.time_column == self.target:
                raise ValueError("时间列不能与目标列相同")
        if self.task == "clustering" and self.require_gpu:
            raise ValueError("KMeans 聚类当前仅支持 CPU 计算")
        return self


class PredictRequest(BaseModel):
    row: dict[str, Any] | None = None
    history: list[Any] | None = None

    @model_validator(mode="after")
    def validate_input(self) -> "PredictRequest":
        """确保预测请求恰好提供一条记录或一段历史序列。"""
        if (self.row is None) == (self.history is None):
            raise ValueError("预测请求必须且只能提供 row 或 history")
        return self


class ProcessRequest(BaseModel):
    operations: list[dict[str, Any]]


class WorkerSelectionRequest(BaseModel):
    """外部计算槽接单选择请求。"""

    enabled: StrictBool


class RemoteServerRequest(BaseModel):
    """登记一台由平台经 SSH 远程接入的计算服务器。"""

    machine_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
    username: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    port: int = Field(default=22, ge=1, le=65535)
    mode: Literal["cpu", "gpu"] = "gpu"
    cpu_slots: int = Field(default=2, ge=0, le=256)
