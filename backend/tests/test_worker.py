"""AC6/AC7/AC8/AC12：无状态 Worker——领取执行、跨 worker 接手、资源提示、失败分类重试。

故障注入说明：存储瞬断通过包装 ArtifactStore 注入（FaultyStore），
核心训练路径真实执行，不 mock 业务逻辑。
"""
from __future__ import annotations

import threading
import time

import pytest

from app import config, jobstore
from app.storage import ArtifactStoreUnavailableError, checkpoint_key, job_key
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
    (ArtifactStoreUnavailableError("读取对象存储产物失败: bucket 不可达"), True),
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
    job = make_job(ds, epochs=4, api_token="training-secret")
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
    log = jobstore.read_job_log(store, job)
    assert "[训练任务启动配置]" in log
    assert '"dataset_id":"' + ds + '"' in log
    assert '"api_token":"[redacted]"' in log
    assert "training-secret" not in log
    w.stop()


def test_running_job_publishes_epoch_log_before_completion(
    store, make_dataset, make_job, q, ls, spawn_worker
):
    """真实训练仍在运行时，共享状态平面应已能读取 epoch 日志。"""
    ds = make_dataset(n=1500)
    job = make_job(ds, epochs=300)
    q.push(job)
    w = spawn_worker("live-log-worker")

    def has_live_epoch_log():
        return (
            job_status(store, job) == jobstore.RUNNING
            and "[epoch 1/300]" in jobstore.read_job_log(store, job)
        )

    assert wait_until(has_live_epoch_log, timeout=30), "训练中应先发布 epoch 日志再完成任务"
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


def test_lease_lost_yields_to_watchdog(store, make_dataset, make_job, q, ls, spawn_worker):
    """失租槽退出且不写 paused，由 watchdog 重排后继续完成训练。"""
    from app.jobs import JobManager

    ds = make_dataset(n=1500)
    job = make_job(ds, epochs=300)
    q.push(job)
    w = spawn_worker("w-lost")
    assert wait_until(lambda: job_status(store, job) == "running", 20)
    with ls._lock:
        ls._leases[job][1] = time.time() - 0.01
    assert wait_until(lambda: w.worker._controller is None, 20)
    assert job_status(store, job) == "running", "失租 worker 不得自行写共享终态"
    w.stop()
    manager = JobManager(store=store, queue=q, lease=ls, start_workers=False, start_watchdog=False)
    try:
        manager._watchdog_once()
        assert job_status(store, job) == "queued"
        assert "心跳丢失" in jobstore.read_job_log(store, job)
        successor = spawn_worker("w-successor")
        assert wait_until(lambda: job_status(store, job) == "completed", 60)
        assert job_epochs(store, job) == list(range(1, 301))
        assert store.exists(job_key(job, "best.pt"))
        successor.stop()
    finally:
        manager.stop()


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


@pytest.mark.parametrize("coordination", ["embedded", "redis"])
def test_worker_renews_lease_during_slow_startup(
    store, make_dataset, make_job, q, ls, monkeypatch, coordination,
):
    """历史日志下载超过租约期限时仍持续续租，并完成真实训练。"""
    from app.worker import TrainingWorker

    if coordination == "redis":
        import os
        import redis
        from app.coordination import RedisLeaseStore

        endpoint = os.environ.get("MTP_TEST_REDIS")
        if not endpoint:
            pytest.skip("未配置 MTP_TEST_REDIS，跳过真实 Redis 慢启动测试")
        ls = RedisLeaseStore(redis.Redis.from_url(endpoint, decode_responses=True))
    job = make_job(make_dataset(), epochs=2)
    jobstore.append_job_log(store, job, "历史日志")
    monkeypatch.setattr(config, "WORKER_LEASE_TTL", 1.0)
    observed = []

    class SlowHistoryStore:
        """仅在历史产物下载时注入真实等待，不替换训练或续租逻辑。"""

        def read_file(self, key, path):
            """阻塞超过初始租期后观察有效持有者。"""
            if key == job_key(job, "log.txt"):
                time.sleep(1.5)
                observed.append(ls.peek_holder(job))
            return store.read_file(key, path)

        def __getattr__(self, name):
            """其余仓储操作继续使用真实本地实现。"""
            return getattr(store, name)

    worker = TrainingWorker(store=SlowHistoryStore(), queue=q, lease_store=ls,
                            capabilities=detect_capabilities("slow-start"))
    worker._execute_job(job)
    assert observed and observed[0], "准备阶段必须持续持有租约"
    assert job_status(store, job) == "completed"
    assert job_epochs(store, job) == [1, 2]
    assert store.exists(job_key(job, "best.pt"))


@pytest.mark.parametrize("state", ["idle", "disabled", "training"])
def test_worker_heartbeat_recovers_registration_without_restart(store, q, state):
    """真实空闲循环和训练控制器均能在注册丢失后恢复完整槽信息。"""
    import os
    import redis
    from app.coordination import RedisLeaseStore
    from app.worker import JobController, TrainingWorker

    endpoint = os.environ.get("MTP_TEST_REDIS")
    if not endpoint:
        pytest.skip("未配置 MTP_TEST_REDIS，跳过真实 worker 注册恢复测试")
    client = redis.Redis.from_url(endpoint, decode_responses=True,
                                  socket_connect_timeout=3, socket_timeout=3)
    ls = RedisLeaseStore(client)
    worker_id = f"recover-{state}-cpu0"
    caps = detect_capabilities(worker_id, machine_id="recover", slot="cpu0")
    caps["managed"] = True
    worker = TrainingWorker(store=store, queue=q, lease_store=ls, capabilities=caps,
                            idle_poll=0.1, gpu_probe_enabled=False)
    controller = None
    try:
        ls.register_worker(worker_id, caps, ttl=5)
        registered = next(item for item in ls.list_workers() if item["worker_id"] == worker_id)
        expected_caps = {key: registered[key] for key in caps}
        ls.set_worker_enabled(worker_id, state != "disabled")
        client.srem("mtp:workers:all", worker_id)
        client.delete(f"mtp:worker:{worker_id}:info", f"mtp:worker:{worker_id}:hb")
        if state == "training":
            assert ls.acquire("registry-job", "registry-token", ttl=5)
            controller = JobController(ls, "registry-job", "registry-token", worker_id,
                                       interval=0.05, ttl=5)
            controller.set_progress(9, state="training")
            controller.start()
            assert wait_until(lambda: client.sismember("mtp:workers:all", worker_id), timeout=2)
        else:
            worker._run_forever_iteration(hb_ttl=5)
        recovered = next(item for item in ls.list_workers() if item["worker_id"] == worker_id)
        for key, value in expected_caps.items():
            assert recovered[key] == value, f"槽位恢复缺失能力字段：{key}"
        assert recovered["active"] is True and recovered["state"] == state
        assert ls.is_worker_enabled(worker_id) is (state != "disabled")
        if state == "training":
            assert recovered["current_job"] == "registry-job" and recovered["epoch"] == 9
            assert ls.peek_holder("registry-job") == "registry-token"
            assert not controller.lease_lost.is_set()
        else:
            assert recovered["current_job"] is None and q.size() == 0
    finally:
        if controller is not None:
            controller.stop()
            ls.release("registry-job", "registry-token")
        ls.deregister_worker(worker_id)
        client.hdel("mtp:workers:enabled", worker_id)
        client.close()


@pytest.mark.parametrize("loss_point", ["metrics", "checkpoint", "failure", "retry"])
def test_superseded_worker_cannot_overwrite_successor(
    store, make_dataset, make_job, q, ls, loss_point,
):
    """旧槽在写入返回或异常收尾时失租，不得覆盖接手槽的状态、产物及缓存。"""
    from app.jobs import JobManager
    from app.worker import TrainingWorker

    job = make_job(make_dataset(), epochs=6)
    old_entered, old_release = threading.Event(), threading.Event()
    new_entered, new_release = threading.Event(), threading.Event()
    paths = {}

    class GatedStore:
        """在真实上传完成后阻塞返回，制造可重复的 worker 交接时序。"""

        def __init__(self, label, entered, release):
            """记录各执行者的单次阻塞点。"""
            self.label, self.entered, self.release = label, entered, release
            self.blocked = False

        def write_file(self, key, path):
            """保留真实产物写入，仅注入外部存储等待或失败。"""
            store.write_file(key, path)
            selected = "checkpoints/ckpt-" if self.label == "old" and loss_point == "checkpoint" else "metrics.jsonl"
            if selected in key and not self.blocked:
                self.blocked = True
                paths[self.label] = path.parent.parent if "/checkpoints/" in str(path) else path.parent
                self.entered.set()
                if not self.release.wait(20):
                    raise TimeoutError("测试存储等待超时")
                if self.label == "old" and loss_point in {"failure", "retry"}:
                    error = ValueError if loss_point == "failure" else ConnectionError
                    raise error("storage connection reset after takeover")

        def __getattr__(self, name):
            """转发未注入故障的真实仓储操作。"""
            return getattr(store, name)

    old = TrainingWorker(store=GatedStore("old", old_entered, old_release), queue=q,
                         lease_store=ls, capabilities=detect_capabilities("old-slot"))
    new = TrainingWorker(store=GatedStore("new", new_entered, new_release), queue=q,
                         lease_store=ls, capabilities=detect_capabilities("new-slot"))
    manager = JobManager(store=store, queue=q, lease=ls, start_workers=False, start_watchdog=False)
    old_thread = threading.Thread(target=old._execute_job, args=(job,), daemon=True)
    new_thread = threading.Thread(target=new._execute_job, args=(job,), daemon=True)
    old_thread.start()
    try:
        assert old_entered.wait(15), "旧槽应先产生真实训练进度"
        with ls._lock:
            ls._leases[job][1] = time.time() - 1
        manager._watchdog_once()
        assert job_status(store, job) == "queued"
        new_thread.start()
        assert new_entered.wait(15), "接手槽应能从已上传断点继续训练"
        before = {key: store.read_bytes(key) for key in store.list_keys(job_key(job, ""))}
        old_release.set()
        old_thread.join(10)
        assert not old_thread.is_alive()
        after = {key: store.read_bytes(key) for key in store.list_keys(job_key(job, ""))}
        assert after == before, "旧 worker 的退出、失败和 finally 不得修改接手任务"
        assert paths["old"] != paths["new"], "临时目录必须按执行批次隔离"
        assert (paths["new"] / "metrics.jsonl").exists(), "旧 worker 不得清理新槽目录"
        new_release.set()
        new_thread.join(15)
        assert job_status(store, job) == "completed"
        assert job_epochs(store, job) == list(range(1, 7))
        record = jobstore.read_job(store, job)
        assert [segment["status"] for segment in record["segments"]] == ["interrupted", "completed"]
        assert record["summary"]["final"]["val_accuracy"] >= 0
        assert store.exists(job_key(job, "best.pt"))
    finally:
        old_release.set()
        new_release.set()
        old_thread.join(10)
        if new_thread.ident is not None:
            new_thread.join(10)
        manager.stop()


@pytest.mark.parametrize("operation", ["state", "model", "prune"])
@pytest.mark.parametrize("lease_failure", ["expired", "new_owner", "unavailable"])
def test_lease_guard_rejects_unowned_mutations(store, ls, tmp_path, monkeypatch, operation, lease_failure):
    """失租或协调面不可达时，状态覆盖、模型上传及断点删除都不得产生副作用。"""
    from app.worker import JobController, JobLeaseLost, _LeaseGuardedStore

    job, token = "guarded-job", "old-token"
    key = job_key(job, "best.pt")
    store.write_bytes(key, b"published-model")
    assert ls.acquire(job, token, 10)
    controller = JobController(ls, job, token, "old-worker", 0.1, 10)
    guarded = _LeaseGuardedStore(store, controller.ensure_owned)
    if lease_failure == "unavailable":
        def unavailable(*args):
            """只替换外部协调服务的失败结果，不绕过所有权判断。"""
            raise ConnectionError("redis temporarily unavailable")
        monkeypatch.setattr(ls, "renew", unavailable)
    else:
        with ls._lock:
            ls._leases[job][1] = time.time() - 1
        if lease_failure == "new_owner":
            assert ls.acquire(job, "new-token", 10)
    stale_model = tmp_path / "stale.pt"
    stale_model.write_bytes(b"stale-model")
    with pytest.raises(JobLeaseLost):
        if operation == "state":
            guarded.write_json(job_key(job, "job.json"), {"status": "paused"})
        elif operation == "model":
            guarded.write_file(key, stale_model)
        else:
            guarded.delete(key)
    assert store.read_bytes(key) == b"published-model"
    assert not store.exists(job_key(job, "job.json"))
    assert controller.lease_lost.is_set() and controller.pause_event.is_set()


def test_controller_caps_heartbeat_interval_and_clears_old_signals(ls):
    """心跳配置大于 TTL 时自动缩短周期；旧暂停信号在清理前不得触发本次执行。"""
    from app.worker import JobController

    assert ls.acquire("short-lease", "owner", 0.3)
    ls.set_signal("short-lease", "pause")
    controller = JobController(ls, "short-lease", "owner", "worker", 10, 0.3)
    controller.start()
    try:
        time.sleep(0.65)
        assert ls.peek_holder("short-lease") == "owner"
        assert not controller.pause_event.is_set()
        ls.clear_signals("short-lease")
        controller.enable_signals()
        ls.set_signal("short-lease", "cancel")
        assert controller.cancel_event.wait(1)
        assert not controller.pause_event.is_set()
    finally:
        controller.stop()


@pytest.mark.parametrize("task", ["classification", "regression"])
def test_selected_training_resource_executes_on_target_only(store, make_dataset, make_job, q, ls, spawn_worker, task):
    """真实训练只由指定槽执行，验证指标、产物、日志与执行轨迹。"""
    ds = make_dataset(kind=task, n=120)
    job = make_job(ds, epochs=2, task=task)
    record = jobstore.read_job(store, job)
    record["spec"]["resource_hint"]["worker_id"] = "selected-cpu"
    jobstore.write_job(store, record)
    q.push(job)
    other = spawn_worker(capabilities=_caps("other-cpu"))
    assert wait_until(lambda: other.worker._last_skip[0] == job, 5)
    assert job_status(store, job) == "queued"
    target = spawn_worker(capabilities=_caps("selected-cpu"))
    assert wait_until(lambda: job_status(store, job) == "completed"
                      and bool((jobstore.read_job(store, job) or {}).get("segments"))
                      and ls.peek_holder(job) is None, 40)
    final = jobstore.read_job(store, job)
    assert final["worker_id"] == "selected-cpu"
    assert all(segment["worker_id"] == "selected-cpu" for segment in final["segments"])
    assert final["spec"]["resource_hint"]["worker_id"] == "selected-cpu"
    metrics = jobstore.read_metrics(store, job)
    assert len(metrics) == 2
    assert ("val_accuracy" if task == "classification" else "val_r2") in metrics[-1]
    if task == "regression":
        assert "val_rmse" in final["summary"]["final"]
    assert store.exists(job_key(job, "best.pt"))
    assert store.exists(job_key(job, "preprocessor.json"))
    assert "selected-cpu" in jobstore.read_job_log(store, job)
    other.stop()
    target.stop()


def test_selected_training_resource_kept_on_auto_retry(store, make_dataset, make_job, q, ls):
    """环境错误自动重试不解除初始的资源选择。"""
    from app.worker import TrainingWorker

    job = make_job(make_dataset(n=40), epochs=2)
    record = jobstore.read_job(store, job)
    record["spec"]["resource_hint"]["worker_id"] = "selected-cpu"
    jobstore.write_job(store, record)
    worker = TrainingWorker(store=store, queue=q, lease_store=ls, capabilities=_caps("selected-cpu"), gpu_probe_enabled=False)
    worker._handle_run_failure(job, RuntimeError("CUDA out of memory"))
    final = jobstore.read_job(store, job)
    assert final["status"] == "queued" and final["retry_count"] == 1
    assert final["spec"]["resource_hint"]["worker_id"] == "selected-cpu"
    assert job in q.snapshot()


@pytest.mark.parametrize("change", ["disable", "reassign", "degraded"])
def test_selected_training_resource_rechecked_after_lease(store, make_dataset, make_job, q, ls, monkeypatch, change):
    """模拟取得租约期间改选或停用，不能让旧槽越过校验开始训练。"""
    from app.worker import TrainingWorker

    job = make_job(make_dataset(n=40), epochs=2)
    record = jobstore.read_job(store, job)
    record["spec"]["resource_hint"]["worker_id"] = "selected-cpu"
    jobstore.write_job(store, record)
    worker = TrainingWorker(store=store, queue=q, lease_store=ls, capabilities=_caps("selected-cpu"), gpu_probe_enabled=False)
    acquire = ls.acquire

    def acquire_then_change(*args, **kwargs):
        """在真实租约成功后注入并发状态变更，不替代核心领取逻辑。"""
        result = acquire(*args, **kwargs)
        if change == "disable":
            ls.set_worker_enabled("selected-cpu", False)
        elif change == "degraded":
            worker._degraded = True
        else:
            changed = jobstore.read_job(store, job)
            changed["spec"]["resource_hint"]["worker_id"] = "another-cpu"
            jobstore.write_job(store, changed)
        return result

    monkeypatch.setattr(ls, "acquire", acquire_then_change)
    worker._execute_job(job)
    assert job_status(store, job) == "queued"
    assert not store.exists(job_key(job, "best.pt"))
    assert ls.peek_holder(job) is None
    assert job in q.snapshot()


def test_selected_cpu_clustering_rejects_replaced_gpu_slot(store, make_dataset, make_job, q, ls):
    """资源登记后硬件能力变化时，CPU 聚类仍不得在 GPU 槽上执行。"""
    from app.worker import TrainingWorker

    job = make_job(make_dataset(kind="clustering", n=40), task="clustering")
    record = jobstore.read_job(store, job)
    record["spec"]["resource_hint"]["worker_id"] = "selected-slot"
    jobstore.write_job(store, record)
    worker = TrainingWorker(store=store, queue=q, lease_store=ls,
                            capabilities=_caps("selected-slot", gpu=True, vram=8192),
                            idle_poll=0.01, gpu_probe_enabled=False)
    worker._execute_job(job)
    assert job_status(store, job) == "queued"
    assert ls.peek_holder(job) is None
    assert not store.exists(job_key(job, "best.pt"))
    assert job in q.snapshot()
