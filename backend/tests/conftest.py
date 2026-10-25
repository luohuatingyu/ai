"""端到端公共夹具：环境隔离、数据集/任务工厂、worker 驱动助手。

在导入任何 app 模块之前固化环境变量（config 于导入期固化路径），
本套件统一运行在「单机零依赖」形态：local 状态平面 + 嵌入式协调面。
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---- 环境先于一切 app 导入 ----
_TMP = Path(tempfile.mkdtemp(prefix="mtp-pytest-"))
os.environ.update(
    {
        "DATA_DIR": str(_TMP),
        "USE_DATABASE": "false",
        "STORAGE_BACKEND": "local",
        "REDIS_URL": os.environ.get("MTP_TEST_REDIS", ""),  # 未配置=嵌入式；配置=多机 Redis 模式
        "EMBEDDED_WORKERS": "2",  # 嵌入 worker 线程数（API 模块测试用）
        "WORKER_CPU_SLOTS": "2",  # Supervisor E2E 需要 >=2 个槽验证并行
        "WORKER_MANAGED": "false",  # 测试夹具中的 worker 保持原有自动接单语义
        "WORKER_MACHINE_ID": "",
        "WORKER_HEARTBEAT_INTERVAL": "0.3",
        "WORKER_LEASE_TTL": "2",
        "WORKER_CHECKPOINT_INTERVAL": "2",
        "WORKER_CHECKPOINT_KEEP": "3",
        "JOB_MAX_AUTO_RETRY": "3",
        "JOB_RETRY_BACKOFF": "0.2",
        "WATCHDOG_INTERVAL": "1",
    }
)
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

sys_path_added = str(Path(__file__).resolve().parents[1])
if sys_path_added not in sys.path:
    sys.path.insert(0, sys_path_added)

from app import config, jobstore, worker as worker_mod  # noqa: E402
from app.coordination import EmbeddedJobQueue, EmbeddedLeaseStore  # noqa: E402
from app.storage import get_store, job_key  # noqa: E402


def wait_until(pred, timeout: float = 30.0, step: float = 0.1) -> bool:
    """轮询等待条件成立（测试专用）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


@pytest.fixture()
def store():
    """local 状态平面：每个测试前清空数据目录，保证用例互不污染。"""
    for sub in (config.DATASETS_DIR, config.JOBS_DIR, config.WORKER_SCRATCH_DIR):
        if sub.exists():
            shutil.rmtree(sub, ignore_errors=True)
        sub.mkdir(parents=True, exist_ok=True)
    return get_store()


@pytest.fixture()
def q():
    return EmbeddedJobQueue()


@pytest.fixture()
def ls():
    return EmbeddedLeaseStore()


@pytest.fixture()
def make_dataset(store):
    """数据集工厂：旧扁平布局 {id}.csv + {id}.meta.json；kind 控制 label 类型。"""
    counter = {"n": 0}

    def _make(kind: str = "classification", n: int = 300, cols: int = 3, name: str = None):
        counter["n"] += 1
        ds_id = name or f"ds-{uuid.uuid4().hex[:8]}"
        rng = np.random.RandomState(7)
        data = {f"f{i+1}": rng.randn(n) for i in range(cols)}
        if kind == "classification":
            data["target"] = rng.randint(0, 3, n)
        else:
            data["target"] = rng.randn(n) * 10
        df = pd.DataFrame(data)
        config.DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(config.DATASETS_DIR / f"{ds_id}.csv", index=False)
        meta = {"id": ds_id, "name": ds_id, "columns": list(df.columns),
                "row_count": n, "target": "target", "task": kind}
        config.DATASETS_DIR.joinpath(f"{ds_id}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return ds_id

    return _make


@pytest.fixture()
def make_job(store):
    """任务记录工厂：直接写状态平面，返回 job_id。"""
    counter = {"n": 0}

    def _make(ds_id: str, epochs: int = 6, require_gpu: bool = False,
              min_vram_mb: int = 0, task: str = "classification", job_id: str = None,
              **params) -> str:
        counter["n"] += 1
        job_id = job_id or f"job-{counter['n']:03d}-{uuid.uuid4().hex[:6]}"
        record = make_job_record(job_id, ds_id=ds_id, epochs=epochs,
                                 require_gpu=require_gpu, min_vram_mb=min_vram_mb,
                                 task=task, **params)
        jobstore.write_job(store, record)
        return job_id

    return _make


class WorkerHandle:
    """后台 worker 线程句柄。"""

    def __init__(self, worker: "worker_mod.TrainingWorker", thread: threading.Thread):
        self.worker = worker
        self.thread = thread

    def stop(self):
        self.worker.request_shutdown()
        self.thread.join(timeout=8)


@pytest.fixture()
def spawn_worker(store, q, ls):
    """启动后台 TrainingWorker（可指定能力，模拟不同机器/卡）。"""
    handles = []

    def _spawn(name: str = None, capabilities: dict = None, idle_poll: float = 0.05) -> WorkerHandle:
        caps = capabilities or worker_mod.detect_capabilities(name)
        w = worker_mod.TrainingWorker(store=store, queue=q, lease_store=ls,
                                      capabilities=caps, idle_poll=idle_poll,
                                      gpu_probe_enabled=False)
        t = threading.Thread(target=w.run_forever, daemon=True)
        t.start()
        h = WorkerHandle(w, t)
        handles.append(h)
        return h

    yield _spawn
    for h in handles:
        h.stop()


def job_status(store, job_id: str):
    rec = jobstore.read_job(store, job_id)
    return None if rec is None else rec.get("status")


def job_epochs(store, job_id: str):
    return [r["epoch"] for r in jobstore.read_metrics(store, job_id)]


def make_job_record(job_id: str, ds_id: str = "ds-test", epochs: int = 6,
                    require_gpu: bool = False, min_vram_mb: int = 0,
                    task: str = "classification", **params) -> dict:
    """构造标准任务记录（测试用最小合法 spec）。"""
    spec = {
        "dataset_id": ds_id, "dataset_name": ds_id, "target": "target",
        "task": task,
        "params": {"epochs": epochs, "batch_size": 32, "lr": 0.01,
                   "hidden_size": 16, "hidden_layers": 1, "test_split": 0.2,
                   "seed": 42, **params},
        "resource_hint": {"require_gpu": require_gpu, "min_vram_mb": min_vram_mb},
    }
    return {
        "id": job_id, "spec": spec, "status": jobstore.QUEUED,
        "created_at": time.time(), "started_at": None, "finished_at": None,
        "error": None, "device": None, "summary": None, "segments": [],
        "worker_id": None, "retry_count": 0, "last_error": None,
        "redispatched": 0,
    }
