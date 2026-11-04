## 本次变更测试结果：emdash/hot-boxes-clean-y9u6p 合并至 master（2026-11-04）

### Git 基准点与范围

- **Commit Hash**：5d83958（Merge branch 'emdash/hot-boxes-clean-y9u6p'，父提交 master=8b2d698、分支=6304027）。
- **测试日期 / 分支**：2026-11-04 / master。
- **范围**：纯合并提交。分支侧业务变更：backend/app 统一训练 API 与 LLM/回归/时序训练器拆分（`training/tasks/*`）、Worker 行为调整、部署 503 契约路由及既有缺陷修复，`backend/app` 相对合并前 master 增量 58 文件 +12912/−1279；frontend-vue 全部 24 页重设计（详见下两节分支侧报告）。

### 合并与冲突处理

- `git merge --no-edit` 自动合并完成，**唯一冲突为 TEST_REPORT.md**（两侧报告格式重构导致整文件冲突）。
- 解决方式：保留 master 侧"中文表头修复合并至 master（2026-10-31）"合并说明 + 分支侧重构后的最新全量报告（见下文），业务代码零手工改动。
- 业务代码等效性：`backend/`、`frontend-vue/` 合并结果与两侧既有已测基线逐字一致（无任何非报告文件手工改动），两侧全量测试结论直接适用于合并后代码树（同 ada4ea8 合并时的论证方式）。

### 验收标准—测试用例映射

| 验收标准 | 验证方式与输入 | 预期结果 | 场景 |
|---|---|---|---|
| 合并冲突标记无遗漏入库 | `grep -c "^<<<<<<<\|^=======$\|^>>>>>>>" TEST_REPORT.md` | 0（实际 0） | 边界 |
| 合并后工作区干净 | `git status --porcelain` | 空输出（实际为空） | 兼容、回归 |

### 已知限制、重测建议与回滚

- 本次会话未重新执行容器重建与全量回归；对新代码树的最终验收建议补做 `docker compose up --build -d` + 后端全量 pytest 复核（分支侧基线：后端 777 通过 / 0 失败 / 31 跳过，前端单测 199/0，走查 96/96）。
- 回滚：`git revert -m 1 5d83958`。

## 本次变更测试结果：中文表头修复合并至 master（2026-10-31）

### Git 基准点与范围

- **Commit Hash**：ada4ea8（Merge branch 'emdash/hot-boxes-clean-y9u6p'，含 `55fa9c7` 中文表头与秒数精度 + `10dcfad` 报告）。
- **测试日期 / 分支**：2026-10-31 / master。
- **范围**：纯合并提交，合并为自动合并无冲突（`grep` 检查 TEST_REPORT.md 冲突标记为 0）。合并前分支侧已按 `10dcfad` 基准完成全量测试（见下节）；合并后 master 与 10dcfad 的 `backend/`、`frontend/` 业务代码 diff 为空，差异仅 TEST_REPORT.md（+96 行），测试结论直接适用。

### 验收标准—测试用例映射

| 验收标准 | 验证方式与输入 | 预期结果 | 场景 |
|---|---|---|---|
| 合并后业务代码与已测版本一致 | `git diff 10dcfad ada4ea8 -- backend/ frontend/` | 空输出（实测为空） | 兼容、回归 |
| 合并冲突无遗漏标记 | `grep -c "^<<<<<<<\|^=======$\|^>>>>>>>" TEST_REPORT.md` | 0（实际 0） | 边界 |

### 测试执行结果

- 使用分支侧 10dcfad 基准的既有全量结果：前端 188/188，后端 693 收集（661 通过、1 既有失败、31 跳过）。

实际执行命令：`git diff 10dcfad ada4ea8 -- backend/ frontend/`；冲突标记计数检查。

### 已知限制、重测建议与回滚

- 本次合并未引入新代码差异，无需回滚；撤销整个合并 `git revert -m 1 ada4ea8`。
- 后续 master 侧 `backend/app/` 或 `backend/static/` 变更时按常规触发重测并重建 Compose。


# 测试报告

## 前端 24 页全量重设计（2026-11-04）

### 变更范围

按用户确认的六阶段方案完成 `frontend-vue/` 应用框架与全部 24 个业务页面的重新设计
（后端接口与数据结构未改动，仅前端仓库与构建产物变更）：

1. **设计与测试底座**：石墨蓝设计令牌体系（main.scss 重写）、App 框架重构（分组侧栏/
   折叠持久化/移动端抽屉/通知中心）、路由 meta（面包屑/菜单归属/页面描述）、6 个公共
   组件（PageShell/StatCard/StatusTag/EmptyState/FilterBar/SectionCard）、状态映射与格式
   化工具；补齐 Vitest + Vue Test Utils + happy-dom + Playwright 测试底座与此前缺失的
   ESLint 配置。
2. **数据准备与工作台**：数据集导入/列表/HuggingFace、会话管理/新建/Notebook 详情。
3. **模板与训练**：模板库/我的模板/创建编辑/详情、任务列表/创建/详情。
4. **模型与集群**：模型注册表/部署管理/模型详情、Worker 管理/远程服务器。
5. **Pipeline 全链路**：新增 `utils/dag.ts`（依赖校验/分层布局/图构建），Pipeline 五页全部
   改造，创建/编辑采用 Vue Flow 可视化画布 + 属性面板。
6. **全量交付验收**：容器重建、后端全量回归、24 页 × 4 视口浏览器走查、本报告更新。

**修复的存量缺陷**（全部先有失败测试或走查用例复现，再修复）：

| 缺陷 | 位置 | 影响 |
|---|---|---|
| Monaco ESM 入口无 default 导出，`mod.default` 为 undefined | 会话详情 | 打开任意 Notebook 会话即抛错，编辑器不可用 |
| 编辑器容器用 `:nth-of-type` 定位，永远命中第一个单元格 | 会话详情 | 重排/新增单元格后编辑器错位 |
| `onMounted(refreshData)` 遗漏 | 远程服务器 | 页面打开永不加载列表 |
| `nextTick` 未导入，轮询回调首轮抛错被 catch 静默吞掉 | 远程服务器 | 操作进度永不更新、轮询不停止 |
| 编辑 Pipeline 保存时固定覆盖 `resource_hint:{}`/`retry_policy:{}`/`timeout:3600`/`continue_on_failure:false` | Pipeline 创建 | 高级配置静默丢失 |
| 4 处 `el-link :underline` 布尔用法（EP 2.14 已弃用） | 多页 | 控制台报错 |
| `StatCard.displayValue` 非响应式 | 公共组件 | 指标更新后文本不刷新 |
| 我的模板空列表渲染空白加载区（无空态） | 我的模板 | 空状态不可见 |
| 数据集列表导入/HF 对话框与独立页面重复 | 数据集列表 | 双入口维护成本 |

### 测试执行结果（2026-11-04）

| 验证 | 命令 | 结果 |
|---|---|---|
| 单元/组件测试 | `cd frontend-vue && npm run test:unit` | **199 passed / 0 failed**（连续 3 轮稳定） |
| 浏览器 e2e（交互回归） | `npm run test:e2e`（Chromium，冒烟 + 表单校验 + 侧栏导航） | **5 passed / 0 failed** |
| 全路由视觉走查 | `E2E_BASE_URL=http://localhost:8000 npx playwright test tests/e2e/walkthrough.spec.ts` | **96/96 通过**（24 路由 × 4 视口：1440/1280/768/390；外壳渲染、页面标题、无未捕获渲染错误、逐页截图人工走查） |
| 前端类型检查 | `npm run type-check`（vue-tsc） | 0 错误 |
| 前端 Lint | `npm run lint`（本轮补齐配置后首次可用） | 0 错误 |
| 前端构建 | `npm run build` | ✓ built in 8.6s |
| 容器重建 | `docker compose up --build -d` | 4 镜像构建成功，trainer/worker/frontend 重建启动，frontend healthy |
| 后端全量回归（容器） | `docker run --rm -v "$(pwd):/repo" -w /repo/backend <trainer镜像> python -m pytest tests/ -q`（Python 3.12.15） | **777 passed / 0 failed / 31 skipped**（较上轮基线 775 增加 2 个为 1b956ab 已提交的部署契约测试；后端代码本轮零改动，无回归） |
| SPA 回退抽查 | `curl http://localhost:8000/training/jobs` | 200 返回 index.html |
| API 抽查 | `curl http://localhost:8000/api/datasets` | 200，返回真实数据集 |

**走查覆盖说明**：走查用例（`tests/e2e/walkthrough.spec.ts`）已入库，可重复执行；截图为
临时产物（`/tmp/mtp-walkthrough`，约 7.8MB）已按规范清理，不纳入仓库。走查在容器化前端
（mtp-frontend，构建产物 + 同源 /api 代理）上执行，数据为平台真实数据。

### 关键模块测试结果

| 模块 | 覆盖内容 | 结果 |
|---|---|---|
| 设计系统/框架 | 设计令牌、公共组件行为（键盘可达/事件）、路由 meta | 32 项通过 |
| 数据链路 | 列表筛选参数、导入载荷、HF 物化降级、预览/统计抽屉、预处理 JSON 校验 | 22 项通过 |
| 工作台 | 会话 CRUD/内核启停、创建摘要联动、Notebook 执行/全部运行/变量轮询清理 | 26 项通过 |
| 模板与训练 | 模板 CRUD、四类任务提交载荷（target/n_clusters/time_column）、模板预填、指标图表数据、日志过滤 | 43 项通过 |
| 模型与集群 | 注册表筛选/归档、部署停止、Worker 汇总聚合/心跳滞后/托管开关、服务器登记载荷/凭证/操作轮询生命周期 | 29 项通过 |
| Pipeline | DAG 校验（重复/自依赖/缺失/循环/布局防呆）、字段保留往返、五页交互（图节点点击/执行历史/运行状态映射） | 48 项通过 |
| 后端回归 | 全量套件（本轮前端改动不涉及后端代码） | 777 passed / 31 skipped |

### 重测触发条件

- 修改 `frontend-vue/src/` 下任意页面/组件/工具 → 重跑 `npm run test:unit` + `npm run build`；
  涉及交互链路（表单提交/弹窗/轮询）时同步重跑 `npm run test:e2e`。
- 修改 `frontend-vue/` 构建配置或依赖 → 重跑 `docker compose up --build -d` 并做走查抽查。
- 修改 `backend/app/` → 按既有规则重跑容器全量套件。
- DAG 工具（`utils/dag.ts`）被 Pipeline 画布与详情页共同消费，其改动必须重跑全部
  Pipeline 相关测试（`npx vitest run src/utils/__tests__/dag.spec.ts src/views/pipelines`）。

### 已知问题

- Element Plus 2.14 表单校验在 happy-dom 下放行空必填值（真实浏览器行为正常，已用 e2e
  在真实 Chromium 中覆盖：HuggingFace 导入、新建会话、登记服务器三个入口）。单元测试
  因此不依赖 EP 校验语义，业务校验（JSON 参数、DAG 依赖）均为前端自实现逻辑，不受影响。
- 生产构建依赖 unplugin-auto-import 注入 Vue API：本轮已将涉及运行时行为的页面全部改为
  显式导入（该差异曾是远程服务器页轮询缺陷的根因之一）。
- 后端 31 个 skipped 用例为存量基线（GPU/多机相关），非本轮引入。
- Playwright 截图走查为人工视觉验收辅助，最终视觉标准以产品确认结果为准。

### 下次测试建议

- 为 Worker 管理的 30 秒自动刷新与任务详情/执行详情的 5 秒轮询补充真实浏览器长时运行
  观测（当前以单元测试断言定时器生命周期）。
- Pipeline 画布的拖拽连线（Vue Flow 交互）目前以“属性面板改依赖”路径测试，可在真实
  浏览器补充拖拽建边的 e2e 用例。
- 视觉回归可引入 Playwright 截图对比基线（`toHaveScreenshot`），在固定视口与数据集下
  防止样式回归。

### Git 基准点

- **Commit Hash**: 2949a18（Fix Monaco ESM import in session detail; add full-route walkthrough spec）
- **分支**: worktree（emdash-hot-boxes-clean-y9u6p）
- **测试日期**: 2026-11-04
- **本报告覆盖的前端改造提交**：81b65fc（底座+框架+仪表盘+数据集列表）→ 20479c1（数据准备+工作台）→ 0bcb3ca（模板+训练）→ c92ad42（模型+集群）→ 3112128（Pipeline）→ 2949a18（Monaco 修复+走查）

---

## 前端全面优化与训练任务列表 502 修复（2026-11-03）

### 变更范围

按用户确认的方案对 11 项页面与功能问题进行改造：远程服务器与凭证、Worker 管理、仪表盘、
数据集维护、新建会话、脚本模板库、训练任务列表 502、训练任务创建、模型注册表、部署管理、
Pipeline。模型删除采用**归档（软删除）**：`DELETE /api/models/{id}` 仅置 `status=archived`，
不删记录与产物，前端默认筛选 `ready`、可切换查看已归档模型。

**后端变更**（仅 `backend/app/routers/models.py`，+63 行）：
- 新增 `GET /api/models/deployments`（全局部署列表，支持 model_id/status 过滤，注册在 `/{model_id}` 之前避免路由遮蔽）；
- 新增 `DELETE /api/models/deployments/{deployment_id}`（停止部署，置 `status=stopped`）；
- 修复 `download_model` 缺失 `import app.config` 导致本地存储模式 NameError；
- 两个新端点在数据库不可用时返回受控 503（与 remote 路由既有模式一致）。

**前端变更**（19 个文件）：
- 502 根因修复：训练任务列表/仪表盘/任务详情此前请求不存在的 `/api/jobs`（后端实际为
  `/api/training/jobs`），容器代理下表现为 502/404；创建任务页误拼 `/api/api/training/jobs`；
  详情页 `.replace('{id}')` 模板字符串未替换。全部改为正确路径。
- API 客户端：统一提取 FastAPI `detail` 到 `err.message`（含 422 字段校验信息）、超时 60s、
  数组查询参数按重复键序列化（`tags=a&tags=b`）、upload 支持附带表单字段。
- App.vue 渲染全局通知（此前 `appStore.success/error` 无任何 UI 呈现）+ 侧栏折叠。
- 占位页重写为真实功能：NewSession（创建会话并跳转）、TemplateCreate/MyTemplates
  （创建/编辑/删除用户模板）、DatasetImport（上传+示例+导入历史）、DatasetHuggingFace、
  ModelRegistry/Deployments/ModelDetail、PipelineList/Create/Runs/RunDetail/Detail、
  JobList/JobDetail/JobCreate（结构化表单+模板预填）、WorkerList（启停+状态）、
  RemoteServers（登记+凭证管理+操作轮询）、TemplateDetail。
- 存量缺陷修复：数据集预览误用预处理表单的 dataset_id、删除无二次确认（数据集/会话/
  任务/Pipeline/凭证）、上传未携带描述与标签、会话创建把 `metadata_json` 误发后端、
  `PipelineRunDetail` 路由改为 `/pipelines/run/:pipelineId/:runId`。

### 测试执行结果（2026-11-03）

| 验证 | 命令 | 结果 |
|---|---|---|
| 前端类型检查+构建 | `cd frontend-vue && npm run build`（vue-tsc + vite） | 类型检查 0 错误，✓ built in 7.14s |
| 容器重建 | `docker compose up --build -d` | 4 镜像构建成功，trainer/worker/frontend 重建并启动，frontend healthy |
| 全量回归（容器） | `docker run --rm -v "$(pwd):/repo" -w /repo/backend <trainer镜像> python -m pytest tests/ -q`（Python 3.12） | **775 passed / 0 failed / 31 skipped** |
| API 修复验证 | `curl http://localhost:8000/api/training/jobs` | 200（修复前该页请求 `/api/jobs` → 404，代理下 502） |
| 旧错误路径对照 | `curl http://localhost:8000/api/jobs` | 404（确认为 502 根因） |
| 新增部署接口 | `curl .../api/models/deployments` | 200 `{"deployments":[],"total":0,...}`；删除不存在部署 → 404 `部署记录不存在` |
| 部署接口契约测试 | `pytest tests/test_new_training_api.py -q`（Python 3.12 容器） | **21 passed**（含新增 2 个：全局列表过滤/路由遮蔽、停止部署/404/详情同步） |
| 无 DB 受控错误 | 本地 `USE_DATABASE=false` 启动后 curl | 部署接口 503 中文提示；`/api/models`、`/api/workbench/sessions` 为存量 DB 依赖 500（容器内正常） |
| 其余接口抽查 | `/api/datasets` `/api/samples` `/api/datasets/import-history` `/api/clusters/workers` `/api/clusters/workers/summary` `/api/templates` `/api/remote-ssh-credentials` | 全部 200/503（受控） |
| SPA 回退 | `curl http://localhost:8000/training/jobs` | 200 返回 index.html |
| ESLint | `npx eslint .` | 不可用（项目无 ESLint 配置文件，存量情况）；类型门禁由 vue-tsc 覆盖 |

**容器全量套件说明**：采用与上次基准相同的方式（trainer 镜像 + 挂载完整仓库）运行，
结果 **775 passed / 0 failed / 31 skipped**，与基线完全一致，本次后端改动未引入失败。
另注：曾尝试仅把 `backend/tests` 拷入运行中的 trainer 容器执行，出现 22 个失败——
全部因仓库根目录夹具（`scripts/worker-node.sh`、launcher 等，测试以 `parents[2]` 相对解析）
缺失所致，仓库挂载方式下不存在，非代码回归。

**本机环境限制说明**：宿主机为 Python 3.9（容器为 3.12）。本机直跑 pytest 存在两类与代码
无关的收集错误：① PEP 604 语法（已装 `eval_type_backport` 解决）；② 缺依赖
（`openpyxl`/`datasets`/`cryptography`，共 4 个模块无法收集）。本机补装 cryptography 后
对比基线 ca35ad5：全量跑失败集合与基线一致（watchdog/worker 类用例在全量负载下的时序抖动，
单跑即过；`test_parallel` llm_pretraining 暂停恢复与 `test_performance` cpu quota 为可稳定
复现的存量失败，基线同样失败），**本次变更无回归**。API 级验证以容器栈结果为准。

### 关键模块测试结果

- 数据层/API 层：容器全量套件 0 失败（含 storage、db_utils、trainer、API 契约）。
- 前端：vue-tsc 全量类型检查通过；构建产物含全部 15 个重写页面 chunk。
- 新增后端路由：200/404/503 三分支实测通过。

### 重测触发条件

与既有规则一致：`backend/app/`、`frontend-vue/src/` 业务代码变更、路由（`main.py`、
`router/index.ts`）、核心配置（`config.py`、`vite.config.ts`、`docker-compose.yml`）变更后
需重跑"前端构建 + 容器全量套件 + docker compose 重建"。

### 已知问题

1. 模型注册表 / 部署 / Pipeline / Notebook 会话接口依赖 MySQL（`USE_DATABASE=false` 时
   返回 500），为既有架构行为；容器部署默认开启数据库，不受影响。
2. `test_parallel`（llm_pretraining 暂停恢复）与 `test_performance`（cpu quota）存在存量
   失败，与本次变更无关（基线 ca35ad5 同样失败）。
3. ESLint 声明于 package.json 但项目无 ESLint 配置文件，`npm run lint` 不可执行。
4. 部署"停止"仅置记录状态，推理服务实际下线依赖后续 Worker 执行器实现（后端注释中标注
   TODO，本次未扩展）。

### 下次测试建议

- 前端可引入 vitest 对 API 客户端的 paramsSerializer 与错误信息提取做单元测试。
- 修复存量失败：`test_performance::test_cpu_quota_respected` 的 AttributeError、
  `test_parallel` 的 llm_pretraining 暂停恢复。
- 为远程服务器操作轮询（`/api/remote-operations/*`）补充 E2E 用例。

**2026-11-03 补充**：部署接口契约测试已补齐
（`test_model_deployments_global_list_endpoint`：全局列表/状态与模型过滤/路由遮蔽；
`test_model_deployment_stop_endpoint`：停止置 stopped/404/模型详情同步），本机 2 passed、
Python 3.12 容器模块级 21 passed。按重测触发规则，仅新增测试代码无需重跑全量套件。

### Git 基准点

- **Commit Hash**：c6d3524（Return controlled 503 for deployment endpoints when database is unavailable）
- **分支**：emdash/hot-boxes-clean-y9u6p
- **测试日期**：2026-11-03
- **测试命令**：前端 `npm run build`；后端 `docker compose exec trainer python -m pytest tests/ -q`

---

## 运行环境全面验证结果（2026-11-01）：五大需求端到端验证 32/32 通过

### 验证方式与范围

在真实部署栈（docker compose：seaweedfs + trainer + worker + frontend-vue）上，通过
`backend/scripts/verify_requirements.py`（HTTP API 端到端）+ Python 3.12 容器全量测试套件
双重验证。验证期间发现并修复 4 个集成缺陷（见下），最终 **32 项运行时检查全部 PASS，
全量测试套件 775 通过 / 0 失败**。

### 部署栈验证结果（verify_requirements.py，32/32 PASS）

| 需求 | 验证项（全部 ✅） | 证据 |
|---|---|---|
| **REQ1 交互式工作台**（10 项） | 资源列表/摘要、创建会话并**绑定指定计算资源**、启动内核、**单步执行**、**跨单元格变量持久化**（area=28.27）、变量查看器、运行时异常捕获（ZeroDivisionError）不崩溃、异常后继续执行、**多选资源创建会话**（分布式绑定） | workers=2, session=nb-…, output=area=28.27 |
| **REQ2 脚本模板库**（6 项） | 6 类内置模板（分类/回归/聚类/时序/LLM-SFT/LLM-DPO）、参数定义含 dataset_id/target 数据集交互字段、参数验证+默认值自动填充、非法参数拒绝、渲染为训练脚本（占位符全替换）、**一键实例化** | categories 全覆盖, rendered length=1029 |
| **REQ3 OSS 数据集分离拉取/训练**（5 项） | 数据集导入（拉取阶段）→ 元数据就绪（rows=60）→ **训练任务引用该数据集**（计算资源从状态平面取数）→ 训练完成 → 指标完整（epoch≥2） | ds-…-1a6461 → job-…-829d4f completed |
| **REQ4 模型注册表**（5 项） | 训练完成 → **best.pt 自动上传 OSS 且可下载**（200, 3135B）→ **注册表自动注册可见**（search 按 job_id 命中）→ 列表字段完整（含 source_job_id）→ 版本管理接口 | 自动注册 4 条, versions=['1.0.0'] |
| **REQ5 Pipeline 编排**（4 项） | 创建菱形 DAG（4 节点）→ **DAG 节点与依赖边正确** → 未知依赖 400 拒绝 → 执行历史接口 | edges={(pull,proc_a),(pull,proc_b),(proc_a,train),(proc_b,train)} |
| **附加**（2 项） | Worker 校验接口（不存在拒绝）、平台健康检查 | all_valid=false + 原因 |

### 验证期间发现并修复的集成缺陷

1. **Notebook 会话双 ID 不一致**：`db_utils.create_notebook_session` 自生成 ID 与内存会话 ID
   不同 → 单元格写入外键失败（notebook_cells → notebook_sessions）。修复：支持 `session_id`
   透传，`session_manager` 传入生成的 ID。
2. **变量快照序列化崩溃**：单元格 `import math` 后模块对象进入变量快照，SQLAlchemy JSON 列
   无法序列化 → 500。修复：`VariableStore._is_serializable` 拒绝模块/函数/类，仅保留
   JSON 安全变量。
3. **内置模板实例化外键失败**：内置模板仅存于代码（无 DB 行），实例化记录外键失败。
   修复：内置模板跳过 DB 实例化记录（`template_registry.instantiate` 移除 DB 写，
   路由层仅对用户模板记录）。
4. **训练完成自动注册未接入执行路径**：`MODEL_AUTO_REGISTER` 配置存在但实际执行路径
   （app.worker `_run_claimed`）从未调用注册表。修复：新增 `registry/auto_register.py`
   单一入口（开关控制 + 失败不阻断 + 兼容 dict/TrainingSummary/旧路径产物约定），接入
   `_run_claimed` 成功路径；`models` 列表响应补 `source_job_id` 字段。
5. **worker 容器旧库结构崩溃**：线上 MySQL 为旧 Schema（缺 worker_id 等列）→ 任务镜像行
   写入失败、注册表外键失败。处置：按用户确认重刷数据库（drop_all + create_all 新 Schema）。
6. **worker 容器执行路径回归**：`compute.worker_host._run_local` 缺 checkpoint/torch 导入
   且未实现完整任务生命周期（状态收尾/重试/产物同步）。决策：worker 容器执行回归经过全
   生命周期验证的 `app.worker` TrainingWorker（现含自动注册），`worker_host` 保留为
   Notebook 内核宿主扩展入口，避免训练生命周期逻辑双份维护。
7. **前端构建链修复**（Vue 工程）：vue-tsc 1.x 与 TS 5.5+ 不兼容（升级 ^2.1.0）、
   @vue-flow/controls 版本不存在（^1.1.0）、缺失视图文件、模板未闭合标签、图标导出名
   （Play/Stop/Save → VideoPlay/VideoPause/DocumentChecked）、`el-tag-input` 杜撰组件
   （改 el-select allow-create）。生成 package-lock.json（npm ci 可复现构建）。
8. **前端去 nginx 化**（用户指令）：`frontend-vue` 采用与旧版相同的零依赖 Node 服务
   （server.js：静态托管 dist + SPA 回退 + /api 与 /ws 流式代理 + /healthz），
   删除 nginx.conf；docker-compose 健康检查回 /healthz。

### 运行时验证记录（关键命令与结果）

| 验证 | 命令 | 结果 |
|---|---|---|
| 容器重建 | `docker compose build trainer worker frontend` | 全部 EXIT=0 |
| 全栈启动 | `docker compose up -d` | 4 容器 healthy/running |
| 前端构建 | `npm ci && npm run build`（node:20 容器） | vue-tsc 0 错误 + vite ✓ built in 11.10s |
| 前端页面 | `curl http://localhost:8000/` | Vue index.html + hash assets |
| SPA 回退 | `curl http://localhost:8000/workbench/sessions` | 返回 index.html |
| API 反代 | `curl http://localhost:8000/api/health` | {"ok":true} |
| 需求验证 | `python scripts/verify_requirements.py http://localhost:8000` | **32/32 PASS** |
| 自动注册实证 | 训练任务日志 | `[13:50:12] 模型已自动注册: classification-job-… v1.0.0（id=model-005c72b1aae2）` |
| 全量回归 | `pytest tests/ -q`（Python 3.12 容器） | **775 passed / 0 failed / 31 skipped** |

### Git 基准点

- **Commit Hash**：见本次提交（End-to-end verification: fix integration defects and enable auto-registration）
- **测试命令**：`docker run --rm -v "$(pwd):/repo" -w /repo/backend <trainer镜像> python -m pytest tests/ -q`

---

## 历史报告：平台全面重构（2026-11-01）

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
