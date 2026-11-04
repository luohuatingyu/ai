"""异构设备放置器：模型层到异构设备的最优放置"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
import torch
import torch.nn as nn

from app.training.distributed.strategy import ParallelStrategy, DeviceCapability, ParallelMode


@dataclass
class LayerInfo:
    """模型层信息"""
    name: str
    module: nn.Module
    param_count: int
    param_memory_mb: float          # 参数显存占用
    activation_memory_mb: float     # 激活值显存占用（估算）
    compute_flops: float            # 计算量 (FLOPs)
    layer_type: str                 # linear/conv/attention/embedding/norm/other
    input_shape: Tuple[int, ...]
    output_shape: Tuple[int, ...]


@dataclass
class PlacementPlan:
    """放置计划"""
    layer_to_device: Dict[str, torch.device]      # 层名 -> 设备
    layer_to_rank: Dict[str, int]                 # 层名 -> rank
    stage_boundaries: List[int]                   # 流水线阶段边界（层索引）
    dp_groups: List[List[int]]                    # 数据并行组
    memory_usage_per_device: Dict[int, float]     # 每设备显存占用 (MB)
    compute_balance: Dict[int, float]             # 每设备计算负载 (FLOPs)
    communication_volume: Dict[Tuple[int, int], float]  # 设备间通信量 (MB)


class HeterogeneousPlacer:
    """
    异构感知模型放置器
    
    解决：不同 GPU 规格（如 A100 80G + RTX 3090 24G + CPU）如何协同训练
    
    策略：
    1. 显存约束优先：大模型层放大显存 GPU，小层/Embedding 放小显存
    2. 算力加权：前向/反向计算量大的层放高算力 GPU
    3. 通信最小化：减少跨慢速互联（以太网）的激活/梯度传输
    4. 拓扑感知：同机 NVLink 优先放同一 PP 阶段或 TP 组
    5. 负载均衡：各设备计算时间尽量均衡
    """
    
    def __init__(self, strategy: ParallelStrategy):
        self.strategy = strategy
        self.capabilities = strategy.device_capabilities
        self.world_size = strategy.world_size
        
    def place_model(
        self, 
        model: nn.Module, 
        input_shape: Tuple[int, ...] = (1, 2048),
        strategy_override: Optional[ParallelStrategy] = None
    ) -> PlacementPlan:
        """
        计算最优放置方案
        
        返回 PlacementPlan，包含每层到设备/rank 的映射
        """
        if strategy_override:
            strategy = strategy_override
        else:
            strategy = self.strategy
        
        # 1. 分析模型结构
        layer_infos = self._analyze_model(model, input_shape)
        
        # 2. 根据并行模式选择放置算法
        if strategy.mode == ParallelMode.DATA_PARALLEL:
            return self._place_data_parallel(layer_infos, strategy)
        elif strategy.mode == ParallelMode.PIPELINE_PARALLEL:
            return self._place_pipeline_parallel(layer_infos, strategy)
        elif strategy.mode == ParallelMode.HETEROGENEOUS:
            return self._place_heterogeneous(layer_infos, strategy)
        elif strategy.mode == ParallelMode.HYBRID:
            return self._place_hybrid(layer_infos, strategy)
        else:
            return self._place_data_parallel(layer_infos, strategy)
    
    def _analyze_model(self, model: nn.Module, input_shape: Tuple[int, ...]) -> List[LayerInfo]:
        """分析模型结构：层数、参数量、激活大小、计算量"""
        layer_infos = []
        
        # 注册 forward hook 捕获激活形状
        activations = {}
        def hook_fn(name):
            def hook(module, input, output):
                if isinstance(output, torch.Tensor):
                    activations[name] = output.shape
                elif isinstance(output, (tuple, list)) and output:
                    activations[name] = output[0].shape if isinstance(output[0], torch.Tensor) else None
            return hook
        
        handles = []
        for name, module in model.named_modules():
            if len(list(module.children())) == 0:  # 叶子模块
                handles.append(module.register_forward_hook(hook_fn(name)))
        
        # 前向传播获取激活形状
        device = next(model.parameters()).device
        dummy_input = torch.randn(input_shape, device=device)
        model.eval()
        with torch.no_grad():
            try:
                model(dummy_input)
            except Exception:
                pass
        
        for handle in handles:
            handle.remove()
        
        # 收集层信息
        for name, module in model.named_modules():
            if len(list(module.children())) == 0:
                param_count = sum(p.numel() for p in module.parameters())
                param_memory = param_count * 2 / 1024 / 1024  # BF16/FP16: 2 bytes per param
                
                # 估算激活显存
                act_shape = activations.get(name)
                if act_shape:
                    act_elements = 1
                    for dim in act_shape:
                        act_elements *= dim
                    activation_memory = act_elements * 2 / 1024 / 1024
                else:
                    activation_memory = 0
                
                # 估算计算量
                compute_flops = self._estimate_flops(module, act_shape)
                
                # 判断层类型
                layer_type = self._classify_layer(module)
                
                layer_infos.append(LayerInfo(
                    name=name,
                    module=module,
                    param_count=param_count,
                    param_memory_mb=param_memory,
                    activation_memory_mb=activation_memory,
                    compute_flops=compute_flops,
                    layer_type=layer_type,
                    input_shape=act_shape if act_shape else (0,),
                    output_shape=act_shape if act_shape else (0,),
                ))
        
        return layer_infos
    
    def _estimate_flops(self, module: nn.Module, output_shape: Optional[Tuple]) -> float:
        """估算模块 FLOPs"""
        if isinstance(module, nn.Linear):
            in_features = module.in_features
            out_features = module.out_features
            batch_seq = output_shape[0] * output_shape[1] if output_shape and len(output_shape) >= 2 else 1
            return 2 * in_features * out_features * batch_seq
        elif isinstance(module, (nn.Conv1d, nn.Conv2d, nn.Conv3d)):
            # 简化估算
            return 1e9
        elif isinstance(module, (nn.MultiheadAttention,)):
            # Attention FLOPs 估算
            return 1e9
        return 1e6
    
    def _classify_layer(self, module: nn.Module) -> str:
        """分类层类型"""
        if isinstance(module, nn.Embedding):
            return "embedding"
        elif isinstance(module, nn.Linear):
            return "linear"
        elif isinstance(module, (nn.Conv1d, nn.Conv2d, nn.Conv3d)):
            return "conv"
        elif isinstance(module, (nn.LayerNorm, nn.BatchNorm1d, nn.BatchNorm2d, nn.GroupNorm)):
            return "norm"
        elif isinstance(module, (nn.MultiheadAttention,)):
            return "attention"
        elif isinstance(module, (nn.LSTM, nn.GRU, nn.RNN)):
            return "rnn"
        return "other"
    
    def _place_data_parallel(self, layer_infos: List[LayerInfo], strategy: ParallelStrategy) -> PlacementPlan:
        """数据并行：所有层复制到所有设备"""
        devices = [c.device for c in self.capabilities[:self.world_size]]
        
        plan = PlacementPlan(
            layer_to_device={info.name: devices[0] for info in layer_infos},
            layer_to_rank={info.name: 0 for info in layer_infos},
            stage_boundaries=[],
            dp_groups=strategy.dp_groups,
            memory_usage_per_device={},
            compute_balance={},
            communication_volume={},
        )
        
        total_param_mem = sum(info.param_memory_mb for info in layer_infos)
        for rank, device in enumerate(devices):
            plan.memory_usage_per_device[rank] = total_param_mem
            plan.compute_balance[rank] = sum(info.compute_flops for info in layer_infos)
        
        return plan
    
    def _place_pipeline_parallel(self, layer_infos: List[LayerInfo], strategy: ParallelStrategy) -> PlacementPlan:
        """流水线并行：按层切分到不同阶段"""
        pp_degree = strategy.pp_degree
        num_layers = len(layer_infos)
        layers_per_stage = math.ceil(num_layers / pp_degree)
        
        stage_boundaries = []
        layer_to_device = {}
        layer_to_rank = {}
        
        devices = [c.device for c in self.capabilities[:self.world_size]]
        
        for i, info in enumerate(layer_infos):
            stage_idx = min(i // layers_per_stage, pp_degree - 1)
            stage_boundaries.append(i) if i % layers_per_stage == 0 else None
            
            # 该阶段的设备（可能有多个 DP 副本）
            stage_devices = strategy.get_pp_stage_devices()
            if stage_devices and stage_idx < len(stage_devices):
                device = stage_devices[stage_idx][0]
            else:
                device = devices[stage_idx % len(devices)]
            
            layer_to_device[info.name] = device
            layer_to_rank[info.name] = stage_idx
        
        # 去重边界
        stage_boundaries = sorted(set([i for i in range(0, num_layers, layers_per_stage)]))
        
        # 计算显存/计算分布
        memory_usage = {}
        compute_balance = {}
        for stage_idx in range(pp_degree):
            stage_layers = layer_infos[stage_idx * layers_per_stage:(stage_idx + 1) * layers_per_stage]
            memory_usage[stage_idx] = sum(l.param_memory_mb + l.activation_memory_mb for l in stage_layers)
            compute_balance[stage_idx] = sum(l.compute_flops for l in stage_layers)
        
        return PlacementPlan(
            layer_to_device=layer_to_device,
            layer_to_rank=layer_to_rank,
            stage_boundaries=stage_boundaries,
            dp_groups=strategy.dp_groups,
            memory_usage_per_device=memory_usage,
            compute_balance=compute_balance,
            communication_volume={},
        )
    
    def _place_heterogeneous(self, layer_infos: List[LayerInfo], strategy: ParallelStrategy) -> PlacementPlan:
        """
        异构感知放置：核心算法
        
        使用贪心 + 启发式优化：
        1. 先放 Embedding 和大 Linear 层（显存敏感）到大显存设备
        2. 计算密集层放高算力设备
        3. 尽量将相邻层放同一设备减少通信
        4. 流水线阶段边界对齐设备边界
        """
        # 按设备能力排序：显存大 -> 算力强
        sorted_devices = sorted(
            enumerate(self.capabilities),
            key=lambda x: (x[1].vram_mb, x[1].compute_tflops),
            reverse=True
        )
        
        # 分层策略：
        # - Embedding/大 Linear -> 最大显存设备
        # - Attention/FFN -> 高算力设备
        # - Norm/Small layers -> 剩余设备
        
        # 计算总显存需求
        total_param_mem = sum(info.param_memory_mb for info in layer_infos)
        total_activation_mem = sum(info.activation_memory_mb for info in layer_infos)
        
        # 为每个设备分配显存预算
        device_budgets = {}
        for rank, cap in sorted_devices:
            # 预留 20% 显存给激活值和优化器状态
            usable_vram = cap.vram_mb * 0.8
            device_budgets[rank] = usable_vram
        
        # 贪心分配
        layer_to_device = {}
        layer_to_rank = {}
        device_mem_used = {rank: 0 for rank, _ in sorted_devices}
        device_compute = {rank: 0 for rank, _ in sorted_devices}
        
        for info in layer_infos:
            required_mem = info.param_memory_mb + info.activation_memory_mb
            
            # 选择最佳设备
            best_rank = None
            best_score = -1
            
            for rank, cap in sorted_devices:
                if device_mem_used[rank] + required_mem > device_budgets[rank]:
                    continue
                
                # 评分：显存剩余 + 算力匹配
                mem_score = (device_budgets[rank] - device_mem_used[rank]) / device_budgets[rank]
                compute_score = cap.normalized_compute / max(sum(c.normalized_compute for _, c in sorted_devices), 1)
                
                # 层类型偏好
                type_bonus = 0
                if info.layer_type == "embedding" and cap.vram_mb == max(c.vram_mb for _, c in sorted_devices):
                    type_bonus = 10
                elif info.layer_type in ("linear", "attention") and cap.compute_tflops == max(c.compute_tflops for _, c in sorted_devices):
                    type_bonus = 5
                
                score = mem_score * 0.4 + compute_score * 0.4 + type_bonus * 0.2
                
                if score > best_score:
                    best_score = score
                    best_rank = rank
            
            if best_rank is None:
                # 兜底：放到显存最大的设备
                best_rank = sorted_devices[0][0]
            
            device = self.capabilities[best_rank].device
            layer_to_device[info.name] = device
            layer_to_rank[info.name] = best_rank
            device_mem_used[best_rank] += required_mem
            device_compute[best_rank] += info.compute_flops
        
        # 构建 PP 阶段（基于 rank 分组）
        ranks_used = sorted(set(layer_to_rank.values()))
        rank_to_stage = {rank: i for i, rank in enumerate(ranks_used)}
        stage_boundaries = []
        for i, info in enumerate(layer_infos):
            if i == 0 or layer_to_rank[info.name] != layer_to_rank[layer_infos[i-1].name]:
                stage_boundaries.append(i)
        
        return PlacementPlan(
            layer_to_device=layer_to_device,
            layer_to_rank=layer_to_rank,
            stage_boundaries=stage_boundaries,
            dp_groups=strategy.dp_groups,
            memory_usage_per_device=device_mem_used,
            compute_balance=device_compute,
            communication_volume=self._estimate_communication(layer_infos, layer_to_rank),
        )
    
    def _place_hybrid(self, layer_infos: List[LayerInfo], strategy: ParallelStrategy) -> PlacementPlan:
        """混合并行：PP + DP + TP 组合"""
        # 简化：先按 PP 切分，组内 DP
        return self._place_pipeline_parallel(layer_infos, strategy)
    
    def _estimate_communication(
        self, 
        layer_infos: List[LayerInfo], 
        layer_to_rank: Dict[str, int]
    ) -> Dict[Tuple[int, int], float]:
        """估算设备间通信量"""
        comm = {}
        for i in range(len(layer_infos) - 1):
            rank_a = layer_to_rank.get(layer_infos[i].name, 0)
            rank_b = layer_to_rank.get(layer_infos[i+1].name, 0)
            if rank_a != rank_b:
                # 通信量 ≈ 激活值大小
                act_mem = layer_infos[i].activation_memory_mb
                key = (min(rank_a, rank_b), max(rank_a, rank_b))
                comm[key] = comm.get(key, 0) + act_mem
        return comm


def apply_placement(model: nn.Module, plan: PlacementPlan) -> nn.Module:
    """将放置计划应用到模型（移动参数到目标设备）"""
    for name, module in model.named_modules():
        if name in plan.layer_to_device:
            device = plan.layer_to_device[name]
            module.to(device)
    return model