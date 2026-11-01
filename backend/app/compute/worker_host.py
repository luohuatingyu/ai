"""Worker 宿主进程：注册、心跳、拉取任务、启动内核/训练"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import app.config as config
from app import jobstore
from app.coordination import JobQueue, LeaseStore, build_coordination
from app.storage import ArtifactStore, get_store, resolve_dataset_for_worker
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.training.distributed import ParallelStrategy, create_strategy_from_config
from app.worker import detect_capabilities, probe_visible_gpu
from app.performance import ResourceSampler


@dataclass
class WorkerConfig:
    """Worker 配置"""
    worker_id: str
    machine_id: str
    slot: str
    capabilities: Dict
    scratch_dir: Path
    lease_ttl: float = config.WORKER_LEASE_TTL
    heartbeat_interval: float = config.WORKER_HEARTBEAT_INTERVAL
    idle_poll: float = 1.0
    gpu_probe_enabled: bool = config.WORKER_GPU_PROBE


class WorkerHost:
    """
    Worker 宿主进程：单线程领取-执行循环
    
    生命周期：
    1. 注册到协调平面（Redis LeaseStore）
    2. 定期心跳上报状态、资源使用
    3. 从队列领取任务（训练/Notebook/推理）
    4. 校验资源提示、抢租约
    5. 启动执行器（KernelExecutor 或分布式训练）
    6. 完成/暂停/取消/失败 -> 释放租约 -> 清理本地 scratch
    """
    
    def __init__(self, cfg: WorkerConfig, log_fn: Callable[[str], None] = print):
        self.cfg = cfg
        self.log = log_fn
        self.store = get_store()
        self.queue, self.lease, self.distributed = build_coordination()
        self.resource_sampler = ResourceSampler(str(cfg.capabilities.get("device", "cpu")))
        self._shutdown = threading.Event()
        self._current_job: Optional[str] = None
        self._executor_process: Optional[subprocess.Popen] = None
        
    def run_forever(self):
        """主循环"""
        # 注册 Worker
        hb_ttl = max(config.WORKER_LEASE_TTL * 2.0, config.WORKER_HEARTBEAT_INTERVAL * 3.0)
        self.lease.register_worker(self.cfg.worker_id, self.cfg.capabilities, hb_ttl)
        self.resource_sampler.start()
        
        gpu = self.cfg.capabilities.get("gpu", {})
        device_label = f"{gpu.get('backend', 'gpu')}({gpu.get('name')})" if gpu.get("available") else "CPU"
        self.log(f"Worker {self.cfg.worker_id} 启动: 机器={self.cfg.machine_id} 槽位={self.cfg.slot} 设备={device_label}")
        
        try:
            while not self._shutdown.is_set():
                self._run_iteration(hb_ttl)
        except Exception as e:
            self.log(f"Worker {self.cfg.worker_id} 主循环异常: {e}")
        finally:
            self.resource_sampler.stop()
            self.lease.deregister_worker(self.cfg.worker_id)
            self.log(f"Worker {self.cfg.worker_id} 已退出")
    
    def _run_iteration(self, hb_ttl: float):
        """单次调度迭代"""
        # 健康自检
        healthy = self._self_probe()
        if healthy != getattr(self, '_last_healthy', True):
            self._last_healthy = healthy
            if not healthy:
                self.log(f"Worker {self.cfg.worker_id}: GPU 探测失败，进入 degraded 状态")
        
        # 检查是否启用
        enabled = self.lease.is_worker_enabled(
            self.cfg.worker_id, default=not bool(self.cfg.capabilities.get("managed", False))
        )
        
        # 检查是否被预留（联合训练）
        assignment = self.lease.peek_holder(f"slot:{self.cfg.worker_id}")
        if assignment:
            # 已被预留，等待
            self._publish_heartbeat(hb_ttl, state="reserved")
            time.sleep(self.cfg.idle_poll)
            return
        
        if not healthy or not enabled:
            self._publish_heartbeat(hb_ttl, state="degraded" if not healthy else "disabled")
            time.sleep(self.cfg.idle_poll)
            return
        
        # 发布空闲心跳
        self._publish_heartbeat(hb_ttl, state="idle")
        
        # 尝试领取任务
        job_id = self.queue.pop(timeout=self.cfg.idle_poll)
        if job_id:
            self._execute_job(job_id)
    
    def _self_probe(self) -> bool:
        """GPU 软探测"""
        if not self.cfg.gpu_probe_enabled:
            return True
        gpu = self.cfg.capabilities.get("gpu", {})
        if not gpu.get("available"):
            return True
        try:
            return probe_visible_gpu()
        except Exception:
            return False
    
    def _publish_heartbeat(self, hb_ttl: float, state: str = "idle"):
        """发布心跳"""
        self.lease.heartbeat_worker(
            self.cfg.worker_id,
            {
                "state": state,
                "current_job": self._current_job,
                "epoch": None,
                "machine_id": self.cfg.machine_id,
                "slot": self.cfg.slot,
                "managed": bool(self.cfg.capabilities.get("managed", False)),
                "resources": self.resource_sampler.snapshot(),
            },
            hb_ttl,
        )
    
    def _execute_job(self, job_id: str):
        """执行任务"""
        self._current_job = job_id
        record = jobstore.read_job(self.store, job_id)
        
        if not record or record.get("status") != jobstore.QUEUED:
            self.log(f"[{job_id}] 任务状态非 queued，跳过")
            self._current_job = None
            return
        
        spec_dict = record.get("spec", {})
        hint = spec_dict.get("resource_hint", {})
        
        # 校验资源提示
        if not self._match_resource_hint(hint):
            self.queue.push(job_id)
            self._current_job = None
            return
        
        # 抢租约
        token = f"{self.cfg.worker_id}:{uuid.uuid4().hex[:8]}"
        if not self.lease.acquire(job_id, token, config.WORKER_LEASE_TTL):
            self.log(f"[{job_id}] 租约被其他 Worker 持有，放回队列")
            self.queue.push(job_id)
            self._current_job = None
            return
        
        try:
            # 更新任务状态为运行中
            jobstore.update_job(self.store, job_id, status=jobstore.RUNNING, worker_id=self.cfg.worker_id)
            
            # 构建训练规格
            spec = TrainingSpec(**spec_dict)
            
            # 准备数据集
            dataset_path = resolve_dataset_for_worker(spec.dataset_id, self.cfg.scratch_dir / job_id)
            
            # 检查是否需要分布式训练
            if hint.get("parallel_workers"):
                self._run_distributed(job_id, spec, dataset_path, hint)
            else:
                self._run_local(job_id, spec, dataset_path)
                
        except Exception as e:
            self.log(f"[{job_id}] 执行异常: {e}")
            jobstore.update_job(self.store, job_id, status=jobstore.FAILED, error=str(e))
        finally:
            self.lease.release(job_id, token)
            self._current_job = None
    
    def _match_resource_hint(self, hint: Dict) -> bool:
        """校验资源提示是否匹配"""
        gpu = self.cfg.capabilities.get("gpu", {})
        
        if hint.get("require_gpu") and not gpu.get("available"):
            return False
        
        min_vram = int(hint.get("min_vram_mb", 0))
        if min_vram > 0 and int(gpu.get("vram_mb", 0)) < min_vram:
            return False
        
        requested_backend = str(hint.get("device_backend", "auto"))
        actual_backend = str(gpu.get("backend") or self.cfg.capabilities.get("device_backend") or ("cuda" if gpu.get("available") else "cpu"))
        if requested_backend != "auto" and requested_backend != actual_backend:
            return False
        
        return True
    
    def _run_local(self, job_id: str, spec: TrainingSpec, dataset_path: Path):
        """运行本地单机训练"""
        from app.training.core import TrainingContext
        
        context = TrainingContext(
            job_id=job_id,
            job_dir=self.cfg.scratch_dir / job_id,
            store=self.store,
            dataset_path=dataset_path,
            device=torch.device(self.cfg.capabilities.get("device", "cpu")),
        )
        
        # 恢复 checkpoint
        latest = find_latest_checkpoint_local(context.checkpoint_dir)
        if latest:
            epoch, ckpt_path = latest
            context.resume_state = torch.load(ckpt_path, map_location="cpu")
        
        def on_epoch_end(metrics):
            jobstore.append_job_log(self.store, job_id, f"Epoch {metrics.get('epoch')}: {metrics}")
            # 同步指标到 OSS
            self.store.write_file(f"jobs/{job_id}/metrics.jsonl", context.metrics_path)
        
        def on_checkpoint(epoch):
            # 同步 checkpoint 到 OSS
            sync_checkpoints_to_oss(self.store, job_id, context.checkpoint_dir)
        
        run_training(spec, context, on_epoch_end, on_checkpoint)
    
    def _run_distributed(self, job_id: str, spec: TrainingSpec, dataset_path: Path, hint: Dict):
        """运行分布式训练（作为协调器 rank 0）"""
        from app.training.distributed.coordinator import LocalDistributedLauncher
        
        # 解析并行策略
        parallel_workers = hint.get("parallel_workers", [])
        capabilities = []
        for pw in parallel_workers:
            # 从租约查询 Worker 能力
            worker_info = self.lease.peek_holder(f"slot:{pw['worker_id']}")
            if worker_info:
                capabilities.append(worker_info)
        
        strategy = create_strategy_from_config(
            {
                "mode": hint.get("parallel_mode", "dp"),
                "dp_degree": len(parallel_workers),
                "pp_degree": hint.get("pp_degree", 1),
                "tp_degree": hint.get("tp_degree", 1),
            },
            capabilities,
        )
        
        # 启动分布式训练
        launcher = LocalDistributedLauncher()
        launcher.launch(
            job_id=job_id,
            spec=spec,
            strategy=strategy,
            dataset_path=str(dataset_path),
            store=self.store,
            log_fn=self.log,
        )


def main():
    parser = argparse.ArgumentParser(description="Worker 宿主进程")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--machine-id", default="")
    parser.add_argument("--slot", default="cpu0")
    parser.add_argument("--scratch-dir", default="")
    parser.add_argument("--embedded", action="store_true", help="嵌入式模式（API 进程内运行）")
    args = parser.parse_args()
    
    # 探测能力
    caps = detect_capabilities(args.worker_id or None, args.machine_id or None, args.slot or None)
    
    cfg = WorkerConfig(
        worker_id=caps["worker_id"],
        machine_id=caps["machine_id"],
        slot=caps["slot"],
        capabilities=caps,
        scratch_dir=Path(args.scratch_dir) if args.scratch_dir else config.WORKER_SCRATCH_DIR,
    )
    
    host = WorkerHost(cfg)
    
    # 信号处理
    def signal_handler(signum, frame):
        host._shutdown.set()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    host.run_forever()


if __name__ == "__main__":
    import torch
    main()