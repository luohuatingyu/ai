"""AC1/AC7（控制层）：JobManager——状态机控制、watchdog 重派、队列防丢失。

watchdog 是"机器或单卡不可用不影响整体任务完成"的最后防线：
- running 且租约过期 → 自动重新排队（不消耗重试额度）；
- queued 但不在队列 → 补投（防丢失）。
"""
from __future__ import annotations

import time

import pytest

from app import config, jobstore
from app.jobs import JobManager, api_view
from tests.conftest import job_epochs, job_status, wait_until


def test_submit_creates_record_and_enqueues(store, q, ls, make_dataset):
    """submit：写权威记录 → 入队 → 初始字段正确。"""
    ds = make_dataset()
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=False)
    rec = mgr.submit({"dataset_id": ds, "target": "target", "task": "classification",
                      "params": {"epochs": 2}, "resource_hint": {"require_gpu": True}})
    assert q.size() == 1
    stored = jobstore.read_job(store, rec["id"])
    assert stored["status"] == "queued"
    assert stored["spec"]["resource_hint"]["require_gpu"] is True
    assert "任务创建" in jobstore.read_job_log(store, rec["id"])


def test_cancel_running_via_signal(store, q, ls, make_dataset, make_job, spawn_worker):
    """cancel（running）：发信号 → worker 存档停止 → cancelled。"""
    ds = make_dataset(n=1200)
    job_id = make_job(ds, epochs=300)
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=False)
    q.push(job_id)
    w = spawn_worker("w-cancel")
    assert wait_until(lambda: job_status(store, job_id) == "running", 20)
    rec = mgr.cancel(job_id)
    assert rec["status"] in ("cancelled", "running")  # running: 信号已发，等 worker 停
    assert wait_until(lambda: job_status(store, job_id) == "cancelled", 25)
    assert "任务已取消" in jobstore.read_job_log(store, job_id)
    w.stop()


def test_pause_resume_state_machine(store, q, ls, make_dataset, make_job, spawn_worker):
    """pause（running→paused）与 resume（paused→queued→完成）状态机。"""
    ds = make_dataset(n=1200)
    job_id = make_job(ds, epochs=200)
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=False)
    q.push(job_id)
    w = spawn_worker("w-pr")
    assert wait_until(lambda: job_status(store, job_id) == "running", 20)
    mgr.pause(job_id)
    assert wait_until(lambda: job_status(store, job_id) == "paused", 25)
    # resume 后接续完成
    mgr.resume(job_id)
    assert wait_until(lambda: job_status(store, job_id) == "completed", 90)
    eps = job_epochs(store, job_id)
    assert eps == list(range(1, 201)), "resume 后曲线应连续无重复"
    w.stop()


def test_pause_queued_job_directly(store, q, ls, make_dataset, make_job):
    """pause（queued）：直接转 paused；队列条目仍在（领取后按状态跳过）。"""
    ds = make_dataset()
    job_id = make_job(ds, epochs=2)
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=False)
    q.push(job_id)
    rec = mgr.pause(job_id)
    assert rec["status"] == "paused"
    assert q.pop(timeout=0.2) == job_id


def test_resume_rejects_non_paused(store, q, ls, make_dataset, make_job):
    """异常场景：仅 paused 可恢复；queued/terminal 恢复报 ValueError。"""
    ds = make_dataset()
    job_id = make_job(ds, epochs=2)
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=False)
    with pytest.raises(ValueError):
        mgr.resume(job_id)
    jobstore.update_job(store, job_id, status="cancelled")
    with pytest.raises(ValueError):
        mgr.resume(job_id)


def test_watchdog_redispatches_expired_running(store, q, ls, make_dataset, make_job):
    """AC7（watchdog）：running 且租约过期 → 重新排队 + redispatched+1（不耗重试额度）。"""
    ds = make_dataset()
    job_id = make_job(ds, epochs=30)
    # 直接伪造 running + 过期租约（模拟执行槽崩溃/心跳迷失）
    jobstore.update_job(store, job_id, status="running", worker_id="ghost",
                        started_at=time.time())
    with ls._lock:
        ls._leases[job_id] = ["ghost:tok", time.time() - 1]
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=True)
    try:
        ok = wait_until(lambda: job_status(store, job_id) == "queued", 10)
        assert ok, "watchdog 应把租约过期的 running 任务重派回队列"
        rec = jobstore.read_job(store, job_id)
        assert rec["redispatched"] == 1
        assert (rec.get("retry_count") or 0) == 0, "watchdog 重派不消耗重试额度"
        assert "watchdog" in jobstore.read_job_log(store, job_id)
        assert q.size() >= 1
    finally:
        mgr.stop()


def test_watchdog_repushes_missing_queue_entry(store, q, ls, make_dataset, make_job):
    """防丢失：queued 但队列条目丢失 → watchdog 补投。"""
    ds = make_dataset()
    make_job(ds, epochs=2)  # 只写记录，不入队（模拟队列条目丢失）
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=True)
    try:
        ok = wait_until(lambda: q.size() >= 1, 10)
        assert ok, "watchdog 应补投丢失的队列条目"
    finally:
        mgr.stop()


def test_watchdog_keeps_healthy_running_job(store, q, ls, make_dataset, make_job):
    """回归保护：租约健康的 running 任务不被 watchdog 误重派。"""
    ds = make_dataset()
    job_id = make_job(ds, epochs=2)
    jobstore.update_job(store, job_id, status="running", worker_id="live-worker",
                        started_at=time.time())
    assert ls.acquire(job_id, "live:tok", ttl=60)
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=True)
    try:
        time.sleep(2.5)  # 跨越多个巡检周期（WATCHDOG_INTERVAL=1）
        assert job_status(store, job_id) == "running"
        assert (jobstore.read_job(store, job_id).get("redispatched") or 0) == 0
        assert q.size() == 0
    finally:
        mgr.stop()


def test_delete_guard_and_cleanup(store, q, ls, make_dataset, make_job):
    """delete：running 拒绝；终态任务删除后记录与产物消失。"""
    ds = make_dataset()
    job_id = make_job(ds, epochs=2)
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=False)
    jobstore.update_job(store, job_id, status="running")
    with pytest.raises(ValueError):
        mgr.delete(job_id)
    jobstore.update_job(store, job_id, status="failed")
    mgr.delete(job_id)
    assert jobstore.read_job(store, job_id) is None


def test_api_view_shape(store, q, ls, make_dataset, make_job):
    """api_view 字段形状（前端契约）：核心字段 + 多卡扩展字段。"""
    ds = make_dataset()
    job_id = make_job(ds, epochs=2)
    mgr = JobManager(store=store, queue=q, lease=ls, start_workers=False,
                     start_watchdog=False)
    rec = mgr.get(job_id)
    view = api_view(rec, include_metrics=True, include_summary=True, store=store)
    for key in ("id", "status", "spec", "resource_hint", "worker_id",
                "retry_count", "segments", "last_error"):
        assert key in view, f"api_view 缺少 {key}"
    assert view["metrics"] == []
