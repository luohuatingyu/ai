"""协调面测试：队列、租约（单写者/fencing）、控制信号、worker 注册。

对应验收标准：
- 租约语义：SET NX 单写者；token 校验续期/释放；过期即失效（不依赖时钟同步）；
- 控制信号：set/get/clear（worker 领取时清脏信号）；
- 队列：数量守恒、重复入队无害、snapshot 供 watchdog 防丢失。
"""
from __future__ import annotations

import time

from app.coordination import EmbeddedJobQueue, EmbeddedLeaseStore


# ---------------------------------------------------------------------------
# 队列
# ---------------------------------------------------------------------------

def test_queue_fifo_and_size():
    q = EmbeddedJobQueue()
    q.push("a"); q.push("b")
    assert q.size() == 2
    assert q.pop(timeout=0.1) == "a"
    assert q.pop(timeout=0.1) == "b"
    assert q.pop(timeout=0.05) is None


def test_queue_snapshot():
    q = EmbeddedJobQueue()
    q.push("a"); q.push("b")
    assert q.snapshot() == ["a", "b"]
    q.pop(timeout=0.1)
    assert q.snapshot() == ["b"]


def test_queue_rebuild():
    q = EmbeddedJobQueue()
    q.push("x")
    q.rebuild(["m", "n"])
    assert q.snapshot() == ["m", "n"]


def test_queue_pop_timeout_returns_none():
    q = EmbeddedJobQueue()
    t0 = time.time()
    assert q.pop(timeout=0.2) is None
    assert time.time() - t0 >= 0.15


# ---------------------------------------------------------------------------
# 租约（fencing 核心语义）
# ---------------------------------------------------------------------------

def test_lease_single_writer():
    """同一任务只有一个 winner（SET NX）。"""
    ls = EmbeddedLeaseStore()
    assert ls.acquire("job-a", "tok-1", ttl=5) is True
    assert ls.acquire("job-a", "tok-2", ttl=5) is False
    assert ls.peek_holder("job-a") == "tok-1"


def test_lease_renew_only_by_holder():
    """续期仅持有者 token 生效；他人 token 续期失败。"""
    ls = EmbeddedLeaseStore()
    ls.acquire("job-a", "tok-1", ttl=5)
    assert ls.renew("job-a", "tok-1", 5) is True
    assert ls.renew("job-a", "tok-2", 5) is False


def test_lease_expired_cannot_renew_but_reacquirable():
    """过期语义与 Redis 对齐：过期后同 token 也无法续租（key 已不存在），
    但任何 worker 可以立即重新获取——watchdog 重派依赖此语义。"""
    ls = EmbeddedLeaseStore()
    ls.acquire("job-a", "tok-1", ttl=0.05)
    time.sleep(0.08)
    assert ls.renew("job-a", "tok-1", 5) is False
    assert ls.acquire("job-a", "tok-2", 5) is True


def test_lease_release_token_guarded():
    """释放仅持有者 token 生效，不误伤新持有者。"""
    ls = EmbeddedLeaseStore()
    ls.acquire("job-a", "tok-1", ttl=5)
    assert ls.release("job-a", "tok-2") is False  # 他人不能释放
    assert ls.peek_holder("job-a") == "tok-1"
    assert ls.release("job-a", "tok-1") is True
    assert ls.peek_holder("job-a") is None


# ---------------------------------------------------------------------------
# 控制信号
# ---------------------------------------------------------------------------

def test_signals_set_get_clear():
    ls = EmbeddedLeaseStore()
    ls.set_signal("job-a", "cancel")
    ls.set_signal("job-a", "pause")
    assert ls.get_signals("job-a") == {"cancel", "pause"}
    ls.clear_signals("job-a")
    assert ls.get_signals("job-a") == set()


def test_signals_per_job_isolated():
    ls = EmbeddedLeaseStore()
    ls.set_signal("job-a", "cancel")
    ls.set_signal("job-b", "pause")
    assert ls.get_signals("job-a") == {"cancel"}
    assert ls.get_signals("job-b") == {"pause"}


# ---------------------------------------------------------------------------
# worker 注册（/api/workers 数据源）
# ---------------------------------------------------------------------------

def test_worker_register_heartbeat_deregister():
    ls = EmbeddedLeaseStore()
    ls.register_worker("w1", {"gpu": {"available": False}}, ttl=5)
    ls.heartbeat_worker("w1", {"state": "idle", "current_job": None}, ttl=5)
    workers = ls.list_workers()
    assert len(workers) == 1 and workers[0]["worker_id"] == "w1"
    assert workers[0]["active"] is True
    ls.deregister_worker("w1")
    assert ls.list_workers() == []


def test_cleanup_stale_workers_embedded():
    """幽灵槽清理：心跳长期未更新的注册被清除，存活 worker 保留。"""
    ls = EmbeddedLeaseStore()
    ls.register_worker("w-old", {"gpu": {"available": False}}, ttl=5)
    ls.register_worker("w-live", {"gpu": {"available": False}}, ttl=5)
    # 人为回拨 last_seen，模拟崩溃后心跳永久停止
    with ls._lock:
        ls._workers["w-old"]["last_seen"] = time.time() - 1000
    removed = ls.cleanup_stale_workers(grace_seconds=300)
    assert removed == 1
    assert [w["worker_id"] for w in ls.list_workers()] == ["w-live"]


def test_lease_store_healthy():
    assert EmbeddedLeaseStore().healthy() is True


def test_worker_selection_is_saved_independently_of_heartbeat():
    """外部槽默认待启用，人工选择在 worker 断线重连后仍保留。"""
    ls = EmbeddedLeaseStore()
    ls.register_worker("remote-a-cpu0", {"managed": True}, ttl=5)

    assert ls.is_worker_enabled("remote-a-cpu0", default=False) is False
    ls.set_worker_enabled("remote-a-cpu0", True)
    ls.deregister_worker("remote-a-cpu0")
    ls.register_worker("remote-a-cpu0", {"managed": True}, ttl=5)
    assert ls.is_worker_enabled("remote-a-cpu0", default=False) is True

    ls.set_worker_enabled("remote-a-cpu0", False)
    assert ls.is_worker_enabled("remote-a-cpu0", default=True) is False


def test_remote_server_and_operation_metadata_roundtrip():
    """远程连接元数据与操作状态可持久读取，且服务器记录不要求 SSH 私钥字段。"""
    ls = EmbeddedLeaseStore()
    server = {
        "machine_id": "remote-server-a",
        "host": "compute.example.net",
        "username": "worker",
        "port": 22,
        "mode": "gpu",
        "status": "provisioning",
    }
    ls.set_remote_server("remote-server-a", server)
    operation = {"operation_id": "op-a", "status": "queued", "machine_id": "remote-server-a"}
    ls.set_remote_operation("op-a", operation)

    assert ls.get_remote_server("remote-server-a") == server
    assert ls.list_remote_servers() == [server]
    assert ls.get_remote_operation("op-a") == operation


def test_build_coordination_embedded():
    """兼容性：REDIS_URL 为空时返回嵌入式实现（单机零依赖形态）。"""
    import pytest

    from app import config
    from app.coordination import build_coordination

    if config.REDIS_URL:
        pytest.skip("REDIS_URL 已配置（多机模式），嵌入式回退断言不适用")
    queue, lease, distributed = build_coordination()
    assert distributed is False
    assert isinstance(queue, EmbeddedJobQueue)
    assert isinstance(lease, EmbeddedLeaseStore)


# ---------------------------------------------------------------------------
# Redis 实现语义一致性（有 Redis 时执行；CI 无 Redis 自动跳过）
# ---------------------------------------------------------------------------

def test_unjsonable_legacy_repr_fallback():
    """兼容性：旧版 jsonable 的 Python repr 序列化（单引号 dict）可还原。"""
    from app.coordination import unjsonable

    assert unjsonable("{'available': False, 'name': None, 'count': 0, 'vram_mb': 0}") == {
        "available": False, "name": None, "count": 0, "vram_mb": 0}
    assert unjsonable("somehost") == "somehost"      # 普通字符串原样返回
    assert unjsonable("'a:b'") == "'a:b'"            # 引号包裹的字符串保守保留原值（避免歧义）
    assert unjsonable("[1, 2]") == [1, 2]            # JSON 优先路径
    assert unjsonable("None") is None                 # 旧 Redis 字段中的空值应还原为空
    assert unjsonable("null") is None                 # JSON null 同样还原为空
    assert unjsonable(3) == 3                        # 非字符串原样


def test_redis_semantics_match_embedded():
    """同一脚本在 Redis 实现上复跑全部租约/信号断言（需 MTP_TEST_REDIS）。"""
    import os

    if not os.environ.get("MTP_TEST_REDIS"):
        import pytest

        pytest.skip("未配置 MTP_TEST_REDIS，跳过 Redis 语义一致性测试")
    import redis as redislib

    from app.coordination import RedisJobQueue, RedisLeaseStore

    client = redislib.Redis.from_url(os.environ["MTP_TEST_REDIS"], decode_responses=True)
    client.flushdb()
    q, ls = RedisJobQueue(client), RedisLeaseStore(client)

    q.push("a"); q.push("b")
    assert q.size() == 2 and q.snapshot() == ["a", "b"]
    assert q.pop(timeout=1) == "a"

    assert ls.acquire("job-r", "t1", 5) is True
    assert ls.acquire("job-r", "t2", 5) is False
    assert ls.renew("job-r", "t1", 5) is True
    assert ls.renew("job-r", "t2", 5) is False
    assert ls.release("job-r", "t2") is False
    assert ls.release("job-r", "t1") is True

    ls.set_signal("job-r", "cancel")
    assert "cancel" in ls.get_signals("job-r")
    ls.clear_signals("job-r")
    assert ls.get_signals("job-r") == set()

    ls.register_worker("wr", {"gpu": {"available": False}}, ttl=5)
    assert ls.list_workers()[0]["worker_id"] == "wr"
    ls.set_worker_enabled("wr", False)
    assert ls.is_worker_enabled("wr", default=True) is False
    ls.deregister_worker("wr")
    assert ls.is_worker_enabled("wr", default=True) is False

    server = {"machine_id": "remote-wr", "host": "compute.example.net", "port": 22}
    ls.set_remote_server("remote-wr", server)
    assert ls.get_remote_server("remote-wr") == server
    assert ls.list_remote_servers() == [server]
    operation = {"operation_id": "remote-op", "status": "queued"}
    ls.set_remote_operation("remote-op", operation)
    assert ls.get_remote_operation("remote-op") == operation


def test_redis_worker_registry_roundtrip_nested_caps():
    """Redis 注册往返：嵌套 gpu dict / 数值字段经序列化后类型还原（/api/workers 数据源）。"""
    import os

    if not os.environ.get("MTP_TEST_REDIS"):
        import pytest

        pytest.skip("未配置 MTP_TEST_REDIS，跳过 Redis 注册往返测试")
    import redis as redislib

    from app.coordination import RedisLeaseStore

    client = redislib.Redis.from_url(os.environ["MTP_TEST_REDIS"], decode_responses=True)
    client.flushdb()
    ls = RedisLeaseStore(client)
    caps = {
        "worker_id": "w-nested", "machine_id": "m1", "slot": "gpu0",
        "hostname": "h1", "gpu": {"available": True, "name": "TestGPU-8G",
                                  "count": 1, "vram_mb": 8192},
        "torch_version": "2.8.0", "cuda_version": None,
        "python_version": "3", "started_at": 1700000000.5,
    }
    ls.register_worker("w-nested", caps, ttl=30)
    ls.heartbeat_worker("w-nested", {"state": "running", "current_job": "job-x", "epoch": 42}, ttl=30)
    workers = ls.list_workers()
    assert len(workers) == 1
    w = workers[0]
    gpu = w["gpu"]
    assert isinstance(gpu, dict), f"gpu 应还原为 dict: {gpu!r}"
    assert gpu["available"] is True and gpu["vram_mb"] == 8192
    assert w["epoch"] == 42 and isinstance(w["epoch"], int)
    assert w["current_job"] == "job-x" and w["state"] == "running"
    assert w["started_at"] == 1700000000.5
    assert w["active"] is True
    ls.deregister_worker("w-nested")


def test_cleanup_stale_workers_redis():
    """Redis 幽灵槽清理：HB 消失且 last_seen 超宽限的条目被清除，在线 worker 保留。"""
    import os

    if not os.environ.get("MTP_TEST_REDIS"):
        import pytest

        pytest.skip("未配置 MTP_TEST_REDIS，跳过 Redis 幽灵槽清理测试")
    import redis as redislib

    from app.coordination import RedisLeaseStore

    client = redislib.Redis.from_url(os.environ["MTP_TEST_REDIS"], decode_responses=True)
    client.flushdb()
    ls = RedisLeaseStore(client)
    ls.register_worker("w-dead", {"gpu": {"available": False}}, ttl=5)
    ls.register_worker("w-live", {"gpu": {"available": False}}, ttl=300)
    ls.heartbeat_worker("w-live", {"state": "idle"}, ttl=300)  # 注册后立即心跳（真实时序）
    # 模拟崩溃：删除 HB key + 回拨 last_seen
    client.delete("mtp:worker:w-dead:hb")
    client.hset("mtp:worker:w-dead:info", "last_seen", time.time() - 1000)
    try:
        removed = ls.cleanup_stale_workers(grace_seconds=300)
        assert removed == 1
        ids = [w["worker_id"] for w in ls.list_workers()]
        assert "w-dead" not in ids and "w-live" in ids
        # 宽限期内不清理：HB 消失但 last_seen 新鲜（注册/重启间隙）
        client.delete("mtp:worker:w-live:hb")
        assert ls.cleanup_stale_workers(grace_seconds=300) == 0
    finally:
        ls.deregister_worker("w-dead")
        ls.deregister_worker("w-live")


def test_embedded_remote_ssh_credentials_store_opaque_ciphertext():
    """协调存储的远程凭证接口只接收密文，并支持清除。"""
    ls = EmbeddedLeaseStore()
    ciphertext = "gAAAAABtest-encrypted-value"

    assert ls.get_remote_ssh_credentials() is None
    ls.set_remote_ssh_credentials(ciphertext)
    assert ls.get_remote_ssh_credentials() == ciphertext
    ls.delete_remote_ssh_credentials()
    assert ls.get_remote_ssh_credentials() is None


def test_redis_remote_ssh_credentials_use_dedicated_key():
    """Redis 凭证密文写入独立 key，并可读回和删除。"""
    from app.coordination import RedisLeaseStore

    class FakeRedis:
        def __init__(self):
            self.values = {}

        def set(self, key, value):
            self.values[key] = value

        def get(self, key):
            return self.values.get(key)

        def delete(self, key):
            return self.values.pop(key, None) is not None

    redis = FakeRedis()
    ls = RedisLeaseStore(redis)
    ciphertext = "fernet-ciphertext-only"

    ls.set_remote_ssh_credentials(ciphertext)
    assert redis.values == {"mtp:remote:ssh_credentials": ciphertext}
    assert ls.get_remote_ssh_credentials() == ciphertext
    ls.delete_remote_ssh_credentials()
    assert redis.values == {}
