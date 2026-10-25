"""协调平面：任务队列 + 租约 + Worker 注册（Redis 与嵌入式双实现）。

多机架构下的三个核心协调原语：
1. JobQueue    —— 任务队列：API 入队，worker 出队（谁有空谁领取）；
2. LeaseStore  —— 租约/心跳：保证"同一任务同一时刻最多一个写入者"，
                  心跳过期即视为 worker 失联，任务自动回队列由他人接手（fencing token 防僵尸）；
3. 控制信号     —— cancel/pause 经由协调平面传达给正在训练的 worker。

REDIS_URL 为空时自动切换 Embedded 实现（进程内队列/锁，单机零依赖），
接口完全一致——测试与单机开发无需任何外部组件。
"""
from __future__ import annotations

import ast
import json
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Set

import app.config as config

# Redis key 统一前缀，避免与其它系统冲突
_QUEUE_KEY = "mtp:queue:jobs"
_LEASE_KEY = "mtp:lease:{job_id}"
_SIGNAL_KEY = "mtp:signal:{job_id}"
_WORKERS_SET = "mtp:workers:all"
_WORKER_INFO = "mtp:worker:{worker_id}:info"
_WORKER_HB = "mtp:worker:{worker_id}:hb"
_WORKER_ENABLED = "mtp:workers:enabled"

# 信号 TTL：控制信号 1 小时后自动清理，避免遗留脏信号影响后续轮次
_SIGNAL_TTL = 3600

# renew（续租）Lua：token 匹配才续期 —— 僵尸 worker 拿着旧 token 无法续租
_RENEW_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

# release（释放租约）Lua：token 匹配才删除 —— 防止误删他人的租约
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class JobQueue:
    """任务队列接口：控制平面入队，计算平面出队。"""

    def push(self, job_id: str) -> None:
        """任务入队（队尾）。"""
        raise NotImplementedError

    def pop(self, timeout: float = 1.0) -> Optional[str]:
        """阻塞出队一个任务 ID；超时返回 None。"""
        raise NotImplementedError

    def remove(self, job_id: str) -> bool:
        """从队列中移除指定任务（取消/暂停排队任务用）；返回是否移除成功。"""
        raise NotImplementedError

    def rebuild(self, job_ids: List[str]) -> None:
        """清空队列并用给定任务重建（服务重启恢复用，队列是可重建的派生态）。"""
        raise NotImplementedError

    def size(self) -> int:
        """当前排队任务数。"""
        raise NotImplementedError

    def snapshot(self) -> List[str]:
        """当前在队的任务 id 列表（watchdog 防丢失重派用，尽力而为）。"""
        raise NotImplementedError


class EmbeddedJobQueue(JobQueue):
    """进程内队列（单机嵌入式模式）：deque + 条件变量实现阻塞 pop。"""

    def __init__(self) -> None:
        self._items: Deque[str] = deque()
        self._cond = threading.Condition()

    def push(self, job_id: str) -> None:
        with self._cond:
            self._items.append(job_id)
            self._cond.notify()

    def pop(self, timeout: float = 1.0) -> Optional[str]:
        with self._cond:
            if not self._items:
                self._cond.wait(timeout)
            if self._items:
                return self._items.popleft()
            return None

    def remove(self, job_id: str) -> bool:
        with self._cond:
            try:
                self._items.remove(job_id)
                return True
            except ValueError:
                return False

    def rebuild(self, job_ids: List[str]) -> None:
        with self._cond:
            self._items.clear()
            self._items.extend(job_ids)
            self._cond.notify_all()

    def size(self) -> int:
        with self._cond:
            return len(self._items)

    def snapshot(self) -> List[str]:
        with self._cond:
            return list(self._items)


class RedisJobQueue(JobQueue):
    """Redis List 实现的任务队列（多机模式）。"""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    def push(self, job_id: str) -> None:
        # RPUSH 尾插：列表顺序 = 入队顺序（snapshot 与 pop 方向一致）
        self._redis.rpush(_QUEUE_KEY, job_id)

    def pop(self, timeout: float = 1.0) -> Optional[str]:
        # BLPOP 是单命令原子出队（队首）：两个 worker 同时领取不会拿到同一个任务。
        # 阻塞窗口与 socket 超时相等的边界竞态下 redis-py 会抛 TimeoutError，
        # 语义上等价于"窗口内无任务"——捕获并返回 None（worker 退避后重试）。
        try:
            result = self._redis.blpop(_QUEUE_KEY, timeout=max(int(timeout), 1))
        except Exception:
            return None
        if result is None:
            return None
        _, job_id = result
        return job_id

    def remove(self, job_id: str) -> bool:
        # LREM 尽力移除；任务可能已被 worker 领走，返回 False 属正常
        return bool(self._redis.lrem(_QUEUE_KEY, 0, job_id))

    def rebuild(self, job_ids: List[str]) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(_QUEUE_KEY)
        if job_ids:
            pipe.rpush(_QUEUE_KEY, *job_ids)  # 尾插保持原顺序（队首=第一个任务）
        pipe.execute()

    def size(self) -> int:
        return int(self._redis.llen(_QUEUE_KEY))

    def snapshot(self) -> List[str]:
        return [str(item) for item in self._redis.lrange(_QUEUE_KEY, 0, -1)]


class LeaseStore:
    """租约 + 控制信号 + Worker 注册的接口。

    租约语义（保证单写者与防僵尸）：
    - acquire: SET NX —— 只有一个 worker 能拿到任务；
    - renew:   仅当持有者 token 匹配才续期 —— 被回收的旧 worker 无法"复活"；
    - release: 仅当 token 匹配才删除 —— 不误伤新持有者。
    """

    def healthy(self) -> bool:
        """协调平面连通性自检（worker 启动/看门狗周期探测用）。"""
        raise NotImplementedError

    # ---------------- 租约 ----------------

    def acquire(self, job_id: str, token: str, ttl: float) -> bool:
        """尝试获取任务租约；已被他人持有返回 False。"""
        raise NotImplementedError

    def renew(self, job_id: str, token: str, ttl: float) -> bool:
        """续租：token 匹配且租约未失效时延长 ttl；失败说明租约已被回收。"""
        raise NotImplementedError

    def release(self, job_id: str, token: str) -> bool:
        """释放租约（token 匹配才生效）。"""
        raise NotImplementedError

    def peek_holder(self, job_id: str) -> Optional[str]:
        """查看当前租约持有者 token（watchdog 判定失联用）；无租约返回 None。"""
        raise NotImplementedError

    # ---------------- 控制信号（cancel / pause） ----------------

    def set_signal(self, job_id: str, kind: str) -> None:
        """向正在训练的任务发送控制信号（kind: cancel / pause）。"""
        raise NotImplementedError

    def get_signals(self, job_id: str) -> Set[str]:
        """读取当前活跃信号（不清除；worker 领取任务时统一 clear）。"""
        raise NotImplementedError

    def clear_signals(self, job_id: str) -> None:
        """清除任务全部控制信号（worker 领取任务时调用，防止脏信号误伤新一轮）。"""
        raise NotImplementedError

    # ---------------- Worker 注册与心跳 ----------------

    def register_worker(self, worker_id: str, info: Dict[str, object], ttl: float) -> None:
        """worker 启动注册：写入能力信息（显卡/显存/版本）并开启心跳。"""
        raise NotImplementedError

    def heartbeat_worker(self, worker_id: str, fields: Dict[str, object], ttl: float) -> None:
        """worker 心跳：刷新在线 TTL 并更新动态字段（当前任务/epoch 等）。"""
        raise NotImplementedError

    def deregister_worker(self, worker_id: str) -> None:
        """worker 退出时注销。"""
        raise NotImplementedError

    def list_workers(self) -> List[Dict[str, object]]:
        """列出全部注册过的 worker；active 字段标识心跳是否仍在有效期。"""
        raise NotImplementedError

    def set_worker_enabled(self, worker_id: str, enabled: bool) -> None:
        """保存计算槽是否允许接收新任务的设置。"""
        raise NotImplementedError

    def is_worker_enabled(self, worker_id: str, default: bool = True) -> bool:
        """读取计算槽接单开关；未配置时返回调用方给定的兼容默认值。"""
        raise NotImplementedError

    def cleanup_stale_workers(self, grace_seconds: float) -> int:
        """清理心跳长期未更新的 worker 注册条目（崩溃/未优雅退出残留），返回清除数量。"""
        raise NotImplementedError


class EmbeddedLeaseStore(LeaseStore):
    """进程内租约实现：字典 + 锁，语义与 Redis 版完全一致（测试友好）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: Dict[str, List] = {}       # job_id -> [token, expires_at]
        self._signals: Dict[str, Set[str]] = {}   # job_id -> 活跃信号集合
        self._workers: Dict[str, Dict[str, object]] = {}  # worker_id -> info(含 last_seen)
        self._worker_enabled: Dict[str, bool] = {}  # worker_id -> 是否允许接单

    def healthy(self) -> bool:
        return True

    # ---------------- 租约 ----------------

    def acquire(self, job_id: str, token: str, ttl: float) -> bool:
        with self._lock:
            now = time.time()
            holder = self._leases.get(job_id)
            # 无租约或已过期才能获取
            if holder is None or holder[1] <= now:
                self._leases[job_id] = [token, now + ttl]
                return True
            return False

    def renew(self, job_id: str, token: str, ttl: float) -> bool:
        with self._lock:
            holder = self._leases.get(job_id)
            # 与 Redis 语义对齐：过期后 key 已不存在，同 token 也无法续租
            if holder is None or holder[0] != token or holder[1] <= time.time():
                return False
            holder[1] = time.time() + ttl
            return True

    def release(self, job_id: str, token: str) -> bool:
        with self._lock:
            holder = self._leases.get(job_id)
            if holder is not None and holder[0] == token:
                del self._leases[job_id]
                return True
            return False

    def peek_holder(self, job_id: str) -> Optional[str]:
        with self._lock:
            holder = self._leases.get(job_id)
            if holder is None or holder[1] <= time.time():
                return None
            return str(holder[0])

    # ---------------- 控制信号 ----------------

    def set_signal(self, job_id: str, kind: str) -> None:
        with self._lock:
            self._signals.setdefault(job_id, set()).add(kind)

    def get_signals(self, job_id: str) -> Set[str]:
        with self._lock:
            return set(self._signals.get(job_id, set()))

    def clear_signals(self, job_id: str) -> None:
        with self._lock:
            self._signals.pop(job_id, None)

    # ---------------- Worker 注册与心跳 ----------------

    def register_worker(self, worker_id: str, info: Dict[str, object], ttl: float) -> None:
        with self._lock:
            merged = dict(info)
            merged["last_seen"] = time.time()
            merged["ttl"] = ttl
            self._workers[worker_id] = merged

    def heartbeat_worker(self, worker_id: str, fields: Dict[str, object], ttl: float) -> None:
        with self._lock:
            info = self._workers.setdefault(worker_id, {})
            info.update(fields)
            info["last_seen"] = time.time()
            info["ttl"] = ttl

    def deregister_worker(self, worker_id: str) -> None:
        with self._lock:
            self._workers.pop(worker_id, None)

    def list_workers(self) -> List[Dict[str, object]]:
        with self._lock:
            out: List[Dict[str, object]] = []
            for worker_id, info in self._workers.items():
                item = {"worker_id": worker_id}
                item.update(info)
                age = time.time() - float(info.get("last_seen", 0))
                item["active"] = age <= float(info.get("ttl", 30)) * 2
                out.append(item)
            return out

    def set_worker_enabled(self, worker_id: str, enabled: bool) -> None:
        """在进程内记录计算槽接单开关，供嵌入式模式和测试复用。"""
        with self._lock:
            self._worker_enabled[worker_id] = bool(enabled)

    def is_worker_enabled(self, worker_id: str, default: bool = True) -> bool:
        """读取进程内计算槽接单状态；未显式配置时保持旧 worker 默认行为。"""
        with self._lock:
            return self._worker_enabled.get(worker_id, bool(default))

    def cleanup_stale_workers(self, grace_seconds: float) -> int:
        """清理心跳长期未更新的注册条目（存活 worker 心跳远短于宽限期）。"""
        now = time.time()
        removed = 0
        with self._lock:
            for worker_id in list(self._workers.keys()):
                last_seen = float(self._workers[worker_id].get("last_seen", 0))
                if now - last_seen > grace_seconds:
                    del self._workers[worker_id]
                    removed += 1
        return removed


class RedisLeaseStore(LeaseStore):
    """Redis 实现的租约/信号/注册（多机模式）。

    心跳即 TTL：`SET ... EX ttl`，进程崩溃后 key 自动过期——
    无需任何跨服务器时钟比对，watchdog 只看 key 是否存在。
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    def healthy(self) -> bool:
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    # ---------------- 租约 ----------------

    def acquire(self, job_id: str, token: str, ttl: float) -> bool:
        # SET NX EX：原子抢锁，同一任务只有一个 winner
        return bool(self._redis.set(_LEASE_KEY.format(job_id=job_id), token, ex=int(ttl), nx=True))

    def renew(self, job_id: str, token: str, ttl: float) -> bool:
        result = self._redis.eval(
            _RENEW_LUA, 1, _LEASE_KEY.format(job_id=job_id), token, str(int(ttl))
        )
        return bool(result)

    def release(self, job_id: str, token: str) -> bool:
        result = self._redis.eval(
            _RELEASE_LUA, 1, _LEASE_KEY.format(job_id=job_id), token
        )
        return bool(result)

    def peek_holder(self, job_id: str) -> Optional[str]:
        value = self._redis.get(_LEASE_KEY.format(job_id=job_id))
        return str(value) if value else None

    # ---------------- 控制信号 ----------------

    def set_signal(self, job_id: str, kind: str) -> None:
        key = _SIGNAL_KEY.format(job_id=job_id)
        pipe = self._redis.pipeline()
        pipe.hset(key, kind, 1)
        pipe.expire(key, _SIGNAL_TTL)
        pipe.execute()

    def get_signals(self, job_id: str) -> Set[str]:
        value = self._redis.hgetall(_SIGNAL_KEY.format(job_id=job_id))
        return set(value.keys())

    def clear_signals(self, job_id: str) -> None:
        self._redis.delete(_SIGNAL_KEY.format(job_id=job_id))

    # ---------------- Worker 注册与心跳 ----------------

    def register_worker(self, worker_id: str, info: Dict[str, object], ttl: float) -> None:
        payload = {str(k): (jsonable(v)) for k, v in info.items()}
        pipe = self._redis.pipeline()
        pipe.sadd(_WORKERS_SET, worker_id)
        pipe.hset(_WORKER_INFO.format(worker_id=worker_id), mapping=payload)
        pipe.set(_WORKER_HB.format(worker_id=worker_id), "1", ex=int(ttl))
        pipe.execute()

    def heartbeat_worker(self, worker_id: str, fields: Dict[str, object], ttl: float) -> None:
        payload = {str(k): jsonable(v) for k, v in fields.items()}
        payload["last_seen"] = time.time()
        pipe = self._redis.pipeline()
        if payload:
            pipe.hset(_WORKER_INFO.format(worker_id=worker_id), mapping=payload)
        pipe.set(_WORKER_HB.format(worker_id=worker_id), "1", ex=int(ttl))
        pipe.execute()

    def deregister_worker(self, worker_id: str) -> None:
        pipe = self._redis.pipeline()
        pipe.srem(_WORKERS_SET, worker_id)
        pipe.delete(_WORKER_INFO.format(worker_id=worker_id))
        pipe.delete(_WORKER_HB.format(worker_id=worker_id))
        pipe.execute()

    def list_workers(self) -> List[Dict[str, object]]:
        out: List[Dict[str, object]] = []
        for worker_id in self._redis.smembers(_WORKERS_SET):
            info = self._redis.hgetall(_WORKER_INFO.format(worker_id=worker_id))
            if not info:
                continue
            item: Dict[str, object] = {"worker_id": worker_id}
            # Redis 存储值全部为字符串：还原嵌套结构（gpu dict / 数值等）
            item.update({k: unjsonable(v) for k, v in info.items()})
            item["active"] = bool(self._redis.exists(_WORKER_HB.format(worker_id=worker_id)))
            out.append(item)
        return out

    def set_worker_enabled(self, worker_id: str, enabled: bool) -> None:
        """将接单选择写入 Redis，使计算槽重启后仍保留人工选择。"""
        self._redis.hset(_WORKER_ENABLED, worker_id, "1" if enabled else "0")

    def is_worker_enabled(self, worker_id: str, default: bool = True) -> bool:
        """读取 Redis 中的接单选择；尚未人工选择的槽位使用兼容默认值。"""
        value = self._redis.hget(_WORKER_ENABLED, worker_id)
        if value is None:
            return bool(default)
        return str(value) == "1"

    def cleanup_stale_workers(self, grace_seconds: float) -> int:
        """清理心跳 key 已消失且注册信息长期未更新的 worker 条目。

        进程崩溃（未优雅退出）时 HB key 自动过期，但 _WORKERS_SET 与 info 残留；
        watch 侧定期调用本方法清除（存活 worker 心跳远短于宽限期，不受影响）。
        """
        now = time.time()
        removed = 0
        for worker_id in self._redis.smembers(_WORKERS_SET):
            if self._redis.exists(_WORKER_HB.format(worker_id=worker_id)):
                continue  # 心跳存活：worker 在线
            info = self._redis.hgetall(_WORKER_INFO.format(worker_id=worker_id))
            last_seen = info.get("last_seen")
            if last_seen is not None and (now - float(last_seen)) <= grace_seconds:
                continue  # 宽限期内（可能正处于注册/重启间隙）
            pipe = self._redis.pipeline()
            pipe.srem(_WORKERS_SET, worker_id)
            pipe.delete(_WORKER_INFO.format(worker_id=worker_id))
            pipe.delete(_WORKER_HB.format(worker_id=worker_id))
            pipe.execute()
            removed += 1
        return removed


def jsonable(value: object) -> str:
    """把任意值转为 Redis 可存储的字符串（嵌套结构 JSON 编码）。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def unjsonable(value: object) -> object:
    """把 Redis 读回的字符串还原为原始类型（嵌套结构 JSON 解码）。

    兼容旧版 jsonable 的 Python repr 序列化（单引号 dict 等）。
    """
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed = value
    if parsed is None:
        return None
    if isinstance(parsed, (dict, list, bool, int, float)):
        return parsed
    # 旧格式回退：ast.literal_eval 解析 Python repr（普通字符串原样返回）
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError, MemoryError):
        return value
    if parsed is None:
        return None
    if isinstance(parsed, (dict, list, bool, int, float)):
        return parsed
    return value


# ---------------------------------------------------------------------------
# 工厂：按 REDIS_URL 决定 Redis / Embedded 实现
# ---------------------------------------------------------------------------

def create_redis_client():
    """创建 Redis 客户端（decode_responses=True，返回原生 str）。"""
    import redis  # 惰性导入：嵌入式模式无需安装

    return redis.Redis.from_url(
        config.REDIS_URL,
        password=config.REDIS_PASSWORD or None,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )


def build_coordination() -> tuple:
    """按配置构建 (queue, lease_store, distributed: bool)。

    REDIS_URL 为空 -> Embedded 实现（API 进程内置 worker，单机零依赖）；
    否则 -> Redis 实现（多机：API 与各服务器 worker 连同一个 Redis）。
    """
    if not config.REDIS_URL:
        return EmbeddedJobQueue(), EmbeddedLeaseStore(), False
    client = create_redis_client()
    return RedisJobQueue(client), RedisLeaseStore(client), True
