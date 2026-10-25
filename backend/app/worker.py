"""无状态训练 Worker：GPU 只是可随时释放的纯计算单元。

生命周期（每个任务）：
    出队 -> 校验资源提示 -> 抢租约(SET NX) -> 拉取历史产物+最新checkpoint
    -> 标记 running -> 训练(逐 epoch 同步 metrics/日志/checkpoint 到状态平面)
    -> 完成/暂停/取消/失败 -> 释放租约 -> 清理本地 scratch

设计约束：
- 本地磁盘只是 scratch（缓存），所有训练语义状态都写状态平面（对象存储/本地存储抽象）；
- 任何时刻同一任务只有一个租约持有者（单写者），心跳丢失即被 watchdog 重派，
  本节点在下一个 epoch 边界优雅退出（先存档再停，绝不双写）；
- checkpoint 全 CPU 化：A 服务器训练 1 小时后释放，B 服务器用不同型号显卡即可接手续训。
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import app.config as config
from app import jobstore
from app.coordination import JobQueue, LeaseStore, build_coordination
from app.storage import (
    ArtifactStore,
    checkpoint_key,
    get_store,
    job_key,
    parse_checkpoint_epoch,
    prune_checkpoints,
    resolve_dataset_for_worker,
)
from app.trainer import JobCancelled, JobPaused, load_trainer_state, run_training


def detect_capabilities(
    worker_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    slot: Optional[str] = None,
) -> dict:
    """探测本机计算能力（GPU 型号/显存/软件版本）。

    worker 启动时调用一次：既是 /api/workers 展示的数据源，
    也是资源提示匹配（require_gpu / min_vram）的依据。
    GPU 探测失败不阻塞启动（退化为 CPU 计算单元）。

    machine_id / slot：一卡一进程模式下，Supervisor 传入机器与卡槽标识，
    使 /api/workers 可按「机器 -> 卡」两级聚合展示。
    """
    gpu = {"available": False, "name": None, "count": 0, "vram_mb": 0}
    try:
        import torch

        if torch.cuda.is_available():
            gpu = {
                "available": True,
                "name": torch.cuda.get_device_name(0),
                "count": int(torch.cuda.device_count()),
                "vram_mb": int(torch.cuda.get_device_properties(0).total_memory // 2**20),
            }
    except Exception:
        pass
    try:
        import torch as _torch

        torch_version, cuda_version = str(_torch.__version__), _torch.version.cuda
    except Exception:
        torch_version, cuda_version = "未知", None
    hostname = socket.gethostname()
    return {
        "worker_id": worker_id or f"worker-{hostname}-{uuid.uuid4().hex[:6]}",
        "machine_id": machine_id or config.WORKER_MACHINE_ID or hostname,
        "slot": slot or "cpu0",
        "hostname": hostname,
        "managed": config.WORKER_MANAGED,
        "cpu": {"cores": _detect_cpu_cores(), "memory_mb": _detect_memory_mb()},
        "gpu": gpu,
        "torch_version": torch_version,
        "cuda_version": cuda_version,
        "python_version": platform.python_version(),
        "started_at": time.time(),
    }


def _detect_cpu_cores() -> int:
    """优先按容器 CPU 亲和性返回可用核心数，回退到系统逻辑核心数。"""
    try:
        return max(len(os.sched_getaffinity(0)), 1)
    except (AttributeError, OSError):
        return max(int(os.cpu_count() or 1), 1)


def _detect_memory_mb() -> Optional[int]:
    """探测容器可用内存上限，结合系统总内存避免把整台宿主机内存误报给容器。"""
    limits: List[int] = []
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path, "r", encoding="utf-8") as memory_file:
                raw = memory_file.read().strip()
            if raw != "max":
                value = int(raw)
                if 0 < value < 2**60:
                    limits.append(value)
        except (OSError, ValueError):
            pass
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        if pages > 0 and page_size > 0:
            limits.append(pages * page_size)
    except (AttributeError, OSError, ValueError):
        pass
    return max(min(limits) // (1024 * 1024), 1) if limits else None


def probe_visible_gpu() -> bool:
    """GPU 软探测：验证当前进程可见的 GPU 能完成一次微型张量运算。

    供两类场景使用：
    - 子进程心跳周期内自检（CUDA_VISIBLE_DEVICES 已隔离到单卡）；
    - `python -m app.worker --probe-gpu` 探测子进程（Supervisor 恢复判定）。
    任何异常均视为不健康，绝不抛出——探测本身必须先于一切可靠。
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        x = torch.tensor([2.0], device="cuda")
        return float((x * 2).item()) == 4.0
    except Exception:
        return False


# 环境性错误特征（可重试）：硬件/驱动/网络/存储瞬断——换卡/换机/稍后重试即可能成功
_RETRYABLE_PATTERNS = (
    "cuda", "out of memory", "oom", "device-side assert", "nccl",
    "connection reset", "connection refused", "timed out", "timeout",
    "temporarily unavailable", "broken pipe", "no space left",
    "storage", "rpc error", "econnreset",
)

# 业务性错误类型（不可重试）：重试也无法改变结果，直接判 FAILED
_FATAL_EXCEPTION_TYPES = (FileNotFoundError, ValueError, KeyError)


def classify_retryable(exc: BaseException) -> bool:
    """失败分类：环境性错误重试（带 checkpoint 续试），业务性错误直接失败。

    规则：
    - FileNotFoundError / ValueError / KeyError 视为业务性错误（数据集缺失、
      超参/格式非法、续训不兼容等），重试无意义；
    - 其余异常（RuntimeError/OSError/ConnectionError 等）按错误文本匹配
      环境性特征词（CUDA/OOM/网络瞬断/存储故障）判定可重试。
    """
    if isinstance(exc, _FATAL_EXCEPTION_TYPES):
        return False
    text = f"{exc}".lower()
    return any(p in text for p in _RETRYABLE_PATTERNS)


class JobController:
    """单任务控制信号容器：cancel/pause 事件 + 租约续期心跳线程。

    - 分布式模式：后台线程每 heartbeat 周期做三件事——
      读取协调平面信号（cancel/pause -> 置本地事件）、续租（token 校验）、刷新 worker 心跳；
    - 租约续期失败（被 watchdog 重派/心跳丢失）：立即置 pause 事件，
      训练器在下一个 epoch/批次边界存档并退出，天然杜绝双写者；
    - 嵌入式模式与分布式共用同一套逻辑（LeaseStore 接口一致）。
    """

    def __init__(
        self,
        lease_store: LeaseStore,
        job_id: str,
        token: str,
        worker_id: str,
        interval: float,
        ttl: float,
    ) -> None:
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.lease_lost = threading.Event()  # 租约已被回收（本节点已无写入资格）
        self._lease_store = lease_store
        self._job_id = job_id
        self._token = token
        self._worker_id = worker_id
        self._interval = max(float(interval), 0.2)
        self._ttl = float(ttl)
        self._hb_ttl = max(float(ttl) * 2.0, self._interval * 3.0)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._err_since: Optional[float] = None
        self._epoch = 0
        self._state = "starting"

    def set_progress(self, epoch: int, state: str = "training") -> None:
        """训练循环回调：更新当前进度（心跳里上报给 /api/workers 展示）。"""
        self._epoch = int(epoch)
        self._state = state

    def start(self) -> None:
        """启动心跳/续租线程。"""
        self._thread = threading.Thread(
            target=self._loop, name=f"hb-{self._job_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """停止心跳线程（任务收尾时调用）。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval * 3 + 1.0)
            self._thread = None

    def _loop(self) -> None:
        """心跳循环：信号同步 + 租约续期 + worker 存活性刷新。"""
        while not self._stop_event.wait(self._interval):
            try:
                signals = self._lease_store.get_signals(self._job_id)
                if "cancel" in signals:
                    self.cancel_event.set()
                if "pause" in signals:
                    self.pause_event.set()
                if not self._lease_store.renew(self._job_id, self._token, self._ttl):
                    # token 不匹配/租约已被回收：本节点失去写入资格，优雅暂停
                    self.lease_lost.set()
                    self.pause_event.set()
                else:
                    self._lease_store.heartbeat_worker(
                        self._worker_id,
                        {
                            "current_job": self._job_id,
                            "epoch": self._epoch,
                            "state": self._state,
                        },
                        self._hb_ttl,
                    )
                self._err_since = None
            except Exception:
                # 协调平面瞬时不可达：先容忍网络抖动；持续失联超过租约期则让位
                now = time.time()
                if self._err_since is None:
                    self._err_since = now
                elif now - self._err_since > self._ttl:
                    self.lease_lost.set()
                    self.pause_event.set()


class TrainingWorker:
    """训练执行器：单线程领取-执行循环；嵌入式模式下由多个线程实例并行。"""

    def __init__(
        self,
        store: Optional[ArtifactStore] = None,
        queue: Optional[JobQueue] = None,
        lease_store: Optional[LeaseStore] = None,
        capabilities: Optional[dict] = None,
        idle_poll: float = 1.0,
        log: Callable[[str], None] = print,
        gpu_probe_enabled: Optional[bool] = None,
    ) -> None:
        self.store = store or get_store()
        self.queue = queue  # type: ignore[assignment]
        self.lease_store = lease_store  # type: ignore[assignment]
        self.caps = capabilities or detect_capabilities()
        self.worker_id = str(self.caps["worker_id"])
        self._idle_poll = max(float(idle_poll), 0.1)
        self._log = log
        self._shutdown = threading.Event()
        self._controller: Optional[JobController] = None
        self._last_skip: Tuple[Optional[str], float] = (None, 0.0)
        self._degraded = False  # 卡软探测失败标记：不再领取新任务，恢复后自动摘除
        self._gpu_probe_enabled = (
            config.WORKER_GPU_PROBE if gpu_probe_enabled is None else gpu_probe_enabled
        )

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    @property
    def gpu_available(self) -> bool:
        """本机是否有可用 GPU（require_gpu 任务的领取前置条件）。"""
        return bool((self.caps.get("gpu") or {}).get("available"))

    def request_shutdown(self, pause_current: bool = True) -> None:
        """请求优雅停机：暂停当前任务（存档）并在本轮循环后退出。"""
        self._shutdown.set()
        if pause_current and self._controller is not None:
            self._controller.pause_event.set()

    def _self_probe(self) -> bool:
        """本执行槽健康自检：GPU 槽做微型张量探测，CPU 槽恒为健康。

        探测失败 -> degraded（不领取新任务，等待恢复）；这是两级自愈中的
        第一级（软故障不杀进程，恢复后自动摘标记）。
        """
        if not self.gpu_available or not self._gpu_probe_enabled:
            return True
        return probe_visible_gpu()

    def run_forever(self) -> None:
        """主循环：注册 -> 健康探测 -> 空闲心跳 -> 领取任务 -> 执行，直到优雅停机。

        degraded 状态下仍然维持心跳（/api/workers 可见卡异常），但不领取新任务；
        探测恢复后自动摘除 degraded 标记，重新投入调度——即"检测不可用的卡是否恢复"。
        """
        hb_ttl = max(config.WORKER_LEASE_TTL * 2.0, config.WORKER_HEARTBEAT_INTERVAL * 3.0)
        self.lease_store.register_worker(self.worker_id, self.caps, hb_ttl)
        gpu = self.caps.get("gpu") or {}
        self._log(
            f"worker {self.worker_id} 启动: 机器={self.caps.get('machine_id')} 槽位={self.caps.get('slot')} "
            f"设备={'cuda(' + str(gpu.get('name')) + ')' if gpu.get('available') else 'cpu'}"
            f" / torch={self.caps.get('torch_version')} / 存储={config.STORAGE_BACKEND}"
        )
        try:
            while not self._shutdown.is_set():
                try:
                    self._run_forever_iteration(hb_ttl)
                except Exception as exc:  # noqa: BLE001
                    # 协调面瞬断（Redis 网络抖动等）不得杀死 worker 进程：
                    # 退避后继续循环，任务由租约/watchdog 机制兜底
                    self._log(f"worker {self.worker_id}: 协调面异常，稍后重试: {exc}")
                    self._shutdown.wait(self._idle_poll)
        finally:
            self.lease_store.deregister_worker(self.worker_id)
            self._log(f"worker {self.worker_id} 已退出")

    def _run_forever_iteration(self, hb_ttl: float) -> None:
        """单轮调度迭代：自探测并心跳；未启用的外部槽只保持在线、不领取任务。"""
        healthy = self._self_probe()
        if healthy == self._degraded:  # 状态翻转才打日志，避免刷屏
            self._degraded = not healthy
            if self._degraded:
                self._log(f"worker {self.worker_id}: GPU 探测失败，进入 degraded（暂停领取新任务，等待恢复）")
            else:
                self._log(f"worker {self.worker_id}: GPU 探测恢复，重新投入调度")
        enabled = self.lease_store.is_worker_enabled(
            self.worker_id, default=not bool(self.caps.get("managed"))
        )
        self.lease_store.heartbeat_worker(
            self.worker_id,
            {
                "state": "degraded" if self._degraded else ("idle" if enabled else "disabled"),
                "current_job": None,
                "machine_id": self.caps.get("machine_id"),
                "slot": self.caps.get("slot"),
                "managed": bool(self.caps.get("managed")),
            },
            hb_ttl,
        )
        if self._degraded or not enabled:
            # 掉卡或人工停用期间保持心跳，但不领取新任务。
            self._shutdown.wait(self._idle_poll)
            return
        try:
            job_id = self.queue.pop(timeout=self._idle_poll)
        except Exception as exc:  # noqa: BLE001
            # 队列领取异常：退避后由外层循环重试
            self._log(f"worker {self.worker_id}: 队列领取异常，稍后重试: {exc}")
            self._shutdown.wait(self._idle_poll)
            return
        if job_id is None:
            return
        if not self.lease_store.is_worker_enabled(
            self.worker_id, default=not bool(self.caps.get("managed"))
        ):
            # 停用开关可能在队列弹出后到达；把任务放回队列，避免该槽接到新工作。
            self.queue.push(job_id)
            self._shutdown.wait(self._idle_poll)
            return
        self._execute_job(job_id)

    # ------------------------------------------------------------------
    # 内部：领取与执行
    # ------------------------------------------------------------------

    def _execute_job(self, job_id: str) -> None:
        """领取一个任务：校验 -> 抢租约 -> 执行（含全部异常兜底）。"""
        record = jobstore.read_job(self.store, job_id)
        if record is None:
            self._log(f"[{job_id}] 任务元信息缺失，跳过")
            return
        if record.get("status") != jobstore.QUEUED:
            # 已被取消/暂停的排队任务：直接丢弃（不再回队列）
            self._log(f"[{job_id}] 状态为 {record.get('status')}，跳过")
            return
        spec = record.get("spec") or {}
        hint = spec.get("resource_hint") or {}
        gpu = self.caps.get("gpu") or {}
        min_vram = int(hint.get("min_vram_mb") or 0)
        # 资源提示匹配（到卡级）：require_gpu 要求是 GPU 卡；min_vram_mb 要求显存下限。
        # 不匹配则放回队列等待合适的卡（提示日志防刷屏），不影响队列中其它任务。
        if (hint.get("require_gpu") or min_vram > 0) and not gpu.get("available"):
            self.queue.push(job_id)
            last_id, last_ts = self._last_skip
            if last_id != job_id or time.time() - last_ts > 30.0:
                self._log(f"[{job_id}] 资源提示需要 GPU，本槽无可用 GPU，已放回队列等待 GPU worker")
                self._last_skip = (job_id, time.time())
            time.sleep(min(2.0, self._idle_poll))
            return
        if min_vram > 0 and int(gpu.get("vram_mb") or 0) < min_vram:
            self.queue.push(job_id)
            last_id, last_ts = self._last_skip
            if last_id != job_id or time.time() - last_ts > 30.0:
                self._log(f"[{job_id}] 显存不足（需 ≥ {min_vram} MiB，本卡 {gpu.get('vram_mb')} MiB），已放回队列")
                self._last_skip = (job_id, time.time())
            time.sleep(min(2.0, self._idle_poll))
            return
        token = f"{self.worker_id}:{uuid.uuid4().hex[:8]}"
        if not self.lease_store.acquire(job_id, token, config.WORKER_LEASE_TTL):
            # 租约被其它 worker 持有（重派竞态）：把弹出的条目放回队列。
            # 数量守恒——每个 worker 只放回自己弹出的条目，不会重复堆积；
            # 持有者是活着的 worker 时该副本稍后会被按状态跳过，无害；
            # 持有者已死但 TTL 未到时，该副本是任务不丢失的关键兜底。
            self._log(f"[{job_id}] 租约被其它 worker 持有，稍后重试领取")
            time.sleep(min(1.0, self._idle_poll))
            self.queue.push(job_id)
            return
        # 清除可能遗留的控制信号，保证本轮执行从干净状态开始
        self.lease_store.clear_signals(job_id)
        try:
            self._run_claimed(job_id, record, spec, token)
        except Exception:
            self._log(f"[{job_id}] worker 执行异常:\n{traceback.format_exc()}")
        finally:
            self.lease_store.release(job_id, token)

    def _run_claimed(self, job_id: str, record: dict, spec: dict, token: str) -> None:
        """执行已持有租约的任务：拉状态 -> 训练 -> 同步产物 -> 状态收尾。"""
        store = self.store
        scratch = config.WORKER_SCRATCH_DIR / job_id
        controller: Optional[JobController] = None
        try:
            shutil.rmtree(scratch, ignore_errors=True)
            scratch.mkdir(parents=True, exist_ok=True)
            params = spec.get("params") or {}

            def joblog(msg: str) -> None:
                """统一任务日志通道：本地 scratch 追加 + 整文件同步到状态平面。

                不能直接 append 到状态平面：finally 的 _flush_final 会用
                scratch 副本整文件覆盖状态平面日志，直接 append 的行会被冲掉。
                """
                stamp = time.strftime("%H:%M:%S")
                try:
                    with (scratch / "log.txt").open("a", encoding="utf-8") as f:
                        f.write(f"[{stamp}] {msg}\n")
                except Exception:
                    pass
                try:
                    store.write_file(job_key(job_id, "log.txt"), scratch / "log.txt")
                except Exception:
                    pass

            # 拉取历史产物到 scratch：日志/指标延续既有内容，best.pt 供最终报告加载
            for name in ("log.txt", "metrics.jsonl", "best.pt", "best_model.zip"):
                key = job_key(job_id, name)
                if store.exists(key):
                    store.read_file(key, scratch / name)

            # ---- 恢复训练上下文：优先最新 checkpoint，损坏则回退更早版本 ----
            resume_state = None
            resumed_epoch = 0
            for epoch, key in _list_checkpoints(store, job_id):
                try:
                    local_ckpt = store.read_file(key, scratch / "resume-ckpt.pt")
                    resume_state = load_trainer_state(local_ckpt)
                    resumed_epoch = epoch
                    break
                except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
                    joblog(f"checkpoint e{epoch} 加载失败，回退更早版本: {exc}")
                    resume_state = None
            # checkpoint 是进度唯一权威：无条件把 metrics 回滚到 resume epoch
            # （resume_state 为空时回滚到 0——孤儿进度清空，重试曲线不会出现重复 epoch）
            truncated = jobstore.truncate_metrics_to_epoch(store, job_id, resumed_epoch)
            (scratch / "metrics.jsonl").write_text(truncated, encoding="utf-8")
            if resume_state is not None:
                gpu_name = (self.caps.get("gpu") or {}).get("name") or "CPU"
                joblog(
                    f"接手续训: 从 epoch {resumed_epoch} 的 checkpoint 恢复"
                    f"（本节点 {self.worker_id}，{'GPU: ' + gpu_name if self.gpu_available else 'CPU'}，"
                    f"跨设备续训语义连续、不承诺逐位一致）",
                )
            elif resumed_epoch == 0 and truncated == "":
                joblog("未找到可用 checkpoint：从头开始训练（此前孤儿进度已回滚）")

            # ---- 标记 running（started_at 只在首次运行时固定） ----
            jobstore.update_job(
                store, job_id,
                status=jobstore.RUNNING,
                started_at=record.get("started_at") or time.time(),
                worker_id=self.worker_id,
                error=None,
            )

            controller = JobController(
                self.lease_store, job_id, token, self.worker_id,
                config.WORKER_HEARTBEAT_INTERVAL, config.WORKER_LEASE_TTL,
            )
            controller.start()
            self._controller = controller

            dataset_path = None
            if str(params.get("dataset_source", "local")) != "hub":
                dataset_path = resolve_dataset_for_worker(str(spec["dataset_id"]), scratch)
            training_params = dict(params)
            training_params.setdefault("dataset_id", str(spec["dataset_id"]))

            def scratch_log(msg: str) -> None:
                """训练器日志：写本地 scratch，随 on_epoch_end 整文件幂等同步到状态平面。"""
                with (scratch / "log.txt").open("a", encoding="utf-8") as f:
                    f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

            # 本段执行的起始 epoch（续训场景 = 最近 checkpoint 之后）
            segment_start = (int(resume_state.get("epoch", 0)) + 1) if resume_state else 1
            last_ckpt: dict = {"epoch": None}  # 暂停/取消时记录本段实际执行到的 epoch

            def on_epoch_end(rec: dict) -> None:
                """每 epoch 结束：同步 metrics/日志到状态平面（读方写后即可见）。"""
                store.write_file(job_key(job_id, "metrics.jsonl"), scratch / "metrics.jsonl")
                store.write_file(job_key(job_id, "log.txt"), scratch / "log.txt")
                controller.set_progress(int(rec.get("epoch", 0)), "training")

            def on_checkpoint(epoch_done: int) -> None:
                """trainer-state 存档回调：上传版本化 checkpoint 并只保留最近 N 个。"""
                store.write_file(
                    checkpoint_key(job_id, epoch_done),
                    scratch / "checkpoints" / f"ckpt-e{epoch_done:04d}.pt",
                )
                prune_checkpoints(store, job_id, config.WORKER_CHECKPOINT_KEEP)
                controller.set_progress(epoch_done, "training")
                last_ckpt["epoch"] = int(epoch_done)  # 供暂停分段记录用

            joblog(f"worker {self.worker_id} 开始执行训练任务")

            # 本段执行的起始 epoch（续训场景 = 最近 checkpoint 之后）

            try:
                summary = run_training(
                    dataset_path=dataset_path,
                    target=str(spec["target"]),
                    task=str(spec["task"]),
                    params=training_params,
                    cancel_event=controller.cancel_event,
                    log_fn=scratch_log,
                    job_dir=scratch,
                    pause_event=controller.pause_event,
                    resume_state=resume_state,
                    on_epoch_end=on_epoch_end,
                    on_checkpoint=on_checkpoint,
                )
            except JobPaused as exc:
                # 优雅暂停：训练器已在暂停点存档并上传（on_checkpoint），
                # 任务进入 paused，可被任意其它服务器/显卡接手
                reason = (
                    "worker 心跳丢失（任务已被重派），本节点停止写入"
                    if controller.lease_lost.is_set() else str(exc)
                )
                # 断点产物同步：best/last/preprocessor 一并上传状态平面——
                # 恢复后即使验证指标不再提升，接手 worker 也有 best.pt 可交付/预测
                for name in ("best.pt", "last.pt", "preprocessor.json", "best_model.zip"):
                    artifact = scratch / name
                    if artifact.exists():
                        store.write_file(job_key(job_id, name), artifact)
                # 记录本槽实际执行的分段（跨槽续训 UI 分段标注数据源）
                paused_epoch = int(last_ckpt.get("epoch") or 0)
                segments = list(record.get("segments") or [])
                if paused_epoch >= segment_start:
                    segments.append({
                        "worker_id": self.worker_id,
                        "device": "cuda" if (self.caps.get("gpu") or {}).get("available") else "cpu",
                        "from_epoch": segment_start,
                        "to_epoch": paused_epoch,
                        "at": time.time(),
                    })
                jobstore.update_job(store, job_id, status=jobstore.PAUSED,
                                   worker_id=self.worker_id, segments=segments)
                joblog(
                    f"训练暂停: {reason}；断点与产物已同步，可由任意节点接手"
                    f"（POST /api/jobs/{job_id}/resume）",
                )
                return
            except JobCancelled:
                # 取消（可在任意 eopch）：同步已完成部分的最佳产物供交付/预测
                for name in ("best.pt", "last.pt", "preprocessor.json", "best_model.zip"):
                    artifact = scratch / name
                    if artifact.exists():
                        store.write_file(job_key(job_id, name), artifact)
                jobstore.update_job(store, job_id, status=jobstore.CANCELLED,
                                   finished_at=time.time())
                joblog("任务已取消")
                return

            # ---- 成功：上传最终产物 + 记录训练分段（UI 分段设备标注数据源） ----
            for name in ("metrics.jsonl", "log.txt", "best.pt", "last.pt",
                         "preprocessor.json", "summary.json", "best_model.zip"):
                artifact = scratch / name
                if artifact.exists():
                    store.write_file(job_key(job_id, name), artifact)
            segments = list(record.get("segments") or [])
            segments.append({
                "worker_id": self.worker_id,
                "device": summary.get("device"),
                "from_epoch": (int(resume_state.get("epoch", 0)) + 1) if resume_state else 1,
                "to_epoch": summary.get("epochs_total"),
                "at": time.time(),
            })
            jobstore.update_job(
                store, job_id,
                status=jobstore.COMPLETED,
                device=summary.get("device"),
                summary=summary,
                segments=segments,
                finished_at=time.time(),
            )
            joblog(
                f"训练完成: 最佳 epoch {summary.get('best_epoch')}，"
                f"用时 {summary.get('duration_sec')}s（worker {self.worker_id}）",
            )
        except Exception as exc:  # noqa: BLE001
            # 失败分类：业务性错误直接 FAILED；环境性错误带 checkpoint 自动重试
            self._handle_run_failure(job_id, exc, scratch)
        finally:
            if controller is not None:
                controller.stop()
            self._controller = None
            self._flush_final(scratch, job_id)
            shutil.rmtree(scratch, ignore_errors=True)

    def _set_failed(self, job_id: str, error: str, scratch: Optional[Path] = None) -> None:
        """任务失败收尾：状态/错误信息落状态平面并写日志（日志统一走 joblog 防覆盖）。"""
        error = str(error).strip()
        try:
            jobstore.update_job(self.store, job_id, status=jobstore.FAILED,
                               error=error, finished_at=time.time())
            if scratch is not None and scratch.exists():
                stamp = time.strftime("%H:%M:%S")
                try:
                    with (scratch / "log.txt").open("a", encoding="utf-8") as f:
                        f.write(f"[{stamp}] 任务失败: {error[:2000]}\n")
                except Exception:
                    pass
            else:
                jobstore.append_job_log(self.store, job_id, f"任务失败: {error}".strip()[:2000])
        except Exception:
            self._log(f"[{job_id}] 失败状态写入异常:\n{traceback.format_exc()}")

    def _handle_run_failure(self, job_id: str, exc: BaseException, scratch: Optional[Path] = None) -> None:
        """失败分类收尾：这是"单卡故障不影响整体任务完成"的核心保障。

        - 环境性错误（CUDA/OOM/网络瞬断/存储故障等，classify_retryable 判定）：
          未超过 JOB_MAX_AUTO_RETRY 上限时，任务回到队列，任意可用卡/机器
          从最近 checkpoint 接续训练（进度不丢失），重试计数写入任务记录。
        - 业务性错误（数据集缺失/超参非法等）或重试额度耗尽：直接 FAILED。

        日志写本地 scratch（若有）：finally 的 _flush_final 会整文件同步，
        避免状态平面直接 append 被 scratch 副本覆盖而丢失。
        """
        def local_log(msg: str) -> None:
            """失败消息写入本地 scratch 日志（随 finally 同步到状态平面）。"""
            if scratch is None or not scratch.exists():
                return
            try:
                with (scratch / "log.txt").open("a", encoding="utf-8") as f:
                    f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            except Exception:
                pass

        retry_count = 0
        try:
            record = jobstore.read_job(self.store, job_id) or {}
            retry_count = int(record.get("retry_count") or 0)
        except Exception:
            pass
        retryable = classify_retryable(exc)
        if retryable and retry_count < config.JOB_MAX_AUTO_RETRY:
            new_count = retry_count + 1
            local_log(
                f"环境性错误，自动重试 {new_count}/{config.JOB_MAX_AUTO_RETRY}"
                f"（断点已保留，任意可用卡可接续）: {exc}",
            )
            try:
                jobstore.update_job(
                    self.store, job_id,
                    status=jobstore.QUEUED,
                    retry_count=new_count,
                    # 记录最近一次失败原因，但任务仍可续试（非终态）
                    last_error=f"{exc}",
                )
            except Exception:
                self._log(f"[{job_id}] 重试状态写入异常:\n{traceback.format_exc()}")
            self._log(
                f"[{job_id}] retryable error, auto-retry {new_count}/{config.JOB_MAX_AUTO_RETRY}: {exc}"
            )
            if config.JOB_RETRY_BACKOFF > 0:
                # 退避（可被停机信号打断）：避免同卡立刻陷入失败循环
                self._shutdown.wait(min(float(config.JOB_RETRY_BACKOFF), 30.0))
            self.queue.push(job_id)  # 回到队列，任意 worker（含本卡恢复后）可续试
            return
        if retryable:
            local_log(f"环境性错误自动重试已耗尽 {config.JOB_MAX_AUTO_RETRY} 次")
            self._set_failed(
                job_id,
                f"{exc}\n[环境性错误自动重试已耗尽 {config.JOB_MAX_AUTO_RETRY} 次]"
                f"\n{traceback.format_exc(limit=8)}",
                scratch,
            )
        else:
            self._set_failed(job_id, f"{exc}\n{traceback.format_exc(limit=8)}", scratch)

    def _flush_final(self, scratch: Path, job_id: str) -> None:
        """收尾兜底：把本地最后的 metrics/日志/最佳产物同步到状态平面（幂等）。

        best.pt/last.pt/preprocessor.json 也纳入兜底：失败路径下本地训练进度
        产生的最佳权重不应丢失（重试/预测/交付都要能取到）。
        """
        try:
            for name in (
                "metrics.jsonl", "log.txt", "best.pt", "last.pt",
                "preprocessor.json", "best_model.zip",
            ):
                artifact = scratch / name
                if artifact.exists():
                    self.store.write_file(job_key(job_id, name), artifact)
        except Exception:
            pass


def _list_checkpoints(store: ArtifactStore, job_id: str) -> List[Tuple[int, str]]:
    """列出任务的版本化 checkpoint，按 epoch 降序（最新优先，逐个回退）。"""
    pairs: List[Tuple[int, str]] = []
    for key in store.list_keys(job_key(job_id, "checkpoints/")):
        epoch = parse_checkpoint_epoch(key)
        if epoch is not None:
            pairs.append((epoch, key))
    pairs.sort(reverse=True)
    return pairs


# ---------------------------------------------------------------------------
# 机器级守护（Supervisor）：一卡一进程 + 掉卡探测自动恢复
# ---------------------------------------------------------------------------

class WorkerSupervisor:
    """一机一守护：按可见 GPU 数量启动一卡一子进程，监控子进程存活。

    设计（对应验收 AC9/AC10/AC11）：
    - 每张可见 GPU 一个子进程（CUDA_VISIBLE_DEVICES=i 隔离），另加
      WORKER_CPU_SLOTS 个 CPU 执行槽（纯 CPU 训练 / require_gpu=false 任务）；
    - 一张卡的驱动崩溃只杀死对应子进程，同机其它卡完全不受影响（故障隔离）；
    - 子进程死亡不等于卡死亡：重启前先用一次性探测子进程（--probe-gpu）
      验证该卡可用，探测不通过则本轮进入 recovering（下轮再试），卡恢复后
      自动重启子进程并重新领取任务——即"检测不可用的卡是否恢复"；
    - CPU 槽子进程死亡直接重启（不涉及卡）。

    协调约定：子进程死亡期间，其心跳 TTL 自然过期，watchdog 会把该卡
    正在训练的任务重派给其它卡/机器（进度由 checkpoint 保证不丢失）。
    """

    def __init__(
        self,
        queue: JobQueue,
        lease_store: LeaseStore,
        store: ArtifactStore,
        log: Callable[[str], None] = print,
        cpu_slots: Optional[int] = None,
    ) -> None:
        self.queue = queue
        self.lease_store = lease_store
        self.store = store
        self._log = log
        self._interval = max(float(config.WORKER_SUPERVISOR_PROBE_INTERVAL), 1.0)
        self._shutdown = threading.Event()
        self._children: Dict[str, "subprocess.Popen"] = {}  # slot -> 子进程
        self._slots: List[Tuple[str, Optional[str]]] = []   # (slot 短名, GPU 索引或 None)
        gpu_count = _gpu_count()
        for i in range(gpu_count):
            self._slots.append((f"gpu{i}", str(i)))
        n_cpu = config.WORKER_CPU_SLOTS if cpu_slots is None else cpu_slots
        for j in range(max(int(n_cpu), 1) if gpu_count == 0 else int(n_cpu)):
            self._slots.append((f"cpu{j}", None))

    # ------------------------------------------------------------------
    def _spawn(self, slot: str, gpu_index: Optional[str]) -> "subprocess.Popen":
        """启动一个卡槽子进程：CUDA_VISIBLE_DEVICES 隔离到单卡（必须在 CUDA 初始化前设置）。"""
        env = dict(os.environ)
        env.pop("_", None)  # 防止个别环境变量干扰 torch 启动偏好
        if gpu_index is not None:
            env["CUDA_VISIBLE_DEVICES"] = gpu_index
        env["WORKER_SLOT"] = slot
        env["PYTHONUNBUFFERED"] = "1"
        child = subprocess.Popen(
            [sys.executable, "-m", "app.worker", "--slot", slot],
            env=env,
        )
        self._log(f"supervisor: 启动卡槽 {slot} 子进程 pid={child.pid}")
        return child

    def _probe_gpu(self, gpu_index: Optional[str]) -> bool:
        """探测指定卡是否恢复：一次性 --probe-gpu 子进程（退出码 0 = 健康）。"""
        if gpu_index is None:
            return True
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = gpu_index
        try:
            probe = subprocess.run(
                [sys.executable, "-m", "app.worker", "--probe-gpu"],
                env=env, capture_output=True, timeout=60,
            )
            return probe.returncode == 0
        except Exception as exc:
            self._log(f"supervisor: 探测 GPU {gpu_index} 异常: {exc}")
            return False

    def _launch_slots(self) -> None:
        """启动全部未运行的卡槽子进程（首次启动与掉卡恢复共用）。"""
        for slot, gpu_index in self._slots:
            if slot in self._children and self._children[slot] is not None:
                continue
            if gpu_index is not None and not self._probe_gpu(gpu_index):
                # 掉卡时先不启动子进程：等下轮探测恢复再启动（recovering）
                self._log(f"supervisor: 卡 {slot}（GPU {gpu_index}）探测失败，暂不启动（recovering）")
                continue
            self._children[slot] = self._spawn(slot, gpu_index)

    def start(self) -> None:
        """首次按 current 能力拉起全部卡槽。"""
        self._launch_slots()

    def request_shutdown(self) -> None:
        """优雅停机：级联向全部子进程发送 SIGTERM（子进程存档暂停后退出）。"""
        self._shutdown.set()
        self._stop_children()

    def _stop_children(self) -> None:
        """向全部子进程发送优雅停机信号并回收。"""
        for slot, child in list(self._children.items()):
            if child is not None and child.poll() is None:
                self._log(f"supervisor: 通知卡槽 {slot} 优雅停机")
                try:
                    child.terminate()
                except Exception:
                    pass
        for slot, child in list(self._children.items()):
            if child is None:
                continue
            try:
                child.wait(timeout=30)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
            self._children.pop(slot, None)

    def run_forever(self) -> None:
        """守护循环：周期检查子进程存活；死亡槽位经探测确认卡片健康后自动重启。"""
        gpu_count = sum(1 for _, idx in self._slots if idx is not None)
        self._log(
            f"supervisor 启动: 机器={socket.gethostname()} 卡槽数={len(self._slots)}"
            f"（GPU 槽 {gpu_count} + CPU 槽 {len(self._slots) - gpu_count}）"
        )
        try:
            while not self._shutdown.is_set():
                self._supervise_once()
                self._shutdown.wait(self._interval)
        finally:
            self._stop_children()
            self._log("supervisor 已退出")

    def _supervise_once(self) -> None:
        """一轮守护：发现死亡/缺失槽位 -> 探测卡恢复 -> 重启子进程。"""
        # 正常子进程死亡（驱动崩溃/OOM kill/异常退出）→ 按卡探测决定重启
        for slot, child in list(self._children.items()):
            if child is None:
                continue
            rc = child.poll()
            if rc is None:
                continue
            self._children.pop(slot, None)
            self._log(f"supervisor: 卡槽 {slot} 子进程退出（rc={rc}），进入恢复流程")
        self._launch_slots()
        # 全部子进程死亡时无需额外处理：心跳 TTL 过期后 watchdog 重派任务

    def all_children_alive(self) -> bool:
        """测试观察点：全部槽位子进程是否存活。"""
        return all(
            slot in self._children and self._children[slot] is not None
            and self._children[slot].poll() is None
            for slot, _gpu in self._slots
        )


def _gpu_count() -> int:
    """本机可见 GPU 数（Supervisor 决定拉起几个卡槽用）；失败按 0 处理。"""
    try:
        import torch

        return int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 独立进程入口：python -m app.worker（多机模式，需 Redis 作为队列）
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """独立进程入口（多机模式）。

    三种运行模式（argparse）：
    - 无参数    : Supervisor 模式——按本机 GPU 数一卡一子进程并守护恢复；
    - --slot    : 卡槽子进程模式（由 Supervisor 拉起，CUDA_VISIBLE_DEVICES 已隔离）；
    - --probe-gpu: 一次性卡健康探测（退出码 0 = 该进程可见的 GPU 可用）。
    """
    parser = argparse.ArgumentParser(description="无状态训练 worker（一卡一进程）")
    parser.add_argument("--slot", default=None, help="卡槽标识（如 A-gpu0 / A-cpu0），Supervisor 子进程模式")
    parser.add_argument("--probe-gpu", action="store_true", help="一次性 GPU 健康探测：健康退出码 0，否则 1")
    parser.add_argument("--cpu-slots", type=int, default=None,
                        help="Supervisor 额外 CPU 执行槽数（默认取 WORKER_CPU_SLOTS）")
    parser.add_argument("--idle-poll", type=float, default=5.0, help="空闲轮询周期（秒）")
    args = parser.parse_args(argv)

    # ---- 探测模式：健康快速退出，不建立任何长连接 ----
    if args.probe_gpu:
        return 0 if probe_visible_gpu() else 1

    # ---- 协调平面自检（Supervisor 与卡槽子进程都必须接入分布式队列）----
    if not config.REDIS_URL:
        print("[worker] REDIS_URL 未配置：独立 worker 需要 Redis 作为任务队列与租约中心。")
        print("[worker] 单机模式无需启动独立 worker——API 进程已内置嵌入式 worker。")
        return 2
    queue, lease_store, distributed = build_coordination()
    if not distributed:
        print("[worker] 协调平面初始化异常：请检查 REDIS_URL 配置。")
        return 2
    store = get_store()
    if not lease_store.healthy():
        print(f"[worker] Redis 连接失败（REDIS_URL={config.REDIS_URL}），请检查网络与凭据。")
        return 2
    try:
        store.list_keys("jobs/")
    except Exception as exc:
        print(f"[worker] 状态平面不可达（STORAGE_BACKEND={config.STORAGE_BACKEND}）: {exc}")
        return 2


    def _handle(signum, _frame):  # noqa: ANN001
        """SIGTERM/SIGINT：优雅停机（Supervisor 级联子进程；卡槽存档暂停任务）。"""
        print(f"[worker] 收到信号 {signum}，正在优雅停机…", flush=True)
        if supervisor is not None:
            supervisor.request_shutdown()
        elif worker is not None:
            worker.request_shutdown(pause_current=True)


    supervisor: Optional[WorkerSupervisor] = None

    # ---- 卡槽子进程模式：本进程绑定单卡，注册后进入领取循环 ----
    if args.slot is not None:
        machine_id = config.WORKER_MACHINE_ID or socket.gethostname()
        worker_prefix = config.WORKER_ID or machine_id
        slot_worker_id = f"{worker_prefix}-{args.slot}"
        caps = detect_capabilities(slot_worker_id, machine_id=machine_id, slot=args.slot)
        if caps["gpu"]["available"]:
            print(f"[worker] GPU 就绪: {caps['gpu']['name']}（{caps['gpu']['vram_mb']} MiB）slot={args.slot}")
        else:
            print(f"[worker] 槽位 {args.slot} 无可见 GPU：以 CPU 训练（require_gpu 任务不会被领取）")
        worker = TrainingWorker(
            store=store, queue=queue, lease_store=lease_store,
            capabilities=caps, idle_poll=args.idle_poll,
        )
        signal.signal(signal.SIGTERM, _handle)   # Supervisor 停机 -> 子进程存档暂停
        signal.signal(signal.SIGINT, _handle)
        worker.run_forever()
        return 0

    # ---- Supervisor 模式：一卡一子进程 + 掉卡探测自动恢复 ----
    caps = detect_capabilities()
    gpu_count = _gpu_count()
    if gpu_count:
        print(f"[supervisor] 检测到 {gpu_count} 张 GPU，将一卡一进程启动")
    else:
        print("[supervisor] 未检测到 GPU：启动 CPU 执行槽（require_gpu 任务不会被领取）")
    supervisor = WorkerSupervisor(
        queue=queue, lease_store=lease_store, store=store,
        cpu_slots=args.cpu_slots,
    )
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    supervisor.start()
    supervisor.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
