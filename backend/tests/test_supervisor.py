"""AC9/AC10/AC11：一卡一进程 Supervisor——槽位规划、子进程守护、掉卡探测恢复。

AC9/AC10/AC11 的端到端（真实多子进程 + Redis 协调）需要 Redis：
设 MTP_TEST_REDIS=redis://... 后运行；未配置时跳过（已在 TEST_REPORT 注明）。
纯逻辑（槽位规划/参数）不依赖 Redis，始终执行。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from app import config
from app.worker import WorkerSupervisor, _gpu_count, detect_capabilities


# ---------------------------------------------------------------------------
# 纯逻辑：槽位规划（本机无 GPU 时 = WORKER_CPU_SLOTS 个 CPU 槽）
# ---------------------------------------------------------------------------

def test_slot_planning_cpu_machine(monkeypatch):
    """纯 CPU 机器：无 GPU 槽，WORKER_CPU_SLOTS 决定 CPU 槽数（至少 1）。"""
    monkeypatch.setattr("app.worker._gpu_count", lambda: 0)
    monkeypatch.setattr(config, "WORKER_CPU_SLOTS", 2)
    sup = WorkerSupervisor(queue=None, lease_store=None, store=None, cpu_slots=2)
    gpu_slots = [s for s, idx in sup._slots if idx is not None]
    cpu_slots = [s for s, idx in sup._slots if idx is None]
    assert gpu_slots == [] and cpu_slots == ["cpu0", "cpu1"]


def test_slot_planning_with_gpus(monkeypatch):
    """GPU 机器：N 个 gpu 槽（带 CUDA 索引）+ CPU 槽。"""
    monkeypatch.setattr("app.worker._gpu_count", lambda: 2)
    monkeypatch.setattr(config, "WORKER_CPU_SLOTS", 1)
    sup = WorkerSupervisor(queue=None, lease_store=None, store=None, cpu_slots=1)
    slots = sup._slots
    assert slots[:2] == [("gpu0", "0"), ("gpu1", "1")]
    assert slots[2] == ("cpu0", None)


def test_gpu_count_safe_on_cpu_machine():
    """本机探测不抛异常，返回非负整数。"""
    n = _gpu_count()
    assert isinstance(n, int) and n >= 0


def test_capabilities_slot_fields():
    """能力表包含机器/槽位字段（/api/workers 聚合展示数据源）。"""
    caps = detect_capabilities("w-x")
    assert "machine_id" in caps and "slot" in caps
    assert caps["slot"] == "cpu0"


# ---------------------------------------------------------------------------
# 端到端（需 Redis；AC9 并行 / AC10 kill 接手 / AC11 恢复）
# ---------------------------------------------------------------------------

def _redis_available() -> bool:
    if not os.environ.get("MTP_TEST_REDIS"):
        return False
    try:
        import redis as redislib

        client = redislib.Redis.from_url(os.environ["MTP_TEST_REDIS"],
                                         decode_responses=True, socket_connect_timeout=2)
        return bool(client.ping())
    except Exception:
        return False


REDIS = pytest.mark.skipif(not _redis_available(), reason="需要 MTP_TEST_REDIS 才能跑多进程 E2E")


def _wait(pred, timeout=60, step=0.25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False


@REDIS
def test_supervisor_parallel_and_child_restart(make_dataset, store):
    """AC9：两槽并行各跑一任务；AC10：SIGKILL 子进程后 supervisor 重启新进程接手。"""
    import json
    import signal

    from app import jobstore
    from app.coordination import build_coordination
    from app.storage import job_key

    ds = make_dataset()
    env = dict(os.environ)
    env["MTP_E2E"] = "1"

    queue, lease, distributed = build_coordination()
    assert distributed, "E2E 需要 REDIS_URL"
    lease_queue = queue

    # 写入两个任务并入队
    ids = []
    for i in range(2):
        job_id = f"job-e2e-{i}-{int(time.time())}"
        record = {
            "id": job_id,
            "spec": {"dataset_id": ds, "dataset_name": ds, "target": "target",
                     "task": "classification",
                     "params": {"epochs": 30, "batch_size": 64, "lr": 0.01,
                                "hidden_size": 32, "hidden_layers": 2,
                                "test_split": 0.2, "seed": 42},
                     "resource_hint": {"require_gpu": False, "min_vram_mb": 0}},
            "status": "queued", "created_at": time.time(), "started_at": None,
            "finished_at": None, "error": None, "device": None, "summary": None,
            "segments": [], "worker_id": None, "retry_count": 0,
            "last_error": None, "redispatched": 0,
        }
        jobstore.write_job(store, record)
        lease_queue.push(job_id)
        ids.append(job_id)

    sup_log = open("/tmp/mtp-sup-e2e.log", "w")
    sup = subprocess.Popen([sys.executable, "-m", "app.worker"], env=env,
                           stdout=sup_log, stderr=subprocess.STDOUT,
                           cwd=str(__import__("pathlib").Path(__file__).parents[1]))
    try:
        # AC9：两个任务同时在 running（两个槽并行）
        def both_running():
            return all(jobstore.read_job(store, j)["status"] == "running" for j in ids)
        assert _wait(both_running, 60), "两个任务应同时在跑（AC9 多执行槽并行）"

        # AC10：杀掉一个子进程
        pg = subprocess.run(["pgrep", "-f", "app.worker --slot"], capture_output=True, text=True)
        pids = [int(p) for p in pg.stdout.split()]
        assert len(pids) >= 2
        victim = pids[0]
        os.kill(victim, signal.SIGKILL)
        time.sleep(1)
        # supervisor 自动补齐槽位（新 pid 与旧不同）
        def restarted():
            pg2 = subprocess.run(["pgrep", "-f", "app.worker --slot"],
                                 capture_output=True, text=True)
            pids2 = [int(p) for p in pg2.stdout.split()]
            return len(pids2) >= 2 and victim not in pids2
        assert _wait(restarted, 40), "supervisor 应自动重启死亡槽位（AC11 CPU 槽）"

        # 手动重派被杀槽的任务（正式 watchdog 的行为；断言接手后能完成）
        for j in ids:
            rec = jobstore.read_job(store, j)
            if rec["status"] != "completed":
                jobstore.update_job(store, j, status="queued")
                lease_queue.push(j)

        def all_done():
            return all(jobstore.read_job(store, j)["status"] == "completed" for j in ids)
        assert _wait(all_done, 120), "kill 后任务应由其余槽接手完成（AC10）"
    finally:
        sup.send_signal(signal.SIGTERM)
        try:
            sup.wait(timeout=30)
        except Exception:
            sup.kill()
        sup_log.close()
        # 清理队列残留
        import redis as redislib

        redislib.Redis.from_url(os.environ["MTP_TEST_REDIS"],
                                decode_responses=True).flushdb()


def test_probe_gpu_exit_code():
    """AC11（探测子进程语义）：--probe-gpu 在无可见 GPU 时返回非 0（supervisor 据此等待恢复）。\n    （独立子进程，不依赖 Redis，始终执行。）"""
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""
    probe = subprocess.run([sys.executable, "-m", "app.worker", "--probe-gpu"],
                           env=env, capture_output=True, timeout=120,
                           cwd=str(__import__("pathlib").Path(__file__).parents[1]))
    has_gpu = _gpu_count() > 0 and os.environ.get("CUDA_VISIBLE_DEVICES", "") != ""
    if has_gpu:
        assert probe.returncode == 0
    else:
        assert probe.returncode != 0, "无可见 GPU 时探测必须失败（supervisor 据此等待恢复）"
