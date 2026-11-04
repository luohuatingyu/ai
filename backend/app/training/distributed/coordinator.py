"""分布式训练协调器：主节点负责启动、监控、聚合"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import torch

import app.config as config
from app.training.distributed.strategy import ParallelStrategy, DeviceCapability, create_strategy_from_config
from app.storage import ArtifactStore, get_store, checkpoint_key
from app.training.core import TrainingSpec, TrainingContext, run_training


@dataclass
class DistributedJobRequest:
    """分布式训练作业请求"""
    job_id: str
    spec: TrainingSpec
    strategy: ParallelStrategy
    dataset_path: str
    resume_path: Optional[str] = None
    master_addr: str = "localhost"
    master_port: int = 29500
    world_size: int = 1
    rank: int = 0


@dataclass
class WorkerStatus:
    """Worker 状态"""
    rank: int
    worker_id: str
    status: str  # starting/running/completed/failed
    epoch: int = 0
    metrics: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None
    last_heartbeat: float = 0


class DistributedCoordinator:
    """
    分布式训练协调器（主节点）
    
    职责：
    1. 解析并行策略，分配 rank
    2. 启动各 Worker 进程（本地或远程 SSH）
    3. 监控训练进度，收集指标
    4. 处理失败重试、检查点同步
    5. 协调流水线并行的微批次流动
    """
    
    def __init__(
        self,
        store: ArtifactStore,
        log_fn: Callable[[str], None] = print,
    ):
        self.store = store
        self.log = log_fn
        self.workers: Dict[int, WorkerStatus] = {}
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        
    def launch_distributed_training(
        self,
        request: DistributedJobRequest,
        on_epoch_end: Optional[Callable[[int, Dict], None]] = None,
        on_checkpoint: Optional[Callable[[int], None]] = None,
    ) -> Any:
        """
        启动分布式训练
        
        返回训练结果摘要
        """
        self.log(f"[{request.job_id}] 启动分布式训练: world_size={request.world_size}, strategy={request.strategy.mode.value}")
        
        # 1. 准备各 rank 的环境变量
        env_vars = self._prepare_env(request)
        
        # 2. 启动 Worker 进程
        processes = []
        for rank in range(request.world_size):
            if rank == 0:
                # 主进程在当前进程运行
                proc = None
            else:
                # 子进程/远程进程
                proc = self._launch_worker(rank, request, env_vars[rank])
            processes.append((rank, proc))
        
        # 3. 主进程运行训练（rank 0）
        try:
            # 构建分布式上下文
            dist_ctx = self._build_dist_context(request, rank=0)
            
            # 创建训练上下文
            context = TrainingContext(
                job_id=request.job_id,
                job_dir=Path(config.WORKER_SCRATCH_DIR) / request.job_id,
                store=self.store,
                dataset_path=Path(request.dataset_path),
                device=torch.device(f"cuda:{dist_ctx.local_rank}" if torch.cuda.is_available() else "cpu"),
                distributed=dist_ctx,
            )
            
            # 运行训练
            summary = run_training(
                request.spec,
                context,
                on_epoch_end=lambda m: on_epoch_end(0, m) if on_epoch_end else None,
                on_checkpoint=lambda e: on_checkpoint(e) if on_checkpoint else None,
            )
            
            # 等待其他 rank 完成
            for rank, proc in processes:
                if proc:
                    proc.wait(timeout=300)
            
            return summary
            
        except Exception as e:
            self.log(f"[{request.job_id}] 分布式训练失败: {e}")
            # 终止所有子进程
            for rank, proc in processes:
                if proc and proc.poll() is None:
                    proc.terminate()
            raise
    
    def _prepare_env(self, request: DistributedJobRequest) -> List[Dict[str, str]]:
        """准备各 rank 的环境变量"""
        envs = []
        for rank in range(request.world_size):
            cap = request.strategy.device_capabilities[rank] if rank < len(request.strategy.device_capabilities) else None
            env = {
                "MASTER_ADDR": request.master_addr,
                "MASTER_PORT": str(request.master_port),
                "WORLD_SIZE": str(request.world_size),
                "RANK": str(rank),
                "LOCAL_RANK": str(cap.local_rank if cap else rank),
                "NCCL_SOCKET_IFNAME": os.environ.get("NCCL_SOCKET_IFNAME", ""),
                "NCCL_IB_DISABLE": os.environ.get("NCCL_IB_DISABLE", "0"),
            }
            envs.append(env)
        return envs
    
    def _launch_worker(
        self, 
        rank: int, 
        request: DistributedJobRequest,
        env: Dict[str, str]
    ) -> subprocess.Popen:
        """启动 Worker 子进程"""
        # 构建启动命令
        cmd = [
            "python", "-m", "app.training.distributed.worker",
            "--job-id", request.job_id,
            "--rank", str(rank),
            "--master-addr", request.master_addr,
            "--master-port", str(request.master_port),
            "--world-size", str(request.world_size),
            "--dataset-path", request.dataset_path,
        ]
        
        if request.resume_path:
            cmd.extend(["--resume-path", request.resume_path])
        
        # 合并环境变量
        full_env = os.environ.copy()
        full_env.update(env)
        
        self.log(f"[{request.job_id}] 启动 Worker rank={rank}: {' '.join(cmd)}")
        
        proc = subprocess.Popen(
            cmd,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        
        # 启动日志读取线程
        threading.Thread(
            target=self._read_worker_logs,
            args=(request.job_id, rank, proc),
            daemon=True
        ).start()
        
        return proc
    
    def _read_worker_logs(self, job_id: str, rank: int, proc: subprocess.Popen):
        """读取 Worker 日志"""
        try:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self.log(f"[{job_id}] [rank {rank}] {line}")
        except Exception as e:
            self.log(f"[{job_id}] [rank {rank}] 日志读取异常: {e}")
    
    def _build_dist_context(self, request: DistributedJobRequest, rank: int):
        from app.training.core import DistributedContext
        cap = request.strategy.device_capabilities[rank] if rank < len(request.strategy.device_capabilities) else None
        
        return DistributedContext(
            rank=rank,
            world_size=request.world_size,
            local_rank=cap.local_rank if cap else rank,
            backend=request.strategy.comm_backend.value,
            strategy={
                "mode": request.strategy.mode.value,
                "dp_degree": request.strategy.dp_degree,
                "pp_degree": request.strategy.pp_degree,
                "tp_degree": request.strategy.tp_degree,
                "dp_groups": request.strategy.dp_groups,
                "pp_stages": request.strategy.pp_stages,
                "tp_groups": request.strategy.tp_groups,
            },
        )


class LocalDistributedLauncher:
    """本地分布式启动器：使用 torchrun 或手动启动多进程"""
    
    @staticmethod
    def launch(
        job_id: str,
        spec: TrainingSpec,
        strategy: ParallelStrategy,
        dataset_path: str,
        store: ArtifactStore,
        log_fn: Callable[[str], None],
        on_epoch_end: Optional[Callable[[int, Dict], None]] = None,
        on_checkpoint: Optional[Callable[[int], None]] = None,
    ) -> Any:
        """使用 torchrun 启动本地分布式训练"""
        
        world_size = strategy.world_size
        if world_size <= 1:
            # 单进程退化
            dist_ctx = None
            context = TrainingContext(
                job_id=job_id,
                job_dir=Path(config.WORKER_SCRATCH_DIR) / job_id,
                store=store,
                dataset_path=Path(dataset_path),
                device=select_device(),
                distributed=None,
            )
            return run_training(spec, context, on_epoch_end, on_checkpoint)
        
        # 使用 torchrun 启动
        master_port = config.DISTRIBUTED_MASTER_PORT_BASE + int(job_id.split("-")[-1], 16) % 1000
        
        cmd = [
            "torchrun",
            f"--nproc_per_node={world_size}",
            f"--master_port={master_port}",
            "-m", "app.training.distributed.worker",
            "--job-id", job_id,
            "--dataset-path", dataset_path,
            "--spec", json.dumps({
                "task": spec.task,
                "target": spec.target,
                "params": spec.params,
                "resource_hint": spec.resource_hint,
            }),
            "--strategy", json.dumps({
                "mode": strategy.mode.value,
                "dp_degree": strategy.dp_degree,
                "pp_degree": strategy.pp_degree,
                "tp_degree": strategy.tp_degree,
                "pp_chunks": strategy.pp_chunks,
                "pp_schedule": strategy.pp_schedule,
                "comm_backend": strategy.comm_backend.value,
            }),
        ]
        
        log_fn(f"[{job_id}] 启动 torchrun: {' '.join(cmd)}")
        
        # 这里简化：实际应该同步等待完成并收集结果
        # 完整实现需要 IPC 通信收集指标
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        for line in proc.stdout:
            log_fn(f"[{job_id}] {line.strip()}")
        
        proc.wait()
        
        if proc.returncode != 0:
            raise RuntimeError(f"分布式训练失败，退出码: {proc.returncode}")
        
        # 读取结果
        result_path = Path(config.WORKER_SCRATCH_DIR) / job_id / "summary.json"
        if result_path.exists():
            import json
            with open(result_path) as f:
                return json.load(f)
        
        return {"status": "completed"}