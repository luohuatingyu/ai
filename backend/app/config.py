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

for _d in (DATA_DIR, DATASETS_DIR, JOBS_DIR):
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
