"""平台路径等基础配置。"""
from __future__ import annotations

import os
from pathlib import Path

# 数据目录解析（优先级：环境变量 > 容器默认 > 本地默认）：
# 1. 容器内（存在 /app 目录）默认 /app/data，由 docker-compose 挂载宿主机 ./data 持久化；
# 2. 本地直跑（无 /app 目录）自动回退到项目根目录下的 data/，保证不经 Docker 也能运行。
_LOCAL_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DEFAULT_DATA_DIR = "/app/data" if Path("/app").is_dir() else str(_LOCAL_DATA_DIR)
DATA_DIR = Path(os.environ.get("DATA_DIR", _DEFAULT_DATA_DIR))
DATASETS_DIR = DATA_DIR / "datasets"
JOBS_DIR = DATA_DIR / "jobs"

# 代码内置目录（随镜像打包）
APP_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_ROOT / "static"
SAMPLE_DIR = APP_ROOT / "sample_data"

# ---------------------------------------------------------------------------
# 多机训练架构：控制/状态/计算三平面分离
# - 控制平面（API）：只负责接收请求、入队、查询状态，本身不训练
# - 状态平面：Redis（队列+租约+心跳）+ 对象存储（数据集与训练产物）
# - 计算平面：无状态 GPU Worker，随时可释放/替换，凭 checkpoint 接手续训
# ---------------------------------------------------------------------------

# 存储后端：local=本地文件系统（单机零依赖）；s3=S3 兼容对象存储（多机共享状态）
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").strip().lower() or "local"

# Redis 连接地址：留空时启用“嵌入式模式”（API 进程内置 worker 线程，无需任何外部组件）
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "").strip()

# S3 兼容对象存储配置（STORAGE_BACKEND=s3 时生效）。
# 适用于任意 S3 协议服务：SeaweedFS / 阿里云 OSS(S3 网关) / 腾讯云 COS / AWS S3 等，
# 例如：
#   SeaweedFS: S3_ENDPOINT=localhost:9000
#   阿里云 OSS: S3_ENDPOINT=s3.oss-cn-hangzhou.aliyuncs.com  S3_SECURE=true
#   腾讯云 COS: S3_ENDPOINT=cos.ap-guangzhou.myqcloud.com    S3_SECURE=true
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "localhost:9000").strip()
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin").strip()
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin").strip()
S3_BUCKET = os.environ.get("S3_BUCKET", "mtp-artifacts").strip()
S3_SECURE = os.environ.get("S3_SECURE", "false").strip().lower() == "true"

# Worker（计算单元）配置
WORKER_ID = os.environ.get("WORKER_ID", "").strip()  # 独立 worker 身份标识（空则自动生成）
WORKER_MACHINE_ID = os.environ.get("WORKER_MACHINE_ID", "").strip()  # 外部计算服务器的稳定显示名
WORKER_MANAGED = os.environ.get("WORKER_MANAGED", "false").strip().lower() == "true"  # 新接入槽位默认待人工启用
WORKER_SCRATCH_DIR = DATA_DIR / "worker-scratch"       # 本地临时缓存（scratch），可随时清空
WORKER_HEARTBEAT_INTERVAL = float(os.environ.get("WORKER_HEARTBEAT_INTERVAL", "10"))   # 心跳周期（秒）
WORKER_LEASE_TTL = float(os.environ.get("WORKER_LEASE_TTL", "30"))                     # 租约有效期（秒）
EMBEDDED_WORKERS = int(os.environ.get("EMBEDDED_WORKERS", "2"))                         # 嵌入式模式 worker 线程数
WORKER_CHECKPOINT_INTERVAL = int(os.environ.get("WORKER_CHECKPOINT_INTERVAL", "5"))     # 每训练 N 个 epoch 存一次 checkpoint
WORKER_CHECKPOINT_KEEP = int(os.environ.get("WORKER_CHECKPOINT_KEEP", "3"))             # 版本化 checkpoint 保留个数（可回退）

# 多机多卡：一卡一进程的机器级守护（Supervisor）与卡健康自愈
WORKER_SUPERVISOR_PROBE_INTERVAL = float(os.environ.get("WORKER_SUPERVISOR_PROBE_INTERVAL", "15"))  # 掉卡探测周期（秒）
WORKER_GPU_PROBE = os.environ.get("WORKER_GPU_PROBE", "true").strip().lower() == "true"  # 心跳周期内是否做 GPU 软探测
WORKER_CPU_SLOTS = int(os.environ.get("WORKER_CPU_SLOTS", "1"))  # Supervisor 额外提供的 CPU 执行槽数（纯 CPU 训练）

# 远程计算服务器管理：默认关闭；SSH 凭证可由页面加密保存，也兼容环境变量回退。
WORKER_REMOTE_ENABLED = os.environ.get("WORKER_REMOTE_ENABLED", "false").strip().lower() == "true"
WORKER_REMOTE_TOKEN = os.environ.get("WORKER_REMOTE_TOKEN", "").strip()
WORKER_REMOTE_IMAGE = os.environ.get("WORKER_IMAGE", "").strip()
WORKER_REMOTE_REDIS_URL = os.environ.get("WORKER_REMOTE_REDIS_URL", "").strip()
WORKER_REMOTE_S3_ENDPOINT = os.environ.get("WORKER_REMOTE_S3_ENDPOINT", "").strip()
WORKER_SSH_AUTH_METHOD = os.environ.get("WORKER_SSH_AUTH_METHOD", "key").strip().lower()
WORKER_SSH_PASSWORD = os.environ.get("WORKER_SSH_PASSWORD", "")
WORKER_SSH_KEY_PATH = os.environ.get("WORKER_SSH_KEY_PATH", "/run/remote-worker/ssh_key").strip()
WORKER_SSH_KEY_PASSPHRASE = os.environ.get("WORKER_SSH_KEY_PASSPHRASE", "")
WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY = os.environ.get(
    "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", ""
).strip()
WORKER_SSH_KNOWN_HOSTS_PATH = os.environ.get(
    "WORKER_SSH_KNOWN_HOSTS_PATH", "/run/remote-worker/known_hosts"
).strip()

# 任务失败分类重试：环境性错误（CUDA/OOM/网络瞬断等）带 checkpoint 自动续试，不影响整体完成
JOB_MAX_AUTO_RETRY = int(os.environ.get("JOB_MAX_AUTO_RETRY", "5"))       # 自动重试上限（.env 可配，默认 5）
JOB_RETRY_BACKOFF = float(os.environ.get("JOB_RETRY_BACKOFF", "2"))       # 重试退避（秒）
WATCHDOG_INTERVAL = float(os.environ.get("WATCHDOG_INTERVAL", "5"))       # watchdog 巡检周期（秒）：重派心跳迷失任务 / 防队列丢失
WORKER_STALE_CLEANUP_AGE = int(os.environ.get("WORKER_STALE_CLEANUP_AGE", "300"))  # 崩溃残留 worker 注册的清理宽限期（秒）

# 运行期目录统一创建（幂等）
for _d in (DATA_DIR, DATASETS_DIR, JOBS_DIR, WORKER_SCRATCH_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 数据库（MySQL 可选镜像）配置：全项目唯一的 DB 配置来源
# ---------------------------------------------------------------------------
USE_DATABASE = os.environ.get("USE_DATABASE", "true").lower() == "true"
DB_HOST = os.environ.get("DB_HOST", "localhost").strip()
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("DB_USER", "root").strip()
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_NAME = os.environ.get("DB_NAME", "model_training").strip()
DB_CHARSET = os.environ.get("DB_CHARSET", "utf8mb4").strip()
DB_COLLATION = os.environ.get("DB_COLLATION", "utf8mb4_unicode_ci").strip()
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
DB_POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "3600"))
DB_ECHO = os.environ.get("DB_ECHO", "false").strip().lower() == "true"

# 数据库路径是否启用（镜像写失败一律静默，文件权威源不受影响）
DB_AVAILABLE = USE_DATABASE
