# 项目全面重构方案

## 🎯 核心假设与决策

| 决策点 | 假设/选择 | 理由 |
|---------|----------|------|
| **重构范围** | 完全重写训练核心 | 现有 jobs/trainer/worker 耦合度高，不支持新需求（Notebook、异构分布式、Pipeline） |
| **保留基础设施** | storage.py、dataset_manager.py、remote_workers.py、config.py、coordination.py、database.py、db_utils.py、routers/datasets.py | 存储抽象、数据集管理、远程Worker管理、配置、协调平面、数据库、数据集API 已成熟稳定 |
| **分布式策略** | **流水线并行 + 数据并行混合 + 异构感知调度** | 1) 模型切分跨设备（流水线并行）解决显存不足<br>2) 同构组内数据并行（DDP）最大化吞吐<br>3) 调度器感知异构算力，动态分配微批次/层 |
| **前端架构** | 独立 Vue 3 + Vite + TypeScript 工程 (`frontend-vue/`) | 现代工程化、类型安全、热更新、生态丰富，通过 nginx 反向代理 API |
| **Notebook内核** | **自研轻量执行器 + 计算资源选择器** | 非 Jupyter Kernel；会话绑定 Worker(s)；单选=单机训练/推理，多选=分布式训练 |
| **模型注册表** | 复用 ArtifactStore (S3/Local) + 新增 DB 表 `registered_models` | 兼容现有存储抽象，元数据入库便于查询/版本管理 |

---

## 🏗️ 新架构总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              前端层 (Vue 3 + Vite)                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ Workbench│ │ Templates│ │ Models   │ │Pipelines │ │ Clusters │          │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │ HTTPS/WS
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API 网关层 (FastAPI)                             │
│  /api/workbench/*  /api/templates/*  /api/models/*  /api/pipelines/*        │
│  /api/clusters/*   /api/datasets/*   /api/remote-servers/*                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│   编排控制平面       │   │   状态平面           │   │   计算平面           │
│  ┌───────────────┐  │   │  ┌───────────────┐  │   │  ┌───────────────┐  │
│  │ Pipeline Engine│  │   │  │ Redis Queue   │  │   │  │ Worker Pool   │  │
│  │ (DAG执行器)    │  │   │  │ Lease/Heartbeat│  │   │  │ (异构感知)     │  │
│  ├───────────────┤  │   │  ├───────────────┤  │   │  ├───────────────┤  │
│  │ Notebook Mgr  │  │   │  │ S3 Artifacts  │  │   │  │ Trainer Core  │  │
│  │ (会话/内核)    │  │   │  │ (模型/数据/    │  │   │  │ (统一训练入口) │  │
│  ├───────────────┤  │   │  │  Checkpoint)  │  │   │  ├───────────────┤  │
│  │ Template Eng  │  │   │  └───────────────┘  │   │  │ Distributed   │  │
│  │ (模板实例化)   │  │   │  ┌───────────────┐  │   │  │ Coordinator   │  │
│  ├───────────────┤  │   │  │ MySQL Mirror  │  │   │  │ (流水线/数据/  │  │
│  │ Model Registry│  │   │  │ (注册表/任务/   │  │   │  │  张量并行)     │  │
│  └───────────────┘  │   │  │  数据集元数据)   │  │   │  └───────────────┘  │
└─────────────────────┘   └─────────────────────┘   └─────────────────────┘
```

---

## 📦 模块拆解与文件规划

### 保留模块（仅必要修改）
```
backend/app/
├── config.py              ✅ 保留，新增分布式/Notebook/Pipeline 配置项
├── storage.py             ✅ 保留，复用 ArtifactStore 抽象
├── dataset_manager.py     ✅ 保留，新增 pull_to_oss、get_oss_path 方法
├── remote_workers.py      ✅ 保留，复用 SSH 部署能力
├── coordination.py        ✅ 保留，复用 Queue/Lease/Worker 注册
├── database.py            ✅ 保留，新增 registered_models/pipelines/notebook_sessions 表
├── db_utils.py            ✅ 保留，新增 CRUD 方法
├── routers/
│   ├── datasets.py        ✅ 保留，复用数据集 API
│   └── common.py          ✅ 保留，通用工具
├── device.py              ✅ 保留，设备探测
├── runtime_info.py        ✅ 保留，代码指纹/框架版本
├── performance.py         ✅ 保留，性能采样
├── inference.py           ✅ 保留，推理服务
├── predictor.py           ✅ 保留，预测加载
├── jobstore.py            🔄 重写：适配新任务模型
├── models.py              🔄 重写：新增核心数据模型
```

### 删除模块
```
backend/app/
├── jobs.py                ❌ 删除 → 由 training_job.py + pipeline_engine.py 替代
├── trainer.py             ❌ 删除 → 由 training/core.py + training/distributed.py 替代
├── worker.py              ❌ 删除 → 由 compute/worker_host.py + compute/kernel_executor.py 替代
├── parallel.py            ❌ 删除 → 合并到 training/distributed.py
├── parallel_worker.py     ❌ 删除 → 合并到 training/distributed.py
├── llm_trainer.py         ❌ 删除 → 合并到 training/tasks/llm.py
├── routers/jobs.py        ❌ 删除 → 拆分为 routers/training_jobs.py, routers/notebook.py, routers/pipelines.py
```

### 新增模块结构
```
backend/app/
├── models.py                           # 统一数据模型（Pydantic + SQLAlchemy）
├── training/
│   ├── __init__.py
│   ├── core.py                         # 统一训练入口：run_training(spec, context)
│   ├── context.py                      # 训练上下文：数据、设备、检查点、日志、分布式环境
│   ├── checkpoint.py                   # 统一检查点管理（保存/加载/裁剪/跨设备迁移）
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── base.py                     # Task 基类
│   │   ├── classification.py
│   │   ├── regression.py
│   │   ├── clustering.py
│   │   ├── time_series.py
│   │   └── llm.py                      # pretrain/sft/dpo/ppo 统一入口
│   └── distributed/
│       ├── __init__.py
│       ├── coordinator.py              # 分布式协调器（主节点）
│       ├── worker.py                   # 分布式 Worker（子进程/远程）
│       ├── strategy.py                 # 并行策略：DP/PP/TP/FSDP/异构混合
│       ├── placer.py                   # 异构感知设备放置（拓扑感知、显存/算力加权）
│       ├── communicator.py             # 通信后端抽象（NCCL/Gloo/MPI）
│       └── profiler.py                 # 分布式性能分析
├── compute/
│   ├── __init__.py
│   ├── worker_host.py                  # Worker 宿主进程（注册、心跳、拉取任务、启动内核）
│   ├── kernel_executor.py              # 代码执行内核（Notebook/脚本/模板实例化）
│   ├── resource_selector.py            # 计算资源选择器（单选/多选/自动调度）
│   ├── session_manager.py              # Notebook 会话管理
│   └── environment.py                  # 执行环境隔离（conda/venv/容器）
├── orchestration/
│   ├── __init__.py
│   ├── pipeline_engine.py              # DAG 执行引擎
│   ├── steps/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── dataset_pull.py
│   │   ├── dataset_process.py
│   │   ├── training.py
│   │   ├── model_upload.py
│   │   └── custom_script.py
│   ├── scheduler.py                    # 定时/事件触发调度器
│   └── artifact_passing.py             # 步骤间产物传递（OSS key + 元数据）
├── registry/
│   ├── __init__.py
│   ├── model_registry.py               # 模型注册表核心
│   ├── template_registry.py            # 脚本模板注册表
│   └── versioning.py                   # 语义化版本管理
├── routers/
│   ├── training_jobs.py                # 训练任务 API
│   ├── notebook.py                     # Notebook 会话/内核 API
│   ├── templates.py                    # 模板库 API
│   ├── models.py                       # 模型注册表 API
│   ├── pipelines.py                    # Pipeline 编排 API
│   └── clusters.py                     # 计算集群/Worker 管理 API
└── utils/
    ├── code_executor.py                # 安全代码执行工具
    └── distributed_utils.py            # 分布式工具函数
```

---

## 🔑 核心设计详解

### 1. 统一训练入口 `training/core.py`

```python
"""所有训练（单机/分布式/Notebook/模板/Pipeline步骤）的唯一入口"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from pathlib import Path

@dataclass
class TrainingContext:
    """训练运行时上下文，包含一切执行所需资源"""
    job_id: str
    job_dir: Path                    # 本地工作目录 (scratch/job_id)
    store: ArtifactStore             # 产物存储
    dataset_path: Path               # 已解析的数据集本地路径
    device: torch.device             # 当前进程设备
    distributed: Optional[DistributedContext] = None  # 分布式环境（None=单机）
    log_fn: Callable[[str], None] = print
    checkpoint_dir: Path = None
    metrics_path: Path = None
    cancel_event: threading.Event = None
    pause_event: threading.Event = None
    resume_state: Optional[dict] = None  # 断点续训状态
    
@dataclass
class DistributedContext:
    """分布式训练上下文"""
    rank: int
    world_size: int
    local_rank: int
    backend: str                     # nccl/gloo
    strategy: ParallelStrategy       # 并行策略对象
    process_group: Any = None        # torch.distributed.ProcessGroup
    pipeline_stages: List[int] = None  # 流水线阶段划分
    dp_groups: List[List[int]] = None  # 数据并行组

def run_training(
    spec: TrainingSpec,              # 统一训练规格（含任务类型、超参、资源提示）
    context: TrainingContext
) -> TrainingSummary:
    """
    统一训练执行流程：
    1. 解析 spec → 确定 Task 实现类
    2. 初始化分布式环境（如果 context.distributed 不为 None）
    3. 准备数据（DataLoader、分布式采样器）
    4. 构建模型/优化器/调度器（按策略放置到设备）
    5. 恢复 checkpoint（如果有）
    6. 训练循环：前向→反向→同步→优化器步进→评估→检查点
    7. 保存最终产物、上传到 OSS、注册模型
    8. 返回 TrainingSummary
    """
```

### 2. 异构感知分布式策略 `training/distributed/strategy.py`

```python
"""并行策略定义与异构感知放置"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import torch

class ParallelMode(Enum):
    DATA_PARALLEL = "dp"           # DDP/FSDP
    PIPELINE_PARALLEL = "pp"       # Pipeline (GPipe/1F1B)
    TENSOR_PARALLEL = "tp"         # Tensor Parallel (Megatron-style)
    HYBRID = "hybrid"              # 混合并行
    HETEROGENEOUS = "heterogeneous" # 异构混合：不同算力设备分担不同工作

@dataclass
class DeviceCapability:
    """设备能力描述"""
    worker_id: str
    device: torch.device
    device_type: str               # cuda/rocm/xpu/cpu
    vram_mb: int
    compute_tflops: float          # 理论峰值算力
    memory_bandwidth_gbps: float
    interconnect: str              # nvlink/pcie/ethernet
    tags: Dict[str, str] = field(default_factory=dict)  # 如 {"zone": "rack1"}

@dataclass
class ParallelStrategy:
    """并行策略：描述模型如何切分、数据如何分发"""
    mode: ParallelMode
    # 数据并行
    dp_degree: int = 1
    dp_groups: List[List[int]] = None  # rank 列表
    # 流水线并行
    pp_degree: int = 1
    pp_stages: List[List[int]] = None  # 每阶段包含的层索引
    # 异构感知
    device_capabilities: List[DeviceCapability] = None
    # 异构分配策略
    hetero_assignment: Dict[int, Dict] = None  # rank -> {layers, micro_batch_size, weight}
    
    def get_device_mesh(self) -> torch.distributed.DeviceMesh:
        """构建 DeviceMesh，供 PyTorch DTensor/FSDP 使用"""
        pass
    
    def compute_micro_batch_allocation(self, global_batch: int) -> Dict[int, int]:
        """异构感知微批次分配：算力强的分更多微批次"""
        if self.mode != ParallelMode.HETEROGENEOUS:
            return {rank: global_batch // self.dp_degree for rank in range(self.world_size)}
        # 加权分配
        total_flops = sum(c.compute_tflops for c in self.device_capabilities)
        return {
            i: max(1, int(global_batch * c.compute_tflops / total_flops))
            for i, c in enumerate(self.device_capabilities)
        }
```

### 3. 异构设备放置器 `training/distributed/placer.py`

```python
"""模型层到异构设备的最优放置"""

class HeterogeneousPlacer:
    """
    解决：不同 GPU 规格（如 A100 80G + RTX 3090 24G + CPU）如何协同训练
    
    策略：
    1. 拓扑感知：同机 NVLink 优先放同一 PP 阶段或 TP 组
    2. 显存约束：大模型层放大显存 GPU，小层/Embedding 放小显存
    3. 算力加权：前向/反向计算量大的层放高算力 GPU
    4. 通信最小化：减少跨慢速互联（以太网）的激活/梯度传输
    """
    
    def place_model(
        self, 
        model: nn.Module, 
        capabilities: List[DeviceCapability],
        strategy: ParallelStrategy
    ) -> Dict[str, torch.device]:
        """
        返回 {parameter_name: device} 映射
        支持：
        - Pipeline 并行：按层切分，每阶段分配到一组设备
        - 数据并行：同一层复制到 DP 组内所有设备
        - 异构微批次：不同设备处理不同大小的微批次
        """
        # 1. 分析模型结构：层数、参数量、激活大小、计算量
        layer_infos = self._analyze_model(model)
        
        # 2. 构建成本模型：显存占用、计算时间、通信时间
        cost_model = self._build_cost_model(layer_infos, capabilities)
        
        # 3. 搜索最优放置（动态规划/贪心/ILP）
        placement = self._optimize_placement(cost_model, strategy)
        
        return placement
```

### 4. Notebook 内核执行器 `compute/kernel_executor.py`

```python
"""轻量级代码执行内核：非 Jupyter，支持计算资源绑定"""

class KernelExecutor:
    """
    执行模式：
    - 单资源：在指定 Worker 进程中执行（通过 Unix socket / gRPC）
    - 多资源：启动分布式协调器 + 多个 Worker 进程，建立通信组
    
    变量持久化：会话级内存字典 + 可选序列化到 Redis
    """
    
    def __init__(self, session_id: str, bound_workers: List[str]):
        self.session_id = session_id
        self.bound_workers = bound_workers  # Worker IDs
        self.variables: Dict[str, Any] = {}
        self.execution_count = 0
        
    def execute(
        self, 
        code: str, 
        mode: Literal["single", "distributed"] = "single",
        timeout: float = 300
    ) -> ExecutionResult:
        """
        执行代码片段：
        - single: 在主 Worker 进程中 exec()，捕获 stdout/stderr/返回值/异常
        - distributed: 生成分布式启动脚本，在多 Worker 上并行执行
        """
        if mode == "single":
            return self._execute_local(code, timeout)
        else:
            return self._execute_distributed(code, timeout)
    
    def _execute_distributed(self, code: str, timeout: float) -> ExecutionResult:
        """
        1. 解析代码中的分布式标记（如 @distributed, torch.distributed.init_process_group）
        2. 生成启动脚本：设置 MASTER_ADDR, MASTER_PORT, RANK, WORLD_SIZE
        3. 通过 coordination 平面在各 Worker 启动子进程
        4. 收集各 rank 的输出/结果
        """
        pass
```

### 5. Pipeline DAG 执行引擎 `orchestration/pipeline_engine.py`

```python
"""独立 DAG 执行引擎：不复用 JobManager，专注工作流编排"""

@dataclass
class PipelineStep:
    id: str
    name: str
    type: StepType  # DATASET_PULL / DATASET_PROCESS / TRAINING / MODEL_UPLOAD / CUSTOM
    config: Dict[str, Any]           # 步骤参数
    depends_on: List[str] = field(default_factory=list)
    resource_hint: ResourceHint = None
    retry_policy: RetryPolicy = None
    timeout: float = 3600

@dataclass
class PipelineRun:
    pipeline_id: str
    run_id: str
    status: RunStatus
    step_states: Dict[str, StepState]  # step_id -> state
    artifact_map: Dict[str, str]       # step_id -> OSS artifact key
    started_at: float
    finished_at: Optional[float]

class PipelineEngine:
    def __init__(self, store: ArtifactStore, lease: LeaseStore, queue: JobQueue):
        self.store = store
        self.lease = lease
        self.queue = queue
        
    async def execute(self, pipeline: Pipeline, trigger: Trigger) -> PipelineRun:
        """拓扑排序执行 DAG，支持并行分支、失败重试、产物传递"""
        # 1. 拓扑排序得到执行层级
        levels = self._topological_sort(pipeline.steps)
        
        # 2. 逐层执行，同层并行
        for level in levels:
            await self._execute_level(level, pipeline, run)
            
        # 3. 收集最终产物
        return run
    
    async def _execute_step(self, step: PipelineStep, run: PipelineRun) -> StepResult:
        """单步执行：根据类型分派"""
        if step.type == StepType.DATASET_PULL:
            return await self._run_dataset_pull(step, run)
        elif step.type == StepType.TRAINING:
            return await self._run_training(step, run)
        # ... 其他类型
    
    async def _run_training(self, step: PipelineStep, run: PipelineRun) -> StepResult:
        """调用统一训练入口 training.core.run_training"""
        # 从 artifact_map 解析上游产物（数据集路径、预处理器等）
        # 构建 TrainingSpec 和 TrainingContext
        # 提交到训练队列或直接执行（同步等待）
        pass
```

### 6. 模型注册表 `registry/model_registry.py`

```python
"""模型注册表：训练产物自动注册、版本管理、部署"""

class ModelRegistry:
    def __init__(self, store: ArtifactStore, db: SessionLocal):
        self.store = store
        self.db = db
        
    def register_from_training(
        self, 
        job_id: str, 
        summary: TrainingSummary,
        name: str = None,
        tags: List[str] = None,
        description: str = ""
    ) -> RegisteredModel:
        """
        训练完成后自动调用：
        1. 从 OSS 读取 best.pt、preprocessor.json、summary.json
        2. 生成模型 ID、版本号（语义化或自动递增）
        3. 复制/移动产物到模型注册表命名空间：models/{model_id}/v{version}/
        4. 写入 DB 元数据
        5. 返回 RegisteredModel
        """
        pass
    
    def deploy(self, model_id: str, version: str, config: DeployConfig) -> Deployment:
        """部署为推理服务：创建推理 Worker、注册到服务发现、返回端点"""
        pass
```

---

## 🗄️ 数据库 Schema 变更

```sql
-- 新增表（在 db_init.py 中定义）

-- Notebook 会话
CREATE TABLE notebook_sessions (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    owner VARCHAR(128),
    bound_workers JSON,              -- 绑定的 Worker IDs
    status VARCHAR(32) NOT NULL,     -- starting/running/idle/busy/stopped/error
    kernel_type VARCHAR(16) DEFAULT 'python',
    metadata JSON,                   -- 环境变量、依赖、挂载等
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    last_activity DOUBLE PRECISION NOT NULL
);

-- Notebook 单元格历史
CREATE TABLE notebook_cells (
    id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL REFERENCES notebook_sessions(id),
    cell_index INT NOT NULL,
    cell_type VARCHAR(16) NOT NULL,  -- code/markdown
    source TEXT NOT NULL,
    outputs JSON,
    execution_count INT,
    status VARCHAR(16) NOT NULL,     -- pending/running/completed/error
    started_at DOUBLE PRECISION,
    finished_at DOUBLE PRECISION
);

-- 脚本模板
CREATE TABLE script_templates (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    description TEXT,
    category VARCHAR(64) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,           -- Jinja2 模板
    parameters JSON NOT NULL,        -- 参数定义
    dataset_requirements JSON,
    is_builtin BOOLEAN DEFAULT FALSE,
    created_by VARCHAR(128),
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

-- 模型注册表
CREATE TABLE registered_models (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    version VARCHAR(64) NOT NULL,
    source_job_id VARCHAR(64),
    task VARCHAR(64) NOT NULL,
    framework VARCHAR(32) NOT NULL,  -- torch/sklearn/xgboost
    metrics JSON,                    -- 关键指标
    artifacts JSON,                  -- OSS 路径 {best, preprocessor, ...}
    tags JSON,
    description TEXT,
    status VARCHAR(16) DEFAULT 'ready',  -- ready/archived/deployed
    deployed_endpoint VARCHAR(512),
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    UNIQUE(name, version)
);

-- Pipeline 定义
CREATE TABLE pipelines (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    description TEXT,
    steps JSON NOT NULL,             -- 步骤定义数组
    schedule VARCHAR(128),           -- cron 表达式
    status VARCHAR(16) DEFAULT 'draft', -- draft/active/paused/archived
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

-- Pipeline 执行记录
CREATE TABLE pipeline_runs (
    id VARCHAR(64) PRIMARY KEY,
    pipeline_id VARCHAR(64) NOT NULL REFERENCES pipelines(id),
    trigger VARCHAR(32) NOT NULL,    -- manual/scheduled/event
    status VARCHAR(16) NOT NULL,     -- running/completed/failed/cancelled
    step_states JSON,                -- 每步状态
    artifact_map JSON,               -- 步骤产物映射
    started_at DOUBLE PRECISION NOT NULL,
    finished_at DOUBLE PRECISION,
    error TEXT
);
```

---

## 🔄 迁移路径

### Phase 1: 基础设施就绪（Week 1）
- [ ] 创建新模块目录结构
- [ ] 编写 `models.py` 统一数据模型
- [ ] 更新 `database.py` / `db_init.py` 新增表
- [ ] 更新 `config.py` 新增配置项
- [ ] 单元测试：模型序列化、DB CRUD

### Phase 2: 训练核心重写（Week 1-2）
- [ ] `training/core.py` 统一入口
- [ ] `training/tasks/*` 各任务类型实现
- [ ] `training/checkpoint.py` 统一检查点
- [ ] `training/distributed/*` 分布式协调、策略、放置、通信
- [ ] 单元测试：单机训练、分布式策略构建、检查点跨设备加载

### Phase 3: 计算平面重写（Week 2）
- [ ] `compute/worker_host.py` Worker 宿主
- [ ] `compute/kernel_executor.py` 代码执行内核
- [ ] `compute/resource_selector.py` 资源选择器
- [ ] `compute/session_manager.py` Notebook 会话
- [ ] 集成测试：Notebook 单步执行、多资源分布式执行

### Phase 4: 编排与注册表（Week 2-3）
- [ ] `orchestration/pipeline_engine.py` DAG 执行
- [ ] `orchestration/steps/*` 步骤实现
- [ ] `registry/model_registry.py` 模型注册
- [ ] `registry/template_registry.py` 模板库
- [ ] 集成测试：Pipeline 端到端、模型自动注册

### Phase 5: API 层（Week 3）
- [ ] `routers/training_jobs.py` 训练任务 API
- [ ] `routers/notebook.py` Notebook API
- [ ] `routers/templates.py` 模板 API
- [ ] `routers/models.py` 模型注册表 API
- [ ] `routers/pipelines.py` Pipeline API
- [ ] `routers/clusters.py` 集群管理 API

### Phase 6: 前端 Vue 3 工程（Week 3-4）
- [ ] 初始化 `frontend-vue/` (Vite + Vue 3 + TS + Pinia + Vue Router)
- [ ] 页面：Workbench（Monaco Editor + WebSocket）、Templates、Models、Pipelines、Clusters
- [ ] 组件库：资源选择器、DAG 编辑器、模型卡片、代码单元格
- [ ] E2E 测试

### Phase 7: 集成验证与文档（Week 4）
- [ ] 端到端流程验证
- [ ] 性能基准测试（异构分布式训练吞吐）
- [ ] 更新 `TEST_REPORT.md`
- [ ] 编写使用文档

---

## 🧪 关键测试用例

| 模块 | 测试场景 | 验收标准 |
|------|---------|---------|
| **training.core** | 单机分类/回归/聚类/时序/LLM 训练 | 指标达标、产物完整、可断点续训 |
| **distributed.strategy** | 同构 4xA100 DP、异构 A100+3090 PP+DP | 正确构建 DeviceMesh、微批次分配加权正确 |
| **distributed.placer** | 大模型跨 80G/24G/CPU 放置 | 显存不溢出、通信量最小、负载均衡 |
| **kernel_executor** | Notebook 单步执行、变量持久化、多资源分布式执行 | 输出正确、变量跨单元格共享、分布式所有 rank 完成 |
| **pipeline_engine** | 5步 DAG：Pull→Process→Train→Upload→Deploy | 拓扑序正确、产物传递正确、失败重试生效 |
| **model_registry** | 训练完成自动注册、版本递增、部署推理 | 注册表可见、版本可查、部署端点可调用 |
| **resource_selector** | 单选/多选/自动调度、资源提示校验 | 正确绑定 Worker、不匹配时拒绝/回退队列 |

---

## ⚠️ 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 分布式协调复杂度高 | 高 | 先实现单机训练核心，再逐步加入 DP → PP → 异构；复用 PyTorch DeviceMesh/DTensor |
| 异构放置算法最优解难求 | 中 | 先实现启发式贪心（显存优先 + 算力加权），后续引入 ILP/强化学习优化 |
| Notebook 变量跨进程持久化 | 中 | 单机模式进程内字典；分布式模式序列化到 Redis（仅可序列化对象） |
| 前端 Monaco Editor 大文件加载 | 低 | 按需加载、Worker 预加载、虚拟滚动 |
| 现有数据迁移 | 中 | 编写迁移脚本：旧 jobs → 新 training_jobs，旧模型文件 → 注册表 |

---

## 📋 交付清单

1. **后端重写代码** - 约 15 个新模块，完全替代 jobs/trainer/worker/parallel/llm_trainer
2. **前端 Vue 3 工程** - `frontend-vue/` 完整工程化项目
3. **数据库迁移脚本** - `scripts/migrate_to_new_schema.py`
4. **内置脚本模板** - 6 个内置模板（分类/回归/聚类/时序/LLM-SFT/LLM-DPO）
5. **测试报告** - `TEST_REPORT.md` 完整记录
6. **部署配置** - 更新 `docker-compose.yml`、`docker-compose.remote.yml` 支持新架构
7. **文档** - API 文档、架构设计文档、使用指南

---

## ✅ 下一步行动

请确认上述方案，重点关注：
1. **分布式异构策略** 是否接受"流水线+数据并行混合+异构感知放置"方向
2. **前端独立工程** 目录结构和构建部署方式
3. **迁移策略** 是否需要保留旧 API 兼容期

确认后我将开始 Phase 1 实施。