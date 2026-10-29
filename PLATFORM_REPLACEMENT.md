# 可视化模型训练平台替代方案

## 目标与结论

本文为现有可视化模型训练项目提供一套可直接采用的开源替代方案，不在现有项目上继续扩展调度或训练逻辑。目标环境为 3 台型号不同的 GPU 设备和 5 台 CPU 服务器，训练任务覆盖 LLM、OCR、计算机视觉、语音、表格模型及传统机器学习。

**首选方案：自托管 ClearML + ClearML Agent + MinIO/S3 + 按模型家族维护的容器镜像。**ClearML 提供网页任务队列、远程执行、日志、指标、数据集与模型管理，最接近“部署后即可管理多种自定义训练代码”的平台替代品。GPU 和 CPU 机器分别运行 Agent，并按硬件能力绑定队列。

如果首要要求是 HPC 级公平调度、资源配额、抢占和多节点资源预留，则选 **Slurm + Open OnDemand + Apptainer + MLflow + MinIO/S3**。如果已有 Kubernetes 运维能力并需要云原生多租户，则评估 **Kubernetes + Kueue + Kubeflow Trainer/Pipelines + MLflow + MinIO/S3**。

目前没有一个成熟开源产品能同时内置 LLM、OCR、语音、视觉和表格任务的全部算法、预训练权重、数据处理及一键训练配方。这里的“支持多种模型”是指平台能通过独立容器运行相应领域的成熟训练框架和代码。

## 推荐架构：ClearML

```text
用户浏览器
    |
ClearML Web UI / API
    ├── 任务队列与远程执行
    ├── 实验、日志、指标、数据集和模型版本
    └── 用户与项目管理（按所选版本核实权限能力）
          |
    ClearML Agent（每台计算服务器）
    ├── CPU 队列
    ├── GPU-A 队列
    ├── GPU-B 队列
    ├── GPU-C 队列
    └── 可选：特定框架/大显存队列
          |
    容器镜像（PyTorch / PaddlePaddle / CPU ML 等）
          |
    MinIO 或兼容 S3 存储（数据集、checkpoint、模型产物）
```

### 组件职责

| 组件 | 职责 |
|---|---|
| ClearML Server | 网页入口、任务队列、任务状态、日志与指标、数据集和模型记录 |
| ClearML Agent | 从指定队列领取任务，在对应机器和容器环境中执行训练 |
| Docker | 隔离不同训练框架及其 CUDA、PyTorch、PaddlePaddle 依赖 |
| MinIO/S3 | 保存数据集版本、训练 checkpoint、模型和其他产物 |
| 训练框架 | 执行具体的模型训练、评估、预测与导出 |

### 资源与队列设计

CPU 和三类 GPU 分开建队列。每台机器上的 Agent 只监听与本机硬件和运行环境匹配的队列。具体 GPU 名称、显存和软件后端应通过机器清单及队列配置管理；不要假定队列会自动推断任务的显存需求。

| 队列 | 放入的资源 | 典型工作负载 |
|---|---|---|
| `cpu-small` | 5 台 CPU 服务器 | 小模型、传统机器学习、交叉验证、数据预处理、超参数组合 |
| `gpu-a` | GPU 设备 A | 满足其显存和 CUDA/ROCm/XPU 环境的单卡任务 |
| `gpu-b` | GPU 设备 B | 满足其显存和运行环境的单卡任务 |
| `gpu-c` | GPU 设备 C | 满足其显存和运行环境的单卡任务 |
| `gpu-large` | 实际显存达到要求的设备 | LLM 微调、较大视觉模型等；按硬件情况映射到一个或多个 GPU 队列 |

优先将多个独立训练、交叉验证和超参数搜索任务分配给不同设备。小模型可以跑 CPU，单卡能容纳的模型跑合适的 GPU。只有确认训练框架支持且任务确实受益时，才提交多机多卡任务。

不同 GPU 可以各自训练独立任务，但不能自动合并显存。不同型号 GPU 混入同一个同步训练组时，通常受最慢设备、通信和最小可用显存限制。CPU 资源适合承担独立 CPU 训练、数据处理和评估；一般不将 CPU 与 GPU 组成同一个同步数据并行组。

### 模型框架组合

每个领域使用独立容器镜像，避免依赖冲突。所有领域都通过 ClearML 记录任务、日志和指标；训练脚本仍由相应领域框架负责。

| 模型领域 | 推荐训练框架 | 执行环境建议 |
|---|---|---|
| LLM 预训练与微调 | PyTorch、Hugging Face Transformers/Datasets、PEFT、TRL | 独立 PyTorch/CUDA 镜像；大模型按需要加入 FSDP 或 DeepSpeed |
| OCR 与文档理解 | PaddleOCR；或 OpenMMLab MMOCR | PaddlePaddle 与 PyTorch/MMOCR 使用不同镜像 |
| 图像分类、检测与分割 | PyTorch、OpenMMLab MMPreTrain/MMDetection/MMSegmentation | 视觉训练镜像 |
| 语音与 ASR | NVIDIA NeMo、Whisper/Hugging Face、ESPnet | 按 GPU 后端单独构建镜像 |
| 表格与传统机器学习 | scikit-learn、XGBoost、LightGBM、CatBoost | 优先使用 CPU 镜像；根据算法和数据规模决定是否用 GPU |
| 强化学习 | Stable-Baselines3；分布式场景可评估 RLlib | CPU/GPU 需求由具体环境和算法声明 |
| 超参数优化 | ClearML Hyperparameter Optimization 或 Optuna | 每组试验作为独立队列任务，设置并发上限 |

对 AMD ROCm、Intel XPU 等设备，应单独验证驱动、运行时和框架镜像。CPU、CUDA、ROCm、XPU 的依赖不能仅靠一个通用镜像保证兼容。Apple MPS 需要 macOS 原生执行环境，不作为 Linux GPU 容器处理。

## 两条备选路线

### HPC 调度优先：Slurm

```text
Open OnDemand（浏览器门户）
    -> Slurm（队列、分区、配额、公平调度、资源预留）
    -> Apptainer 容器（训练环境）
    -> PyTorch DDP/FSDP、DeepSpeed、MMOCR、PaddleOCR 等
    -> MLflow（实验和模型记录）+ MinIO/S3（产物）
```

Slurm 是固定服务器集群的成熟批处理调度器，适合按 GPU 型号和 CPU 队列划分资源，也适合需要多节点作业资源预留的训练。但它不是一个现成的模型训练 SaaS：用户通常需要提交训练脚本或作业模板。Open OnDemand 提供浏览器访问与作业入口，MLflow 提供实验记录；它们不会自动生成覆盖各领域的一键训练流程。

适用条件：资源公平性和多节点调度比低门槛的模型任务 UI 更重要，且团队接受以脚本、容器和作业模板使用集群。

### Kubernetes 平台优先：Kubeflow

```text
Kubernetes（容器与节点管理）
    + Kueue（批任务队列与准入）
    + Kubeflow Trainer/Pipelines（分布式训练与工作流）
    + MLflow（实验和模型记录）
    + MinIO/S3（数据和产物）
```

这套体系适合已有 Kubernetes 团队、需要多租户和统一容器平台的组织。GPU 节点需要相应厂商的 Kubernetes Device Plugin；不同 GPU 型号要通过节点标签、资源类型和调度策略区分。对 8 台固定服务器来说，集群的部署、升级、网络和存储维护成本通常高于 Slurm 或 ClearML Agent。

## 方案比较与选择

| 维度 | ClearML 自托管 | Slurm + Open OnDemand | Kubernetes + Kubeflow |
|---|---|---|---|
| 现成训练任务 UI | 强 | 中，偏门户和作业提交 | 强，需组合多个组件 |
| 固定裸机集群调度 | 中，主要依赖 Agent 队列 | 强 | 强，但依赖 K8s 资源插件和组件配置 |
| 多种模型框架 | 运行自定义脚本/容器，覆盖广 | 运行自定义脚本/容器，覆盖广 | 运行自定义容器，覆盖广 |
| HPC 级配额、公平调度、抢占 | 需核实具体能力/版本 | 强 | 通过 Kueue 等组件实现 |
| 部署和维护负担 | 低到中 | 中 | 高 |
| 对 8 台机器的匹配度 | **高，最接近现有产品形态** | **高，适合批任务和资源治理** | 仅在已有 K8s 能力时高 |

**当前推荐：先评估 ClearML 自托管作为直接替代平台。**它能更快替代现有的训练任务 UI、队列和实验记录；模型多样性通过不同容器镜像和训练代码提供。若试用发现队列无法满足公平调度或多机资源原子预留，再选 Slurm 作为主调度器，而不是同时叠加多个调度器。

如果首要目标明确为集群级公平调度和多机资源组，直接选 Slurm 方案；如果已经有 Kubernetes 团队，则不要额外引入 Slurm，可评估 Kubeflow/Kueue。

## 落地与验收

1. **硬件盘点**：记录每台机器的 CPU 核数、内存、GPU 型号/显存、驱动、CUDA/ROCm/XPU 版本、网络和磁盘，并确定哪些 GPU 任务可以运行在哪些节点。
2. **部署平台与存储**：部署 ClearML 自托管服务和 MinIO/S3。先确认数据、checkpoint、模型产物均持久化到共享对象存储，不依赖 Agent 本地临时目录。
3. **部署 Agent 与队列**：五台 CPU 机器接入 CPU 队列，三台 GPU 设备按 GPU 类别接入队列。为不同软件后端配置独立镜像和 Agent 环境。
4. **建立领域镜像**：先准备 `cpu-ml`、`pytorch-cuda`、`llm`、`mmocr`、`paddleocr` 镜像；只有确实需要时再增加 NeMo、ROCm 或 XPU 镜像。
5. **试点训练**：至少验证 CPU 表格模型、单 GPU 视觉模型、LLM 微调、OCR 训练、超参数搜索，以及任务取消/失败/重试、日志/指标、checkpoint 和模型下载。
6. **权限与备份验收**：验证登录、用户隔离、队列访问权限、对象存储权限、数据和模型备份恢复。ClearML 不同版本的权限能力可能不同，生产部署前应确认所需身份认证和 RBAC 是否包含在采用的版本中；必要时放在具备认证的内网网关后。
7. **分阶段迁移**：先迁移新建试验和少量数据集；对现有模型、数据集元信息、历史日志和 checkpoint 做兼容性评估。不要假定现有 `.pt` checkpoint 可直接被任意模型框架恢复。

建议验收标准：每台机器可从正确队列领取任务；资源不满足时任务留在队列或明确失败；两类以上容器镜像能运行；任务日志、指标、模型和数据版本可追溯；worker 重启后任务状态和已上传 checkpoint 不丢；非授权用户不能访问私有任务和模型。

## 已知限制与风险

- ClearML、Slurm、Kubeflow 都是通用平台或调度层，不会内置所有领域的训练配方；仍需选择和维护各模型库及训练代码。
- ClearML Agent 队列不是 HPC 调度器的完全等价物。对严格公平调度、配额、抢占、原子分配多机 GPU 的要求，应使用 Slurm 或 Kubernetes/Kueue 并验证对应功能。
- 多卡/多机训练要由 PyTorch DDP/FSDP、DeepSpeed、MPI 或领域框架实现；平台只负责任务执行和资源提供。
- 不同 GPU 厂商和运行时通常要求不同容器镜像。CUDA、ROCm、XPU 和 PaddlePaddle 依赖不能假设可以混装。
- 迁移训练任务不等于迁移训练语义。优化器状态、RNG 状态、数据加载位置和预处理格式必须由原训练代码及目标框架兼容处理。
- ClearML Server 的自托管部署依赖其服务组件和持久化配置；上线前需完成升级、备份、恢复、访问控制和容量测试。

## 项目链接

- ClearML: https://github.com/clearml/clearml
- ClearML Agent: https://github.com/clearml/clearml-agent
- ClearML Server: https://github.com/clearml/clearml-server
- Slurm: https://slurm.schedmd.com/
- Open OnDemand: https://openondemand.org/
- Apptainer: https://apptainer.org/
- Kubeflow: https://www.kubeflow.org/
- Kueue: https://kueue.sigs.k8s.io/
- MLflow: https://mlflow.org/
- Hugging Face Transformers: https://github.com/huggingface/transformers
- DeepSpeed: https://github.com/microsoft/DeepSpeed
- PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR
- MMOCR: https://github.com/open-mmlab/mmocr
- NVIDIA NeMo: https://github.com/NVIDIA/NeMo
