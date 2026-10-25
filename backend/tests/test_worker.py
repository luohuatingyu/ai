"""AC6/AC7/AC8/AC12：无状态 Worker——领取执行、跨 worker 接手、资源提示、失败分类重试。

故障注入说明：存储瞬断通过包装 ArtifactStore 注入（FaultyStore），
核心训练路径真实执行，不 mock 业务逻辑。
"""
from __future__ import annotations

import threading
import time

import pytest

from app import config, jobstore
from app.storage import checkpoint_key, job_key
from app.worker import classify_retryable, detect_capabilities, probe_visible_gpu
from tests.conftest import job_epochs, job_status, wait_until


# ---------------------------------------------------------------------------
# 失败分类（单元）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exc,want_retryable", [
    (RuntimeError("CUDA error: out of memory"), True),
    (RuntimeError("NCCL timeout"), True),
    (ConnectionError("connection refused"), True),
    (OSError("storage connection reset"), True),
    (FileNotFoundError("dataset missing"), False),   # 业务性：数据缺失
    (ValueError("bad hyper-params"), False),         # 业务性：超参非法
    (KeyError("target column"), False),              # 业务性：字段缺失
])
def test_classify_retryable(exc, want_retryable):
    assert classify_retryable(exc) is want_retryable


def test_probe_visible_gpu_never_raises():
    """软探测在任何环境都不得抛异常（CPU 机器返回 False）。"""
    assert probe_visible_gpu() in (True, False)


def test_detect_capabilities_reports_cpu_and_stable_server_identity(monkeypatch):
    """硬件上报含 CPU 核心/内存，并优先使用配置的稳定服务器标识。"""
    monkeypatch.setattr(config, "WORKER_MACHINE_ID", "worker-lab")
    monkeypatch.setattr(config, "WORKER_MANAGED", True)

    caps = detect_capabilities("worker-lab-cpu0", slot="cpu0")

    assert caps["machine_id"] == "worker-lab"
    assert caps["managed"] is True
    assert caps["cpu"]["cores"] >= 1
    assert caps["cpu"]["memory_mb"] is None or caps["cpu"]["memory_mb"] >= 1


# ---------------------------------------------------------------------------
# 正常执行（AC6）
# ---------------------------------------------------------------------------

def test_worker_completes_job(store, make_dataset, make_job, q, ls, spawn_worker):
    ds = make_dataset()
    job = make_job(ds, epochs=4)
    q.push(job)
    w = spawn_worker("srv-a")
    ok = wait_until(lambda: job_status(store, job) == "completed", 40)
    assert ok, f"状态: {job_status(store, job)}"
    rec = jobstore.read_job(store, job)
    assert job_epochs(store, job) == [1, 2, 3, 4]
    assert store.exists(job_key(job, "best.pt"))
    assert store.exists(job_key(job, "preprocessor.json"))
    assert rec["summary"]["epochs_total"] == 4
    assert rec["segments"] and rec["segments"][0]["worker_id"] == w.worker.worker_id
    assert rec["segments"][0]["machine_id"] == w.worker.caps["machine_id"]
    assert rec["segments"][0]["slot"] == w.worker.caps["slot"]
    assert rec["segments"][0]["from_epoch"] == 1 and rec["segments"][0]["to_epoch"] == 4
    w.stop()


def test_managed_worker_waits_for_manual_selection(
    store, make_dataset, make_job, q, ls, spawn_worker
):
    """外部计算槽默认只上报硬件与心跳，人工启用后才领取任务。"""
    ds = make_dataset(n=1200)
    job = make_job(ds, epochs=30)
    queued_job = make_job(ds, epochs=1)
    q.push(job)
    q.push(queued_job)
    caps = _caps("remote-pending", gpu=False)
    caps.update({"worker_id": "remote-pending-cpu0", "machine_id": "remote-pending",
                 "slot": "cpu0", "managed": True})

    handle = spawn_worker(capabilities=caps, idle_poll=0.05)
    assert wait_until(
        lambda: any(
            item.get("worker_id") == "remote-pending-cpu0"
            and item.get("state") == "disabled"
            for item in ls.list_workers()
        ),
        timeout=5,
    )
    assert job_status(store, job) == "queued"
    assert q.size() == 2, "待选资源不得取走排队任务"

    ls.set_worker_enabled("remote-pending-cpu0", True)
    assert wait_until(lambda: job_status(store, job) == "running", timeout=5)
    ls.set_worker_enabled("remote-pending-cpu0", False)
    assert wait_until(lambda: job_status(store, job) == "completed", timeout=40)
    assert wait_until(
        lambda: any(
            item.get("worker_id") == "remote-pending-cpu0"
            and item.get("state") == "disabled"
            for item in ls.list_workers()
        ),
        timeout=5,
    )
    assert job_status(store, queued_job) == "queued"
    assert q.size() == 1, "停用后已运行任务可以收尾，但不可再领取下一任务"
    handle.stop()


def test_completed_job_not_reprocessed(store, make_dataset, make_job, q, ls, spawn_worker):
    """兼容性/幂等：重复入队的已完成任务按状态跳过，指标不翻倍。"""
    ds = make_dataset()
    job = make_job(ds, epochs=2)
    q.push(job)
    w = spawn_worker("w-first")
    assert wait_until(lambda: job_status(store, job) == "completed", 40)
    q.push(job)  # 模拟队列重复条目
    time.sleep(1.0)
    assert job_status(store, job) == "completed"
    assert job_epochs(store, job) == [1, 2]
    w.stop()


# ---------------------------------------------------------------------------
# 跨 worker 接手（AC7：单卡不可用不影响任务完成）
# ---------------------------------------------------------------------------

def test_pause_then_other_worker_resumes(store, make_dataset, make_job, q, ls, spawn_worker):
    """暂停 → 另一 worker 从 checkpoint 接手 → 曲线连续无重复、segments 两段。"""
    ds = make_dataset(n=1500)
    job = make_job(ds, epochs=300)
    q.push(job)
    wa = spawn_worker("srv-a")
    assert wait_until(lambda: job_status(store, job) == "running", 20)
    assert wait_until(lambda: len(job_epochs(store, job)) >= 2, 30), \
        "先跑出至少 2 个 epoch 再暂停（避免首 epoch 前暂停的边界态）"
    ls.set_signal(job, "pause")
    assert wait_until(lambda: job_status(store, job) == "paused", 25)
    paused_epoch = max(job_epochs(store, job))
    assert paused_epoch >= 2
    assert store.exists(checkpoint_key(job, paused_epoch)), "暂停点断点应已同步状态平面"
    wa.stop()

    jobstore.update_job(store, job, status=jobstore.QUEUED)
    q.push(job)
    wb = spawn_worker("srv-b")
    assert wait_until(lambda: job_status(store, job) == "completed", 120)
    rec = jobstore.read_job(store, job)
    assert job_epochs(store, job) == list(range(1, 301)), "续训曲线应连续无重复"
    segs = rec["segments"]
    assert len(segs) == 2
    assert segs[0]["worker_id"] != segs[1]["worker_id"]
    assert segs[1]["from_epoch"] == paused_epoch + 1
    wb.stop()


def test_lease_lost_graceful_pause(store, make_dataset, make_job, q, ls, spawn_worker):
    """异常场景：租约被回收（心跳过期/watchdog 重派）→ 本槽优雅让位，任务 paused。"""
    ds = make_dataset(n=1500)
    job = make_job(ds, epochs=300)
    q.push(job)
    w = spawn_worker("w-lost")
    assert wait_until(lambda: job_status(store, job) == "running", 20)
    with ls._lock:
        ls._leases[job][1] = time.time() - 0.01
    assert wait_until(lambda: job_status(store, job) == "paused", 20)
    assert "心跳丢失" in jobstore.read_job_log(store, job)
    w.stop()


def test_checkpoint_corrupt_falls_back_to_older(store, make_dataset, make_job, q, ls, spawn_worker):
    """异常场景：最新 checkpoint 损坏 → 回退更早版本续训（进度尽量不丢）。"""
    ds = make_dataset(n=1000)
    job = make_job(ds, epochs=40)
    q.push(job)
    wa = spawn_worker("srv-a")
    assert wait_until(lambda: len(job_epochs(store, job)) >= 4, 40)
    ls.set_signal(job, "pause")
    assert wait_until(lambda: job_status(store, job) == "paused", 25)
    paused_epoch = max(job_epochs(store, job))
    wa.stop()

    # 损坏最新 checkpoint（模拟上传半截/位翻转）
    store.write_bytes(checkpoint_key(job, paused_epoch), b"corrupted-not-torch")

    jobstore.update_job(store, job, status=jobstore.QUEUED)
    q.push(job)
    wb = spawn_worker("srv-b")
    assert wait_until(lambda: job_status(store, job) == "completed", 120)
    assert "回退更早版本" in jobstore.read_job_log(store, job)
    eps = job_epochs(store, job)
    assert eps == sorted(set(eps)), f"回退续训后曲线仍应无重复 epoch: {eps}"
    assert eps == list(range(1, 41)), "回退续训后应补满全部 epoch"
    wb.stop()


# ---------------------------------------------------------------------------
# 资源提示（AC8：任务分发匹配到卡级）
# ---------------------------------------------------------------------------

def _caps(name: str, gpu: bool = False, vram: int = 0) -> dict:
    caps = detect_capabilities(name)
    caps["gpu"] = ({"available": True, "name": "TestGPU", "count": 1, "vram_mb": vram}
                   if gpu else
                   {"available": False, "name": None, "count": 0, "vram_mb": 0})
    return caps


def test_manual_retry_target_is_the_only_worker_that_claims_job(
    store, make_dataset, make_job, q, ls, spawn_worker
):
    """指定重试槽时其他 worker 放回任务，目标槽执行并留下机器与卡槽记录。"""
    ds = make_dataset(n=120)
    job = make_job(ds, epochs=2)
    target_id = "retry-node-cpu2"
    jobstore.update_job(
        store,
        job,
        status=jobstore.QUEUED,
        retry_worker_id=target_id,
        retry_assignment_mode="worker",
        error=None,
    )
    q.push(job)

    other_caps = _caps("other-node-cpu0")
    other_caps.update({"worker_id": "other-node-cpu0", "machine_id": "other-node", "slot": "cpu0"})
    other = spawn_worker(capabilities=other_caps)
    assert wait_until(lambda: any(item.get("worker_id") == "other-node-cpu0" for item in ls.list_workers()), 5)
    assert wait_until(lambda: other.worker._last_skip[0] == job, 5)
    assert job_status(store, job) == jobstore.QUEUED

    target_caps = _caps(target_id)
    target_caps.update({"worker_id": target_id, "machine_id": "retry-node", "slot": "cpu2"})
    target = spawn_worker(capabilities=target_caps)
    assert wait_until(lambda: job_status(store, job) == jobstore.COMPLETED, 40)

    record = jobstore.read_job(store, job)
    assert record["worker_id"] == target_id
    assert record["segments"][-1]["worker_id"] == target_id
    assert record["segments"][-1]["machine_id"] == "retry-node"
    assert record["segments"][-1]["slot"] == "cpu2"
    assert record["segments"][-1]["status"] == "completed"
    assert record["retry_worker_id"] is None
    other.stop()
    target.stop()


def test_require_gpu_job_not_claimed_by_cpu_worker(store, make_dataset, make_job, q, ls):
    """AC8：require_gpu 任务在 CPU 槽保持排队（放回队列，等 GPU 卡）。"""
    ds = make_dataset()
    job = make_job(ds, epochs=2, require_gpu=True)
    q.push(job)
    from app.worker import TrainingWorker

    w = TrainingWorker(store=store, queue=q, lease_store=ls,
                       capabilities=_caps("cpu-only"), idle_poll=0.1,
                       gpu_probe_enabled=False)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    time.sleep(1.5)
    assert job_status(store, job) == "queued", "CPU 槽不得领取 require_gpu 任务"
    assert q.size() >= 1, "任务应被放回队列等待 GPU worker"
    w.request_shutdown()
    t.join(timeout=5)


def test_min_vram_job_skipped_by_smaller_gpu(store, make_dataset, make_job, q, ls):
    """卡级显存匹配：显存不足的卡不领取。"""
    ds = make_dataset()
    job = make_job(ds, epochs=2, min_vram_mb=8192)
    q.push(job)
    from app.worker import TrainingWorker

    w = TrainingWorker(store=store, queue=q, lease_store=ls,
                       capabilities=_caps("small-gpu", gpu=True, vram=4096),
                       idle_poll=0.1, gpu_probe_enabled=False)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    time.sleep(1.5)
    assert job_status(store, job) == "queued"
    assert q.size() >= 1, "显存不足的任务应放回队列等待更大显存的卡"
    w.request_shutdown()
    t.join(timeout=5)


def test_min_vram_job_claimed_by_big_gpu(store, make_dataset, make_job, q, ls, spawn_worker):
    """显存达标的 GPU 槽正常领取（领取匹配按能力表，训练设备本身自动回退 CPU）。"""
    ds = make_dataset()
    job = make_job(ds, epochs=2, min_vram_mb=2048)
    q.push(job)
    w = spawn_worker(capabilities=_caps("big-gpu", gpu=True, vram=8192))
    assert wait_until(lambda: job_status(store, job) == "completed", 40)
    w.stop()


# ---------------------------------------------------------------------------
# 失败分类重试（AC12：环境性错误自动重试，业务性直接失败）
# ---------------------------------------------------------------------------

class FaultyStore:
    """包装 store：前 fail_times 次 checkpoint 写入抛环境性异常（存储瞬断注入）。"""

    def __init__(self, inner, fail_times=1):
        self.inner = inner
        self.fail_times = fail_times
        self.raised = 0

    def write_file(self, key, path):
        if "checkpoints/ckpt-" in key and self.raised < self.fail_times:
            self.raised += 1
            raise OSError("storage connection reset during checkpoint upload")
        return self.inner.write_file(key, path)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_env_error_auto_retries_then_completes(store, make_dataset, make_job, q, ls):
    """环境性错误 → 自动重试（计数+1、回队列）→ 再次执行成功，曲线连续。"""
    ds = make_dataset()
    job = make_job(ds, epochs=6)
    from app.worker import TrainingWorker

    w = TrainingWorker(store=FaultyStore(store, fail_times=1), queue=q,
                       lease_store=ls, capabilities=detect_capabilities("w-fault"),
                       idle_poll=0.05, gpu_probe_enabled=False)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    q.push(job)
    assert wait_until(lambda: job_status(store, job) == "completed", 60)
    rec = jobstore.read_job(store, job)
    assert rec["retry_count"] == 1
    assert [segment["status"] for segment in rec["segments"]] == ["retrying", "completed"]
    assert "storage connection reset" in (rec.get("last_error") or "")
    assert job_epochs(store, job) == list(range(1, 7)), "重试不得产生重复/缺失 epoch"
    assert rec["error"] is None, "最终成功应清空 error"
    assert "自动重试 1/3" in jobstore.read_job_log(store, job)
    w.request_shutdown()
    t.join(timeout=5)


def test_env_error_exhausts_retry_then_failed(store, make_dataset, make_job, q, ls):
    """持续环境性错误 → 耗尽 JOB_MAX_AUTO_RETRY(3) → FAILED。"""
    ds = make_dataset()
    job = make_job(ds, epochs=6)
    from app.worker import TrainingWorker

    w = TrainingWorker(store=FaultyStore(store, fail_times=10 ** 9), queue=q,
                       lease_store=ls, capabilities=detect_capabilities("w-dead"),
                       idle_poll=0.05, gpu_probe_enabled=False)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    q.push(job)
    assert wait_until(lambda: job_status(store, job) == "failed", 120)
    rec = jobstore.read_job(store, job)
    assert rec["retry_count"] == int(config.JOB_MAX_AUTO_RETRY)
    assert len(rec["segments"]) == int(config.JOB_MAX_AUTO_RETRY) + 1
    assert rec["segments"][-1]["status"] == "failed"
    assert "自动重试已耗尽" in str(rec["error"])
    log = jobstore.read_job_log(store, job)
    assert log.count("自动重试 ") == int(config.JOB_MAX_AUTO_RETRY)
    w.request_shutdown()
    t.join(timeout=5)


def test_business_error_fails_without_retry(store, make_dataset, make_job, q, ls, spawn_worker):
    """业务性错误（数据集缺失）→ 直接 FAILED，不消耗重试、不回队列。"""
    job = make_job("no-such-dataset", epochs=2)
    q.push(job)
    w = spawn_worker("w-fatal")
    assert wait_until(lambda: job_status(store, job) == "failed", 30)
    rec = jobstore.read_job(store, job)
    assert rec.get("retry_count", 0) == 0
    assert rec["segments"][-1]["status"] == "failed"
    assert rec["segments"][-1]["worker_id"] == w.worker.worker_id
    assert rec["segments"][-1]["machine_id"] == w.worker.caps["machine_id"]
    assert rec["segments"][-1]["slot"] == w.worker.caps["slot"]
    assert q.size() == 0, "业务性失败不应回队列"
    assert "任务失败" in jobstore.read_job_log(store, job)
    w.stop()


def test_worker_survives_queue_pop_error(store, make_dataset, make_job, q, ls):
    """异常场景：协调面瞬断（pop 抛异常）不得杀死 worker——退避后继续领取。"""
    class FlakyQueue:
        """包装队列：前 fail_times 次 pop 抛连接异常（模拟 Redis 抖动）。"""

        def __init__(self, inner, fail_times=2):
            self.inner = inner
            self.fail_times = fail_times

        def pop(self, timeout=1.0):
            if self.fail_times > 0:
                self.fail_times -= 1
                raise ConnectionError("redis connection reset")
            return self.inner.pop(timeout)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    ds = make_dataset()
    job = make_job(ds, epochs=2)
    from app.worker import TrainingWorker

    w = TrainingWorker(store=store, queue=FlakyQueue(q), lease_store=ls,
                       capabilities=detect_capabilities("w-flaky"),
                       idle_poll=0.1, gpu_probe_enabled=False)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    q.push(job)  # 前两次 pop 失败后第三次应领取成功
    assert wait_until(lambda: job_status(store, job) == "completed", 40)
    assert t.is_alive(), "worker 循环不应因 pop 异常退出"
    w.request_shutdown()
    t.join(timeout=5)
