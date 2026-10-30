"""API 请求模型（pydantic），从 main.py 抽出以拆分路由。"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, model_validator


class ParallelMember(BaseModel):
    """联合训练的固定成员和本地批大小，允许不同算力节点承担不同数据量。"""

    worker_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    batch_size: int = Field(default=32, ge=1, le=4096)


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
    worker_id: str | None = Field(default=None, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    require_gpu: bool = False
    min_vram_mb: int = Field(0, ge=0, le=1024 * 1024)
    device_backend: Literal["auto", "cuda", "rocm", "xpu", "mps", "cpu"] = "auto"
    parallel_workers: list[ParallelMember] = Field(default_factory=list, max_length=32)
    parallel_timeout: int = Field(default=300, ge=10, le=3600)

    @model_validator(mode="before")
    @classmethod
    def validate_resource_selection(cls, data: Any) -> Any:
        """单槽定向与多节点联合选择互斥，避免调度策略相互覆盖。"""
        if isinstance(data, dict) and data.get("worker_id") and data.get("parallel_workers"):
            raise ValueError("指定单个计算槽与多节点联合训练不能同时选择")
        return data

    @model_validator(mode="after")
    def validate_task_fields(self) -> "TrainRequest":
        """按任务类型校验数据源、模型标识、目标列和资源参数。"""
        llm_tasks = {"llm_pretraining", "llm_sft", "llm_dpo", "llm_ppo"}
        if self.parallel_workers:
            identifiers = [item.worker_id for item in self.parallel_workers]
            if len(identifiers) < 2 or len(set(identifiers)) != len(identifiers):
                raise ValueError("联合训练必须选择 2～32 个不同的计算槽")
            if self.task == "clustering":
                raise ValueError("KMeans 暂不支持梯度同步，请使用单节点模式")
            if self.task in llm_tasks and any(item.batch_size > 64 for item in self.parallel_workers):
                raise ValueError("LLM 联合训练的各节点批大小不得超过 64")
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
        if self.device_backend == "cpu" and self.require_gpu:
            raise ValueError("CPU 后端不能同时设置 require_gpu=true")
        if self.task == "clustering" and self.device_backend not in {"auto", "cpu"}:
            raise ValueError("KMeans 聚类当前仅支持 CPU 后端")
        return self


class HubDatasetRequest(BaseModel):
    """维护页的 Hub 来源，仅允许公开仓库引用，不接收任意下载地址或令牌。"""

    repo_id: str = Field(min_length=1, max_length=193)
    revision: str = Field(default="main", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_./-]+$")
    subset: str | None = Field(default=None, min_length=1, max_length=128)
    split: str = Field(default="train", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    storage_mode: Literal["local", "remote"] = "remote"
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_hub_source(self) -> "HubDatasetRequest":
        """拒绝 URL、本地路径和非法仓库标识，下载器只能访问 Hub 仓库。"""
        from huggingface_hub.utils import validate_repo_id

        validate_repo_id(self.repo_id)
        if self.subset and (not self.subset.strip() or any(ord(char) < 32 for char in self.subset)):
            raise ValueError("子集名称不能为空或包含控制字符")
        if any(not tag.strip() or len(tag) > 100 for tag in self.tags):
            raise ValueError("标签须为 1～100 个字符")
        return self


class PredictRequest(BaseModel):
    worker_id: str | None = Field(default=None, min_length=1, max_length=160)
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

    worker_id: str | None = Field(default=None, min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=32768)
    max_new_tokens: int = Field(default=32, ge=1, le=512)
    temperature: float = Field(default=0.0, ge=0, le=2.0)
    top_p: float = Field(default=0.9, gt=0, le=1.0)


class ProcessRequest(BaseModel):
    operations: list[dict[str, Any]]


class WorkerSelectionRequest(BaseModel):
    """外部计算槽接单选择请求。"""

    enabled: StrictBool


class RetryJobRequest(BaseModel):
    """失败任务的重试分配方式；worker_id 为空表示继续自动调度。"""

    worker_id: str | None = Field(default=None, min_length=1, max_length=160)


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
    device_backend: Literal["auto", "cuda", "rocm", "xpu", "mps", "cpu"] = "auto"
    cpu_slots: int = Field(default=2, ge=0, le=256)
    distributed_host: str = Field(default="", max_length=253, pattern=r"^[A-Za-z0-9.:-]*$")
    remote_redis_url: str | None = Field(default=None, max_length=4096)
    remote_s3_endpoint: str | None = Field(default=None, max_length=512)
    # 远程构建仓库源（可选，目标机构建 worker 镜像时注入加速源）
    docker_registry: str | None = Field(default=None, max_length=253)
    pip_index_url: str | None = Field(default=None, max_length=2048)
    apt_mirror: str | None = Field(default=None, max_length=253)

    @model_validator(mode="after")
    def validate_deployment_overrides(self) -> "RemoteServerRequest":
        """拒绝远程部署配置中的换行和格式错误值。"""
        if self.remote_redis_url and not self.remote_redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("Redis 地址必须以 redis:// 或 rediss:// 开头")
        if self.remote_redis_url and any(char.isspace() or ord(char) < 32 for char in self.remote_redis_url):
            raise ValueError("Redis 地址不能包含空白或控制字符")
        if self.remote_s3_endpoint and any(char.isspace() or ord(char) < 32 for char in self.remote_s3_endpoint):
            raise ValueError("S3 地址不能包含空白字符")
        if self.docker_registry and not re.fullmatch(r"[A-Za-z0-9.-]+(:[0-9]+)?", self.docker_registry):
            raise ValueError("镜像加速源必须是主机[:端口]格式，例如 docker.m.daocloud.io")
        if self.pip_index_url and not self.pip_index_url.startswith(("http://", "https://")):
            raise ValueError("pip 源必须以 http:// 或 https:// 开头")
        if self.pip_index_url and any(char.isspace() or ord(char) < 32 for char in self.pip_index_url):
            raise ValueError("pip 源不能包含空白或控制字符")
        if self.apt_mirror and not re.fullmatch(r"[A-Za-z0-9.-]+(:[0-9]+)?", self.apt_mirror):
            raise ValueError("apt 源必须是主机[:端口]格式，例如 mirrors.aliyun.com")
        return self


class RemoteServerUpdateRequest(BaseModel):
    """编辑已登记服务器的连接信息和单机部署配置覆盖值。"""

    host: str | None = Field(default=None, min_length=1, max_length=253, pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
    username: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    ssh_credential_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    port: int | None = Field(default=None, ge=1, le=65535)
    mode: Literal["cpu", "gpu"] | None = None
    device_backend: Literal["auto", "cuda", "rocm", "xpu", "mps", "cpu"] | None = None
    cpu_slots: int | None = Field(default=None, ge=0, le=256)
    distributed_host: str | None = Field(default=None, max_length=253, pattern=r"^[A-Za-z0-9.:-]*$")
    remote_redis_url: str | None = Field(default=None, max_length=4096)
    remote_s3_endpoint: str | None = Field(default=None, max_length=512)
    clear_remote_redis_url_override: bool = False
    # 远程构建仓库源（可选，目标机构建 worker 镜像时注入加速源）
    docker_registry: str | None = Field(default=None, max_length=253)
    pip_index_url: str | None = Field(default=None, max_length=2048)
    apt_mirror: str | None = Field(default=None, max_length=253)

    @model_validator(mode="after")
    def validate_deployment_overrides(self) -> "RemoteServerUpdateRequest":
        """拒绝编辑请求中的换行和格式错误值。"""
        if self.remote_redis_url and not self.remote_redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("Redis 地址必须以 redis:// 或 rediss:// 开头")
        if self.remote_redis_url and any(char.isspace() or ord(char) < 32 for char in self.remote_redis_url):
            raise ValueError("Redis 地址不能包含空白或控制字符")
        if self.remote_s3_endpoint and any(char.isspace() or ord(char) < 32 for char in self.remote_s3_endpoint):
            raise ValueError("S3 地址不能包含空白字符")
        if self.docker_registry and not re.fullmatch(r"[A-Za-z0-9.-]+(:[0-9]+)?", self.docker_registry):
            raise ValueError("镜像加速源必须是主机[:端口]格式，例如 docker.m.daocloud.io")
        if self.pip_index_url and not self.pip_index_url.startswith(("http://", "https://")):
            raise ValueError("pip 源必须以 http:// 或 https:// 开头")
        if self.pip_index_url and any(char.isspace() or ord(char) < 32 for char in self.pip_index_url):
            raise ValueError("pip 源不能包含空白或控制字符")
        if self.apt_mirror and not re.fullmatch(r"[A-Za-z0-9.-]+(:[0-9]+)?", self.apt_mirror):
            raise ValueError("apt 源必须是主机[:端口]格式，例如 mirrors.aliyun.com")
        return self


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
