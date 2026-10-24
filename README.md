# 可视化模型训练平台

基于 **Docker + Python 3.12 + PyTorch（GPU 优先）** 的一站式可视化训练页面：

- 📦 上传 CSV 数据集，在线预览与列选择
- 🚀 页面配置超参（epochs / batch_size / lr / 网络结构 / 验证集比例）一键发起训练
- 📈 实时 Loss 曲线、验证指标曲线（Chart.js）、进度条
- 📜 实时训练日志，任务可排队、取消、删除
- 🏆 训练完成自动给出分类报告 / 回归指标（MSE、RMSE、MAE、R²）
- 🔮 加载 best.pt 在线单条预测，模型权重可下载
- 🔧 预留 GPU 接入：镜像内置 CUDA 12 构建 torch，宿主机装好驱动即自动启用 GPU
- 💾 支持MySQL数据库存储，使用已有MySQL服务器

无需任何登录/权限逻辑，开放访问。

## 快速开始

### 使用已有MySQL数据库（推荐）

1. **配置MySQL连接**
   ```bash
   cp .env.example .env
   # 编辑.env文件，设置正确的MySQL连接信息
   ```

2. **创建数据库（如果需要）**
   ```sql
   CREATE DATABASE model_training CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```

3. **启动应用**
   ```bash
   # GPU 版（推荐，需 NVIDIA 驱动 + nvidia-container-toolkit）
   docker compose up -d --build

   # GPU 尚未就绪时，先用 CPU 版把页面跑起来（镜像相同，仅去掉 gpus 声明）
   docker compose -f docker-compose.cpu.yml up -d --build
   ```

打开 http://localhost:8000 即可使用。

### 使用Docker内置MySQL（不推荐）

如果您想快速测试，可以使用Docker内置的MySQL：

```bash
# 1. 先启动MySQL
docker run -d --name mysql-temp -e MYSQL_ROOT_PASSWORD=password -e MYSQL_DATABASE=model_training -e MYSQL_USER=appuser -e MYSQL_PASSWORD=apppass -p 33061:3306 mysql:8.0

# 2. 启动应用
export DB_HOST=localhost
export DB_PORT=33061
export DB_USER=root
export DB_PASSWORD=password
export DB_NAME=model_training
docker compose up -d --build
```

建议流程：在左侧先点「＋ classification_demo」「＋ regression_demo」导入任意一个内置示例数据集（客户流失二分类 / 房价回归），点「开始训练」，右侧实时观看曲线与日志。

## GPU 环境接入（后续补充）

| 环节 | 说明 |
| --- | --- |
| 宿主机驱动 | 安装与本机匹配的 NVIDIA 驱动（容器内**不需要**装 CUDA Toolkit，torch 的 Linux wheel 已内置 CUDA 12 运行时依赖） |
| 容器运行时 | Linux 需安装 `nvidia-container-toolkit` 并 `systemctl restart docker`；Docker Desktop（Windows/WSL2）在设置中开启 GPU |
| 验证宿主能力 | `docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi` |
| 启动 | 直接使用 `docker-compose.yml`（已含 `gpus: all`） |
| 页面确认 | 顶栏徽章显示 GPU 型号即成功；每次训练的「设备」统计也会显示 GPU/CPU |

CPU→GPU 无需改一行代码或镜像：训练器启动时自动探测 `torch.cuda.is_available()`，无 GPU 时仅打印提示并回退 CPU。

## 数据库（MySQL，可选镜像存储）

采用**文件为主、数据库镜像**的双写设计：`./data/` 下的 job.json / log.txt / metrics.jsonl 始终是第一数据源，MySQL 仅镜像 `datasets` / `training_jobs` / `job_logs` 三张表；**数据库不可用时训练、上传、页面完全不受影响**（启动脚本等待最多 90 秒后跳过初始化）。

- 首次启动自动建表（`backend/app/db_init.py`）
- `USE_DATABASE=false` 可彻底关闭数据库路径（仅文件存储）
- 页面图表依赖 Chart.js CDN（浏览器需可访问公网），训练与数据存储全程本地完成
- 详细说明见根目录 `MYSQL_SETUP.md`、`USING_EXISTING_MYSQL.md`

## 目录结构

```
├── docker-compose.yml        # GPU 版（默认）
├── docker-compose.cpu.yml    # CPU 临时版（GPU 环境就绪前使用）
├── backend/
│   ├── Dockerfile            # python:3.12-slim + CUDA 构建 torch
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py           # FastAPI 路由 + 静态页托管
│   │   ├── jobs.py           # 任务管理：线程池、状态机、持久化、日志
│   │   ├── trainer.py        # CSV 分类/回归训练器（PyTorch）+ 在线预测
│   │   ├── database.py / models.py / db_utils.py / db_init.py  # MySQL 可选镜像
│   │   └── config.py
│   ├── static/index.html     # 可视化单页（无 Node 构建）
│   ├── sample_data/          # 内置示例数据集
│   └── scripts/generate_samples.py
└── data/                     # 运行时产物（持久化卷，已 gitignore）
    ├── datasets/             # 上传的 CSV + 元信息
    └── jobs/<job_id>/        # log.txt / metrics.jsonl / best.pt / summary.json
```

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/system` | 设备信息（GPU 型号 / torch 版本），页面徽章数据源 |
| POST | `/api/datasets` | 上传 CSV（multipart 字段名 `file`） |
| GET | `/api/datasets/{id}` | 元信息 + 前 N 行预览 + 列类型 |
| POST | `/api/train` | 发起训练 `{dataset_id, target, task, epochs, batch_size, lr, hidden_layers, hidden_size, test_split, seed}` |
| GET | `/api/jobs` | 任务列表（含各任务逐 epoch 指标） |
| GET | `/api/jobs/{id}` | 任务详情：状态、进度、指标序列、summary |
| GET | `/api/jobs/{id}/logs` | 日志增量拉取（`?offset=` 行号） |
| POST | `/api/jobs/{id}/cancel` | 取消（当前批次结束后停止） |
| DELETE | `/api/jobs/{id}` | 删除任务及产物 |
| GET | `/api/jobs/{id}/model` | 下载 best.pt |
| POST | `/api/jobs/{id}/predict` | `{ "row": {...单行字段...} }` 用最佳模型预测 |

## 扩展新的训练类型

`backend/app/trainer.py` 是内置的「表格数据 MLP 分类/回归」训练器。要接入自定义训练（如图像、NLP、任意脚本）：

1. 在 `trainer.py` 中新增 `run_<name>(... 同签名 ...)` 函数，通过 `log_fn` 打日志、`_append_jsonl` 写逐 epoch 指标、尊重 `cancel_event`；
2. 在 `main.py` 的 `/api/train` 中按任务类型路由到新函数；
3. 前端参数区按需增删字段（`index.html` 纯原生 JS，无构建步骤）。

## 注意事项

- 数据与模型产物保存在宿主机 `./data/` 目录，容器重建不丢失。
- 任务由进程内线程池执行（并发 2），适合单机训练场景；多机/多队列可在 `jobs.py` 基础上替换为 Celery/Redis。
- 页面图表依赖 Chart.js CDN，浏览器需可访问公网；日志与训练完全本地运行。
- 镜像构建首次约 2~4 GB 下载（torch CUDA wheel），后续走 Docker 缓存。
