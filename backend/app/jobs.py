"""训练任务管理：线程池执行、状态机、日志/指标文件、磁盘持久化。

数据全部落在 ./data/jobs/<job_id>/ 下：
- job.json       任务元信息+状态（每次变更原子覆盖写）
- log.txt        追加式日志
- metrics.jsonl  逐 epoch 指标
- best.pt/last.pt/preprocessor.json/summary.json 由训练器产出
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import app.config as config

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
Terminal = {COMPLETED, FAILED, CANCELLED}


def _now() -> float:
    return time.time()


def _persist(job: "Job") -> None:
    path = job.dir / "job.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "id": job.id,
                "spec": job.spec,
                "status": job.status,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "error": job.error,
                "device": job.device,
                "summary": job.summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def _append_log(job: "Job", msg: str) -> None:
    try:
        stamp = time.strftime("%H:%M:%S")
        with job.log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except OSError:
        pass


def read_metrics(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


class Job:
    def __init__(self, spec: dict):
        self.id = f"job-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.spec: dict = spec
        self.status: str = QUEUED
        self.created_at: float = _now()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.error: str | None = None
        self.device: str | None = None
        self.summary: dict | None = None
        self.cancel_event = threading.Event()

    @property
    def dir(self) -> Path:
        return config.JOBS_DIR / self.id

    @property
    def log_path(self) -> Path:
        return self.dir / "log.txt"

    @property
    def metrics_path(self) -> Path:
        return self.dir / "metrics.jsonl"

    def api_view(self, include_metrics: bool = False, include_summary: bool = False) -> dict:
        data = {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "device": self.device,
            "spec": self.spec,
        }
        if include_summary:
            data["summary"] = self.summary
        if include_metrics:
            data["metrics"] = read_metrics(self.metrics_path)
        return data


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="train")
        self._load_existing()

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------

    def submit(self, spec: dict) -> Job:
        job = Job(spec)
        job.dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs[job.id] = job
        _persist(job)
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job.status in Terminal:
            raise ValueError("任务已结束，无需取消")
        job.cancel_event.set()
        _append_log(job, "收到取消请求，将在当前批次结束后停止…")
        return job

    def delete(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job and job.status in (QUEUED, RUNNING):
            raise ValueError("请先取消运行中的任务再删除")
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(config.JOBS_DIR / job_id, ignore_errors=True)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _load_existing(self) -> None:
        """重启后从磁盘恢复任务历史；中断的任务标记为 failed。"""
        for job_dir in sorted(config.JOBS_DIR.glob("job-*")):
            path = job_dir / "job.json"
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job = Job(raw.get("spec", {}))
            job.id = raw.get("id", job.id)
            job.status = raw.get("status", FAILED)
            job.created_at = raw.get("created_at", _now())
            job.started_at = raw.get("started_at")
            job.finished_at = raw.get("finished_at")
            job.error = raw.get("error")
            job.device = raw.get("device")
            job.summary = raw.get("summary")
            if job.status in (QUEUED, RUNNING):
                job.status = FAILED
                job.error = "服务重启，任务中断"
                if not job.finished_at:
                    job.finished_at = _now()
                _append_log(job, "服务重启，任务标记为失败")
                _persist(job)
            self._jobs[job.id] = job

    def _run(self, job: Job) -> None:
        job.status = RUNNING
        job.started_at = _now()
        _append_log(job, "任务开始")
        _persist(job)

        spec = job.spec
        try:
            from app.trainer import JobCancelled, run_training

            summary = run_training(
                dataset_path=config.DATASETS_DIR / f"{spec['dataset_id']}.csv",
                target=spec["target"],
                task=spec["task"],
                params=spec["params"],
                cancel_event=job.cancel_event,
                log_fn=lambda msg: _append_log(job, msg),
                job_dir=job.dir,
            )
            job.device = summary.get("device")
            job.summary = summary
            job.status = COMPLETED
        except JobCancelled:
            job.status = CANCELLED
            _append_log(job, "任务已取消")
        except Exception as exc:  # noqa: BLE001
            job.status = FAILED
            job.error = str(exc)
            _append_log(job, f"任务失败: {exc}")
            _append_log(job, traceback.format_exc())
        finally:
            job.finished_at = _now()
            _persist(job)
