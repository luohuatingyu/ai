"""分布式训练 Worker 入口：作为子进程运行，执行指定 rank 的训练"""
from __future__ import annotations

import argparse
import json
import os
import sys
import torch
import torch.distributed as dist

import app.config as config
from app.training.core import (
    TrainingSpec, 
    TrainingContext, 
    DistributedContext, 
    run_training,
)
from app.training.distributed.strategy import ParallelStrategy, ParallelMode, CommBackend
from app.storage import get_store


def setup_distributed(rank: int, world_size: int, backend: str, master_addr: str, master_port: int):
    """初始化分布式环境"""
    os.environ["MASTER_ADDR"] = master_addr
    os.environ["MASTER_PORT"] = str(master_port)
    os.environ["WORLD_SIZE"] = str(world_size)
    os.environ["RANK"] = str(rank)
    
    if not dist.is_initialized():
        dist.init_process_group(
            backend=backend,
            init_method="env://",
            world_size=world_size,
            rank=rank,
        )
    
    # 设置当前设备
    if torch.cuda.is_available():
        local_rank = int(os.environ.get("LOCAL_RANK", rank))
        torch.cuda.set_device(local_rank)
        device = torch.device(f"cuda:{local_rank}")
    else:
        device = torch.device("cpu")
    
    return device


def cleanup_distributed():
    """清理分布式环境"""
    if dist.is_initialized():
        dist.destroy_process_group()


def main():
    parser = argparse.ArgumentParser(description="分布式训练 Worker")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--world-size", type=int, required=True)
    parser.add_argument("--master-addr", default="localhost")
    parser.add_argument("--master-port", type=int, default=29500)
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument("--spec", required=True, help="JSON 格式的 TrainingSpec")
    parser.add_argument("--strategy", required=True, help="JSON 格式的 ParallelStrategy")
    parser.add_argument("--resume-path", default=None)
    
    args = parser.parse_args()
    
    # 解析参数
    spec_dict = json.loads(args.spec)
    spec = TrainingSpec(**spec_dict)
    
    strategy_dict = json.loads(args.strategy)
    strategy = ParallelStrategy(
        mode=ParallelMode(strategy_dict["mode"]),
        dp_degree=strategy_dict.get("dp_degree", 1),
        pp_degree=strategy_dict.get("pp_degree", 1),
        tp_degree=strategy_dict.get("tp_degree", 1),
        pp_chunks=strategy_dict.get("pp_chunks", 4),
        pp_schedule=strategy_dict.get("pp_schedule", "1f1b"),
        comm_backend=CommBackend(strategy_dict.get("comm_backend", "nccl")),
    )
    
    # 初始化分布式
    device = setup_distributed(
        args.rank, args.world_size, 
        strategy.comm_backend.value,
        args.master_addr, args.master_port
    )
    
    print(f"[Worker {args.rank}/{args.world_size}] 初始化完成, device={device}")
    
    try:
        # 构建分布式上下文
        dist_ctx = DistributedContext(
            rank=args.rank,
            world_size=args.world_size,
            local_rank=int(os.environ.get("LOCAL_RANK", args.rank)),
            backend=strategy.comm_backend.value,
            strategy={
                "mode": strategy.mode.value,
                "dp_degree": strategy.dp_degree,
                "pp_degree": strategy.pp_degree,
                "tp_degree": strategy.tp_degree,
                "pp_chunks": strategy.pp_chunks,
                "pp_schedule": strategy.pp_schedule,
            },
        )
        
        # 创建训练上下文
        store = get_store()
        context = TrainingContext(
            job_id=args.job_id,
            job_dir=Path(config.WORKER_SCRATCH_DIR) / args.job_id,
            store=store,
            dataset_path=Path(args.dataset_path),
            device=device,
            distributed=dist_ctx,
        )
        
        # 恢复 checkpoint
        if args.resume_path:
            context.resume_state = torch.load(args.resume_path, map_location="cpu")
        
        # 运行训练
        summary = run_training(spec, context)
        
        print(f"[Worker {args.rank}] 训练完成: {summary}")
        
    except Exception as e:
        print(f"[Worker {args.rank}] 训练失败: {e}", file=sys.stderr)
        raise
    finally:
        cleanup_distributed()


if __name__ == "__main__":
    from pathlib import Path
    main()