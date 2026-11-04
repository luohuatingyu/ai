"""计算资源选择器：单选/多选/自动调度"""
from __future__ import annotations

import app.config as config
from app.coordination import LeaseStore
from app.training.distributed import DeviceCapability, ParallelStrategy, ParallelMode
from typing import Any, Dict, List, Optional, Tuple


class ResourceSelector:
    """
    计算资源选择器
    
    支持模式：
    - 单选：用户指定单个 Worker
    - 多选：用户指定多个 Worker（分布式训练）
    - 自动调度：根据资源提示自动匹配最优 Worker(s)
    - 异构感知：考虑不同 GPU 规格、CPU/GPU 混合
    """
    
    def __init__(self, lease_store: LeaseStore, default_strategy: str = config.RESOURCE_SELECTOR_DEFAULT_STRATEGY):
        self.lease_store = lease_store
        self.default_strategy = default_strategy
    
    def select_workers(
        self,
        resource_hint: Dict[str, Any],
        strategy: str = None,
        max_workers: int = config.RESOURCE_SELECTOR_MAX_WORKERS_PER_JOB,
    ) -> Tuple[List[str], ParallelStrategy]:
        """
        根据资源提示选择 Worker
        
        Returns:
            (selected_worker_ids, parallel_strategy)
        """
        strategy = strategy or self.default_strategy
        
        # 获取所有可用 Worker
        all_workers = self.lease_store.list_workers()
        available_workers = [
            w for w in all_workers 
            if w.get("active") and self.lease_store.is_worker_enabled(
                w["worker_id"], default=not w.get("managed", False)
            ) and w.get("state") not in ("disabled", "degraded", "offline", "stopping")
        ]
        
        if not available_workers:
            raise RuntimeError("没有可用的计算资源")
        
        # 解析资源提示
        require_gpu = resource_hint.get("require_gpu", False)
        min_vram = int(resource_hint.get("min_vram_mb", 0))
        device_backend = resource_hint.get("device_backend", "auto")
        parallel_workers = resource_hint.get("parallel_workers")  # 用户指定的多选
        
        # 过滤匹配的 Worker
        candidates = []
        for w in available_workers:
            if self._match_worker(w, require_gpu, min_vram, device_backend):
                candidates.append(w)
        
        if not candidates:
            raise RuntimeError("没有匹配资源要求的计算资源")
        
        # 根据策略选择
        if parallel_workers:
            # 用户显式多选：验证并返回
            selected_ids = [pw["worker_id"] for pw in parallel_workers]
            return self._validate_explicit_selection(selected_ids, candidates, resource_hint)
        
        if strategy == "manual":
            # 手动模式：需要用户在前端选择，这里返回候选列表
            raise ValueError("手动模式需要用户指定 Worker")
        
        if strategy == "hetero_aware" and require_gpu:
            # 异构感知：选择算力互补的多 Worker
            return self._select_heterogeneous(candidates, resource_hint, max_workers)
        
        # 自动模式：单 Worker 最优匹配
        best = self._select_best_single(candidates, resource_hint)
        return [best["worker_id"]], self._build_single_strategy(best)
    
    def _match_worker(
        self, 
        worker: Dict, 
        require_gpu: bool, 
        min_vram: int, 
        device_backend: str
    ) -> bool:
        """校验 Worker 是否匹配资源要求"""
        gpu = worker.get("gpu", {})
        
        if require_gpu and not gpu.get("available"):
            return False
        
        if min_vram > 0 and int(gpu.get("vram_mb", 0)) < min_vram:
            return False
        
        if device_backend != "auto":
            actual_backend = str(gpu.get("backend") or worker.get("device_backend") or ("cuda" if gpu.get("available") else "cpu"))
            if device_backend != actual_backend:
                return False
        
        return True
    
    def _select_best_single(self, candidates: List[Dict], resource_hint: Dict) -> Dict:
        """选择单个最优 Worker"""
        # 评分：显存充足度 + 算力 + 空闲度
        scored = []
        for w in candidates:
            gpu = w.get("gpu", {})
            score = 0
            
            # 显存评分（越大越好，但有上限）
            vram = gpu.get("vram_mb", 0)
            min_vram = resource_hint.get("min_vram_mb", 0)
            if vram > 0:
                score += min(vram / max(min_vram * 2, 1), 2) * 10
            
            # 算力评分
            compute = gpu.get("compute_tflops", 0)
            if compute > 0:
                score += min(compute / 100, 5) * 5
            
            # 空闲评分（无当前任务加分）
            if w.get("state") == "idle":
                score += 10
            
            scored.append((score, w))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
    
    def _select_heterogeneous(
        self, 
        candidates: List[Dict], 
        resource_hint: Dict, 
        max_workers: int
    ) -> Tuple[List[str], ParallelStrategy]:
        """异构感知多 Worker 选择"""
        # 将候选按设备类型分组
        gpu_workers = [w for w in candidates if w.get("gpu", {}).get("available")]
        cpu_workers = [w for w in candidates if not w.get("gpu", {}).get("available")]
        
        # 如果需要 GPU，优先选 GPU
        require_gpu = resource_hint.get("require_gpu", False)
        if require_gpu and not gpu_workers:
            raise RuntimeError("需要 GPU 但无可用 GPU Worker")
        
        # 选择策略：大显存 + 高算力组合
        selected = []
        
        # 主节点：最大显存 GPU
        if gpu_workers:
            primary = max(gpu_workers, key=lambda w: w.get("gpu", {}).get("vram_mb", 0))
            selected.append(primary)
        
        # 额外 Worker：按算力加权选择
        remaining_slots = max_workers - 1
        if remaining_slots > 0:
            pool = [w for w in gpu_workers if w != primary] + cpu_workers
            pool.sort(key=lambda w: w.get("gpu", {}).get("compute_tflops", 0) or w.get("cpu", {}).get("cores", 0), reverse=True)
            selected.extend(pool[:remaining_slots])
        
        worker_ids = [w["worker_id"] for w in selected]
        strategy = self._build_heterogeneous_strategy(selected, resource_hint)
        
        return worker_ids, strategy
    
    def _validate_explicit_selection(
        self, 
        selected_ids: List[str], 
        candidates: List[Dict], 
        resource_hint: Dict
    ) -> Tuple[List[str], ParallelStrategy]:
        """验证用户显式选择的 Worker"""
        candidate_map = {w["worker_id"]: w for w in candidates}
        selected = []
        
        for wid in selected_ids:
            if wid not in candidate_map:
                raise ValueError(f"Worker {wid} 不可用或不存在")
            w = candidate_map[wid]
            if not self._match_worker(w, 
                                    resource_hint.get("require_gpu", False),
                                    resource_hint.get("min_vram_mb", 0),
                                    resource_hint.get("device_backend", "auto")):
                raise ValueError(f"Worker {wid} 不满足资源要求")
            selected.append(w)
        
        strategy = self._build_strategy_from_selection(selected, resource_hint)
        return selected_ids, strategy
    
    def _build_single_strategy(self, worker: Dict) -> ParallelStrategy:
        """构建单 Worker 策略"""
        return ParallelStrategy(
            mode=ParallelMode.DATA_PARALLEL,
            dp_degree=1,
            device_capabilities=[self._worker_to_capability(worker)],
        )
    
    def _build_heterogeneous_strategy(
        self, 
        workers: List[Dict], 
        resource_hint: Dict
    ) -> ParallelStrategy:
        """构建异构并行策略"""
        capabilities = [self._worker_to_capability(w) for w in workers]
        
        # 根据 Worker 类型决定并行模式
        gpu_count = sum(1 for c in capabilities if c.is_gpu)
        cpu_count = len(capabilities) - gpu_count
        
        if gpu_count > 1:
            mode = ParallelMode.HETEROGENEOUS
        elif gpu_count == 1 and cpu_count > 0:
            mode = ParallelMode.HETEROGENEOUS
        else:
            mode = ParallelMode.DATA_PARALLEL
        
        return ParallelStrategy(
            mode=mode,
            dp_degree=len(workers),
            device_capabilities=capabilities,
        )
    
    def _build_strategy_from_selection(
        self, 
        workers: List[Dict], 
        resource_hint: Dict
    ) -> ParallelStrategy:
        """从选中 Worker 构建策略"""
        capabilities = [self._worker_to_capability(w) for w in workers]
        gpu_count = sum(1 for c in capabilities if c.is_gpu)
        
        if gpu_count > 1:
            return ParallelStrategy(
                mode=ParallelMode.HETEROGENEOUS,
                dp_degree=len(workers),
                device_capabilities=capabilities,
            )
        elif gpu_count == 1:
            return ParallelStrategy(
                mode=ParallelMode.DATA_PARALLEL,
                dp_degree=len(workers),
                device_capabilities=capabilities,
            )
        else:
            return ParallelStrategy(
                mode=ParallelMode.DATA_PARALLEL,
                dp_degree=len(workers),
                device_capabilities=capabilities,
            )
    
    def _worker_to_capability(self, worker: Dict) -> DeviceCapability:
        """Worker 信息转 DeviceCapability"""
        gpu = worker.get("gpu", {})
        cpu = worker.get("cpu", {})
        
        return DeviceCapability(
            worker_id=worker["worker_id"],
            rank=0,  # 由协调器分配
            device=torch.device(gpu.get("device", "cpu")) if gpu.get("available") else torch.device("cpu"),
            device_type=gpu.get("backend", "cpu") if gpu.get("available") else "cpu",
            device_name=gpu.get("name", "CPU"),
            vram_mb=gpu.get("vram_mb", 0),
            compute_tflops=gpu.get("compute_tflops", cpu.get("cores", 1) * 0.1),
            memory_bandwidth_gbps=gpu.get("memory_bandwidth_gbps", 50.0),
            interconnect=gpu.get("interconnect", "pcie"),
            numa_node=0,
            tags={"machine_id": worker.get("machine_id", ""), "slot": worker.get("slot", "")},
        )


def get_available_resources(lease_store: LeaseStore) -> Dict[str, Any]:
    """获取可用计算资源摘要（供前端展示）"""
    workers = lease_store.list_workers()
    available = [
        w for w in workers 
        if w.get("active") and lease_store.is_worker_enabled(
            w["worker_id"], default=not w.get("managed", False)
        )
    ]
    
    summary = {
        "total": len(available),
        "gpu": 0,
        "cpu": 0,
        "by_machine": {},
        "by_gpu_type": {},
        "details": [],
    }
    
    for w in available:
        gpu = w.get("gpu", {})
        machine = w.get("machine_id", "unknown")
        
        if gpu.get("available"):
            summary["gpu"] += 1
            gpu_name = gpu.get("name", "Unknown GPU")
            summary["by_gpu_type"][gpu_name] = summary["by_gpu_type"].get(gpu_name, 0) + 1
        else:
            summary["cpu"] += 1
        
        if machine not in summary["by_machine"]:
            summary["by_machine"][machine] = {"gpu": 0, "cpu": 0, "workers": []}
        summary["by_machine"][machine]["workers"].append(w["worker_id"])
        if gpu.get("available"):
            summary["by_machine"][machine]["gpu"] += 1
        else:
            summary["by_machine"][machine]["cpu"] += 1
        
        summary["details"].append({
            "worker_id": w["worker_id"],
            "machine_id": machine,
            "slot": w.get("slot"),
            "device": gpu.get("device", "cpu") if gpu.get("available") else "cpu",
            "gpu_name": gpu.get("name"),
            "vram_mb": gpu.get("vram_mb"),
            "state": w.get("state"),
            "current_job": w.get("current_job"),
        })
    
    return summary