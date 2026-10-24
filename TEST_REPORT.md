# 测试报告

## Git基准点

- **Commit Hash**: 3bb4a96
- **提交信息**: Merge emdash/hot-boxes-clean-y9u6p: dataset compatibility fixes and AGENTS.md improvements
- **测试日期**: 2026-10-24
- **分支**: master（自 emdash/hot-boxes-clean-y9u6p 合并，该分支本地功能测试 32/32；容器化验证基于本合并提交）

## 本次变更测试结果（2026-10-24）

**变更范围**：修复数据集新旧两套存储格式不兼容导致的链路断裂（4 个业务文件）

- `backend/app/main.py`：`_load_meta_or_404` 支持回退读取 `metadata/{id}.json`；`_save_dataset_from_frame` 统一走 `DatasetManager` 保存；补齐缺失的 `safe_filename`、`get_job_logs` 导入；清理未使用导入
- `backend/app/dataset_manager.py`：`get_dataset` 回退旧扁平格式；新增 `_resolve_csv` 双布局查找；`list_datasets` 合并两种元数据源并去重；`delete_dataset` 同时清理旧扁平文件
- `backend/app/jobs.py`：训练前按新旧两种布局解析数据集 CSV 路径
- `backend/app/config.py`：数据目录默认值改为容器安全（容器内 `/app/data`，本地回退项目根 `data/`）

**测试执行结果**：
- 总测试用例：32 个
- 通过：32 个 (100%)
- 失败：0 个
- 错误：0 个
- 跳过：0 个

**关键模块测试**：
- 系统接口（health/system/samples）：3/3
- 数据集管理（导入/列表/详情/统计/删除）：9/9
- 训练任务（发起/监控/日志/极限参数）：6/6
- 预测与模型下载：3/3
- 异常与安全（非法 ID/路径穿越/非法参数）：8/8
- 兼容性回归（旧扁平格式数据集可查可训练）：2/2
- 清理回归（删除任务/数据集/列表恢复基线）：3/3

**本次修复的缺陷**（均先复现失败场景再修复验证通过）：

| # | 缺陷 | 复现方式 | 修复验证 |
|---|------|---------|---------|
| 1 | 示例导入的数据集在列表不可见、详情 404（新旧元数据格式不兼容） | `POST /api/datasets/sample/...` 后 `GET /api/datasets/{id}` 返回 404 | 导入后详情 200、列表 +1 ✓ |
| 2 | 上传/新格式数据集无法发起训练（`_load_meta_or_404` 仅读旧扁平格式） | 对列表中数据集 `POST /api/train` 返回 404 | 返回 job_id 且训练完成 ✓ |
| 3 | `safe_filename` 在 main.py 使用但未导入（`/api/datasets/validate` 端点潜在 NameError） | 代码走查发现 | 导入补齐 ✓ |
| 4 | `get_job_logs` 在日志端点使用但未导入（数据库日志路径静默失效） | 代码走查发现 | 导入补齐 ✓ |
| 5 | config.py `DATA_DIR` 默认 `../data` 会破坏容器内路径（回归） | 代码走查：容器内解析为 `/data` 而非挂载的 `/app/data` | 按目录存在性自动选择 ✓ |

**测试用例执行命令**（临时脚本，测试完成后已删除）：
```bash
cd backend && python3 test_comprehensive.py   # 32/32 通过
```

## 容器化部署验证（2026-10-24）

**验证环境**：master 合并提交 `3bb4a96`；Apple Silicon 宿主机（无 NVIDIA GPU）→ 按 README 规范使用 `docker-compose.cpu.yml`（镜像相同，仅去掉 gpus 声明）；Docker Hub 网络已恢复。

**验证结果**：
- ✅ 镜像构建成功：`docker compose -f docker-compose.cpu.yml up --build -d`（含 torch CUDA wheel 安装，容器内 Python 3.12 + torch 2.14.1+cu130，宿主有 GPU 时自动启用）
- ✅ 容器启动正常：MySQL 不可达时按设计等待后跳过初始化，文件存储为主数据源
- ✅ 核心链路端到端通过：示例导入 → 详情预览 → 训练（2 epochs，completed，准确率 0.75）→ 单条预测（返回预测 + 概率）
- ✅ **数据持久化挂载验证**：容器内写入的数据集元数据与任务产物（best.pt / job.json / metrics.jsonl 等）正确落盘宿主机 `./data/`，证明 `config.py` 容器路径修复（633bbcb）在容器环境行为正确
- ✅ 验证后清理：测试数据集/任务已通过 API 删除，容器已停止

**已知噪音**：容器日志中 `/health` 404 来自外部监控探测（compose 与 Dockerfile 均未配置 healthcheck；应用路由为 `/api/health`），对功能无影响。

## 测试覆盖范围

- ✅ 正常场景：健康检查、系统信息、示例导入全链路、训练监控、预测、模型下载
- ✅ 边界场景：预览行数参数、极限训练参数（1 epoch/最大 batch/lr 上限/0 隐藏层）、epochs=0 校验
- ✅ 异常场景：不存在资源 404、目标列不存在 400、非法任务类型 422、缺失字段 422
- ✅ 权限与安全：路径穿越 ID 拒绝、非法示例文件名拒绝
- ✅ 兼容性场景：修复前旧扁平格式数据集（07:57-08:09 期间创建）仍可查询、可训练
- ✅ 回归场景：删除任务/数据集后列表恢复基线、旧格式数据集回归训练

## 已知问题与限制

1. **Docker 验证已完成（2026-10-24）**：早前因无法连接 Docker Hub 构建失败；网络恢复后已完成容器化验证，全链路通过且数据挂载正确（见"容器化部署验证"章节）。GPU 探测路径（CUDA 分支）因本机无 NVIDIA GPU 未验证，需在 GPU 宿主机复验
2. **`/api/datasets/{id}/process` 与 `/api/datasets/{id}/profile` 端点仍仅支持新格式数据集**（读取 `raw/`、`processed/` 固定路径），对旧扁平格式数据集返回 500；核心链路（导入/列表/详情/训练/预测）已双格式兼容
3. **MySQL 数据库路径未测试**：本地无可用数据库连接（`USE_DATABASE=false` 运行），DB 镜像逻辑仅做代码走查
4. **远端仓库待同步（需 push 授权）**：`origin/hot-boxes-temp` 仍含历史临时测试脚本提交（本地已剔除并删除分支）；`origin/master` 落后于本地 master。AGENTS.md 禁止 `git push`，如需同步请明确授权
5. **分支 `emdash/hot-boxes-clean-y9u6p` 存在并行开发中的新模块**（`fd40a1e`：storage 抽象 + coordination 平面，尚未接线到 main/jobs）：未纳入 master 合并范围，待其功能完成并测试后再合并

## 重测触发条件

- ✅ 修改了 `backend/app/` 下的非测试代码
- ✅ 修改了数据库模型和操作
- ✅ 修改了训练器、任务管理业务逻辑
- ✅ 修改了 API 接口
- ✅ 修改了核心配置（影响业务逻辑的配置）

## 下次测试建议

1. 在 NVIDIA GPU 宿主机上验证 GPU 版 compose（`gpus` 声明与 CUDA 自动启用路径）
2. 补齐 `/api/datasets/{id}/process`、`/profile` 端点的旧格式兼容（或明确限制新格式使用）
3. 配置可用 MySQL 后测试数据库镜像路径（datasets/training_jobs/job_logs 三表双写）
4. 补充前端页面交互测试（Chart.js 曲线、日志增量拉取、进度条）
5. 将临时功能测试固化为正式测试套件（建议 pytest + httpx，纳入 requirements-dev）
6. 上传接口实测 multipart 上传流程（本次因无现成 CSV 上传样例，通过示例导入覆盖等价路径）
7. `fd40a1e` 的 storage/coordination 新模块接线后，补充多机架构（嵌入式模式/Redis/MinIO）功能测试
