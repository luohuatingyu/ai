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

# 存储后端：local=本地文件系统（单机零依赖）；minio=S3 兼容对象存储（多机共享状态）
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").strip().lower() or "local"

# Redis 连接地址：留空时启用“嵌入式模式”（API 进程内置 worker 线程，无需任何外部组件）
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "").strip()

# MinIO / S3 兼容对象存储配置（STORAGE_BACKEND=minio 时生效）
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000").strip()
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin").strip()
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin").strip()
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "training-platform").strip()
MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").strip().lower() == "true"

# Worker（计算单元）配置
WORKER_ID = os.environ.get("WORKER_ID", "").strip()  # 独立 worker 身份标识（空则自动生成）
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

# 任务失败分类重试：环境性错误（CUDA/OOM/网络瞬断等）带 checkpoint 自动续试，不影响整体完成
JOB_MAX_AUTO_RETRY = int(os.environ.get("JOB_MAX_AUTO_RETRY", "5"))       # 自动重试上限（.env 可配，默认 5）
JOB_RETRY_BACKOFF = float(os.environ.get("JOB_RETRY_BACKOFF", "2"))       # 重试退避（秒）

# 运行期目录统一创建（幂等）
for _d in (DATA_DIR, DATASETS_DIR, JOBS_DIR, WORKER_SCRATCH_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 数据库配置
USE_DATABASE = os.environ.get("USE_DATABASE", "true").lower() == "true"
if USE_DATABASE:
    try:
        from app.database import DATABASE_URL
        DB_AVAILABLE = True
    except ImportError:
        DB_AVAILABLE = False
else:
    DB_AVAILABLE = False
