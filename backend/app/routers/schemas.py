"""API 请求模型（pydantic），从 main.py 抽出以拆分路由。"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, model_validator


class TrainRequest(BaseModel):
    dataset_id: str
    target: str | None = None
    task: Literal[
        "classification", "regression", "clustering", "time_series_forecasting",
        "llm_pretraining", "llm_sft", "llm_dpo", "llm_ppo",
    ] = "classification"
    dataset_source: Literal["local", "hub"] = "local"
    dataset_config: str | None = Field(default=None, max_length=128)
    dataset_split: str = Field(default="train", min_length=1, max_length=128)
    base_model: str | None = Field(default=None, max_length=256)
    reward_model: str | None = Field(default=None, max_length=256)
    text_column: str = Field(default="text", min_length=1, max_length=128)
    prompt_column: str = Field(default="prompt", min_length=1, max_length=128)
    completion_column: str | None = Field(default=None, max_length=128)
    chosen_column: str = Field(default="chosen", min_length=1, max_length=128)
    rejected_column: str = Field(default="rejected", min_length=1, max_length=128)
    max_samples: int = Field(default=5000, ge=1, le=1_000_000)
    max_seq_len: int = Field(default=512, ge=2, le=8192)
    vocab_size: int = Field(default=8000, ge=32, le=250_000)
    model_hidden_size: int = Field(default=256, ge=16, le=8192)
    model_layers: int = Field(default=4, ge=1, le=96)
    model_heads: int = Field(default=4, ge=1, le=128)
    ppo_updates: int = Field(default=4, ge=1, le=16)
    generation_tokens: int = Field(default=32, ge=1, le=512)
    llm_lr: float = Field(default=2e-5, gt=0, le=1.0)
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
        """按任务类型校验数据源、模型标识、目标列和资源参数。"""
        llm_tasks = {"llm_pretraining", "llm_sft", "llm_dpo", "llm_ppo"}
        repo_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?$")

        def validate_repo_id(value: str | None, label: str, required: bool = False) -> None:
            """只接受 Hugging Face 仓库 ID，拒绝 URL、路径和参数注入。"""
            if required and not value:
                raise ValueError(f"{label}不能为空")
            if value and (not repo_pattern.fullmatch(value) or value in {".", ".."}):
                raise ValueError(f"{label}必须是有效的 Hugging Face 仓库 ID")

        if self.task in llm_tasks:
            if self.dataset_source == "hub":
                validate_repo_id(self.dataset_id, "数据集 ID", required=True)
            if self.task in {"llm_sft", "llm_dpo", "llm_ppo"}:
                validate_repo_id(self.base_model, "基座模型", required=True)
            if self.task == "llm_ppo":
                validate_repo_id(self.reward_model, "奖励模型", required=True)
            if self.model_hidden_size % self.model_heads:
                raise ValueError("模型隐藏维度必须能被注意力头数整除")
            if self.task == "llm_pretraining" and self.vocab_size < 32:
                raise ValueError("从零预训练的词表大小至少为 32")
        elif self.dataset_source != "local":
            raise ValueError("Hub 数据源仅支持 LLM 任务")
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


class GenerateRequest(BaseModel):
    """LLM 文本生成请求，并限制单次推理的资源消耗。"""

    prompt: str = Field(min_length=1, max_length=32768)
    max_new_tokens: int = Field(default=32, ge=1, le=512)
    temperature: float = Field(default=0.0, ge=0, le=2.0)
    top_p: float = Field(default=0.9, gt=0, le=1.0)


class ProcessRequest(BaseModel):
    operations: list[dict[str, Any]]


class WorkerSelectionRequest(BaseModel):
    """外部计算槽接单选择请求。"""

    enabled: StrictBool


class RemoteServerRequest(BaseModel):
    """登记一台由平台经 SSH 远程接入的计算服务器。"""

    machine_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
    username: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    ssh_credential_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
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


class RemoteSSHCredentialRequest(BaseModel):
    """页面新增或替换 SSH 凭证；认证秘密只存在于本次请求内。"""

    name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    auth_method: Literal["password", "key", "key_password"]
    private_key: str | None = Field(default=None, max_length=256 * 1024)
    password: str | None = Field(default=None, max_length=4096)
    passphrase: str | None = Field(default=None, max_length=4096)
    make_default: bool = False

    @model_validator(mode="after")
    def validate_credential(self) -> "RemoteSSHCredentialRequest":
        """按凭证类型检查密钥、登录密码和私钥口令，并拒绝控制字符。"""
        if not self.name.strip() or any(char in self.name for char in "\x00\r\n"):
            raise ValueError("凭证名称不能为空或包含控制字符")
        if self.auth_method in {"key", "key_password"}:
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
        elif self.private_key is not None or self.passphrase is not None:
            raise ValueError("密码认证不能同时提交私钥或私钥口令")

        if self.auth_method in {"password", "key_password"}:
            if not self.password or "\x00" in self.password:
                raise ValueError("密码认证需要填写 SSH 登录密码")
            if "\n" in self.password or "\r" in self.password:
                raise ValueError("SSH 密码不能包含换行符")
        elif self.password is not None:
            raise ValueError("密钥认证不能同时提交登录密码")

        if self.passphrase is not None and (
            "\x00" in self.passphrase or "\n" in self.passphrase or "\r" in self.passphrase
        ):
            raise ValueError("私钥口令不能包含换行符或空字符")
        return self
