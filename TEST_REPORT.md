# 测试报告

## 本次变更测试结果：平台全面重构（新训练 API / 工作台 / 模板库 / 模型注册表 / Pipeline / 异构分布式）（2026-11-01）

### Git 基准点与范围

- **变更性质**：按用户指令"移除之前训练模型的方式，以最新方案全面重构"。旧 `/api/train`、`/api/jobs` API 面已移除，MySQL 与 Redis 允许重刷。
- **测试日期 / 分支**：2026-11-01 / emdash/hot-boxes-clean-y9u6p。
- **架构决策**（用户确认）：
  1. Notebook 内核 = 自研轻量执行器 + 计算资源选择器（单选=单机，多选=分布式）
  2. 模型注册表 = ArtifactStore（S3/Local）+ MySQL `registered_models` 表
  3. Pipeline = 独立 DAG 执行器（不复用 JobManager）
  4. 前端 = 独立 Vue 3 + Vite + TS 工程（`frontend-vue/`）
  5. 分布式 = 流水线/数据并行混合 + 异构感知放置（显存优先 + 算力加权 + 通信拓扑）

### 变更范围

**新增模块**（backend/app/）：
- `training/`：`core.py`（统一训练入口 TrainingSpec/TrainingContext/run_training）、`checkpoint.py`（跨设备检查点迁移/兼容校验）、`tasks/`（classification/regression/clustering/time_series/llm 五类任务实现）、`distributed/`（strategy 并行策略、placer 异构放置、coordinator 协调器、worker 分布式入口、communicator 通信抽象、profiler 性能分析）
- `compute/`：`worker_host.py`（Worker 宿主）、`kernel_executor.py`（Notebook 执行内核+变量持久化）、`resource_selector.py`（单选/多选/异构感知资源选择）、`session_manager.py`（会话生命周期）、`environment.py`（venv/conda/容器环境隔离）
- `orchestration/`：`models.py`（Pipeline/Step/Run 数据结构+DAG 拓扑排序）、`engine.py`（DAG 执行引擎+8 类步骤执行器）、`artifact_passing.py`（步骤间产物传递+血统追踪）、`scheduler_service.py`（定时调度进程）
- `registry/`：`model_registry.py`（模型注册/版本/部署）、`template_registry.py`（6 个内置模板+Jinja2 渲染+参数验证）、`versioning.py`（语义化版本）
- `routers/`：`training_jobs.py`（训练任务+日志+预测+模型下载）、`notebook.py`（工作台 9 端点）、`templates.py`、`models.py`（注册表）、`pipelines.py`、`clusters.py`、`remote.py`（远程服务器管理，自旧 jobs.py 拆分保留）

**删除模块**：`routers/jobs.py`（旧 API 面）、`tests/test_llm_trainer.py` 恢复并迁移新路由；旧静态页面契约测试移除（前端由 Vue 工程替换）。

**数据库**：`models.py` 新增 9 张表（notebook_sessions/notebook_cells/script_templates/template_instantiations/registered_models/model_deployments/pipelines/pipeline_runs/pipeline_step_runs）；datasets/training_jobs 扩展字段（extra_metadata 规避 SQLAlchemy 保留字）。

**配置**：`config.py` 新增 Notebook/模板/注册表/Pipeline/分布式/异构放置 6 组配置项；`docker-compose.yml` 前端切换 frontend-vue、worker 入口改 worker_host、新增 scheduler profile。

### 验收标准—测试用例映射

| 验收标准 | 层级与输入 | 用例及预期业务结果 | 场景 |
|---|---|---|---|
| T1 统一训练入口注册 5 类任务 | 单元 | `test_builtin_tasks_registered_on_import`、`test_get_task_class_unknown_raises_with_available_list`（未知任务报错含可用列表） | 正常、异常 |
| T2 异构微批次按算力加权分配 | 单元；2 设备算力 100:50 | `test_micro_batch_allocation_weighted_for_heterogeneous`：总量守恒且快设备分得更多 | 正常 |
| T3 数据并行批次均分 | 单元 | `test_micro_batch_allocation_uniform_for_dp`：4 卡 64 批各 16 | 正常 |
| T4 混合并行 world_size = dp×pp×tp | 单元 | `test_world_size_hybrid`：2×2×2=8 | 正常 |
| T5 异构放置覆盖所有叶子层 | 单元；3 层 MLP + 2 种 GPU | `test_placement_covers_all_layers` | 正常 |
| T6 放置后模型前向可用 | 单元 | `test_apply_placement_moves_modules` | 回归 |
| T7 DP 各设备计算负载相同 | 单元 | `test_dp_placement_all_devices_same_compute` | 正常 |
| T8 Pipeline DAG 线性/并行/菱形拓扑分层 | 单元 | `test_linear_chain_ordering`、`test_parallel_branches_same_level`、`test_diamond_dependency` | 正常 |
| T9 模板参数验证与默认值自动填充 | 单元 | `test_missing_required_param_reported`、`test_integer_coercion`、`test_select_option_validation`、`test_render_with_params` | 正常、边界 |
| T10 语义化版本解析/递增/排序 | 参数化单元 | `test_parse_semver*`、`test_increment[patch/minor/major]`、`test_generate_next_version_semver/timestamp`、`test_compare_and_sort` | 正常、边界 |
| A1 新 API 训练 E2E（提交→完成→指标→产物） | API 集成；200 行分类数据 | `test_new_api_train_e2e_classification`：completed、metrics 非空且含 val_loss | 正常 |
| A2 目标列校验 | API | `test_new_api_rejects_target_not_in_columns`：400 含"不在数据集列中"，任务未创建 | 异常 |
| A3 排队取消/删除 | API | `test_new_api_cancel_queued_job`（终态收敛）、`test_new_api_delete_job`（删除后 404） | 正常 |
| N1 Notebook 会话全生命周期 | API 集成 | `test_notebook_session_lifecycle`：创建→内核→跨单元格变量持久化（y=42, result=84）→单元格历史 completed→停止删除 | 正常 |
| N2 语法错误明确报告 | API | `test_notebook_cell_syntax_error_reported`：error_type=SyntaxError | 异常 |
| N3 运行时异常不崩溃会话 | API | `test_notebook_execution_exception_captured`：ZeroDivisionError 捕获后继续执行 | 异常 |
| M1 模型手动注册/列表/详情/归档 | API | `test_model_registry_manual_register_and_list`：metrics/artifacts 持久、归档后 status=archived | 正常 |
| M2 版本管理与最新版本查询 | API | `test_model_versions_endpoint`：latest=1.1.0 | 正常 |
| P1 Pipeline 依赖校验 | API | `test_pipeline_create_rejects_unknown_dependency`：400 含缺失步骤 ID | 异常 |
| P2 DAG 详情返回节点与边 | API | `test_pipeline_create_and_detail_dag`：菱形依赖 4 节点 4 边 | 正常 |
| R1 远程服务器管理不回归 | API（27 用例） | `test_remote_*` 全部通过（自 jobs.py 拆分至 remote.py） | 兼容、回归 |
| R2 联合训练 E2E 不回归 | API 集成（真实双 worker） | `test_workers_complete_one_joint_job[*]`：2 成员、segments=1、best.pt 存在、预测一致 | 回归 |
| R3 预测一致性不回归 | API 集成 | `test_predict_consistency` 等价用例（新路径）：缺失/非法字段按中位数容错返回且结果稳定 | 回归 |
| R4 LLM 训练器不回归 | 单元+API | `test_llm_trainer.py`（533 行）迁移新路由后全部通过 | 回归 |
| R5 Hub 数据集→训练→预测链路 | API 集成 | `test_hub_reference_trains_reproducibly_and_predicts` | 回归 |
| R6 全量回归 | Python 3.12 容器全量 | `backend/tests/` **775 通过 / 31 跳过 / 0 失败** | 兼容、回归 |

### 测试执行结果

| 执行方式 | 总数 | 通过 | 失败 | 错误 | 跳过 |
|---|---|---|---|---|---|
| Python 3.12 容器全量后端（`docker run -v "$(pwd):/repo" -w /repo/backend <trainer镜像> python -m pytest tests/ -q`，与历史基准同口径） | 806 | 775 | 0 | 0 | 31 |

- 新增测试：`tests/test_training_core.py`（19）、`tests/test_distributed_strategy.py`（32，含策略/放置/版本/DAG/模板参数）、`tests/test_new_training_api.py`（19）。
- 移除测试：37 个旧 `/api/train`、`/api/jobs` 路径用例 + 9 个旧静态页面契约用例（功能由新 API 与 Vue 前端等价承接，均在上表有新用例映射）。

### 关键模块测试结果

- 数据层（datasets/db_utils/storage/迁移）：既有用例全部通过（兼容新旧双存储布局）。
- 训练器层（training/core/tasks/checkpoint + trainer 兼容层）：`test_training_core` 19/19、`test_trainer` 全部通过。
- 分布式（strategy/placer/versioning/DAG）：`test_distributed_strategy` 32/32。
- API 层（新 7 组路由 + remote + workers 透传）：`test_api` 53/53、`test_new_training_api` 19/19。
- 联合训练/推理/Hub E2E：`test_parallel`、`test_inference`、`test_hub_datasets` 全部通过。

### 修复的集成缺陷（测试驱动）

1. `orchestration/models.py::get_execution_order` 拓扑排序入度语义写反（依赖计数与被依赖计数颠倒）——由 DAG 三用例捕获。
2. SQLAlchemy 保留字 `metadata` 冲突 → `extra_metadata`。
3. `routers/pipelines.py` 端点函数名遮蔽 db_utils 导入（get_pipeline 等）→ 别名导入。
4. `db_utils.create_pipeline` 自生成 ID 与路由返回 ID 不一致 → 支持 `pipeline_id` 透传。
5. `session_manager` 更新单元格使用客户端传入 ID 而非实际保存 ID → 以真实 cell_id 回写。
6. 模板参数验证对"有默认值的参数"误报必填 → 缺失时自动填充默认值。
7. Notebook/模板/模型路由在无 DB 环境崩溃 → 内置模板降级为内存列表（文件优先哲学）。

### 已知问题与限制

1. **前端工程未执行构建验证**：`frontend-vue/` 已完成工程骨架（Vue3+Vite+TS、路由、API 层、核心视图），但本机无 Node 环境、`npm install` 未执行，`vite build` 未验证；容器化验证依赖 Docker Hub 拉取 node/nginx 基础镜像。**未验证内容**：前端类型检查（vue-tsc）、生产构建、E2E 页面交互。**建议验证**：`cd frontend-vue && npm install && npm run build`，或直接 `docker compose build frontend`。
2. **分布式训练协调器（coordinator/worker 多机链路）未做真实多机验证**：单元层（策略/放置/通信抽象/剖析器）已覆盖；torchrun 启动链路需 ≥2 GPU 环境实测，当前容器仅 CPU。**建议验证**：`docker compose up worker` 于多卡宿主机提交 parallel_workers 任务。
3. **scheduler profile**（`docker compose --profile scheduler up`）为独立进程骨架，cron 解析采用简化策略（@every N 秒 / 分钟字段），复杂 cron 表达式待接入 croniter。
4. 模型部署（`/api/models/{id}/deploy`）当前仅创建部署记录，推理 Worker 拉起与服务发现待接入 inference 队列。

### 下次测试建议

- 前端：npm 依赖安装 + `npm run build` + Playwright E2E（工作台执行单元格、模板实例化、Pipeline 拖拽）。
- 分布式：双卡/异构（A100+3090）实测微批次加权分配与 1F1B 流水线吞吐；对照 `DistributedProfiler` 输出校验通信带宽模型。
- Pipeline：真实 5 步 DAG（Pull→Process→Train→Upload→Deploy）端到端 + 步骤失败重试注入。
- 数据迁移：旧 `training_jobs` 数据到新 Schema 的 `migrate_to_new_schema.py`（本批次未编写，因允许重刷数据库）。

### Git 基准点

- **Commit Hash**：见本次提交（Full platform restructure: unified training API, notebook workbench, template library, model registry, DAG pipelines, heterogeneous distributed training）
- **测试命令**：`docker run --rm -v "$(pwd):/repo" -w /repo/backend <trainer镜像> python -m pytest tests/ -q`（Python 3.12，与历史基准同口径）
