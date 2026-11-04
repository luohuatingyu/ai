"""分布式性能分析器"""
from __future__ import annotations

import time
import torch
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from contextlib import contextmanager


@dataclass
class ProfileEvent:
    """性能事件"""
    name: str
    start_time: float
    end_time: float
    duration_ms: float
    rank: int
    category: str  # compute/communication/memory
    metadata: Dict = field(default_factory=dict)


class DistributedProfiler:
    """分布式训练性能分析器"""
    
    def __init__(self, rank: int, enabled: bool = True):
        self.rank = rank
        self.enabled = enabled
        self.events: List[ProfileEvent] = []
        self._active_events: Dict[str, float] = {}
    
    @contextmanager
    def profile(self, name: str, category: str = "compute", **metadata):
        """上下文管理器：自动记录耗时"""
        if not self.enabled:
            yield
            return
        
        torch.cuda.synchronize() if torch.cuda.is_available() else None
        start = time.perf_counter()
        try:
            yield
        finally:
            torch.cuda.synchronize() if torch.cuda.is_available() else None
            end = time.perf_counter()
            self.events.append(ProfileEvent(
                name=name,
                start_time=start,
                end_time=end,
                duration_ms=(end - start) * 1000,
                rank=self.rank,
                category=category,
                metadata=metadata,
            ))
    
    def record_event(self, name: str, duration_ms: float, category: str = "compute", **metadata):
        """手动记录事件"""
        if not self.enabled:
            return
        self.events.append(ProfileEvent(
            name=name,
            start_time=time.perf_counter() - duration_ms / 1000,
            end_time=time.perf_counter(),
            duration_ms=duration_ms,
            rank=self.rank,
            category=category,
            metadata=metadata,
        ))
    
    def get_summary(self) -> Dict:
        """获取性能摘要"""
        if not self.events:
            return {}
        
        by_category = {}
        for event in self.events:
            cat = event.category
            if cat not in by_category:
                by_category[cat] = {"count": 0, "total_ms": 0, "events": []}
            by_category[cat]["count"] += 1
            by_category[cat]["total_ms"] += event.duration_ms
            by_category[cat]["events"].append({
                "name": event.name,
                "duration_ms": event.duration_ms,
                "metadata": event.metadata,
            })
        
        return {
            "rank": self.rank,
            "total_events": len(self.events),
            "by_category": by_category,
            "total_compute_ms": sum(e.duration_ms for e in self.events if e.category == "compute"),
            "total_communication_ms": sum(e.duration_ms for e in self.events if e.category == "communication"),
            "total_memory_ms": sum(e.duration_ms for e in self.events if e.category == "memory"),
        }
    
    def export_chrome_trace(self, path: str):
        """导出 Chrome Trace 格式（可在 chrome://tracing 查看）"""
        import json
        trace_events = []
        for event in self.events:
            trace_events.append({
                "name": event.name,
                "cat": event.category,
                "ph": "X",  # 完整事件
                "ts": event.start_time * 1_000_000,  # 微秒
                "dur": event.duration_ms * 1000,     # 微秒
                "pid": self.rank,
                "tid": 0,
                "args": event.metadata,
            })
        
        with open(path, "w") as f:
            json.dump({"traceEvents": trace_events}, f)


class CommunicationProfiler:
    """通信性能专用分析器"""
    
    def __init__(self, rank: int):
        self.rank = rank
        self.comm_events: List[Dict] = []
    
    def record_all_reduce(self, tensor_size_mb: float, duration_ms: float, dtype: str = "fp16"):
        self.comm_events.append({
            "op": "all_reduce",
            "tensor_size_mb": tensor_size_mb,
            "duration_ms": duration_ms,
            "bandwidth_gbps": (tensor_size_mb * 8 / 1000) / (duration_ms / 1000) if duration_ms > 0 else 0,
            "dtype": dtype,
        })
    
    def record_all_gather(self, tensor_size_mb: float, duration_ms: float, world_size: int):
        total_mb = tensor_size_mb * world_size
        self.comm_events.append({
            "op": "all_gather",
            "tensor_size_mb": tensor_size_mb,
            "total_size_mb": total_mb,
            "duration_ms": duration_ms,
            "bandwidth_gbps": (total_mb * 8 / 1000) / (duration_ms / 1000) if duration_ms > 0 else 0,
            "world_size": world_size,
        })
    
    def record_p2p(self, tensor_size_mb: float, duration_ms: float, direction: str, peer: int):
        self.comm_events.append({
            "op": f"p2p_{direction}",
            "tensor_size_mb": tensor_size_mb,
            "duration_ms": duration_ms,
            "bandwidth_gbps": (tensor_size_mb * 8 / 1000) / (duration_ms / 1000) if duration_ms > 0 else 0,
            "peer_rank": peer,
        })
    
    def get_summary(self) -> Dict:
        if not self.comm_events:
            return {}
        
        by_op = {}
        for e in self.comm_events:
            op = e["op"]
            if op not in by_op:
                by_op[op] = {"count": 0, "total_mb": 0, "total_ms": 0, "avg_bandwidth": 0}
            by_op[op]["count"] += 1
            by_op[op]["total_mb"] += e.get("tensor_size_mb", e.get("total_size_mb", 0))
            by_op[op]["total_ms"] += e["duration_ms"]
            by_op[op]["avg_bandwidth"] = (
                by_op[op]["total_mb"] * 8 / 1000 / (by_op[op]["total_ms"] / 1000)
                if by_op[op]["total_ms"] > 0 else 0
            )
        
        return {"rank": self.rank, "by_operation": by_op}