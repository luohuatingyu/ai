"""分布式通信抽象：封装 NCCL/Gloo/MPI 通信操作"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Any
import torch
import torch.distributed as dist


class Communicator(ABC):
    """通信器基类"""
    
    @abstractmethod
    def all_reduce(self, tensor: torch.Tensor, op: str = "sum") -> torch.Tensor:
        """全规约"""
        pass
    
    @abstractmethod
    def all_gather(self, tensor: torch.Tensor) -> List[torch.Tensor]:
        """全收集"""
        pass
    
    @abstractmethod
    def broadcast(self, tensor: torch.Tensor, src: int) -> torch.Tensor:
        """广播"""
        pass
    
    @abstractmethod
    def send(self, tensor: torch.Tensor, dst: int):
        """点对点发送"""
        pass
    
    @abstractmethod
    def recv(self, tensor: torch.Tensor, src: int) -> torch.Tensor:
        """点对点接收"""
        pass
    
    @abstractmethod
    def barrier(self):
        """屏障同步"""
        pass


class NCCLCommunicator(Communicator):
    """NCCL 通信器（GPU 间高性能通信）"""
    
    def __init__(self, group: Optional[Any] = None):
        self.group = group or dist.group.WORLD
    
    def all_reduce(self, tensor: torch.Tensor, op: str = "sum") -> torch.Tensor:
        op_map = {
            "sum": dist.ReduceOp.SUM,
            "mean": dist.ReduceOp.SUM,  # 后续除以 world_size
            "max": dist.ReduceOp.MAX,
            "min": dist.ReduceOp.MIN,
        }
        dist.all_reduce(tensor, op=op_map.get(op, dist.ReduceOp.SUM), group=self.group)
        if op == "mean":
            tensor /= dist.get_world_size(self.group)
        return tensor
    
    def all_gather(self, tensor: torch.Tensor) -> List[torch.Tensor]:
        world_size = dist.get_world_size(self.group)
        output = [torch.empty_like(tensor) for _ in range(world_size)]
        dist.all_gather(output, tensor, group=self.group)
        return output
    
    def broadcast(self, tensor: torch.Tensor, src: int) -> torch.Tensor:
        dist.broadcast(tensor, src=src, group=self.group)
        return tensor
    
    def send(self, tensor: torch.Tensor, dst: int):
        dist.send(tensor, dst=dst, group=self.group)
    
    def recv(self, tensor: torch.Tensor, src: int) -> torch.Tensor:
        dist.recv(tensor, src=src, group=self.group)
        return tensor
    
    def barrier(self):
        dist.barrier(group=self.group)


class GlooCommunicator(Communicator):
    """Gloo 通信器（CPU/跨节点通信）"""
    
    def __init__(self, group: Optional[Any] = None):
        self.group = group or dist.group.WORLD
    
    def all_reduce(self, tensor: torch.Tensor, op: str = "sum") -> torch.Tensor:
        op_map = {
            "sum": dist.ReduceOp.SUM,
            "mean": dist.ReduceOp.SUM,
            "max": dist.ReduceOp.MAX,
            "min": dist.ReduceOp.MIN,
        }
        dist.all_reduce(tensor, op=op_map.get(op, dist.ReduceOp.SUM), group=self.group)
        if op == "mean":
            tensor /= dist.get_world_size(self.group)
        return tensor
    
    def all_gather(self, tensor: torch.Tensor) -> List[torch.Tensor]:
        world_size = dist.get_world_size(self.group)
        output = [torch.empty_like(tensor) for _ in range(world_size)]
        dist.all_gather(output, tensor, group=self.group)
        return output
    
    def broadcast(self, tensor: torch.Tensor, src: int) -> torch.Tensor:
        dist.broadcast(tensor, src=src, group=self.group)
        return tensor
    
    def send(self, tensor: torch.Tensor, dst: int):
        dist.send(tensor, dst=dst, group=self.group)
    
    def recv(self, tensor: torch.Tensor, src: int) -> torch.Tensor:
        dist.recv(tensor, src=src, group=self.group)
        return tensor
    
    def barrier(self):
        dist.barrier(group=self.group)


def get_communicator(backend: str, group: Optional[Any] = None) -> Communicator:
    """获取通信器实例"""
    backend = backend.lower()
    if backend == "nccl":
        return NCCLCommunicator(group)
    elif backend == "gloo":
        return GlooCommunicator(group)
    else:
        raise ValueError(f"不支持的通信后端: {backend}")


# 流水线并行通信辅助
class PipelineCommunicator:
    """流水线并行专用通信器：处理激活值和梯度的跨阶段传递"""
    
    def __init__(self, stage_id: int, num_stages: int, communicator: Communicator):
        self.stage_id = stage_id
        self.num_stages = num_stages
        self.comm = communicator
        self._prev_stage = stage_id - 1 if stage_id > 0 else None
        self._next_stage = stage_id + 1 if stage_id < num_stages - 1 else None
    
    def send_activations(self, activations: torch.Tensor):
        """发送激活值到下一阶段"""
        if self._next_stage is not None:
            self.comm.send(activations, dst=self._next_stage)
    
    def recv_activations(self, shape: tuple, dtype: torch.dtype, device: torch.device) -> torch.Tensor:
        """从上一阶段接收激活值"""
        if self._prev_stage is not None:
            buffer = torch.empty(shape, dtype=dtype, device=device)
            return self.comm.recv(buffer, src=self._prev_stage)
        return None
    
    def send_gradients(self, gradients: torch.Tensor):
        """发送梯度到上一阶段"""
        if self._prev_stage is not None:
            self.comm.send(gradients, dst=self._prev_stage)
    
    def recv_gradients(self, shape: tuple, dtype: torch.dtype, device: torch.device) -> torch.Tensor:
        """从下一阶段接收梯度"""
        if self._next_stage is not None:
            buffer = torch.empty(shape, dtype=dtype, device=device)
            return self.comm.recv(buffer, src=self._next_stage)
        return None


# 张量并行通信辅助
class TensorParallelCommunicator:
    """张量并行通信器：处理行/列并行的 All-Reduce/All-Gather"""
    
    def __init__(self, tp_group: Any, tp_size: int, tp_rank: int):
        self.tp_group = tp_group
        self.tp_size = tp_size
        self.tp_rank = tp_rank
        self.comm = get_communicator("nccl", tp_group)
    
    def all_reduce_row(self, tensor: torch.Tensor) -> torch.Tensor:
        """行并行：输出维度分片，需要 All-Reduce"""
        return self.comm.all_reduce(tensor, op="sum")
    
    def all_gather_col(self, tensor: torch.Tensor, dim: int = -1) -> torch.Tensor:
        """列并行：输入维度分片，需要 All-Gather"""
        gathered = self.comm.all_gather(tensor)
        return torch.cat(gathered, dim=dim)
    
    def reduce_scatter_col(self, tensor: torch.Tensor, dim: int = -1) -> torch.Tensor:
        """列并行反向：梯度 Reduce-Scatter"""
        # 简化实现：All-Reduce 后切片
        reduced = self.comm.all_reduce(tensor, op="sum")
        chunk_size = reduced.shape[dim] // self.tp_size
        start = self.tp_rank * chunk_size
        end = start + chunk_size
        return reduced.narrow(dim, start, chunk_size)