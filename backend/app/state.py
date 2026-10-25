"""应用级单例（控制面共享状态）。

由 main.py 导入时创建一次；各 routers 通过本模块引用，
避免路由模块相互导入造成循环依赖。
"""
from __future__ import annotations

import app.config as config
from app.data_processor import DataProcessor
from app.dataset_importer import DatasetImporter
from app.dataset_manager import DatasetManager
from app.jobs import JobManager

# 任务控制面（状态机 + watchdog + 嵌入式 worker 线程）
manager = JobManager()

# 数据集领域组件
dataset_manager = DatasetManager(config.DATASETS_DIR)
data_processor = DataProcessor()
dataset_importer = DatasetImporter(config.DATASETS_DIR)
