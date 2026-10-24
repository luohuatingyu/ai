"""平台路径等基础配置。"""
from __future__ import annotations

import os
from pathlib import Path

# 容器内默认 /app/data，通过 docker-compose 挂载到宿主机 ./data 以持久化
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
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
