# 测试报告

## Git 基准点

- **Commit Hash**: 633bbcb
- **提交信息**: Fix dataset storage format compatibility between DatasetManager and legacy paths
- **测试日期**: 2026-10-24
- **分支**: emdash/hot-boxes-clean-y9u6p

## 本次变更测试结果（2026-10-24）

**变更范围**：修复数据集新旧两套存储格式不兼容导致的核心链路缺陷

1. **缺陷 A（导入示例后不可见）**：`/api/datasets/sample/{name}` 导入走旧扁平格式（`{id}.csv` + `{id}.meta.json`），
   而 `DatasetManager` 的列表/详情只读新结构（`metadata/{id}.json` + `processed/{id}.csv`），
   导致 README 推荐的「导入内置示例→开始训练」主流程断裂（列表不可见、详情 404）。
2. **缺陷 B（上传数据集无法训练）**：`/api/train` 的元信息检查只读旧扁平 `{id}.meta.json`，
   `jobs.py` 训练时只读旧扁平 `{id}.csv`，导致通过上传（新结构保存）的数据集发起训练返回 404。
3. **缺陷 C（隐藏 NameError）**：`main.py` 使用了未导入的 `safe_filename`（validate 端点会崩溃）、
   `get_job_logs`（数据库日志分支静默失效）。
4. **回归修复**：`config.py` 的 `DATA_DIR` 默认值 `../data` 在容器内解析为 `/data`，
   会绕过 docker-compose 的 `./data:/app/data` 挂载导致数据不持久化，改为容器内 `/app/data`、本地回退项目根 `data/`。

**修复方式**（兼容双格式，最小侵入）：
- `main.py`：`_load_meta_or_404` 旧格式优先、新结构兜底；`_save_dataset_from_frame` 统一走 `DatasetManager.save_dataset`；补齐缺失导入、清理未使用导入（numpy/shutil/Depends/uuid）
- `dataset_manager.py`：`get_dataset` 新结构优先、旧扁平兜底；新增 `_resolve_csv` 双布局查找；`list_datasets` 合并两种元数据源并按 ID 去重；`delete_dataset` 同步清理两种格式文件
- `jobs.py`：训练前按新旧布局解析数据集 CSV 路径
- `config.py`：容器安全的数据目录解析（环境变量 > 容器 `/app/data` > 本地项目根 `data/`）

**测试执行结果**（命令：`cd backend && python3 test_comprehensive.py`，服务以 `USE_DATABASE=false python3 -m uvicorn app.main:app` 本地运行）：

- 总测试用例：32 个
- 通过：32 个 (100%)
- 失败：0 个
- 错误：0 个
- 跳过：0 个

**关键模块测试**：
- 系统信息 API（/api/health、/api/system）：4/4
- 数据集管理（导入/列表/详情/统计/删除）：9/9
- 训练任务链路（发起/监控/日志/预测/模型下载）：5/5
- 边界场景（预览行数、极限参数、epochs=0 校验）：3/3
- 异常与安全场景（非法 ID、路径穿越、非法参数、目标列校验）：8/8
- 兼容性回归（旧扁平格式数据集可查询/可训练）：2/2
- 任务与数据集删除回归：5/5

**缺陷修复验证**（修复前失败 → 修复后通过）：
- 导入示例 → 列表可见、详情 200（修复前 404）
- 上传格式数据集 → 发起训练返回 job_id 并成功完成（修复前 404）

## 测试覆盖范围

已覆盖：
- ✅ 系统信息、健康检查
- ✅ 示例数据集导入全链路（导入 → 列表 → 详情 → 统计）
- ✅ 训练全链路（发起 → 轮询监控 → 日志 → 单条预测 → 模型下载）
- ✅ 边界（1 epoch / batch_size=4096 / lr=0.9 / 0 隐藏层 / rows=1 / epochs=0 拒绝）
- ✅ 异常（不存在资源 404、非法目标列 400、非法任务类型与缺字段 422）
- ✅ 安全（路径穿越 ID、路径穿越示例文件名均被拒绝）
- ✅ 兼容性（旧扁平格式数据集详情/训练回归通过）
- ✅ 删除任务/数据集及资源计数恢复基线

未覆盖（后续建议）：
- ❌ CSV 上传端点（`POST /api/datasets` multipart 上传）
- ❌ 回归任务训练（本次仅验证分类链路）
- ❌ 任务取消（`POST /api/jobs/{id}/cancel`）
- ❌ 数据库镜像路径（本次 `USE_DATABASE=false`，MySQL 不可达）
- ❌ 前端页面交互（`static/index.html`）
- ❌ Docker 容器化部署验证

## 重测触发条件

见 AGENTS.md「测试报告管理规则」。简述：修改 `backend/app/` 业务代码、数据库模型/操作、
训练逻辑、API 接口或影响业务逻辑的核心配置后，必须重新执行完整测试并更新本报告；
仅修改文档/前端/构建配置（不影响业务逻辑）无需重测。

检查命令：
```bash
BASELINE=$(grep "Commit Hash" TEST_REPORT.md | head -1 | awk '{print $4}')
git diff $BASELINE HEAD -- backend/app/
```

## 已知问题与限制

1. **Docker 部署未验证（环境受阻）**：本机无法连接 Docker Hub（`auth.docker.io` i/o timeout），
   `docker compose up --build -d` 在拉取 `python:3.12-slim` 基础镜像阶段失败。
   建议在有 Docker Hub 网络连通性的环境执行 `docker compose up --build -d` 完成容器化验证。
2. **本地测试环境差异**：本地直跑使用宿主机 Python 3.9.6（容器为 Python 3.12）；
   代码使用 `from __future__ import annotations` 且不依赖 3.10+ 语法，已通过 3.9 解释器全量测试，
   理论上 3.12 兼容，但仍建议容器化验证后确认。
3. **预测宽容设计**：`predict_row` 对缺失/非法字段以训练期中位数/众数填充后返回预测（HTTP 200），
   属预期设计（见 `trainer.py::_featurize_row`），前端用户若输入错误字段会得到基于默认值的预测而非报错。
4. **任务删除时序**：排队/运行中的任务拒绝删除（HTTP 400），为状态机保护；前端删除前应等待终态或先取消。
5. **process/profile 端点对旧扁平数据集未做回退**（读取 `raw/`、`processed/` 固定路径），
   旧格式数据集调用数据预处理端点会失败；核心链路（列表/详情/训练）不受影响。

## 下次测试建议

1. 补充 CSV multipart 上传用例（合法文件、空文件、超大文件、非 CSV 后缀、恶意文件名）
2. 补充回归任务链路（regression_demo → 训练 → R²/RMSE/MAE 指标断言）
3. 补充任务取消用例（运行中取消 → cancelled 状态）
4. 在 MySQL 可达环境下开启 `USE_DATABASE=true` 验证数据库镜像与日志分支
5. 前端页面手工回归（导入示例 → 下拉选择 → 开始训练 → 曲线/日志/预测 Tab）
6. 容器化部署验证（GPU 版与 CPU 版 compose 文件）
