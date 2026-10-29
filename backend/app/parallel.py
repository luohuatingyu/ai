"""异构同步数据并行：按数据量聚合梯度，由 rank 0 更新并广播唯一模型。"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import timedelta

import numpy as np
import torch
import torch.distributed as dist


class ParallelContext:
    """每个独立训练进程持有一个通信组，CPU/GPU 混合统一使用 CPU 通信张量。"""

    def __init__(self, spec: dict) -> None:
        """建立有超时的通信组；GPU 专用组使用 NCCL，其余使用 Gloo。"""
        self.rank = int(spec["rank"])
        self.sizes = [int(item["batch_size"]) for item in spec["members"]]
        self.world_size = len(self.sizes)
        self.backend = spec["backend"]
        self.device = torch.device(spec["device"])
        self.comm_device = self.device if self.backend == "nccl" else torch.device("cpu")
        self.seed = int(spec.get("seed", 42))
        self.parameters = []
        self.buffers = []
        self.progress = None
        if self.device.type == "cuda":
            torch.cuda.set_device(self.device)
        host = spec["host"]
        if ":" in host:
            host = f"[{host}]"
        dist.init_process_group(
            backend=self.backend,
            init_method=f"tcp://{host}:{int(spec['port'])}",
            rank=self.rank,
            world_size=self.world_size,
            timeout=timedelta(seconds=int(spec.get("timeout", 300))),
        )

    @property
    def primary(self) -> bool:
        """只有主节点负责优化器更新、验证和最终产物发布。"""
        return self.rank == 0

    def check_identity(self, value: bytes) -> None:
        """对比固定长度摘要，拒绝不同数据、特征顺序、词表或模型结构混入同一组。"""
        digest = torch.tensor(list(hashlib.sha256(value).digest()), dtype=torch.uint8, device=self.comm_device)
        gathered = [torch.empty_like(digest) for _ in range(self.world_size)]
        dist.all_gather(gathered, digest)
        if any(not torch.equal(digest, other) for other in gathered):
            raise ValueError("联合训练节点的数据、预处理或模型结构不一致")

    def initialize(self, modules: list, identity: bytes) -> None:
        """校验可训练参数布局并广播初始权重，恢复时也以主节点断点为准。"""
        layout = []
        for index, module in enumerate(modules):
            for name, parameter in module.named_parameters():
                if parameter.requires_grad:
                    self.parameters.append(parameter)
                    layout.append((index, name, list(parameter.shape), str(parameter.dtype)))
            self.buffers.extend(module.buffers())
        self.check_identity(identity + json.dumps(layout, sort_keys=True).encode())
        self.broadcast_parameters()

        if self.progress is not None:
            self.progress()

    def broadcast_parameters(self) -> None:
        """广播主节点参数及缓冲区，避免异构设备各自更新带来参数漂移。"""
        with torch.no_grad():
            for parameter in [*self.parameters, *self.buffers]:
                communication = parameter.detach().to(self.comm_device).contiguous()
                dist.broadcast(communication, src=0)
                parameter.copy_(communication.to(parameter.device))

    def sync_module(self, module) -> None:
        """同步冻结的参考模型，确保所有节点使用相同奖励和参考概率。"""
        tensors = list(module.state_dict().items())
        self.check_identity(json.dumps([(name, list(value.shape), str(value.dtype)) for name, value in tensors]).encode())
        with torch.no_grad():
            for _, value in tensors:
                communication = value.to(self.comm_device).contiguous()
                dist.broadcast(communication, src=0)
                value.copy_(communication.to(value.device))

    def json_from_primary(self, value):
        """使用 JSON 字节广播指标，通信时不反序列化 pickle 对象。"""
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() if self.primary else b""
        length = torch.tensor([len(encoded)], dtype=torch.int64, device=self.comm_device)
        dist.broadcast(length, src=0)
        size = int(length.item())
        if size > 16 * 1024 * 1024:
            raise ValueError("联合训练元数据超过 16 MiB")
        data = torch.tensor(list(encoded), dtype=torch.uint8, device=self.comm_device) if self.primary else torch.empty(size, dtype=torch.uint8, device=self.comm_device)
        dist.broadcast(data, src=0)
        return json.loads(bytes(data.cpu().tolist()).decode())

    def totals(self, values: list[float]) -> list[float]:
        """合并分子和分母，禁止将不同批大小节点的均值直接平均。"""
        tensor = torch.tensor(values, dtype=torch.float64, device=self.comm_device)
        dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
        return tensor.cpu().tolist()

    def control(self, cancel_event, pause_event, allow_pause: bool = False) -> bool:
        """所有节点在相同边界检查控制信号；暂停只在完整 epoch 边界生效。"""
        signal = 2 if cancel_event.is_set() else int(pause_event.is_set())
        tensor = torch.tensor([signal], dtype=torch.int32, device=self.comm_device)
        dist.all_reduce(tensor, op=dist.ReduceOp.MAX)
        signal = int(tensor.item())
        if signal == 2:
            from app.trainer import JobCancelled
            raise JobCancelled()
        if signal == 1:
            pause_event.set()
        return allow_pause and signal == 1

    def indices(self, count: int, epoch: int):
        """分配不重复的全局批次；尾批空分片仍参与通信，既不补样本也不丢样本。"""
        epoch_seed = (self.seed + epoch * 1_000_003 + self.rank) % (2**32)
        torch.manual_seed(epoch_seed)
        np.random.seed(epoch_seed)
        random.seed(epoch_seed)
        generator = torch.Generator().manual_seed(self.seed + epoch)
        order = torch.randperm(count, generator=generator).tolist()
        total = sum(self.sizes)
        offset = sum(self.sizes[:self.rank])
        for start in range(0, count, total):
            yield order[start + offset:min(start + offset + self.sizes[self.rank], start + total)]

    def step(self, optimizer, weight: int, max_norm: float | None = None) -> None:
        """聚合加权梯度后由主节点执行一次 AdamW 更新，再广播同一版本参数。"""
        weights = self.totals([float(weight)])
        if weights[0] <= 0:
            raise ValueError("联合训练批次没有有效样本或目标 token")
        finite = all(parameter.grad is None or bool(torch.isfinite(parameter.grad).all()) for parameter in self.parameters)
        status = torch.tensor([int(finite)], dtype=torch.int32, device=self.comm_device)
        dist.all_reduce(status, op=dist.ReduceOp.MIN)
        if not status.item():
            raise ValueError("联合训练出现非有限梯度，已拒绝更新模型")
        # 未参与本地前向的尾批节点发送零梯度；全组均未使用的参数保持 grad=None。
        used = torch.tensor([int(p.grad is not None and weight > 0) for p in self.parameters], dtype=torch.int32, device=self.comm_device)
        dist.all_reduce(used, op=dist.ReduceOp.MAX)
        for index, parameter in enumerate(self.parameters):
            if not used[index].item():
                parameter.grad = None
                continue
            gradient = torch.zeros_like(parameter, device=self.comm_device) if parameter.grad is None or weight == 0 else parameter.grad.detach().to(self.comm_device).clone()
            gradient.mul_(weight)
            dist.reduce(gradient, dst=0, op=dist.ReduceOp.SUM)
            if self.primary:
                parameter.grad = gradient.div_(weights[0]).to(parameter.device)
        if self.primary:
            if max_norm is not None:
                torch.nn.utils.clip_grad_norm_(self.parameters, max_norm)
            optimizer.step()
        self.broadcast_parameters()
        if self.progress is not None:
            self.progress()

    def close(self, successful: bool = False) -> None:
        """正常结束时确认全组完成；异常路径直接销毁通信组以唤醒其他成员。"""
        if dist.is_initialized():
            try:
                if successful:
                    dist.barrier()
            finally:
                dist.destroy_process_group()
