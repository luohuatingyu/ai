# 可视化模型训练平台

基于 **Docker + Python 3.12 + PyTorch（GPU 优先）** 的一站式可视化训练页面：

- 📦 上传 CSV 数据集，在线预览与列选择
- 🚀 页面配置超参（epochs / batch_size / lr / 网络结构 / 验证集比例）一键发起训练
- 📈 实时 Loss 曲线、验证指标曲线（Chart.js）、进度条
- 📜 实时训练日志，任务可排队、暂停（保存断点）、恢复（跨卡/跨机接续）、取消、删除
- 🖥 计算槽面板：多机多卡一卡一进程，卡状态（空闲/训练/降级/恢复中/离线）实时可见
- 🏆 训练完成自动给出分类报告 / 回归指标（MSE、RMSE、MAE、R²）
- 🔮 加载 best.pt 在线单条预测，模型权重可下载
- ♻️ 环境性故障（CUDA/OOM/网络瞬断）自动从断点续训（`JOB_MAX_AUTO_RETRY` 次内），单卡故障不影响整体任务完成
- 🔧 预留 GPU 接入：镜像内置 CUDA 12 构建 torch，宿主机装好驱动即自动启用 GPU
- 💾 支持MySQL数据库存储（可选镜像），使用已有MySQL服务器

无需任何登录/权限逻辑，开放访问。

## 快速开始

### 使用已有MySQL数据库（明文密码）

1. **配置MySQL连接**
   ```bash
   cp .env.example .env
   # 编辑.env文件，设置明文密码
   # DB_PASSWORD=your_password
   ```

2. **如果不知道密码，重置MySQL**
   ```bash
   # 手动重置MySQL数据库
   mysql -u root -p -e "DROP DATABASE IF EXISTS model_training; CREATE DATABASE model_training CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
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

## 架构（四平面 + 无状态计算单元）

平台从"单进程线程池训练"升级为**控制 / 协调 / 状态 / 计算**四平面，GPU 只是可随时释放的纯计算单元：

```
前端入口  frontend (Node)        静态托管页面 + /api 同源反向代理（与后端 Python 按语言分离）
控制平面  trainer (FastAPI)      提交/暂停/恢复/取消任务，任务面板，watchdog
协调平面  redis                  任务队列 + 租约(SET NX+TTL) + worker 心跳注册
状态平面  seaweedfs (S3)         数据集 / checkpoint / 产物；job.json 权威，MySQL 仅镜像
          （可替换为阿里云 OSS / 腾讯云 COS / AWS S3 等任意 S3 兼容服务）
计算平面  worker (一卡一进程)    supervisor 按 GPU 数拉起子进程并守护恢复
```

前端（原生 HTML/JS/CSS 单页）与后端（Python/FastAPI）按语言拆分为两个容器：
- **frontend 容器**：Node 零依赖静态服务（无 npm 依赖、无构建链），页面与 `/api` 同源（8000 端口），`/api/*` 原样反代到 trainer——浏览器无需 CORS；
- **trainer 容器**：仅内网服务 API；本地单进程开发时后端仍自带 `/static` 兜底，不起前端容器也能看页面。

核心语义：

- **checkpoint 是进度唯一权威**：完整 trainer-state（模型+优化器+RNG+epoch+预处理器）CPU 化保存；服务器 A 训练后释放，服务器 B 用不同型号显卡从 checkpoint 接续（跨设备语义连续、不承诺逐位一致）。
- **租约 fencing**：任务执行前抢租约（单写者）；心跳即 TTL，进程崩溃 key 自动过期，watchdog 只看 key 是否存在（不依赖时钟同步），把迷失任务自动重派（不消耗重试额度）。
- **两级自愈**：① worker 内软探测（微型张量运算）失败 → `degraded` 停止领取新任务，恢复后自动摘除；② 子进程死亡 → supervisor 用 `--probe-gpu` 探测该卡，**卡恢复后才重启子进程**（`recovering` → 重启入列）。
- **失败分类**：数据集缺失/超参非法等业务性错误直接 FAILED；CUDA/OOM/网络瞬断等环境性错误自动重试（上限 `JOB_MAX_AUTO_RETRY`，默认 5，.env 可配），每次重试都从最近 checkpoint 续训。
- **两种部署形态**：
  - **单机零依赖**（默认）：`STORAGE_BACKEND=local`、`REDIS_URL` 留空 —— API 进程内嵌 worker 线程，协调面为进程内实现，行为与多机模式一致；
  - **多机多卡**：所有节点连同一 Redis + S3 对象存储（SeaweedFS / 阿里云 OSS / 腾讯云 COS 等），任意服务器加卡即自动入列领取任务。

## 多机多卡部署

### 1. 主节点（frontend + API + Redis + SeaweedFS + worker）

```bash
docker compose up -d --build     # GPU 版；纯 CPU 环境用 docker-compose.cpu.yml
```

打开 http://localhost:8000：页面由 frontend 容器托管，API 经同源代理转发。

### 2. 追加 GPU 服务器（可不同显卡型号）

```bash
# 在新服务器上克隆同一仓库，配置指向主节点：
cat > .env <<'EOF'
REDIS_URL=redis://主节点IP:6379/0
STORAGE_BACKEND=s3
S3_ENDPOINT=主节点IP:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=mtp-artifacts
S3_SECURE=false
EOF
docker compose up -d --build worker
```

新服务器的每一张卡会注册为独立计算槽（页面「计算槽」面板可见），空闲即领任务；该机掉卡/驱动崩溃只影响其上的任务，其余机器继续训练。

### 3. 迁移历史数据（老版本升级）

```bash
cd backend
STORAGE_BACKEND=s3 S3_ENDPOINT=主节点IP:9000 S3_ACCESS_KEY=... S3_SECRET_KEY=... \
    python scripts/migrate_to_object_storage.py
```

把本地遗留数据集与任务产物（含版本化 checkpoint）幂等迁入对象存储，老任务即可跨机续训。

### 4. Worker 进程模型（也可在裸机 GPU 服务器直接跑）

```bash
cd backend
REDIS_URL=redis://主节点IP:6379/0 STORAGE_BACKEND=s3 S3_ENDPOINT=主节点IP:9000 python -m app.worker
# supervisor：探测可见 GPU 数 N → N 个"一卡一子进程"+WORKER_CPU_SLOTS 个 CPU 槽，掉卡自动探测恢复
python -m app.worker --slot A-gpu0   # 单卡子进程（CUDA_VISIBLE_DEVICES 由 supervisor 注入）
python -m app.worker --probe-gpu     # 一次性卡健康探测（退出码 0=健康）
```

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
├── docker-compose.yml        # 多机架构 GPU 版（frontend + trainer + seaweedfs + worker，Redis/MySQL 走 .env 外部容器）
├── docker-compose.cpu.yml    # 多机架构 CPU 版（无 gpus 声明，worker 走 CPU 槽）
├── frontend/
│   ├── Dockerfile            # node:20-alpine（基础镜像经 .env 可配国内源）
│   └── server.js             # 零依赖静态服务 + /api 反向代理（容器内运行）
├── backend/
│   ├── Dockerfile            # python:3.12-slim + CUDA 构建 torch
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py           # FastAPI 路由 + 静态页托管（本地开发兜底）
│   │   ├── jobs.py           # 任务登记/控制/watchdog（状态平面版）
│   │   ├── jobstore.py       # job.json CRUD / metrics 回滚 / 日志（权威记录层）
│   │   ├── worker.py         # 无状态 TrainingWorker + 一卡一进程 WorkerSupervisor
│   │   ├── coordination.py   # 队列/租约/worker 注册（Redis 与嵌入式双实现）
│   │   ├── storage.py        # ArtifactStore（local / S3 兼容对象存储）+ checkpoint 版本化
│   │   ├── trainer.py        # 可续训训练器（PyTorch）+ 在线预测
│   │   ├── database.py / models.py / db_utils.py / db_init.py  # MySQL 可选镜像
│   │   └── config.py
│   ├── static/index.html     # 可视化单页（计算槽面板 + 暂停/恢复）
│   ├── sample_data/          # 内置示例数据集
│   ├── scripts/generate_samples.py
│   └── scripts/migrate_to_object_storage.py  # 老数据迁入对象存储（幂等）
└── data/                     # 运行时产物（持久化卷，已 gitignore）
    ├── datasets/             # 上传的 CSV + 元信息
    └── jobs/<job_id>/        # log.txt / metrics.jsonl / best.pt / checkpoints/
```

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/system` | 设备信息（GPU 型号 / torch 版本），页面徽章数据源 |
| POST | `/api/datasets` | 上传 CSV（multipart 字段名 `file`） |
| GET | `/api/datasets/{id}` | 元信息 + 前 N 行预览 + 列类型 |
| POST | `/api/train` | 发起训练 `{dataset_id, target, task, epochs, batch_size, lr, hidden_layers, hidden_size, test_split, seed, require_gpu, min_vram_mb}` |
| GET | `/api/jobs` | 任务列表（含各任务逐 epoch 指标） |
| GET | `/api/jobs/{id}` | 任务详情：状态、进度、指标序列、summary、segments（跨槽续训分段） |
| GET | `/api/jobs/{id}/logs` | 日志增量拉取（`?offset=` 行号） |
| POST | `/api/jobs/{id}/cancel` | 取消（当前批次结束后停止） |
| POST | `/api/jobs/{id}/pause` | 暂停（保存断点后进入 paused，可跨机/卡恢复） |
| POST | `/api/jobs/{id}/resume` | 恢复（重新排队，任意可用卡从 checkpoint 接续） |
| DELETE | `/api/jobs/{id}` | 删除任务及产物 |
| GET | `/api/jobs/{id}/model` | 下载 best.pt |
| POST | `/api/jobs/{id}/predict` | `{ "row": {...单行字段...} }` 用最佳模型预测 |
| GET | `/api/workers` | 计算槽列表（多机多卡，卡级状态/当前任务/心跳）+ 队列深度 |

## 扩展新的训练类型

`backend/app/trainer.py` 是内置的「表格数据 MLP 分类/回归」训练器。要接入自定义训练（如图像、NLP、任意脚本）：

1. 在 `trainer.py` 中新增 `run_<name>(... 同签名 ...)` 函数，通过 `log_fn` 打日志、`_append_jsonl` 写逐 epoch 指标、尊重 `cancel_event`；
2. 在 `main.py` 的 `/api/train` 中按任务类型路由到新函数；
3. 前端参数区按需增删字段（`index.html` 纯原生 JS，无构建步骤）。

## 注意事项

- 数据与模型产物保存在宿主机 `./data/` 目录（本地模式）或 S3 对象存储（多机模式：SeaweedFS 卷 / 阿里云 OSS / 腾讯云 COS），容器重建不丢失。
- 单机模式任务由 API 进程内嵌 worker 线程执行；多机模式由各服务器的 worker 服务（一卡一进程）领取，可混合部署。
- 页面图表依赖 Chart.js CDN，浏览器需可访问公网；日志与训练数据全程本地/内网运行。
- 镜像构建首次约 2~4 GB 下载（torch CUDA wheel），后续走 Docker 缓存。
- 跨设备续训为语义连续（模型权重/优化器动量/RNG 状态完整恢复），不承诺逐位一致；同卡同 seed 复跑可复现。
