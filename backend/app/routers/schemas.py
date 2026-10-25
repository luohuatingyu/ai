"""API 请求模型（pydantic），从 main.py 抽出以拆分路由。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool


class TrainRequest(BaseModel):
    dataset_id: str
    target: str
    task: Literal["classification", "regression"] = "classification"
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


class PredictRequest(BaseModel):
    row: dict[str, Any]


class ProcessRequest(BaseModel):
    operations: list[dict[str, Any]]


class WorkerSelectionRequest(BaseModel):
    """外部计算槽接单选择请求。"""

    enabled: StrictBool
