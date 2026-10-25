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

import platform
import shutil
import signal
import socket
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Tuple

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


def detect_capabilities(worker_id: Optional[str] = None) -> dict:
    """探测本机计算能力（GPU 型号/显存/软件版本）。

    worker 启动时调用一次：既是 /api/workers 展示的数据源，
    也是资源提示匹配（require_gpu / min_vram）的依据。
    GPU 探测失败不阻塞启动（退化为 CPU 计算单元）。
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
    return {
        "worker_id": worker_id or f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:6]}",
        "hostname": socket.gethostname(),
        "gpu": gpu,
        "torch_version": torch_version,
        "cuda_version": cuda_version,
        "python_version": platform.python_version(),
        "started_at": time.time(),
    }


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

    def run_forever(self) -> None:
        """主循环：注册 -> 空闲心跳 -> 领取任务 -> 执行，直到优雅停机。"""
        hb_ttl = max(config.WORKER_LEASE_TTL * 2.0, config.WORKER_HEARTBEAT_INTERVAL * 3.0)
        self.lease_store.register_worker(self.worker_id, self.caps, hb_ttl)
        gpu = self.caps.get("gpu") or {}
        self._log(
            f"worker {self.worker_id} 启动: 设备={'cuda(' + str(gpu.get('name')) + ')' if gpu.get('available') else 'cpu'}"
            f" / torch={self.caps.get('torch_version')} / 存储={config.STORAGE_BACKEND}"
        )
        try:
            while not self._shutdown.is_set():
                self.lease_store.heartbeat_worker(
                    self.worker_id, {"state": "idle", "current_job": None}, hb_ttl
                )
                job_id = self.queue.pop(timeout=self._idle_poll)
                if job_id is None:
                    continue
                self._execute_job(job_id)
        finally:
            self.lease_store.deregister_worker(self.worker_id)
            self._log(f"worker {self.worker_id} 已退出")

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
        if hint.get("require_gpu") and not self.gpu_available:
            # 资源提示不匹配（本机无 GPU）：放回队列，由 GPU worker 领取；
            # 提示日志按任务去重，避免刷屏
            self.queue.push(job_id)
            last_id, last_ts = self._last_skip
            if last_id != job_id or time.time() - last_ts > 30.0:
                self._log(f"[{job_id}] 需要 GPU 训练，本机无 GPU，已放回队列等待 GPU worker")
                self._last_skip = (job_id, time.time())
            time.sleep(min(2.0, self._idle_poll))
            return
        token = f"{self.worker_id}:{uuid.uuid4().hex[:8]}"
        if not self.lease_store.acquire(job_id, token, config.WORKER_LEASE_TTL):
            self._log(f"[{job_id}] 租约被其它 worker 持有（重派竞态），跳过")
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

            # 拉取历史产物到 scratch：日志/指标延续既有内容，best.pt 供最终报告加载
            for name in ("log.txt", "metrics.jsonl", "best.pt"):
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
                    jobstore.append_job_log(
                        store, job_id,
                        f"checkpoint e{epoch} 加载失败，回退更早版本: {exc}",
                    )
                    resume_state = None
            if resume_state is not None:
                # checkpoint 是进度唯一权威：先回滚指标到存档 epoch，再续训（曲线无重复无锯齿）
                truncated = jobstore.truncate_metrics_to_epoch(store, job_id, resumed_epoch)
                (scratch / "metrics.jsonl").write_text(truncated, encoding="utf-8")
                gpu_name = (self.caps.get("gpu") or {}).get("name") or "CPU"
                jobstore.append_job_log(
                    store, job_id,
                    f"接手续训: 从 epoch {resumed_epoch} 的 checkpoint 恢复"
                    f"（本节点 {self.worker_id}，{'GPU: ' + gpu_name if self.gpu_available else 'CPU'}，"
                    f"跨设备续训语义连续、不承诺逐位一致）",
                )

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

            dataset_path = resolve_dataset_for_worker(str(spec["dataset_id"]), scratch)

            def scratch_log(msg: str) -> None:
                """训练器日志：写本地 scratch，随 on_epoch_end 整文件幂等同步到状态平面。"""
                with (scratch / "log.txt").open("a", encoding="utf-8") as f:
                    f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

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

            jobstore.append_job_log(
                store, job_id, f"worker {self.worker_id} 开始执行训练任务"
            )

            try:
                summary = run_training(
                    dataset_path=dataset_path,
                    target=str(spec["target"]),
                    task=str(spec["task"]),
                    params=params,
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
                jobstore.update_job(store, job_id, status=jobstore.PAUSED,
                                   worker_id=self.worker_id)
                jobstore.append_job_log(
                    store, job_id,
                    f"训练暂停: {reason}；断点已保存，可由任意节点接手"
                    f"（POST /api/jobs/{job_id}/resume）",
                )
                return
            except JobCancelled:
                jobstore.update_job(store, job_id, status=jobstore.CANCELLED,
                                   finished_at=time.time())
                jobstore.append_job_log(store, job_id, "任务已取消")
                return

            # ---- 成功：上传最终产物 + 记录训练分段（UI 分段设备标注数据源） ----
            for name in ("metrics.jsonl", "log.txt", "best.pt", "last.pt",
                         "preprocessor.json", "summary.json"):
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
            jobstore.append_job_log(
                store, job_id,
                f"训练完成: 最佳 epoch {summary.get('best_epoch')}，"
                f"用时 {summary.get('duration_sec')}s（worker {self.worker_id}）",
            )
        except FileNotFoundError as exc:
            # 数据集未同步到状态平面等可预期错误：任务失败并给出明确指引
            self._set_failed(job_id, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._set_failed(job_id, f"{exc}\n{traceback.format_exc(limit=8)}")
        finally:
            if controller is not None:
                controller.stop()
            self._controller = None
            self._flush_final(scratch, job_id)
            shutil.rmtree(scratch, ignore_errors=True)

    def _set_failed(self, job_id: str, error: str) -> None:
        """任务失败收尾：状态/错误信息落状态平面并写日志。"""
        try:
            jobstore.update_job(self.store, job_id, status=jobstore.FAILED,
                               error=str(error).strip(), finished_at=time.time())
            jobstore.append_job_log(self.store, job_id, f"任务失败: {error}".strip()[:2000])
        except Exception:
            self._log(f"[{job_id}] 失败状态写入异常:\n{traceback.format_exc()}")

    def _flush_final(self, scratch: Path, job_id: str) -> None:
        """收尾兜底：尽力把本地 metrics/日志的最后状态同步到状态平面（幂等）。"""
        try:
            for name in ("metrics.jsonl", "log.txt"):
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
# 独立进程入口：python -m app.worker（多机模式，需 Redis 作为队列）
# ---------------------------------------------------------------------------

def main() -> int:
    """独立 worker 进程入口：环境自检 -> 注册 -> 领取循环（SIGTERM 优雅停机）。"""
    if not config.REDIS_URL:
        print("[worker] REDIS_URL 未配置：独立 worker 需要 Redis 作为任务队列与租约中心。")
        print("[worker] 单机模式无需启动独立 worker——API 进程已内置嵌入式 worker。")
        return 2
    queue, lease_store, distributed = build_coordination()
    if not distributed:
        print("[worker] 协调平面初始化异常：请检查 REDIS_URL 配置。")
        return 2
    store = get_store()

    # ---- 启动自检：协调平面 / 状态平面 / GPU，任何一步失败给出明确指引 ----
    if not lease_store.healthy():
        print(f"[worker] Redis 连接失败（REDIS_URL={config.REDIS_URL}），请检查网络与凭据。")
        return 2
    try:
        store.list_keys("jobs/")
    except Exception as exc:
        print(f"[worker] 状态平面不可达（STORAGE_BACKEND={config.STORAGE_BACKEND}）: {exc}")
        return 2

    caps = detect_capabilities(config.WORKER_ID or None)
    if caps["gpu"]["available"]:
        print(f"[worker] GPU 就绪: {caps['gpu']['name']}（{caps['gpu']['vram_mb']} MiB）")
    else:
        print("[worker] 未检测到 GPU：将以 CPU 训练（require_gpu 任务不会被本节点领取）")

    worker = TrainingWorker(
        store=store, queue=queue, lease_store=lease_store,
        capabilities=caps, idle_poll=5.0,
    )

    def _handle(signum, _frame):  # noqa: ANN001
        """SIGTERM/SIGINT：优雅停机——当前任务立即存档暂停，随后退出主循环。"""
        print(f"[worker] 收到信号 {signum}，正在优雅停机（保存断点后退出）…", flush=True)
        worker.request_shutdown(pause_current=True)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
