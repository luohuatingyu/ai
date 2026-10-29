"""训练任务管理（状态平面版）。

职责分离后的 JobManager 是控制平面的"任务登记处"，只做四件事：
1. 任务创建：写 job.json（状态平面的权威记录）→ 入队 → 尽力镜像数据库；
2. 任务控制：cancel/pause/resume 通过协调平面信号与状态流转表达；
3. 任务查询：全部从状态平面读取（API/UI 与 Worker 看到同一份数据）；
4. Watchdog：周期巡检——心跳迷失的 running 任务自动重派（不消耗重试额度）；
   排队任务不在队列中时补投（防丢失）。

训练执行本身不在本模块：由 Worker（独立进程或嵌入式线程）领取；
"文件优先、DB 可选镜像"哲学保留——job.json 是唯一权威，数据库只做镜像。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, List, Optional

import app.config as config
from app import jobstore
from app.coordination import build_coordination
from app.jobstore import (
    CANCELLED,
    COMPLETED,
    FAILED,
    PAUSED,
    QUEUED,
    RUNNING,
)
from app.storage import ArtifactStore, get_store, job_key
from app.worker import TrainingWorker, detect_capabilities

TERMINAL = {COMPLETED, FAILED, CANCELLED}

_LEGACY_DELETABLE = {QUEUED, PAUSED, FAILED, CANCELLED, COMPLETED}


def api_view(
    record: dict,
    include_metrics: bool = False,
    include_summary: bool = False,
    store: Optional[ArtifactStore] = None,
) -> dict:
    """任务记录的 API 视图（与旧 Job.api_view 兼容，并补充多卡相关字段）。"""
    data = {
        "id": record.get("id"),
        "status": record.get("status"),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "error": record.get("error"),
        "device": record.get("device"),
        "machine_id": record.get("machine_id"),
        "slot": record.get("slot"),
        "current_attempt": record.get("current_attempt"),
        "spec": record.get("spec") or {},
        "resource_hint": (record.get("spec") or {}).get("resource_hint") or {},
        "worker_id": record.get("worker_id"),
        "retry_worker_id": record.get("retry_worker_id"),
        "retry_assignment_mode": record.get("retry_assignment_mode") or "auto",
        "manual_retry_count": record.get("manual_retry_count") or 0,
        "retry_count": record.get("retry_count") or 0,
        "last_error": record.get("last_error"),
        "redispatched": record.get("redispatched") or 0,
        "segments": record.get("segments") or [],
    }
    if include_summary:
        data["summary"] = record.get("summary")
    if include_metrics:
        store = store or get_store()
        data["metrics"] = jobstore.read_metrics(store, record.get("id") or "")
    return data


class JobManager:
    """控制平面任务登记处（含嵌入式 worker 线程与 watchdog 的宿主）。

    分布式模式（REDIS_URL 配置时）：只做登记/控制/watchdog，训练由独立
    worker 进程（一卡一进程）领取；
    嵌入式模式（无 Redis，单机零依赖）：额外启动 EMBEDDED_WORKERS 个
    TrainingWorker 线程，API 进程内直接消费队列。
    """

    def __init__(
        self,
        store: Optional[ArtifactStore] = None,
        queue=None,
        lease=None,
        start_workers: Optional[bool] = None,
        start_watchdog: bool = True,
    ) -> None:
        self.store = store or get_store()
        if queue is None or lease is None:
            built_queue, built_lease, distributed = build_coordination()
            self.queue = queue if queue is not None else built_queue
            self.lease = lease if lease is not None else built_lease
            self.distributed = distributed
        else:
            self.queue = queue
            self.lease = lease
            self.distributed = config.REDIS_URL != ""
        self._db_available = config.DB_AVAILABLE
        self._stop = threading.Event()
        self._workers: List[TrainingWorker] = []
        self._threads: List[threading.Thread] = []
        self._recover_on_restart()
        self._start_watchdog = start_watchdog
        self._watchdog_thread: Optional[threading.Thread] = None
        if start_watchdog:
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name="job-watchdog", daemon=True
            )
            self._watchdog_thread.start()
        # 嵌入式模式：API 进程内直接起 worker 线程消费队列（单机零依赖）
        if start_workers is None:
            start_workers = not self.distributed
        if start_workers:
            self._start_embedded_workers()

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------

    def submit(self, spec: dict) -> dict:
        """创建任务：写权威 job.json → 入队 → 尽力镜像数据库。"""
        for required in ("dataset_id", "task"):
            if not spec.get(required):
                raise ValueError(f"缺少必填字段: {required}")
        if spec.get("task") not in {
            "clustering", "llm_pretraining", "llm_sft", "llm_dpo", "llm_ppo"
        } and not spec.get("target"):
            raise ValueError("缺少必填字段: target")
        job_id = f"job-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        spec = dict(spec)
        spec["target"] = spec.get("target") or ""
        spec.setdefault("dataset_name", spec["dataset_id"])
        spec.setdefault("resource_hint", {})  # resource_hint: {require_gpu, min_vram_mb}
        record = {
            "id": job_id,
            "spec": spec,
            "status": QUEUED,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "device": None,
            "summary": None,
            "segments": [],
            "worker_id": None,
            "machine_id": None,
            "slot": None,
            "current_attempt": None,
            "retry_worker_id": None,
            "retry_assignment_mode": "auto",
            "retry_count": 0,
            "manual_retry_count": 0,
            "last_error": None,
            "redispatched": 0,
        }
        jobstore.write_job(self.store, record)
        self.queue.push(job_id)
        jobstore.append_job_log(self.store, job_id, "任务创建，已进入队列")
        self._mirror_create(spec, job_id)
        return record

    def get(self, job_id: str) -> Optional[dict]:
        """读取任务记录（权威源 job.json）。"""
        return jobstore.read_job(self.store, job_id)

    def list(self) -> List[dict]:
        """列出全部任务（按创建时间倒序）。"""
        records: List[dict] = []
        for job_id in jobstore.list_job_ids(self.store):
            record = jobstore.read_job(self.store, job_id)
            if record is not None:
                records.append(record)
        records.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
        return records

    def cancel(self, job_id: str) -> dict:
        """取消任务：running 走协调平面信号（存档并停止），排队/暂停直接标记。"""
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        status = record.get("status")
        if status in TERMINAL:
            raise ValueError("任务已结束，无需取消")
        if status == RUNNING:
            # 抢占式取消信号：控制线程逐 epoch/批次读取，保存断点后停止
            self.lease.set_signal(job_id, "cancel")
            jobstore.append_job_log(self.store, job_id, "收到取消请求，将在当前批次结束后停止…")
            return jobstore.update_job(self.store, job_id)
        # queued/paused：直接终态（排队条目会被 worker 按状态丢弃）
        updated = jobstore.update_job(self.store, job_id, status=CANCELLED,
                                      finished_at=time.time())
        jobstore.append_job_log(self.store, job_id, "任务已取消（未开始训练）")
        return updated

    def pause(self, job_id: str) -> dict:
        """暂停任务：running 发信号（checkpoint 后转 paused）；queued 直接转。"""
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        status = record.get("status")
        if status not in (RUNNING, QUEUED):
            raise ValueError("仅排队中或训练中的任务可暂停")
        if status == RUNNING:
            self.lease.set_signal(job_id, "pause")
            jobstore.append_job_log(self.store, job_id, "收到暂停请求，正在保存断点…")
            return jobstore.update_job(self.store, job_id)
        updated = jobstore.update_job(self.store, job_id, status=PAUSED)
        jobstore.append_job_log(self.store, job_id, "排队任务已暂停")
        return updated

    def resume(self, job_id: str) -> dict:
        """恢复暂停任务，保留指定槽或自动调度策略并从断点接续。"""
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        if record.get("status") != PAUSED:
            raise ValueError("仅暂停中的任务可恢复")
        updated = jobstore.update_job(self.store, job_id, status=QUEUED)
        self.queue.push(job_id)
        jobstore.append_job_log(
            self.store, job_id,
            "任务恢复：重新排队，" + (
                f"等待指定计算槽 {record['spec']['resource_hint']['worker_id']} 从最近 checkpoint 接续训练"
                if ((record.get("spec") or {}).get("resource_hint") or {}).get("worker_id")
                else "任意可用卡/机器将从最近 checkpoint 接续训练"
            ),
        )
        return updated

    def retry(self, job_id: str, worker_id: Optional[str] = None) -> dict:
        """重试失败任务；可将下一次执行固定到一个 worker 或交回自动调度。"""
        record = self.get(job_id)
        if worker_id and record and ((record.get("spec") or {}).get("resource_hint") or {}).get("parallel_workers"):
            raise ValueError("联合训练必须按原成员整组重试")
        token = f"manual-retry:{uuid.uuid4().hex}"
        if not self.lease.acquire(
            job_id, token, max(float(config.WORKER_LEASE_TTL), 10.0)
        ):
            raise RuntimeError("任务租约仍有效，或另一个重试请求正在处理")
        try:
            record = self.get(job_id)
            if record is None:
                raise KeyError(job_id)
            if record.get("status") != FAILED:
                raise ValueError("仅失败任务可重新排队")

            # 定向训练的手动重试显式更新策略；旧任务仍保持单次重试选槽语义。
            spec = dict(record.get("spec") or {})
            hint = dict(spec.get("resource_hint") or {})
            if hint.get("worker_id"):
                if worker_id:
                    hint["worker_id"] = worker_id
                else:
                    hint.pop("worker_id", None)
                spec["resource_hint"] = hint
            previous_error = record.get("error") or record.get("last_error")
            assignment_mode = "worker" if worker_id else "auto"
            updated = jobstore.update_job(
                self.store,
                job_id,
                status=QUEUED,
                error=None,
                finished_at=None,
                current_attempt=None,
                retry_count=0,
                manual_retry_count=int(record.get("manual_retry_count") or 0) + 1,
                last_error=previous_error,
                retry_worker_id=worker_id,
                retry_assignment_mode=assignment_mode,
                retry_requested_at=time.time(),
                spec=spec,
            )
            jobstore.append_job_log(
                self.store,
                job_id,
                "手动重试：" + (
                    f"指定计算槽 {worker_id}"
                    if worker_id
                    else "使用自动资源分配"
                ) + "；自动重试额度已重新计数",
            )
            self.queue.push(job_id)
            return updated
        finally:
            self.lease.release(job_id, token)

    def delete(self, job_id: str) -> None:
        """删除任务及其全部产物（终态或暂停/排队任务；running 需先取消）。"""
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        if record.get("status") == RUNNING:
            raise ValueError("请先取消运行中的任务再删除")
        jobstore.delete_job_artifacts(self.store, job_id)
        self._mirror_delete(job_id)

    def stop(self) -> None:
        """停掉 watchdog 与嵌入式 worker（测试/优雅退出用）。"""
        self._stop.set()
        for worker in self._workers:
            worker.request_shutdown(pause_current=False)
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=5)

    # ------------------------------------------------------------------
    # 内部：重启恢复 / 数据库镜像 / watchdog / 嵌入式 worker
    # ------------------------------------------------------------------

    def _recover_on_restart(self) -> None:
        """重启恢复；中断态处理：
        - running：watchdog 之后按租约状态自动重派（不在此处臆断失败）；
        - queued：后续 watchdog 检测缺失队列条目时补投；
        - paused/completed/failed/cancelled：保持原状。
        """
        for job_id in jobstore.list_job_ids(self.store):
            record = jobstore.read_job(self.store, job_id)
            if record is None:
                continue
            if record.get("status") == FAILED and record.get("error") == "服务重启，任务中断":
                # 兼容旧逻辑留下的状态：重新排队（有 checkpoint 则续训）
                jobstore.update_job(self.store, job_id, status=QUEUED, error=None)

    def _mirror_create(self, spec: dict, job_id: str) -> None:
        """任务创建时的数据库镜像（失败静默，不影响文件权威源）。"""
        if not self._db_available:
            return
        try:
            from app.database import SessionLocal
            from app.db_utils import save_job_to_db

            with SessionLocal() as db:
                save_job_to_db(
                    db=db,
                    job_id=job_id,
                    dataset_id=spec["dataset_id"],
                    dataset_name=spec.get("dataset_name", spec["dataset_id"]),
                    target=spec["target"],
                    task=spec["task"],
                    params=spec.get("params") or {},
                )
        except Exception as exc:
            jobstore.append_job_log(self.store, job_id, f"数据库保存失败: {exc}")

    def _mirror_delete(self, job_id: str) -> None:
        """删除任务时的数据库镜像清理（失败静默；DB 表是镜像，可重建）。

        先删子表 job_logs（外键引用 training_jobs），再删父表任务行，
        否则会触发 FK 约束失败导致镜像残留。
        """
        if not self._db_available:
            return
        try:
            from app.database import SessionLocal
            from app.db_utils import get_job_from_db
            from app.models import JobLog

            with SessionLocal() as db:
                row = get_job_from_db(db, job_id)
                if row is not None:
                    db.query(JobLog).filter(JobLog.job_id == job_id).delete()
                    db.delete(row)
                    db.commit()
        except Exception:
            pass

    def _watchdog_loop(self) -> None:
        """watchdog 主循环：周期巡检 running 租约与排队条目防丢失。"""
        interval = max(float(config.WATCHDOG_INTERVAL), 1.0)
        while not self._stop.wait(interval):
            try:
                self._watchdog_once()
            except Exception:
                # watchdog 自身异常不得中断巡检循环
                pass

    def _watchdog_once(self) -> None:
        """一轮巡检：
        - running 且租约不存在（执行槽崩溃/心跳迷失）→ 重新排队（不消耗重试额度）；
        - queued 但不在队列里 → 补投（防丢失；重复入队由领取方按状态跳过）；
        - 清理崩溃残留的 worker 注册条目（心跳长期未更新的幽灵槽）。
        """
        try:
            self.lease.cleanup_stale_workers(config.WORKER_STALE_CLEANUP_AGE)
        except Exception:
            pass  # 清理失败不影响本轮巡检主逻辑
        try:
            inflight = set(self.queue.snapshot())
        except Exception:
            inflight = None  # 队列观测失败时跳过补投，只做重派
        for job_id in jobstore.list_job_ids(self.store):
            record = jobstore.read_job(self.store, job_id)
            if record is None:
                continue
            status = record.get("status")
            if status == RUNNING and self.lease.peek_holder(job_id) is None:
                redispatched = int(record.get("redispatched") or 0) + 1
                jobstore.finish_current_attempt(
                    self.store,
                    job_id,
                    attempt_status="interrupted",
                    error="执行槽心跳丢失，任务由 watchdog 重新派发",
                    status=QUEUED,
                    worker_id=None,
                    machine_id=None,
                    slot=None,
                    retry_worker_id=None,
                    retry_assignment_mode="auto",
                    redispatched=redispatched,
                )
                jobstore.append_job_log(
                    self.store, job_id,
                    "watchdog: 执行槽心跳丢失（租约已过期），任务重新排队"
                    "——进度由 checkpoint 保留，" + (
                        f"等待指定计算槽 {record['spec']['resource_hint']['worker_id']} 恢复"
                        if ((record.get("spec") or {}).get("resource_hint") or {}).get("worker_id")
                        else "任意可用卡可接手"
                    ),
                )
                self.queue.push(job_id)
            elif status == QUEUED and inflight is not None and job_id not in inflight:
                self.queue.push(job_id)

    def _start_embedded_workers(self) -> None:
        """嵌入式模式：在 API 进程内启动 N 个 worker 线程消费队列。"""
        count = max(int(config.EMBEDDED_WORKERS), 1)
        for i in range(count):
            caps = detect_capabilities(f"embedded-{i}")
            worker = TrainingWorker(
                store=self.store, queue=self.queue, lease_store=self.lease,
                capabilities=caps,
                # 嵌入式线程同进程，无需过于频繁的心跳
                idle_poll=1.0,
                gpu_probe_enabled=False,
            )
            self._workers.append(worker)
            thread = threading.Thread(
                target=worker.run_forever, name=f"embedded-worker-{i}", daemon=True
            )
            self._threads.append(thread)
            thread.start()
