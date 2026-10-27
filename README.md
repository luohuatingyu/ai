# 可视化模型训练平台

基于 **Docker + Python 3.12 + PyTorch（GPU 优先）** 的一站式可视化训练页面：

- 📦 上传 CSV 数据集，在线预览与列选择
- 🚀 页面配置超参（epochs / batch_size / lr / 网络结构 / 验证集比例）一键发起训练
- 📈 实时 Loss 曲线、验证指标曲线（Chart.js）、进度条
- 📜 实时训练日志，任务可排队、暂停（保存断点）、恢复（跨卡/跨机接续）、取消、删除
- 🖥 计算资源管理：平台通过 SSH 远程执行单文件部署脚本、自动检测 CPU/GPU、手动选择计算槽加入资源池，并可远程启动/停止/重启 worker
- 🏆 支持分类、回归、KMeans 聚类、单序列预测、LLM 从零预训练、全参数 SFT、DPO 与 PPO/RLHF
- 🔮 表格模型在线单条预测；LLM 可输入 prompt 生成文本，模型包可下载
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
   # 未指定硬件时默认使用 CPU 版
   bash scripts/start-platform.sh

   # 选择 GPU 版（需 NVIDIA 驱动 + nvidia-container-toolkit）
   PLATFORM_MODE=gpu bash scripts/start-platform.sh

   # 开启远程管理时单独启用 Docker socket 覆盖
   PLATFORM_MODE=gpu REMOTE_MANAGEMENT=true bash scripts/start-platform.sh
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
PLATFORM_MODE=gpu bash scripts/start-platform.sh
```

建议流程：在左侧先点「＋ classification_demo」「＋ regression_demo」导入任意一个内置示例数据集（客户流失二分类 / 房价回归），选择分类或回归后点「开始训练」，右侧实时观看曲线与日志。

训练任务类型：分类和回归需要选择目标列；KMeans 聚类不需要目标列，可设置簇数；单序列时间序列预测需要选择时间列、数值目标列、历史窗口长度和预测步数，按时间顺序留出验证数据。

### LLM 训练

训练配置中可选择本地数据集或 Hugging Face Hub 数据集，设置配置名、split、文本/偏好列和最大样本数。支持从随机初始化 tokenizer 与 decoder-only GPT-2 开始预训练、对 Hub CausalLM 做全参数 SFT、使用 `prompt/chosen/rejected` 偏好对做 DPO，以及配置标量奖励模型执行 PPO 式 RLHF。LLM 模型加载和数据集加载都关闭 `trust_remote_code`；Hub 模型与数据集需为可公开访问的仓库。

LLM 任务要求单张 GPU，并将 tokenizer 与模型权重保存为 `best_model.zip`；完成后可在「预测」页输入 prompt 生成文本。PPO 需要 `num_labels=1` 的奖励模型。首版仅支持 decoder-only CausalLM，不做单任务多卡并行；请按 GPU 显存选择模型、批大小与序列长度。可上传 CSV、JSON 或 JSONL/NDJSON 文本与偏好数据。

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
  - **多机多卡**：所有节点连同一 Redis + S3 对象存储（SeaweedFS / 阿里云 OSS / 腾讯云 COS 等），外接服务器的计算槽经页面手动启用后才参与调度。

## 多机多卡部署

### 1. 主节点（frontend + API + Redis + SeaweedFS + worker）

```bash
bash scripts/start-platform.sh   # 默认 CPU；设置 PLATFORM_MODE=gpu 选择 GPU 版
```

打开 http://localhost:8000：页面由 frontend 容器托管，API 经同源代理转发。

### 2. 追加计算服务器（CPU / GPU 均可）

外接服务器由平台直接通过 SSH 管理。目标服务器不需要检出或接收本项目源码，也不需要访问镜像仓库；平台通过 Docker socket 导出本机 worker 镜像，经 SSH 流式传输到目标机。开始前准备：

1. 无需手工准备 `known_hosts`。平台首次连接目标主机时会自动信任并持久化其主机密钥；之后检测到密钥变化会拒绝连接。首次连接采用自动信任，需确保目标主机地址正确。
2. 在项目 `.env` 中设置平台默认配置：
   ```dotenv
   WORKER_IMAGE=mtp-worker:local
   REDIS_URL=redis://目标服务器可访问的Redis主机:6379/0
   S3_ENDPOINT=目标服务器可访问的S3主机:8333
   WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY=使用Fernet生成的稳定密钥
   ```
   `WORKER_IMAGE` 是平台 Docker 中 worker 镜像的本地标签，默认镜像由 Compose 构建；无需配置仓库地址，服务器页面不单独配置镜像。首次部署时，平台通过 Docker socket 导出镜像，再通过 SSH 流式传输到目标机执行 `docker load`；目标机不需要访问镜像仓库或执行 `docker pull`。目标机必须能访问同一 Redis 和 S3 存储。远程服务器和 SSH 凭证目录保存到 MySQL；远程管理 Compose 默认启用 MySQL，需在 `.env` 配置可从 trainer 容器访问的 `DB_HOST`、`DB_USER`、`DB_PASSWORD`、`DB_NAME` 等连接参数。`WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY` 用于页面凭证加密，可用 `python3.12 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'` 生成并长期保管。更换此密钥后，MySQL 中已有的页面凭证无法解密，需要重新保存。在「SSH 凭证管理」中添加用户名 + 密码、用户名 + 密钥或用户名 + 密钥 + 密码凭证。第三种方式先尝试密钥认证，失败后再尝试密码。私钥、登录密码和可选私钥口令都由页面提交并加密保存到 MySQL；登录凭证无需写入 `.env`，页面不会回显已保存的秘密。添加服务器时选择凭证；SSH 用户名留空时使用凭证用户名，填写后则覆盖该服务器的凭证用户名。OpenSSH 会按密钥内容识别其支持的 Ed25519、ECDSA、RSA OpenSSH/PEM 格式。建议将 `.env` 权限限制为 `600`。服务器没有专属 Redis URL 或 S3 地址时，分别使用 `.env` 中的 `REDIS_URL` 和 `S3_ENDPOINT` 覆盖远程 worker 配置；`WORKER_REMOTE_REDIS_URL` 和 `WORKER_REMOTE_S3_ENDPOINT` 可单独设置平台级默认值。服务器卡片的「生效配置」可查看平台当前解析出的镜像、Redis/S3 地址、来源和目标状态，认证信息会脱敏；查看结果是待下发配置。修改服务器配置时，平台会覆盖目标机配置文件并重建 worker 容器，使新配置立即进入容器；部署操作只同步 worker 代码，启动/重启操作单独控制容器。`WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY` 是服务端加密密钥，不是目标 SSH 登录凭证。
3. 构建平台 worker 镜像。GPU 平台使用默认 Compose 文件；纯 CPU 平台使用 CPU 文件：
   ```bash
   docker compose build worker

   # CPU 版
   docker compose -f docker-compose.cpu.yml build worker
   ```
   远程管理覆盖文件会将 `/var/run/docker.sock` 挂载到 trainer，以便导出平台本地镜像。Docker socket 可控制宿主 Docker，权限接近宿主 root；应限制平台管理 API 的网络访问。镜像只在首次安装时通过 SSH 传输，传输耗时取决于镜像大小和网络带宽；后续部署只同步 worker 代码。
4. 使用远程管理 Compose 覆盖文件启动平台。GPU / CPU 两种启动方式如下：
   ```bash
   PLATFORM_MODE=gpu REMOTE_MANAGEMENT=true bash scripts/start-platform.sh

   # CPU 版
   PLATFORM_MODE=cpu REMOTE_MANAGEMENT=true bash scripts/start-platform.sh
   ```
5. 打开主页面「添加服务器」，在「SSH 凭证管理」中配置全平台共用的密钥或密码，再填写目标主机、SSH 用户/端口、服务器标识及 CPU/GPU 类型。平台会执行安装并启动 worker；资源心跳出现后，CPU/GPU 槽默认处于待选择状态，手动点击「加入资源池」才会接收训练任务。服务器卡片还可通过 SSH 启动、停止或重启 worker。

页面配置私钥时，平台仅将加密密文保存到 MySQL；执行 SSH 时在平台临时创建权限为 `600` 的密钥文件，操作完成后立即删除。远程操作轮询状态仍保存在 Redis。SSH 用户需要有 Docker 权限。目标服务器需要运行 Docker Engine。GPU 目标机还需要 NVIDIA 驱动和 NVIDIA Container Toolkit。`stop` 和 `restart` 会给予 worker 最多 45 秒优雅停机时间，运行中的任务会保存断点，再由其它在线计算槽接续。

私钥格式和认证算法由平台容器内的 OpenSSH 自动协商；旧服务器若只支持已禁用的弱算法（例如 RSA/SHA-1 `ssh-rsa` 签名），连接会失败，不会自动降低安全设置。

远程操作配置以单文件脚本通过 SSH stdin 发送；脚本会把 worker 运行所需配置以权限 `600` 保存到目标机用户的 `~/.config/mtp-worker/`，不会把平台源码复制到目标服务器。服务器记录不保存 SSH 密钥或 S3 密钥。平台 API 尚未提供身份认证，远程管理 API 默认启用且不使用独立令牌；部署时必须限制平台管理页面的网络访问，只允许可信网络访问。

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
| 启动 | 使用 `PLATFORM_MODE=gpu bash scripts/start-platform.sh` |
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
├── docker-compose.remote.yml # 远程 SSH 管理启用覆盖配置
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
│   ├── static/index.html     # 可视化单页（服务器/资源池面板 + 暂停/恢复）
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
| GET | `/api/jobs/{id}` | 任务详情：状态、进度、指标序列、summary、资源执行轨迹 |
| GET | `/api/jobs/{id}/logs` | 日志增量拉取（`?offset=` 行号） |
| POST | `/api/jobs/{id}/cancel` | 取消（当前批次结束后停止） |
| POST | `/api/jobs/{id}/pause` | 暂停（保存断点后进入 paused，可跨机/卡恢复） |
| POST | `/api/jobs/{id}/resume` | 恢复（重新排队，任意可用卡从 checkpoint 接续） |
| POST | `/api/jobs/{id}/retry` | 失败任务重试；`{ "worker_id": null }` 自动分配，指定 ID 时固定到兼容槽位 |
| DELETE | `/api/jobs/{id}` | 删除任务及产物 |
| GET | `/api/jobs/{id}/model` | 下载 best.pt |
| POST | `/api/jobs/{id}/predict` | `{ "row": {...单行字段...} }` 用最佳模型预测 |
| GET | `/api/workers` | 服务器、硬件能力、心跳与槽位启用状态 + 队列深度 |
| PATCH | `/api/workers/{worker_id}` | 设置外接计算槽是否加入资源池 `{ "enabled": true/false }` |

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
