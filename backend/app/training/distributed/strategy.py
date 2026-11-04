"""分布式并行策略定义与异构感知放置"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
import torch


class ParallelMode(Enum):
    """并行模式"""
    DATA_PARALLEL = "dp"                    # DDP/FSDP 数据并行
    PIPELINE_PARALLEL = "pp"                # 流水线并行 (GPipe/1F1B)
    TENSOR_PARALLEL = "tp"                  # 张量并行
    HYBRID = "hybrid"                       # 混合并行
    HETEROGENEOUS = "heterogeneous"         # 异构感知混合并行


class CommBackend(Enum):
    """通信后端"""
    NCCL = "nccl"
    GLOO = "gloo"
    MPI = "mpi"


@dataclass
class DeviceCapability:
    """设备能力描述"""
    worker_id: str
    rank: int
    device: torch.device
    device_type: str                      # cuda/rocm/xpu/cpu
    device_name: str                      # 如 "NVIDIA A100 80GB"
    vram_mb: int                          # 显存
    compute_tflops: float                 # 理论峰值算力 (FP16/BF16)
    memory_bandwidth_gbps: float          # 显存带宽
    interconnect: str                     # nvlink/pcie/ethernet/infiniband
    numa_node: int = 0                    # NUMA 节点
    tags: Dict[str, str] = field(default_factory=dict)  # 如 {"zone": "rack1", "rack": "r1"}
    
    @property
    def is_gpu(self) -> bool:
        return self.device_type in ("cuda", "rocm", "xpu")
    
    @property
    def normalized_compute(self) -> float:
        """归一化算力（相对于基准设备）"""
        return self.compute_tflops
    
    @property
    def normalized_memory(self) -> float:
        """归一化显存"""
        return self.vram_mb / 1024  # GB


@dataclass
class ParallelStrategy:
    """并行策略：描述模型如何切分、数据如何分发"""
    mode: ParallelMode
    
    # 数据并行
    dp_degree: int = 1
    dp_groups: List[List[int]] = field(default_factory=list)
    
    # 流水线并行
    pp_degree: int = 1
    pp_stages: List[List[int]] = field(default_factory=list)  # 每阶段包含的层索引
    pp_chunks: int = 4  # 微批次分块数
    pp_schedule: str = "1f1b"  # 1f1b/gpipe
    
    # 张量并行
    tp_degree: int = 1
    tp_groups: List[List[int]] = field(default_factory=list)
    
    # 异构感知
    device_capabilities: List[DeviceCapability] = field(default_factory=list)
    hetero_assignment: Dict[int, Dict] = field(default_factory=dict)  # rank -> {layers, micro_batch_size, weight}
    
    # 通信
    comm_backend: CommBackend = CommBackend.NCCL
    bucket_size_mb: int = 25  # DDP 桶大小
    
    @property
    def world_size(self) -> int:
        return self.dp_degree * self.pp_degree * self.tp_degree
    
    def get_device_mesh(self) -> Any:
        """构建 DeviceMesh，供 PyTorch DTensor/FSDP 使用"""
        if not self.device_capabilities:
            return None
        
        try:
            from torch.distributed.device_mesh import DeviceMesh
            device_type = self.device_capabilities[0].device.type
            mesh_shape = (self.dp_degree, self.pp_degree, self.tp_degree)
            mesh_devices = [c.device for c in self.device_capabilities[:self.world_size]]
            return DeviceMesh(device_type, mesh_devices, mesh_shape=mesh_shape)
        except Exception:
            return None
    
    def compute_micro_batch_allocation(self, global_batch: int) -> Dict[int, int]:
        """异构感知微批次分配：算力强的分更多微批次"""
        if self.mode != ParallelMode.HETEROGENEOUS or not self.device_capabilities:
            return {rank: global_batch // self.dp_degree for rank in range(self.world_size)}
        
        # 按算力加权分配
        total_flops = sum(c.normalized_compute for c in self.device_capabilities)
        allocation = {}
        remaining = global_batch
        for i, c in enumerate(self.device_capabilities):
            if i == len(self.device_capabilities) - 1:
                allocation[i] = max(1, remaining)
            else:
                alloc = max(1, int(global_batch * c.normalized_compute / total_flops))
                allocation[i] = alloc
                remaining -= alloc
        return allocation
    
    def get_pp_stage_devices(self) -> List[List[torch.device]]:
        """获取每个流水线阶段的设备列表"""
        if self.mode != ParallelMode.PIPELINE_PARALLEL and self.mode != ParallelMode.HETEROGENEOUS:
            return []
        
        stage_devices = []
        for stage_ranks in self.pp_stages:
            devices = [self.device_capabilities[r].device for r in stage_ranks if r < len(self.device_capabilities)]
            stage_devices.append(devices)
        return stage_devices


def create_strategy_from_config(config: Dict[str, Any], capabilities: List[DeviceCapability]) -> ParallelStrategy:
    """从配置创建并行策略"""
    mode_str = config.get("mode", "dp")
    mode = ParallelMode(mode_str) if isinstance(mode_str, str) else mode_str
    
    strategy = ParallelStrategy(
        mode=mode,
        dp_degree=config.get("dp_degree", 1),
        pp_degree=config.get("pp_degree", 1),
        tp_degree=config.get("tp_degree", 1),
        pp_chunks=config.get("pp_chunks", 4),
        pp_schedule=config.get("pp_schedule", "1f1b"),
        comm_backend=CommBackend(config.get("comm_backend", "nccl")),
        device_capabilities=capabilities,
    )
    
    # 自动构建组
    world_size = strategy.world_size
    ranks = list(range(min(world_size, len(capabilities))))
    
    # DP 组
    if strategy.dp_degree > 1:
        for i in range(0, world_size, strategy.dp_degree):
            group = ranks[i:i + strategy.dp_degree]
            if len(group) == strategy.dp_degree:
                strategy.dp_groups.append(group)
    
    # PP 阶段
    if strategy.pp_degree > 1:
        layers_per_stage = config.get("layers_per_stage", [])
        if layers_per_stage:
            strategy.pp_stages = layers_per_stage
        else:
            # 平均分配
            stage_size = world_size // strategy.pp_degree
            for i in range(strategy.pp_degree):
                strategy.pp_stages.append(ranks[i * stage_size:(i + 1) * stage_size])
    
    # TP 组
    if strategy.tp_degree > 1:
        for i in range(0, world_size, strategy.tp_degree):
            group = ranks[i:i + strategy.tp_degree]
            if len(group) == strategy.tp_degree:
                strategy.tp_groups.append(group)
    
    return strategy