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


class RemoteSSHCredentialsRequest(BaseModel):
    """页面配置平台统一 SSH 密钥或密码；明文只存在于本次请求内。"""

    auth_method: Literal["key", "password"]
    private_key: str | None = Field(default=None, max_length=256 * 1024)
    password: str | None = Field(default=None, max_length=4096)
    passphrase: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def validate_credentials(self) -> "RemoteSSHCredentialsRequest":
        """只接受与认证方式对应的必需凭证，并拒绝注入控制字符。"""
        if self.auth_method == "key":
            if not self.private_key or "\x00" in self.private_key:
                raise ValueError("密钥认证需要有效的 SSH 私钥")
            lines = self.private_key.strip().splitlines()
            if (
                len(lines) < 3
                or not lines[0].startswith("-----BEGIN ")
                or not lines[0].endswith(" PRIVATE KEY-----")
                or not lines[-1].startswith("-----END ")
                or not lines[-1].endswith(" PRIVATE KEY-----")
            ):
                raise ValueError("私钥格式无效；请粘贴完整的 OpenSSH / PEM 私钥")
            if self.password is not None:
                raise ValueError("密钥认证不能同时提交登录密码")
        else:
            if not self.password or "\x00" in self.password:
                raise ValueError("密码认证需要填写 SSH 登录密码")
            if "\n" in self.password or "\r" in self.password:
                raise ValueError("SSH 密码不能包含换行符")
            if self.private_key is not None or self.passphrase is not None:
                raise ValueError("密码认证不能同时提交私钥或私钥口令")
        if self.passphrase is not None and (
            "\x00" in self.passphrase or "\n" in self.passphrase or "\r" in self.passphrase
        ):
            raise ValueError("私钥口令不能包含换行符或空字符")
        return self
