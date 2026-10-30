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
import hashlib
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
_REMOTE_SERVERS = "mtp:remote:servers"
_REMOTE_SSH_CREDENTIALS = "mtp:remote:ssh_credentials"
_REMOTE_OPERATION = "mtp:remote:operation:{operation_id}"
_REMOTE_OPERATION_CANCEL = "mtp:remote:operation:cancel:{operation_id}"
_REMOTE_OPERATION_EXECUTOR = "mtp:remote:operation:executor:{operation_id}"
_REMOTE_OPERATION_TTL = 7 * 24 * 3600
_REMOTE_EXECUTOR_TTL = 90

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

# 状态与租约在同一事务内校验，禁止旧执行者或空闲轮询覆盖新占用者。
_GUARDED_HEARTBEAT_LUA = """
local expected = cjson.decode(ARGV[4])
for i = 4, #KEYS do
  local actual = redis.call('GET', KEYS[i])
  local token = expected[i - 3]
  if token == cjson.null then
    if actual then return 0 end
  elseif actual ~= token then
    return 0
  end
end
local fields = cjson.decode(ARGV[3])
redis.call('SADD', KEYS[1], ARGV[1])
for key, value in pairs(fields) do
  redis.call('HSET', KEYS[2], key, value)
end
redis.call('SET', KEYS[3], '1', 'EX', ARGV[2])
return 1
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

    def inference_queue(self, worker_id: str) -> JobQueue:
        """在同一协调实例中共享各计算槽的独立预测队列。"""
        queues = self.__dict__.setdefault("_inference_queues", {})
        return queues.setdefault(worker_id, EmbeddedJobQueue())


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

    def __init__(self, redis_client, queue_key: str = _QUEUE_KEY) -> None:
        """允许独立业务队列复用协调连接，默认保持训练队列不变。"""
        self._redis = redis_client
        self._queue_key = queue_key

    def inference_queue(self, worker_id: str) -> JobQueue:
        """按计算槽分队列，避免其它 Worker 领取指定资源的预测。"""
        suffix = hashlib.sha256(worker_id.encode()).hexdigest()
        return RedisJobQueue(self._redis, f"mtp:queue:inference:{suffix}")

    def push(self, job_id: str) -> None:
        # RPUSH 尾插：列表顺序 = 入队顺序（snapshot 与 pop 方向一致）
        self._redis.rpush(self._queue_key, job_id)

    def pop(self, timeout: float = 1.0) -> Optional[str]:
        # BLPOP 是单命令原子出队（队首）：两个 worker 同时领取不会拿到同一个任务。
        # 阻塞窗口与 socket 超时相等的边界竞态下 redis-py 会抛 TimeoutError，
        # 语义上等价于"窗口内无任务"——捕获并返回 None（worker 退避后重试）。
        try:
            if timeout <= 0:
                return self._redis.lpop(self._queue_key)
            result = self._redis.blpop(self._queue_key, timeout=max(int(timeout), 1))
        except Exception:
            return None
        if result is None:
            return None
        _, job_id = result
        return job_id

    def remove(self, job_id: str) -> bool:
        # LREM 尽力移除；任务可能已被 worker 领走，返回 False 属正常
        return bool(self._redis.lrem(self._queue_key, 0, job_id))

    def rebuild(self, job_ids: List[str]) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(self._queue_key)
        if job_ids:
            pipe.rpush(self._queue_key, *job_ids)  # 尾插保持原顺序（队首=第一个任务）
        pipe.execute()

    def size(self) -> int:
        return int(self._redis.llen(self._queue_key))

    def snapshot(self) -> List[str]:
        return [str(item) for item in self._redis.lrange(self._queue_key, 0, -1)]


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

    def heartbeat_worker(self, worker_id: str, fields: Dict[str, object], ttl: float,
                         *, lease_conditions: Optional[Dict[str, Optional[str]]] = None) -> bool:
        """原子校验可选租约条件后发布心跳；条件值 None 表示该租约必须空闲。"""
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

    def set_remote_server(self, machine_id: str, info: Dict[str, object]) -> None:
        """保存不含密钥的远程服务器连接信息。"""
        raise NotImplementedError

    def get_remote_server(self, machine_id: str) -> Optional[Dict[str, object]]:
        """读取远程服务器连接信息。"""
        raise NotImplementedError

    def list_remote_servers(self) -> List[Dict[str, object]]:
        """列出已登记的远程服务器连接信息。"""
        raise NotImplementedError

    def delete_remote_server(self, machine_id: str) -> None:
        """删除一条远程服务器连接记录，不访问目标服务器。"""
        raise NotImplementedError

    def set_remote_ssh_credentials(self, encrypted_credentials: str) -> None:
        """保存已加密的平台级 SSH 凭证。"""
        raise NotImplementedError

    def get_remote_ssh_credentials(self) -> Optional[str]:
        """读取已加密的平台级 SSH 凭证。"""
        raise NotImplementedError

    def delete_remote_ssh_credentials(self) -> None:
        """清除页面保存的平台级 SSH 凭证。"""
        raise NotImplementedError

    def set_remote_operation(self, operation_id: str, info: Dict[str, object]) -> None:
        """保存远程 SSH 操作进度。"""
        raise NotImplementedError

    def get_remote_operation(self, operation_id: str) -> Optional[Dict[str, object]]:
        """读取远程 SSH 操作进度。"""
        raise NotImplementedError

    def request_remote_operation_cancel(self, operation_id: str) -> None:
        """请求中断远程 SSH 操作，供操作执行器轮询。"""
        raise NotImplementedError

    def is_remote_operation_cancel_requested(self, operation_id: str) -> bool:
        """检查远程 SSH 操作是否收到中断请求。"""
        raise NotImplementedError

    def clear_remote_operation_cancel(self, operation_id: str) -> None:
        """清除远程 SSH 操作的中断请求。"""
        raise NotImplementedError

    def set_remote_operation_executor(self, operation_id: str) -> None:
        """写入远程操作执行者的存活标记（带 TTL，执行进程周期性续写）。"""
        raise NotImplementedError

    def is_remote_operation_executor_active(self, operation_id: str) -> bool:
        """检查远程操作执行者是否仍在存活周期内。"""
        raise NotImplementedError

    def clear_remote_operation_executor(self, operation_id: str) -> None:
        """清除远程操作执行者存活标记（操作正常结束时调用）。"""
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
        self._remote_servers: Dict[str, Dict[str, object]] = {}
        self._remote_operations: Dict[str, Dict[str, object]] = {}
        self._remote_operation_cancellations: Set[str] = set()
        self._remote_ssh_credentials: Optional[str] = None

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

    def heartbeat_worker(self, worker_id: str, fields: Dict[str, object], ttl: float,
                         *, lease_conditions: Optional[Dict[str, Optional[str]]] = None) -> bool:
        """持锁核对所有权再更新状态，空闲发布不能越过并发资源预留。"""
        with self._lock:
            now = time.time()
            for key, expected in (lease_conditions or {}).items():
                holder = self._leases.get(key)
                actual = holder[0] if holder is not None and holder[1] > now else None
                if actual != expected:
                    return False
            info = self._workers.setdefault(worker_id, {})
            info.update(fields)
            info["last_seen"] = now
            info["ttl"] = ttl
            return True

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

    def set_remote_server(self, machine_id: str, info: Dict[str, object]) -> None:
        """在进程内保存服务器元数据；敏感 Redis URL 由调用方先加密。"""
        with self._lock:
            self._remote_servers[machine_id] = dict(info)

    def get_remote_server(self, machine_id: str) -> Optional[Dict[str, object]]:
        """读取进程内远程服务器元数据副本。"""
        with self._lock:
            info = self._remote_servers.get(machine_id)
            return dict(info) if info is not None else None

    def list_remote_servers(self) -> List[Dict[str, object]]:
        """列出进程内登记的远程服务器。"""
        with self._lock:
            return [dict(info) for info in self._remote_servers.values()]

    def delete_remote_server(self, machine_id: str) -> None:
        """删除进程内远程服务器元数据。"""
        with self._lock:
            self._remote_servers.pop(machine_id, None)

    def set_remote_ssh_credentials(self, encrypted_credentials: str) -> None:
        """在进程内保存 Fernet 密文，供嵌入式模式和测试复用。"""
        with self._lock:
            self._remote_ssh_credentials = encrypted_credentials

    def get_remote_ssh_credentials(self) -> Optional[str]:
        """读取进程内的平台 SSH 凭证密文副本。"""
        with self._lock:
            return self._remote_ssh_credentials

    def delete_remote_ssh_credentials(self) -> None:
        """清除进程内的平台 SSH 凭证密文。"""
        with self._lock:
            self._remote_ssh_credentials = None

    def set_remote_operation(self, operation_id: str, info: Dict[str, object]) -> None:
        """在进程内保存远程操作状态副本。"""
        with self._lock:
            self._remote_operations[operation_id] = dict(info)

    def get_remote_operation(self, operation_id: str) -> Optional[Dict[str, object]]:
        """读取进程内远程操作状态副本。"""
        with self._lock:
            info = self._remote_operations.get(operation_id)
            return dict(info) if info is not None else None

    def request_remote_operation_cancel(self, operation_id: str) -> None:
        """在进程内登记远程 SSH 操作的中断请求。"""
        with self._lock:
            self._remote_operation_cancellations.add(operation_id)

    def is_remote_operation_cancel_requested(self, operation_id: str) -> bool:
        """检查进程内是否登记了远程 SSH 操作的中断请求。"""
        with self._lock:
            return operation_id in self._remote_operation_cancellations

    def clear_remote_operation_cancel(self, operation_id: str) -> None:
        """清除进程内远程 SSH 操作的中断请求。"""
        with self._lock:
            self._remote_operation_cancellations.discard(operation_id)

    def set_remote_operation_executor(self, operation_id: str) -> None:
        """嵌入式实现无跨进程存活信息，执行者活性由调用方进程内注册表判断。"""
        return

    def is_remote_operation_executor_active(self, operation_id: str) -> bool:
        return False

    def clear_remote_operation_executor(self, operation_id: str) -> None:
        return

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
        """保留本进程注册的能力快照，供 Redis 注册信息丢失后恢复。"""
        self._redis = redis_client
        self._worker_capabilities: Dict[str, Dict[str, str]] = {}

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
        """缓存序列化后的启动能力，并用同一心跳事务发布完整注册信息。"""
        self._worker_capabilities[worker_id] = {str(k): jsonable(v) for k, v in info.items()}
        self.heartbeat_worker(worker_id, {}, ttl)

    def heartbeat_worker(self, worker_id: str, fields: Dict[str, object], ttl: float,
                         *, lease_conditions: Optional[Dict[str, Optional[str]]] = None) -> bool:
        """原子校验租约并补齐索引、能力与状态，兼容未传条件的旧调用方。"""
        # 能力快照按槽隔离；动态进度最后合并，不能被启动时的旧状态覆盖。
        payload = dict(self._worker_capabilities.get(worker_id, {}))
        payload.update({str(k): jsonable(v) for k, v in fields.items()})
        payload["last_seen"] = time.time()
        if lease_conditions:
            keys = [_WORKERS_SET, _WORKER_INFO.format(worker_id=worker_id),
                    _WORKER_HB.format(worker_id=worker_id)]
            keys.extend(_LEASE_KEY.format(job_id=key) for key in lease_conditions)
            return bool(self._redis.eval(
                _GUARDED_HEARTBEAT_LUA, len(keys), *keys, worker_id, str(int(ttl)),
                json.dumps(payload), json.dumps(list(lease_conditions.values())),
            ))
        pipe = self._redis.pipeline()
        pipe.sadd(_WORKERS_SET, worker_id)
        pipe.hset(_WORKER_INFO.format(worker_id=worker_id), mapping=payload)
        pipe.set(_WORKER_HB.format(worker_id=worker_id), "1", ex=int(ttl))
        pipe.execute()
        return True

    def deregister_worker(self, worker_id: str) -> None:
        """显式注销时丢弃能力缓存，避免复用槽 ID 时恢复旧硬件信息。"""
        self._worker_capabilities.pop(worker_id, None)
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

    def set_remote_server(self, machine_id: str, info: Dict[str, object]) -> None:
        """将远程服务器配置写入 Redis；可能含密码的 Redis URL 只保存加密密文。"""
        self._redis.hset(_REMOTE_SERVERS, machine_id, json.dumps(info, ensure_ascii=False))

    def get_remote_server(self, machine_id: str) -> Optional[Dict[str, object]]:
        """从 Redis 读取一个远程服务器配置。"""
        value = self._redis.hget(_REMOTE_SERVERS, machine_id)
        if value is None:
            return None
        parsed = unjsonable(value)
        return parsed if isinstance(parsed, dict) else None

    def list_remote_servers(self) -> List[Dict[str, object]]:
        """从 Redis 列出服务器配置；SSH 凭证不进入记录，Redis URL 覆盖值仅有密文。"""
        values = self._redis.hgetall(_REMOTE_SERVERS)
        out = []
        for machine_id, value in values.items():
            parsed = unjsonable(value)
            if isinstance(parsed, dict):
                parsed.setdefault("machine_id", str(machine_id))
                out.append(parsed)
        return sorted(out, key=lambda item: str(item.get("machine_id", "")))

    def set_remote_operation(self, operation_id: str, info: Dict[str, object]) -> None:
        """将远程操作状态写入 Redis，供页面轮询和 API 重启后查询。"""
        self._redis.set(
            _REMOTE_OPERATION.format(operation_id=operation_id),
            json.dumps(info, ensure_ascii=False),
            ex=_REMOTE_OPERATION_TTL,
        )

    def get_remote_operation(self, operation_id: str) -> Optional[Dict[str, object]]:
        """从 Redis 读取远程操作状态。"""
        value = self._redis.get(_REMOTE_OPERATION.format(operation_id=operation_id))
        if value is None:
            return None
        parsed = unjsonable(value)
        return parsed if isinstance(parsed, dict) else None

    def request_remote_operation_cancel(self, operation_id: str) -> None:
        """将中断请求写入 Redis，供执行操作的 API 实例轮询。"""
        self._redis.set(
            _REMOTE_OPERATION_CANCEL.format(operation_id=operation_id),
            "1",
            ex=_REMOTE_OPERATION_TTL,
        )

    def is_remote_operation_cancel_requested(self, operation_id: str) -> bool:
        """从 Redis 检查远程操作是否收到中断请求。"""
        return bool(self._redis.get(_REMOTE_OPERATION_CANCEL.format(operation_id=operation_id)))

    def clear_remote_operation_cancel(self, operation_id: str) -> None:
        """清除 Redis 中远程操作的中断请求。"""
        self._redis.delete(_REMOTE_OPERATION_CANCEL.format(operation_id=operation_id))

    def set_remote_operation_executor(self, operation_id: str) -> None:
        """写入远程操作执行者存活标记；TTL 到期即判定执行进程已消失。"""
        self._redis.set(
            _REMOTE_OPERATION_EXECUTOR.format(operation_id=operation_id),
            "1",
            ex=_REMOTE_EXECUTOR_TTL,
        )

    def is_remote_operation_executor_active(self, operation_id: str) -> bool:
        """执行者存活标记存在即视为仍在执行。"""
        return bool(
            self._redis.get(_REMOTE_OPERATION_EXECUTOR.format(operation_id=operation_id))
        )

    def clear_remote_operation_executor(self, operation_id: str) -> None:
        """操作正常结束后移除执行者存活标记。"""
        self._redis.delete(_REMOTE_OPERATION_EXECUTOR.format(operation_id=operation_id))

    def delete_remote_server(self, machine_id: str) -> None:
        """从 Redis 删除远程服务器元数据，不访问目标服务器。"""
        self._redis.hdel(_REMOTE_SERVERS, machine_id)

    def set_remote_ssh_credentials(self, encrypted_credentials: str) -> None:
        """将 Fernet 密文写入独立 Redis key，避免和服务器元数据混存。"""
        self._redis.set(_REMOTE_SSH_CREDENTIALS, encrypted_credentials)

    def get_remote_ssh_credentials(self) -> Optional[str]:
        """读取独立 Redis key 中的平台 SSH 凭证密文。"""
        value = self._redis.get(_REMOTE_SSH_CREDENTIALS)
        return str(value) if value is not None else None

    def delete_remote_ssh_credentials(self) -> None:
        """从 Redis 清除页面保存的平台 SSH 凭证密文。"""
        self._redis.delete(_REMOTE_SSH_CREDENTIALS)

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
