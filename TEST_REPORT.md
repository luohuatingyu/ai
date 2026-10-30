## 本次变更测试结果：曲线 Tab 与训练记录、结果恢复（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：5597f556e8ef00b2efabedfe4a623fb62bb94171。
- **提交信息**：Add curve tabs and restore training detail views。
- **测试日期 / 分支**：2026-10-30 / emdash/hot-boxes-clean-y9u6p。
- **范围与技术**：原生 HTML/CSS/JavaScript、Chart.js；曲线增加 Loss、验证指标、训练吞吐、阶段耗时二级 Tab，每次只展示一张图。详情切换先渲染当前缓存，合并同一任务选择的未完成请求，15 秒超时释放，并保持任务/选择版本隔离。未修改 backend/app/、接口、数据格式、配置或依赖。
- **提交隔离**：只提交本次 5 个文件的增量。工作区原有历史分页、资源刷新及服务器管理等改动和原暂存状态保持；未将这些内容纳入本次提交。
- **基准点检查**：原报告首个基准点 f8184d1 与任务开始时 HEAD 的 backend/app/ 存在 coordination.py、routers/jobs.py、worker.py 历史变更，因此执行 Python 3.12 完整回归。

### 验收标准—测试用例映射

| 验收标准 | 层级 / 前置条件与输入 | 可执行用例与预期业务结果 | 场景 |
|---|---|---|---|
| 每次只显示一张训练曲线 | 前端交互；加载带指标任务，依次点击四个子 Tab，再切换外层详情 | workbench-interactions.test.cjs「曲线二级标签只展示一张图…」「曲线页面初始只显示 Loss…」；恰好一个可见面板、指标值保留、画布重新测量、返回时保留所选曲线 | 正常、回归 |
| 二级 Tab 键盘可用且不干扰外层 | 前端交互；方向键、Home、End 和无效标签名 | 同文件「曲线标签支持方向键…」；焦点、ARIA 和可见面板一致，外层仍为曲线；非法选择不改状态 | 边界、无障碍 |
| 训练记录与结果立即显示 | 前端交互；已完成任务、准确率 0.85，挂起后拒绝详情请求 | 同文件参数化「切换record/result时立即显示已加载详情…」；记录包含逐轮 0.85，结果显示 85.00%，失败后不清空 | 正常、异常 |
| 慢响应不被轮询持续作废 | 前端异步；连续五次轮询并切到记录页 | 同文件「慢详情请求在连续轮询与切换标签期间合并…」；只发一次请求、绕过缓存、带超时信号，完成后显示记录，错误后可重试 | 并发、超时、恢复 |
| 跨任务与同 ID 重新选择不串数据 | 前端异步；A 挂起后选 B、A→B→A/选择版本更新、旧请求成功或失败 | 同文件旧响应/乱序用例；只采纳当前选择的数据，旧请求不能清除新请求状态 | 异常、回归 |
| 旧任务和外部图表组件故障可理解 | 前端；空指标、缺性能、缺 Chart.js，恢复组件 | performance.test.cjs 四种曲线参数化用例及既有单位/数据切换用例；按选中曲线显示空状态、明确组件缺失、恢复后数据正确 | 边界、依赖异常、兼容 |
| 记录中的不可信文本安全展示 | 前端；任务名/错误/配置含 HTML 文本 | 保留训练记录、任务列表转义断言；输出转义文本，历史任务字段缺失有空状态 | 安全、兼容 |
| 桌面与窄屏实际可用 | Safari，已完成的既有分类任务；桌面和 390×1080 视口 | 浏览器切换验证指标/阶段耗时/吞吐及外层记录、结果；单图正常绘制，记录含 30 轮指标，结果显示 83.33% 及分类报告 | 视觉、集成 |

### 测试执行结果

| 阶段 / 实际命令 | 总数 | 通过 | 失败 | 错误 | 跳过 |
|---|---:|---:|---:|---:|---:|
| 修改前工作区 node --test frontend/*.test.cjs | 128 | 125 | 3 | 0 | 0 |
| 先补复现测试 node --test frontend/workbench-interactions.test.cjs | 34 | 27 | 7 | 0 | 0 |
| 修复后专项 node --test frontend/workbench-interactions.test.cjs frontend/performance.test.cjs | 42 | 42 | 0 | 0 | 0 |
| 修复后工作区 node --test frontend/*.test.cjs | 139 | 139 | 0 | 0 | 0 |
| Python 3.12.15 后端完整 pytest（命令见下） | 648 | 617 | 0 | 0 | 31 |
| 隔离提交快照 node --test frontend/workbench-interactions.test.cjs frontend/performance.test.cjs | 42 | 42 | 0 | 0 | 0 |
| 隔离提交快照 node --test frontend/*.test.cjs | 135 | 131 | 4 | 0 | 0 |
| 原 HEAD 0eac415 对照 node --test frontend/training-resources.test.cjs | 27 | 23 | 4 | 0 | 0 |

- 当前工作区前端通过率 100%；后端执行用例通过率 100%，31 项按已有环境条件跳过，未修改或弱化跳过条件。
- 修改前 3 项失败来自测试 fixture 未初始化已有分页状态；补齐 jobPage/historyPageCount，保留原业务断言后通过。
- 先添加的 7 项失败覆盖本次请求竞争、缓存显示和曲线标签缺失；实现后全部通过。原乱序测试改为同一 ID 的新选择版本，继续验证迟到响应不能覆盖新响应。
- 隔离提交的 4 项失败均为既有 remote-management.js 的 starting/training/running/reserved 状态与样式断言，已在原 HEAD 对照复现。其修复属于开始时即存在的未提交内容；本次不夹带。当前工作区包含原有修复，因此完整 139 项通过。独立检出本提交仍有这 4 项历史失败，不能标记为独立完整回归全绿。
- 首次生成隔离快照采用无上下文补丁，发生片段位置偏移、脚本语法失败；该临时快照已丢弃，改用三方合并并人工核对唯一状态行冲突后重新验证。真实工作区代码未受影响。
- 后端包含数据层、训练器、任务管理、API、旧/新布局、预测及 CPU 联合训练回归；2 条既有警告来自 SQLAlchemy declarative_base 与 pandas xlrd。

实际执行命令：

```bash
node --test frontend/workbench-interactions.test.cjs
node --test frontend/workbench-interactions.test.cjs frontend/performance.test.cjs
node --test frontend/*.test.cjs
docker run --rm --pull=never --network none \
  --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest \
  python -m pytest tests/ -q -p no:cacheprovider \
    --basetemp=/tmp/mtp-curve-tabs-tests --tb=short -rs
docker compose up --build -d
docker compose ps
docker compose exec -T trainer python --version
git diff --check
```

- 容器重建成功，frontend/SeaweedFS 健康，trainer/worker 运行；Node fetch 检查首页和 /api/health 均 200，首页含 4 个曲线标签及新样式版本。Node vm.Script 校验页面内联脚本通过。
- 浏览器验证使用已有任务，仅切换显示；没有新增或删除训练数据。窄屏验收后已退出响应式模式，恢复桌面视图。
- 调试/隔离快照保存于 /tmp/mtp-curve-tabs-VmGUxb，交付前删除；后端测试临时数据仅位于 --rm 容器内，随容器退出清理。无运行时产物提交。

### 已知问题、未验证范围与下次建议

- 实际服务仍偶发详情请求超过 15 秒；页面会显示刷新失败提示并在后续轮询重试，已有记录和结果保持可见。后端接口延迟优化不在本次前端修复范围，建议另行分析对象存储/请求耗时。
- 未配置 MTP_TEST_REDIS、MTP_TEST_S3，且无真实 NVIDIA GPU/多卡 NCCL，31 项相关集成/硬件测试跳过；应在对应环境补验。
- 实际浏览器验证覆盖 Safari 桌面及响应式 390 像素视口；未执行实体手机和其他浏览器验收。使用已有 AbortSignal.timeout（Safari 16+ 及现代浏览器支持）。
- 原提交中资源状态 4 项历史失败应由已有资源修复工作提交后再验独立完整套件；本次未修改第三方代码或训练业务逻辑。
- 后续修改详情请求生命周期、图表 Tab、任务结果格式时重跑上述前端专项与完整套件；修改 backend/app/ 时执行 Python 3.12 完整回归并更新基准点。
- 回滚方式：撤销 5597f55 后执行 docker compose up --build -d；无数据迁移。

## 本次变更测试结果：训练性能与计算资源观测（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：`f8184d1`（Add training performance observability）。
- **测试日期 / 分支**：2026-10-30 / `emdash/hot-boxes-clean-y9u6p`。
- **对照范围**：仅覆盖 `PLATFORM_REPLACEMENT.md` 中与训练性能、吞吐、计算资源占用相关的首批优化；不涉及权限、认证、AMP、LoRA、调度器或异步发布。
- **实现范围**：新增低频资源采样和 CPU 线程预算；记录每轮 samples/s、LLM 有效 tokens/s、step 和训练/验证/检查点阶段耗时；CUDA pinned memory 与 non-blocking 传输；减少训练循环中的 GPU 标量同步；API 与工作台展示资源卡片和性能曲线；性能记录独立于原有精度指标，续训按 checkpoint epoch 回滚。

### 验收标准—测试用例映射

| 验收标准 | 可执行验证 | 结果与场景 |
|---|---|---|
| 资源占用可观测且未知值不伪造 | `tests/test_performance.py`：CPU 差分、RSS/线程、GPU UUID 匹配、驱动缺失/超时、采样线程隔离；`test_api.py::test_performance_detail_and_resource_heartbeat` | 通过；首样本为未知，合法 0 保留，采样故障不阻塞心跳；正常、边界、异常、兼容 |
| 线程预算受 CPU 亲和性/容器配额/槽数约束 | `test_thread_budget_bounds`、`test_cpu_quota_respected`、Supervisor 后端可见性回归 | 通过；每槽预算至少 1，保留更小正整数覆盖，非法值启动前拒绝 |
| 训练吞吐和阶段时间可追踪 | `test_real_training_records_throughput`（分类/回归/聚类/时序）、`test_pretraining_from_scratch_saves_metrics_bundle_and_generates`、`test_training_artifacts_metrics_and_predictions` | 通过；生成独立 `performance.jsonl`，samples/s 或有效 tokens/s、step、保存/发布耗时均可序列化 |
| 训练循环减少同步并保持结果兼容 | 训练器和 LLM 既有回归、固定 seed 重复训练、联合训练权重/空尾批/续训用例 | 通过；CPU 路径行为保持，GPU 路径将标量同步推迟到轮次边界 |
| 检查点耗时与断点恢复曲线正确 | `test_checkpoint_timing_and_errors`、Worker 完成/暂停接手用例、性能文件按恢复 epoch 截断 | 通过；保存与发布分项记录，发布失败原样传播，重试不产生重复轮次 |
| 页面展示资源和性能曲线 | `node --test frontend/*.test.cjs`，含 `frontend/performance.test.cjs` | 108 通过；覆盖未知/零值/过期、曲线断点、吞吐单位、阶段堆叠和旧任务空状态 |

### 测试执行结果

| 阶段 / 命令 | 总数 | 通过 | 失败 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---|
| 后端完整回归 | 597 | 597 | 0 | 24 | Python 3.12.15；2 条既有弃用警告；跳过项来自未配置 Redis/S3/NVIDIA GPU/NCCL |
| 前端完整回归 `node --test frontend/*.test.cjs` | 108 | 108 | 0 | 0 | Node 内置测试 |
| 容器重建 `docker compose up --build -d` | 4 服务 | 4 启动 | 0 | 0 | trainer、worker、frontend、SeaweedFS 均正常 |
| HTTP 健康核对 | 2 | 2 | 0 | 0 | `/api/health` 与首页均返回 200 |
| 真实 CPU 训练冒烟 | 1 | 1 | 0 | 0 | 示例分类数据集，指定本地 CPU 槽，2 epochs 完成；accuracy、`best.pt`、2 条性能记录均存在 |

完整回归实际命令：

```bash
docker run --rm --pull=never --network none \
  --mount type=bind,src="$PWD",dst=/workspace \
  --workdir /workspace/backend \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest \
  python -m pytest tests/ -q -p no:cacheprovider \
    --basetemp=/tmp/mtp-performance-full --tb=short -rs

node --test frontend/*.test.cjs
docker compose up --build -d
git diff --check
```

真实冒烟通过 API 完成：导入 `classification_demo.csv`，提交到 `2f2d5053373e-cpu0`，任务 `job-20261030-053753-7138d4` 完成后删除任务和临时数据集。训练结果包含 `val_accuracy=0.75`、`best.pt` 和每轮 `samples_per_second`；运行产物未保留在工作区。

### 已知限制与下次测试建议

- 本机没有可用 NVIDIA GPU，GPU 利用率、显存和 CUDA 加速路径只通过 mock/兼容测试验证，不能据此宣称 GPU 提速比例；建议在目标 NVIDIA/ROCm/XPU 环境做基线对照。
- 主机 CPU 指标是整机口径，同一主机多个槽的百分比不能相加；嵌入式 worker 的进程指标包含共享 API 进程。采样历史限于 worker 生命周期内最近 120 个样本，超过 20 秒标记过期。
- 未配置真实 Redis、S3 和多卡 NCCL，因此 24 项既有条件测试按项目约定跳过。
- Safari 原生自动化通道在本次验收中返回 “native pipe closed”，未完成人工视觉检查；DOM/Node 行为测试及 HTTP 内容核对已通过。
- 后续建议在真实 GPU 节点比较开启前后吞吐、GPU 利用率、显存峰值和检查点发布占比，并补做窄屏页面视觉验收。
- 回滚方式：撤销业务提交 `f8184d1` 后重新执行 `docker compose up --build -d`；无数据迁移。

## 本次变更测试结果：服务器与凭证管理拆分（2026-10-29）

### Git 基准点与范围

- **Commit Hash**：`60a8c67adfeb90ffda3d36ca92ab59c24d7f757e`。
- **提交信息**：Separate server and credential management pages。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围与技术**：训练台、数据集页导航；新增服务器管理页、凭证管理页及共享原生 JavaScript/CSS；Node.js 内置测试和 pytest。后端 API、配置、依赖及数据格式未修改。
- **行为**：服务器和凭证以独立导航页维护。服务器页管理连接、资源类型、部署/生命周期、日志和生效配置；弹窗只从凭证目录选择已有凭证，并提供跳转与刷新。凭证页独立新增、编辑、删除、设为默认，支持密码、私钥和混合认证。训练台只展示资源状态并提供管理入口。
- **隔离验收**：共享工作区有其他并行任务的未提交改动。以独立 Git 索引从 `fd76e3756ed463a11fbb13052ce391913e09b3c6` 生成候选快照，运行完整测试后只提交本任务的 8 个文件；其他任务的工作区改动和暂存内容未纳入本提交。

### 验收标准—测试用例映射

| 验收标准 | 层级、用例与前置条件 | 输入、预期和场景 |
|---|---|---|
| 各管理页面独立，训练台保留只读资源状态 | pytest `test_management_tabs_have_independent_pages_and_forms`；四个 HTML 页面 | 每页导航指向服务器/凭证页且当前项唯一；服务器表单只在服务器页，凭证表单只在凭证页，工作台没有管理弹窗或生命周期操作；正常、兼容、回归 |
| 服务器表单可使用已有凭证并正确维护服务器 | Node `frontend/server-modal.test.cjs` 中校验、添加 POST、编辑 PATCH、凭证刷新、弹窗焦点/关闭用例；pytest 远程服务器静态契约用例 | 必填、主机/端口边界、GPU/CPU 切换、CPU 槽 0/256/257、加载失败重试、旧异步响应隔离；正常、边界、异常、兼容 |
| 服务器列表能刷新并呈现管理操作 | Node“服务器页合并登记与心跳并保留远程操作”“服务器目录空状态和失败可以重试”；pytest 生命周期/中断/日志静态契约 | 合并 worker 心跳与 SSH 登记、保留部署和生命周期按钮、转义恶意字段；工作列表/远程目录失败显示重试提示，后续刷新恢复；正常、空集合、异常、安全、回归 |
| 凭证独立维护且秘密不回显 | Node 覆盖 password/key/key_password × 新增/编辑、重复提交、失败保留、认证切换清理、删除冲突、保存期间旧目录响应；pytest SSH 凭证静态/API 契约 | 保存后秘密清空并留在本页；失败时保留输入且解锁；删除被引用凭证由服务端拒绝；正常、边界、异常、状态冲突、兼容 |
| 离开页面保护草稿并清理秘密 | Node 凭证草稿保护、编辑切换和离开清理；服务器弹窗取消、Escape、导航与 beforeunload 用例 | 取消离开保留草稿，确认后才导航；页面离开清空秘密，不重复确认；异常/状态冲突、安全 |
| UI 在运行环境可访问 | Safari 桌面检查 + Compose 健康及 HTTP 核对 | 服务器页、列表、添加弹窗、凭证选择、CPU 条件输入和未保存导航提示可见；两个页面和共享 JS 经运行服务提供；正常、部署、回归 |

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---|
| 相关 pytest API/页面用例 | 22 | 22 | 0 | 64 deselected | Python 3.12.15；包含服务器/凭证 API 兼容和管理页结构 |
| 候选快照完整 pytest | 527 | 503 | 0 | 24 | 执行用例通过率 100%；含后端全部套件 |
| 候选快照前端 Node 完整套件 | 55 | 55 | 0 | 0 | 页面交互、凭证与服务器弹窗测试 |
| 共享工作区前端回归 | 106 | 106 | 0 | 0 | 包括其他并行任务已有的前端用例；不作为隔离候选总数 |
| Compose 本地等价重建 | 3 服务 | 3 启动 | 0 | 0 | trainer、worker、frontend 使用已有依赖镜像重建并启动 |
| HTTP / 内容核对 | 4 项 | 4 | 0 | 0 | `/api/health`、服务器页、凭证页、共享 JS 哈希与工作区一致 |

完整 pytest 的 24 项跳过包括未配置真实 Redis 的 19 项、未配置 S3 的 2 项、Supervisor Redis E2E 1 项、真实 NVIDIA GPU/NCCL 的 2 项。保留既有 SQLAlchemy 与 Pandas 两条弃用警告。未执行真实远端服务器部署或真实凭证保存。

### 实际命令与结果

```bash
node --test frontend/*.test.cjs
node --check backend/static/remote-management.js
git diff --check
docker run --rm --pull=never --network none --mount type=bind,src=/tmp/mtp-management-tabs.WXcAB6/candidate,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-management-tabs-deps:local python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/mtp-management-tabs-candidate-final --tb=short -rs
docker run --rm --pull=never --network none --mount type=bind,src=/Users/xyzc/emdash/worktrees/ai-d973ef21/emdash-hot-boxes-clean-y9u6p,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-management-tabs-deps:local python -m pytest tests/test_api.py -q -p no:cacheprovider --basetemp=/tmp/mtp-management-tabs-related-final -k 'remote_server or remote_ssh or management_tabs' --tb=short
docker compose up --build -d
cat <<'YAML' | docker compose -f docker-compose.yml -f - up --build -d
services:
  trainer:
    build: &management_build
      context: ./backend
      dockerfile_inline: |
        FROM mtp-management-tabs-deps:local
        COPY app /app/app
        COPY static /app/static
        COPY sample_data /app/sample_data
        COPY Dockerfile worker.Dockerfile requirements-worker.txt /app/
        COPY init_db.sh start.sh /app/
        RUN chmod +x /app/init_db.sh /app/start.sh
  worker:
    build: *management_build
YAML
curl --fail --silent http://127.0.0.1:8000/api/health
curl --fail --silent http://127.0.0.1:8000/server-manager.html
curl --fail --silent http://127.0.0.1:8000/credential-manager.html
```

- 默认 Compose 从头构建未通过：PyTorch 下载收到不完整响应并出现 SSL record layer 错误，完整包的 SHA256 校验阻止构建继续。随后使用临时 Compose 覆盖，以本机已有 Python 3.12.15 依赖镜像（与仓库 `requirements.txt` 哈希一致）为基底，重新复制当前后端、静态文件和启动脚本；trainer、worker、frontend 构建并启动成功。未修改仓库 Compose、依赖版本或校验策略。
- `/api/health` 返回 `{"ok":true}`；两个页面可访问，线上 `remote-management.js` SHA256 与提交内容一致。服务继续运行在 `http://localhost:8000/`。
- Safari 已核对桌面服务器列表和添加弹窗、现有凭证选项、CPU 条件字段及未保存离开提示。凭证表单的认证切换和提交状态由 Node 测试验证。由于 Safari 窗口被并行验收任务切换，未完成窄屏浏览器实测；移动布局规则通过 CSS/页面结构检查，但不把它报告为浏览器实测通过。
- 调试及候选快照保存在 `/tmp/mtp-management-tabs.WXcAB6/`，提交后清除；该目录只用于隔离提交和测试，不纳入 Git。

### 已知限制与后续验证

- 默认在线 PyTorch 下载受当前网络 SSL 传输失败影响，本次使用同依赖版本的本地 Python 3.12.15 镜像完成等价 Compose 重建。网络稳定时建议补跑标准 `docker compose up --build -d` 从头构建。
- 24 项环境依赖测试需在配置真实 Redis、S3 和 NVIDIA GPU/NCCL 的环境补验；窄屏 Safari 实测也待补充。
- 回滚可撤销 `60a8c67` 并重新构建服务；没有数据迁移。本次未执行 `git push`。

## 本次变更测试结果：数据集按需预览（2026-10-29）

### Git 基准点与范围

- **Commit Hash**：`f88ceedd6c45891f3c8595868447ca2ff1bebb25`。
- **提交信息**：Show dataset previews on demand。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围与技术**：仅训练工作台 `backend/static/index.html` 和正式测试 `frontend/dataset-preview.test.cjs`；原生 HTML/JavaScript、Node.js 内置测试框架。无新增依赖、后端业务代码或仓库配置变更。
- **行为**：选择数据集只读取列表中的列元信息，默认不显示也不请求预览；点击“预览数据”后展示前五行及规模，再次点击收起；切换数据集清空旧预览。保留训练列选择，Hub 引用点击后才下载，失败可重试；请求序号阻止旧响应覆盖当前状态。上传通过数据集管理页提供，导航保留旧上传锚点，兼容现有配置定位逻辑。
- **提交隔离**：共享工作区同时存在其他任务修改；以 Git 基线生成只包含本次变更的页面快照，单独运行已有前端套件，使用独立索引提交；未提交其他任务的页面重构、后端修改或测试。

### 验收标准—测试用例

下表均为 Node 前端行为测试；只模拟 DOM 和 HTTP 边界，执行页面实际列表、列配置、预览和事件函数。标准输入为两组数据集元信息及包含七行的详情响应。

| 验收标准 | 前置条件、输入和可执行用例 | 预期与场景 |
|---|---|---|
| 默认隐藏，点击才请求数据 | `首屏预览默认隐藏…`、`加载列表与切换只使用列元信息…`；初次加载、切换列表 | 隐藏表格与统计；仅请求列表，目标列和时间列仍可选择；正常、兼容、回归 |
| 点击展开，再次点击收起 | `点击加载前五行与规模…`；详情返回七行，连续两次点击 | 仅五行数据、准确行列统计、ARIA 展开状态同步；收起清空内容；正常、边界 |
| 预览不重置训练配置 | `预览加载与完成不重置用户已选择…`；请求前及等待期间修改列 | 保留有效列选择；单列数据集的时间列为空；兼容、回归 |
| 收起和切换后不受旧响应影响 | `收起后的旧请求…`、`往返切换后旧请求…`；分别参数化成功和失败响应 | 保持收起或最新数据，不显示旧数据/错误；并发、异常、回归 |
| 空列表与远程引用处理正确 | `空列表禁用预览…`；Hub remote/error/ready 参数化用例 | 空列表禁用按钮、清空列和预览；Hub 点击前不请求详情，下载后补齐列；边界、兼容 |
| 失败可重试且文本安全 | `请求失败保留列选择…`、`特殊字符与缺失单元格…`；HTTP 异常、恶意标签、缺失值、零行零列 | 错误安全转义，重试成功恢复；数据按文本展示，缺失值为空；异常、安全、边界 |

### 执行命令与结果

| 阶段 / 实际命令 | 通过 | 失败 | 错误 / 跳过 | 结论 |
|---|---:|---:|---:|---|
| 修复前 `node --test frontend/dataset-preview.test.cjs` | 0 | 14 | 0 / 0 | 缺少预览按钮且选择数据集自动请求详情，复现问题；旧 Hub 分支在 DOM 边界额外产生异步拒绝提示 |
| 修复后同一命令 | 14 | 0 | 0 / 0 | 按需展示、列选择、旧响应、空列表、Hub、重试和转义全部通过 |
| 隔离版本 `node --test /tmp/mtp-dataset-preview-mGzTZ0/validation/frontend/*.test.cjs` | 41 | 0 | 0 / 0 | 14 项新测试、9 项联合训练和18项服务器弹窗测试全部通过 |
| 共享工作区首次 `node --test --test-reporter=tap frontend/*.test.cjs \| tail -12` | 72 | 18 | 0 / 0 | 服务器弹窗测试仍读取正在迁移的旧页面结构，与修改前失败一致 |
| 共享工作区最终 `node --test --test-reporter=tap frontend/*.test.cjs \| tail -12` | 106 | 0 | 0 / 0 | 其他并行任务更新其测试后，全前端套件通过，执行通过率 100% |
| `git diff --check`、独立索引 `git diff --cached --check`、页面脚本 `vm.Script` 解析 | 通过 | — | — | 无空白或脚本语法错误 |

- 隔离快照首次生成时，临时脚本错误截断了 `updateStart` 等已有函数，导致23项测试失败；修正快照生成范围后41项全部通过。该错误仅存在于临时快照，未提交。
- **完整容器重建未通过**：三次执行 `docker compose up --build -d`。前两次后端安装 PyTorch 时下载约7.1/454 MB，约360秒后抛出 `ssl.SSLError: [SSL] record layer failure`；第三次切换到镜像中的 483.7 MB PyTorch 包后，等待约60秒仍无新增下载进度，手动停止（退出码130）。不能将完整 Compose 构建标记通过。
- **前端部署通过**：上述构建中的 frontend 镜像成功生成；执行 `docker compose up -d --no-build --no-deps frontend` 成功重建启动。Node `fetch` 断言 `/healthz` 为 `ok`、页面包含预览按钮与默认隐藏容器、上传链接指向管理页且原上传控件不存在，全部通过；最终 `/api/health` 返回 HTTP 200。一次在容器重建完成前执行的资源断言读到旧资源而失败，等待启动完成后同一断言通过。
- **Python 3.12 等价验证通过**：使用现有运行镜像启动临时容器 `mtp-dataset-preview-api-validation`，只读挂载本地 `backend/`，以 `USE_DATABASE=false STORAGE_BACKEND=local REDIS_URL= DATA_DIR=/tmp/dataset-preview python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` 启动。`docker exec mtp-dataset-preview-api-validation python -c 'import sys, urllib.request; assert sys.version_info[:2] == (3,12); print(sys.version.split()[0]); print(urllib.request.urlopen("http://127.0.0.1:8000/api/health").read().decode())'` 返回 Python **3.12.15** 与 `{"ok":true}`，与目标容器主版本无差异。
- **实际浏览器验证通过**：隔离容器仅映射 `127.0.0.1:18017`，通过 `POST /api/datasets/sample/classification_demo.csv` 与 `regression_demo.csv` 创建临时示例；Safari 验证初始没有数据表且目标列已填充，点击显示五行及“260 行 · 5 列”，再次点击表格和统计消失。常用入口一度返回502，后续健康检查已恢复200。

### 限制、重测与清理

- **未执行**：后端完整 pytest、真实 Hub 网络下载和独立窄屏视觉验收。此次仅前端修改，按已确认测试范围不运行训练全套测试；Hub 网络边界以参数化测试隔离。浏览器验收期间共享 Safari 被其他任务切换，未继续调整窗口；切换数据集与请求竞态已由自动化用例覆盖。
- **仍需验证**：网络恢复后重新执行 `docker compose up --build -d`，确认所有服务从默认依赖源构建成功；补做窄屏下的展开、横向滚动及真实 Hub 首次下载。当前前端行为、106 项前端测试与替代运行验证通过，不代表完整容器化验证通过。
- **重测触发**：修改数据集列表元信息、详情 API、列配置、预览状态或相关页面事件时重跑本套件及全部前端测试；涉及后端业务时按项目规定执行完整 Python 套件并更新业务基准。
- **回滚**：撤销 `f88ceed` 后统一重建；无数据迁移。
- **清理**：临时快照与独立索引位于 `/tmp/mtp-dataset-preview-mGzTZ0/`，交付前删除；临时容器停止后自动删除，两个测试数据集仅存在于其临时数据目录，随容器销毁。保留正式测试，未提交运行产物；共享工作区其他任务改动保持原状，未执行 git push。

## 本次变更测试结果：训练数据内存优化（2026-10-29）

### Git 基准点与范围

- **Commit Hash**：`500826aed29f34a3bcdb8b4eb0452378046dc940`。
- **提交信息**：Reduce training data memory overhead。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围与技术**：Python 3.12.15、Pandas、NumPy、PyTorch；仅修改 `trainer.py`、`llm_trainer.py` 及对应正式测试，无新增依赖、数据库迁移或仓库配置修改。CSV/JSONL 按 max_samples 限量读取，Hub 按上限迭代；编码后释放语料。表格特征提前使用训练原有的 float32，保留原精度中位数，及时释放 DataFrame 与全量数组，原地标准化独占矩阵。
- **验证隔离**：内存与行为对比以 `01658e9` 为基线，candidate 为该基线加本次补丁；另对包含同期联合训练、Hub 导入及前端改动的冻结 integration 快照做完整回归。提交的两个业务文件与 integration 快照及重建后 Worker 容器的 SHA256 一致；仅提交本任务四个文件的补丁。测试后其他会话继续产生的任务管理/前端改动不属于本次验收。

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 错误 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| 修复前 LLM 读取回归 | 10 | 1 | 9 | 0 | 0 | 复现超过上限仍解析尾部、非法上限未前置检查、Hub 迭代超限 |
| 修复前数据生命周期回归 | 4 | 0 | 4 | 0 | 0 | 四类训练均保留原始 DataFrame |
| 最终训练器专项 | 60 | 60 | 0 | 0 | 0 | 3.46 秒，表格训练与 LLM 全套 |
| 最终独立快照完整套件 | 430 | 408 | 0 | 0 | 22 | 61.98 秒，已执行用例通过率 100% |
| 工作区联合训练兼容专项 | 108 | 106 | 0 | 0 | 2 | 107.32 秒，含分类/回归/时序、LLM 四模式、暂停续训、空尾批 |
| 冻结工作区完整套件 | 526 | 502 | 0 | 0 | 24 | 162.24 秒，已执行用例通过率 100%，含跳过的总通过比例 95.44% |
| 修改前后业务产物对比 | 4 | 4 | 0 | 0 | 0 | 分类/回归/聚类/时序的预处理、权重张量、逐 epoch/最终指标、非法字段预测逐项完全相等 |
| 容器健康验证 | 3 | 3 | 0 | 0 | 0 | 隔离 Python 3.12 Uvicorn、Compose API、前端 healthz |

**过程中的问题**：初次专项 51 通过、1 失败，新测试误将 Pandas 的 NaN 预期为 None；修正为检查嵌套消息原样保留和原有 NaN 表示。首次完整测试 405 通过、1 失败、22 跳过，新测试的类别列需明确指定既有支持的 object 类型，避免 Pandas 3 自动推断为 str；修正夹具后全部通过。没有修改类别识别业务或弱化已有测试。四文件自定义执行顺序曾出现 3 项 Worker 竞争失败；原始基线相同顺序也出现 2 项失败，均涉及残留嵌入 Worker 消费队列。按项目标准顺序执行完整套件后相关测试全部通过，未修改或跳过这些断言。临时产物对比脚本最初漏建任务目录，补齐脚本前置条件后四类业务对比通过。

**关键模块覆盖**：训练器专项 60/60；完整套件覆盖发起训练、状态、日志、指标和模型产物，固定 seed 重复训练、暂停/续训、分类/回归/聚类/时序预测、LLM 预训练/SFT/DPO/PPO、旧扁平与新结构数据集、队列/租约和远程 Worker。联合训练专项覆盖多进程实际训练、权重加权、故障、超时和空尾批。SQLAlchemy declarative_base 与 Pandas xlrd 的两条既有弃用警告保留。

### 验收标准—测试用例映射

| 验收标准 | 层级、前置条件与输入 | 业务预期与结果 | 场景 |
|---|---|---|---|
| 限量读取的内容和顺序正确 | 参数化集成 `test_llm_loading_preserves_selected_rows`：CSV/JSONL/NDJSON/JSON，1/3/8 条、上限 3、中文/引号/换行 | 返回原样的前 min(N,3) 条；12/12 通过 | 正常、边界、格式兼容 |
| 上限外不物化、上限内不吞错误 | `test_llm_sample_limit_stops_before_unused_records`、`test_llm_loading_rejects_malformed_selected_records`、`test_llm_jsonl_blank_lines_do_not_consume_sample_limit` | 上限外损坏行不解析，上限内明确失败，空行不占额度；7/7 通过 | 边界、异常、新分支 |
| 空值与非法输入可解释 | `test_llm_loading_rejects_invalid_sample_limit`、`test_llm_loading_rejects_empty_corpus`、`test_llm_loading_reports_missing_file_and_accepts_maximum_limit` | 非法/越界上限前置拒绝；空语料、缺失文件报错；1000000 合法上限可读取小文件；9/9 通过 | 空值、最大最小值、资源不存在 |
| Hub 与嵌套消息保持兼容和安全 | `test_hub_loading_limits_before_materializing_rows` 使用真实 Dataset/受控迭代器；`test_llm_loading_preserves_nested_messages`；既有 Hub ID/remote-code 安全测试 | 不消费超过上限的记录；嵌套消息/空值保留；非法仓库 URL 拒绝，trust_remote_code=False；通过 | 兼容、安全、异常 |
| 训练中释放原始数据 | `test_training_releases_source_frames_before_epochs`：四类真实训练、弱引用观察；`test_llm_training_releases_raw_records_after_encoding`：真实预训练/SFT | epoch/切分时原始数据已可回收，指标有限且 best.pt/模型包非空，重复容错预测一致；6/6 通过 | 生命周期、训练链路、回归 |
| 特征内存减半而训练精度保持 | `test_feature_memory_reduction_preserves_precision_metadata_and_source`：缺失、正负无穷、常量、类别；另四类训练前后产物对照 | 特征值等于旧 float32 矩阵，中位数/列顺序保留，输入表不变；单元素 4 bytes，产物对照完全一致 | 预处理一致性、边界、兼容 |
| 实际内存改善 | 独立 Linux 进程、相同依赖镜像/CPU 线程、固定语料/宽表/seed，各运行三次 | RSS 中位数如下；样本计数、val_loss、accuracy 和权重产物保持一致 | 性能、可复现、真实训练 |

### 内存实测

环境为同一 Linux ARM64 / Python 3.12.15 镜像，PyTorch 2.14.1+cu130 实际使用 CPU，OMP/MKL/torch 均单线程。峰值取 Linux `resource.getrusage(RUSAGE_SELF).ru_maxrss`（KiB），阶段常驻量取 `/proc/self/statm`；每个负载独立进程重复三次，表内为中位数。

| 负载与测量位置 | 修改前 | 修改后 | 减少 |
|---|---:|---:|---:|
| LLM 读取 60000 条长文本 CSV，max_samples=100；进程峰值 | 908.48 MiB | 584.98 MiB | 35.61% |
| 60000 行 × 160 数值特征分类训练；预处理结束、模型构建后常驻内存 | 942.88 MiB | 733.32 MiB | 22.23% |
| 同一宽表训练完整进程峰值 | 1078.27 MiB | 1056.75 MiB | 2.00% |

原始三次 KiB：LLM 基线 [930280, 931832, 928104]，修改后 [597428, 599024, 599028]；宽表峰值基线 [1108556, 1104148, 1095524]，修改后 [1081264, 1084224, 1082116]；模型构建后 RSS 基线 [969496, 965512, 956656]，修改后 [747876, 750920, 754120]。首次无阶段采样的基线也已测得 LLM 峰值 [930324, 927728, 929016]，宽表 [1107804, 1095036, 1099212]，趋势一致。

宽表使用固定 seed=42、batch_size=512、hidden_size=8、1 epoch，48000/12000 训练/验证行；所有运行 val_loss=1.1020245555241903、accuracy=0.3274166666666667，best.pt 非空。宽表的最高峰主要来自 CSV 解析，所以不能把阶段内存下降 22% 宣称为全程峰值下降 22%。LLM 数字是语料读取阶段，不代表任意大模型训练的总内存降幅。

### 实际执行命令与运行环境

临时根目录 `/private/tmp/mtp-memory-W31n7K/` 包含 base、candidate、integration 三份隔离快照及验证脚本，完成后清理。以下命令均实际执行；测试只写容器临时目录。

```bash
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-memory-W31n7K/candidate,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-memory-deps:validation python -m pytest tests/test_trainer.py tests/test_llm_trainer.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/memory-targeted-final
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-memory-W31n7K/candidate,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-memory-deps:validation python -m pytest tests/ -v --tb=short -p no:cacheprovider --basetemp=/tmp/memory-full-final
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-memory-W31n7K/integration,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-memory-deps:validation python -m pytest tests/test_parallel.py tests/test_trainer.py tests/test_llm_trainer.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/memory-integration
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-memory-W31n7K/integration,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-memory-deps:validation python -m pytest tests/ -q --tb=short -p no:cacheprovider --basetemp=/tmp/memory-integration-full
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-memory-W31n7K,dst=/workspace,readonly --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env PROFILE_MEMORY=1 mtp-memory-deps:validation python /workspace/memory_bench.py /workspace/base
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-memory-W31n7K,dst=/workspace,readonly --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env PROFILE_MEMORY=1 mtp-memory-deps:validation python /workspace/memory_bench.py /workspace/candidate
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-memory-W31n7K,dst=/workspace,readonly --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-memory-deps:validation python /workspace/equivalence.py
docker compose up --build -d
docker compose -f docker-compose.yml -f /private/tmp/mtp-memory-W31n7K/compose-validation.yml up --build -d
docker compose ps
curl --fail --silent http://127.0.0.1:8000/healthz
git diff --cached --check
git status --short
```

- **默认从零构建未通过**：Aliyun 的 454 MB torch 下载在约 307 秒时仅收到 6.6 MB，SHA256 校验拒绝，进入第二轮重试；停止该次构建，退出 130。未降低校验要求或改动仓库下载源。
- **等价 Compose 重建通过**：临时覆盖以现有依赖镜像 `sha256:8eb9862184e78e09be326b0f239a4bd68ab8bae94a1b67871d38c1154be03496` 为基底，重新复制当前源码、静态资源和启动脚本；基底与仓库 requirements.txt SHA256 均为 `7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`。trainer/worker/frontend 统一重建启动成功，API /api/health 返回 200 和 {"ok":true}，前端 healthz 返回 ok。临时覆盖首次因 dockerfile:null 不符合 Compose schema 被拒绝，移除多余字段后成功；仓库 Compose 未改动。
- **本地等价启动通过**：宿主机无 Python 3.12，使用一次性无网络 Python 3.12.15 容器，在 backend 目录以 USE_DATABASE=false、STORAGE_BACKEND=local、临时 DATA_DIR、REDIS_URL 为空执行 `python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000`。首次健康请求早于启动完成而连接拒绝，启动完成后业务断言通过；没有使用旧 Python 或语法检查替代验证。容器已停止并删除。
- **部署源码核对**：Worker 内 trainer.py SHA256=`7e2fe99c94d18bb45b1ebee34b03752ce68e972d0622ac57577686c8241bb6c3`，llm_trainer.py SHA256=`80554d6b3cdc69e598cd96a891057a0dc2cefbb638557c9839f389f8cc9ecd89`，均与本次提交和兼容快照相同。未执行真实远程服务器更新。

### 限制、后续验证与清理

- 24 个环境跳过项为 19 个真实 Redis、2 个 S3、1 个 Supervisor E2E、2 个真实 GPU/NCCL 场景；本次未配置对应环境补跑。未验证 GPU 显存下降、x86_64 或其他依赖版本，不能宣称所有服务器的内存降幅相同。
- CSV/JSONL 只对选中记录进行类型推断和解析，日志明确说明采样边界；上限外损坏记录不会导致本次样本训练失败。普通 JSON 仍须解析整体结构，仅避免把未选中的行物化为字典；Hub 数据构建/下载策略未改为 streaming，只限制 Python 记录物化。单条超长文本、全量表格 CSV 解析和模型/优化器本体的峰值仍然存在。
- 继续优化时应优先测量实际远程任务的数据解析峰值；流式全量表格训练、模型量化、混合精度、并发槽数量不在本次实现范围。修改读取规则、dtype、标准化、训练/预测、联合训练或数据布局后需重跑对应专项、完整套件与固定内存负载；稳定网络下补验默认 Compose 从零构建。
- 回滚可撤销 `500826a` 并重新构建平台/Worker；无数据迁移。临时快照、脚本和补丁位于上述临时目录，交付前清除；一次性测试容器及数据自动删除，临时依赖镜像标签删除，运行服务保留。本任务代码和报告分开提交，未执行 git push，其他会话的未提交修改保留。


## 本次变更测试结果：数据集卡片标题挤压修复（2026-10-29）

### Git 基准点与范围

- **Commit Hash**：`630aa3c87c18689505bff1b410bf2200aa9b0549`。
- **提交信息**：Fix dataset card title layout。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围与技术**：原生 HTML/CSS；仅修改 `backend/static/app.css` 的数据集页标题和操作区规则，以及 `backend/static/dataset-manager.html` 的 CSS 缓存版本。没有修改后端、依赖、配置、数据或第三方代码。
- **根因与行为**：桌面窄卡片中标题与不收缩的四按钮区域共用一行，标题宽度可被挤到接近 0。修复后标题占满卡片内容宽度，按钮另起一行、左对齐并自动折行；长名称完整换行。缓存版本从 `20261029-hub-1` 更新为 `20261029-dataset-layout-1`。
- **验收边界**：独立回归副本由 `git archive a7bee30` 加本次两个文件构成。完整 pytest 的业务基准为 `a7bee30`，本节不将其后其他任务的联合训练、内存优化等后端提交宣称为已完整回归。本次提交中的两个前端文件与该验收副本一致。并行工作期间其他任务继续修改这两个文件，包括卡片结构、导航和新的缓存版本；这些修改被保留，没有夹带到本次提交。

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 错误 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| Chromium 布局与交互，修复前 | 27 | 19 | 8 | 0 | 0 | 预期失败；桌面标题宽度接近 0，窄屏标题未独占可用宽度 |
| 同一组 Chromium 用例，修复后 | 27 | 27 | 0 | 0 | 0 | 四视口、普通/边界数据、按钮命中、详情/处理/搜索通过 |
| 训练页 CSS 作用域回归 | 4 | 4 | 0 | 0 | 0 | 每个视口逐项比较 339 个节点的位置、尺寸和关键计算样式，无变化 |
| 独立版本前端完整 Node 套件 | 18 | 18 | 0 | 0 | 0 | 已提交版本的全部前端正式测试 |
| 独立版本后端完整 pytest | 434 | 412 | 0 | 0 | 22 | Python 3.12.15，65.21 秒；已执行用例通过率 100%，总通过比例 94.93% |
| 共享工作区前端套件复核 | 47 | 38 | 9 | 0 | 0 | 混入其他任务正在编写的训练工作台测试，不能作为本次独立验收结果 |
| 部署后现场卡片布局 | 4 | 4 | 0 | 0 | 0 | 1600/1024/390/320px，现场 3 张卡片均未挤压标题或溢出 |
| 部署及本地等价服务 HTTP 核对 | 2 组 | 2 组 | 0 | 0 | 0 | 每组检查健康接口及 HTML/CSS；核对范围见下文 |

后端 22 项既有跳过涉及未配置 `MTP_TEST_REDIS`、`MTP_TEST_S3` 的真实 Redis/S3/Supervisor 验证，不计为通过。保留 SQLAlchemy `declarative_base` 和 Pandas `xlrd` 两条既有弃用警告。没有删除、跳过或弱化既有测试。

**共享工作区失败分析**：首次运行全量 Node 套件时，训练工作台交互测试正在由其他任务补充，出现未实现函数/行为相关失败；再次采样为 47 项中 38 通过、9 失败。失败均位于当时未跟踪的 `frontend/workbench-interactions.test.cjs`，涉及搜索/状态筛选、任务卡片复用、旧详情成功与失败响应、同任务详情乱序、日志跟随、并发日志去重、预测模板旧响应和键盘标签切换。它们不属于本次 CSS/缓存修复，未擅自修改；相关任务应在开发完成后重新验收。已提交基准的全部 18 项 Node 测试通过。本节不宣称共享工作区整体测试通过。

### 验收标准—测试用例映射

| 验收标准 | 层级与前置条件 | 输入、预期结果与副作用 | 场景 |
|---|---|---|---|
| 标题不被按钮挤压 | Chromium；真实页面代码，仅数据 API 在网络边界隔离 | 远程 `lhoestq/demo1` 四按钮、普通 CSV 三按钮；标题宽度等于内容宽度、操作在标题下方、短仓库名单行、名称文本完整 | 正常、截图缺陷回归 |
| 长名称和空值可读 | Chromium；同一布局断言参数化执行 | 超长英文仓库名、超长中文名称、空名称、0 行/空字段、已缓存 Hub、含 HTML 字符名称；完整文本或“未命名数据集”可见，按钮数量正确，无卡片横向溢出 | 边界、默认行为兼容 |
| 响应式和按钮可操作 | Chromium；上述两组数据各跑四种视口 | 1600、1024、390、320px；页面无横向溢出，所有按钮边界位于卡片内，浏览器 trial click 命中成功 | 桌面/手机、正常/边界 |
| 详情、处理和筛选保持可用 | Chromium；API fixture 隔离现场数据 | 点击本地详情显示正确名称并请求对应 ID；处理打开操作面板；搜索只保留匹配卡片，无匹配显示空状态 | 交互、空集合、回归 |
| 训练工作台不受样式影响 | Chromium；固定同一已提交 HTML，禁用页面脚本以隔离其他逻辑 | 修复前后 CSS 在四视口下对所有 339 个 DOM 节点比较位置、尺寸和关键计算样式，结果相同 | 样式作用域、兼容回归 |
| 更新应用到运行服务 | Compose 与 HTTP/Chromium | 健康接口返回 `ok: true`；线上 CSS 包含本次规则、HTML 使用非旧缓存版本；四视口现场卡片通过几何断言 | 部署、运行回归 |

浏览器测试使用系统已有 Playwright/Chromium，不新增依赖。API mock 只隔离网络，页面渲染、CSS、搜索和事件分派均运行真实代码；不对现场数据执行删除、下载、训练或处理写操作。

### 实际执行命令

```bash
# 项目根目录；临时副本与脚本在验证结束后清理
git archive a7bee30 | tar -x -C /tmp/mtp-dataset-layout.ysficK/isolated
cp backend/static/app.css backend/static/dataset-manager.html /tmp/mtp-dataset-layout.ysficK/isolated/backend/static/
PHASE=before node /tmp/mtp-dataset-layout.ysficK/check-layout.cjs
PHASE=after node /tmp/mtp-dataset-layout.ysficK/check-layout.cjs
node /tmp/mtp-dataset-layout.ysficK/check-workbench.cjs
node --test frontend/*.test.cjs
node --test /tmp/mtp-dataset-layout.ysficK/isolated/frontend/*.test.cjs
docker run --rm --pull=never --network none --mount type=bind,src=/tmp/mtp-dataset-layout.ysficK/isolated,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 f72a45f60ca7 python -m pytest tests/ -v -p no:cacheprovider --basetemp=/tmp/mtp-dataset-layout-tests --tb=short
docker compose up --build -d
node /tmp/mtp-dataset-layout.ysficK/check-live.cjs
curl -fsS http://127.0.0.1:18087/api/health
git diff --check
```

先执行 before，再改动两个文件并覆盖副本、执行 after。共享工作区第二次 Node 采样通过 Node `spawnSync('node', ['--test', ...files])` 调用同一完整前端套件，仅筛选显示汇总和失败用例名，不改变执行或断言。

### 构建、等价验证与限制

1. 默认 `docker compose up --build -d` 的阿里云 PyPI 镜像下载失败：454MB PyTorch 包在约 307 秒后只得到 6.6MB，随后 SHA256 不匹配。停止重复下载，命令退出 130；**默认 Dockerfile 联网构建未通过**。
2. 使用已有 Python 3.12.15 依赖镜像复用依赖，容器内外 `requirements.txt` SHA256 均为 `7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`。初次引用的测试镜像在并行工作中已被移除，临时覆盖构建随即取消；重新核对并固定当时的运行镜像 `e2f8321e2aba` 为 `mtp-dataset-layout-base:validation` 后成功重建。没有修改仓库 Compose、Dockerfile 或依赖清单。
3. 成功构建并启动 trainer、worker、frontend 的实际命令如下，退出 0；这是复用已有依赖的 Compose 构建，不代表默认联网安装成功：

```bash
docker tag e2f8321e2aba mtp-dataset-layout-base:validation
cat <<'YAML' | docker compose -f docker-compose.yml -f - up --build -d
services:
  trainer:
    build: &dataset_layout_build
      context: ./backend
      dockerfile_inline: |
        FROM mtp-dataset-layout-base:validation
        COPY app /app/app
        COPY static /app/static
        COPY sample_data /app/sample_data
        COPY Dockerfile worker.Dockerfile requirements-worker.txt /app/
        COPY init_db.sh start.sh /app/
        RUN chmod +x /app/init_db.sh /app/start.sh
  worker:
    build: *dataset_layout_build
YAML
```

4. 另执行本地源码等价启动验证。宿主机没有可调用的 `python3.12`，为遵守 Python 3.12 要求，在一次性容器内只读挂载独立源码，运行相同 `uvicorn` 入口、`USE_DATABASE=false`，临时数据置于容器 `/tmp`；没有用 Python 3.9 代替，也没有用此验证替代 Compose 构建：

```bash
docker run --rm --name mtp-dataset-layout-smoke --pull=never -p 127.0.0.1:18087:8000 --mount type=bind,src=/tmp/mtp-dataset-layout.ysficK/isolated,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env DATA_DIR=/tmp/mtp-dataset-layout-data --env USE_DATABASE=false --env STORAGE_BACKEND=local --env REDIS_URL= --env EMBEDDED_WORKERS=0 mtp-dataset-layout-base:validation python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

5. 独立源码服务健康检查通过；`/static/dataset-manager.html`、`/static/app.css` 与验收副本逐字一致。首次误用前端根路径返回 404，改用后端实际 `/static/` 挂载后通过。现场 8000 服务整文件相等检查曾因其他任务同时调整导航和页面结构失败，随后按本次变更边界逐项核对 CSS 规则及缓存版本，并通过四种视口的实际卡片布局验证；没有宣称现场整页与独立版本相同。现场数据为三张本地卡片，远程四按钮场景由独立浏览器 fixture 覆盖。
6. 已检查修复前后桌面截图、部署后桌面与 320px 卡片截图。没有执行 Safari/Firefox、手机实机、真实 Hub 下载或删除；本次不修改这些业务路径。真实 Redis/S3/Supervisor 环境验证、后续其他任务变更的完整验收及默认联网构建仍需各自补验。默认构建建议网络恢复后重新执行原命令。
7. **重测触发与回滚**：调整卡片结构、标题/按钮布局、响应式规则或缓存方式时，重跑上述浏览器场景及前端测试；业务代码变化按项目规则完整回归。可撤销 `630aa3c` 回滚本次两文件变更并重新构建；后续其他任务继续修改相同区域时应先检查冲突，无数据迁移。

**提交与清理**：本次两个文件通过独立暂存索引仅提交已验收差异，没有回退或提交同文件中其他任务的修改；报告另行提交。临时目录 `/tmp/mtp-dataset-layout.ysficK` 中的脚本、截图、独立副本及暂存索引在交付前删除。一次性测试容器自动销毁，等价验证容器已停止并移除，临时依赖镜像标签已删除。未提交运行时产物、未执行 git push；其他任务的未提交/未跟踪文件保留。

## 本次变更测试结果：异构多节点联合训练（2026-10-29）

### Git 基准点与实现范围

- **Commit Hash**：`f5ba35f916635f9b8c0e370916984e949ab591e8`。
- **提交信息**：Add heterogeneous synchronous multi-node training。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围**：新增 `parallel.py` / `parallel_worker.py`，接入表格/时间序列/LLM 训练器、Worker 槽预留及租约、任务 API 与请求校验、远程互联地址与 host 网络部署、训练页面、README 和正式测试。未新增依赖、迁移数据库或修改仓库 Compose 配置；工作区预先存在的 Dockerfile 下载重试修改不属于本提交。
- **技术环境**：Docker Desktop / Linux ARM64，Python **3.12.15**，PyTorch **2.14.1+cu130**（实际以 CPU 执行）、Gloo、Redis 7、SeaweedFS S3、FastAPI/Pydantic、原生 HTML/JavaScript。未使用宿主机旧版 Python 执行业务验证。
- **实现行为**：2～32 个计算槽按不同本地批大小处理数据，按样本数或有效目标 token 数加权汇总梯度，主节点更新并广播唯一模型；全 CUDA/NCCL 组使用 NCCL，CPU/混合组使用 Gloo。支持分类、回归、时间序列、LLM 预训练/SFT/DPO/PPO；KMeans 保持单节点。暂停保存完整 epoch，取消/故障终止本轮，原成员整组恢复；表格产物 `best.pt`，LLM 产物 `best_model.zip`。
- **验证隔离**：测试期间其他会话又修改了训练器和前端。最终从 `a7bee30` 导出独立快照，再覆盖本任务已经部署的精确源码和正式测试。提交前逐文件比较暂存 blob 与快照，16 个提交文件全部一致；共享工作区的其他修改保留，未混入本提交。快照与临时 Compose 文件位于 `/private/tmp/mtp-parallel-validation-PURH0f/`，完成后清除。

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 错误 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| 新增进展回归，修复前 | 3 | 0 | 3 | 0 | 0 | 复现参与 rank 的进展计数为 0，长 epoch 会被父进程误判无进展 |
| 新增页面回归，修复前 | 9 | 3 | 6 | 0 | 0 | 4 个 LLM 分支覆盖了 CPU 资源设置；缺少 Gloo 校验；编辑时汇总不刷新 |
| 首轮联合/远程相关套件 | 177 | 175 | 0 | 0 | 2 | 初始实现验证，75.31 秒 |
| 扩展联合/远程相关套件 | 198 | 196 | 0 | 0 | 2 | 修复及边界测试通过，87.30 秒；随后补充 LLM 续训用例 |
| 分类与 LLM 暂停续训 | 2 | 2 | 0 | 0 | 0 | 与不中断训练逐参数、逐指标一致，24.17 秒 |
| 最终独立快照完整 pytest | **488** | **464** | **0** | **0** | **24** | **140.45 秒**；已执行用例通过率 100%，含跳过的总通过比例 95.08% |
| Redis/S3 环境补测 | 33 | 33 | 0 | 0 | 0 | 真实隔离服务，2.97 秒；覆盖上述 19 个 Redis、2 个 S3 跳过项，另含已有同类回归 |
| 独立快照前端测试 | 27 | 27 | 0 | 0 | 0 | 联合训练 9 项、既有服务器弹窗 18 项 |
| 跨容器联合训练 | 1 | 1 | 0 | 0 | 0 | 两个独立 worker、真实 Redis/S3、eth0 上 Gloo 通信、完整 API 业务断言 |
| Python 3.12 本地等价 API | 1 | 1 | 0 | 0 | 0 | 隔离 Uvicorn、禁用数据库/Redis、临时本地存储，健康接口断言通过 |

最终完整套件中的联合训练模块为 **46 通过 / 2 个真实 GPU 用例跳过**，远程管理为 **151 通过**；其余数据层、训练器、调度/API 和既有功能的可执行测试均通过。共享工作区完整套件也得到 464 通过、24 跳过（160.03 秒），最终验收以上述独立快照为准。保留 SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 两条既有弃用警告；没有删除、跳过或弱化已有有效测试。

### 验收标准与可执行用例映射

以下联合训练用例位于 `backend/tests/test_parallel.py`；使用真实 PyTorch 计算与通信，只有外部进程启动/网络加载边界按需隔离。

| 验收标准 | 测试层级、前置条件与输入 | 可执行用例 | 预期结果 / 场景 |
|---|---|---|---|
| AC1 一组只更新一个模型，批大小可不同 | CPU 多进程；7 条样本，批大小 (2,1)/(1,5)/(3,3,3) | `test_weighted_gradients_equal_global_batch_with_empty_tail` | 各 rank 每一步权重均等于串行全局批计算；尾批不补齐、不丢失；全部 rank 上报进展。正常/边界 |
| AC2 CPU/GPU 兼容与能力选择 | 注册 CPU、不同 GPU 名称及软件版本；实际 CPU 进程 | `test_member_validation_and_backend_selection`、`test_incompatible_members_are_rejected`、`test_cpu_slot_hides_inherited_gpu_visibility` | 选择 NCCL/Gloo 正确；版本/厂商/能力不兼容及禁用/离线节点拒绝；CPU 槽隐藏 CUDA。兼容/异常/资源权限 |
| AC2 真实异构硬件 | 需要至少一张或两张 NVIDIA GPU | `test_real_cuda_cpu_mixed_training`、`test_real_nccl_training` | 检查真实混合通信及 NCCL 参数一致性；本机无 GPU，**未执行** |
| AC3 所有批准的梯度训练任务 | 微型本地模型和数据，双 CPU rank，包含空尾批 | `test_training_artifacts_metrics_and_predictions`、`test_parallel_llm_finetuning_and_empty_tail` | 分类/回归/时间序列/预训练/SFT/DPO/PPO 指标有限且产物可用；PPO value head、全局优势统计参与更新。正常/边界 |
| AC4 API 全链路与旧数据兼容 | flat/structured 两种数据布局 × 分类/回归 | `test_workers_complete_one_joint_job` | POST 训练→轮询→日志→accuracy/R²/RMSE→唯一 best.pt；预测稳定，缺失/非法数值与显式训练中位数填充的结果一致。端到端/兼容/回归 |
| AC5 暂停、恢复、复现 | 固定 seed、成员、批大小；分类及 LLM 在 epoch 1 暂停 | `test_pause_resume_matches_uninterrupted_and_repeatable` | 恢复到 epoch 3 后参数和指标与完整运行一致；恢复起点和 checkpoint 正确。控制/可复现 |
| AC6 异常与有界退出 | 数据不一致、rank 退出/取消/NaN、成员缺席超时 | `test_different_data_is_rejected_by_all_ranks`、`test_rank_failure_stops_the_group_without_publishing_model`、`test_missing_rank_times_out_and_cleans_subprocess_files` | 明确失败或整组取消，无错误模型，子进程及通信文件清理。异常/超时 |
| AC7 槽位互斥与状态隔离 | 某槽被占用；主节点 token 被替换；失败组请求单槽重试 | `test_group_reservation_rolls_back_if_member_is_busy`、`test_member_does_not_renew_or_overwrite_replaced_leader`、`test_group_retry_cannot_be_assigned_to_single_worker` | 部分预留回滚，旧成员不能续租/覆盖新主节点，重试保持原组。冲突/资源权限 |
| AC8 参数边界及单节点兼容 | 重复/非法 ID、1/33 节点、0/超大批大小、超时边界、KMeans、LLM GPU 限制 | `test_invalid_parallel_requests_are_rejected`、`test_parallel_schema_limits_and_unsupported_tasks`、`test_parallel_schema_accepts_upper_bound`、两项 `test_api_*` | 明确拒绝非法请求，32 节点上界合法；单节点 LLM 仍默认要求 GPU，联合 LLM 可用 CPU。边界/恶意输入/兼容 |
| AC9 远程配置与已有部署兼容 | Bash 引导脚本；start/restart × bridge/host；地址设置/清空 | `test_remote_workers.py::test_distributed_host_requires_host_network_before_start`、`test_optional_distributed_host_configuration_can_be_cleared` | bridge 配置联合地址时提示先部署；host 可运行；清空恢复旧配置文本。配置/兼容 |
| AC10 页面操作与参数提交 | 实际页面函数；DOM/网络边界替身 | `frontend/parallel-training.test.cjs` | 节点批大小、资源限制、LLM CPU 载荷、掉线/停用/Gloo 缺失校验、聚类回退和编辑汇总正确。前端/回归 |

平台现有产品未提供登录/角色系统，本次未新增身份认证流程；安全验证覆盖恶意成员 ID、禁用资源、租约越权、旧执行轮次隔离、JSON 通信与已有 API/SSH 回归，不将未存在的登录权限体系记为已验证。

### 实际执行命令与环境

所有 pytest 均通过 Docker 中 Python 3.12 执行，公共运行参数为 `--rm --pull=never`、只读绑定项目到 `/workspace`、工作目录 `/workspace/backend`、`PYTHONDONTWRITEBYTECODE=1`、`OMP_NUM_THREADS=1`、`MKL_NUM_THREADS=1`。本地套件使用 `--network none`；测试产物仅写容器临时目录。关键完整命令：

```bash
docker run --rm --pull=never --network none --mount type=bind,src=/Users/xyzc/emdash/worktrees/ai-d973ef21/emdash-hot-boxes-clean-y9u6p,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_parallel.py tests/test_remote_workers.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-parallel-targeted-final
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-parallel-validation-PURH0f/snapshot,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v -rs --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-parallel-isolated
docker run --rm --pull=never --network mtp-parallel-check --mount type=bind,src=/Users/xyzc/emdash/worktrees/ai-d973ef21/emdash-hot-boxes-clean-y9u6p,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env MTP_TEST_REDIS=redis://mtp-parallel-check-redis:6379/0 --env MTP_TEST_S3=mtp-parallel-check-s3:8333 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_coordination.py tests/test_storage.py tests/test_worker.py -q -k 'redis or heartbeat_recovers or s3' --tb=short -rs -p no:cacheprovider --basetemp=/tmp/mtp-parallel-services
node --test frontend/parallel-training.test.cjs
node --test frontend/*.test.cjs
node --test /private/tmp/mtp-parallel-validation-PURH0f/snapshot/frontend/parallel-training.test.cjs /private/tmp/mtp-parallel-validation-PURH0f/snapshot/frontend/server-modal.test.cjs
docker compose up --build -d
docker compose -f docker-compose.yml -f /private/tmp/mtp-parallel-validation-PURH0f/compose.yml up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/healthz
git diff --cached --check
git status --short
```

早期回归和补充续训分别以相同 Docker 包装执行 `python -m pytest tests/test_parallel.py -q -k weighted --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-parallel-progress-regression`、`python -m pytest tests/test_parallel.py -q -k pause_resume --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-parallel-resume`。完整页面内联脚本也通过 Node `vm.Script` 语法解析；该项未代替业务测试或浏览器视觉验证。

**跨容器业务验证**：创建独立网络 `mtp-parallel-check`，启动临时 Redis (`redis:7-alpine`) 和 SeaweedFS (`chrislusf/seaweedfs:latest server -s3 -filer -dir=/tmp -ip=0.0.0.0`)；两个 worker 容器用生产入口 `python -m app.worker --slot cpu0 --idle-poll 0.1`，分别注册 `node-a-cpu0`、`node-b-cpu0`，设置各自容器 DNS 为互联地址、`GLOO_SOCKET_IFNAME=eth0`，连接隔离 Redis 和 S3 bucket `parallel-check`。第三个一次性容器通过 `python -u -` 执行内联 TestClient 验证：上传 80 行分类数据，以 batch 5+2 训练 3 epochs；断言唯一 segment 含两成员、全局批 7、3 条指标、accuracy 有效、S3 best.pt 存在、日志含联合启动、两次预测一致且槽租约全部释放。结果打印 PASS。未操作用户已有远程服务器或线上训练数据；脚本未保存到仓库。

### 容器验证、限制与后续建议

- **默认从零构建未完成**：规定命令 `docker compose up --build -d` 已执行，在从当前 pip 源下载 454.0 MB PyTorch 包的阶段等待数分钟仍未完成，主动取消，退出码 **130**。未将其标记为构建通过，也没有跳过包校验或修改项目依赖。
- **等价依赖基底的统一 Compose 重建通过**：复用镜像 `sha256:ad102bbe9afd8b1005897712d96332b702f5f7e547832d4b046335610ad98153` 的已安装依赖，以临时 Dockerfile 复制最新 app/static/sample_data 与构建资源，临时 Compose 覆盖 trainer/worker 的 Dockerfile 路径。基底和项目 `requirements.txt` SHA256 均为 `7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`。全部 Compose 服务成功重建启动，`/api/health` 返回 `{"ok":true}`，`/healthz` 返回 `ok`。关键训练文件和页面 SHA256 与本任务快照一致，运行镜像为 `sha256:f72a45f60ca7f88976cc1a9740eaefbbf63eb118cdbe645dc3e17ee0c72850ee`。没有提交临时覆盖配置。
- **本地等价启动通过**：在一次性 Python 3.12 容器中挂载项目，以 `USE_DATABASE=false STORAGE_BACKEND=local REDIS_URL= DATA_DIR=/tmp/parallel-local-api python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000` 启动，容器内 `urllib.request` 验证健康结果。与目标 Python 3.12 无版本差异。
- **剩余未验证项**：2 项真实 NVIDIA CPU/GPU 混合和多 GPU NCCL 测试未执行；已有 Supervisor kill/restart E2E 在完整套件因未配置 Redis 跳过，补测未选择该项，当前镜像也缺少其依赖的 `pgrep`。在真实 GPU/Linux 环境补跑对应测试，并为 Supervisor 验证提供 procps。真实跨物理主机、跨网段网络、x86_64 与性能加速比未测；跨容器验证不能替代这些硬件验证。
- **浏览器验证限制**：Computer Use 的 in-app browser 不可用，浏览器列表为空，未进行真实浏览器渲染/交互验收；前端函数业务测试和整体脚本解析均通过。建议有浏览器环境后检查不同屏宽下的节点选择、批大小编辑和远程地址填写。
- **使用限制**：每节点需容纳完整模型；混合 CPU/GPU 不保证加速；NVIDIA CUDA 与 CPU 以外后端暂未适配。成员需相同 PyTorch 版本（CPU/CUDA 后缀可不同），可信双向可达网络及必要动态通信端口；节点/数据/批大小不变的 CPU 续训已验证逐位一致，跨设备不保证逐位一致。默认依赖从零构建仍需在稳定网络补验。
- **重测触发**：修改梯度汇总、数据分片、预处理、LLM 目标函数、任务/槽租约、断点、训练 API、远程部署或相关业务配置，重新执行相关套件、完整回归、跨容器验证及可用硬件测试；用户明确要求时同样重测。
- **回滚**：先停止或暂停联合任务，再撤销本业务提交并统一重建服务；旧版本不能继续接收联合训练配置。不涉及数据库迁移。报告单独提交，未执行 git push。
- **清理**：本任务临时 worker/API/Redis/S3 容器、隔离网络、验证镜像标签已移除；容器测试数据和权重随容器删除。临时快照/Compose 目录交付前清除；仅保留正式测试用例，运行产物未纳入 Git。共享工作区其他会话的未提交修改保留。

## 本次变更测试结果：Hugging Face Hub 数据集维护（2026-10-29）

### Git 基准点与实现范围

- **Commit Hash**：`1ad502d1de08b4a5ccdf3a5f04480baf7bc13bcd`。
- **提交信息**：Add on-demand Hugging Face dataset imports。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围**：DatasetManager、数据集 API/请求模型、训练入口的数据准备、存储解析、维护页与训练页、正式 Hub 测试。无新增依赖、数据库迁移或仓库配置修改。
- **技术环境**：Python **3.12.15**、FastAPI/Pydantic、Pandas、datasets 4.8.5、huggingface_hub 0.36.2、PyTorch CPU、原生 HTML/CSS/JavaScript。
- **行为**：维护页可立即下载或仅保存远程引用；保存引用只查询元信息并固定 commit SHA。首次明确预览、统计、画像、处理或训练时下载，缓存复用；列表和训练页自动加载不触发拉取。失败保留引用和脱敏错误供重试。文件锁串行化同一数据集的下载/删除；临时 CSV 通过限制检查、同步 S3 后原子发布。
- **兼容范围**：旧扁平布局与 metadata/raw/processed 布局、本地上传、LLM 既有入口、本地及 S3 Worker。
- **测试隔离**：共享工作区同时存在其他任务修改，未纳入本次提交。最终另行导出本次拟提交树 `297696ca1b0d8e36f707526b898e2bc220beeb28` 验证；导出后并行合入 `8346b5f` 服务器弹窗修改，最终基准与该测试树的 **backend/app/ 完全一致**，仅另含该并行前端/API 测试变更。工作区整套测试另有记录，不能把工作区结果当成纯净提交结果。

### 测试结果

| 阶段 | 总数 | 通过 | 失败 | 错误 | 跳过 |
|---|---:|---:|---:|---:|---:|
| 初版 Hub 专项 | 39 | 39 | 0 | 0 | 0 |
| 首次相关回归 | 146 | 137 | 7 | 0 | 2 |
| 修正隔离并补充并发删除后的相关回归 | 147 | 145 | 0 | 0 | 2 |
| 共享工作区完整 pytest | 466 | 442 | 0 | 0 | 24 |
| 拟提交内容独立完整 pytest | 432 | 410 | 0 | 0 | 22 |

最终独立完整测试 49.57 秒，已执行用例通过率 **100%**（含跳过为 94.91%）；新增 Hub 用例 **40/40**，没有删除、跳过或弱化有效测试。两条既有弃用警告来自 SQLAlchemy declarative_base 和 Pandas xlrd。

首次相关回归的 7 项失败均为训练等待超时：新增测试 fixture 停止了共用 JobManager，后续 API 测试无法执行任务。已改为模块独立的 JobManager、EmbeddedJobQueue、EmbeddedLeaseStore，只停止自身管理器；相关套件及完整套件均通过。并发删除用例改为下载未完成时真实发起删除，验证等待锁、最终无 CSV 和无法重新使用已删除 ID。

### 验收标准—可执行测试映射

正式用例均在 `backend/tests/test_hub_datasets.py`；外部 Hub 网络使用 Mock，流式迭代器、CSV 转换、缓存、API、训练和预测运行真实逻辑。

| 验收标准 | 层级 / 前置条件及输入 | 测试用例 | 预期结果 / 场景 |
|---|---|---|---|
| 保存远程引用不下载且固定版本 | API；公开仓库，remote/v1/plain/test | test_remote_reference_pins_revision_without_downloading | 元信息固定 SHA，列表可见，无 CSV、无下载调用；正常 |
| 两种模式首次使用并复用缓存 | API；local/remote 两种模式 | test_import_and_first_preview_cache_data | 120 行及列值正确，仅下载一次；正常/兼容 |
| 每个数据消费者支持引用 | API/存储；stats/profile/process/worker | test_first_use_downloads_for_each_data_consumer | 得到实际数据并转为 ready；正常/回归 |
| 非法输入不能访问 Hub | API；12 组空值、超长、URL、路径、非法 split/mode/tags | test_invalid_sources_do_not_reach_hub | 422，无网络请求或元数据；边界/安全 |
| 受限仓库和外部错误不泄漏信息 | API；401/403/404/500、gated | test_hub_metadata_failures_are_clear_and_redacted、test_gated_dataset_is_rejected | 明确 4xx/503，不回显秘密/路径；权限/异常 |
| 下载失败保留引用，可重试 | API；TimeoutError/ValueError 后恢复 | test_download_failure_retains_reference_and_retry_works | error 状态、无残缺 CSV；同 ID 重试 ready；异常/恢复 |
| 大小限制不能静默截断 | API；行/列/字节等于上限与超过上限 | test_download_limits_are_exact_without_truncation | 边界成功，超限 400 且无 CSV；边界 |
| 支持嵌套文本、拒绝空和媒体 | API；empty/media/nested_media/text | test_supported_text_and_unsupported_data | 文本 JSON 结构保留，其他完整失败；正常/边界/异常 |
| 下载与删除正确协调 | 管理器；并发请求及下载中删除 | test_concurrent_downloads_share_one_cache、test_delete_waits_for_inflight_download | 单次下载、删除等待、不会复活；并发/状态冲突 |
| S3 故障可恢复且 Worker 可读 | API/存储；模拟远端失败后恢复 | test_object_store_sync_failure_rolls_back_and_remote_worker_reads_cache | 失败无本地 CSV，重试后远端副本与 Worker 读取一致；异常/兼容 |
| 远程引用可直接训练且可复现 | API/真实 CPU；固定 seed 两次分类训练 | test_hub_reference_trains_reproducibly_and_predicts | completed、accuracy、日志、best.pt，重复指标一致；正常/回归 |
| 预测沿用训练预处理 | API/真实模型；正常、缺失、非法字段，各重复预测 | 同上 | 预测成功且结果稳定；边界/兼容 |
| 旧布局与历史训练继续有效 | 完整既有 API/Storage/Trainer 套件 | test_process_dataset_supports_legacy_flat_layout 等 | 两布局、分类/回归/聚类/时序、指标及产物回归通过 |
| 页面自动加载不下载 | Node vm 执行实际 loadPreview 函数；Stub DOM/API | 下述前端行为检查 | 自动加载 0 次请求；显式预览下载并更新状态；异常显示文字 |

### 实际执行命令

以下均从项目根目录执行；测试容器只读挂载源码、使用独立临时数据，不连接生产协调服务。

```bash
# 初版专项：39 passed
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_hub_datasets.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-hub-targeted
# 首轮相关回归使用相同参数，basetemp=/tmp/mtp-hub-related：7 failed，137 passed，2 skipped
# fixture 修正后：145 passed，2 skipped
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_hub_datasets.py tests/test_api.py tests/test_storage.py tests/test_llm_trainer.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-hub-related-fixed
# 工作区整套：442 passed，24 skipped，118.46 秒
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-hub-full
# 本次拟提交树已导出至下述临时目录：410 passed，22 skipped
docker run --rm --pull=never --network none --mount type=bind,src=/tmp/mtp-hub-work.OhXGPM/verified-source,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v --tb=short -r s -p no:cacheprovider --basetemp=/tmp/mtp-hub-isolated
git diff --check
```

前端另以 Node 26.9.0 的 `vm.Script` 编译两个 HTML 内联脚本，均通过；`vm.runInContext` 执行实际 `loadPreview` 和 `apiJson`，断言：自动进入远程引用不调用 API；点击下载预览后显示实际行并变为 ready；网络失败显示明确错误；Pydantic 422 数组错误显示 msg 而非 [object Object]。均使用 `node <<'JS'` 内联脚本，未生成或提交临时测试代码。Safari 实际展示了 Hub 表单与默认“保存远程引用”，提交公开仓库成功；由于浏览器同时被其他操作切换，未完成独立的完整视觉/窄屏验收，不将其记为通过。

### 真实 Hub、S3 与部署验证

1. 使用一次性 Python 3.12 容器真实访问 `lhoestq/demo1`：`register_hub_dataset('lhoestq/demo1', 'main', None, 'train', 'remote')` 返回固定 SHA `87ecf163bedca9d80598b528940a9c4f99e14c11`，CSV 数量 0；`ensure_local(id)` 与 `get_dataset_preview(id)` 返回 5 行、6 列。首次内联 smoke 脚本存在多余括号的 SyntaxError，修正后实际网络调用通过。
2. Safari 在部署页保存测试引用 `ds-20261029-111333-ae0e30`。通过 `curl --silent --fail http://localhost:8000/api/datasets` 及容器内 urllib 验证保存后仍为 remote，raw CSV 不存在。
3. `docker exec -i -e PYTHONDONTWRITEBYTECODE=1 mtp-trainer python -` 内联脚本对该 ID 调用 `POST /api/datasets/{id}/materialize`，断言 ready、5 行、真实 S3 对象存在；`GET /api/datasets/{id}` 预览 5 行。
4. `docker exec -i -e PYTHONDONTWRITEBYTECODE=1 mtp-worker python -` 使用 `TemporaryDirectory` 和 `resolve_dataset_for_worker(id, Path(work))`，Pandas 断言 5 行且包含 review 列，验证独立 Worker 的真实 S3 读取。
5. 同样在 trainer 中真实调用 `POST /api/datasets/hub`，输入 `{"repo_id":"lhoestq/demo1","storage_mode":"local"}`，断言 ready、5 行及 S3 副本。随后通过 DELETE 清理两个本次创建的 ID，断言对应本地 CSV 和 S3 对象均不存在，并清理对应锁文件。
6. 已执行要求的 `docker compose up --build -d`。默认依赖下载出现 `[SSL] record layer failure`，454 MB 的 torch wheel 首轮仅下载约 8.9 MB 后失败，第二轮仍极慢，因此中止该轮。没有把默认从零构建标记成功。
7. 通过现有 Dockerfile 的 BASE_IMAGE 构建参数复用已安装依赖的本地 Python 3.12 镜像，临时 Compose 覆盖文件仅位于 `/tmp/mtp-hub-work.OhXGPM/compose-cache.yml`，不修改仓库配置。已执行两次 `COMPOSE_FILE="$PWD/docker-compose.yml:/tmp/mtp-hub-work.OhXGPM/compose-cache.yml" docker compose up --build -d`，均成功重建并启动 trainer、worker、frontend；第二次包含参数错误反馈修正。首次创建缓存标签的实际命令为 `docker tag emdash-hot-boxes-clean-y9u6p-trainer:latest mtp-hub-trainer-base:validation` 和 `docker tag mtp-worker:local mtp-hub-worker-base:validation`；覆盖文件分别向两服务传入对应 BASE_IMAGE。
8. `docker compose ps --format json` 确认服务 running、frontend/seaweedfs healthy；`curl --silent --fail http://localhost:8000/api/health` 返回 `{"ok":true}`。容器内 DatasetManager、数据集路由、storage 三文件 SHA-256 与工作区一致。

### 未执行项、限制与后续建议

- 最终套件 22 项环境跳过：15 项真实 Redis 协调/注册测试、4 项真实 Redis Worker 测试、1 项 Supervisor 多进程测试、2 项独立 S3 测试；未配置隔离的 MTP_TEST_REDIS / MTP_TEST_S3。工作区套件另有 2 项其他任务的 CUDA 多节点测试因无 GPU 跳过。真实 Hub→S3→独立 Worker smoke 已通过，但不替代这些独立故障测试。建议在隔离 Redis/S3 和具备 GPU 的环境补跑相应套件。
- 仅支持无需授权的公开表格/文本数据集，不接受 token；不运行远程代码，不支持图像/音频。沿用 500 MB、200 万行、100 列限制。子集/split 和数据格式在实际下载时验证，保存引用时不提前下载验证。
- 下载为同步请求，耗时受 Hub 网络和数据规模影响；CSV 完成后用 Pandas 计算元信息，内存占用可能高于文件大小。未进行最大规模/长时间下载压力测试。
- 默认基础镜像从零安装依赖仍未验证成功，建议网络恢复后重跑无覆盖的 `docker compose up --build -d`。本次成功的是复用本地依赖的容器重建，没有使用低版本 Python 替代。
- 浏览器完整视觉/移动端验收未完成；表单结构、交互函数和实际保存动作已验证。平台现有数据集 API 无账户鉴权，本次未新建权限模型；安全测试覆盖受限 Hub 拒绝、输入校验、禁用凭证/远程代码与错误脱敏。
- 临时快照/索引/导出源码/构建覆盖文件位于已告知的 `/tmp/mtp-hub-work.OhXGPM`，交付前清理；测试容器自动删除，测试数据及 S3 副本已清理。正式测试保留在仓库。
- 重测触发条件：Hub 元数据模型、拉取/发布/回滚、存储解析、训练入口、前端按需触发逻辑或相关依赖变更时重跑对应测试及完整套件。
- 回滚：回退本次功能提交并重建容器；无需数据库回滚。已下载的 CSV 沿用既有结构，未下载引用需要恢复此功能后才能使用。

---

## 本次变更测试结果：添加服务器弹窗交互优化（2026-10-29）

### Git 基准点与范围

- **Commit Hash**：`8346b5fa3cd5f89259a860f38b00e4cfdf0a9fa4`。
- **提交信息**：Improve server dialog interaction and validation。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围**：`backend/static/index.html`、`backend/static/app.css`、`backend/tests/test_api.py`、`frontend/server-modal.test.cjs`。原生 HTML/CSS/JavaScript，Node 内置 test/vm，pytest；没有新增依赖、修改后端接口、配置或数据结构。
- **行为**：连接信息与计算资源分组；高级项折叠；标题和操作栏固定、内容独立滚动；凭证就近管理、空状态引导及保存后自动选中；校验定位、失败保留输入、防重复提交；遮罩不关闭、未保存提醒、Tab 焦点循环、关闭清空秘密。添加与编辑共用交互，登记后仍需单独部署。
- **隔离说明**：共享工作区存在其他任务的联合训练、Hub 数据集、镜像及 Worker 改动。本次完整验收以 `5a969d5d3a50cfaac6796e4dfb0116cbc405fa4f` 归档加本次 4 个文件为准；与本次提交之间仅有其他测试报告提交，业务及测试内容一致。只提交弹窗相关差异，没有提交或回退其他任务的代码；联合训练互联地址字段保留在共享工作区，但不纳入本次独立提交或测试前提。其后其他功能提交不由本节结果背书。

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 错误 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| 新增静态回归，实施前 | 2 | 0 | 2 | 0 | 0 | 预期失败，缺少分组布局和关闭保护 |
| 首轮相关 API/页面测试 | 21 | 20 | 1 | 0 | 0 | 原测试按无参函数签名截取源码，加入 preferredId 参数后截取失败；更新定位字符串并保留原断言 |
| 相关 API/页面测试修正后 | 21 | 21 | 0 | 0 | 0 | 另有 44 项被 -k 筛选，不计入执行数 |
| Node 交互回归，工作区及隔离版本 | 18 | 18 | 0 | 0 | 0 | 每轮均 18/18；执行真实页面函数，DOM/网络边界隔离 |
| 共享工作区完整 pytest | 453 | 426 | 3 | 0 | 24 | 混有其他任务未提交功能，190.13 秒；详见失败分析 |
| 本次独立提交内容完整 pytest | 394 | 372 | 0 | 0 | 22 | Python 3.12.15，51.76 秒；已执行用例通过率 100%，总通过比例 94.42% |

保留 2 条已有弃用警告：SQLAlchemy declarative_base、Pandas xlrd。22 项跳过涉及未配置 MTP_TEST_REDIS、MTP_TEST_S3 的真实 Redis/S3 与 Supervisor 场景，不计为通过。内联 JavaScript 语法检查、`git diff --check` 和本次提交差异检查通过。

**共享工作区失败分析**：

- `test_hub_reference_trains_reproducibly_and_predicts`：其他任务新增的 Hub 训练用例在 30 秒内未进入终态。未擅自放宽等待阈值或修改该功能。
- `test_remote_bootstrap_script_start_unchanged_config_keeps_pure_operation`、`test_remote_bootstrap_script_restart_unchanged_config_forces_restart`：其他任务未提交的联合训练逻辑新增 WORKER_DISTRIBUTED_HOST 行，与当前已提交测试期望不一致。
- 隔离完整套件保留所有基准内正式测试，两项远端测试均通过；Hub 用例尚不属于当时已提交基准。本次弹窗不修改这些后端路径，未通过删改有效测试规避失败；共享工作区整体仍需由相关功能完成后重新验收。

**实际执行命令**（项目根目录；一次性容器、只读挂载、无外部网络，测试产物随容器销毁）：

```bash
node --test frontend/server-modal.test.cjs
node --test /tmp/mtp-server-modal.I3q8Iw/isolated/frontend/server-modal.test.cjs
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_api.py -q -p no:cacheprovider --basetemp=/tmp/mtp-modal-red -k 'remote_server_dialog' --tb=short
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-modal-deps:local python -m pytest tests/test_api.py -q -p no:cacheprovider --basetemp=/tmp/mtp-modal-targeted-final -k 'remote_server or remote_ssh' --tb=short
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-modal-deps:local python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/mtp-modal-full --tb=short
docker run --rm --pull=never --network none --mount type=bind,src=/tmp/mtp-server-modal.I3q8Iw/isolated,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 mtp-modal-deps:local python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/mtp-modal-isolated --tb=short
curl -fsS http://localhost:8000/api/health
git diff --check
```

隔离副本由 Node 调用 `git archive 5a969d5d3a50cfaac6796e4dfb0116cbc405fa4f`，通过 tar 解包后覆盖本次文件；没有改动共享工作区来运行隔离测试。初轮相关测试使用同类 Docker 命令。Node vm.Script 检查页面内联脚本语法。

### 验收标准—测试用例映射

| 验收标准 | 层级、前置条件与用例 | 输入 → 预期结果及副作用 | 场景 |
|---|---|---|---|
| 分组与固定操作栏、折叠高级项 | pytest 静态契约；test_remote_server_dialog_groups_fields_and_keeps_actions_outside_scroll_area；桌面 Safari | 读取 HTML/CSS → 高级项默认关闭，submit 关联正确表单，内容滚动区与 footer 分离；桌面实际呈现无重叠 | 正常、布局回归 |
| 无效字段可见且可修正 | Node 参数化字段校验、折叠区域校验 | 空标识、非法主机、端口 0/65536/2.5；边界 1/65535 → 阻止提交并聚焦；折叠项报告无效时自动展开；输入修正后可提交 | 空值、非法输入、数值边界 |
| CPU/GPU 状态同步 | Node 高级字段和计算槽测试；Safari CPU 切换 | GPU 隐藏禁用 CPU 校验；CPU -1/257 拒绝、0/256 允许；编辑 CPU 0 正确回填 | 边界、添加/编辑兼容 |
| 凭证流程不中断填写 | Node 空目录、默认选择、保存成功/失败测试 | 空目录引导创建；默认回填不误报未保存；新增成功自动选中、清空秘密、恢复按钮，服务器输入保留；失败可重试 | 正常、空集合、依赖失败、回归 |
| 防重复提交及旧响应覆盖 | Node 延迟 Promise 测试 | 重复保存只发一次请求，body 和表单 inert；保存时不能关闭；旧会话加载响应不得改变新会话选择；失败解锁并保留输入 | 并发、时序、异常 |
| 安全关闭与键盘导航 | Node 未保存保护和键盘测试；pytest test_remote_server_dialog_protects_input_and_keyboard_navigation | 取消关闭保留内容，确认丢弃清空秘密并恢复触发焦点；Tab/Shift+Tab 循环；Esc 经过关闭保护；遮罩事件不关闭 | 安全、误触、键盘回归 |
| 添加/编辑接口兼容 | Node POST/PATCH 载荷测试；21 项已有 API/SSH 相关测试 | POST 携带 machine_id，PATCH 不修改标识；数值转换、可选用户名、S3 覆盖和清除 Redis 保持含义；成功保存后关闭 | 正常、接口兼容、已有认证/脱敏回归 |

未修改训练/预测/数据路径；对应既有已提交测试通过完整套件回归。未新增认证或授权逻辑，沿用后端已有权限校验。

### 构建、页面验证与限制

1. 已执行 `docker compose up --build -d`，默认 PyPI 镜像的 PyTorch 大包下载不完整并出现哈希不匹配；本次构建进程随后停止，退出 130，不记为默认从零构建成功。
2. 运行依赖镜像与 requirements.txt 的 SHA256 同为 `7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`。固定原镜像为临时标签 `mtp-modal-deps:local`，使用 stdin 临时 Compose 覆盖复用 Python 3.12 依赖并复制最新源码、静态资源、启动文件。未修改仓库配置或依赖文件。首次临时覆盖使用 dockerfile:null 被 Compose 校验拒绝，移除该项后重建成功。
3. 实际成功命令如下；trainer、worker、frontend 完成 Compose 构建启动，健康接口返回 HTTP 200 / {"ok":true}。这是依赖复用的容器重建，不能解释为默认 Dockerfile 全量联网构建通过。

```bash
cat <<'YAML' | docker compose -f docker-compose.yml -f - up --build -d
services:
  trainer:
    build: &modal_build
      context: ./backend
      dockerfile_inline: |
        FROM mtp-modal-deps:local
        COPY app /app/app
        COPY static /app/static
        COPY sample_data /app/sample_data
        COPY Dockerfile worker.Dockerfile requirements-worker.txt /app/
        COPY init_db.sh start.sh /app/
        RUN chmod +x /app/init_db.sh /app/start.sh
  worker:
    build: *modal_build
YAML
```

4. 使用 Node fetch 只读比较线上 HTML 的弹窗、会话逻辑和事件绑定片段与工作区内容，均一致；共享工作区其他任务同时变更 HTML，因此整页哈希曾不同，未将它误判为弹窗版本不一致。CSS 从 HTTP 成功读取；初次误查 /health 返回 404，正确健康路径为 /api/health。
5. Safari 桌面实际检查了分组、首字段焦点、固定标题/底栏、CPU 槽位显示、凭证目录加载及凭证管理展开。曾在环境重建期间显示凭证读取失败，之后成功读取 1 条既有凭证；未新建或修改现场凭证/服务器。截图仅在工具中查看，没有保存文件。
6. **未执行/限制**：窄屏实机和完整浏览器端到端提交尚未验证；桌面被其他操作持续切换，工具多次返回用户已改变应用状态。Node fixture 不替代真实浏览器布局/原生验证气泡。建议用 390×844 与 1280×720 复查高级项展开、滚动、Tab/Esc、遮罩、失败重试和固定按钮；在隔离服务补充真实提交流程。真实 Redis/S3/Supervisor 的 22 项环境测试未执行；默认依赖联网构建待网络恢复后复验。
7. **重测触发**：继续修改弹窗结构/状态/校验或相关 API 时，运行 Node 交互测试和相关 pytest；后端业务变更需完整回归并更新其基准。回滚本次功能可撤销 `8346b5f` 后重新构建，不涉及数据迁移。

**清理与提交**：本任务临时目录 `/tmp/mtp-server-modal.I3q8Iw`（含原始备份、隔离副本、暂存索引）在交付前删除，临时依赖标签移除；本任务创建的 Safari 验证标签已关闭。正式测试保留，运行数据、秘密和调试脚本不提交。代码与本报告独立提交，未执行 git push；共享工作区其他任务的改动保留。

## 本次变更测试结果：本地计算资源注册自动恢复（2026-10-29）

### Git 基准点、根因与范围

- **Commit Hash**：`5a969d5d3a50cfaac6796e4dfb0116cbc405fa4f`。
- **基准提交信息**：Update test report for slim remote workers。
- **本次修复提交**：`db1d5c698ac1cf031e77a0cbc58d79f711490c9f`（Restore worker registration on every heartbeat）。最终完整套件直接运行该提交的归档；其后至基准提交只有测试报告变化，业务源码完全相同。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围**：`backend/app/coordination.py`、`backend/tests/test_coordination.py`、`backend/tests/test_worker.py`。没有新增依赖、修改部署配置、迁移数据或修改第三方代码。
- **现场证据**：API 资源列表为空时，Redis 可连接、`mtp:workers:all` 为空，但两个槽的 info hash 仍在持续更新；Worker 日志存在 Redis 连接拒绝、连接/读取超时。旧实现仅启动注册时写索引和硬件能力，心跳只写动态字段。注册数据丢失或长时间断连后被清理时，存活 Worker 无法自行重返资源列表。尚不能仅凭这些日志区分最初触发是 Redis 数据丢失还是超时清理。
- **实现**：按 worker_id 缓存序列化后的能力快照；每次心跳在同一 Redis 事务内补齐集合索引、能力信息、最新动态状态和 TTL；动态字段覆盖启动快照，注销时丢弃缓存。沿用现有序列化，独立的启停开关与任务租约不被恢复逻辑覆盖。
- **技术环境**：Python **3.12.15**、pytest、redis-py / Redis 7、PyTorch 2.14.1+cu130（实际 CPU 执行）、FastAPI、Docker Compose。测试 Redis 为独立容器的 DB 15，不访问生产 Redis。

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 错误 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| 修复前新增回归 | 15 | 0 | 15 | 0 | 0 | 稳定复现注册丢失后无法恢复 |
| 初次修复后新增回归 | 15 | 3 | 12 | 0 | 0 | 新测试将 CUDA 版本原始字符串与既有解码后的数值比较；改为与注册成功后的能力快照比较，未改变序列化或弱化已有测试 |
| 工作区真实 Redis 相关套件 | 80 | 79 | 0 | 0 | 1 | 22.20 秒；协调层和 Worker 全套，包含 15 个新增恢复用例 |
| 并发工作区完整套件 | 453 | 426 | 3 | 0 | 24 | 191.43 秒；其他会话未提交修改参与执行，失败详见下文 |
| 原基准加修复的隔离完整套件 | 366 | 344 | 0 | 0 | 22 | 69.76 秒；`d1f4115` 加本次三个文件，无其他工作区修改 |
| 同一隔离快照 Redis 相关套件 | 80 | 79 | 0 | 0 | 1 | 21.34 秒；相关三个文件 SHA256 与提交候选一致 |
| 最终修复提交完整套件 | 392 | 370 | 0 | 0 | 22 | **54.90 秒**；归档 `db1d5c6`，包含期间已提交的远程 Worker 精简改动；已执行用例通过率 100%，总通过比例 94.39% |
| Python 3.12 本地等价 API 冒烟 | 1 | 1 | 0 | 0 | 0 | 无数据库、无 Redis、本地临时存储，Uvicorn 启动后健康业务断言通过，正常关闭 |

完整套件保留 SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 两条原有弃用警告。22 个跳过项包含 15 个新增 Redis 恢复场景和 4 个既有 Redis 场景，均由真实 Redis 相关套件补齐；其余为 2 个外部 S3 集成、1 个 Supervisor E2E。Redis 相关套件跳过的是该模式不适用的嵌入式回退断言，嵌入式完整套件已验证。

**所有失败与中断记录**：

- 并发工作区的 `test_hub_reference_trains_reproducibly_and_predicts` 未在 30 秒内进入终态；`test_remote_bootstrap_script_start_unchanged_config_keeps_pure_operation` 与 `test_remote_bootstrap_script_restart_unchanged_config_forces_restart` 的 `_CURRENT_WORKER_ENV` 与其他会话新增配置不同。三项涉及本任务之外的未提交改动；原基准隔离快照及最终已提交代码的完整测试均通过。未修改或弱化这些测试，不能据本报告宣称整个共享工作区已验收。
- 额外尝试将最终完整套件全部切换到 Redis 模式，因未配置独立测试 Worker，`test_train_e2e_classification` 的任务保持 queued；8 通过、1 失败后中止（退出 2），不作为有效全量验收。按项目夹具约定，以嵌入式完整套件加隔离 Redis 专项验收。
- 一次误重复启动的隔离完整测试在 36 通过后被中断，未作为完整测试结果计入；对应容器已退出。

**关键模块**：协调层/Worker 79 个适用用例全部通过，验证注册恢复、TTL、启停控制、租约失效、训练控制器、恢复后进度及多槽隔离；完整套件中的数据层、训练器、API、远程管理等所有可执行用例通过，保留训练/日志/accuracy、R²、RMSE/best.pt、固定 seed、预测一致性与预处理、新旧数据布局的既有回归覆盖。

### 验收标准—测试用例映射

| 验收标准 | 层级、前置条件与输入 | 预期业务结果及关键副作用 | 场景 |
|---|---|---|---|
| 注册信息丢失后恢复 | Redis 集成，先注册完整 CPU/GPU/版本/并行能力；`test_redis_heartbeat_restores_worker_registration`，索引/详情/全部缺失 × idle/training/disabled，共 9 组 | 下一心跳恢复唯一资源项、完整能力、最新进度和活动状态，TTL 为正，接单选择保持原值 | 正常、缺失、状态边界、兼容、回归 |
| 长断连清理后不需重启 | Redis 集成；`test_redis_heartbeat_restores_after_stale_cleanup`，删除 HB 与任务租约、超宽限清理、再次心跳 | 资源重新出现、禁用状态保留；丢失的任务租约仍不可续租，不越权复活旧任务 | 超时、依赖异常、所有权安全、回归 |
| 空输入及观察者兼容 | Redis 集成；`test_redis_heartbeat_handles_empty_fields_and_uncached_worker`，空字段/无缓存/其他实例更新 | 空心跳有效，不带入其他槽 GPU 信息；已有 Redis 能力保留、动态进度更新 | 空值、缓存边界、多槽隔离、旧行为兼容 |
| 注销后不恢复旧能力 | Redis 集成；`test_redis_deregister_discards_cached_capabilities`，注销、同 ID 空心跳与重新注册 | 旧 GPU 信息清除，新能力可恢复 | 生命周期、状态冲突、回归 |
| 真实 Worker 各状态自动恢复 | Worker 集成；`test_worker_heartbeat_recovers_registration_without_restart[idle/disabled/training]`，删除注册键，执行空闲循环或真实 JobController | 不重新启动即可恢复完整能力、接单选择、正确 job/epoch；训练租约仍有效；空闲不凭空产生任务 | 正常、禁用、训练中、异常、权限状态 |
| 修复应用到实际运行环境 | Compose 重建、容器内源码校验、连续读取 `/api/workers` 与 `/api/health` | API 健康，两个 CPU 槽在线启用且空闲，心跳递增、同批采样 started_at 不变、队列为 0 | 部署、运行时、回归 |

未修改认证、任务调度策略、训练算法或数据接口，因此权限断言聚焦原有禁用选择、槽隔离和任务租约所有权；不将模拟 GPU 能力恢复称为真实 GPU 硬件测试。

### 实际执行命令

以下命令在项目根目录执行。临时快照根为 `/tmp/mtp-registry-validation-EtEB8o`，`committed/` 为 `git archive db1d5c6` 导出的最终源码，均已清理。每个 pytest 容器只读挂载源码，测试产物留在一次性容器内。

```bash
docker run --rm --pull=never --network container:mtp-registry-test-redis --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env MTP_TEST_REDIS=redis://127.0.0.1:6379/15 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_coordination.py tests/test_worker.py -k 'restores_worker_registration or restores_after_stale_cleanup or handles_empty_fields_and_uncached_worker or discards_cached_capabilities or recovers_registration_without_restart' -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-registry-before
docker run --rm --pull=never --network container:mtp-registry-test-redis --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env MTP_TEST_REDIS=redis://127.0.0.1:6379/15 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_coordination.py tests/test_worker.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-registry-targeted
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-registry-full
docker run --rm --pull=never --network none --mount type=bind,src=/tmp/mtp-registry-validation-EtEB8o,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-registry-isolated
docker run --rm --pull=never --network container:mtp-registry-test-redis --mount type=bind,src=/tmp/mtp-registry-validation-EtEB8o,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env MTP_TEST_REDIS=redis://127.0.0.1:6379/15 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_coordination.py tests/test_worker.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-registry-isolated-redis
docker run --rm --pull=never --network container:mtp-registry-test-redis --mount type=bind,src=/tmp/mtp-registry-validation-EtEB8o/committed,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env MTP_TEST_REDIS=redis://127.0.0.1:6379/15 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-registry-committed
docker run --rm --pull=never --network none --mount type=bind,src=/tmp/mtp-registry-validation-EtEB8o/committed,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-registry-committed-embedded
docker compose up --build -d
docker compose -f docker-compose.yml -f /tmp/mtp-registry-validation-EtEB8o/compose.yml up --build -d
docker exec mtp-worker sha256sum /app/app/coordination.py
docker exec mtp-trainer sha256sum /app/app/coordination.py
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/workers
git diff --check -- backend/app/coordination.py backend/tests/test_coordination.py backend/tests/test_worker.py
```

本地等价 API 验证通过 `docker run --rm -i --pull=never --network none` 挂载最终快照、工作目录为 `/workspace/backend`，以 stdin 中的 Python 3.12 验证代码启动 `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`；环境为 `USE_DATABASE=false STORAGE_BACKEND=local REDIS_URL='' EMBEDDED_WORKERS=0`、DATA_DIR 指向 `TemporaryDirectory`。等待最多 30 秒，断言 HTTP 200 与 `{"ok":true}`，finally 中终止并等待服务退出。没有宿主机 Python 版本差异，没有保留临时验证代码或数据。

### 构建与现场结果、限制及清理

- **默认从零构建未通过**：执行了 `docker compose up --build -d`，但阿里云 PyPI 镜像下载 454 MB 的 torch wheel 时出现 SSL `record layer failure`，首轮只获得约 8.9 MB，重试仍缓慢；主动终止，退出 130。不能将默认配方从零下载依赖标为通过。
- **统一 Compose 重建部署通过**：临时覆盖文件分别复用正在运行的 trainer / worker 镜像作为依赖基底（`6aa174d8a769…` / `4e4286e07171…`），两者的 requirements SHA256 均与仓库一致：`7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`。仅 COPY 修复后的 `app/coordination.py`，再统一 Compose 构建并启动全部服务，退出 0。没有修改仓库 Compose 文件。
- **运行时核对通过**：trainer、worker 内 coordination.py SHA256 均为 `cc485f98f4424f08b4f6d4e1297a4f83c180d1c929aabe4cb733cc6fea1e5fa5`，与提交源码一致。健康 API 返回 ok；两个 CPU 槽（宿主 8 核、7934 MiB）均 active/enabled/idle，队列深度 0。连续采样确认 last_seen 递增、started_at 保持一致。并发任务随后又重建服务，末次检查新容器仍含同一修复，两个槽继续在线。
- **恢复归因**：初期两个槽曾因另一任务重启容器而重新出现，不能把那次恢复归因于本修复；本修复的不重启恢复能力由隔离 Redis 故障注入和真实 Worker 心跳测试证明。生产 Redis 未做删键/清库试验，也未改变用户启停选择。
- **已知限制 / 未执行**：外部 Redis 的网络或服务故障仍可能短暂影响资源显示，修复保证连接恢复后的注册自愈；Redis 整库丢失时不会重建独立的持久控制设置或任务租约。外部 S3、Supervisor E2E、真实 NVIDIA GPU、远程旧 Worker 的现场升级未执行。本报告不验收其他会话尚未提交的功能，也不宣称所有远端已更新。
- **后续验证与重测触发**：网络恢复后执行默认 Compose 从零构建；按需补测 S3/Supervisor/GPU 与远程部署。后续改动协调层、心跳、注册/清理、Worker、任务租约、序列化或相关业务配置，应重新执行 Redis 专项、完整套件及运行环境核对。用户明确要求时重测。
- **回滚**：需要时撤销 `db1d5c6` 并统一重建；没有数据库迁移。回滚会重新失去注册自愈能力。
- **清理与提交**：临时快照和 Compose 覆盖目录 `/tmp/mtp-registry-validation-EtEB8o` 已删除；隔离 Redis 容器已停止并自动移除，测试/API 容器及临时镜像标签已清理。本任务三个代码/测试文件已提交，报告单独提交；不执行 git push。其他会话的工作区改动保留。

## 本次变更测试结果：远程训练节点同步与 CPU 镜像精简（2026-10-29）

### Git 基准点与范围

- **Commit Hash**：`fae7d1e9be2903fac0631aa7442672c3b097a513`。
- **提交信息**：Slim remote worker packages and CPU dependencies。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **范围**：远程 worker 构建/代码包白名单、CPU/GPU 构建参数、镜像类型兼容性检查、独立 `worker.Dockerfile` / `requirements-worker.txt`、平台镜像携带构建资源及对应测试和 README。未操作远端服务器、清理远端镜像、修改数据结构或添加项目依赖。
- **隔离说明**：工作区同时存在其他会话的联合训练、数据集、前端等未提交改动，以及预先存在的 Dockerfile 下载重试改动。本次最终测试在 `d1f4115` 加本提交修改的独立快照执行；暂存业务源码及测试与快照的 Git blob hash 一致。仅提交本任务的文件/代码块，不将共享工作区其他功能视为已验收。测试后其他会话新增的 `db1d5c6` 心跳注册提交不属于本次验证基准点。
- **技术环境**：Docker Desktop / Linux ARM64，Python **3.12.15**。完整 pytest 使用现有平台依赖（PyTorch 2.14.1+cu130，以 CPU 执行）；新专用镜像实际安装 **PyTorch 2.15.0+cpu**、transformers 4.57.6、datasets 4.8.5。依赖范围沿用平台清单，worker 不安装管理 API、数据库管理、Excel 导入或测试工具。

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 错误 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| 实现前新增回归 | 7 | 0 | 7 | 0 | 0 | 复现携带管理端内容、缺少硬件构建参数及非法模式未拒绝 |
| 早期共享工作区远程套件 | 126 | 124 | 2 | 0 | 0 | 其他会话新增空的 `WORKER_DISTRIBUTED_HOST` 行，导致两项配置不变断言失败；未通过弱化断言规避 |
| 最终隔离快照远程套件 | 145 | 145 | 0 | 0 | 0 | 13.29 秒；打包、构建、SSH、生命周期、回滚及模式检查全部通过 |
| 最终隔离快照完整 pytest | 377 | 370 | 0 | 0 | 7 | **52.08 秒**，已执行用例通过率 100%，含跳过的总通过比例 98.14% |
| 新 CPU 镜像依赖验证 | 1 | 1 | 0 | 0 | 0 | `pip check`、Python 3.12、`torch.version.cuda is None`；无 nvidia-*、Triton 或平台专用包 |
| 新 CPU 镜像真实训练 | 3 | 3 | 0 | 0 | 0 | 分类、回归、离线微型 LLM 预训练；验证业务指标、权重/模型包、可复现性和预测/生成 |
| 隔离 Uvicorn 冒烟 | 1 | 1 | 0 | 0 | 0 | Python 3.12、无数据库/Redis、本地临时存储，`/api/health` 返回 200 和 `{"ok":true}` |

补充边界测试前的完整套件也通过（364 通过、7 跳过），最终数字以上表为准。保留 SQLAlchemy `declarative_base()`、Pandas `xlrd` 两条既有弃用警告；微型 LLM 的默认 loss 提示不影响断言。没有删除、跳过或弱化已有有效测试。

**关键模块覆盖**：远程管理 145/145；完整套件中的数据层、训练器、Worker/协调层及 API 所有可执行用例通过，覆盖旧扁平与新结构数据集、分类/回归/聚类/时间序列、LLM、发起训练/状态/日志/指标/产物和预测回归。新镜像另外直接运行真实训练，不依赖平台中已安装的 API/数据库包。

### 验收标准—测试用例映射

| 验收标准 | 测试层级、前置条件及输入 | 预期业务结果 / 实际结果 | 场景 |
|---|---|---|---|
| 同步只包含训练运行文件 | 单元/归档集成：`test_worker_packages_exclude_control_plane_and_keep_training`，构建包、压缩代码包两种输入 | 保留 worker、训练器、LLM、队列、存储和入口；排除页面、管理 API、数据库、测试、数据与秘密文件；2/2 通过 | 正常、安全、兼容 |
| 缺失或不安全源码不发送 | 单元：`test_worker_runtime_rejects_unsafe_or_incomplete_sources`，缺失、符号链接、目录、根目录链接；`test_worker_runtime_validates_optional_training_modules`，两个可选模块的普通文件/断链/目录 | 正常可选模块随包发布，其余拒绝打包；10/10 通过 | 边界、资源不存在、安全、新分支 |
| CPU/GPU 依赖按目标类型选择 | 单元+真实 Docker：`test_remote_build_selects_worker_runtime`、`test_remote_build_rejects_invalid_runtime`，cpu/gpu、空值/auto/命令注入；CPU 镜像依赖检查 | 参数正确，非法值构建前失败；CPU 安装官方 CPU torch 且无 CUDA；全部通过 | 正常、空值、非法参数、安全 |
| 精简包能训练并稳定预测 | 进程集成：`test_packaged_worker_trains_without_control_plane`，屏蔽平台依赖，分类/回归各训练两次，输入正常行/空行/非法字段 | 产生 metrics、accuracy 或 R²/RMSE 和非空 best.pt；固定 seed 最终指标一致；同模型重复预测一致；2/2 通过，另在新 CPU 镜像复验通过 | 训练链路、可复现、容错、预处理、回归 |
| 保留 LLM 能力 | 真实 CPU 镜像：复用 `test_pretraining_from_scratch_saves_metrics_bundle_and_generates` 的原有业务断言，离线微型语料，两次预训练 | loss/perplexity 有限、模型包与 checkpoint 可读、固定 seed 一致、重复生成一致；通过 | 正常、可复现、依赖兼容 |
| 阻止 CPU 镜像误启 GPU 节点 | shell 集成：`test_gpu_actions_reject_cpu_image_and_accept_legacy`，deploy/restart × cpu/gpu/历史无标签镜像 | CPU 镜像明确拒绝 GPU 操作，且不停止旧容器；GPU/历史镜像保留兼容行为；6/6 通过 | 状态冲突、兼容、生命周期 |
| 保持原构建与管理流程 | 现有远程套件，含传统 Docker builder、源参数、启动/停止/配置与回滚 | 所有相关用例通过；平台运行容器能找到专用配方并产生精简归档 | 回归、兼容、异常 |

### 实际命令与容器验证

最终快照目录为 `/private/tmp/mtp-worker-slim-yNMojo/`（`/tmp` 对应同一目录）。实际专项及完整测试命令：

```bash
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-worker-slim-yNMojo,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-slim-targeted-final
docker run --rm --pull=never --network none --mount type=bind,src=/private/tmp/mtp-worker-slim-yNMojo,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-slim-full-delivery
docker compose up --build -d
docker compose -f docker-compose.yml -f /private/tmp/mtp-worker-slim-yNMojo/compose-validation.yml up --build -d
docker image inspect mtp-worker:slim-validation --format 'bytes={{.Size}} mode={{index .Config.Labels "org.mtp.worker.mode"}} arch={{.Architecture}}'
docker run --rm -i --pull=never --network none --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --mount type=bind,src=/private/tmp/mtp-worker-slim-yNMojo,dst=/workspace,readonly mtp-worker:slim-validation python -u - < /private/tmp/mtp-worker-slim-yNMojo/validate_runtime.py
git diff --cached --check
git status --short
```

- **默认 Compose 从零构建未通过**：torch 的 454.0 MB 下载只得到 11.5 MB，pip SHA256 校验拒绝，进入重试；为避免长期无效等待取消构建，退出 130。不能将其记为默认 Dockerfile 从零构建成功。
- **统一容器重建与启动通过**：临时 Compose 覆盖仅以现有平台镜像 `sha256:8eb9862184e78e09be326b0f239a4bd68ab8bae94a1b67871d38c1154be03496` 为依赖基底，覆盖本任务快照的 app、静态文件及构建资源。基底 `/app/requirements.txt` 与仓库 SHA256 均为 `7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`。统一构建并重建 trainer/worker、启动全部 Compose 服务成功；实际 `/api/health` 返回 200，前端 `/healthz` 返回 ok；平台容器内专用配方与清单存在，可生成下表中的精简包。仓库 Compose 配置未改动。
- **本地等价启动通过**：宿主机无 Python 3.12，因此未使用宿主机旧版 Python。一次性隔离容器中以 Python 3.12 执行 `USE_DATABASE=false python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000`（workdir 为挂载的 `backend`，额外设置本地临时 DATA_DIR、STORAGE_BACKEND=local、REDIS_URL 为空），健康业务断言通过。没有 Python 版本差异，未将语法检查当成功能测试。
- **独立 CPU 镜像实际构建通过**：首次直接下载官方 CPU torch 两次截断，pip 校验拒绝后重试，随后取消。改用仅本机临时 CONNECT 代理（保留端到端 TLS、仅允许 PyTorch/PyPI 域名）后，使用完全相同的专用 Dockerfile 和 `_worker_build_context` 生成的 tar stdin，执行 `docker build --progress=plain --tag mtp-worker:slim-validation --build-arg WORKER_MODE=cpu --build-arg PIP_INDEX_URL=https://pypi.org/simple --build-arg HTTPS_PROXY=http://host.docker.internal:18947 -` 成功。没有改写 CPU 索引、使用 GPU 包替代或跳过 hash 校验。新 CPU 镜像中直接验证依赖与三类训练全部通过。

### 体积实测、限制与回滚

| 项目 | 调整前 | 本提交独立快照 | 减少 |
|---|---:|---:|---:|
| 首次上传的原始 tar 构建包 | 757,760 bytes（740 KiB） | 225,280 bytes（220 KiB） | 70.3% |
| 后续 gzip 代码包 | 127,501 bytes（124.5 KiB） | 57,777 bytes（56.4 KiB） | 54.7% |
| 本地 ARM64 worker 镜像 | 10,152,772,321 bytes | 2,252,195,111 bytes（CPU） | 77.8% |

归档体积会随后续源码变化；可选联合训练模块存在时也会按白名单携带。以上是本提交快照的数据，不是所有架构/服务器的固定大小保证。镜像减少主要来自去除 CPU 节点不需要的 CUDA/NVIDIA/Triton 依赖；GPU 镜像仍保留 GPU 所需运行时。

**已知限制与未执行项**：完整套件保留 7 个原有环境跳过项（4 个真实 Redis、2 个 S3 集成、1 个 Supervisor E2E），本次没有配置相应测试环境补跑；没有 NVIDIA GPU，未验证真实 CUDA 或 x86_64 镜像，也未执行真实远端安装/网络下载。不能将镜像本地构建通过等同于所有远端网络可访问。默认平台依赖从零构建仍受当前网络截断影响，等价依赖基底的 Compose 验证已通过。

**行为与后续建议**：新建 CPU 节点使用精简 CPU 依赖；既有节点部署代码仍复用现有镜像，不会自动减少旧镜像占用。旧节点需在维护窗口重新构建并替换容器，确认训练已退出后再清理不用的镜像；本次未代用户执行。CPU 镜像切换 GPU 必须先提供 GPU 版镜像。建议在目标架构及稳定网络下验证首次安装，并按需补跑 Redis/S3/Supervisor 与真 GPU 场景。

**重测触发条件与回滚**：修改远程打包白名单、构建配方/依赖、模式兼容性、Worker、训练/预测、数据布局或业务配置后，重新执行对应套件、完整测试和镜像业务验证；用户明确要求时也重测。回滚可撤销 `fae7d1e` 后统一重建平台；既有远端镜像和数据未改动，无数据迁移。

**清理与提交**：临时快照、CPU wheel、验证脚本、补丁、Compose 覆盖及本机代理均在 `/private/tmp/mtp-worker-slim-yNMojo/`，验证完成后清除，代理和隔离 API 容器停止，临时验证镜像标签删除；pytest/训练产物随一次性容器清理。本任务代码与报告分开提交，不执行 git push。共享工作区的其他会话改动保留，未纳入本次提交。

## 本次变更测试结果：训练启动失租与接手任务写入隔离（2026-10-29）

### Git 基准点与变更范围

- **Commit Hash**：`9013f1c8b0db669523ffba2c81ad0f46e25d97c4`。
- **基准提交信息**：Fix stalled SSH input streaming during remote deployment。
- **本次修复提交**：`eb813869fbc31fb21aae306579b4567b173b17de`（Fix training lease lifecycle and authoritative logs）。
- **测试日期 / 分支**：2026-10-29 / `emdash/hot-boxes-clean-y9u6p`。
- **基准说明**：本次训练修复完成后，工作区另行合入远程部署修复 `9013f1c`，因此对该最新提交补跑完整套件。预先存在的 `backend/Dockerfile` 和 `backend/tests/test_remote_workers.py` 未提交修改参与了工作区测试，但不属于本次修改或提交；不能将结果解释为不含这两项改动的纯净 checkout 验收。
- **范围**：`backend/app/worker.py`、`backend/app/routers/jobs.py` 及现有 Worker/API 测试。取得租约后立即续租，心跳覆盖准备和收尾阶段；共享状态、日志、指标、模型和断点清理写入前校验 token；失租者退出，恢复交给 watchdog；scratch 按执行批次隔离，末级仍为 job_id 以兼容数据库日志；断点发布前上传对应指标和最佳模型；文件日志优先，数据库仅为缺失文件的旧任务回退；结束日志先于结束状态发布。
- **技术环境**：Python 3.12.15、PyTorch 2.14.1+cu130（CPU）、FastAPI、pytest 9.1.1；测试使用本地仓储/内存协调层和隔离 Redis，现场任务使用部署中的 Redis、S3 与数据库。没有新增依赖、修改配置文件或数据库结构。

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 错误 | 跳过 | 结果说明 |
|---|---:|---:|---:|---:|---:|---|
| 修复前新增回归测试 | 9 | 2 | 7 | 0 | 0 | 稳定复现慢启动失租、四种旧槽覆盖路径，以及数据库覆盖完整/空文件日志 |
| 第一轮修复后新增回归 | 9 | 9 | 0 | 0 | 0 | 原有复现全部转为通过 |
| 首次相关套件 | 152 | 148 | 1 | 0 | 3 | 取消状态先可见、结束日志后发布的时序缺陷；随后调整发布顺序，未弱化测试 |
| 相关套件修复后 | 152 | 149 | 0 | 0 | 3 | Worker、JobManager、协调层、训练器、API 全部可执行用例通过 |
| 首次完整套件 | 340 | 333 | 0 | 0 | 7 | 新增真实 Redis 慢启动参数分支在未配置 Redis 时跳过 |
| 隔离 Redis 专项 | 8 | 8 | 0 | 0 | 0 | 含真实 Redis 租约、注册、清理与慢启动；补齐完整套件中的 4 个 Redis 环境跳过项 |
| 合入并行修复后的完整套件 | 351 | 344 | 0 | 0 | 7 | 基准 `9013f1c`；49.17 秒，实际执行用例通过率 100%，含跳过的总通过比例 98.01% |

完整套件保留 2 条已有弃用警告：SQLAlchemy `declarative_base()` 和 Pandas `xlrd`。最初两次启动测试容器引用了已被并行重建替换的历史镜像 ID，Docker 退出 125，未执行测试；改用现有 trainer 镜像后正常执行。

**实际测试命令**（项目根目录执行；一次性容器、仓库只读、测试数据与生产隔离）：

```bash
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_worker.py tests/test_api.py -k 'slow_startup or superseded_worker or authoritative_file' -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-lease-regression
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_worker.py tests/test_jobmanager.py tests/test_coordination.py tests/test_trainer.py tests/test_api.py -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-lease-targeted
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-lease-full
docker run -d --rm --pull=never --name mtp-lease-test-redis redis:7-alpine redis-server --save '' --appendonly no
docker run --rm --pull=never --network container:mtp-lease-test-redis --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env MTP_TEST_REDIS=redis://127.0.0.1:6379/15 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_coordination.py tests/test_worker.py -k 'redis or slow_startup' -q --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-lease-redis
docker stop mtp-lease-test-redis
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v --tb=short -p no:cacheprovider --basetemp=/tmp/mtp-lease-final
git diff --check
```

第一轮修复前回归命令与上述对应命令相同，但未传 `--tb=short`。`git diff --check` 通过。

### 验收标准—测试用例映射

| 验收标准 | 层级、前置条件与用例 | 输入 → 预期结果和副作用 | 场景 |
|---|---|---|---|
| 准备超过初始 TTL 仍可完成 | Worker 集成，真实训练；`test_worker_renews_lease_during_slow_startup[embedded/redis]` | TTL 1 秒、历史下载等待 1.5 秒 → 租约有效、2 个 epoch 连续、completed、best.pt 存在 | 正常、时间边界、慢依赖、Redis 兼容 |
| 旧执行者不覆盖接手任务 | 双 Worker 集成；`test_superseded_worker_cannot_overwrite_successor[metrics/checkpoint/failure/retry]` | 真实写入后阻塞返回、回收租约、watchdog 重派、新槽训练 → 旧槽退出时共享文件逐字节不变、新目录保留，最终 1–6 轮连续且模型存在 | 并发、异常、状态冲突、重试、回归 |
| 失租写入失败关闭 | 仓储与真实内存租约；`test_lease_guard_rejects_unowned_mutations` 的 9 个参数组合 | 过期/新 owner/协调面不可达 × 状态/模型/断点清理 → 抛 JobLeaseLost、旧模型不变、不新增暂停状态 | 所有权隔离、安全、依赖失败、边界 |
| 心跳与信号不会被错误配置破坏 | 控制器；`test_controller_caps_heartbeat_interval_and_clears_old_signals` | 心跳 10 秒、TTL 0.3 秒、旧暂停信号 → 自动缩短周期、准备期不误暂停，清理后取消信号有效 | 配置边界、信号兼容 |
| 失租能自动接续，手动控制仍有效 | Worker/JobManager/API；`test_lease_lost_yields_to_watchdog`、既有暂停/恢复/取消与重试测试 | 失租后旧槽不写 paused，watchdog 排队，新槽完成 300 轮；手动暂停仍保存断点、恢复和取消生效 | 正常、异常、回归 |
| 中断原因可见且清日志不复活旧内容 | SQLite + API；`test_job_logs_prefer_authoritative_file_with_legacy_fallback`、`test_job_logs_paginate_authoritative_file`、既有清日志测试 | 文件包含心跳原因/空文件/无文件、数据库有旧记录；505 行分页、负/越界偏移 → 正确来源、顺序和游标 | 正常、空值、兼容、分页边界 |
| 训练预测和数据兼容保持正确 | 完整套件：Trainer、LLM Trainer、Storage、API | 分类 accuracy、回归 R²/RMSE、模型产物、固定 seed、断点与完整训练比较、重复预测、缺失输入处理、新旧数据布局 | ML 全链路、可复现性、预处理一致性、回归 |

### 容器构建与现场任务验证

1. 已执行 `docker compose up --build -d`。默认依赖层从阿里云镜像下载 454 MB PyTorch，约 4 分钟未完成，停止该次构建（退出 130）；不记为默认从零构建通过。
2. 校验运行镜像与仓库 `requirements.txt` 的 SHA256 均为 `7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`，复用已验证依赖镜像，临时 Compose 覆盖中通过 `dockerfile_inline` 复制全部 app/static/sample_data 及启动脚本。
3. 执行 `docker compose -f docker-compose.yml -f /tmp/mtp-lease-build-eb81386/compose.yml up --build -d` 成功，trainer/worker 已重新创建，frontend/SeaweedFS 正常。此为完整 Compose 容器重建，不是宿主机启动 uvicorn。临时覆盖不修改仓库 Dockerfile、Compose 或 `.env`。
4. `docker exec mtp-worker sha256sum /app/app/worker.py` 与源码一致：`ed13502c18073f5a0c4bd4d256261f927d6921d3ef2964c8b0188480226303eb`；`docker exec mtp-trainer sha256sum /app/app/routers/jobs.py` 与源码一致：`fdc967c382343a2d43f01a9aa3051394fe81ad079968ad5b17a0937fb67c8131`。`curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 15 http://127.0.0.1:8000/api/health` 返回 `{"ok":true}`。
5. 已按批准方案执行 `curl -fsS --max-time 30 -X POST http://127.0.0.1:8000/api/jobs/job-20261029-083952-b64fb1/resume`，恢复用户原任务。持续查询详情和日志后确认 **completed、30/30 轮、100%**；`redispatched` 保持原来的 1、`retry_count=0`，没有新增重派或自动重试。
6. 最佳 epoch 为 **7**，最终以最佳权重评估的 accuracy 为 **0.8333333333333334（83.33%）**，loss 为 0.4520623981952667；训练/验证 240/60 条，5 特征，CPU，耗时 454.9 秒。第 30 轮本身的 accuracy 为 75%，与最终加载最佳权重的 83.33% 不混淆。
7. 使用 `docker exec -i -e PYTHONDONTWRITEBYTECODE=1 mtp-trainer python -u -` 经 Python 3.12 标准库访问实际 `/model`、`/predict`、`/logs`：模型下载首次 30 秒超时，改为 60 秒后成功下载 **139,939 字节**；`torch.load(io.BytesIO(model_bytes), map_location='cpu', weights_only=True)` 确认 classification、5→128→128→128→2 网络。输入 `{"row":{"年龄":35,"月收入":8000,"月均访问":10,"投诉次数":0,"会员类型":1}}` 连续预测两次，均为“否”，概率均为 `否=0.5208588242530823、是=0.4791412055492401`，完整结果相等。实际日志含最终准确率与“训练完成”。

### 已知限制、未执行项及后续建议

- 默认网络依赖下载的从零构建尚未完成；本次容器交付复用了内容匹配的依赖镜像。建议网络稳定后再验证默认 Dockerfile 全量依赖构建。
- 完整套件的 7 个跳过项中，4 个 Redis 环境用例已通过专项补跑。**2 个独立 S3 集成测试和 1 个 Supervisor 子进程终止/重启测试未执行**；现场任务已通过真实 S3 的训练、模型下载和预测链路，但不代替上述独立故障测试。测试镜像缺少 Supervisor E2E 所需 `pgrep`，本次未为测试引入系统依赖。
- 没有 NVIDIA GPU，未验证真实 CUDA、多机跨卡或长时间网络分区。实际任务在 CPU 完成；GPU 不可用提示的文案与 GPU 支持不在本次修改范围。
- 本次所有权保护在每次共享写入前校验 Redis token；不能撤销已经发送且在租约失效后才完成的远端 PUT。若需要覆盖此类在途写入，应进一步设计存储端条件写入/版本隔离，并注入跨存储请求的网络分区测试；不能将本次保护宣称为 Redis 与 S3 的跨系统事务。
- 后续修改 Worker、任务状态、日志路由、协调/存储层、训练/预测或业务配置时需重新执行相关套件、完整测试并更新基准；用户明确要求时也重测。
- 代码回滚可撤销 `eb81386` 后统一重建服务。本次未更改数据结构；原任务已生成的模型、指标和训练报告保留。

**清理与提交**：pytest 临时目录 `/tmp/mtp-lease-*` 随一次性容器清理；隔离 Redis 容器已停止并自动删除；临时构建目录 `/tmp/mtp-lease-build-eb81386/` 和基底镜像标签已删除；现场下载/预测验证新生成的 `data/worker-scratch/serve/job-20261029-083952-b64fb1/` 缓存已删除。没有提交模型、数据、日志或其他运行时产物；业务代码与本测试报告分开提交，不执行 git push。已有 Dockerfile/远程测试改动保留。

## 用户重启远端后的复测（2026-10-29）

**范围与基准**：用户告知已重启 `47.101.33.133`，要求继续尝试。本次只做运行状态复测，不修改业务代码、配置或依赖；业务基准仍为 `9013f1c8b0db669523ffba2c81ad0f46e25d97c4`，不重新运行已通过的完整 pytest 或重建镜像。

**验证结果**：

| 检查 | 实际结果 | 结论 |
|---|---|---|
| 初次 SSH/MySQL/Redis 协议响应 | 22/3306/6379 端口分别收到 42/95/34 字节协议响应 | 重启后曾恢复可连接 |
| GET `/api/workers` 首次查询 | `Linux-cpu0`，active=true，started_at=1793267671.1449924，last_seen=1793267691.2174897 | 新配置下 CPU 槽已启动并注册 |
| GET `/api/workers` 后续查询 | 同一槽 active=true，last_seen=1793267795.0427866，heartbeat_age_sec=0.8，current_job=null | 心跳至少推进约 104 秒；不是旧记录被误认在线 |
| 资源槽启用状态 | enabled=false、state=disabled | 受管理槽默认不接单，不代表容器启动失败；未擅自启用接单 |
| GET `/api/remote-servers` 初次查询 | deployed=true，status=error，上次操作仍为 `f4a8ee96a44b434499dea5f5fcc693a1` | 保留前次心跳验收超时记录，不能用该历史状态否定新心跳 |
| 训练任务检查 | GET `/api/jobs` 无 queued/running/starting 任务 | 本次没有中断训练，也未发起新训练 |
| 只读 SSH 容器/资源查询 | 一次 SSH 查询 35 秒超时；另一次尚未取得凭证即卡在 MySQL 握手，随后清理诊断进程 | 未取得新的容器日志、内存、负载、挂载与重启计数 |
| POST `/api/remote-servers/Linux/restart` | 为验证完整生命周期提交请求；客户端 20 秒超时，无操作 ID 响应 | 执行结果未知，HTTP 超时不等于服务端未执行；未重复提交 |
| 限时 MySQL 新连接诊断 | 连接等待初始通信包报 `2013 (HY000): Lost connection to MySQL server at 'waiting for initial communication packet'` | 本次失败发生在 SQL 业务查询前；不能仅归因于旧连接池 |
| 后续协议与 API 复查 | SSH/MySQL/Redis 多次 3–6 秒协议超时，`/api/workers` 返回 500、`/api/remote-servers` 超时；直接 Redis 管线读取也超时 | 远端管理连接仍不稳定，持续可用性未验收 |

**实际执行方式**：运行容器中 Python 3.12 使用 `socket.create_connection` 检测指定三端口，Redis 仅发送无凭证 PING 并统计响应字节；`urllib.request.urlopen` 查询上述 API。只读 SSH 尝试 `uptime`、`free -m`、`docker inspect` 指定状态/挂载字段、目标 `.env` 非敏感字段及 `docker logs --tail=35`；未成功取得结果，不记为通过。SQLAlchemy 在单次诊断进程中使用 `connection_timeout=8/read_timeout=10/write_timeout=10`，没有修改平台配置。所有秘密仅用于现有服务器认证，没有输出凭据；没有保存调试脚本或数据文件。结束前运行 `git diff --check`、`git status --short`，仅提交本报告。

**结果界限与下一步**：已证实修复后的 worker 能在主机重启后自动启动并注册一个 CPU 槽，但主机持续可访问、容器重启次数稳定及完整管理操作尚未验收。网络故障与主机资源压力均未证实，不能将其当成既定根因。已请求用户通过云控制台提供 `uptime`、`free -h`、`docker stats --no-stream` 输出；待连接恢复后先检查超时 restart 请求是否产生操作记录，再检查容器日志与心跳，避免重复提交。SSH 传输代码无需因本次外部连接超时而重复修改；若需变更配置或扩展修复范围，按项目规则确认。

## SSH 大输入传输修复与远端恢复验证（2026-10-29）

- **Commit Hash**：`9013f1c8b0db669523ffba2c81ad0f46e25d97c4`（Fix stalled SSH input streaming during remote deployment）。
- **分支**：`emdash/hot-boxes-clean-y9u6p`；Python 3.12.15；用户已确认下文修复、测试及重新部署方案。
- **范围**：`backend/app/remote_workers.py` 和 `backend/tests/test_remote_workers.py`；没有新增依赖或修改仓库配置。工作区原有 Dockerfile 重试及相关测试断言改动保留，参与工作区测试但未纳入本次代码提交。
- **结论**：SSH 传输卡死已修复，完整自动测试通过，真机已收到完整构建包。已复用远端现有镜像下发新配置和代码并重建容器；远程资源心跳尚未验收，不能标记端到端恢复完成。

**关键实现**：Python 标准库独立发送线程独占 stdin，通过非阻塞 `os.write` 和 `select` 持续发送完整输入，结束时关闭管道；主线程继续 `communicate` 读取实时 stdout/stderr。超时、取消和 OS 写入错误会回收子进程及发送线程；远端提前退出的 BrokenPipe 保留原退出码与 stderr；日志回调异常仍按既有约定隔离。文本按流编码发送，二进制 tar 不做转换。方法及核心分支均含中文说明。

**测试结果**：

| 执行范围 | 总数 | 通过 | 失败 | 错误 | 跳过 |
|---|---:|---:|---:|---:|---:|
| 修复前定向复现 | 11 | 6 | 5 | 0 | 0 |
| 修复后远程专项 | 119 | 119 | 0 | 0 | 0 |
| 最终完整 pytest | 351 | 344 | 0 | 0 | 7 |
| 隔离 Python 3.12 API 冒烟 | 1 | 1 | 0 | 0 | 0 |

完整套件 69.03 秒，已执行用例通过率 100%，含跳过为 98.01%。7 项环境跳过为真实 Redis 协调/租约/Supervisor 和真实 S3 用例，未配置 `MTP_TEST_REDIS` / `MTP_TEST_S3`；保留既有门控，未删除或弱化有效测试。两条既有警告：SQLAlchemy declarative_base、Pandas xlrd 弃用。

修复前两个大输入用例稳定超时，两个超时/取消清理用例发现 stdin 未关闭；另一个初版回调用例错误地期待日志异常向上传播，与既有 `_emit_progress` 隔离日志故障的约定不符，修正为验证回调被执行、异常被隔离、随后超时正常回收。原二进制 Mock 用例改为真实进程校验含 NUL、非 UTF-8 字节和 CRLF 的完整往返，断言强度提高。最终增加 OS 写入错误验证。专项通过后仅补充发送线程退出与异常收集顺序，最终完整套件覆盖了该最终版本。

**实际测试命令**（项目根目录；只读挂载、隔离网络/数据）：

```bash
# 修复前：5 failed, 6 passed, 107 deselected
docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py \
  -q -p no:cacheprovider --basetemp=/tmp/mtp-ssh-red \
  -k 'remote_input or preserves_binary_build_context'
# 修复后专项：119 passed
docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py \
  -q -p no:cacheprovider --basetemp=/tmp/mtp-ssh-green --tb=short
# 最终完整套件：344 passed, 7 skipped
docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ \
  -v -p no:cacheprovider --basetemp=/tmp/mtp-ssh-full --tb=short
git diff --check
```

**验收标准—最终可执行用例映射**：

| 标准 | 层级、前置条件及输入 | 预期结果与实际结果 | 场景 |
|---|---|---|---|
| 跨轮询完整传输并收到 EOF | 真实子进程 `test_remote_input_survives_poll_timeout`；接收端延迟 0.6 秒，0/1024/716800 重复单元，文本/二进制六组 | 字节数及 SHA256 一致、stdin 关闭、退出 0；6/6 通过 | 空值、边界、大输入、兼容 |
| 二进制无损传输 | `test_run_remote_subprocess_preserves_binary_build_context`，NUL/0xff/CRLF | 收到原始字节、退出 0、stderr 空；通过 | tar 格式兼容 |
| 实时日志不重复 | 上述六组接收前输出 ready/waiting；既有 CR/部分行用例 | 子进程未退出时收到回调，完整输出无重复；通过 | 正常、回归 |
| 卡住时可取消与超时清理 | `test_remote_input_cleanup_when_receiver_stalls`，2 MiB 输入、接收端不读，timeout/cancel/callback 三组 | 有界结束、管道关闭、无发送线程/子进程遗留；3/3 通过 | 超时、中断、日志故障 |
| 提前退出和写入失败可诊断 | `test_remote_input_early_receiver_exit_preserves_error`、`test_remote_input_write_error_is_reported_and_cleaned_up` | 保留错误退出码/消息或返回脱敏发送错误，回收资源；通过 | 依赖失败、安全 |
| 既有构建/部署行为兼容 | 远程专项与完整套件 | Buildx 兼容回退、脱敏、凭证、配置、训练/预测/数据回归通过 | 兼容、安全、回归 |
| 真机服务正常运行 | 真实 Linux Docker，配置+代码同步及重建 | 容器已创建并运行，但 30 秒心跳未通过，随后远端服务响应超时；尚未验收 | 外部环境、部署 |

**平台重建与等价验证**：

1. 执行 `docker compose up --build -d`；阿里云 pip 镜像下载 454 MB torch 包连续约 3 分钟无新增进展，主动中断构建，退出 130。未记为默认联网重建通过。
2. 运行镜像、工作区以及远端旧镜像的 `requirements.txt` SHA256 均为 `7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`。无依赖变更，采用下文既有“离线依赖复用”临时 stdin Compose 覆盖：`docker compose -f docker-compose.yml -f - up --build -d`，Dockerfile inline 从现有 trainer 镜像复制最新 app/static/sample_data/Dockerfile/启动脚本。实际重建成功，trainer/worker 重建运行，frontend/SeaweedFS 健康。仓库 Compose、环境和卷配置未改。
3. trainer `/api/health` 返回 `{"ok":true}`；运行 `remote_workers.py` 与工作区 SHA256 相同：`914dda0510cfabffde08f15022281e46f1330fc56dff18f46d0de2b03268c29e`。
4. 本地等价验证仍使用 Python 3.12.15：`docker run --rm -d --name mtp-ssh-smoke --network none --env USE_DATABASE=false --env REDIS_URL= --env STORAGE_BACKEND=local --env DATA_DIR=/tmp/mtp-ssh-smoke --env EMBEDDED_WORKERS=0 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`；容器内 urllib 查询 `/api/health` 成功，随后 `docker stop mtp-ssh-smoke` 清理。该冒烟不能替代完整套件或联网构建。

**真实远端恢复过程**（UTC）：

- 检查原操作已因其它平台重启变为 failed；远端没有残留 `docker build`，无需杀构建进程。平台没有活跃训练任务，Linux 没有有效资源槽。旧容器镜像为 `sha256:784a8a1627901c0cf688b4155b956824b38c27e1972a9ab68e56551ae6929d1b`，保留镜像供回退；旧容器仍有无效 SeaweedFS 配置，重启次数已达 5470。
- 经 POST `/api/remote-servers/Linux/install` 发起 `b5961e5f123849b7b94aa52433df26e7`。09:38:33 日志出现 `Sending build context to Docker daemon 747.5kB`、`Step 1/17` 和 `Step 2/17`，确认真实 SSH 构建包传输已完成。09:39:04 基础镜像拉取失败：访问 Docker Hub `registry-1.docker.io` 的 HTTPS 请求 i/o timeout。缺少 Buildx 已正常回退传统构建器，没有再卡在 stdin。
- 复用依赖清单相同的既有远端镜像。因平台 `deployed=false` 与真实旧容器存在不一致，直接调用已有 `execute_remote_operation` 执行 config/deploy 恢复路径，仍保留协调存储操作日志、状态持久化和心跳检查；没有修改路由或手写伪造成功状态。
- config 操作 `d72dab363a994e4b98de4d2db11d6fc5` 于 09:41:09 完成，写入 `~/.config/mtp-worker/Linux.env`；安全摘要为 CPU 槽 1、OSS S3 endpoint、bucket xyzc、HTTPS true、Redis 和 S3 凭证已配置。
- deploy 操作 `f4a8ee96a44b434499dea5f5fcc693a1` 同步最新代码并复用旧镜像重建；09:41:44 新容器 `52c25a5b176318b297bc3f63af28408655ee2a4f05f47422f46f4029c60c05f7` 快照 `state=running restart_count=0 exit_code=0`，脚本正常返回。30 秒未收到心跳，记录 `heartbeat_ok=false`，服务器状态 error，deployed 标记已更新；不能把操作 completed 理解为 worker 在线。
- 随后只读 SSH 检查在 35 秒超时；平台 `/api/workers` 因 Redis 响应异常返回 500、`/api/remote-servers` 读取超时。SSH/MySQL/Redis TCP 可连接，但容器内协议响应均出现 5 秒超时；宿主侧 SSH 等待 banner 30 秒超时，Redis 曾有一次 2.6 秒响应。尚无足够证据区分网络异常与主机资源压力，未推断根因，也未执行主机重启或修改数据库/Redis配置。已请用户从云控制台确认主机资源和状态。

**已知限制、重测条件与后续**：当前代码测试通过，平台和远端容器重建已执行；远端 worker 持续运行、OSS/Redis 可访问、新代次心跳尚未完成验收。从头联网构建受镜像/依赖下载阻碍。远端恢复响应后，应读取新容器命令/挂载和日志，确认环境已加载、重启次数稳定、CPU 槽 1 上线；需要时使用已部署服务器的 start/restart 流程恢复。若发现新的代码或配置问题，按范围重新确认方案。SSH 发送、取消、超时、构建或启动逻辑再变更时必须重新运行专项和完整测试。不能将本节标记为远程恢复完成。

**清理与提交**：测试数据仅位于一次性容器 `/tmp/mtp-ssh-red`、`/tmp/mtp-ssh-green`、`/tmp/mtp-ssh-full`，已随 `--rm` 删除；冒烟容器已删除。所有诊断脚本经 stdin，SSH 私钥仅在 memfd 内存中，已关闭；两次卡在只读数据库获取的诊断 Python 进程已终止。未留下调试文件、未提交数据/秘密/运行时结果；代码提交 `9013f1c`，本报告单独提交。既有 Dockerfile 和一处测试断言差异属于原工作区改动，保留不纳入提交。

## 远程部署卡住与旧 worker 启动失败诊断（2026-10-29，修复前记录）

**检查版本**：`eb813869fbc31fb21aae306579b4567b173b17de`；分支 `emdash/hot-boxes-clean-y9u6p`。本节是只读诊断及缺陷复现，不更新下文完整测试验收基准，不代表当前工作区通过完整测试。本次仅修改本报告；既有 Dockerfile、远程测试及其它任务的变更不纳入本次提交。

**现场证据**（2026-10-29 17:12–17:24，Asia/Shanghai）：

- 平台唯一登记的 `Linux` 服务器为 `deployed=false`，最近操作 `c71dfb971c3b46af84b45b90f22cae6f` 为 `install/running`，停留在“构建目标架构镜像”。09:12:31 UTC 报 Buildx 缺失，09:12:32 自动切换传统构建器，此后只有每 30 秒一次的通用等待提示。该提示不证明正在下载依赖。
- 只读 SSH 确认目标 Docker client/server 均为 29.1.3，`docker buildx` 不存在。远端 `docker build --tag mtp-worker:local -` 已等待超过 7 分钟，stdin 为管道，读取量约 96 KB，未完成约 0.7 MiB 构建上下文的接收。平台 SSH 子进程读取量也远小于完整构建包。
- 目标机已有 2026-10-27 创建的旧容器 `mtp-worker-Linux`；检查时短暂显示 running，但 `RestartCount=5449`，日志持续报 `NameResolutionError` / `MaxRetryError`：无法解析 `seaweedfs:8333`，失败发生在 `S3ArtifactStore._ensure_bucket()`。因此 running 快照不能证明服务正常。
- 旧容器启动命令为 `python -m app.worker`，只挂载 `/app/data`，没有 `/app/config`；目标 SSH 用户目录下 `~/.config/mtp-worker/Linux.env` 不存在。安全筛选的容器环境为 `S3_ENDPOINT=seaweedfs:8333`、`S3_BUCKET=mtp-artifacts`、`S3_SECURE=false`、`WORKER_CPU_SLOTS=0`。
- 平台待下发配置已是 OSS S3 地址、另一 bucket、HTTPS、CPU 槽 1。平台记录未部署时，PATCH 仅保存配置；当前 install 卡在构建阶段，尚未走到写配置和创建新容器。旧容器继续使用原始配置，可以解释“更新配置后部署仍启动不了”。
- 运行 trainer 与工作区的 `remote_workers.py`、`remote_worker.sh`、`worker-entrypoint.sh` SHA256 一致，本次卡住不是这三个文件的运行版本落后。

**代码根因**：`backend/app/remote_workers.py::_communicate_remote_process` 每 0.25 秒调用 `Popen.communicate(input=pending_input, timeout=...)`，第一次超时后把 `pending_input` 置为 `None`。Python 3.12 的 `_communicate` 仅在本轮 input 非空时注册 stdin 可写事件；大输入在首次轮询内未发送完时，后续不再发送剩余数据，也不会关闭 stdin。远端等待剩余 tar 输入，平台等待远端输出，最终只能超时。缺少 Buildx 已有兼容回退，不是唯一阻塞因素。

**实际执行与结果**：Python 3.12.15；3 个行为探针，2 通过、1 个预期业务目标失败（稳定复现缺陷），0 跳过。小输入和标准 communicate 对照通过；原函数大输入失败。另一次直接用 Python 标准库复现相同轮询时序也停在 65,536/716,800 字节。

以下命令在一次性、断网容器内提取并执行运行镜像的原函数；仅将取消查询和进度输出替换为无副作用边界，stdin 发送、轮询、超时及真实子进程均使用原实现。收到的完整内容用字节数和 SHA256 校验。

```bash
docker run --rm -i --network none --entrypoint python emdash-hot-boxes-clean-y9u6p-trainer:latest -u - <<'PY'
import ast, hashlib, pathlib, re, subprocess, sys, time
from typing import Callable
source = pathlib.Path('/app/app/remote_workers.py').read_text()
function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == '_communicate_remote_process')
namespace = {'Callable':Callable,'subprocess':subprocess,'time':time,'re':re,'_remote_operation_cancel_requested':lambda operation_id:False,'_emit_progress':lambda *args:None}
exec(compile(ast.Module(body=[function],type_ignores=[]),'remote_workers.py','exec'),namespace)
communicate = namespace['_communicate_remote_process']
child = [sys.executable,'-c','import hashlib,sys,time; time.sleep(0.6); data=sys.stdin.buffer.read(); print(len(data),hashlib.sha256(data).hexdigest(),flush=True)']
for size in (1024,716800):
    payload=b'x'*size
    process=subprocess.Popen(child,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        output,_=communicate(process,None,2,input_data=payload)
        expected=f'{size} {hashlib.sha256(payload).hexdigest()}'
        print('actual_function',size,'PASS' if output.decode().strip()==expected else 'FAIL')
    except subprocess.TimeoutExpired:
        print('actual_function',size,'FAIL: timed out; sent',process._input_offset,'of',size,'bytes')
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)
payload=b'x'*716800
result=subprocess.run(child,input=payload,capture_output=True,timeout=4,check=True)
expected=f'{len(payload)} {hashlib.sha256(payload).hexdigest()}'
print('standard_communicate_control',len(payload),'PASS' if result.stdout.decode().strip()==expected else 'FAIL')
PY
```

输出：`actual_function 1024 PASS`；`actual_function 716800 FAIL: timed out; sent 65536 of 716800 bytes`；`standard_communicate_control 716800 PASS`。初次探针缺少注解类型 `Callable` 导入，在执行前失败；补齐导入后得到以上结果，未修改产品代码。

只读现场检查使用 `docker compose ps --all`、平台 GET `/api/remote-servers`、GET `/api/remote-operations/{id}`、GET `/api/remote-servers/Linux/effective-config`，以及现有凭证认证后的 `docker version`、`docker buildx version`、`docker ps -a --filter name=mtp-worker`、`docker inspect` 的指定状态/命令/挂载字段、`docker logs --tail=60 mtp-worker-Linux`、`ps` 和构建进程 `/proc/<pid>/io`。配置输出仅筛选上述非敏感字段；没有输出密码或私钥。第一轮诊断 SSH 使用 `/proc/self/fd` 内存密钥路径时因 SSH 关闭 fd 而认证失败，改为父进程 `/proc/<pid>/fd` 后只读查询成功。全过程未中断部署、未停止或重建远端容器、未修改远端配置。

**待确认修复方案**：

1. 仅修改 `backend/app/remote_workers.py`、`backend/tests/test_remote_workers.py` 和本报告。先增加真实子进程大输入失败用例，再将 stdin 持续发送与输出轮询解耦，保留实时日志、取消和超时行为；正确关闭 stdin、回收发送线程和子进程，不增加依赖、不改平台配置。
2. 按下表验证后执行 `python -m pytest tests/test_remote_workers.py -v` 和完整 `python -m pytest tests/ -v`（backend 目录、Python 3.12、隔离数据）。执行 `docker compose up --build -d`；若环境阻碍，按项目规则记录失败与替代验证，不能标记完整部署通过。
3. 获得远端操作确认后，中断本次挂起部署，检查并清理本次遗留构建进程，重新部署 `Linux`；验证容器通过新版入口和只读配置目录加载 OSS 配置、CPU 槽 1，重启次数不持续增长，收到新代次资源心跳。当前旧容器和平台 deployed 标记不一致，因此不能仅 docker restart。

| 验收标准 | 测试层级、前置条件和输入 | 预期业务结果 | 场景 |
|---|---|---|---|
| 构建包完整发送 | Python 真实子进程，接收端延迟大于首轮轮询，参数化空/小/超过管道容量的 bytes 和文本 | 收到完整字节、SHA256 一致、收到 EOF、正常退出 | 正常、边界、兼容 |
| 大输入发送期间实时输出 | 子进程接收前后向 stdout/stderr 写标记，传输大输入 | 及时回调且不重复，不丢输出、不阻塞发送 | 正常、回归 |
| 发送中取消/超时可回收 | 接收端不消费或提前退出，触发取消/超时/BrokenPipe | 有界返回、错误准确、无遗留线程/子进程 | 异常、资源清理 |
| 构建回退与脱敏不回归 | 既有 Buildx 缺失、传统构建失败、秘密脱敏用例及完整套件 | 兼容构建参数与秘密保护保持有效 | 兼容、安全、回归 |
| 真机加载新配置并在线 | 确认后重部署当前旧容器，OSS/Redis 配置来自已保存记录 | 新入口、配置挂载、CPU 槽正确，日志无旧地址 DNS 错误，心跳有效 | 部署验收 |

**风险与回滚**：发送线程与取消处理需防止死锁和管道泄漏；重建平台会中断当前部署操作，远端 install 会替换旧容器，应先确认运行任务。源码可 revert 本次独立修复提交；远端重部署前应保留旧镜像标识和非敏感运行元数据，失败时按旧部署参数恢复，但旧配置本身无法正常服务，不能将恢复旧容器等同于恢复服务。

**未执行**：本次不运行完整 pytest、不执行 Compose 重建、不重部署远端、不验证真实 OSS/Redis 访问或修复后心跳；这些需在修复方案确认后实施。本节结论仅为根因诊断，缺陷仍存在。后续修改 SSH 发送、超时、取消或远程构建时必须重跑表中用例及完整套件并更新验收基准。

**清理**：所有探针通过 stdin 运行，没有保存调试脚本；一次性容器由 `--rm` 删除；诊断 SSH 密钥仅存在 Linux memfd 内存中，已关闭；探针子进程均回收。未创建待提交的运行时产物。结束前以 `git diff --check`、`git status --short` 检查，仅提交本报告。

## 本次变更测试结果：在线服务器配置生效修复（2026-10-29）

- **Commit Hash**：`99c8defd9a452f85e328b9b1e4b3bd20f84b7b25`（Keep remote config progress messages consistent）。
- **实现提交**：`ca78f44`（Fix remote worker configuration application）；`112cd9c`（Clarify remote config activation and extend regression tests）；`99c8def`。
- **分支**：`emdash/hot-boxes-clean-y9u6p`；测试日期：2026-10-29。
- **重测依据**：`git diff d808115 HEAD -- backend/app/` 显示三个远程 worker 文件有业务变更，因此运行相关及完整套件。后续业务代码、配置加载、SSH 脚本、启动入口或心跳判断变动均须重测。

**行为及影响范围**：保持 README 的保存后等待重启约定。在线卡片可重启；保存、下发进度和完成提示明确重启时机。Bash 配置写入错误不再被忽略，无变化正常成功；入口启动前清除受管理旧变量，缺少配置时退出 78。启动/重启检查旧容器入口、只读配置目录挂载和 GPU 授权，不兼容时提示先部署代码重建。Python 执行器在 SSH 前采样启动代次，排除旧代次心跳。未修改依赖、平台 `.env`、Compose 仓库文件或数据库结构。

**最终测试结果**：

| 范围 | 总数 | 通过 | 失败 | 错误 | 跳过 |
|---|---:|---:|---:|---:|---:|
| 完整 pytest 套件 | 320 | 314 | 0 | 0 | 6 |
| 远程脚本/执行器、API、配置仓储专项 | 171 | 171 | 0 | 0 | 0 |
| 最后一次前端提示调整后的 API 回归 | 59 | 59 | 0 | 0 | 0 |
| 实际页面卡片渲染（Node stdin） | 4 | 4 | 0 | 0 | 0 |

- Python **3.12.15**，完整套件 49.07 秒；已执行用例通过率 100%，含环境跳过计为 98.13%。远程模块 108/108，API 59/59，配置仓储 4/4；完整套件另覆盖数据层、训练/预测、固定 seed、任务管理、协调层、worker/Supervisor 和启动脚本回归。
- 完整套件在 `112cd9c` 内容上通过；其后仅调整前端状态文字和对应断言，API 全量 59 项再次通过。业务代码未再变动。
- 6 个既有环境门控跳过：3 个真实 Redis 协调用例、1 个 Redis Supervisor 多进程用例、2 个真实 S3 用例；未配置 `MTP_TEST_REDIS` / `MTP_TEST_S3`。未删除或弱化有效测试。
- 两条既有警告：SQLAlchemy `declarative_base()` 弃用、Pandas `xlrd` 引擎弃用。

**实际测试命令**（项目根目录；仓库只读挂载，测试数据和协调存储隔离）：

```bash
# 相关套件：171 passed
docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 \
  sha256:b8b22036d0d888f513973a6c7618f535fc1631467b6da05b85b285c655dea2b2 \
  python -m pytest tests/test_remote_workers.py tests/test_api.py tests/test_remote_config_repository.py \
  -q -p no:cacheprovider --basetemp=/tmp/mtp-config-regression

# 完整套件：314 passed, 6 skipped
docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 \
  sha256:b8b22036d0d888f513973a6c7618f535fc1631467b6da05b85b285c655dea2b2 \
  python -m pytest tests/ -v -p no:cacheprovider --basetemp=/tmp/mtp-config-full

# 最后前端改动后：59 passed
docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest \
  python -m pytest tests/test_api.py -q -p no:cacheprovider --basetemp=/tmp/mtp-config-ui

bash -n backend/app/remote_worker.sh backend/app/worker-entrypoint.sh
git diff --check
```

**验收标准—测试用例映射**：

| 验收标准 | 层级、前置条件和用例 | 输入与业务结果 | 场景 |
|---|---|---|---|
| 在线可重启，提示区分保存/写入/应用 | API 静态契约 `test_remote_server_card_filters_lifecycle_actions_by_presence`，Node 执行实际页面 `workerServerCard` | 已部署在线显示停止+重启；离线显示启动+重启；未部署不显示生命周期按钮；忙时重启禁用；任务排队时只提示正在下发 | 正常、边界、回归 |
| 写入失败不能报告成功 | Shell `test_remote_bootstrap_script_reports_config_write_failure`；生命周期无变化及变更用例 | 无权限 HOME 真实触发写入失败，非零退出且不输出更新成功；配置一致时仍可启动/重启；修改配置写入正确值 | 异常、权限、兼容 |
| 不继承已删除配置，缺文件拒绝启动 | 入口集成 `test_worker_entrypoint_clears_removed_values_and_rejects_missing_file`、`test_worker_entrypoint_loads_env_file_and_execs_worker` | 继承旧密码/槽数后加载新文件，删除键不再残留；缺文件返回 78；空值、空格、等号和特殊字符按字面量处理 | 正常、边界、安全 |
| 旧容器及模式不匹配有明确诊断 | Shell 参数化 `test_remote_bootstrap_script_rejects_legacy_container_on_restart`、`test_remote_bootstrap_script_rejects_gpu_mode_change_on_restart` | 分别缺入口/挂载、CPU→GPU、GPU→CPU 均拒绝重启并提示部署；GPU 同模式正常重启且 CPU 槽为 0 | 兼容、分支、回归 |
| 检查失败不改配置或重启 | Shell 参数化 `test_remote_bootstrap_script_inspect_failure_stops_restart` | Cmd/Mounts/DeviceRequests 任一 Docker 查询失败，原 env 保留，不执行 restart | 外部依赖失败、关键副作用 |
| 旧心跳不冒充新启动 | 单元参数化 `test_worker_heartbeat_probe_ignores_preexisting_worker_generation`、采样及读取失败用例 | 同 ID 相同/更早/缺失/非法启动时间拒绝；较新代次或新槽接纳；其它机器记录排除；协调存储失败返回空基线 | 正常、边界、异常、兼容 |
| 历史功能及敏感数据保护不回归 | 完整套件，SSH 配置/API 现有安全用例 | 输入注入拒绝、凭据加密/不回显、部署回滚、训练/预测及双数据布局行为继续通过 | 安全、兼容、回归 |

**构建、启动及运行验证**：

1. 已按要求执行默认 `docker compose up --build -d`。前次被用户中断；续跑约 5 分钟仍停留在阿里云镜像下载 454 MB torch wheel，无新的完成输出。停止本次挂起构建（退出 130）；**默认联网依赖重建未完成，不记为通过**。
2. 校验已有 Python 3.12 镜像的 `/app/requirements.txt` 与仓库 SHA256 相同（`7d7733dba1caf8314b89bb5437101c44d52b538d435096ad15a112d45264e71e`），且本次没有依赖变更。先通过 stdin Dockerfile 复用已安装依赖、复制最新应用代码构建；最终用以下临时 stdin 构建覆盖执行完整 Compose 重建启动。覆盖只影响本次构建方法，不改仓库配置、服务环境或数据卷：

```bash
docker compose -f docker-compose.yml -f - up --build -d <<'YAML'
services:
  trainer:
    build:
      dockerfile_inline: &cached_backend |
        FROM emdash-hot-boxes-clean-y9u6p-trainer:latest
        WORKDIR /app
        COPY app ./app
        COPY static ./static
        COPY sample_data ./sample_data
        COPY Dockerfile ./Dockerfile
        COPY init_db.sh start.sh ./
        RUN chmod +x init_db.sh start.sh
        CMD ["./start.sh"]
  worker:
    build:
      dockerfile_inline: *cached_backend
YAML
```

3. 上述离线依赖复用构建 **成功**，trainer、worker、frontend 重建运行，SeaweedFS 运行且健康，frontend 健康；`curl -fsS --max-time 10 http://127.0.0.1:8000/api/health` 返回 `{"ok":true}`。Node stdin 将容器代码/实际 HTTP 页面与本地文件逐字节比较，三个远程 worker 文件、请求模型、路由及页面一致。运行时检查 `_remote_build_args`、`docker_registry` 请求字段及启动代次采样均存在，原先运行后端落后导致仓库源字段被忽略的问题已在本机环境消除。
4. 本地等价 API 启动验证使用一次性 Python 3.12 容器：`docker run --rm -d --name mtp-config-smoke --network none --env USE_DATABASE=false --env REDIS_URL= --env STORAGE_BACKEND=local --env DATA_DIR=/tmp/mtp-config-smoke --env EMBEDDED_WORKERS=0 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`；容器内 urllib 断言 `/api/health` 为真，通过后 `docker stop mtp-config-smoke` 清理。不使用宿主机 Python 3.9 替代；该验证与容器均为 3.12.15。
5. `node` stdin 解析全部内联 JavaScript 通过；实际页面函数四种状态渲染验证通过；真实 Docker 执行挂载 Go 模板语法通过。

**过程问题及处理**：首次相关套件只挂载 backend 到 `/app`，读取项目根 Compose 文件的测试报 FileNotFoundError（154 通过、1 失败）；改为完整仓库挂载后 155 项全部通过。首轮完整套件 302 通过、6 跳过；增加上述分支测试后为最终 314 通过、6 跳过。一次运行时代码核对误用了不存在的类名 `RemoteServerUpdate`，更正为 `RemoteServerUpdateRequest` 后通过，未改业务逻辑。

**已知限制与后续验证**：未连接真实 SSH 目标机执行保存→重启→确认槽数/地址链路，未实测 NVIDIA GPU 授权切换或旧远程容器升级；这些不能由 Shell Docker 替身测试代替。旧 worker 无 `started_at` 或操作前协调存储读取失败时兼容回退到普通心跳确认，不能保证新代次；零 CPU 槽或部署后保持停止时仍会等待心跳超时，不能据此判断配置失败。源设置只在镜像构建时生效，修改平台 `.env` 需重建平台容器。默认 Dockerfile 从头下载依赖的路径仍需网络稳定后重验。当前完整测试包括工作区已有 Dockerfile 重试及其断言调整，两处既有改动未纳入本次提交。

**清理及提交范围**：验证脚本通过 stdin 运行；pytest 临时数据位于一次性容器 `/tmp/mtp-config-regression`、`/tmp/mtp-config-full`、`/tmp/mtp-config-ui`（前轮 `/tmp/mtp-remote-config-tests`），均随 `--rm` 删除；冒烟容器已停止删除。无新调试文件、数据、密钥或模型权重提交。工作区原有 `backend/Dockerfile` 和 `backend/tests/test_remote_workers.py` 的 pip 断言修改保留。收尾检查期间还出现其它任务对 `worker.py`、`test_worker.py` 及 `test_api.py` 日志测试的并行改动，均未触碰或纳入本次提交；上述测试和运行版本一致性结论仅适用于本节提交基准，不覆盖这些随后出现的改动。

## 本次变更测试结果：worker 自动补同步历史数据集（2026-10-29）

**Git基准点**：`d808115`（Auto-migrate local datasets for workers）
**分支**：`emdash/hot-boxes-clean-y9u6p`
**变更范围**：对象存储缺少数据集对象时，worker 先按新旧目录兼容规则查找当前节点本地 CSV，找到后自动同步到 `datasets/{dataset_id}.csv` 并下载到 scratch；本地也缺失时继续返回迁移脚本提示；对象存储故障继续向上抛出。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 场景 |
|---|---|---|---|
| 本地遗留 CSV 可自动补同步并供 worker 使用 | 存储单元测试 `test_worker_resolve_auto_migrates_local_legacy_dataset`；模拟对象存储缺失、本地 `processed/{id}.csv` 存在 | 解析数据集 → 上传 `datasets/{id}.csv` → scratch 内容与本地 CSV 一致 | 正常、兼容 |
| 已同步对象保持直接下载行为 | 存储单元测试 `test_worker_resolve_uses_existing_remote_dataset`；模拟对象已存在 | 解析数据集 → 直接下载，不调用本地上传 | 回归 |
| 双端均缺失仍给出迁移提示 | 存储单元测试 `test_worker_resolve_keeps_migration_error_when_dataset_is_missing` | 对象与本地均无 CSV → `FileNotFoundError` 包含迁移脚本提示 | 边界、异常 |
| 对象存储故障不伪装为文件缺失 | 存储单元测试 `test_worker_resolve_propagates_auto_migration_failure` | 本地 CSV 存在但上传失败 → 保留 `ArtifactStoreUnavailableError` | 异常、基础设施故障 |

**测试执行结果**：

- Python 3.12.15 容器完整套件：**301 通过、0 失败、0 错误、6 跳过**，命令：
  `docker run --rm --network none --mount type=bind,src=$PWD,dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 sha256:b8b22036d0d888f513973a6c7618f535fc1631467b6da05b85b285c655dea2b2 python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/mtp-hot-boxes-tests-full`
- 存储专项（Python 3.12.15 容器）：**31 通过、0 失败、0 错误、2 跳过**。
- 宿主机 Python 3.9.6 存储专项：**31 通过、0 失败、0 错误、2 跳过**；仅作辅助验证，不替代 Python 3.12 结果。
- 完整套件警告：SQLAlchemy `declarative_base()` 弃用警告、Pandas `xlrd` 引擎弃用警告，均为既有警告。

**容器重建结果**：按仓库要求执行 `docker compose up --build -d`。默认配置使用阿里云 PyPI 镜像时，torch wheel 下载内容的 SHA256 与索引声明不一致，pip 重试后构建失败；随后使用 `PIP_INDEX_URL=https://pypi.org/simple` 重试，ARM64 torch 约 483.7 MB 依赖下载速度过低，未在本次验证窗口内完成，已停止。故本次不能标记 Compose 重建通过；完整测试使用已有 Python 3.12.15 镜像并挂载当前仓库代码完成。

**已知问题与限制**：没有本地 CSV 的远程 worker 仍需要先运行 `scripts/migrate_to_object_storage.py`；自动补同步只在对象缺失且当前节点拥有本地副本时生效。未修改迁移脚本、配置或数据库结构。

**下次测试建议**：在可稳定下载依赖的环境重新执行 `docker compose up --build -d`，随后检查 trainer、worker、frontend 健康状态；在真实多机环境验证历史数据集首次被 worker 领取时的自动补同步与并发幂等行为。

## 远程服务器配置不生效专项检查（2026-10-29，缺陷待修复）

**检查版本**：`82e9cc5bbdd33eee498ac5f4d25c2bf315dda051`（Pass apt/pip mirror build args to local compose build）。
**分支**：`emdash/hot-boxes-clean-y9u6p`。
**检查范围**：README 的远程服务器配置约定、页面提交和按钮渲染、配置仓储、SSH 脚本、worker 入口、心跳验收，以及本机运行的 trainer 与工作区版本一致性。本次仅检查并记录结果，未修改业务代码、运行配置或远程服务器，也未重启运行服务。
**基准说明**：下文既有完整测试验收基准 `83472ed` 保留。本节不是新版本验收通过记录，不能用本次定向检查替代完整回归。

### 实际运行环境检查

- 当前 trainer 为 Python 3.12.15。对比 `docker compose config --format json` 与运行容器的环境，REDIS_URL、REDIS_PASSWORD、WORKER_REMOTE_REDIS_URL、WORKER_REMOTE_REDIS_PASSWORD、S3_ENDPOINT、WORKER_REMOTE_S3_ENDPOINT、S3_BUCKET、S3_SECURE、WORKER_REMOTE_IMAGE、USE_DATABASE 均一致；只输出比较结果，没有输出密钥值。
- 当前 trainer 中 `remote_worker.sh`、`worker-entrypoint.sh` 与工作区一致，但 `remote_workers.py` 不一致。进一步读取运行容器源码，确认没有 `_remote_build_args`，请求模型没有 `docker_registry` 字段，更新路由也没有仓库源字段处理。**如果更新的是 Docker/pip/apt 源，当前运行后端尚不支持这些字段，Pydantic 默认忽略额外输入，页面提交不能使其生效。** 需要让运行服务使用已实现这些字段的代码；本次没有执行重建。
- 对本机 `http://127.0.0.1:8000` 读取服务器和资源状态：仅登记一台 `Linux`，CPU 槽配置为 1，`deployed=false`、`status=error`，最近操作为 `install/failed`，该机器没有活跃计算槽。不能据此认定用户所指的在线服务器就是本机这条记录；也不能在日志错误字段为空时推断安装失败原因。

### 已发现的问题和证据

| 编号 | 问题与触发条件 | 证据、实际影响 | 验证方式 |
|---|---|---|---|
| C1 | 在线服务器无法直接重启应用配置 | `backend/static/index.html:1570` 的在线分支只渲染停止按钮。执行原始 `workerServerCard` 函数，在线动作是 `deploy,stop`，离线才是 `deploy,start,restart`。配置操作只写文件，在线 worker 仍使用旧配置；保存提示却写“正在更新并应用服务器配置” | Node 执行实际卡片渲染函数，覆盖在线/离线分支 |
| C2 | 旧版本容器重启后仍用旧配置 | 提交 `83472ed` 才引入外部配置目录挂载和入口命令。当前 `remote_worker.sh:283` 的 start/restart 仅写文件并调用 Docker start/restart，不检测或迁移旧容器的 Cmd/Mounts；这些创建参数不会因 restart 改变。旧容器仍直接执行 `python -m app.worker`，读取创建时固定的环境 | 当前源码与 `git show 83472ed` 对照；未在真实旧 SSH 容器验证 |
| C3 | 文件写入失败仍报告配置更新成功 | `write_worker_env` 由 `write_worker_env || true` 调用，函数内部命令不受预期的 errexit 保护，且最后无条件 return 0。以无权限用户运行原始 config 脚本，stderr 出现 Permission denied，但退出码仍为 0、stdout 仍报告“worker 配置已更新” | Python 3.12 隔离容器以 UID/GID 65534 运行原始 Bash 脚本，真实触发文件权限失败 |
| C4 | 删除密码或缺失配置文件时仍使用旧环境 | 写配置时空 REDIS_PASSWORD 被省略，入口只 export 文件中存在的键，不清理容器固化的旧变量。继承模拟旧密码、加载不含密码的新文件后仍有旧密码。文件缺失时入口也会直接启动 worker，保留全部旧环境 | 执行原始 worker-entrypoint.sh；仅用 python 可执行替身捕获启动环境。密码删除、文件缺失两例均复现 |
| C5 | 旧心跳被当作本次重启成功 | `_wait_for_remote_worker_heartbeat` 只检查 machine_id 和 active，不检查启动代次。向真实 EmbeddedLeaseStore 登记一个旧 worker，未启动新 worker，函数即返回 True；因此“操作完成/在线”不能证明新配置已加载 | 调用原始等待函数及内存协调存储，未模拟核心判断逻辑 |
| C6 | CPU/GPU 切换仅更新环境和重启不足以生效 | `--gpus all` 仅在创建容器时设置；start/restart 不重新配置 DeviceRequests。CPU 改 GPU 时文件中的 CPU 槽会变成 0，但旧容器仍没有 GPU 授权；反向切换也不会移除原 GPU 访问 | API 允许修改 mode、脚本创建与重启分支代码审查；真实 GPU 未验证 |

**需区分的预期行为**：README 明确规定“更新配置”不立即重启运行中的 worker，因此仅保存后暂时看不到运行变化本身符合约定；“生效配置”接口显示的是平台解析的待下发值，不是从运行中的 worker 读取的结果。平台项目 `.env` 由 Compose 注入，Python config 在进程导入时读取，编辑该文件不会自动刷新已有 trainer 容器。已部署服务器的 Docker/pip/apt 源只在首次镜像构建路径使用，“部署代码”复用既有镜像，不重新安装依赖。

### 实际执行的测试和结果

隔离镜像：`sha256:b8b22036d0d888f513973a6c7618f535fc1631467b6da05b85b285c655dea2b2`，Python 3.12.15。仓库只读挂载，网络关闭，测试数据在一次性容器内，与运行平台的数据和协调存储隔离。

```bash
docker run --rm --network none \
  --mount type=bind,src=/Users/xyzc/emdash/worktrees/ai-d973ef21/emdash-hot-boxes-clean-y9u6p,dst=/workspace,readonly \
  --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 \
  sha256:b8b22036d0d888f513973a6c7618f535fc1631467b6da05b85b285c655dea2b2 \
  python -m pytest tests/test_remote_workers.py tests/test_api.py tests/test_remote_config_repository.py \
  -q -p no:cacheprovider --basetemp=/tmp/mtp-config-audit-tests
```

- 总计 **153 项：151 通过（98.69%）、2 失败、0 错误、0 跳过**；有 1 条 SQLAlchemy 既有弃用警告。
- `test_remote_operation_log_clear_returns_404_for_missing_operation`：期望 404，实际 200。与远程 worker 测试组合执行时，固定操作 ID `b * 32` 留在共享 manager 中。相同镜像和隔离参数下，单独执行 `python -m pytest tests/test_api.py::test_remote_operation_log_clear_returns_404_for_missing_operation -q -p no:cacheprovider --basetemp=/tmp/mtp-config-audit-isolation`，**1 通过**。这是本次发现的测试隔离问题，未修改测试或业务逻辑。
- `test_training_page_resource_retry_and_chart_style_contract`：断言 CSS URL `20261025-2`，工作区现有改动使用 `20261029-1`。属于当前工作区样式改动与既有测试契约不一致，未擅自回退或修改相关文件。
- Node stdin 验证：执行页面原有卡片渲染函数，确认 C1 的在线/离线动作差异。
- Python 3.12 stdin 诊断：清除密码仍保留旧值、缺失文件仍启动、写入权限失败仍返回成功、旧心跳仍判定健康，共 **4 个缺陷场景成功复现**。这是观察到错误行为，不表示相应功能验收通过。
- `git diff --check`、两个远程 shell 文件的 `bash -n` 检查通过。
- 初次误用宿主机 `python3`（3.9.6）的检查：`tests/test_api.py -k 'remote_server_card_filters_lifecycle_actions_by_presence or remote_server_form_exposes_network_overrides_and_effective_config_action' -q` 为 2 通过；`tests/test_remote_workers.py -k 'config_overwrites_remote_config_without_code_archive or start_applies_config_change or restart_applies_config_change or config_keeps_stopped' -q` 因缺少 cryptography 收集报错。这次环境不符合 Python 3.12 要求，不作为验收依据，随后已改用上述 3.12 隔离测试。

**未执行内容与限制**：未执行完整训练/预测测试套件、真实 SSH 目标机生命周期操作、真实 GPU 切换、真实旧容器升级和 Docker Compose 重建。本次没有代码实现，不应把检查结果描述为修复完成。现有测试大量断言 Docker 命令字符串，未证明进程最终读到的新配置；C1 的错误按钮行为还被现有测试明确固化。

**临时文件清理**：新增诊断脚本通过 stdin 执行；诊断环境文件和可执行替身位于一次性容器 `/tmp/mtp-config-audit-*`，由 TemporaryDirectory 清理；pytest 临时目录随 `docker run --rm` 的容器删除，未提交运行时产物。

### 建议修复方案与验收映射（尚未实施，待人工确认）

1. `backend/static/index.html`：在线时提供重启入口；统一“配置已写入、待重启”的提示，明确配置摘要为平台期望值。
2. `backend/app/remote_worker.sh`、`backend/app/worker-entrypoint.sh`：区分写入成功/无变化/失败；写入失败立即终止，保留原配置；明确写出空密码并清除受管理旧变量；配置文件缺失或不可读时拒绝静默使用旧环境。保留“保存不打断任务”的约定。
3. 同一 shell 脚本：启动/重启前识别旧入口、缺少配置挂载或计算模式变化，返回明确“需部署代码重建容器”的错误。通过已有部署流程升级，避免静默承诺 restart 能改变容器创建参数；本方案不自动重建旧目标机。
4. `backend/app/remote_workers.py`：记录操作前的 worker 启动信息，等待新启动代次注册，避免仅凭残留心跳判成功；对已停止容器保持停止的部署、CPU 槽为 0 等场景分别处理。复用现有 started_at 信息比较前后变化，避免依赖跨机器时钟一致。
5. 在现有 `backend/tests/test_remote_workers.py`、`backend/tests/test_api.py` 中先建立失败回归用例，再实现修复；修复相关测试的固定 ID 污染，保留日志不存在应返回 404 的业务断言。README 更新操作时序、旧容器兼容和 CPU/GPU 变更要求，TEST_REPORT.md 单独提交结果。
6. 验证后执行 `docker compose up --build -d`，确认运行服务使用新代码，包括已有的仓库源配置能力。重建涉及当前工作区既有 Dockerfile/样式改动，执行前确认工作区状态并保留他人改动；不改变 `.env`、Compose 配置、数据库结构或依赖。

| 验收标准 | 测试层级与前置条件 | 输入 → 预期 | 场景 |
|---|---|---|---|
| 在线可重启，保存不宣称立即生效 | 页面函数/现有 API 测试；已部署服务器 | 在线、离线、未部署、操作进行中 → 正确按钮、禁用状态和提示 | 正常、边界、回归 |
| 配置写入失败不可成功返回 | Shell 集成；旧配置已存在 | 无变化、权限错误、替换失败 → 无变化成功；真实失败非零且原配置保留，不启动容器 | 正常、异常 |
| 配置是受管理变量的启动来源 | 入口脚本集成；继承旧环境 | 改值、清空密码、缺失文件、特殊字符 → 新值/空值生效；缺文件拒绝启动；不执行输入中的命令 | 边界、安全、兼容 |
| 旧容器和模式变化可诊断 | Shell 集成；Docker 外部依赖替身或隔离测试容器 | 旧 Cmd/缺挂载/CPU↔GPU → 明确要求部署；新式同模式容器正常重启 | 兼容、分支、回归 |
| 新启动不能用旧心跳验收 | 远程管理函数与真实内存协调层 | 仅旧注册、出现新启动代次、超时、停止状态部署、零 CPU 槽 → 不误报成功并区分不应等待心跳的动作 | 正常、异常、边界 |
| 运行版本支持仓库源字段 | 构建后 API 检查与仓库源单元测试 | Docker/pip/apt 源 → 字段保存/回显、首次构建参数正确；已部署复用镜像的限制清晰 | 正常、兼容 |
| 既有接口行为不退化 | 相关套件后运行 `cd backend && python -m pytest tests/ -v`（Python 3.12） | 远程操作、数据、训练、预测 → 保持既有有效业务断言；环境跳过和失败逐项记录 | 完整回归 |

**风险与回滚**：点击重启会短暂中断目标 worker，沿用 45 秒优雅停止和训练断点机制；旧容器和模式变更增加明确拒绝，需按提示部署。保存配置不自动停机。以独立英文提交实施，回滚相应提交并统一容器重建；若已经对目标机部署新入口，需同时恢复目标机相应代码版本，不能只回滚平台页面。无需新增依赖、配置修改或数据库迁移。

**重测触发/下一步**：以上缺陷修复、远程 shell/入口/状态判断改动、部署版本更换，或用户补充不同的实际操作路径后，按映射复跑相关用例；业务代码变更必须完整回归、容器重建并单独更新测试报告。当前不把有缺陷及测试失败的状态标记为开发完成。

## 本次变更测试结果：远程 worker 配置改为外部文件事实源（2026-10-29）

**Git基准点**：`83472ed`（Apply worker config via external env file sourced at container startup）
**分支**：`emdash/hot-boxes-clean-y9u6p`
**测试环境**：Docker Compose，一次性容器 Python 3.12.15（整仓挂载 `/workspace`，`-w /workspace/backend` 执行 pytest）。

**变更范围**（更新配置不再影响运行中的容器，外部 `.env` 为唯一事实源）：
- 新增 `backend/app/worker-entrypoint.sh`：worker 容器入口脚本，每次启动（含 `docker restart`、宿主机重启）逐行读取外部 `.env` 并 `export`（值按字面量处理、不做 shell 求值，规避 `$`/反引号注入），随后 `exec python -m app.worker`。
- `remote_worker.sh`：
  - `write_worker_env` 只落盘（不再产生 `.env.previous` 回滚快照）；GPU 模式统一写 `WORKER_CPU_SLOTS=0`（原 `run` 阶段 `--env` 覆盖移入文件）。
  - `run_worker_container` 挂载整个 `CONFIG_DIR` 到 `/app/config:ro`（目录挂载才能在 `mv` 原子替换后读到新文件），命令改为 `bash /app/app/worker-entrypoint.sh /app/config/<machine_id>.env`。
  - `config` 动作只写文件，不重建/不重启容器；`deploy` 只同步代码并重建容器（不重写配置）；`start`/`restart` 先写最新配置再 `docker start`/`docker restart`（不再重建）；`stop`/`status` 不变。
- `remote_workers.py`：`config` 动作退出"等待资源心跳"集合（只写文件、不重启容器，等待无意义）；更新进度文案。
- `index.html` / README：更新"更新配置 / 部署 / 启动重启"文案，说明配置下次启动/重启生效、部署只同步代码。

**测试执行结果**：
- 远程 Worker 定向测试（`tests/test_remote_workers.py`）：87 通过、0 失败、0 错误、0 跳过。
- API 定向测试（`tests/test_api.py`）：58 通过、0 失败、0 错误、0 跳过。
- 完整测试套件（`tests/`）：288 通过、6 跳过、0 失败、0 错误。跳过项由 MTP_TEST_REDIS / MTP_TEST_S3 / Supervisor 子进程环境条件控制。
- `docker compose up --build -d`：trainer、worker、frontend、SeaweedFS 重建与启动成功。
- `curl -fsS http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- `bash -n backend/app/remote_worker.sh`、`bash -n backend/app/worker-entrypoint.sh`、`git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 首次 `.env` 不存在时创建 | Shell：`test_remote_bootstrap_script_config_writes_env_without_requiring_container` | 无容器时 `config` 返回 0、写 600 权限 env 文件、不调用 Docker | 正常、边界 |
| `config` 只写文件、不碰容器 | Shell：`..._config_overwrites_env_without_touching_container`、`..._config_keeps_stopped_container_stopped` | 运行/停止容器均不产生 stop/rename/run/start；env 更新；输出"下次启动/重启时生效" | 正常、回归 |
| GPU 模式写 `WORKER_CPU_SLOTS=0` | Shell：`test_remote_bootstrap_script_installs_cpu_and_gpu_workers` | cpu 写 2、gpu 写 0；命令含 `--gpus all`、挂载 `/app/config:ro`、入口脚本 | 分支、边界 |
| 入口脚本加载外部 `.env` 并 exec worker | 新增 `test_worker_entrypoint_loads_env_file_and_execs_worker` | 含 `=`/空格/`$`/注释/空值：按字面量 export，`exec python -m app.worker` | 正常、异常、安全 |
| 每次启动/重启读最新配置 | Shell：`..._start_applies_config_change_while_running`、`..._restart_applies_config_change_without_double_restart`、`..._start_applies_config_change_to_stopped_container` | 先写 env 再 `docker start`/`docker restart`，不重建（无 stop/rename/run） | 正常、兼容 |
| 配置无变化时保持纯操作 | Shell：`..._start_unchanged_config_keeps_pure_operation`、`..._restart_unchanged_config_forces_restart` | 不覆盖 env、不产生 `.previous`；restart 仍强制重启 | 边界、回归 |
| 部署只同步代码 | Shell：`test_remote_bootstrap_script_deploy_syncs_code_and_recreates_container` | env 保持原样、重建容器（stop/rename/run/rm） | 兼容、回归 |
| 部署失败回滚容器、不改配置 | Shell：`test_remote_bootstrap_script_deploy_rolls_back_container_without_touching_config` | 重建失败恢复旧容器，env 不变化 | 异常、恢复 |
| 页面文案一致 | API：`test_remote_server_form_exposes_network_overrides_and_effective_config_action` | "同步最新 worker 代码并重建容器""保存后覆盖远端配置文件，下次启动/重启时生效" | 正常、回归 |

**关键模块测试**：远程 Worker 87/87 通过；API 58/58 通过；完整套件 288/288 通过（6 环境跳过）。

**已知问题与限制**：未连接真实 SSH 目标机；入口脚本的 env 覆盖、目录挂载与重建/回滚通过隔离 Docker/python 替身验证。行为变化点——`config` 动作不再立即重启容器，配置在下次启动/重启才生效（此前会立即重建并短暂重启 worker）。

**下次测试建议**：在真实 Linux 目标机上 ① 修改 CPU 槽/Redis 覆盖后点"更新配置"，确认 `.env` 更新且运行中的 worker 不重启、心跳不断；② 再点"重启"，确认容器以新配置重新注册；③ 宿主机重启后确认容器用外部 `.env` 自动恢复新配置。

## 本次变更测试结果：启动/重启远程 worker 时应用最新配置（2026-10-28）

**Git基准点**：`3467a3f`（Apply latest remote config on worker start/restart）
**分支**：`emdash/hot-boxes-clean-y9u6p`
**测试环境**：Docker Compose，一次性容器 Python 3.12.15（整仓挂载 /repo，`-w /repo/backend` 执行 pytest）。

**变更范围**（修复"生效配置落后于部署配置"）：
- `remote_worker.sh`：`write_worker_env` 改为可感知配置变化（`cmp` 对比，一致时不写入、不产生回滚快照）；start/restart 先把平台最新配置下发到目标机，配置有变化时用现有镜像重建容器（保持原运行状态、失败自动回滚旧容器与旧配置），再按语义启动/重启；配置无变化时保持纯容器操作；install/deploy/config 行为不变。
- `remote_workers.py` / `routers/jobs.py`：start/restart 从"宽松校验"改为与部署类动作同等严格（Redis/S3/镜像配置必须可解析，避免用空值覆盖目标机配置）；仅 stop/status 保持宽松（`_PURE_LIFECYCLE_ACTIONS`）。
- `scripts/worker-node.sh`：`restart` 先 `compose up` 应用 `.env.worker` 变化（Compose 检测变化时重建容器），再强制重启进程，保证 restart 语义。
- README 更新生命周期操作描述；行为变化：此前 start/restart 不依赖平台配置即可执行，现要求配置可解析（配置不可解密/缺失时报错并提示）。

**测试执行结果**：
- 远程 Worker + 节点脚本定向测试（`tests/test_remote_workers.py tests/test_worker_node_script.py`）：99 通过、0 失败、0 错误、0 跳过。
- 完整测试套件（`tests/`）：287 通过、6 跳过、0 失败、0 错误。跳过项由 MTP_TEST_REDIS / MTP_TEST_S3 / Supervisor 子进程环境条件控制，与既有跳过类别一致。
- `docker compose up --build -d`：trainer、worker、frontend、SeaweedFS 重建与启动成功。
- `curl -fsS http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- `bash -n backend/app/remote_worker.sh`、`bash -n scripts/worker-node.sh`、`git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 启动在配置变化时应用新配置 | Shell：`test_remote_bootstrap_script_start_applies_config_change_while_running`、`..._start_applies_config_change_to_stopped_container` | 旧 env 文件 + 新 MTP_S3_ENDPOINT：运行中容器重建后保持运行；停止容器先按新配置重建（create）再 start；env 文件内容更新 | 正常、边界 |
| 重启在配置变化时应用新配置 | Shell：`test_remote_bootstrap_script_restart_applies_config_change_without_double_restart` | 重建即完成重启，不重复执行 docker restart；env 文件更新 | 正常 |
| 配置无变化时保持纯容器操作 | Shell：`test_remote_bootstrap_script_start_unchanged_config_keeps_pure_operation`、`..._restart_unchanged_config_forces_restart` | 不重建、不覆盖 env 文件、不产生 `.previous`；restart 仍强制重启进程（卡死恢复） | 边界、兼容 |
| 配置不可解析时严格拒绝 | 单元：`test_run_remote_action_lifecycle_config_requirements`、`test_remote_configuration_errors_lifecycle_skips_platform_checks` | Redis 覆盖不可解密：stop 仍可执行；start/restart/config/deploy/update 拒绝并报"无法解密" | 异常、权限与安全 |
| 节点脚本 restart 应用 env 变化 | Shell：`test_restart_applies_worker_env_then_restarts` | 命令顺序 `up --detach --no-deps worker` 先于 `restart --timeout 45 worker` | 正常、回归 |
| 业务回归保持通过 | 完整 `backend/tests` 套件 | 287 通过、6 跳过 | 数据、训练、API、远程管理回归 |

**关键模块测试**：远程 Worker 与节点脚本 99/99 通过；完整套件 287/287 通过（6 环境跳过）。

**已知问题与限制**：未连接真实 SSH 目标机，重建与回滚通过隔离 Docker 替身与单元断言验证；行为变化点——平台 Redis/S3 配置不可解析时 start/restart 现在会失败（此前静默用旧配置操作容器），页面会显示明确报错。

**下次测试建议**：在真实 Linux 目标机上 ① 页面修改 CPU 槽位/Redis 覆盖后点"重启"，确认 env 文件更新、容器重建并以新配置注册心跳；② 配置无变化时点"重启"，确认仅重启进程、无容器重建；③ 配置有变化且新配置非法（如错误 Redis 地址）时点"重启"，确认自动回滚旧容器且服务器状态可恢复。

## 本次变更测试结果：远程服务器操作整体流程加固（2026-10-28）

**Git基准点**：`a94cb00`（Harden remote server operation flow: executor heartbeat recovery, post-rebuild heartbeat checks, scoped Redis password propagation, lifecycle action validation relaxation, resilient status persistence）
**分支**：`emdash/hot-boxes-clean-y9u6p`
**测试环境**：Docker Compose，容器 Python 3.12.15。

**变更范围**（远程服务器操作全流程审查后的修复）：
- P1-1 远程 Redis 密码来源：新增 `WORKER_REMOTE_REDIS_PASSWORD` 独立配置，平台 `REDIS_PASSWORD` 仅在远程 worker 复用平台 `REDIS_URL` 时沿用；服务器级 URL 覆盖与远程专用 URL 不再泄露平台密码。
- P1-2 操作执行进程崩溃恢复：执行者心跳（Redis TTL 90s，20s 续写）+ 宽限期判定，`queued/running` 且执行者消失的操作自动收敛为 failed 并恢复服务器状态，解除后续操作 409 死锁（轮询/编辑/新操作/取消入口均触发恢复）。
- P1-3 S3 故障重试分类：`classify_retryable` 显式把 `ArtifactStoreUnavailableError` 判为环境性可重试错误。
- P2-1 重建后心跳验证：install/deploy/update/config/start/restart 成功后等待资源心跳（30s），未收到时 operation 记 `heartbeat_ok=false`、服务器状态标记 error 并给出排查提示。
- P2-2 生命周期操作放宽校验：start/stop/restart 只校验 SSH 凭证与服务器记录，Redis 覆盖不可解密或平台配置缺失不再阻塞停止/启动。
- P2-3 终态持久化兜底：MySQL 写入失败只记操作日志警告，不破坏操作终态。
- P3-1 Redis 密码加入控制字符校验；P3-3 配置摘要输出密码配置状态；P3-4 重建失败时回滚 env 文件快照，保持"配置=运行容器"一致；P3-5 写终态前二次取消检查。
- 配置面：`docker-compose.yml` / `docker-compose.cpu.yml` / `.env.example` / README 补充 `WORKER_REMOTE_REDIS_PASSWORD` 并修正镜像传输流程描述（现网为目标机构建）。

**测试执行结果**：
- 远程 Worker 定向测试（`tests/test_remote_workers.py`）：83 通过、0 失败、0 错误、0 跳过。
- 协调/存储/Worker/API 定向测试（`tests/test_coordination.py tests/test_storage.py tests/test_worker.py tests/test_api.py`）：131 通过、0 失败、0 错误、5 跳过（Redis/S3 环境条件控制）。
- 完整测试套件（`tests/`）：284 通过、6 跳过、0 失败、0 错误。跳过项由 MTP_TEST_REDIS / MTP_TEST_S3 / Supervisor 子进程环境条件控制。
- 配置 `MTP_TEST_REDIS=redis://host.docker.internal:6399/0` 复跑协调层：Redis 语义一致性、注册往返、幽灵槽清理及新增执行者存活标记用例全部通过；`test_supervisor_parallel_and_child_restart` 因临时测试容器缺 `pgrep`（slim 镜像）无法执行，属环境限制，与既有跳过类别一致，非本次变更引入。
- `docker compose up --build -d`：trainer、worker、frontend、SeaweedFS 重建与启动成功。
- `curl -fsS http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- `bash -n backend/app/remote_worker.sh`、`python -m py_compile`、`git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 远程 Redis 密码不泄露给其他 Redis | 单元：`test_remote_redis_password_priority_chain`、`test_remote_deployment_settings_apply_platform_fallback_and_server_network_overrides` | 远程专用 URL 未配远程密码时 `redis_password=""`；显式 `WORKER_REMOTE_REDIS_PASSWORD` 优先；复用平台 URL 时沿用 `REDIS_PASSWORD`；生效配置标注 `redis_auth_source` 且无明文 | 正常、边界、权限与安全 |
| 执行进程崩溃后操作自动收敛 | 单元：`test_recover_stale_remote_operation_marks_failed_and_restores_server`、`..._ignores_active_or_recent_operations`；协调：`test_remote_operation_executor_heartbeat_embedded_and_redis` | 过期无执行者的 running 操作标记 failed、服务器恢复 error；活跃/宽限期内操作不受影响 | 异常、恢复、回归 |
| 重建后心跳验证 | 单元：`test_execute_remote_operation_records_missing_worker_heartbeat`、`..._records_healthy_worker_heartbeat`、`test_run_remote_action_deploys_code_without_rebuilding_image` | 心跳缺失→completed+heartbeat_ok=false+服务器 error；心跳正常→completed+heartbeat_ok=true+在线；部署完成等待心跳 | 正常、异常 |
| 生命周期操作不被平台配置阻塞 | 单元：`test_run_remote_action_lifecycle_skips_platform_config`、`test_remote_configuration_errors_lifecycle_skips_platform_checks` | Redis 覆盖不可解密时 stop 仍可执行，config 必须拒绝；生命周期校验跳过 Redis/S3/镜像 | 边界、异常、兼容 |
| S3 故障按环境性错误重试 | 单元：`test_classify_retryable` 增补 `ArtifactStoreUnavailableError` 用例；`test_s3_outage_is_not_treated_as_missing` 等 | 对象存储不可用判定为可重试；仅 NoSuchKey 保持缺失语义 | 异常、回归 |
| 失败回滚保持配置一致性 | Shell：`test_remote_bootstrap_script_config_rolls_back_when_new_container_fails` | 重建失败恢复旧容器并回滚 env 文件，`.previous` 快照清除 | 异常、恢复 |
| 业务回归保持通过 | 完整 `backend/tests` 套件 | 284 通过、6 跳过 | 数据、训练、API、远程管理回归 |

**关键模块测试**：远程 Worker 83/83 通过；协调/存储/Worker/API 定向 131 通过、5 环境跳过；完整套件 284/284 通过（6 环境跳过）。

**已知问题与限制**：未连接真实 SSH 目标机，心跳验证与执行者恢复通过隔离替身与单元断言验证；`WORKER_REMOTE_REDIS_PASSWORD` 未在真实远程 Redis 上端到端验证认证；Supervisor 多进程 E2E 在临时测试容器中因缺少 `pgrep` 未执行（正式测试建议在带 procps 的容器或宿主机执行）。

**下次测试建议**：在真实 Linux 目标机分别验证 ① 平台密码 ≠ 远程 Redis 密码场景下 worker 认证失败不再发生；② 操作执行中重启 trainer 容器后，轮询页面能在宽限期内自动把操作标记失败并允许重新操作；③ deploy 引入错误代码后操作显示 heartbeat_ok=false 且服务器状态为 error。

## 本次变更测试结果：下发远程 worker 的 Redis 密码（2026-10-28）

**Git基准点**：`82ae738`（Forward REDIS_PASSWORD to remote workers）  
**分支**：`emdash/hot-boxes-clean-y9u6p`  
**测试环境**：Docker Compose，容器 Python 3.12.15。

**变更范围**：远程 worker 配置下发链路补齐 `REDIS_PASSWORD`。当 Redis 密码通过 `REDIS_PASSWORD` 环境变量单独配置（而非内嵌在 URL 中）时，`config`/`install` 动作会把密码一并写入目标机 `~/.config/mtp-worker/<machine_id>.env`，使远程 worker 能通过 Redis 认证加入任务队列；`remote_deployment_settings`、脚本环境前缀、操作日志脱敏与“生效配置”的认证状态判断同步更新。

**测试执行结果**：
- 远程 Worker 定向测试：75 通过、0 失败、0 错误、0 跳过。
- API 定向测试：58 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：280 项；274 通过、6 跳过、0 失败、0 错误。跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 构建及启动成功。
- `curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 30 http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| Redis 密码单独配置时随配置下发 | 单元：`test_render_remote_script_includes_redis_password_and_effective_config_marks_auth` | URL 不含认证、`REDIS_PASSWORD` 非空时，脚本前缀含 `MTP_REDIS_PASSWORD`，`remote_deployment_settings` 返回密码，生效配置 `redis_auth_configured=True` | 正常、回归 |
| env 文件写入 Redis 密码 | Shell：`test_remote_bootstrap_script_installs_cpu_and_gpu_workers` | `MTP_REDIS_PASSWORD` 非空时写出 `REDIS_PASSWORD=...` | 正常 |
| 密码不泄露到页面与日志 | 单元：`test_render_remote_script_includes_redis_password_and_effective_config_marks_auth`；`_redact_output` secrets 增补 | 生效配置与操作日志不含密码明文 | 权限与安全 |
| 既有远程部署/配置/训练行为回归 | 完整 `backend/tests` 套件 | 274 通过、6 跳过 | 数据、训练、API、远程管理回归 |

**关键模块测试**：远程 Worker 75/75 通过；API 58/58 通过；完整套件 274/280 通过。

**已知问题与限制**：未连接真实 SSH 目标机；`REDIS_PASSWORD` 下发通过隔离 Docker 替身与单元断言验证，未在真实 Redis 上端到端验证认证。工作区另有 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 的既有未提交改动，未纳入本次提交。

**下次测试建议**：在真实 Linux 目标机执行一次“更新配置”，确认 `~/.config/mtp-worker/<machine_id>.env` 出现 `REDIS_PASSWORD`，worker 启动日志无 `Authentication required` 并能正常领取任务。

## 本次变更测试结果：更新配置后由 start/restart 应用最新 worker 配置（2026-10-28）

**Git基准点**：`bb93970`（Apply remote config on worker restart）  
**分支**：`emdash/hot-boxes-clean-y9u6p`  
**测试环境**：Docker Compose，容器 Python 3.12.15。

**变更范围**：`config` 动作只原子覆盖目标机 worker 环境文件，不操作运行中的容器；`start/restart` 复用现有镜像并重新创建容器，通过 `--env-file` 读取最新配置；新容器启动失败时恢复旧容器并保留最新配置供下次重试。页面和 README 同步说明更新与应用之间的时序。

**测试执行结果**：
- 远程 Worker 定向测试：73 通过、0 失败、0 错误、0 跳过。
- API 定向测试：58 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：278 项；272 通过、6 跳过、0 失败、0 错误。跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 构建及启动成功。
- `curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 30 http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- `bash -n backend/app/remote_worker.sh`、Python 语法检查和 `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 更新配置不重启容器 | Shell：`test_remote_bootstrap_script_config_writes_env_without_container`、`test_remote_bootstrap_script_config_does_not_restart_existing_container` | 写入最新环境文件，不产生 Docker 生命周期命令；运行中的容器保持旧值 | 正常、边界、回归 |
| `start/restart` 应用最新配置 | Shell 参数化：`test_remote_bootstrap_script_lifecycle_uses_latest_env_file` | 使用现有镜像重建容器，`docker run` 读取最新 `--env-file` | 正常、兼容 |
| 更新后失败可恢复 | Shell：`test_remote_bootstrap_script_lifecycle_rolls_back_when_new_container_fails` | 新容器启动失败后恢复旧容器，保留最新环境文件 | 异常、恢复 |
| 部署、停止、状态查询兼容 | Shell：`test_remote_bootstrap_script_deploys_code_without_config_or_container_operations`、`test_remote_bootstrap_script_lifecycle`；完整套件回归 | 部署只同步代码，stop/status 行为保持不变 | 兼容、回归 |
| 页面说明与实际时序一致 | API 页面契约：`test_remote_server_form_exposes_network_overrides_and_effective_config_action` | 页面说明更新配置不重启，提示通过 start/restart 应用 | 正常、回归 |
| 业务回归保持通过 | 完整 `backend/tests` 套件 | 272 通过、6 跳过 | 数据、训练、API、远程管理回归 |

**关键模块测试**：远程 Worker 73/73 通过；API 58/58 通过；完整套件 272/278 通过。

**已知问题与限制**：未连接真实 SSH 目标机；容器重建、`--env-file` 加载和失败回滚通过隔离 Docker 替身验证。工作区另有 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 的既有未提交改动，未纳入本次提交；完整套件是在包含这些工作区改动的容器环境中执行并通过。

**下次测试建议**：在真实 Linux 目标机依次执行“重启 → 更新配置 → 启动/重启”，核对更新后第一次运行仍保持旧值、下一次生命周期操作才读取新值，并验证任务优雅停止与 checkpoint 恢复。

## 本次变更测试结果：部署/更新配置立即重建容器，启动/重启改为纯容器操作（2026-10-28）

**Git基准点**：`863cef1`（Recreate worker container on deploy/config; keep start/restart pure container ops）  
**分支**：`emdash/hot-boxes-clean-y9u6p`  
**测试环境**：Docker Compose，容器 Python 3.12.15。

**变更范围**：远程操作语义对齐「部署=生成配置文件并生成容器、更新配置=更新配置文件与容器配置、启动/重启=只操作容器」：`deploy` 同步 worker 源码、重写环境文件并用现有镜像重建容器；`config` 覆盖环境文件并重建容器立即应用（不再等待手动 start/restart）；重建保持原运行状态（运行中的保持运行、停止的保持停止），新容器创建失败时自动回滚旧容器；`start/restart` 改为纯 `docker start` / `docker restart --time 45`，不再重建容器或读取配置文件。`stop/status`、首次 `install` 行为不变。页面按钮文案、提示与 README 同步更新。

**测试执行结果**：
- 完整测试套件：279 项；273 通过、6 跳过、0 失败、0 错误。跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- 远程 Worker 定向测试（含 6 个 Shell 脚本用例与新增「停止状态保持」用例）：全部通过。
- API 定向测试（页面契约标记更新）：全部通过。
- 以 `tests/test_remote_workers.py tests/test_api.py` 顺序合并运行时出现 1 例既有测试顺序干扰（`test_remote_workers` 中 `"b"*32` 操作记录残留使 `test_api` 的日志清除 404 用例返回 200）；该用例单独复跑通过，标准命令 `pytest tests/` 下 273 项全部通过。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 构建及启动成功。
- `curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 30 http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- `bash -n backend/app/remote_worker.sh` 与 `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 部署生成配置文件并生成容器 | Shell：`test_remote_bootstrap_script_deploy_syncs_code_and_recreates_container` | 同步代码包、重写环境文件、用现有镜像重建并启动容器 | 正常、回归 |
| 更新配置同时更新配置文件与容器配置 | Shell：`test_remote_bootstrap_script_config_recreates_container_with_latest_env` | 覆盖环境文件后停止旧容器、`docker run --detach` 读取最新 `--env-file` | 正常、兼容 |
| 更新配置失败可回滚 | Shell：`test_remote_bootstrap_script_config_rolls_back_when_new_container_fails` | 新容器创建失败后恢复旧容器并重新启动，保留新配置供重试 | 异常、恢复 |
| 更新配置不改变停止状态 | Shell：`test_remote_bootstrap_script_config_keeps_stopped_container_stopped` | 停止容器重建时仅 `docker create`，不自动启动 | 边界、回归 |
| 启动/重启只操作容器 | Shell 参数化：`test_remote_bootstrap_script_lifecycle_controls_container_only` | 仅执行 `docker start` / `docker restart --time 45`，不重建容器、不读写配置文件 | 正常、兼容 |
| 未部署时更新配置给出明确提示 | Shell：`test_remote_bootstrap_script_config_requires_existing_container` | 无容器时报错「请先部署」，环境文件已写入 | 异常、边界 |
| 页面文案与实际动作一致 | API 页面契约：`test_remote_server_form_exposes_network_overrides_and_effective_config_action` | 页面提示部署/更新配置会重建容器，无旧时序描述 | 正常、回归 |
| 业务回归保持通过 | 完整 `backend/tests` 套件 | 273 通过、6 跳过 | 数据、训练、API、远程管理回归 |

**关键模块测试**：远程 Worker 与 API 定向测试全部通过；完整套件 273/279 通过（97.8%）。

**已知问题与限制**：未连接真实 SSH 目标机；容器重建、失败回滚与停止状态保持通过隔离 Docker 替身验证。工作区另有 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 的既有未提交改动，未纳入本次提交；完整套件是在包含这些工作区改动的容器环境中执行并通过。

**下次测试建议**：在真实 Linux 目标机依次执行「更新配置 → 部署代码 → 停止 → 更新配置 → 启动」，核对运行中的 worker 立即应用新配置/代码、停止状态在重建后保持，并验证训练任务优雅停止与 checkpoint 恢复。

# 测试报告

## 本次变更测试结果：更新远程配置时重建 worker 容器（2026-10-28）

**变更范围**：远程 `config` 动作在覆盖目标机 `~/.config/mtp-worker/<machine_id>.env` 后，复用现有 worker 镜像重建容器，使容器通过 `--env-file` 读取新配置；容器启动失败时恢复旧配置和旧容器。无现有容器时只更新配置文件，不创建新容器。页面提示同步更新。`deploy` 仍只同步 worker 代码。

**测试环境**：Docker Compose，容器 Python 3.12.15。

**测试执行结果**：
- 远程 Worker 定向测试：73 通过、0 失败、0 错误、0 跳过。
- API 定向测试：58 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：278 项；272 通过、6 跳过、0 失败、0 错误。跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- 首次运行远程 Worker 定向测试时未挂载仓库根目录 Compose 文件，1 项环境性失败；补齐只读挂载后 73 项全部通过。
- 首次合并运行 API 与远程 Worker 定向文件时有 1 项既有远程日志 404 状态隔离波动；单独复跑 API 文件 58 项全部通过。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 构建及启动成功。
- `curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 30 http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 已有 `Linux.env` 更新后容器使用新配置 | Shell：`test_remote_bootstrap_script_config_recreates_existing_container_with_new_env` | 原有容器复用原镜像 ID，停止并重建；新容器使用 `--env-file` 指向更新后的文件 | 正常、回归 |
| 容器启动失败时不留下半更新状态 | Shell：`test_remote_bootstrap_script_config_rolls_back_when_recreated_container_fails` | 新容器启动失败后恢复旧 `.env`、旧容器名称并重新启动旧容器 | 异常、恢复 |
| 无容器时仍可保存配置但不盲目创建容器 | Shell：`test_remote_bootstrap_script_config_writes_env_without_container` | 文件更新成功，不执行 `docker run` | 边界 |
| 部署代码不覆盖配置 | Shell：`test_remote_bootstrap_script_deploys_code_without_config_or_container_operations`；远程执行器测试 | `deploy` 只切换代码目录，不写 `.env` 或重建容器 | 兼容、回归 |
| 页面文案与实际动作一致 | API 页面契约：`test_remote_server_form_exposes_network_overrides_and_effective_config_action` | 已部署服务器提示会重建 worker 容器且不上传代码 | 正常、回归 |
| 训练、数据、API 和生命周期行为保持通过 | 完整 `tests/` 套件 | 272 通过、6 跳过 | 兼容、回归 |

**关键模块测试**：远程 Worker 73/73 通过；API 58/58 通过；完整套件 272/278 通过。

**已知问题与限制**：未连接真实 SSH 目标机；容器重建、`--env-file` 加载和失败回滚通过隔离 Docker 替身验证。测试时工作区另有既存的 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 修改，未纳入本次功能提交，但完整测试包含这些工作区改动。

**下次测试建议**：在真实 Linux 目标机执行一次配置更新，确认目标机 `docker inspect mtp-worker-<machine_id>` 的 `Config.Env` 与新 `Linux.env` 一致，并验证任务 checkpoint 恢复行为。

**Git基准点**：`84118f4`（Apply remote config by recreating worker）。

## 本次变更测试结果：增加远程服务器生效配置查看（2026-10-28）

**变更范围**：远程服务器卡片增加“生效配置”入口，新增只读 `GET /api/remote-servers/{machine_id}/effective-config` 接口。页面显示平台当前解析出的 Worker 镜像、Redis/S3 地址、配置来源、目标状态和连接参数；Redis 认证信息、S3 密钥和 SSH 凭证均脱敏。结果明确标注为待下发配置，不读取目标机上的敏感配置文件。

**测试环境**：Docker Compose，容器 Python 3.12.15。

**测试执行结果**：
- 远程 Worker 定向测试：71 通过、0 失败、0 错误、0 跳过。
- API 定向测试：58 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：276 项；270 通过、6 跳过、0 失败、0 错误。跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- 首次合并运行定向文件时有 2 项失败：1 项为已有远程日志 404 状态隔离波动，1 项为新增断言中的测试主机名错误；修正断言后分文件复跑和完整套件均通过。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 构建及启动成功。
- `curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 30 http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 页面可查看远程服务器生效配置 | 页面契约：`test_remote_server_form_exposes_network_overrides_and_effective_config_action` | 卡片存在“生效配置”按钮、配置弹窗和接口入口 | 正常、回归 |
| 接口返回解析后的镜像、Redis/S3、来源和状态 | API：`test_remote_server_api_separates_registration_deployment_and_config_update` | 服务器专属覆盖和清空后 `.env` 回退均返回正确结果；不存在服务器返回 404 | 正常、边界、异常 |
| 敏感信息不泄露 | 同上；Redis URL 含密码 | 返回只包含安全 endpoint 和认证状态，不包含 Redis 密码；S3/SSH 密钥不进入响应 | 权限与安全 |
| 既有远程部署和训练行为保持通过 | 完整 `tests/` 套件 | 远程生命周期、训练、数据链路和 API 回归通过 | 兼容、回归 |

**关键模块测试**：远程 Worker 71/71 通过；API 58/58 通过；完整套件 270/276 通过。

**已知问题与限制**：该查看接口展示平台当前解析出的待下发配置，不会主动 SSH 读取目标机当前文件；密码和密钥不可在页面查看。真实远程主机验证未执行。

**下次测试建议**：在测试远程主机上执行配置覆盖后打开“生效配置”，核对平台摘要与目标机 `~/.config/mtp-worker/<machine_id>.env` 的非敏感字段一致。

**Git基准点**：`512b0ff`（Add effective remote config viewer）。

## 本次变更测试结果：移除服务器级 Worker 镜像配置并回退平台环境（2026-10-28）

**变更范围**：服务器新增/编辑页面和 API 不再提供或保存 Worker 镜像覆盖；远程部署统一使用平台 `WORKER_REMOTE_IMAGE`。服务器未配置专属 Redis URL 或 S3 地址时，配置覆盖回退到平台 `.env` 注入的 `REDIS_URL` 和 `S3_ENDPOINT`；编辑时留空会清除旧的服务器级网络覆盖。更新了远程配置文档和测试。

**测试环境**：Docker Compose，容器 Python 3.12.15。

**测试执行结果**：
- 远程 Worker 定向测试：71 通过、0 失败、0 错误、0 跳过。
- API 定向测试：58 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：276 项；270 通过、6 跳过、0 失败、0 错误。跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- 合并运行两个定向文件时出现 1 项既有状态隔离波动（远程日志清除 404 用例返回 200）；单独运行 API 文件通过，完整套件也通过，未复现本次变更相关失败。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 构建及启动成功。
- `curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 30 http://127.0.0.1:8000/api/health`：返回 `{"ok":true}`。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 编辑服务器不配置 Worker 镜像 | API：`test_remote_server_api_separates_registration_deployment_and_config_update`；页面契约：`test_remote_server_form_exposes_host_overrides_and_edit_action` | 服务器记录和公开 API 不包含镜像覆盖；页面无 Worker 镜像字段，部署使用平台镜像 | 正常、回归、兼容 |
| Redis/S3 未配置时回退平台 `.env` | 单元：`test_remote_deployment_settings_apply_platform_fallback_and_server_network_overrides`、`test_remote_server_redis_override_can_be_preserved_replaced_or_cleared` | 清空服务器级覆盖后，生成的远程配置使用 `REDIS_URL`/`S3_ENDPOINT`；专属值仍可显式覆盖 | 正常、边界、回归 |
| 远程配置脚本和生命周期行为保持有效 | `tests/test_remote_workers.py`、`tests/test_api.py` 及完整 `tests/` | 远程安装、配置、部署、启动/停止/重启和敏感信息脱敏均通过 | 异常、安全、兼容、回归 |

**关键模块测试**：远程 Worker 71/71 通过；API 58/58 通过；完整套件 270/276 通过。

**已知问题与限制**：未连接真实 SSH 服务器；远程目标机实际 `.env` 覆盖通过远程脚本和隔离测试验证。两个定向文件合并运行时的远程日志 404 用例存在状态隔离波动，单文件和完整套件均通过。

**下次测试建议**：在测试远程主机执行一次配置覆盖，确认目标机 worker `.env` 在 Redis/S3 留空时实际写入平台值，并验证已部署 worker 在下一次启动后读取新配置。

**Git基准点**：`a26cf58`（Use platform defaults for remote workers）。

## 本次变更测试结果：远程操作日志显示脚本输出（2026-10-28）

**变更范围**：远程服务器执行日志增加 `[远程服务器启动配置]` 摘要，并把远端脚本最终 stdout/stderr 追加到 `operation.log`，确保配置更新后的实际配置摘要在日志窗口可见；训练任务日志增加 `[训练任务启动配置]` 摘要。所有日志均对连接凭证、令牌和密钥字段脱敏。

**测试环境**：Docker Compose，容器 Python 3.12.15。完整测试基于当前已提交版本临时归档运行；工作区预先存在、未纳入本次提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 修改未参与测试。

**测试执行结果**：
- 训练 Worker 定向测试：23 通过、0 失败、0 错误、0 跳过；远程 Worker 定向测试：71 通过、0 失败、0 错误、0 跳过。
- 最新代码提交后的完整测试套件：276 项；270 通过、6 跳过、0 失败、0 错误。跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 构建及启动成功。
- `curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 30 http://127.0.0.1:8000/api/health` 返回 `{"ok":true}`。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 训练任务执行日志打印并标识启动配置 | Worker 集成：`tests/test_worker.py::test_worker_completes_job`；本地状态平面与真实训练流程 | 完成分类训练后日志包含 `[训练任务启动配置]`、数据集 ID 和任务参数 | 正常、回归 |
| 训练启动配置不泄露敏感参数 | 同上；任务参数注入 `api_token` | 日志显示 `"api_token":"[redacted]"`，不包含原始 token | 安全、异常输入 |
| 远程服务器执行日志打印并标识启动配置 | 远程操作集成：`tests/test_remote_workers.py::test_execute_remote_operation_persists_success_and_failure`；隔离 SQLite、远程动作替身 | `start` 操作完成后日志包含 `[远程服务器启动配置]`、动作、模式和 Redis 非敏感 endpoint | 正常、兼容 |
| 配置更新的远端脚本输出在日志窗口可见 | 同上；远程动作替身返回配置摘要 stdout | `operation.output` 与 `operation.log` 都包含配置摘要，前端优先读取日志时仍能显示摘要 | 缺陷回归、正常 |
| 远程连接凭证不进入执行日志 | 同上；Redis URL 含密码 | 日志保留主机/端口/数据库路径，不包含 Redis 密码 | 权限与安全 |
| 远程配置摘要展示实际非敏感配置 | Shell：`test_remote_bootstrap_script_config_overwrites_env_without_touching_container` | `config` 动作输出模式、机器标识、CPU 槽位、Redis 目标、S3 参数和凭证已配置状态 | 正常、回归 |
| 两类日志保持现有执行行为和 API 兼容 | 完整 `tests/` 套件、容器健康检查 | 训练、远程配置/部署/生命周期及日志读取接口保持通过 | 兼容、回归 |

**关键模块测试**：训练 Worker 23/23 通过；远程 Worker 71/71 通过；完整套件 270/276 通过。

**已知问题与限制**：未连接真实 SSH 服务器；远程启动配置和脚本输出通过隔离执行器及脱敏断言验证。

**下次测试建议**：连接一台测试 Linux 主机，确认真实 start/restart 后日志窗口能看到启动配置摘要，并核对目标机实际生效的 worker `.env` 与日志脱敏结果。

**Git基准点**：`8a65551`（Include remote script output in operation logs）。

## Git基准点

- **Commit Hash**: 84118f4（更新远程配置时重建 worker 容器）
- **提交信息**: Apply remote config by recreating worker
- **测试日期**: 2026-10-28
- **分支**: emdash/hot-boxes-clean-y9u6p

## 本次变更测试结果：远程执行日志清除（2026-10-27）

**变更范围**：远程操作日志弹窗增加“清除日志”按钮；新增 `DELETE /api/remote-operations/{operation_id}/logs`，清空 `log`、`output`、`stderr` 和 `error`，保留操作状态与阶段；运行中的操作清除后仍可追加后续日志。

**测试环境**：Docker Compose，容器 Python 3.12。工作区中预先存在的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 修改未纳入本次提交。

**测试执行结果**：
- 远程日志清除与页面契约定向测试：3 通过、0 失败、0 错误、0 跳过。
- API 与远程 worker 定向测试：129 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：270 通过、6 跳过、0 失败、0 错误；跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 构建并启动成功。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 弹窗可清除当前执行日志并保留状态 | API：`test_remote_operation_logs_can_be_cleared_without_changing_state`；页面契约：`test_remote_server_card_exposes_live_operation_logs` | 清除后日志字段为空，操作仍为 running 且阶段不变；按钮、DELETE 调用和空状态提示存在 | 正常、并发后续日志、回归 |
| 清除后运行中的操作可继续写入新日志 | `test_remote_operation_logs_can_be_cleared_without_changing_state` | 清除后追加新阶段日志，新日志可读且状态不变 | 状态边界、回归 |
| 不存在的操作返回 404 | API：`test_remote_operation_log_clear_returns_404_for_missing_operation` | 随机合法 ID 不创建记录并返回 404 | 异常、边界 |

**已知问题与限制**：未连接真实远程 SSH 主机；清除动作及 Redis/内存租约语义通过 API 和隔离协调存储测试验证。页面显示的“暂无执行日志”是清空后的占位文本。

**下次测试建议**：在测试远程主机上执行一次运行中部署操作，清除日志后确认新阶段输出继续出现，并验证 Redis 多进程下清除与追加的时序行为。

**Git基准点**：`cf6afcd`（Add remote log clear control）；本测试报告作为单独提交更新。

## 本次变更测试结果：计算资源远程动作隔离（2026-10-27）

**变更范围**：已部署计算资源的配置修改只覆盖目标机 worker `.env`；部署只同步 worker 代码，不上传新配置、不重建或重启容器；启动、停止和重启继续作为独立容器生命周期操作。新增 `config`、`deploy` 远程动作，并保留旧 `update` 动作作为代码部署兼容别名。首次部署仍负责准备镜像、配置、代码并启动容器。

**测试环境**：Docker Compose，容器 Python 3.12。工作区中预先存在的远程日志清除、`backend/app/storage.py` 和 `backend/tests/test_storage.py` 修改未纳入本次提交。

**测试执行结果**：
- API 与远程 worker 定向测试：129 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：270 通过、6 跳过、0 失败、0 错误；跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- `docker compose up --build -d`：trainer、worker、frontend 和 SeaweedFS 启动成功。
- `curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 30 http://127.0.0.1:8000/api/health` 返回 `{"ok":true}`。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。
- 未连接真实远程 SSH 主机；远程文件覆盖和代码切换通过隔离 Shell 测试验证。清理无关工作区改动后完整测试再次通过。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 修改配置只覆盖远程配置文件 | API：`test_remote_server_api_separates_registration_deployment_and_config_update`；Shell：`test_remote_bootstrap_script_config_overwrites_env_without_touching_container`；SSH 执行器和 Docker 替身 | 已部署 PATCH 触发 `config`；`.env` 更新、代码包不发送且不调用 Docker | 正常、边界、回归 |
| 部署只更新代码 | 单元：`test_run_remote_action_deploys_code_without_rebuilding_image`；Shell：`test_remote_bootstrap_script_deploys_code_without_config_or_container_operations` | `deploy` 携带代码包，保留旧 `.env`，不执行镜像构建、启动、停止或重启 | 正常、兼容、状态隔离 |
| 启动等生命周期操作保持独立 | API 远程操作测试、`test_remote_bootstrap_script_lifecycle` | `start` 只执行 `docker start`，停止/重启保留原有优雅停机行为 | 正常、异常、回归 |
| 首次部署和旧动作兼容 | `test_run_remote_action_builds_worker_on_target_before_install`、完整 `tests/` | 首次 `install` 仍构建镜像并启动；旧 `update` 映射为代码部署 | 兼容、回归 |
| 页面文案与动作语义一致 | `test_remote_server_form_exposes_host_overrides_and_edit_action`、`test_remote_server_card_filters_lifecycle_actions_by_presence` | 页面区分“更新配置”“部署代码”“启动/停止/重启” | 正常、分支 |

**关键模块测试**：API 与远程 worker 129/129 通过；完整套件 270/276 通过。

**下次测试建议**：连接一台测试 Linux 主机，验证配置覆盖后远程 `.env` 权限、停止容器后的部署代码切换，以及单独启动/重启后新配置和新代码的实际加载。

## 本次变更测试结果：远程服务器部署与配置更新分离（2026-10-27）

**变更范围**：新增远程服务器只登记配置，不再自动安装；未部署服务器显示“部署”，成功部署后显示“重新部署”；已部署服务器更新配置后异步应用配置并重建 worker 容器；在线隐藏启动和重启，离线隐藏停止。业务代码提交为 `59ca2fa`。

**测试环境**：Docker Compose，容器 Python 3.12.15。完整测试包含工作区原有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动；这两个文件未纳入本次提交。

**测试执行结果**：
- 定向用例：5 通过、0 失败、0 错误、0 跳过。
- API 与远程 worker 测试文件：120 通过、0 失败、0 错误、0 跳过。
- 首次运行 API 与远程 worker 测试文件时，因测试容器未挂载仓库根目录 Compose 文件，119 项通过、1 项因找不到 `/workspace/docker-compose.yml` 失败；补齐 Compose 文件只读挂载后，同一套测试 120 项全部通过。
- 最终完整测试：267 项；261 通过、6 跳过、0 失败、0 错误。6 项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- 最终 `docker compose up --build -d`：trainer、worker、frontend 镜像构建和服务启动成功；`curl -fsS http://localhost:8000/api/health` 返回 `{"ok":true}`。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。
- 未连接真实远程 SSH 服务器；配置更新触发安装流程由 API 测试中的隔离执行器验证，远端容器重建未做真实主机回环。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 新增服务器只登记配置；部署状态随部署成功持久化 | API：`test_remote_server_api_separates_registration_deployment_and_config_update`、`test_remote_server_uses_credential_username_when_server_username_is_blank`；SQLite 隔离仓储、SSH 执行器替身 | 新登记返回 `deployed=false` 且不调用 SSH；点击部署完成后返回 `deployed=true` | 正常、兼容、秘密不泄露 |
| 已部署服务器更新配置会应用配置并重建 worker；未部署时只保存 | API：`test_remote_server_api_separates_registration_deployment_and_config_update`；隔离 SQLite 与 SSH 执行器 | 未部署 PATCH 返回 200 且不触发远程操作；已部署 PATCH 返回异步操作 ID 并调用 install 流程，配置持久化 | 正常、状态边界、回归 |
| 卡片部署文案依据部署状态切换，旧服务器状态兼容 | 页面/API 静态契约：`test_remote_server_form_exposes_host_overrides_and_edit_action`；兼容性：`test_remote_deployment_settings_apply_per_server_overrides_and_platform_fallback` | 未部署显示“部署”，已部署显示“重新部署”；旧 `online` / `stopped` 记录推断为已部署，显式 false 优先 | 正常、兼容 |
| 在线隐藏启动和重启；离线隐藏停止；未部署不显示生命周期操作 | 页面静态契约：`test_remote_server_card_filters_lifecycle_actions_by_presence` | 在线模板分支只含停止；离线分支含启动和重启；未部署分支为空 | 边界、状态分支 |
| 失败的生命周期操作保留已部署标记 | 单元：`test_execute_remote_operation_persists_success_and_failure`；SQLite 隔离仓储 | 已部署服务器的停止操作遇到 SSH 错误后，状态报错但 `deployed` 仍为 true | 异常、回归 |

**关键模块测试**：API 与远程 worker 120/120 通过；完整数据、训练、API、协调与 worker 套件 261 通过、6 跳过。

**实际执行命令**：

```bash
docker compose up --build -d
```

结果：构建及启动成功。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest -q -p no:cacheprovider tests/test_api.py::test_remote_server_api_separates_registration_deployment_and_config_update tests/test_api.py::test_remote_server_uses_credential_username_when_server_username_is_blank tests/test_api.py::test_remote_server_form_exposes_host_overrides_and_edit_action tests/test_api.py::test_remote_server_card_filters_lifecycle_actions_by_presence tests/test_remote_workers.py::test_remote_deployment_settings_apply_per_server_overrides_and_platform_fallback
```

结果：5 passed。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest -q -p no:cacheprovider tests/test_api.py tests/test_remote_workers.py
```

结果：120 passed。另一次缺少仓库根目录 Compose 文件挂载的同类命令结果为 119 passed、1 failed（环境挂载错误），修正挂载后通过。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -v -p no:cacheprovider
```

结果：261 passed、6 skipped、0 failed。

```bash
curl -fsS http://localhost:8000/api/health
git diff --check
```

结果：健康检查返回 `{"ok":true}`；差异空白检查通过。

**已知问题与限制**：尚未在真实 SSH 目标机验证配置更新后的容器重建和首次心跳；本次未使用真实远端凭证或主机。现有 pytest 弃用警告与本次改动无关。

**下次测试建议**：连接一台测试 Linux 主机，分别验证首次部署、在线节点更新配置后的容器重建、离线节点启动与配置失败后的重新部署。

## 本次变更测试结果：重新部署复用已有镜像（2026-10-27）

**变更范围**：首次部署继续构建并加载 worker 镜像；已部署服务器的重新部署或配置更新只同步 `backend/app` 代码，复用目标机已有镜像并重建容器，不执行 Docker build。更新前保留旧代码、配置和容器，启动失败时回滚。业务代码提交为 `c0e03d1`。

**测试环境**：Docker Compose，容器 Python 3.12.15。测试包含工作区原有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动；这两个文件未纳入本次提交。

**测试执行结果**：
- API 与远程 worker 定向测试：131 通过、0 失败、0 错误、0 跳过。
- 初次新增回归组合运行出现 4 个测试替身断言失败，原因是 Bash stub 的换行转义和日志断言；修正测试替身后整组复跑通过。
- 完整测试：278 项；272 通过、6 跳过、0 失败、0 错误。6 项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- `docker compose up --build -d`：构建和启动成功；`curl -fsS --retry 5 http://127.0.0.1:8000/api/health` 返回 `{"ok":true}`。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 首次部署和重新部署路径分开 | API/远程 worker：`test_run_remote_action_builds_worker_on_target_before_install`、`test_run_remote_action_updates_code_without_rebuilding_image` | `install` 执行目标机镜像构建；`update` 同步代码且命令中没有 `build` | 正常、兼容、回归 |
| 更新配置重建容器但复用镜像 | Shell：`test_remote_bootstrap_script_updates_code_with_existing_image`、`test_remote_update_uses_loaded_image_when_container_or_image_tag_changes` | CPU/GPU 更新写入新配置、挂载新代码目录并复用已有镜像 ID | 正常、边界、资源状态 |
| 目标机缺少可复用镜像时明确失败 | Shell：`test_remote_update_fails_cleanly_when_no_container_or_image_is_available` | 无容器且无镜像时返回错误，不创建代码目录或容器 | 异常、状态冲突 |
| 更新启动失败可安全回滚 | Shell：`test_remote_update_rolls_back_code_and_container_when_start_fails` | 新容器启动失败后恢复旧源码、旧配置和旧容器 | 异常、回归 |
| 页面与 API 区分更新动作及状态 | API/页面：`test_remote_server_api_separates_registration_deployment_and_config_update`、`test_remote_server_form_exposes_host_overrides_and_edit_action` | 已部署配置更新使用 `update`，页面提示复用镜像且不重建镜像 | 正常、兼容 |

**关键模块测试**：API 与远程 worker 131/131 通过；完整套件 272/278 通过，6 项按环境门控跳过。

**实际执行命令**：

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_api.py tests/test_remote_workers.py -q -p no:cacheprovider
```

结果：131 passed、0 failed。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider
```

结果：272 passed、6 skipped、0 failed。

```bash
docker compose up --build -d
docker compose ps
curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 20 http://127.0.0.1:8000/api/health
git diff --check
```

结果：Compose 构建和启动成功；trainer、worker、frontend、SeaweedFS 正常运行；健康检查返回 `{"ok":true}`；差异空白检查通过。

**已知问题与限制**：尚未连接真实远程 Linux 主机执行 SSH 更新回环；目标机必须已有可复用的 worker 镜像，依赖或 Dockerfile 变更仍需另行构建并加载新镜像。

**下次测试建议**：在测试 Linux 主机上分别验证首次部署、已部署 CPU/GPU 重新部署、镜像缺失提示和启动失败回滚，并确认更新后的 worker 心跳。

**重测触发条件**：修改远程部署动作、代码归档、目标机更新脚本、容器挂载/回滚或页面更新提示时，运行 `tests/test_api.py`、`tests/test_remote_workers.py`、完整 `tests/` 和 `docker compose up --build -d`。

**Git基准点**：`c0e03d1`（Apply remote worker config before restart）；测试报告作为单独提交更新。

## 本次变更测试结果：显示远程目标镜像构建进度（2026-10-27）

**变更范围**：目标机 Docker BuildKit 构建启用纯文本进度；SSH 输出采集器把换行和回车都作为进度分隔符；目标机连续 30 秒无新构建输出时写入等待提示，说明可能正在下载基础镜像或 Python 依赖。Buildx 缺失回退、取消、超时和错误脱敏行为保持兼容。业务代码提交为 `7f1ab3d`。

**测试环境**：Docker Compose，测试容器 Python 3.12。完整测试包含工作区原有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动；这两个文件未纳入本次提交。

**测试执行结果**：
- 修复前新增的回归用例按预期复现 3 项失败：回车进度被合并缓冲、静默构建没有提示、构建命令未请求纯文本进度。
- 修复后定向日志与构建用例：5 通过、0 失败、0 错误、0 跳过。
- 远程 worker 完整测试：66 通过、0 失败、0 错误、0 跳过。
- 完整测试：266 项；260 通过、6 跳过、0 失败、0 错误。6 项为 Redis、S3 与 Supervisor 子进程环境门控项。
- `docker compose up --build -d`：构建成功，trainer 与 worker 已启动。trainer 初始化 MySQL 时健康检查短暂返回 502；重试后 `/api/health` 返回 `{"ok":true}`。frontend、SeaweedFS 保持 healthy。
- `git diff --check`：通过。
- pytest 有 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。
- 未连接真实 Linux 目标机进行 SSH 构建回环；实际 Docker 输出格式和远程网络等待通过单元测试模拟验证。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| Docker 构建进度及时进入远程操作日志 | 单元：`test_communicate_remote_process_streams_carriage_return_progress`、`test_remote_worker_build_sends_minimal_context_and_quotes_docker_args` | 回车分隔的 Docker 输出逐条回调；BuildKit 命令带 `--progress=plain` 且仍安全引用镜像名 | 正常、兼容、输入安全 |
| 构建静默达到 30 秒时提示等待原因 | 单元：`test_communicate_remote_process_reports_idle_target_build` | 无 Docker 输出满 30 秒时出现“可能正在下载基础镜像或 Python 依赖”的阶段日志 | 边界、等待场景 |
| Buildx 回退和远程 worker 历史行为保持兼容 | 定向：`test_remote_worker_build_falls_back_when_buildx_is_missing`；完整：`tests/` | Buildx 缺失仍只回退一次；远程 worker 66 项和完整套件均通过 | 异常、兼容、回归 |

**关键模块测试**：远程 worker 构建与 SSH 日志层 66/66 通过；数据、训练、API 等其他模块均包含在完整测试中，整套结果为 260 通过、6 跳过。

**实际执行命令**：

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py::test_communicate_remote_process_streams_only_new_output_lines tests/test_remote_workers.py::test_communicate_remote_process_streams_carriage_return_progress tests/test_remote_workers.py::test_communicate_remote_process_reports_idle_target_build tests/test_remote_workers.py::test_remote_worker_build_sends_minimal_context_and_quotes_docker_args tests/test_remote_workers.py::test_remote_worker_build_falls_back_when_buildx_is_missing -q -p no:cacheprovider
```

结果：5 passed。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py -q -p no:cacheprovider
```

结果：66 passed。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider
```

结果：260 passed、6 skipped。

```bash
docker compose up --build -d
docker compose ps
curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 20 http://127.0.0.1:8000/api/health
git diff --check
```

结果：Compose 构建与启动成功；健康检查最终返回 `{"ok":true}`；差异空白检查通过。

**已知问题与限制**：真实 Linux SSH 目标机尚未复测，基础镜像和 Python 依赖下载耗时仍取决于目标机网络。等待提示每 30 秒重复一次，日志受现有 24,000 字符上限约束。

**下次测试建议**：在实际 Linux 目标机执行一次重新部署，确认 BuildKit 的纯文本步骤实时进入页面日志，并观察网络较慢时的等待提示。

**重测触发条件**：修改 `_communicate_remote_process` 的输出分隔、静默提示或目标机 Docker 构建命令时，运行 `tests/test_remote_workers.py`、完整 `tests/` 和 `docker compose up --build -d`。

**Git基准点**：`7f1ab3d`（Show remote target build progress）；测试报告作为单独提交更新。

## 本次变更测试结果：阿里云 OSS region 自动识别（2026-10-27）

**变更范围**：S3ArtifactStore 从标准阿里云 OSS endpoint（含 S3 网关域名）提取 region 并传给 MinIO 客户端，跳过不兼容 OSS 的路径式 GetBucketLocation 探测；其他 S3 endpoint 继续使用 SDK 默认行为。代码提交为 `63bc551`。

**测试环境**：Docker Compose；容器 Python 3.12、MinIO Python SDK 7.2.20。OSS 回环使用当前 `.env` 中配置的 endpoint、bucket 和凭证；不记录凭证值。完整测试包含工作区中预先存在的 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 未提交改动，这些改动未纳入本次代码提交。

**测试执行结果**：
- 存储定向测试：29 项；27 通过、0 失败、0 错误、2 跳过。跳过项需要显式设置 S3 测试端点。
- 完整测试：264 项；258 通过、0 失败、0 错误、6 跳过。6 项由 Redis、S3 与 Supervisor 子进程环境条件控制。
- OSS bucket 启动检查及对象读写、存在性检查、列表、删除和清理回环通过。分别用 `oss-cn-shanghai.aliyuncs.com` 与 `s3.oss-cn-shanghai.aliyuncs.com` 验证；随后将本地忽略文件 `.env` 中的 `S3_ENDPOINT` 改为 S3 网关 endpoint，未提交该文件。
- `docker compose up --build -d` 后 trainer 启动且 `/api/health` 曾返回 `{"ok":true}`。应用 S3 网关 endpoint 并执行 `docker compose up -d` 后，frontend 与 SeaweedFS healthy，但 trainer 目前等待 MySQL，API 返回 502；容器内至 MySQL 的 TCP 检查未通过。
- worker 尚未通过运行验证：外部 Redis 返回 `Authentication required`，且容器中的 `REDIS_PASSWORD` 为空。需在本地 `.env` 配置有效 Redis 凭证、恢复 MySQL 可达性后重启并复测；本次未执行端到端训练。
- `git diff --check`：通过。pytest 有两条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 标准 OSS endpoint 自动提供正确 region，非 OSS endpoint 不改变默认行为 | 单元：`test_s3_store_infers_region_only_for_regional_oss_endpoints`，7 组参数 | 上海/杭州、内外网及 S3 网关域名传入对应 region；SeaweedFS 与加速域名不传 region | 正常、边界、兼容 |
| OSS bucket 初始化及对象存储基本操作可用 | 集成：当前 `.env` OSS bucket 上执行临时对象回环 | bucket 检查、写/读/exists/list/delete 均成功，最后确认对象已清理 | 正常、回归、资源清理 |
| Compose 应用入口可用 | 容器/API：`docker compose up --build -d`、`docker compose up -d`、`curl /api/health` | 初次代码重建后 API 返回 `ok=true`；应用 endpoint 并重建后 trainer 等待 MySQL，API 502 | 启动、接口回归；外部 DB 阻塞 |
| worker 可接入协调服务 | 容器集成：Compose worker | 未通过；外部 Redis 要求认证，而当前容器没有 Redis 密码，未执行训练链路 | 外部依赖阻塞；修复凭证后需复测 |

**实际执行命令**：

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_storage.py -q -p no:cacheprovider
```

结果：27 passed、2 skipped。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider
```

结果：258 passed、6 skipped、0 failed。

```bash
docker run --rm --env-file .env --entrypoint python emdash-hot-boxes-clean-y9u6p-trainer:latest -c 'from app.storage import S3ArtifactStore; import uuid; st=S3ArtifactStore(); prefix="jobs/it-oss-region-fix/"; key=prefix+uuid.uuid4().hex+".txt"; value=b"oss-region-roundtrip"; wrote=False
try:
 st.write_bytes(key,value); wrote=True
 assert st.read_bytes(key)==value
 assert st.exists(key)
 assert key in st.list_keys(prefix)
 print("oss_roundtrip=passed")
finally:
 if wrote: st.delete(key)
assert not st.exists(key)
print("oss_cleanup=passed")'
docker compose up --build -d
curl -fsS --max-time 5 http://127.0.0.1:8000/api/health
docker compose ps --all
docker compose logs --tail=25 trainer worker
git diff --check
```

结果：OSS 回环与清理通过；Compose 构建通过，API 健康检查通过；worker 因 Redis 缺少有效认证而重启。

```bash
docker run --rm --env-file .env --entrypoint python emdash-hot-boxes-clean-y9u6p-trainer:latest -c 'from app.storage import S3ArtifactStore; import uuid; st=S3ArtifactStore(); prefix="jobs/it-s3-endpoint-prefix/"; key=prefix+uuid.uuid4().hex+".txt"; value=b"s3-endpoint-roundtrip"; wrote=False
try:
 st.write_bytes(key,value); wrote=True
 assert st.read_bytes(key)==value
 assert st.exists(key)
 assert key in st.list_keys(prefix)
 print("s3_endpoint_roundtrip=passed")
finally:
 if wrote: st.delete(key)
assert not st.exists(key)
print("s3_endpoint_cleanup=passed")'
docker compose up -d
curl -fsS --max-time 5 http://127.0.0.1:8000/api/health
```

结果：S3 网关 endpoint 回环与清理通过；Compose 按新 endpoint 重建容器；健康检查暂时返回 502，trainer 等待不可达的 MySQL。

**已知问题与下次测试建议**：恢复 MySQL 可达性并在本地 `.env` 填写有效 `REDIS_PASSWORD` 后重新运行 Compose，确认 trainer API 和 worker 稳定；随后运行一次真实训练任务，验证队列领取、日志、完成状态及产物。修改 S3 endpoint 识别或对象存储客户端初始化时，重新运行 `tests/test_storage.py`、完整 `tests/`、真实 OSS 回环及 `docker compose up --build -d`。

**Git基准点**：`63bc551`（Fix Aliyun OSS region detection）；测试报告单独提交。

## 本次变更测试结果：远程 Docker 缺少 buildx 时兼容构建（2026-10-27）

**变更范围**：远程 worker 镜像仍优先使用 BuildKit。只有目标机返回明确的“BuildKit 已启用但 buildx 缺失或损坏”错误时，才重试一次传统构建器；回退上下文会移除 Dockerfile 中仅用于 pip 下载缓存的 `RUN --mount`，并按目标 Docker daemon 的本机架构构建。其他构建错误不回退；重试失败仍阻止远程安装，错误输出继续脱敏。

**测试环境**：Docker Engine 29.8.0，Docker Compose，测试容器 Python 3.12。完整套件包含工作区原有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动；这两个文件未纳入本次提交。

**测试执行结果**：
- 定向远程 worker 测试：64 通过、0 失败、0 错误、0 跳过。
- 完整测试：257 项；251 通过、0 失败、0 错误、6 跳过。已执行用例通过率 100%；6 项为 Redis、S3 与 Supervisor 子进程环境门控测试。
- `docker compose up --build -d`：构建成功，frontend、trainer、worker 镜像均重建。随后运行状态检查发现 frontend 与 SeaweedFS healthy，但 trainer 和 worker 因当前 Compose S3 endpoint 返回 `SecondLevelDomainForbidden`（要求使用 virtual hosted style）而反复重启；容器运行验证未通过，未修改本次范围外的存储配置。
- `git diff --check`：通过。
- 未连接实际 Linux 目标机复测 SSH 构建与 worker 启动；目标机是否支持传统构建器仍需部署环境确认。
- pytest 输出 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**实际执行命令**：

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py -q -p no:cacheprovider
```

结果：64 passed。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider
```

结果：251 passed、6 skipped。

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=80 trainer worker
git diff --check
```

结果：镜像构建成功；运行状态检查发现 trainer 和 worker 因 S3 endpoint 拒绝 path-style 请求而重启；差异空白检查通过。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| Buildx 缺失时只回退一次，并使用无缓存挂载的 Dockerfile 构建目标机原生架构 | 单元：`test_remote_worker_build_falls_back_when_buildx_is_missing` | 精确缺失错误触发第二次 SSH 构建；命令关闭 BuildKit、不传 `--platform`；Dockerfile 保留 pip 安装步骤并移除 cache mount | 正常、兼容、平台边界 |
| Dockerfile 不含预期的 BuildKit 缓存挂载时拒绝生成传统构建上下文 | 单元：`test_worker_build_context_skips_invalid_same_named_dockerfile` | 传统构建器上下文转换返回明确错误，不发送未经验证的 Dockerfile | 边界、异常、兼容 |
| 普通构建错误不触发回退；回退失败保留脱敏错误 | 单元：`test_remote_worker_build_reports_redacted_target_error`、`test_remote_worker_build_reports_redacted_legacy_fallback_error` | 拉取错误只尝试一次；回退拉取失败时返回脱敏后的最终错误 | 异常、安全、敏感信息保护 |
| 构建失败仍阻止后续安装，既有远程功能保持兼容 | 集成：`test_run_remote_action_stops_when_remote_worker_build_fails`、完整 `tests/` | 构建失败后不运行安装脚本；远程 worker 定向测试 64/64 通过，全套 251 通过、6 跳过 | 状态冲突、回归、兼容 |

**关键模块测试**：远程 worker 与 SSH 构建流程 64/64 通过；完整测试 251 通过、6 个环境门控用例跳过。

**已知问题与限制**：传统 Docker 构建器属于弃用路径，目标机 Docker 若不再提供它，仍需在目标机安装 buildx；真实远程 SSH 构建未复测。当前 Compose S3 endpoint 的请求风格错误导致 trainer 与 worker 重启，需修正该环境配置后才能完成容器运行验证。

**下次测试建议**：修正 Compose S3 endpoint 使其支持当前客户端请求方式；在确认目标机 Docker 尚支持传统构建器的 Linux 服务器上重新安装 worker，确认回退构建、SSH 部署及 worker 心跳。

**重测触发条件**：修改远程构建错误识别、传统构建器回退、兼容 Dockerfile 转换或安装阻断逻辑时，运行 `tests/test_remote_workers.py` 和完整 `tests/`，并执行 `docker compose up --build -d`。

**Git基准点**：`9485de8`（Cover incompatible legacy Dockerfile）；本测试报告作为单独提交更新。

## 本次变更测试结果：目标机从构建上下文构建 worker 镜像（2026-10-27）

**变更范围**：远程安装时不再从平台 Docker 导出并经 SSH 传输完整 worker 镜像。平台将精简构建上下文作为二进制 tar 经 SSH 发送给目标机，由目标 Docker 使用 BuildKit 构建目标架构镜像；通用基础镜像与 Python 依赖由目标机拉取或使用本地缓存。构建输出进入现有脱敏进度日志，构建失败或取消时不会执行安装脚本。实际上下文为 665,600 字节（0.63 MiB）。

**测试环境**：Docker Compose；容器 Python 3.12.15。完整套件包含工作区原有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动，这两个文件未纳入本次提交。

**测试执行结果**：
- 定向远程 worker 测试：`tests/test_remote_workers.py` 为 62 通过、0 失败、0 错误、0 跳过。
- 完整测试：249 通过、6 跳过、0 失败、0 错误。6 项为 Redis、S3 与 Supervisor 子进程环境门控测试。
- `docker compose up --build -d`：通过，trainer、worker 和 frontend 重建启动。
- GPU 与 CPU 远程 Compose 配置检查：`docker compose -f docker-compose.yml -f docker-compose.remote.yml config -q`、`docker compose -f docker-compose.cpu.yml -f docker-compose.remote.yml config -q` 均通过。
- 运行容器生成的构建上下文：665,600 字节（0.63 MiB）；归档不含 `data/` 和 `tests/`。
- `git diff --check`：通过。保留既有 SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 弃用警告。
- 未连接真实 Linux 目标机执行 SSH 构建与启动；目标机上的 Docker Hub、PyPI 镜像连通性和 BuildKit 实际行为仍需部署环境确认。

**实际执行命令**：

```bash
docker compose up --build -d
```

结果：构建和启动成功。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py -q -p no:cacheprovider
```

结果：62 passed。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider
```

结果：249 passed、6 skipped。

```bash
docker compose -f docker-compose.yml -f docker-compose.remote.yml config -q
docker compose -f docker-compose.cpu.yml -f docker-compose.remote.yml config -q
docker compose exec -T trainer python -c 'from app.remote_workers import _worker_build_context; data = _worker_build_context(); print(f"build_context_bytes={len(data)} ({len(data) / 1048576:.2f} MiB)")'
git diff --check
```

结果：两种远程 Compose 配置检查通过；构建上下文 665,600 字节（0.63 MiB）；差异空白检查通过。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 安装只经 SSH 发送精简构建上下文，由目标机执行目标架构构建 | 单元：`test_remote_worker_build_sends_minimal_context_and_quotes_docker_args`、`test_run_remote_action_builds_worker_on_target_before_install`；二进制输入：`test_run_remote_subprocess_preserves_binary_build_context` | tar 仅含 Dockerfile、依赖和项目文件；远端命令以安全引用的参数执行 `docker build --platform linux/amd64`，随后才运行安装脚本 | 正常、输入安全、数据隔离、兼容 |
| 构建错误被脱敏并阻止启动 | 单元/集成：`test_remote_worker_build_reports_redacted_target_error`、`test_run_remote_action_stops_when_remote_worker_build_fails` | Docker 拉取失败时保留错误原因、隐藏 SSH 密码，且不执行安装脚本 | 异常、敏感数据保护、状态冲突 |
| 构建可取消，且拒绝不支持的平台 | 单元/集成：`test_run_remote_action_stops_when_remote_worker_build_is_cancelled`、`test_remote_worker_build_rejects_unsupported_platform_before_ssh` | 取消或输入 Windows 平台时不继续构建/安装 | 中断、非法参数 |
| 远程 SSH、安全日志和现有 worker 功能继续通过 | `tests/test_remote_workers.py` 与完整 `tests/` | 62 项定向测试通过；全套 249 通过、6 环境门控跳过 | 回归、安全、兼容 |

**关键模块测试**：远程 worker 与 SSH 构建流程 62/62 通过；全套测试 249 通过、6 个环境门控用例跳过。

**已知问题与限制**：首次目标机构建仍需要远端 Docker 访问基础镜像源和 Dockerfile 配置的 PyPI 镜像；镜像仍会占用目标机磁盘，但平台到目标机的 SSH 构建输入约 0.63 MiB。未进行真实 Linux 服务器端到端安装。

**下次测试建议**：在可访问 Docker Hub 与 PyPI 镜像的 Linux 服务器上执行远程重新部署，确认首次构建成功；再次部署时检查目标 Docker 缓存是否复用 Python 依赖层，并确认 worker 心跳正常。

**重测触发条件**：修改远程 Docker 构建命令、构建上下文筛选、SSH 二进制输入、取消处理或 worker 安装脚本时，运行 `tests/test_remote_workers.py` 与完整 `tests/`，并重建 Compose 服务。

## 本次变更测试结果：实时远程 worker 部署诊断日志（2026-10-27）

**变更范围**：远程部署操作实时保存带时间戳的脱敏阶段日志和命令输出；日志限制为最近 24,000 个字符。安装 worker 后记录 Docker 容器状态、重启次数及最近 80 行启动日志，并最多等待 15 秒检查资源心跳。前端卡片显示当前阶段和持续时间，日志窗口每 1.5 秒刷新，关闭后停止轮询。既有 `output`、`stderr` 和操作终态字段保持兼容。

**测试环境**：Python 3.12 Docker 镜像 `emdash-hot-boxes-clean-y9u6p-trainer:latest`。完整测试包含工作区原有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动；这些文件没有纳入本次提交。

**测试执行结果**：
- 远程 worker 与 API 定向测试：110 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：250 项；244 通过、0 失败、0 错误、6 跳过。已执行用例通过率 100%。6 项为 Redis、S3 和 Supervisor 子进程环境门控测试。
- `docker compose up --build -d`：基础构建通过，但没有远程管理覆盖，trainer 未挂载 Docker socket；因此第一次远程安装以“无法访问平台 Docker socket”失败。
- `docker compose -f docker-compose.yml -f docker-compose.remote.yml up --build -d`：通过；trainer、worker、frontend 已启动。
- 运行态验证：trainer 挂载 `/var/run/docker.sock`，容器内 socket 存在；平台 Docker API 可读取 `mtp-worker:local` 的平台 `linux/arm64`；`/api/health` 返回 `{"ok":true}`。
- 页面运行环境验证：`curl -fsS http://127.0.0.1:8000/` 返回的页面包含实时日志轮询和资源心跳阶段内容。
- `git diff --check`：通过。
- pytest 报告 2 条既有弃用警告：SQLAlchemy `declarative_base()` 和 Pandas `xlrd` 引擎。

**实际执行命令**：

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py tests/test_api.py -q -p no:cacheprovider
```

结果：110 passed。

```bash
docker compose up --build -d
```

结果：构建和启动成功；默认配置不挂载 Docker socket，无法执行远程镜像导出。

```bash
docker compose -f docker-compose.yml -f docker-compose.remote.yml up --build -d
```

结果：构建和启动成功；trainer 可访问 `/var/run/docker.sock`。

```bash
docker compose -f docker-compose.yml -f docker-compose.remote.yml exec -T trainer python -c 'from pathlib import Path; import app.config as c; from app.remote_workers import _local_docker_image_platform; p=Path(c.WORKER_DOCKER_SOCKET_PATH); print("socket_exists=", p.exists()); print("worker_image=", c.WORKER_REMOTE_IMAGE); print("worker_platform=", _local_docker_image_platform(c.WORKER_REMOTE_IMAGE))'
curl -fsS http://127.0.0.1:8000/api/health
```

结果：`socket_exists=True`、平台镜像 `linux/arm64`、API 返回 `{"ok":true}`。

```bash
docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider
```

结果：244 passed、6 skipped。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 运行期间显示远程阶段和 SSH 输出 | 单元：`test_communicate_remote_process_streams_only_new_output_lines`；集成逻辑：`test_run_remote_install_records_stages_and_worker_heartbeat` | 模拟分段 stdout/stderr、架构检查、镜像传输和 worker 启动；日志只写新增行并显示阶段 | 正常、超时、回归 |
| 操作日志不会泄露凭证且长度有界 | 单元：`test_remote_operation_log_is_redacted_and_bounded` | 输入 SSH 密码、私钥行、Redis URL、S3 密钥和超长日志；预期值被脱敏并截断到限制内 | 安全、边界 |
| 构建失败和 worker 启动状态有可读诊断 | 单元：`test_target_platform_worker_build_failure_is_reported`、`test_remote_bootstrap_script_installs_cpu_and_gpu_workers` | 模拟 Docker 构建错误，验证错误事件；启动脚本验证容器状态和日志快照输出 | 异常、兼容 |
| 资源心跳检查能区分已注册和未注册 | 单元：`test_worker_heartbeat_probe_reports_present_and_missing_slots` | 分别返回匹配的活动 worker 和空集合；预期记录心跳到达或 15 秒无心跳建议 | 正常、边界、异常 |
| 前端日志窗口实时刷新并安全展示 | API/静态契约：`test_remote_server_card_exposes_live_operation_logs` | 检查当前阶段展示、1.5 秒轮询、关闭后停止轮询，日志通过 `textContent` 渲染 | 正常、安全、回归 |
| 原有远程操作结果字段保持兼容 | 单元：`test_execute_remote_operation_persists_success_and_failure` | 成功仍保存 `output`/`stderr`，失败仍保存错误和终态 | 兼容、异常 |

**关键模块测试**：远程部署和 SSH 输出：`tests/test_remote_workers.py`；远程 API 与前端日志契约：`tests/test_api.py`；两者合计 110 项全部通过。完整 `tests/` 为 244 通过、6 个环境门控跳过。

**重测触发条件**：后续修改 `backend/app/` 业务代码、数据模型/数据库操作、数据集链路、训练器/任务管理、API 路由或核心业务配置时，重新运行相关测试及完整套件；仅前端静态文件、文档或构建配置变更按项目规则无需自动重测，用户明确要求时除外。

**已知问题与限制**：没有真实连接用户的 Linux 服务器执行 SSH 安装，因此 Redis/S3 网络、目标机 Docker daemon 和实际 worker 日志尚未端到端验证。15 秒未收到心跳会记录诊断提示，但不会把 SSH 部署成功改判为失败。日志最多保留 24,000 个字符。

**下次测试建议**：在目标 Linux 服务器重新部署一次，确认日志窗口显示镜像架构、传输/加载、容器状态和 worker 启动日志；再分别验证 Redis 或 S3 不可达时的日志诊断。

**Git基准点**：`1c1812c`（Add live remote worker diagnostics）；本测试报告作为单独提交更新。

## 本次变更测试结果：按硬件模式选择平台 Compose 启动（2026-10-27）

**变更范围**：新增 `scripts/start-platform.sh`，按 `PLATFORM_MODE=gpu|cpu` 选择基础 Compose 文件，按 `REMOTE_MANAGEMENT=true|false` 独立选择是否加载远程 socket 覆盖；更新 README 启动说明并添加脚本选择测试。

**测试环境**：Python 3.12 Docker 镜像；Docker Desktop。测试工作区还包含未提交的 `backend/app/remote_workers.py`、`backend/app/storage.py` 与 `backend/tests/test_storage.py` 改动；全量测试和镜像构建包含这些改动，但本次提交未包含它们。

**测试执行结果**：
- 启动脚本定向测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_platform_launcher.py -q -p no:cacheprovider`：7 通过、0 失败。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：246 项，240 通过、6 跳过、0 失败、0 错误；保留 2 条既有弃用警告。
- `bash -n scripts/start-platform.sh`：通过。
- GPU/CPU × 远程覆盖开/关的四种 Compose 配置解析与 socket 挂载断言：全部通过；无远程覆盖时 trainer 不挂载 socket，开启时挂载 `/var/run/docker.sock`。
- `docker compose up --build -d`：通过；随后 `PLATFORM_MODE=gpu REMOTE_MANAGEMENT=true bash scripts/start-platform.sh`：通过。
- 运行态验证：trainer 挂载 Docker socket，平台 Docker API 可读取 `mtp-worker:local` 镜像平台 `linux/arm64`，`/api/health` 返回 200。
- `git diff --check`：通过。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 默认或 CPU 模式选择 CPU Compose，GPU 模式选择 GPU Compose | 启动脚本测试：`test_launcher_selects_compose_files`；用临时 Docker shim 捕获命令 | 未设置模式、CPU、GPU；脚本传入对应基础文件 | 正常、默认行为、兼容 |
| 远程管理独立可选 | 同上及四种 Compose 配置断言 | 开启时追加远程覆盖；关闭时不挂载 Docker socket | 正常、权限边界、兼容 |
| 非法模式与开关在调用 Docker 前失败 | 启动脚本测试：`test_launcher_rejects_invalid_selection_without_running_docker` | `PLATFORM_MODE=auto` 或 `REMOTE_MANAGEMENT=yes`；退出码 2，Docker shim 未运行 | 异常、输入校验 |
| 远程管理启动后 trainer 可访问 Docker Engine | GPU + 远程覆盖真实 Compose 启动、容器检查与 API 验证 | `PLATFORM_MODE=gpu REMOTE_MANAGEMENT=true`；socket、镜像平台查询和健康接口均成功 | 集成、回归 |

**已知问题与限制**：本次未验证 NVIDIA GPU 设备可见性；CPU 模式通过脚本参数测试和 Compose 解析验证，未切换运行中的服务实际启动 CPU 堆栈。平台外部部署器的设置不在仓库中，需配置其调用 `scripts/start-platform.sh` 并设置所选环境变量。

**下次测试建议**：在平台部署器中配置 `PLATFORM_MODE` 与 `REMOTE_MANAGEMENT` 后分别执行 CPU/GPU 部署；确认后续自动重启仍调用本脚本，并在有 NVIDIA GPU 的主机验证 GPU 设备可见性。

**Git基准点**：`3256e7a`（Add mode-aware platform Compose launcher）；本测试报告作为单独提交更新。

## 本次变更测试结果：修复 Docker 自动构建 tar 上下文识别（2026-10-27）

**变更范围**：将 worker Docker 构建上下文从 Python 默认 PAX tar 改为 GNU tar，首个 tar entry 为根目录 `Dockerfile`。Docker Engine API 之前将该上下文当作文本 Dockerfile，误读 `app/worker.py` 中的 `dict(os.environ)` 并报告语法错误。

**测试环境**：Docker 测试镜像 Python 3.12。既有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动保留，并包含在完整测试中；没有纳入本次提交。

**测试执行结果**：
- 远程 worker 定向测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py -q -p no:cacheprovider`：53 通过、0 失败。
- 完整测试：同 Docker 挂载命令运行 `python -m pytest tests/ -q -p no:cacheprovider`：233 通过、6 跳过、0 失败、0 错误。6 项为 Redis、S3 和 Supervisor 子进程的环境门控测试。
- `docker compose up --build -d`：通过；随后以远程管理覆盖配置及当前 Docker Desktop socket 路径重新启动 trainer：通过。`docker compose -f docker-compose.yml -f docker-compose.remote.yml config -q` 通过。
- 运行时上下文检查：归档首项为 `Dockerfile`，构建目录和 Dockerfile 均有效。
- 真实 Docker Engine API 重放 GNU tar 上下文：Dockerfile 语法错误已消失，构建进入基础镜像拉取阶段；30 秒后因网络等待超时中止。AMD64 镜像完整构建未验证。
- `git diff --check`：通过；保留 SQLAlchemy 与 Pandas 两条已有弃用警告。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| Docker Engine 收到标准 tar 上下文而不是 PAX 扩展头 | 远程 worker：`test_builds_worker_image_for_requested_platform_with_minimal_context` | 首个 512 字节 tar header 指向 `Dockerfile`，归档仍包含依赖与 worker 源码 | 正常、Docker API 兼容 |
| 错误目录中的同名 Dockerfile 不进入构建上下文 | 远程 worker：`test_worker_build_context_skips_invalid_same_named_dockerfile` | 含 `dict(os.environ)` 的无效文件被跳过，打包有效项目 Dockerfile | 异常、回归 |
| 构建错误仍停止部署且输出 Docker Engine 原因 | 远程 worker：`test_target_platform_worker_build_failure_is_reported` | 模拟 Engine 返回错误并验证清晰失败结果 | 异常 |
| 远程功能与完整业务套件通过 | `tests/test_remote_workers.py` 与完整 `tests/` | 定向 53 通过；完整 233 通过、6 环境门控跳过 | 回归 |

**已知问题与限制**：当前环境访问 Docker Hub 超时，真实 `linux/amd64` worker 镜像未完成构建。Engine API 重放已验证本次 Dockerfile 解析报错不再出现；需要在基础镜像可下载的部署环境确认构建、SSH 传输和 worker 启动。

**下次测试建议**：在网络可访问 Docker Hub 的平台 Docker 环境点击远程服务器“重新部署并应用配置”，确认目标架构构建和部署完整通过。

**Git基准点**：`55ec8dc`（Use Docker compatible worker build archive）；本测试报告作为单独提交更新。

## 本次变更测试结果：显示远程服务器命令日志（2026-10-27）

**变更范围**：远程操作结束后，服务器卡片提供可展开的执行日志，显示 stdout、stderr 和失败信息；后端保留成功操作的 stdout 与 stderr。日志文本在页面渲染前转义。命令执行期间提示输出将在完成后显示。

**测试环境**：Docker 测试镜像 Python 3.12。工作区存在既有未提交的 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 改动；这些改动参与完整测试，但未纳入本次提交。

**测试执行结果**：
- 日志功能定向用例：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_api.py::test_remote_server_card_displays_command_output_and_error_logs tests/test_remote_workers.py::test_execute_remote_operation_persists_success_and_failure -q -p no:cacheprovider`：2 通过。
- 远程 API 与 worker 测试：同 Docker 命令运行 `tests/test_api.py tests/test_remote_workers.py`：106 通过、0 失败。
- 完整测试：同 Docker 命令运行 `tests/`：233 通过、6 跳过、0 失败、0 错误；跳过项受 Redis、S3 与 Supervisor 子进程环境门控。
- `docker compose up --build -d` 和远程管理覆盖配置重建启动：通过。
- 运行中的 frontend 容器检查：页面含“查看执行日志”控件与日志样式。
- `git diff --check`：通过；保留 SQLAlchemy 与 Pandas 两条既有弃用警告。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 完成的服务器命令可查看 stdout、stderr 和失败信息 | 前端契约：`test_remote_server_card_displays_command_output_and_error_logs` | 渲染器读取三类输出，经过 `esc()` 后放入可展开的 `<pre>`；运行中显示状态提示 | 正常、错误显示、恶意 HTML 防护 |
| 后端保留成功命令的 stdout 与 stderr | 远程操作单测：`test_execute_remote_operation_persists_success_and_failure` | 模拟命令返回 stdout 与 stderr，完成状态中都可读取 | 正常、兼容 |
| 远程 API 与其他业务不回归 | `tests/test_api.py`、`tests/test_remote_workers.py`、完整 `tests/` | 定向 106 项通过；完整 233 项通过、6 项环境门控跳过 | 回归 |

**已知问题与限制**：远程命令通过 SSH 完成后显示完整截断输出（stdout/stderr 各最多 2000 字符）；执行中不逐行流式更新日志。

**下次测试建议**：从页面对测试服务器执行启动或重启，展开“查看执行日志”，确认远端输出可读且敏感凭证仍被脱敏。

**Git基准点**：`c3b2a04`（Show remote server command output）；本测试报告作为单独提交更新。

## 本次变更测试结果：校验并选择有效的 worker Docker 构建上下文（2026-10-27）

**变更范围**：自动构建前从应用目录、容器固定目录和模块目录中选择完整后端上下文；校验 Dockerfile 是 worker 项目文件，并跳过包含 `dict(os.environ)` 这类无效指令的同名文件。避免将错误目录里的 Dockerfile 打包发送给平台 Docker Engine。

**测试环境**：Docker 测试镜像 Python 3.12。工作区既有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动被完整测试覆盖，但不属于本次提交。

**测试执行结果**：
- 构建上下文定向测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py::test_builds_worker_image_for_requested_platform_with_minimal_context tests/test_remote_workers.py::test_worker_build_context_skips_invalid_same_named_dockerfile tests/test_remote_workers.py::test_target_platform_worker_build_failure_is_reported -q -p no:cacheprovider`：3 通过。
- 远程 worker 定向测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py -q -p no:cacheprovider`：53 通过、0 失败。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：232 通过、6 跳过、0 失败、0 错误。6 项为 Redis、S3 与 Supervisor 子进程的环境门控测试。
- `docker compose up --build -d`：通过；远程覆盖 Compose 重建启动也通过。远程 Compose `config -q` 通过。
- 运行容器实际调用 `_worker_build_context()` 检查：打包出 42 项，Dockerfile 不含错误表达式且包含 worker 构建指令。
- `git diff --check`：通过。保留 SQLAlchemy 与 Pandas 两条既有弃用警告。
- 未执行真实 `linux/amd64` Engine 自动构建。独立的 Docker BuildKit 验证未能拉取 `python:3.12-slim`，Docker Hub token 请求网络超时；Compose 重建使用已缓存镜像成功。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 错误目录中的同名 Dockerfile 不会进入 Engine 构建包 | 单元：`test_worker_build_context_skips_invalid_same_named_dockerfile` | 候选目录先提供含 `ENV dict(os.environ)` 的无效文件，后提供有效 worker 目录；归档采用有效 Dockerfile | 错误输入、回归 |
| 正常构建包包含正确 Dockerfile 且不含运行数据 | 单元：`test_builds_worker_image_for_requested_platform_with_minimal_context` | 检查 Engine 请求包含项目 Dockerfile、依赖和 worker 代码，不含数据与测试目录 | 正常、数据隔离 |
| 构建错误继续中止部署并展示 Docker Engine 原因 | 单元：`test_target_platform_worker_build_failure_is_reported` | 模拟 Engine 返回构建错误，接口抛出可读错误 | 异常 |
| 现有远程安装和全平台行为回归通过 | `tests/test_remote_workers.py` 与完整 `tests/` | 53 项远程 worker 测试通过；全套 232 通过、6 环境门控跳过 | 兼容、回归 |

**已知问题与限制**：本环境未能访问 Docker Hub，无法验证真实 AMD64 基础镜像拉取和目标架构构建；需要在 Docker Hub 可达的部署环境触发重新部署验证。

**下次测试建议**：重新部署当前 trainer 版本后再次执行目标为 `linux/amd64` 的远程重部署；确认构建成功并将镜像通过 SSH 传到 Linux 服务器。

**Git基准点**：`c21da75`（Validate worker Docker build context）；本测试报告作为单独提交更新。

## 本次变更测试结果：移除远程管理令牌并默认启用（2026-10-27）

**变更范围**：移除 `WORKER_REMOTE_TOKEN` 和 `WORKER_REMOTE_ENABLED` 配置、Compose 环境变量、远程 API 自定义令牌认证及前端令牌输入；远程管理页面与 API 默认可用。README 说明平台 API 无认证，部署时须通过网络边界限制访问。服务器和 SSH 凭证仍由 MySQL 持久化，不涉及数据库数据迁移或删除。

**测试环境**：Docker 测试镜像 Python 3.12。工作区还存在既有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动；它们包含在完整测试中，但不属于本次代码提交。

**测试执行结果**：
- 定向 API 与远程 worker 测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_api.py tests/test_remote_workers.py -q -p no:cacheprovider`：104 通过、0 失败。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：231 通过、6 跳过、0 失败、0 错误。跳过项为 Redis、S3 和 Supervisor 子进程环境门控测试。
- `docker compose up --build -d`：通过。
- `docker compose -f docker-compose.yml -f docker-compose.remote.yml -f - up --build -d`（将 Docker Desktop socket 覆盖到 trainer）：通过；GPU 和 CPU 远程覆盖配置 `config -q` 检查通过。
- 运行容器页面检查：未发现令牌表单；不带令牌请求 `/api/remote-servers` 返回 HTTP 200，响应包含现存 `Linux` 服务器记录。
- `git diff --check`：通过。保留 SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 两条既有弃用警告。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 远程管理无需启用开关或自定义令牌即可使用 | API：`test_remote_management_is_enabled_without_admin_token`、`test_remote_server_api_installs_and_manages_with_nonsecret_records` | 不提供认证头读取服务器、登记服务器、编辑配置和执行生命周期动作成功；缺失记录仍返回 404，重复登记返回 409 | 正常、兼容、状态冲突 |
| SSH 凭证目录和设置可不带令牌管理，同时秘密仍加密且不回显 | API：`test_remote_ssh_credential_catalog_supports_all_auth_modes_without_secret_echo`、`test_remote_ssh_settings_encrypt_credentials_and_never_return_secrets`、`test_remote_ssh_settings_save_private_key_without_echo` | 无认证头读写/删除凭证成功；MySQL 密文可解密验证；API 响应不含秘密 | 正常、敏感数据保护 |
| 远程操作轮询和取消无需令牌，状态逻辑保持正确 | API：三个 `test_remote_operation_cancel_*` 用例 | 不带认证头读取或取消排队/执行中/残留操作，状态及服务器锁按预期更新 | 正常、并发状态、边界 |
| 前端不再要求或发送远程管理令牌 | 前端契约：`test_remote_ssh_credential_save_button_stays_clickable_while_catalog_loads`；运行容器静态页面检查 | 页面不包含令牌输入、自定义请求头或旧令牌提示；容器 API 不带令牌返回成功 | 兼容、界面回归 |
| 既有 SSH 配置校验、输入安全和其他业务功能继续通过 | `tests/test_remote_workers.py` 与完整 `tests/` | SSH 凭证验证、主机字段注入校验、远程 worker 与其他业务回归通过 | 异常、安全、回归 |

**已知问题与限制**：平台 API 目前没有身份认证，移除管理令牌后，能访问平台的客户端可以管理远程服务器及 SSH 凭证。部署必须通过可信内网、VPN 或反向代理访问控制限制平台管理入口。6 项环境门控测试未执行其外部依赖路径。

**下次测试建议**：在目标部署环境检查网络访问策略，然后从管理页面确认 MySQL 中现有服务器和 SSH 凭证正常显示，并执行预期的远程重部署流程。

**Git基准点**：`a0ebcd6`（Remove remote management token requirements）；本测试报告作为单独提交更新。

## 本次变更测试结果：自动构建目标架构的远程 worker 镜像（2026-10-27）

**变更范围**：重新部署时读取目标 Docker 架构；平台镜像不匹配时通过 Docker Engine Build API，使用打包在 trainer 容器中的最小后端构建上下文，自动构建并校验目标 Linux 架构镜像，然后按既有 SSH 流程传输和安装。构建阶段支持取消；构建失败不会传输或安装错误架构镜像。

**测试环境**：Docker Compose；测试容器 Python 3.12.15。测试时工作区仍有既有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 改动；完整测试包含这些改动，但本次提交未纳入它们。

**测试执行结果**：
- 定向远程 worker 测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py -q -p no:cacheprovider`：52 通过、0 失败。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：231 通过、6 跳过、0 失败、0 错误。跳过项为 Redis、S3 和 Supervisor 子进程的环境门控测试。
- `docker compose up --build -d`：通过；使用远程管理覆盖配置重建启动也通过。trainer 内 Docker socket ping 返回 HTTP 200，构建用 `Dockerfile` 已打包到 `/app/Dockerfile`。
- `git diff --check`：通过。保留 SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 两条既有弃用警告。
- 真实 Linux 远程部署未完成：目标服务器的 SSH 架构探测成功，返回 `linux/x86_64`；本机 Docker Engine 拉取 `python:3.12-slim` 的 amd64 基础镜像时无法解析/连接 Docker Hub。自动构建请求在取消后终止，未向目标机传输镜像，远程 worker 未改变。容器化目标架构构建尚未得到真实网络环境验证。
- 初次定向运行有两项失败：一次测试容器未挂载 Compose 文件，一次新增断言未去除输出换行；修正挂载命令和断言后通过。后续测试运行发现模拟构建器未接受新的可选操作 ID 参数；更新模拟器后定向与完整测试通过。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 架构一致时沿用现有传输流程；架构不一致时自动构建后再传输 | 远程 worker：`test_run_remote_action_trusts_host_key_once_and_keeps_secrets_out_of_argv`、`test_run_remote_action_builds_target_architecture_before_transfer` | 相同平台不触发构建；ARM64 平台对 x86_64 目标自动请求 `linux/amd64` 构建，然后传输镜像并执行安装脚本 | 兼容、正常、架构边界 |
| 最小构建上下文只包含镜像所需文件 | Docker API：`test_builds_worker_image_for_requested_platform_with_minimal_context` | 构建请求包含 Dockerfile、依赖和 worker 代码，不包含数据目录及测试文件；构建结果平台需与目标一致 | 数据隔离、兼容 |
| Docker 构建失败或 Engine 拒绝请求时不部署错误镜像 | Docker API/远程执行器：`test_target_platform_worker_build_failure_is_reported`、`test_target_platform_worker_build_rejects_engine_http_errors`、`test_run_remote_action_stops_when_target_architecture_build_fails` | Docker 返回构建错误或 HTTP 错误时记录明确失败；不传输镜像，不执行远端安装 | 异常、回归 |
| 构建期间可中断操作；拒绝不支持的目标系统 | 远程 worker：`test_target_platform_worker_build_can_be_cancelled`、`test_target_platform_build_rejects_non_linux_targets` | 取消会关闭 Docker 构建连接；非 Linux 目标在构建前被拒绝 | 中断、错误输入 |
| 既有远程安装和 worker 功能继续通过 | `tests/test_remote_workers.py` 与完整 `tests/` | 远程 SSH、镜像传输和全套业务测试无新增失败 | 兼容、回归 |

**已知问题与限制**：自动构建依赖平台 Docker Engine 能访问 Docker Hub 和 Dockerfile 配置的 PyPI 镜像。当前环境 DNS 受限，未完成真实目标镜像构建及 Linux 服务器端到端安装。若目标架构构建失败，远程操作会失败并保留明确错误；不会回退到发送错误架构镜像。

**下次测试建议**：在可访问 Docker Hub 与 PyPI 的平台 Docker 环境中，对 x86_64 Linux 目标点击“重新部署并应用配置”，确认自动构建、SSH 镜像流传输及 worker 启动完整通过。

**重测触发条件**：修改 Docker Engine 构建 API、worker 构建上下文、架构判定、镜像流传输或远程安装流程时，运行 `tests/test_remote_workers.py` 和完整 `tests/`，并重建 Compose 服务。

**Git基准点**：`8c5e8b9`（Build remote worker images for target architecture）；本测试报告作为单独提交更新。

## 本次变更测试结果：支持中断远程 SSH 操作（2026-10-26）

**变更范围**：远程 worker 卡片为排队中或执行中的 SSH 操作提供“中断操作”入口。受保护的取消 API 将请求写入协调存储；执行线程轮询请求并终止本机 SSH 子进程，随后记录 `cancelled` 状态并重新开放服务器控制。排队任务可在 SSH 启动前取消。页面说明取消不会回滚 Linux 上已经完成的部分变更。现有 SSH 保护超时保持不变。

**测试环境**：Docker Compose；测试容器 Python 3.12.15。测试时工作区还包含未提交的 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 改动；它们参与完整测试，但未纳入本次功能提交。

**测试执行结果**：
- 定向协调存储、远程 worker 和 API 测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_coordination.py tests/test_remote_workers.py tests/test_api.py -q -p no:cacheprovider`：119 通过、3 跳过、0 失败（20.09 秒）。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：225 通过、6 跳过、0 失败、0 错误（51.53 秒）。6 项跳过为 Redis、S3 和 Supervisor 子进程集成的环境门控项。
- 首次定向运行有 1 项前端契约断言失败：断言引用了旧变量名 `cancel_requested`，页面实现使用 `cancelRequested`。修正断言后，定向测试和完整测试均通过。
- `docker compose up --build -d`：通过；trainer、worker、frontend 均启动，SeaweedFS healthy。运行中的 trainer OpenAPI 包含 `/api/remote-operations/{operation_id}/cancel`。
- 前端 HTTP 冒烟：通过运行中的 frontend 获取页面，确认响应包含“中断操作”和“正在中断远程操作…”；内联 JavaScript `sed -n '599,2033p' backend/static/index.html | node --check`：通过。
- `git diff --check`：通过。保留 SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 两条既有弃用警告。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 授权管理员可取消排队操作；未授权请求不能改变状态 | API：`test_remote_operation_cancel_requires_admin_and_cancels_queued_operation`；执行器：`test_execute_remote_operation_honors_cancel_before_ssh` | 先无令牌请求，再提交排队操作取消；未授权返回 401 且状态不变，授权后为 `cancelled`，SSH 不启动 | 权限、安全、正常、状态边界 |
| 执行中可以中断 SSH；执行器确认前仍保持忙碌状态 | 子进程：`test_remote_subprocess_stops_when_operation_is_cancelled`；API：`test_remote_operation_cancel_marks_active_operation_as_cancelling` | 对运行中的子进程设置取消信号；子进程退出，API 返回中断请求中，最终操作进入 `cancelled` | 正常、资源清理、并发状态 |
| 后端执行线程已消失时，可以清除遗留的 running 状态 | API：`test_remote_operation_cancel_clears_stale_running_state` | 管理员取消无活动执行线程的 running 记录；操作变为 `cancelled`，服务器控制重新开放 | 异常、恢复、回归 |
| Embedded 与 Redis 协调存储都能传递并清除取消信号 | 协调：`test_remote_operation_cancel_request_isolated_and_clearable`、`test_redis_remote_operation_cancel_signal_is_shared_and_clearable` | 写入后仅对应操作可观察信号；清除后信号消失 | 多实例协调、隔离、兼容 |
| 页面提供取消入口、取消状态和部分变更提示 | API 静态契约：`test_remote_server_cards_expose_interrupt_action_for_active_operations`；运行中 frontend HTTP 与 Node 语法检查 | 卡片显示中断按钮，取消期间显示处理中，确认提示说明远端部分变更不自动回滚 | UI 回归、错误反馈、安全提示 |
| 正常成功、失败和既有远程管理逻辑继续工作 | `tests/test_remote_workers.py`、`tests/test_api.py` 与完整 `backend/tests/` | 远程 SSH、服务器管理、训练、数据和存储测试无新增失败 | 兼容、回归 |

**已知问题与限制**：中断会终止平台侧 SSH 客户端并关闭 SSH 会话，不能撤销 Linux 上已经完成的镜像加载或容器操作。目标机完全卡死或网络故障时，平台无法保证远端进程立即收到断连；页面会提示可能存在部分变更。未连接真实 Linux 服务器执行端到端取消测试。

**下次测试建议**：在 staging Linux 服务器分别于镜像传输、worker 启动阶段点击中断，检查远端 Docker 状态并确认页面状态、服务器控制恢复情况。

**重测触发条件**：修改远程操作协调信号、SSH 子进程生命周期、取消 API 鉴权或页面取消按钮时，运行协调/远程 worker/API 定向测试、完整 `backend/tests/` 并重建 Compose 服务。

**Git基准点**：`d261d53`（Add cancellation for remote worker operations）；本报告作为单独提交更新。

## 本次变更测试结果：修复 SSH 凭证保存按钮不可点击（2026-10-26）

**变更范围**：移除 SSH 凭证保存按钮在页面加载、令牌验证和目录请求期间的初始禁用状态；由提交处理器校验令牌与表单，并在保存期间防止重复提交。缺少令牌或凭证字段不完整时点击会显示明确反馈。凭证状态说明改为 MySQL，与实际存储一致。

**测试环境**：Docker Python 3.12.15；前端通过 Docker Compose 构建并运行。

**测试执行结果**：
- 新增回归测试先失败复现：按钮 HTML 含 `disabled` 属性。修复后执行 `docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_api.py::test_remote_ssh_credential_save_button_stays_clickable_while_catalog_loads -q -p no:cacheprovider`：1 通过。
- API 定向测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_api.py -q -p no:cacheprovider`：48 通过、0 失败（20.56 秒）。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：216 通过、6 跳过、0 失败、0 错误（60.06 秒）。6 项跳过为 Redis、S3 和 Supervisor 子进程集成环境门控。
- `node` 内联 JavaScript 语法检查：通过。
- `docker compose up --build -d` 与远程覆盖 Compose 重建：均通过；容器内确认保存按钮无 `disabled` 属性、状态提示显示 MySQL 且不再显示旧 Redis 文案。远程 Compose `config -q` 和服务状态检查通过。
- `git diff --check`：通过；保留 SQLAlchemy 和 Pandas 两条既有弃用警告。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 页面加载和凭证目录刷新期间保存按钮仍可点击 | 前端契约：`test_remote_ssh_credential_save_button_stays_clickable_while_catalog_loads` | 按钮无初始禁用属性，目录状态变化不禁用按钮；新测试先复现失败，再通过 | 回归、异步加载 |
| 缺少令牌或表单字段时不静默拦截 | 前端契约：同上；API：完整 `test_api.py` | 点击处理器提示令牌要求；表单用 `reportValidity()` 检查，字段不完整时给出提示 | 边界、错误反馈 |
| 页面说明与 MySQL 实际凭证存储一致 | 前端契约：同上；运行容器静态资源检查 | 显示凭证加密保存在 MySQL，不再显示共享 Redis | 状态提示、部署回归 |
| 现有远程管理、训练、数据和存储功能回归通过 | 完整 `backend/tests/` | 216 通过、6 环境门控跳过、0 失败 | 兼容、回归 |

**已知问题与限制**：未在真实浏览器中输入凭证完成保存；页面行为由静态契约、API 回归测试及重建后容器资源检查覆盖。缺少管理令牌或 MySQL/加密配置时按钮仍可点击，但服务端会拒绝保存并显示具体错误。

**下次测试建议**：刷新页面后展开“SSH 凭证管理”，确认按钮可点击；在测试环境配置新 SSH 凭证并验证保存后列表出现凭证元数据。

**重测触发条件**：修改 SSH 凭证表单、目录加载状态、表单校验或凭证 API 时，运行新增前端契约、`tests/test_api.py` 和完整 `backend/tests/`，并重建前端容器。

**Git基准点**：`0a60685`（Keep SSH credential save button interactive）；本报告作为单独提交更新。

## 本次变更测试结果：远程服务器和 SSH 凭证持久化到 MySQL（2026-10-26）

**变更范围**：新增远程服务器配置、SSH 凭证密文和 Redis 迁移标记三张 MySQL 表。服务器新增、编辑、列表、部署状态更新均使用 MySQL；SSH 凭证目录以 Fernet 密文保存在 MySQL，页面仍只收到凭证元数据。远程操作轮询状态继续放在 Redis。启动及首次读取时将旧 Redis 配置幂等迁入 MySQL，不覆盖已有 MySQL 值；迁移标记可避免已删除凭证在重启后复活。远程 Compose 默认启用 MySQL，数据库不可用时接口明确返回错误，不回退写入 Redis 或进程内存。

**测试环境**：pytest 使用 Docker Python 3.12.15；仓储行为测试使用隔离 SQLite；另用已连接的真实 MySQL 检查新增表、`LONGTEXT` 密文列、服务器配置增删改查和迁移标记。全量测试同时包含工作区原有未提交的 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 改动；这两个文件未纳入本次功能提交。

**测试执行结果**：
- 定向远程仓储、worker 与 API 测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_config_repository.py tests/test_remote_workers.py tests/test_api.py -q -p no:cacheprovider`：94 通过、0 失败（17.31 秒）。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：215 通过、6 跳过、0 失败、0 错误（47.03 秒）。跳过项为 3 项 Redis 集成、2 项 S3 集成和 1 项 Supervisor 子进程集成，受外部服务或环境门控。
- 关键模块：MySQL 仓储持久化与迁移测试、远程 worker 凭证和操作测试、远程管理 API 安全/脱敏测试合计 94/94 通过；完整套件 215/215 非跳过用例通过。
- `docker compose up --build -d` 与 `docker compose -f docker-compose.yml -f docker-compose.remote.yml up --build -d`：均通过；trainer、worker 与 frontend 启动。trainer Python 版本为 3.12.15。
- GPU/CPU 远程覆盖 Compose 配置检查：`docker compose -f docker-compose.yml -f docker-compose.remote.yml config -q` 与 `docker compose -f docker-compose.cpu.yml -f docker-compose.remote.yml config -q` 均通过。
- 真实 MySQL 冒烟：连接成功；`remote_servers`、`remote_ssh_credentials`、`remote_config_migrations` 表存在；凭证密文列类型为 `LONGTEXT`；服务器配置新增、读取、删除通过；Redis 迁移标记存在。MySQL 中已有 SSH 凭证记录，因此冒烟脚本保留该记录并跳过凭证单例写入；凭证密文 CRUD 与加密断言由隔离 SQLite 测试覆盖。
- `git diff --check`：通过。保留 SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 两条既有弃用警告。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 服务器配置和加密凭证跨数据库会话持久化；MySQL 模型表及密文大字段可用 | 仓储：`test_remote_server_and_credential_ciphertext_persist_in_database`；真实 MySQL 服务器配置 CRUD 冒烟、表结构检查 | 保存服务器 JSON 与 Fernet 密文，再开新会话读取相同内容；真实 MySQL 服务器记录可新增、读取、删除，凭证密文列为 LONGTEXT | 正常、持久化、MySQL 方言 |
| 旧 Redis 配置仅迁移一次、不覆盖 MySQL，删除后不复活 | 仓储：`test_legacy_redis_remote_config_migrates_once_without_overwriting_mysql`、`test_legacy_migration_marker_is_written_after_success` | Redis 与 MySQL 对同一服务器值不同时保留 MySQL 值；凭证迁移后删除并重跑迁移仍为空 | 兼容、幂等、删除边界 |
| 三种页面 SSH 凭证可加密管理，API 不回传秘密且受管理令牌保护 | API：`test_remote_ssh_credential_catalog_supports_all_auth_modes_without_secret_echo`、`test_remote_ssh_settings_encrypt_credentials_and_never_return_secrets`、`test_remote_ssh_settings_save_private_key_without_echo` | 密码、密钥、密钥加密码保存为 Fernet 密文；未授权请求拒绝；响应只含状态和元数据 | 正常、权限、安全、敏感信息保护 |
| 禁用 MySQL 或数据库读取失败时给出明确错误，不回退存储 | 仓储/API：`test_remote_mysql_repository_rejects_disabled_database`、`test_remote_ssh_settings_require_mysql_and_encryption_key`、`test_remote_ssh_settings_redacts_mysql_read_failure` | `USE_DATABASE=false` 或 MySQL 读取异常时请求失败并脱敏，不写 Redis；缺少 Fernet key 时拒绝凭证保存 | 异常、安全、配置边界 |
| 远程服务器生命周期配置持久化，操作轮询状态仍兼容 Redis | API/worker：`test_remote_server_api_installs_and_manages_with_nonsecret_records`、`test_execute_remote_operation_persists_success_and_failure` | 添加/编辑服务器与完成/失败状态落在 MySQL；操作状态可通过协调存储查询；Redis URL 不明文返回 | 正常、失败、兼容、回归 |
| 既有训练、数据、存储和 API 功能继续工作 | 完整 `backend/tests/` | 215 通过、6 环境门控跳过、0 失败 | 回归、兼容 |

**已知问题与限制**：未连接真实 SSH 目标验证远程安装。真实 MySQL 冒烟未覆盖凭证单例写入，因为数据库已有记录，避免覆盖平台凭证；该行为由隔离 SQLite 仓储测试和 API 加密测试覆盖。外部 Redis、S3 和 Supervisor 子进程集成仍受测试环境门控。

**下次测试建议**：在 staging 环境使用新建且隔离的 SSH 凭证记录验证 MySQL 凭证写入/读取/删除，再连接真实 SSH 目标跑通远程安装、重新部署和 worker 状态轮询。

**重测触发条件**：修改远程配置模型、MySQL 仓储、Redis 迁移、远程管理 API 或凭证加密时，运行远程仓储/worker/API 定向测试、完整 `backend/tests/`、GPU/CPU Compose 检查，并验证 MySQL schema 和连接。

**Git基准点**：`9b3ba84`（Persist remote management config in MySQL）；本报告作为单独提交更新。

## 本次变更测试结果：经 SSH 流式传输本机 worker 镜像（2026-10-26）

**变更范围**：远程安装不再要求目标机访问镜像仓库。trainer 通过挂载的 Docker socket 查询本机镜像平台并导出归档，以流式方式经 SSH stdin 传给目标机 `docker load`；部署脚本确认镜像已加载后再启动 worker。服务器覆盖值和平台 `WORKER_IMAGE` 都指向平台 Docker 中的本地标签。安装前会对比本机镜像与目标 Docker 的系统/架构，名称别名会规范化，平台不一致时在传输镜像前拒绝并提示。

**测试环境**：Docker Compose；测试镜像 Python 3.12.15。远程管理覆盖配置将 `/var/run/docker.sock` 挂载给 trainer。工作区还保留已有未提交的 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 改动；它们未纳入本次功能提交，但参与了完整测试。

**测试执行结果**：
- 定向远程 worker 与 API 测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py tests/test_api.py -q -p no:cacheprovider`：90 通过、0 失败（19.44 秒）。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -v "$PWD/docker-compose.remote.yml:/workspace/docker-compose.remote.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：211 通过、6 跳过、0 失败、0 错误（55.27 秒）。6 项跳过为 3 项 Redis 集成、2 项 S3 集成和 1 项 Supervisor 子进程集成，受外部服务或环境门控。
- 全量测试有一次 `test_cancel_running_via_signal` 未及时观察到取消日志；该用例单独复跑 1 通过，之后完整套件重跑通过。此用例与远程镜像变更无关，记录为一次时序波动。
- `docker compose up --build -d` 与 `docker compose -f docker-compose.yml -f docker-compose.remote.yml up --build -d`：均通过。GPU 与 CPU 远程覆盖配置的 `docker compose ... config -q` 检查均通过；trainer 在 Python 3.12.15 下启动，容器内确认 Docker socket 已挂载。
- 通过运行中的 trainer 访问真实 Docker Engine API：版本接口返回 HTTP 200 / API 1.56；`mtp-worker:local` 平台为 `linux/arm64`；镜像导出接口成功读取 512 字节 tar 归档头。
- Shell 脚本语法、前端内联 JavaScript 语法、页面 HTTP 提示内容与 `git diff --check`：均通过。
- 保留 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 可以从平台 Docker 按标签导出镜像归档，不将完整镜像写入临时文件 | Docker API：`test_local_docker_image_archive_uses_engine_api_version_and_tag_path`、`test_local_docker_image_platform_reads_engine_image_metadata`；流传输：`test_worker_image_archive_streams_over_ssh_without_saving_it` | 按 Engine API 版本查询镜像归档与平台信息；模拟归档经 SSH stdin 到达接收端且内容一致，无临时 tar 文件 | 正常、路径编码、资源与 I/O |
| 安装先检查目标 Docker，并通过 SSH `docker load` 传输；目标脚本不再 pull 镜像或同步源码 | SSH 与脚本：`test_run_remote_action_trusts_host_key_once_and_keeps_secrets_out_of_argv`、`test_render_remote_script_quotes_values_and_does_not_transfer_repository`、`test_remote_bootstrap_script_installs_cpu_and_gpu_workers` | 本地标签默认值经流传输，远程动作运行已加载镜像；脚本没有 `docker pull` 或 `git clone` | 正常、兼容、CPU/GPU 部署回归 |
| 本机与远端平台不兼容时不发送大镜像；平台别名可识别 | 架构：`test_run_remote_action_rejects_mismatched_image_architecture_before_transfer`、`test_run_remote_action_trusts_host_key_once_and_keeps_secrets_out_of_argv` | arm64 镜像对 amd64 目标明确失败且传输函数不调用；本机 `amd64` 与远端 `x86_64` 可匹配 | 兼容性、边界、失败路径 |
| 平台检查错误不得泄露 SSH 凭证；归档失败错误同样脱敏 | `test_remote_docker_platform_rejects_errors_and_redacts_credentials`、`test_worker_image_transfer_failure_is_reported_and_redacted` | Docker 检查失败或返回非法平台时拒绝并隐藏密码；远端 load 错误隐藏敏感值 | 异常、权限与安全、恶意输出 |
| GPU/CPU Compose 均挂载 Docker socket，并保留平台/服务器本地标签默认与覆盖 | `test_compose_separates_local_worker_image_from_remote_default`；GPU/CPU `config -q`；真实 trainer socket 冒烟 | 两类 Compose 可解析并挂载 socket；Docker Engine API 可访问 | 配置兼容、部署回归 |
| 既有 API、训练、数据和存储回归通过 | 完整 `backend/tests/` | 211 通过、6 环境门控跳过，无最终失败 | 兼容、回归 |

**已知问题与限制**：未连接真实远程 SSH 服务器做端到端完整镜像传输和 worker 启动。目标 Docker 的操作系统和 CPU 架构必须与平台本地镜像一致；当前构建出的本机 worker 镜像为 `linux/arm64`，部署到 amd64 服务器前需在平台 Docker 中准备 amd64 worker 镜像。Docker socket 控制宿主 Docker，权限接近宿主 root；远程管理 API 应限制网络访问。

**重测触发条件**：修改 Docker Engine API 导出、镜像 SSH 流、平台匹配、远程 worker 安装脚本或远程 Compose socket 挂载时，运行远程 worker/API 定向测试、GPU/CPU Compose 检查和完整 `backend/tests/`，并重新检查实际 socket 访问。

**Git基准点**：`8493fac`（Stream worker images over SSH）；本报告作为单独提交更新。

## 本次变更测试结果：分离本机与远程 worker 镜像默认值（2026-10-25）

**变更范围**：Compose 原先把本地构建用的 `mtp-worker:local` / `mtp-worker-cpu:local` 注入 `WORKER_IMAGE`，后端因此把不可供远程服务器拉取的本地标签误当成远程默认镜像。现在 Compose 将显式 `.env` 中的 `WORKER_IMAGE` 单独传给 `WORKER_REMOTE_IMAGE`；本机默认标签仍只供本机 worker 使用。若平台和服务器都没有配置远程镜像，远程管理继续明确报缺少可拉取镜像。

**测试环境**：Docker Compose 与 Docker pytest 镜像 Python 3.12。当前 `.env` 未设置 `WORKER_IMAGE`；重新构建后运行中的 trainer 解析到的远程默认镜像为空，而非 `mtp-worker:local`。完整测试时共享工作区仍有未提交的 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 改动，它们参与了测试但未纳入本次提交。

**测试执行结果**：
- 回归测试先失败复现问题：测试进程设置本机镜像 `mtp-worker:local` 且远程镜像为空时，旧配置仍解析出本机标签；GPU/CPU Compose 也都缺少独立 `WORKER_REMOTE_IMAGE` 映射。修复后两项回归测试通过。
- 定向回归：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py::test_remote_image_config_does_not_inherit_local_compose_fallback tests/test_remote_workers.py::test_compose_separates_local_worker_image_from_remote_default -q -p no:cacheprovider`：2 通过。
- `docker compose -f docker-compose.yml -f docker-compose.remote.yml config -q` 与 CPU Compose 等价命令：均通过。
- `docker compose up --build -d`：通过；trainer、worker、frontend 镜像重建并启动，frontend 与 SeaweedFS healthy。
- 运行容器检查：`docker compose exec -T trainer python -c 'from app import config; from app.remote_workers import remote_deployment_settings; print("WORKER_REMOTE_IMAGE_empty=", config.WORKER_REMOTE_IMAGE == ""); print("effective_remote_image=", repr(remote_deployment_settings({})["worker_image"]))'`：确认远程默认值为空，未再继承 `mtp-worker:local`。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/docker-compose.yml:/workspace/docker-compose.yml:ro" -v "$PWD/docker-compose.cpu.yml:/workspace/docker-compose.cpu.yml:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：204 通过、6 跳过、0 失败、0 错误（58.75 秒）。6 项跳过为 3 项 Redis 集成、2 项 S3 集成、1 项 Supervisor 子进程集成，受外部服务或环境门控。
- `git diff --check`：通过。保留 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 本机容器镜像默认值不会被当作远程可拉取镜像；显式平台 WORKER_IMAGE 仍成为远程默认值 | 配置回归：`test_remote_image_config_does_not_inherit_local_compose_fallback` | `WORKER_IMAGE=mtp-worker:local` 且 `WORKER_REMOTE_IMAGE` 为空时远程默认为空；显式远程默认值保持原样 | 默认值、边界、兼容 |
| GPU 与 CPU Compose 都将平台镜像值映射到远程默认配置，同时保留本地镜像回退 | Compose 静态契约：`test_compose_separates_local_worker_image_from_remote_default`；两种 Compose `config -q` | 检查两份 Compose 中独立远程映射和本机默认镜像，配置文件可解析 | CPU/GPU 兼容、部署回归 |
| 服务器镜像覆盖仍优先于平台默认，现有远程管理回归保持通过 | `test_remote_deployment_settings_apply_per_server_overrides_and_platform_fallback`；完整 `backend/tests/` | 单机覆盖值优先，平台值作为回退；204 通过、6 环境门控跳过 | 正常、回归 |

**已知问题与限制**：没有可用仓库镜像时，若 `.env` 未设置可被目标服务器拉取的 `WORKER_IMAGE`，且服务器也未填写镜像覆盖，页面仍会提示镜像缺失；本轮不会把本机 `mtp-worker:local` 当作远程镜像。需要使用真实 registry 引用配置平台默认值或该服务器覆盖值。本轮未连接目标服务器验证实际拉取权限。

**重测触发条件**：修改 worker 镜像配置来源、Compose 默认值或远程部署参数校验时，重新运行新增的两个镜像默认值回归用例、远程 worker 定向测试和完整 `backend/tests/`。

**Git基准点**：`e1f5d1e`（Separate remote and local worker image defaults）；本报告作为单独提交更新。

## 本次变更测试结果：远程服务器部署配置覆盖与 SSH 首次主机指纹信任（2026-10-25）

**变更范围**：远程服务器可分别覆盖 WORKER_IMAGE、Redis URL 和 S3 地址；未填写时回退到平台 `.env` 配置。Redis 覆盖值加密存储并在 API 中脱敏，添加和编辑服务器时均可配置。SSH known_hosts 改为持久化到 DATA_DIR 并使用 `StrictHostKeyChecking=accept-new`：首次连接自动登记主机指纹，后续连接仍校验指纹变化。部署配置变更后通过“重新部署并应用配置”动作生效。

**测试环境**：Docker 测试镜像 Python 3.12；完整测试使用只读挂载的 `backend/` 和 `scripts/`。当前共享工作区另有未提交的 `backend/app/storage.py` 与 `backend/tests/test_storage.py` 改动，这些既有改动参与了完整测试，但未包含在本功能提交中。

**测试执行结果**：
- `docker compose up --build -d`：通过；frontend、trainer、worker 已重建并运行，frontend 与 SeaweedFS healthy。
- GPU 与 CPU Compose 配置检查：`docker compose -f docker-compose.yml -f docker-compose.remote.yml config -q` 和 `docker compose -f docker-compose.yml -f docker-compose.cpu.yml config -q` 均通过。
- 定向测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py tests/test_api.py -q -p no:cacheprovider`：81 通过、0 失败。
- 完整测试：`docker run --rm -v "$PWD/backend:/workspace/backend:ro" -v "$PWD/scripts:/workspace/scripts:ro" -w /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider`：202 通过、6 跳过、0 失败、0 错误（52.99 秒）。6 项跳过为 3 项 Redis 集成、2 项 S3 集成、1 项 Supervisor 子进程集成，受外部服务或环境门控。
- 前端内联脚本 `node -e 'const fs=require("fs"),vm=require("vm"); const html=fs.readFileSync("backend/static/index.html","utf8"); const tail=html.slice(html.lastIndexOf("<script")); const script=tail.slice(tail.indexOf(">")+1,tail.indexOf("</script>")); new vm.Script(script); console.log("inline script syntax: passed")'`：通过。
- `curl -sS http://127.0.0.1:8000/ | rg -o 'remoteWorkerImage|remoteRedisUrl|remoteS3Endpoint|重新部署并应用配置' | sort -u`：页面返回三个覆盖字段和重新部署入口。
- `git diff --check`：通过。保留 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。
- 测试准备中的首次尝试：Compose trainer 镜像未包含测试目录，直接运行 pytest 未发现测试；只挂载 backend 的完整测试有 13 项因未挂载 `scripts/worker-node.sh` 失败。随后将 `scripts/` 也挂载后重跑完整套件通过。定向用例倒序首次运行暴露一个用例未清理合成服务器状态的问题；增加 pytest finalizer 后倒序定向测试通过。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 服务器覆盖值优先，空覆盖回退平台默认；完整单机配置不要求重复设置 WORKER_REMOTE_* | 远程 worker：`test_remote_deployment_settings_apply_per_server_overrides_and_platform_fallback`、`test_remote_configuration_accepts_complete_server_overrides_without_remote_env_values` | 分别提供/省略镜像、Redis、S3 覆盖；得到服务器值或平台 `.env` 值，完整覆盖时配置检查通过 | 正常、空值边界、兼容 |
| 添加、编辑和清除服务器覆盖值有效，编辑期间 Redis 密文不回显，未授权编辑被拒绝 | API：`test_remote_server_api_installs_and_manages_with_nonsecret_records`；页面契约：`test_remote_server_form_exposes_host_overrides_and_edit_action` | 创建服务器并覆盖三个值；编辑镜像/S3、保留或清除 Redis；响应不包含密文或 Redis URL，缺少管理令牌返回 401 | 正常、权限、敏感信息安全、回归 |
| 第一次 SSH 连接自动登记主机密钥，后续仍使用 known_hosts 校验；SSH 参数不泄漏密钥 | SSH 执行：`test_run_remote_action_trusts_host_key_once_and_keeps_secrets_out_of_argv`、`test_run_remote_action_uses_password_from_platform_askpass` | 验证 `accept-new`、持久 known_hosts 路径、密钥/密码不进入命令行，凭证从输出脱敏 | 正常、权限与安全、兼容 |
| 配置表单提供覆盖项及重新部署入口，Compose 不再要求人工提供 known_hosts 宿主机路径 | 前端契约、GPU/CPU Compose `config -q`、页面 HTTP 冒烟 | 表单包含镜像、Redis、S3 字段和重新部署动作；两种 Compose 配置均可解析 | 兼容、部署回归 |
| 既有训练、数据、存储和远程 API 回归通过 | 完整 `backend/tests/` | 202 通过、6 项环境门控跳过，无失败 | 兼容、回归 |

**已知问题与限制**：本轮未连接真实远程 SSH 主机，主机指纹握手及镜像/Redis/S3 在目标服务器上的实际可达性未做端到端验证。首次连接采用 TOFU（首次连接信任），因此需要确保首次连接时目标地址确实指向预期主机；已登记主机后指纹变更会被拒绝。

**重测触发条件**：修改远程服务器配置字段、配置优先级、凭证加密/脱敏、known_hosts 路径或 SSH 校验策略时，运行远程 worker/API 定向测试和完整 `backend/tests/`，并检查 GPU/CPU Compose 配置。

**Git基准点**：`2524386`（Isolate remote worker operation test state）；本报告作为单独提交更新。

## 本次变更测试结果：远程添加问题提示层级修复（2026-10-25）

**变更范围**：远程添加服务器弹窗打开时，将 toast 提示层级从 50 提高到 70，高于弹窗层级 60；增加静态前端回归测试，保证提示层级继续高于弹窗。

**测试环境**：Docker Compose；pytest 容器 Python 3.12.15。测试执行时工作区另有未提交的远程配置、后端及测试改动；完整测试覆盖了当前工作区内容，这些既有改动未包含在 `9085e9b` 提交中。

**测试执行结果**：
- 回归测试先在旧样式下失败（提示层级 50 不高于弹窗 60），确认稳定复现；调整后定向用例通过（1 通过）。
- `docker compose up --build -d`：通过；trainer、worker、frontend 镜像重建并启动，frontend 与 SeaweedFS healthy。
- `docker compose run --rm --no-deps -v "$PWD/backend/tests:/app/tests:ro" -v "$PWD/scripts:/scripts:ro" trainer python -m pytest /app/tests/test_api.py::test_remote_server_feedback_toasts_render_above_add_modal -v`：1 通过、0 失败。
- `docker compose run --rm --no-deps -v "$PWD/backend/tests:/app/tests:ro" -v "$PWD/scripts:/scripts:ro" trainer python -m pytest /app/tests -v`：207 项收集，201 通过、6 跳过、0 失败、0 错误（68.36 秒）。
- 6 项跳过：3 项 Redis 集成、2 项 S3 集成、1 项 Supervisor 子进程集成，受外部服务或环境门控。
- 前端内联 JavaScript `node --check -`：通过；`git diff --check`：通过。
- 保留 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。
- 未执行真实浏览器截图核对；层级关系由新增 CSS 静态契约测试验证。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 远程添加弹窗显示时，校验或提交错误提示位于弹窗上方 | 前端静态契约：`test_remote_server_feedback_toasts_render_above_add_modal`；读取内联 CSS | 比较 `.server-modal` 与 `#toastWrap` 的 z-index；toast 层级必须更高 | 错误提示、弹窗层叠、回归 |
| 现有 API、训练、数据、存储和远程管理行为保持兼容 | 完整 `backend/tests/` | 207 项收集，201 通过、6 跳过、0 失败 | 兼容性、回归 |

**已知问题与限制**：Redis/S3/Supervisor 集成测试需相应外部服务或环境配置；本轮未做浏览器截图级人工核对。

**重测触发条件**：修改远程添加弹窗或 toast 的层叠样式时，重新运行新增前端静态契约用例；若改动业务后端，再运行完整测试并更新本报告基准点。

**Git基准点**：`9085e9b`（Show remote server feedback above modal）；本报告在后续单独提交。

## 本次变更测试结果：LLM 配置表单响应式布局修复（2026-10-25）

**变更范围**：LLM 配置区横跨训练表单整行；内部字段网格按可用宽度自适应，最小列宽 210px，避免窄栏下 Hub/PPO 字段被压缩。更新样式资源 URL 版本，避免浏览器继续复用旧 CSS 缓存。

**测试环境**：Docker Compose；pytest 容器 Python 3.12.15。容器重建后训练页静态资源由前端和 trainer 镜像提供。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| LLM 配置区跨越训练表单整行，避免与任务类型并排挤占宽度 | 前端静态契约：`test_training_page_resource_retry_and_chart_style_contract`；页面包含 `#llmFields`，样式选择器限定在训练表单中并声明跨列 | LLM PPO/RLHF 显示 Hub、Prompt、样本数和 PPO 字段；LLM 区域占满外层表单网格 | 正常、响应式、回归 |
| 内部字段随可用宽度自适应，不出现过窄列 | 同一前端静态契约；CSS 检查内部网格 `auto-fit` 与 210px 最小列宽 | 空间允许时并排显示，空间不足时收为单列；输入字段不再落入约百像素窄列 | 窄屏边界、响应式 |
| 浏览器刷新后会获取新版样式，而不是使用旧 CSS 缓存 | 前端静态契约断言 HTML 的版本化 CSS URL；`curl` 检查服务响应 | 入口指向 `/app.css?v=20261025-2`，CSS 请求返回 200；新的 URL 使浏览器重新请求更新后的样式 | 缓存、部署回归 |
| 既有训练、数据、LLM、资源调度功能继续工作 | 完整 `backend/tests/` 测试套件 | 202 项收集；196 项通过，6 项环境门控跳过，0 失败、0 错误 | 兼容性、回归 |

**测试执行结果**：
- 修改前的布局断言和缓存版本断言均按预期失败；布局样式及 CSS 资源 URL 更新后，定向测试通过（1 通过）。
- `docker compose run --rm --no-deps -v "$PWD/backend/tests:/app/backend/tests:ro" -v "$PWD/backend/static:/app/backend/static:ro" -v "$PWD/scripts:/app/scripts:ro" trainer python -m pytest backend/tests/test_api.py::test_training_page_resource_retry_and_chart_style_contract -v`：1 通过。
- `docker compose up --build -d`：通过；frontend、trainer、worker 镜像重建并启动，SeaweedFS healthy。
- `docker compose run --rm --no-deps -v "$PWD/backend/tests:/app/backend/tests:ro" -v "$PWD/scripts:/app/scripts:ro" trainer python -m pytest backend/tests/ -v`：202 项收集，196 通过、6 跳过、0 失败、0 错误（57.05 秒）。
- 6 项跳过：3 项 Redis 集成、2 项 S3 集成、1 项 Supervisor 子进程集成，受外部服务或环境门控。
- `curl -sS http://127.0.0.1:8000/ | rg 'app\.css\?v='` 与 `curl -sSI 'http://127.0.0.1:8000/app.css?v=20261025-2' | head -5`：入口指向新 URL，CSS 返回 HTTP 200；服务端缓存 1 小时，通过变更 URL 版本让浏览器获取新版资源。
- `git diff --check`：通过；保留 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。
- 未执行浏览器截图级人工核对；响应式行为由前端样式契约检查和 CSS 规则覆盖。

**Git基准点**：`92283c1`（Refresh training stylesheet cache key）；本报告在后续单独提交。

## 本次变更测试结果：训练资源轨迹与失败任务重试（2026-10-25）

**变更范围**：训练任务详情和列表显示实际计算节点、worker 槽位、CPU/GPU、显存与每次执行分段；失败任务支持自动分配或手动指定在线计算槽重试，并继续遵守任务的 GPU 与最低显存要求；优化训练结果卡片、指标图表和移动端布局。

**Git 基准点**：`0ac97b6`（Add training resource tracking and retry controls）；分支 `emdash/hot-boxes-clean-y9u6p`。本报告作为单独提交更新。

**测试环境**：Docker Compose；pytest 容器 Python 3.12.15；未连接真实 GPU。worker 资源选择、GPU 能力和显存边界由集成测试的计算槽注册数据覆盖。完整测试时工作区还含有未提交的存储层改动（`backend/app/storage.py`、`backend/tests/test_storage.py`），这些改动参与了测试，但未纳入本功能提交。

**测试执行结果**：
- `docker compose up --build -d`：通过，trainer、worker、frontend 镜像重建并启动，SeaweedFS healthy。
- `docker compose run --rm --no-deps -v "$PWD/backend/tests:/app/backend/tests:ro" -v "$PWD/scripts:/app/scripts:ro" trainer python -m pytest backend/tests/ -v`：收集 202 项，196 通过、6 跳过、0 失败、0 错误（56.74 秒）。
- 补充输入边界断言后，`docker compose run --rm --no-deps -v "$PWD/backend/tests:/app/backend/tests:ro" -v "$PWD/scripts:/app/scripts:ro" trainer python -m pytest backend/tests/test_api.py::test_failed_job_retry_api_checks_resource_requirements_and_assignment -v`：1 通过、0 失败（0.43 秒）。
- 前端内联 JavaScript `node --check -`：通过；`git diff --check`：通过；Safari 桌面页面已核对资源轨迹、重试控件与浅色曲线布局。
- 6 项跳过：3 项 Redis 协调集成、2 项 S3 集成、1 项 Supervisor 子进程集成，均受外部服务或环境门控。保留 2 条既有弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**前端脚本语法检查命令**：
```bash
set -o pipefail
docker compose run --rm --no-deps trainer python -c 'import pathlib,sys; html=pathlib.Path("/app/static/index.html").read_text(); script=html.rsplit("<script", 1)[1].split(">", 1)[1].split("</script>", 1)[0]; sys.stdout.write(script)' | node --check -
```

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 任务详情记录正在执行的节点、槽位、设备与 epoch 分段；暂停、失败、完成、自动重试和 watchdog 重派均保留实际执行轨迹 | Worker/任务管理器：`test_worker_completes_job`、`test_business_error_fails_without_retry`、`test_env_error_auto_retries_then_completes`、`test_env_error_exhausts_retry_then_failed`、`test_watchdog_redispatches_expired_running`；嵌入式队列和测试 worker | 运行/失败/重试任务；详情和 segments 含 machine、slot、device、状态与 epoch 范围，watchdog 关闭旧分段后重新排队 | 正常、异常、状态恢复、回归 |
| 手动重试可指定在线槽或使用自动分配；只有失败任务可重试，重试计数和错误历史正确 | API/任务管理器：`test_failed_job_retry_api_checks_resource_requirements_and_assignment`、`test_manual_retry_requeues_failed_job_with_selected_assignment`；准备 failed、queued 记录及 CPU/GPU worker | 指定满足条件的 worker 后返回 queued 和 worker ID；空选择交由自动调度；非失败返回 409，缺失任务返回 404，手动计数加一且自动计数重置 | 正常、边界、状态冲突、兼容 |
| 指定目标 worker 才能领取任务；GPU 勾选和最低显存要求在重试中继续生效 | Worker/API：`test_manual_retry_target_is_the_only_worker_that_claims_job`、`test_require_gpu_job_not_claimed_by_cpu_worker`、`test_min_vram_job_skipped_by_smaller_gpu`、`test_min_vram_job_claimed_by_big_gpu`、API 重试用例 | CPU、低显存 GPU、高显存 GPU、停用或离线槽；不兼容选择被拒绝/不能领取，兼容 GPU 槽可领取 | 正常、资源边界、异常、回归 |
| 空/超长 worker ID 被校验；恶意格式只作为普通 ID 精确查找，未注册资源不能被分配 | API：`test_failed_job_retry_api_checks_resource_requirements_and_assignment`；对失败任务提交边界与注入样式 ID | 空字符串及 161 字符 ID 返回 422；`../../;rm -rf /` 返回 409，拒绝未注册资源 | 边界、非法输入、安全 |
| 训练详情、任务卡片与图表有一致清晰的资源布局，重试选项只展示在线且满足资源约束的槽位 | 前端契约：`test_training_page_resource_retry_and_chart_style_contract`；前端内联脚本 `node --check -`；Safari 桌面页面检查 | 页面含资源轨迹、重试 API 与筛选逻辑；图表使用浅色网格/图例，详情和图表在桌面布局可读 | 正常、兼容、回归 |
| 既有训练、预测、数据集双布局、LLM 与 SSH API 保持兼容 | 完整 `backend/tests/`；包含旧训练/预测、结构化数据、LLM 与远程管理回归用例 | 196 项通过、6 项环境门控跳过，无失败 | 兼容性、回归 |

**已知限制**：未在真实 NVIDIA GPU 上执行训练，因此实际 GPU 驱动、显存占用和硬件调度未验证；若手动指定的槽位在排队期间离线，任务会继续等待该槽位上线。Redis/S3 集成需配置相应外部服务后执行。

**下次测试建议**：在至少一个真实 GPU 节点上验证 GPU-only 重试、最低显存筛选和跨槽恢复；配置 `MTP_TEST_REDIS`、`MTP_TEST_S3` 后运行被跳过的集成用例。

**重测触发条件**：修改任务状态机、worker 领取/重试分配、任务详情 API 或训练页面资源/图表交互时，重新运行相关用例和完整 `backend/tests/`，并更新本报告基准点。

## 本次变更测试结果：数据集选择与 CSV 上传修复（2026-10-25）

**变更范围**：修正前端把 `/api/datasets` 响应对象误当数组的问题；空列表时清除旧预览并禁用选择；移除示例数据快捷导入；修复 CSV 数值列上传时 NumPy 整数无法 JSON 序列化的问题。

**测试环境**：Docker Compose；pytest 容器 Python 3.12.15。完整测试运行在当前工作区，因此也覆盖了其他尚未提交的 LLM 与任务重试改动。

**测试执行结果**：
- `docker compose up --build -d`：通过；trainer、worker、frontend 重建并启动，SeaweedFS healthy。
- `docker compose run --rm --no-deps -v ./backend:/app -v ./scripts:/scripts:ro -w /app --entrypoint python trainer -m pytest tests/test_api.py::test_upload_csv_with_integer_columns_is_serializable_and_listed -v`：1 通过。
- `docker compose run --rm --no-deps -v ./backend:/app -v ./scripts:/scripts:ro -w /app --entrypoint python trainer -m pytest tests/test_api.py::test_remote_ssh_settings_redacts_redis_read_failure -v`：1 通过。修正了该既有测试的 monkeypatch 目标，使其指向实际定义状态读取函数的 `app.remote_workers` 模块。
- `docker compose run --rm --no-deps -v ./backend:/app -v ./scripts:/scripts:ro -w /app --entrypoint python trainer -m pytest tests/ -v`：197 项收集，190 通过、6 跳过、1 失败。
- 完整测试的失败是工作区未提交的 `tests/test_llm_trainer.py::test_hub_load_is_restricted_to_valid_ids_and_disables_remote_code`：测试期望 `train`，当前代码按 `max_samples=5000` 传入 `train[:5000]`。这项失败与本次数据集选择和 CSV 上传修复无关。
- 6 项跳过：3 个 Redis 集成、2 个 S3 集成、1 个 Supervisor 集成；需要对应的外部服务或环境配置。
- Node 内联脚本冒烟：通过；验证空列表提示及禁用状态、`{datasets: [...]}` 正确读取后选择器启用并载入选项、示例按钮加载器已移除。
- 前端内联 JavaScript `node --check`：通过；`git diff --check`：通过。
- 保留的弃用警告：SQLAlchemy `declarative_base()` 与 Pandas `xlrd` 引擎。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| API 返回的数据集列表可在页面显示并选择 | 前端 Node 内联脚本冒烟；浏览器页面人工核对 | `{datasets: [...]}` 响应被解析为列表，选择器启用并显示数据集 | 正常、接口格式兼容 |
| 没有数据集时显示上传提示，清空旧预览并禁用选择；不显示 demo 快捷导入 | 前端 Node 内联脚本冒烟；内联脚本语法检查 | 空集合时选择器禁用、预览清空；`loadSamples` 和 `sampleBox` 不再存在 | 空集合、回归 |
| 整数 CSV 上传后可序列化，并能在列表和预览中读取 | API：`test_upload_csv_with_integer_columns_is_serializable_and_listed` | 上传含整数特征列的 CSV 返回 200，内存大小为原生整数，列表与预览内容正确 | 正常、异常回归、API 副作用 |

**已知问题**：当前工作区完整测试仍有上述 1 项 LLM split 断言失败；数据集 CSV 回归测试和前端冒烟均通过。未修改其他未提交的 LLM 工作区文件。

**Git基准点**：36103a0（Fix dataset selection and CSV upload）。

## 本次变更测试结果：KMeans 聚类与单序列时间序列预测（2026-10-25）

**变更范围**：增加无目标列的 CPU KMeans 聚类任务，以及按时间列排序、使用数值目标和历史窗口的单序列时间序列预测任务；补齐任务参数校验、训练指标/模型产物、checkpoint 恢复、预测 API 和训练页面配置。

**测试环境**：Docker Compose；pytest 容器 Python 3.12.15；本机无可用 NVIDIA GPU。新任务 API 端到端用例使用旧扁平数据集布局，现有新结构数据集训练回归用例和数据格式回归用例也包含在完整套件中。测试镜像基于当时工作区构建，因此工作区原有未提交的 `backend/app/storage.py`、`backend/tests/test_storage.py` 和 `backend/static/index.html` 改动也参与了验证，但未包含在本次功能提交。

**测试执行结果**：
- `docker compose up --build -d`：通过；镜像构建完成，服务启动，SeaweedFS healthy。
- 完整测试：171 项收集，165 通过、6 跳过、0 失败、0 错误（55.04 秒）。
- 6 项跳过：3 个 Redis 协调集成、1 个 Redis Supervisor 集成、2 个 S3 集成；需要分别配置 `MTP_TEST_REDIS` / `MTP_TEST_S3`。
- 两条既有弃用警告：SQLAlchemy `declarative_base()` 和 Pandas `xlrd` 引擎；均非失败。
- 前端内联 JavaScript `node --check`：通过；`git diff --check`：通过。
- 首轮定向测试发现 KMeans 分支错误读取神经网络专用参数，已调整任务分支初始化顺序并补齐默认训练参数；首轮完整测试另有 1 项时序异常输入用例在测试数据构造阶段因浮点列赋字符串失败，已先转为字符串列再断言业务校验。修复后重新构建并运行完整套件，最终 165 通过、0 失败。

**完整测试命令**：
```bash
docker compose up --build -d
docker compose run --rm --no-deps \
  -v "$PWD/backend/tests:/app/backend/tests:ro" \
  -v "$PWD/scripts:/app/scripts:ro" \
  trainer python -m pytest backend/tests/ -v
```

**前端语法检查命令**：
```bash
set -o pipefail
docker compose run --rm --no-deps trainer python -c 'import pathlib,sys; html=pathlib.Path("/app/static/index.html").read_text(); script=html.rsplit("<script", 1)[1].split(">", 1)[1].split("</script>", 1)[0]; sys.stdout.write(script)' | node --check -
```

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 可创建不需要目标列的 KMeans 任务，产出指标、模型，并通过 API 稳定预测簇 | API：`test_train_e2e_kmeans_without_target_column`；训练器：`test_kmeans_run_produces_clusters_checkpoint_and_stable_predictions` | 旧扁平 CSV 特征数据、3 个簇；任务完成，簇指标与 `best.pt` 存在，模型预测簇编号有效且重复预测稳定 | 正常、训练产物、预测一致性、数据格式兼容 |
| KMeans 参数边界、非法资源配置、簇数大于不同样本数时会明确拒绝 | API：`test_train_rejects_invalid_new_task_parameters`；训练器：`test_kmeans_rejects_more_clusters_than_distinct_rows` | 簇数小于 2、要求 GPU、不同样本数不足；请求/训练失败并给出明确校验错误 | 边界、异常、资源约束 |
| KMeans checkpoint 可暂停后恢复，迭代历史连续 | 训练器：`test_kmeans_pause_resume_keeps_iteration_history` | 固定数据与初始参数，暂停后从 checkpoint 恢复；迭代历史连续且任务产物可继续读取 | 状态恢复、兼容、回归 |
| 单序列预测按时间排序建立窗口，训练完成后接受恰好 lookback 长度的历史并返回未来数值 | API：`test_train_e2e_single_series_forecasting_and_history_validation`；训练器：`test_timeseries_run_uses_ordered_windows_and_predicts_future_value` | 带时间戳与数值目标的序列；乱序输入按时间重排，训练产生 RMSE/R² 和 `best.pt`，历史窗口预测返回有限数值 | 正常、边界、训练链路、预测一致性 |
| 无效时间、重复时间点、数据太短、非数值目标、错误时间列或历史长度应拒绝 | API：`test_train_rejects_invalid_new_task_parameters`、`test_train_e2e_single_series_forecasting_and_history_validation`；训练器：`test_timeseries_rejects_invalid_time_values_and_short_history` | 非法/重复时间戳、短序列、非数值目标、缺失或与目标相同的时间列、不匹配的历史窗口；返回参数错误或明确训练错误 | 边界、异常、非法输入 |
| 旧任务行为及两套数据集布局兼容，整体回归通过 | API：现有 `test_train_e2e_classification`、`test_train_e2e_regression`、`test_structured_layout_dataset_trains`；完整 `backend/tests/` | 回归/分类仍可训练；新结构数据集仍可通过通用训练读取；完整套件通过 | 兼容、回归 |

**已知问题与限制**：
- 时间序列目前只支持单一数值序列，时间列用于排序；预测单个配置预测步长对应的数值，不输出多变量或整段未来序列。
- KMeans 任务固定在 CPU 运行；本机无 NVIDIA GPU，因此未执行真实 GPU 训练。该限制符合本功能设计。
- Redis/S3 集成需要外部服务配置，本轮按门控跳过；已有弃用警告尚未处理。

**下次测试建议**：若后续增加多变量/多步时间序列、GPU KMeans 或分布式执行支持，应补充对应数据结构、资源调度及真实硬件/服务集成验证。Redis/S3 集成环境可用时运行被跳过的 6 项用例。

**Git基准点**：cd6c986（Add clustering and time series training tasks）。

## 本次变更测试结果：平台统一 SSH 认证与密钥格式兼容（2026-10-25）

**变更范围**：远程 SSH 增加平台统一配置的密钥/密码认证，OpenSSH 自动识别私钥格式；支持加密私钥口令。密码和私钥口令通过 askpass 环境传递，不进入 SSH 命令行或输出；远程凭证只注入 trainer，不注入 worker。现有服务器记录仍默认走密钥认证，远程 API、严格 known_hosts 校验和服务器生命周期行为保持兼容。

**测试环境**：Docker Compose；pytest 容器 Python 3.12.15；本机无可用 NVIDIA GPU；未配置真实远程服务器或 SSH 凭证。

**测试执行结果**：
- `docker compose up --build -d`：通过；trainer、worker、frontend 重建并启动，SeaweedFS 健康。
- 完整测试：163 项收集，157 通过、6 跳过、0 失败、0 错误（51.89 秒）。
- 远程管理定向测试：26 项通过（0.39 秒），在 Python 3.12.15 容器运行。
- pytest 在 Python 3.12.15 容器中只读挂载 `backend/tests/` 与 `scripts/`；工作区原有未提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 也参与全量测试，但没有纳入 `79175c4` 提交。测试结束后，工作区出现 `backend/app/jobs.py`、`backend/app/routers/jobs.py`、`backend/app/routers/schemas.py`、`backend/app/trainer.py` 和 `backend/static/index.html` 的其他未提交改动；这些不属于本次提交，也未纳入本次测试镜像。
- 跳过项：3 个 Redis 协调集成、1 个 Redis Supervisor 集成、2 个 S3 集成；分别需要 `MTP_TEST_REDIS` / `MTP_TEST_S3` 配置。
- 两条非失败警告：SQLAlchemy `declarative_base()` 弃用提示；Pandas 对 xlrd 引擎的未来弃用提示。
- GPU/CPU 两种远程 Compose 配置解析通过；密码环境检查确认 SSH 密码只进入 trainer 环境，不进入 worker 环境。
- 密码模式远程 Compose 一次性容器验证通过：平台密码在 trainer 环境可见、私钥占位不作为密钥文件、只读 `known_hosts` 正常挂载；使用合成密码，没有发起真实 SSH 连接。
- `git diff --check` 通过；OpenSSH `ssh-keygen` 解析测试覆盖 Ed25519、ECDSA、RSA OpenSSH 与 RSA PEM 私钥。
- 首轮本机定向测试的 mock 曾递归调用被替换的 `subprocess.run`；修正后在 Python 3.12 容器定向测试 26 项通过。首轮完整测试未把 `scripts/` 挂载到 `/scripts`，导致 13 项节点脚本用例找不到脚本；改正挂载路径后完整套件通过。这两项均为测试调用问题，不是产品失败。

**定向和完整测试命令**：
```bash
# 项目根目录：完整测试
docker compose run --rm --no-deps \
  -v ./backend/tests:/app/tests:ro \
  -v ./scripts:/scripts:ro \
  trainer python -m pytest /app/tests -v

# 项目根目录：远程 SSH 定向测试
docker compose run --rm --no-deps \
  -v ./backend/tests:/app/tests:ro \
  -v ./scripts:/scripts:ro \
  trainer python -m pytest /app/tests/test_remote_workers.py -v

# 项目根目录：GPU 与 CPU 的远程管理 Compose 解析
WORKER_SSH_KNOWN_HOSTS_HOST_PATH=/etc/hosts WORKER_SSH_KEY_HOST_PATH=/dev/null \
  docker compose -f docker-compose.yml -f docker-compose.remote.yml config --services
WORKER_SSH_KNOWN_HOSTS_HOST_PATH=/etc/hosts WORKER_SSH_KEY_HOST_PATH=/dev/null \
  docker compose -f docker-compose.cpu.yml -f docker-compose.remote.yml config --services

# 项目根目录：密码模式容器冒烟（合成密码，不连接目标机）
WORKER_SSH_AUTH_METHOD=password WORKER_SSH_PASSWORD=synthetic-check-secret \
  WORKER_SSH_KNOWN_HOSTS_HOST_PATH=/etc/hosts \
  docker compose -f docker-compose.yml -f docker-compose.remote.yml \
  run --rm --no-deps trainer python -c 'import os; print("auth=" + os.environ.get("WORKER_SSH_AUTH_METHOD", "") + " password_present=" + str(bool(os.environ.get("WORKER_SSH_PASSWORD"))) + " key_file=" + str(os.path.isfile("/run/remote-worker/ssh_key")) + " known_hosts=" + str(os.path.isfile("/run/remote-worker/known_hosts")))'
```

**验收标准—测试用例映射**：

| 验收标准 | 测试层级与用例 | 输入与预期 | 场景 |
|---|---|---|---|
| 平台可统一选择 SSH 密钥或密码认证，密码与密钥口令不写入命令行或日志，密码不发给 worker 容器 | 执行器：`test_remote_configuration_supports_platform_password_without_key_file`、`test_remote_configuration_requires_password_and_valid_auth_method`、`test_remote_configuration_rejects_newline_in_platform_password`、`test_run_remote_action_uses_password_from_platform_askpass`、`test_run_remote_action_redacts_password_from_ssh_error`、`test_run_remote_action_uses_key_passphrase_without_exposing_it`；Compose 环境检查 | 密码模式不需要私钥；askpass 读取平台环境；命令参数不含凭证、输出会脱敏；worker 环境不含密码 | 正常、边界、异常、权限与安全 |
| 常见私钥格式由 OpenSSH 识别，现有配置默认仍使用私钥认证 | 执行器：`test_openssh_reads_supported_private_key_formats`、`test_remote_configuration_requires_key_permissions_and_registry`、`test_run_remote_action_pins_host_key_and_keeps_secrets_out_of_argv` | Ed25519、ECDSA、RSA OpenSSH/PEM 可解析；保留固定 known_hosts 严格校验；不自动启用弱算法 | 正常、兼容、安全 |
| 页面登记服务器后由平台 SSH 执行脚本，目标机不接收项目源码 | API/脚本：`test_remote_server_api_installs_and_manages_with_nonsecret_records`、`test_render_remote_script_quotes_values_and_does_not_transfer_repository`、`test_run_remote_action_pins_host_key_and_keeps_secrets_out_of_argv` | 授权请求创建异步安装；脚本经 stdin 发送、镜像由目标机拉取；记录不含令牌/私钥；SSH 严格校验已登记主机密钥 | 正常、权限与安全、兼容 |
| 缺配置、错误令牌、非法目标或注入输入会拒绝 SSH 操作 | API/执行器：`test_remote_management_requires_an_enabled_admin_token`、`test_remote_server_api_rejects_shell_injection_before_ssh`、`test_render_remote_script_rejects_shell_and_path_injection`、`test_remote_configuration_requires_key_permissions_and_registry`、`test_remote_configuration_requires_shared_queue_and_object_storage` | 远程功能默认关闭；错误令牌 401、关闭时 503、格式非法 422；共享 Redis/S3、私钥权限或镜像引用不满足时配置检查失败 | 边界、异常、权限与安全 |
| 安装后可启动、停止、重启并重试部署；操作状态可轮询 | API/执行器：`test_remote_server_api_installs_and_manages_with_nonsecret_records`、`test_execute_remote_operation_persists_success_and_failure`、`test_remote_bootstrap_script_lifecycle` | install/start/stop/restart 返回可轮询状态；错误记录失败原因；停止/重启使用 45 秒优雅等待 | 正常、异常、状态冲突、回归 |
| 目标 worker 自动报告 CPU/GPU，资源槽由用户手动选择 | Shell/worker/API：`test_remote_bootstrap_script_installs_cpu_and_gpu_workers`、`test_detect_capabilities_reports_cpu_and_stable_server_identity`、`test_managed_worker_selection_api` | CPU/GPU 镜像配置正确；槽位首次默认待选；用户可手动启用 | 正常、边界、兼容；真实 GPU 受硬件限制 |
| 远程配置可持久读取，既有训练、数据集和存储行为不回归 | `test_remote_server_and_operation_metadata_roundtrip` 与完整 `backend/tests/` | 远程服务器/操作元数据往返通过；完整测试 157 通过、6 跳过、0 失败 | 正常、兼容、回归 |

**未验证内容和限制**：
- 未在真实外部服务器上运行 SSH 安装及生命周期操作；目标机的 Docker 权限、主机指纹、镜像拉取、Redis/S3 连通仍需在目标环境验收。
- 平台密码是全局配置并用于所有远程账号；需要每台服务器使用不同登录密码时，当前版本不支持分别设置。旧服务器若只支持 RSA/SHA-1 等弱 SSH 算法，也不会自动降低客户端安全设置。
- 本机无 NVIDIA GPU，未验证真实 GPU 型号、显存与训练执行；CPU 路径已覆盖。
- Redis/S3 集成按门控跳过，无法在本轮证明外部 Redis 中的选择持久化及对象存储连通。
- 私有镜像仓库需要先让目标机具备镜像拉取凭据；目标机不需要检出源码或手动运行项目脚本。
- 现有应用没有通用 API 身份认证；本次远程管理路由单独使用令牌，页面与其他 API 仍应部署在可信网络或由反向代理保护。

## 本次变更测试结果：页面统一 SSH 凭证管理（2026-10-25）

**变更范围**：远程服务器管理对话框可配置平台统一 SSH 私钥或密码及可选私钥口令。页面只显示凭证来源、认证方式和状态；服务端使用 Fernet 加密后保存到共享 Redis。执行 SSH 时私钥通过权限为 `600` 的临时文件提供，密码/口令通过 askpass 环境传递，操作后删除临时私钥。清除页面凭证后继续使用既有 `.env` 配置。

**测试环境**：Docker Compose；trainer 测试容器 Python 3.12.15；没有配置真实远程服务器或真实凭证。

**测试执行结果**：
- `docker compose up --build -d`：通过，trainer、worker 与 frontend 重建并启动。
- 完整套件：184 项收集，178 通过、6 跳过、0 失败、0 错误（54.29 秒）。
- 定向测试：`test_api.py` 中远程 SSH 设置用例 10 通过；`test_remote_workers.py` 28 通过；协调存储远程凭证用例 2 通过。
- 前端内联 JavaScript `node --check -`：通过；GPU 与 CPU 两种远程 Compose 配置解析：通过。
- 完整套件的 6 项跳过为 3 个 Redis 协调集成、1 个 Redis Supervisor 集成和 2 个 S3 集成；需要配置 `MTP_TEST_REDIS` / `MTP_TEST_S3`。两条 SQLAlchemy、Pandas 弃用警告，均非失败。
- 页面 API 的 5 个用例（参数化后 10 项）已纳入测试：覆盖密文持久化与脱敏响应、密钥/密码校验、错误令牌、缺少 Redis/加密密钥及 Redis 异常脱敏。该测试代码随 `ade30cc` 提交进入当前历史；数据集修复也在该提交中。

**完整测试命令**：
```bash
docker compose run --rm --no-deps \
  -v ./backend/tests:/app/tests:ro \
  -v ./scripts:/scripts:ro \
  trainer python -m pytest /app/tests -v
```

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 页面可保存平台统一密码或不同格式的私钥；凭证明文不回显，Redis 只保存密文 | API：`test_remote_ssh_settings_encrypt_credentials_and_never_return_secrets`、`test_remote_ssh_settings_save_private_key_without_echo`；模型：`test_openssh_reads_supported_private_key_formats` | Fernet 密钥、密码或完整 OpenSSH/PEM 私钥；状态响应仅含来源/方式/配置状态，Redis 值不可直接读出凭证 | 正常、兼容、敏感数据保护 |
| 页面密钥和密码可供远程 SSH 使用，临时密钥严格限权并清理，askpass 错误输出会脱敏 | 执行器：`test_run_remote_action_uses_page_password_and_redacts_it`、`test_run_remote_action_materializes_page_private_key_temporarily`；既有 `.env` 凭证测试在完整套件中回归 | 合成凭证；密码不进入命令参数或错误信息，私钥临时文件为 `0600` 且调用后不存在 | 正常、权限与安全、资源清理、兼容 |
| 未授权、无 Redis、无有效加密密钥、无效凭证或 Redis 故障不能泄漏秘密或写入明文 | API：`test_remote_ssh_settings_require_redis_and_encryption_key`、`test_remote_ssh_settings_reject_invalid_credentials`、`test_remote_ssh_settings_redacts_redis_read_failure`，以及错误令牌断言 | 非法/缺失输入返回脱敏 422；后端能力缺失或 Redis 故障返回脱敏 503；未配置管理令牌返回 401 | 边界、异常、权限与安全 |
| Redis 凭证存储使用独立 key，支持读取和清除；页面清除后回退到 `.env` | 协调层：`test_embedded_remote_ssh_credentials_store_opaque_ciphertext`、`test_redis_remote_ssh_credentials_use_dedicated_key`；API 清除断言 | 存取、删除 `mtp:remote:ssh_credentials`；清除后状态来源为 `env` | 正常、回归、兼容 |
| 现有远程管理、训练、数据集和存储 API 不回归 | 完整 `backend/tests/`；GPU/CPU Compose 配置解析；前端内联脚本语法检查 | 184 项收集，178 通过、6 跳过、0 失败；两种 Compose 配置可解析，页面脚本可解析 | 回归、兼容 |

**未验证内容和限制**：
- 未对真实服务器发起 SSH 连接；当前执行器测试通过 fake SSH 覆盖认证参数、临时文件和输出脱敏。实际目标账号、主机指纹、网络与目标机 Docker 权限需部署环境验收。
- 外部 Redis/S3 集成按环境变量门控跳过；本次页面持久化逻辑由存储单元测试和 API 测试验证，未连接生产 Redis。
- 页面凭证是平台全局单份配置，会用于所有远程服务器账号。更换 `WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY` 后，旧密文无法解密，需恢复原密钥或清除并重新配置。
- 本机无 NVIDIA GPU；本次修改不改变 GPU 训练逻辑。

## 上一轮补充测试：Excel 与数据集管理（2026-10-25）

**变更范围**：训练台和数据集管理页统一视觉与响应式样式；数据集导入、处理、双布局兼容和压缩包安全；Excel `.xlsx/.xls` 运行依赖与接口回归；本次测试同时覆盖工作区中既有的计算节点管理改动。

**测试环境**：Docker Compose 重建成功；pytest 容器为 Python 3.12.15。

**测试执行结果**：
- `docker compose up --build -d`：通过；trainer、worker、frontend 重建并启动，SeaweedFS healthy。
- `docker run --rm -e USE_DATABASE=false -v "$PWD/backend:/app" -v "$PWD/scripts:/scripts:ro" -w /app --entrypoint python emdash-hot-boxes-clean-y9u6p-trainer:latest -m pytest tests/ -v`：133 项收集，127 通过、6 跳过、0 失败、0 错误（51.24 秒）。
- `docker run --rm -e USE_DATABASE=false -v "$PWD/backend:/app" -v "$PWD/scripts:/scripts:ro" -w /app --entrypoint python emdash-hot-boxes-clean-y9u6p-trainer:latest -m pytest tests/test_excel_import.py -q`：4 通过、0 失败。
- 本次 pytest 使用完整工作区挂载；工作区原有未提交改动也参与了测试，但没有纳入本次提交。
- 跳过项：3 个 Redis 协调集成、1 个 Redis Supervisor 集成、2 个 S3 集成，分别需要 `MTP_TEST_REDIS` / `MTP_TEST_S3` 配置。
- 两条非失败警告：既有 SQLAlchemy `declarative_base()` 弃用提示；当前 Pandas 对 xlrd 引擎的未来弃用提示。
- Python 3.12 直接调用处理路由：旧扁平数据集依次执行选列和去重后，第二次仍从原始 CSV 读取；通过，结果为 2 行、`feature` 与 `target` 两列。
- Excel API 回归：`.xlsx` 和 `.xls` 导入均保留行数、列名、类型元数据及预览值；两种损坏 Excel 均返回 400 且不创建数据集。
- Python 3.12 导入器冒烟：JSON 行列元数据、嵌套 TAR.GZ 导入、ZIP 路径穿越拒绝均通过。
- 运行中 HTTP API 冒烟：JSON 上传、详情字段/行数、选列处理与元数据更新均通过；测试数据集 `ds-20261025-093340-add432` 已通过 API 删除。
- Safari 桌面检查：统一导航、数据集卡片和上传空闲状态正常；窄屏断点（720px、480px）已核对 CSS。未执行真实窄屏视口截图验证。

**未验证内容和限制**：
- 未配置 `MTP_TEST_REDIS` / `MTP_TEST_S3`，相应 4 个 Redis 和 2 个 S3 集成用例按门控跳过。
- 未执行真实窄屏视口截图；本次只核对 Safari 桌面布局与 720px、480px CSS 断点。
- 未在 GPU 硬件上验证计算槽实际接卡行为。

**验收标准—测试映射**：

| 验收标准 | 测试/验证 | 结果 |
|---|---|---|
| 页面共享风格、导航与桌面布局 | Safari 桌面页面检查；响应式断点 CSS 检查 | 通过；窄屏实际截图未验证 |
| Excel 导入兼容及错误输入不落库 | `tests/test_excel_import.py`：xlsx、xls、损坏文件参数化 API 用例 | 通过（4/4） |
| 导入后行数、字段和类型元数据完整 | JSON 与 Excel 上传详情断言；直接导入器冒烟 | 通过 |
| JSON、TAR.GZ 可导入且危险压缩包路径拒绝 | API ZIP/TAR.GZ 回归及导入器冒烟 | 通过 |
| 旧扁平布局重复处理始终从原始文件开始 | Python 3.12 直接调用处理路由 | 通过 |
| 新布局处理与 API UI 流程 | `tests/test_api.py` 新旧布局处理和元数据断言 | 通过 |
| 计算节点与 worker 脚本回归 | 全量 pytest 中 API、协调、worker 与脚本测试 | 通过；Redis 外部集成按门控跳过 |
| 全量 API 与 Excel 引擎兼容 | 完整 pytest 套件 | 通过（127 通过、6 跳过） |

## 历史测试结果：多机多卡训练架构（2026-10-25）

**变更范围**：多机多卡无状态训练架构（四平面分离）+ 正式 pytest 测试套件 + 状态平面替换为 SeaweedFS + 前后端容器拆分 + **P0 框架治理重构**

- 三层架构重构（`storage.py` / `coordination.py` / `worker.py` / `jobs.py` / `jobstore.py` / `trainer.py` / `config.py`）：控制（FastAPI）/ 协调（Redis 队列+租约+心跳）/ 状态（S3 对象存储 + 本地文件，job.json 权威）/ 计算（无状态 Worker 一卡一进程）四平面
- 前端（`static/index.html`）：计算槽面板（机器→卡级状态）、暂停/恢复按钮、资源提示表单
- 状态平面 MinIO → **SeaweedFS**（S3 协议兼容，可替换为阿里云 OSS / 腾讯云 COS / AWS S3）；`MINIO_*` 环境变量全部替换为 `S3_*`
- 迁移脚本 `scripts/migrate_to_object_storage.py`（遗留数据迁入对象存储，幂等）
- 正式 pytest 套件 `backend/tests/`（pytest 已纳入 requirements.txt）
- 前后端按语言拆分容器：`frontend/`（Node 零依赖静态服务 + /api 同源反代），trainer 仅内网
- **P0 框架治理**：DB 配置收敛到 `config.py`（单源）；`init_db.sh` 按 `USE_DATABASE` 跳过 90s 等待；`main.py` 864→75 行拆为 `app/routers/`（datasets/jobs/schemas/common）+ `app/state.py` 单例；watchdog 定期清理崩溃残留的 worker 注册（`WORKER_STALE_CLEANUP_AGE`）

**测试执行结果（正式 pytest 套件，嵌入式模式）**：
- 总测试用例：99 个（93 通过 + 6 跳过）
- 通过：93 个 (93.9%)
- 失败：0 个
- 错误：0 个
- 跳过：6 个（3 个需 `MTP_TEST_REDIS` + 2 个需 `MTP_TEST_S3` + 1 个 Redis 模式下的嵌入式回退断言）

**Redis 模式补充验证**（`MTP_TEST_REDIS=redis://localhost:6399/0`，redis:7-alpine 容器）：23 通过 / 1 跳过，包括：
- 协调面语义一致性（队列 FIFO/租约 fencing/信号/注册）
- Supervisor 多进程 E2E（双槽并行 AC9、SIGKILL 子进程自动重启 AC10/AC11、任务重派后完成且 epochs 连续）
- `--probe-gpu` 探测子进程语义（无 GPU 返回非 0）
- Worker 注册往返（嵌套 gpu dict/进度字段类型还原）
- 幽灵槽清理（HB 消失 + last_seen 超宽限清除；宽限期内保护）

**S3 后端补充验证**（`MTP_TEST_S3=localhost:9000`，运行中的 SeaweedFS 容器）：22 通过，包括：
- S3ArtifactStore 写/读/存在性/递归列表/删除往返
- 数据集同步链路（入库 CSV → sync_dataset_to_store → worker 拉取）
- 此前另以 minio 客户端直连 SeaweedFS 实测建桶/上传/下载/stat/2MB 大对象全部通过

**关键模块测试（嵌入式模式）**：
- 存储层（双格式布局兼容/checkpoint 版本化/覆盖写语义/jobstore 回滚/日志/S3 门控）：17+2S3
- 协调层（队列/租约/信号/worker 注册/嵌入式回退/幽灵槽清理/旧数据回退兼容）：13
- 训练器层（完整链路/续训等价/暂停恢复/取消/可复现性/损坏与架构校验）：8
- Worker 层（执行/跨槽接手/租约丢失让位/资源提示/失败分类重试/checkpoint 回退/协调面瞬断幸存）：20
- 任务管理层（状态机/watchdog 重派/防丢失/删除保护/api_view 契约）：10
- API 层（训练/暂停恢复/取消/删除/预测一致性/新结构数据集/静态路径不被参数路由遮蔽）：12
- 配置层（DB 单源/引擎惰性/DB_AVAILABLE 一致性）：3
- Supervisor 层（槽位规划/探测语义）：5/5

**验收标准映射**：
- AC1（单机零依赖）→ test_build_coordination_embedded、test_sync_dataset_local_noop、全 API 测试
- AC2（checkpoint 唯一权威 + 版本化回退）→ test_resume_equals_full_run、test_checkpoint_corrupt_falls_back_to_older、test_prune_checkpoints_keeps_latest_n
- AC3（Redis/MinIO 基础设施）→ Redis 语义一致性测试、S3 后端往返测试
- AC4（暂停/断点续训）→ test_pause_resume_state_machine、test_pause_at_epoch_boundary_via_callback
- AC5（单卡故障不影响完成）→ test_pause_then_other_worker_resumes、test_watchdog_redispatches_expired_running
- AC6（无状态 worker）→ test_worker_completes_job、test_completed_job_not_reprocessed
- AC7（机器/卡不可用自动接手）→ test_lease_lost_graceful_pause、watchdog 重派与补投测试
- AC8（任务分发匹配到卡级）→ test_require_gpu_job_not_claimed_by_cpu_worker、test_min_vram_job_skipped_by_smaller_gpu
- AC9（多卡并行）→ test_supervisor_parallel_and_child_restart（Redis 版）
- AC10（kill 接手）→ 同上（SIGKILL → 重启 → 重派 → completed，epochs 1..N 连续）
- AC11（卡恢复检测）→ test_probe_gpu_exit_code、test_slot_planning_*
- AC12（环境性错误自动重试上限可配）→ test_env_error_auto_retries_then_completes、test_env_error_exhausts_retry_then_failed、test_classify_retryable
- ML 特有：训练链路指标/产物、同 seed 可复现、预测一致性（含缺失字段中位数/众数填充）、双布局数据兼容、预处理一致性（续训后预测可用）

**测试套件发现并修复的缺陷**（先复现失败测试再修复）：
| # | 缺陷 | 复现 | 修复 |
|---|------|------|------|
| 1 | 暂停时未记录本槽执行分段，跨槽续训只显示完成槽的 1 段（UI"跨槽续训 N 段"数据缺失） | `test_pause_then_other_worker_resumes` 断言 segments==2 失败 | `worker.py` JobPaused 分支记录本槽分段（on_checkpoint 捕获暂停 epoch） |
| 2 | Redis 队列 LPUSH+BRPOP 使 snapshot 与出队顺序相反（队列契约不一致） | Redis 语义一致性测试 snapshot 断言失败 | 统一 RPUSH+BLPOP（FIFO，snapshot=入队序） |
| 3 | worker 领取循环在 Redis 瞬断时（BLPOP socket 超时边界竞态）进程崩溃并被 supervisor 反复重启 | 容器运行观察 + `test_worker_survives_queue_pop_error` | `run_forever` 逐轮 try/except 退避重试；`RedisJobQueue.pop` 把阻塞窗口超时归一为"无任务" |
| 4 | worker 注册信息经 Redis 存储时嵌套 dict/数值被 str() 扁平化，`/api/workers` 读回后 `gpu.get` 崩溃（500） | 容器实测 500 + Redis 注册往返测试 | `jsonable/unjsonable` 对称序列化（JSON + 旧 repr 数据回退兼容） |
| 5 | S3 模式下数据集入库未同步对象存储，worker 领取后"数据集未同步到对象存储"直接失败 | 容器全链路冒烟（真实 SeaweedFS） | `main.py` 上传/示例导入路径调用 `sync_dataset_to_store`（尽力而为）；数据集删除同步清理 S3 副本 |
| 6 | compose worker 服务缺少 command 覆盖，容器内跑的是 API 而非 supervisor（计算槽永远注册不上） | 容器实测：worker 容器日志为 uvicorn 启动 | 两个 compose 文件 worker 服务显式 `command: ["python", "-m", "app.worker"]` |
| 7 | 任务删除时 DB 镜像只删父表 training_jobs，未删子表 job_logs，外键约束失败被吞掉，镜像行永久残留 | 容器实测：API 删除后 training_jobs/job_logs 行数不变 | `_mirror_delete` 先删子表日志再删父表任务行 |
| 8 | 数据集删除的 DB 镜像清理是 pass 占位（datasets 行永久残留） | 容器实测：API 删除后 datasets 行数不变 | `delete_dataset` 实现镜像删除（被任务引用的 FK 失败按设计容忍） |
| 9 | `GET /api/datasets/tags` 与 `/api/datasets/import-history` 被 `{ds_id}` 参数路由遮蔽，永久 404（存量死端点，重构冒烟时暴露） | `GET /api/datasets/tags` 返回 404"数据集不存在" | 静态路径路由移到参数路由之前 + 两个回归测试 |

**测试命令**：
```bash
cd backend && python3 -m pytest tests/ -v                     # 嵌入式模式：93 通过 / 6 跳过
MTP_TEST_REDIS=redis://localhost:6399/0 python3 -m pytest tests/test_coordination.py tests/test_supervisor.py -q  # 23/1
MTP_TEST_S3=localhost:9000 python3 -m pytest tests/test_storage.py -q  # 22/22
```

## 容器化部署验证（2026-10-25，SeaweedFS 版）

**验证环境**：本分支 `2c9b101`；Apple Silicon 宿主机（无 NVIDIA GPU）→ 按 README 规范使用 CPU 版 compose（GPU 版 worker 在本机因 `gpus: all` 无 GPU vendor 无法启动，属预期）；状态平面为 SeaweedFS（S3）。

**镜像与依赖源**：
- MinIO 已从 Docker Hub 下架（2025，AIStor 品牌迁移），AIStor 镜像无商业许可证时拒绝全部 S3 操作（实测日志："No valid license found... All S3 operations are denied"）→ **替换为 SeaweedFS**（`chrislusf/seaweedfs`，S3 协议兼容，可替换为阿里云 OSS / 腾讯云 COS / AWS S3）
- SeaweedFS 镜像可正常拉取；minio 客户端（通用 S3 协议）与 SeaweedFS 全操作兼容（实测通过）
- 镜像源经 `.env` 可配置（`SEAWEEDFS_IMAGE`，国内网络可换国内镜像源）

**验证结果**：
- ✅ 镜像构建成功：trainer / worker 两镜像（容器内 Python 3.12 + torch 2.14.1+cu130）
- ✅ 五服务启动健康：seaweedfs（healthy，wget 健康检查；`-ip=0.0.0.0` 使 S3 监听全部接口）、trainer、worker（supervisor 拉起 2 个 CPU 槽子进程）、frontend（healthy）、Redis/MySQL 走宿主机外部容器（localhost 网关映射）
- ✅ S3 状态平面：宿主 9000 → 容器 8333（S3 API）、9001 → 8888（控制台）
- ✅ 计算槽面板：`/api/workers` 返回 2 个在线 CPU 槽（idle/active，含 gpu 能力结构）
- ✅ 全链路端到端：示例数据集导入（自动同步 S3）→ 训练提交 → worker 槽领取 → completed（6 epochs，acc 0.833，segments 记录执行槽）→ 单条预测（返回"否"）→ best.pt 下载（8497 bytes）→ 任务日志 21 行
- ✅ S3 状态平面产物核验：datasets/*.csv、jobs/*/job.json、metrics.jsonl、best.pt/last.pt、preprocessor.json、summary.json、版本化 checkpoint（ckpt-e0005/e0006.pt）全部落库
- ✅ **前后端容器拆分验证**：页面由 frontend（Node 零依赖静态服务）托管（`/` 返回 index.html、`/healthz` 健康检查 healthy）；`/api/*` 经同源反代到 trainer（workers 面板/导入/训练/预测/模型下载全链路通过，无需 CORS）；trainer 仅内网
- ✅ **MySQL 镜像路径验证**（宿主机外部 MySQL 8.0.46，`model_training` 库三表）：训练任务 → training_jobs（status/device/params/summary JSON 完整）+ job_logs（13 行）镜像写入 ✓；API 删除任务 → 父表与子表日志同步删除 ✓；删除数据集 → datasets 镜像同步删除 ✓
- ✅ **P0 重构后回归冒烟**：路由拆分后各领域端点（workers/tags/import-history/operations）与训练/预测/删除全链路经 frontend 同源代理通过；`GET /api/datasets/tags` 与 `import-history` 遮蔽修复后在容器内可达
- ✅ 验证后清理：任务/数据集经 API 删除，S3 对象清空，DB 镜像同步清空（用户 07:30 的自测数据保留）；容器栈保留运行

**已知噪音**：容器日志中 `/health` 404 来自外部监控探测（应用路由为 `/api/health`），对功能无影响；宿主机系统代理（127.0.0.1:12334）会劫持本地 API 请求导致间歇 502——验证脚本已显式禁用代理（与平台代码无关）。

## 测试覆盖范围

- ✅ 正常场景：完整训练链路（提交→指标→best.pt→summary→日志→下载）、跨槽续训、回归/分类双任务
- ✅ 边界场景：空队列/空指标、首 epoch 前暂停、checkpoint 保留窗口（最近 3 个）、keep<=0 防御
- ✅ 异常场景：损坏 checkpoint 回退、数据集缺失、目标列缺失、非法超参续训、坏 metrics 行容忍
- ✅ 权限与安全：任务 ID 路径穿越拒绝、数据集 ID 非法拒绝、租约 token 校验（不可越权释放/续期）
- ✅ 远程管理安全：专用管理令牌、SSH known_hosts 严格校验、私钥权限、输入注入拒绝和敏感配置不进入 SSH argv/服务器记录
- ✅ 兼容性场景：旧扁平/新结构双布局数据集读写训练、旧前端字段契约（api_view）
- ✅ 回归场景：重复入队幂等、健康租约不误重派、删除保护、同 seed 复跑一致
- ✅ 故障注入：存储瞬断（环境性）自动重试与耗尽、执行槽 SIGKILL、租约过期重派

## 已知问题与限制

**新增限制—远程 SSH 目标环境尚未实测**：当前测试使用 fake SSH/Docker；上线前需配置平台统一的 SSH 私钥或密码、核验后的 `known_hosts`、可拉取的 worker 镜像，以及从目标机可访问的 Redis/S3 地址。密钥认证的私钥文件权限需限制为 600。私有镜像需目标机预置 registry 登录凭据。
1. **真实 GPU 行为未验证**：本机无 NVIDIA GPU（torch 2.8 CPU only，容器内 torch 2.14.1+cu130 亦无 GPU），CUDA_VISIBLE_DEVICES 隔离、驱动崩溃恢复、显存匹配的真实行为需在 GPU 服务器按以下清单复验：
   - GPU 版 compose 的 `gpus: all` 与一卡一子进程隔离
   - `--probe-gpu` 在有卡机器返回 0、掉卡恢复后 supervisor 自动重启槽位
   - require_gpu / min_vram_mb 任务由真实 GPU 卡领取
2. **MySQL 数据库路径已验证（2026-10-25）**：宿主机外部 MySQL 8.0.46（`model_training` 库）三表镜像写入/删除同步全链路通过（见容器化部署验证章节）；集群/并发场景未测
3. **SeaweedFS 单机单盘模式**：适用于中小规模状态平面；大规模部署建议 SeaweedFS 集群或阿里云 OSS/腾讯云 COS（S3_* 配置即切换）。实测观察：SeaweedFS 的 ListBuckets 未列出已创建 bucket（bucket 内对象读写均正常），不影响平台使用
4. **阿里云 OSS / 腾讯云 COS 未实测**：S3 协议兼容性基于通用 S3 客户端保证（minio SDK），真实 OSS/COS 端点需配置 `S3_ENDPOINT/S3_SECURE` 后按 S3 集成测试（MTP_TEST_S3）复验
5. 跨设备续训为语义连续（模型/优化器/RNG 完整恢复），不承诺逐位一致；同卡同 seed 复跑可复现（已实测一致）
6. 容器启动时 init_db.sh 固定等待 MySQL 90s（`USE_DATABASE=false` 时也未跳过等待），为历史行为，服务最终正常启动

## 重测触发条件

- ✅ 修改了 `backend/app/` 下的非测试代码（storage/coordination/worker/jobs/jobstore/trainer/main/config 等）
- ✅ 修改了数据库模型和操作
- ✅ 修改了数据集管理/导入/预处理逻辑
- ✅ 修改了训练器或任务管理
- ✅ 修改了 API 路由
- ✅ 修改了核心配置（影响业务逻辑的配置）
- ✅ 修改了远程 SSH 执行器、认证 helper、认证配置或目标机单文件脚本（`backend/app/remote_workers.py`、`backend/app/ssh_askpass.sh`、`backend/app/config.py`、`backend/app/remote_worker.sh`）
- ❌ 仅修改前端（`backend/static/`）、文档、数据库脚本、构建配置、测试代码时不强制重测

标准测试命令：`cd backend && python3 -m pytest tests/ -v`；Redis/S3 集成用 `MTP_TEST_REDIS` / `MTP_TEST_S3` 环境变量门控开启。

## 下次测试建议

1. 用真实 CPU/GPU 目标机验证 SSH 主机密钥校验、镜像仓库认证、Redis/S3 连接和 worker 心跳/槽位手动启用/生命周期
2. GPU 服务器人工验证清单（见"已知问题"第 1 条）
3. 配置真实 MySQL 后回归 DB 镜像路径
4. 在阿里云 OSS / 腾讯云 COS 端点跑 `MTP_TEST_S3` 集成测试
5. SeaweedFS 多机集群模式（filer replication）验证
6. 前端页面交互测试（Chart.js 曲线、计算槽面板轮询、暂停/恢复按钮链路）

## 本次变更测试结果：LLM 训练与微调（2026-10-25）

**变更范围**：新增 LLM 从零预训练、全参数 SFT、DPO、PPO/RLHF 任务；支持本地或 Hugging Face Hub 数据集、JSONL 上传、模型包下载和在线文本生成。

**Git 基准点**：`ad1246c0fa7eaa4344fba9046a03a5772a9ec79e`（Add LLM training and fine-tuning tasks）；分支 `emdash/hot-boxes-clean-y9u6p`。测试报告提交单独记录，不改变此代码基准点。

**测试环境**：Docker Compose，容器 Python 3.12.15；当前主机无可用 NVIDIA GPU。Hub 数据集/模型安全加载使用隔离测试验证，没有进行外网 Hub 下载或真实 GPU 训练。

**测试执行结果**：
- `docker compose up --build -d`：通过，服务镜像重建并启动。
- `docker compose run --rm --no-deps -v ./backend/tests:/app/tests:ro -v ./scripts:/scripts:ro trainer python -m pytest /app/tests -v`：197 项收集，191 通过、6 跳过、0 失败、0 错误；2 个弃用警告（SQLAlchemy `declarative_base()`、Pandas `xlrd`）。
- 前端内联 JavaScript 语法检查（Node `new Function`）：通过；`git diff --check`：通过。
- 6 项跳过为 Redis、S3 和 Supervisor 外部服务门控用例。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 从随机初始化模型执行因果语言预训练，保存指标、模型包并可生成 | 训练器：`test_pretraining_from_scratch_saves_metrics_bundle_and_generates`；小型本地文本样本、固定 seed | 训练 loss/perplexity 有限，生成 `best_model.zip`，同 seed 结果稳定且可生成文本 | 正常、产物、可复现性 |
| 对 Hub CausalLM 做全参数 SFT | 训练器：`test_full_parameter_sft_changes_weights_and_preserves_generation`；本地 mock 小模型与监督样本 | 模型参数发生更新，checkpoint 可加载并继续生成 | 正常、模型恢复 |
| 根据偏好对执行 DPO | 训练器：`test_dpo_uses_preference_pairs_and_emits_finite_metrics`；prompt/chosen/rejected 样本 | loss 有限并输出偏好准确率 | 正常、指标 |
| 基于标量奖励模型执行 PPO/RLHF | 训练器：`test_ppo_runs_reward_and_kl_update_and_restores_checkpoint`；小型 actor、reference 和 reward model | 产生 reward/KL 指标，保存并恢复 checkpoint | 正常、边界恢复 |
| Hub 仓库 ID 校验，禁止加载自定义远程代码 | 数据加载安全：`test_hub_load_is_restricted_to_valid_ids_and_disables_remote_code`；mock Hub 加载器 | 非法仓库 ID 被拒绝，加载器不接收 token 且 `trust_remote_code=False` | 非法输入、权限与安全 |
| LLM 任务 API 接受 Hub 参数并强制 GPU 资源提示 | API：`test_llm_api_accepts_hub_task_and_forces_single_gpu_resource_hint`；mock 队列管理器 | 提交任务保留数据源/模型参数并标记 GPU 需求 | 正常、接口契约 |
| 从训练完成任务包生成文本，且非 LLM 任务不能误用生成接口 | API：`test_generate_api_returns_text_only_for_llm_tasks`；准备模型包与任务记录 | LLM 返回生成文本；非 LLM 请求返回 400 | 正常、状态冲突 |
| Worker 端到端训练、断点与模型包持久化 | Worker/API：`test_worker_executes_llm_job_and_persists_model_and_checkpoint`；本地小型训练任务 | 任务完成，状态存储含模型包、日志、指标和 checkpoint | 训练链路、产物、回归 |
| 上传 JSONL 文本/偏好数据集 | API：`test_jsonl_upload_imports_as_dataset_table`；上传有效 JSONL 文件 | 数据集可导入并按记录列预览 | 正常、数据格式兼容 |
| 既有任务、数据格式和 API 行为保持兼容 | 完整 pytest 套件；包含既有分类/回归、KMeans、单序列预测、双存储布局与 SSH/API 测试 | 191 项通过，既有流程保持可用 | 兼容性、回归 |

**覆盖范围与限制**：CPU 小模型覆盖训练、指标、checkpoint、产物、API 和 Worker 路径；尚未验证真实 GPU 显存表现、真实公网 Hub 下载及大型模型训练。首版限定 decoder-only CausalLM、单 GPU；PPO 需要 `num_labels=1` 的奖励模型。6 个外部服务门控测试本次未执行。

**下次测试建议**：在带 NVIDIA GPU 的环境验证不同显存规模的模型/序列长度配置；用可公开访问的 Hub 数据集和模型做下载与短训练冒烟；部署前根据目标模型检查显存和数据样本格式。

## 本次变更测试结果：页面统一管理 SSH 凭证（2026-10-25）

**变更范围**：新增页面 SSH 凭证目录，支持用户名 + 密码、用户名 + 密钥、用户名 + 密钥 + 密码三种组合；添加服务器时选择凭证，用户名可按服务器覆盖；登录秘密加密保存到共享 Redis，不再从 `.env` 读取 SSH 登录凭证。

**Git 基准点**：`024f234488c8af569dfce84a56fbbd8d3d1295de`（Manage SSH credentials in the page）；分支 `emdash/hot-boxes-clean-y9u6p`。完整测试在该功能代码提交对应的工作区运行；当时工作区还含其他未提交的 LLM、重试和数据相关改动，完整套件结果也覆盖了这些工作区改动。

**测试环境**：Docker Compose 容器（Python 3.12）；未连接真实远程 SSH 主机，SSH 调用通过隔离的命令构造与凭证处理测试验证。

**测试执行结果**：
- `docker compose up --build -d`：通过，代码变更后重建并启动服务。
- `docker compose run --rm --no-deps -v ./backend/tests:/app/tests:ro -v ./scripts:/scripts:ro trainer python -m pytest /app/tests/test_remote_workers.py /app/tests/test_api.py -k 'remote_ssh or remote_server' -v`：13 通过、61 跳过筛选、0 失败；1 个 SQLAlchemy 弃用警告。
- `docker compose run --rm --no-deps -v ./backend/tests:/app/tests:ro -v ./scripts:/scripts:ro trainer python -m pytest /app/tests/test_remote_workers.py -v`：30 通过、0 失败。
- `docker compose run --rm --no-deps -v ./backend/tests:/app/tests:ro -v ./scripts:/scripts:ro trainer python -m pytest /app/tests -v`：201 项收集，195 通过、6 跳过、0 失败、0 错误；2 个弃用警告。
- 前端内联 JavaScript `node --check`：通过；CPU 与远程 Compose 配置解析：通过；`git diff --check`：通过。
- 6 项跳过为外部服务门控测试：未配置 `MTP_TEST_REDIS` 的 Redis/Supervisor 集成用例，以及未配置 `MTP_TEST_S3` 的 2 项 S3 集成用例。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 页面可创建、查看、编辑和删除三种 SSH 凭证；密钥/密码不回显，Redis 中保存密文 | API：`test_remote_ssh_credential_catalog_supports_all_auth_modes_without_secret_echo`、`test_remote_ssh_settings_encrypt_credentials_and_never_return_secrets`；使用测试 Redis 适配层和 Fernet 密钥 | 三种认证组合均可保存并列出元数据；响应不包含秘密；目录密文可解密；被服务器引用的凭证拒绝删除 | 正常、CRUD、敏感数据保护、状态冲突 |
| 创建服务器时选择凭证；省略服务器用户名时继承凭证用户名，填写时可覆盖 | API：`test_remote_server_api_installs_and_manages_with_nonsecret_records`、`test_remote_server_uses_credential_username_when_server_username_is_blank` | SSH 使用解析后的用户名；服务器记录只保存凭证 ID 和非敏感元数据 | 正常、默认行为、兼容 |
| 密码、密钥及组合模式能正确执行认证，组合模式分别处理密钥口令与登录密码 | 远程执行器：`test_run_remote_action_uses_page_password_and_redacts_it`、`test_run_remote_action_materializes_page_private_key_temporarily`、`test_run_remote_action_combines_key_and_password_with_separate_prompts` | 使用页面凭证启动 SSH；组合模式配置密钥优先、密码回退；日志不泄露密码或密钥；临时私钥限制权限并在完成后清理 | 正常、异常认证、秘密脱敏、资源清理 |
| SSH 登录凭证不能从 `.env` 回退；非法输入与未授权管理受到拒绝 | 单元/API：`test_remote_ssh_credentials_do_not_fall_back_to_environment`、`test_remote_ssh_settings_reject_invalid_credentials`、目录 API 的未授权断言、`test_remote_server_api_rejects_shell_injection_before_ssh` | 未配置页面凭证时不读取旧登录环境变量；未授权请求、非法凭证与 shell 注入输入被拒绝 | 安全、恶意输入、异常 |
| 旧记录和既有远程服务器管理行为继续工作 | 完整 pytest 套件及旧单凭证兼容用例；覆盖已有远程配置/API 测试 | 既有 API、记录格式和远程 worker 管理保持可用 | 兼容性、回归 |

**覆盖范围与限制**：覆盖凭证 CRUD、加密存储、三种认证组合、用户名继承/覆盖、秘密脱敏、引用保护、旧数据兼容及无 `.env` 回退。没有对真实 SSH 主机做端到端连接；6 项外部服务门控测试未执行。

**下次测试建议**：在部署环境使用真实 SSH 主机验证 OpenSSH 密钥算法、加密私钥口令与密码回退行为；配置 `MTP_TEST_REDIS`、`MTP_TEST_S3` 后运行被门控的集成测试。

## Compose Redis 密码透传验证（2026-10-27）

**变更范围**：在 `docker-compose.yml` 与 `docker-compose.cpu.yml` 的共享后端环境配置中透传 `REDIS_PASSWORD`。配置变更提交为 `716c3d7`。

**验证结果**：
- 两份 Compose 配置校验通过；脱敏检查确认 trainer 和 worker 的展开配置均收到非空 `REDIS_PASSWORD`。
- `docker compose up --build -d` 构建并重建服务成功；worker 容器内变量检查返回 `set`。
- 从 worker 容器调用 `create_redis_client().ping()` 未通过，报 `Timeout connecting to server`。TCP 建连阶段超时，尚未验证 Redis 密码；worker 仍因 Redis 不可达而重启。
- `git diff --check` 通过。未运行 pytest：本次仅修改 Compose 配置，业务代码未变更。

**实际执行命令**：

```bash
set -o pipefail; docker compose config --format json | ruby -rjson -e 'cfg=JSON.parse(STDIN.read); %w[trainer worker].each { |s| v=cfg.dig("services",s,"environment","REDIS_PASSWORD"); puts "#{s} REDIS_PASSWORD: #{v.nil? || v.empty? ? "missing" : "set"}" }'
set -o pipefail; docker compose -f docker-compose.cpu.yml config --format json | ruby -rjson -e 'cfg=JSON.parse(STDIN.read); %w[trainer worker].each { |s| v=cfg.dig("services",s,"environment","REDIS_PASSWORD"); puts "cpu #{s} REDIS_PASSWORD: #{v.nil? || v.empty? ? "missing" : "set"}" }'
docker compose up --build -d
docker compose exec worker sh -lc 'test -n "$REDIS_PASSWORD" && echo REDIS_PASSWORD=set'
docker compose run --rm --no-deps worker python -c 'from app.coordination import create_redis_client; print(create_redis_client().ping())'
git diff --check
```

结果：配置展开与 worker 环境检查通过；容器内 PING 因 TCP 连接超时失败；`git diff --check` 通过。

**验收标准—验证映射**：密码变量由 Compose 传入 trainer/worker：通过配置展开检查与容器环境检查；worker 对 `47.101.33.133:6379` 完成认证 PING：未通过，当前阻塞在容器到 Redis 的网络连接。

**已知问题与下次验证建议**：检查 Redis 主机安全组、防火墙及 worker 所在网络的出站路径，确保 Docker worker 能连接目标 `6379`。网络连通后重新执行 worker 内 `create_redis_client().ping()`；若返回认证错误，再核对 Redis 密码或 ACL 用户。业务代码测试基准仍为 `63bc551`，本次配置变更未触发完整业务测试。

## Redis ECS 主机侧诊断补充（2026-10-27）

**只读检查结果**：ECS 上运行 Redis 8.0.5，监听 `0.0.0.0:6379`，配置为 `bind 0.0.0.0`、`protected-mode yes` 且已配置密码；未读取或记录密码。未认证的本机 `PING` 返回 `NOAUTH`，证明 Redis 进程可响应并要求认证。UFW 正在运行，默认拒绝入站；6379 仅允许来源 `59.152.38.247`。因此安全组截图中的 `0.0.0.0/0` 放行不会覆盖主机 UFW；若 worker 出口 IP 不是 `59.152.38.247`，主机防火墙会丢弃连接，表现为 TCP 超时。未修改 ECS 防火墙规则。

**结论与后续**：当前最可能阻塞点是 UFW 来源白名单，而非 Redis 密码验证。核对 worker 实际出口 IP，并仅为该地址添加 6379 入站规则；网络连通后再做密码 PING。保留安全组全网来源并非必要。

## Redis UFW 白名单修复与连通验证（2026-10-27）

**操作结果**：抓包确认 worker 的 TCP SYN 来源不在 UFW 原有的 Redis 来源白名单内。已仅为抓获的 worker 出口地址添加 TCP 6379 入站规则；UFW 默认拒绝策略及既有规则保持不变。未修改阿里云安全组，也未读取或记录 Redis 密码。

**验证结果**：从 worker 容器连接 `47.101.33.133:6379` 成功；调用项目 `create_redis_client().ping()` 返回 `True`，证明网络连通与当前认证配置均有效。Compose worker 随后保持运行并启动 CPU 槽。

**实际执行命令**：

```bash
docker compose run --rm --no-deps worker python -c 'import socket; s=socket.create_connection(("47.101.33.133", 6379), timeout=5); print("TCP connected"); s.close()'
docker compose run --rm --no-deps worker python -c 'from app.coordination import create_redis_client; print("Redis ping:", create_redis_client().ping())'
docker compose ps --format table
docker compose logs --tail=15 worker
```

**遗留项**：阿里云安全组仍允许 `0.0.0.0/0`，虽然 UFW 已按 worker 来源限制访问，仍建议将安全组入方向同步收窄到实际所需的固定来源。业务代码测试基准未变，本次为主机防火墙操作及容器连通验证。

## Redis 全 IPv4 入站放通验证（2026-10-27）

**操作范围**：按用户要求，在 ECS UFW 添加 `0.0.0.0/0` 到 TCP 6379 的入站允许规则。阿里云安全组原已开放全 IPv4；现在两层均允许任意 IPv4 连接。Redis 仍要求密码认证，但 Redis 服务未启用 TLS。

**验证结果**：`docker compose run --rm --no-deps worker python -c 'from app.coordination import create_redis_client; print(create_redis_client().ping())'` 返回 `True`；`docker compose ps` 显示 worker 持续运行。

**安全说明**：Redis 6379 当前可从整个公网访问。密码认证仍生效；由于未配置 TLS，客户端与 Redis 间流量未加密。

## 本次变更测试结果：增加任务日志清除功能（2026-10-27）

**变更范围**：训练任务日志页增加“清除日志”按钮；新增 `DELETE /api/jobs/{job_id}/logs`，同时清空状态存储日志和数据库镜像；使用 `log_revision` 防止运行中的 worker 将旧日志快照恢复。

**测试执行结果**：
- 定向日志清除测试：2 passed、0 failed。
- API 测试：56 passed、0 failed。
- Worker 测试：23 passed、0 failed。
- Compose：`docker compose up --build -d` 构建并启动成功。
- 组合测试第一次运行出现 1 个失败但进程输出未保留；拆分重跑 API 与 worker 均通过，未复现该失败。

**验收标准—测试用例映射**：
| 验收标准 | 测试 | 覆盖场景 |
|---|---|---|
| 清除按钮调用接口并清空显示 | `test_clear_job_logs_removes_file_and_database_logs` | 正常、数据库、回归 |
| 不存在任务返回 404 | `test_clear_job_logs_returns_404_for_missing_job` | 异常、边界 |
| 运行中清除不恢复旧快照 | worker `log_revision` 同步逻辑 | 并发、回归 |

**Git基准点**：`0dc831c`（Add task log clearing）。
## 本次变更测试结果：统一前端 UI 样式（2026-10-29）

**Git基准点**：`bdaa3d9`（Refresh frontend visual design system）
**分支**：`emdash/hot-boxes-clean-y9u6p`
**变更范围**：统一训练工作台与数据集管理页的浅色设计令牌、导航、卡片、表单、按钮、状态反馈、弹窗和响应式布局；仅修改 `backend/static/` 前端静态资源，不涉及业务 API、数据模型或训练逻辑。

**验收标准—测试用例映射**：

| 验收标准 | 测试层级、前置条件与用例 | 输入与预期 | 覆盖场景 |
|---|---|---|---|
| 两个页面使用统一的视觉层级和控件状态 | 静态资源检查；通过前端容器读取两个页面及共享 CSS | 页面均引用 `app.css?v=20261029-1`，CSS 设计令牌和页面选择器存在 | 正常、兼容 |
| 训练工作台和数据集管理页在桌面布局下无样式语法错误 | CSS 静态检查 | 花括号数量 431/431；`git diff --check` 通过 | 回归 |
| 窄屏布局有明确的折叠和堆叠行为 | CSS 规则检查 | 存在 1080px、720px、560px 响应式规则，覆盖导航、表单、按钮组、卡片和弹窗 | 边界、兼容 |
| 前端静态服务可以提供新资源 | 容器/API 冒烟 | `curl http://127.0.0.1:8000/healthz` 返回 `ok`；两个页面返回新 CSS 版本号 | 正常、回归 |

**测试执行结果**：

- 前端镜像：`docker compose build frontend` **通过**。
- 前端服务：`docker compose up -d --no-build frontend` **通过**，容器启动。
- 静态资源冒烟：healthz 返回 `ok`；训练页与数据集页均返回 `app.css?v=20261029-1`。
- CSS 静态检查：花括号平衡、`git diff --check` **通过**。
- 完整 `docker compose up --build -d`：**未通过**。失败发生在后端依赖安装阶段，阿里云镜像返回的 `torch-2.14.1` 文件哈希与 requirements 锁定值不一致（Expected `0b13ac...`, Got `061ff5...`）；未执行完整 pytest，不能将本次变更标记为完整容器回归通过。

**关键模块测试**：前端静态服务与资源版本检查通过；后端业务测试未执行（本次仅前端样式变更，且完整 Compose 构建被依赖镜像哈希错误阻断）。

**已知问题与限制**：完整 Compose 重建需要修复或更换当前 PyPI 镜像中的 torch 包哈希后重新执行；前端镜像本身已成功构建并启动。

**下次测试建议**：在可用的 Python 3.12 依赖镜像环境中重新执行 `docker compose up --build -d`，随后运行完整 `cd backend && python -m pytest tests/ -v` 回归套件。

## 本次变更测试结果：弹窗分页展示数据集预览（2026-10-29）

### Git 基准点与范围

- **Git 基准点**：`80bf639`（Add paginated dataset preview modal）；本报告随后单独提交。
- **分支**：`emdash/hot-boxes-clean-y9u6p`。
- **变更范围**：训练工作台的数据集预览改为按需打开弹窗；弹窗按每页 10 行请求并展示表格，支持上一页、下一页、关闭、遮罩关闭和 Escape 关闭。数据集详情 API 新增 `rows` / `offset` 分页参数并返回页码元数据；旧的不带参数调用继续返回前 5 行。
- **涉及文件**：`backend/static/index.html`、`backend/app/dataset_manager.py`、`backend/app/routers/datasets.py`、`frontend/dataset-preview.test.cjs`、`backend/tests/test_dataset_preview_pagination.py`。

### 验收标准—测试用例映射

| 验收标准 | 测试层级、用例与输入 | 预期与覆盖场景 |
|---|---|---|
| 预览数据只在弹窗中展示，训练页主布局不内联表格 | Node：`预览入口打开弹窗，主页面不再内联表格`；列表加载用例 | 默认不请求详情；点击后打开 `role=dialog` 弹窗并加载第一页；正常、兼容、回归 |
| 数据按页展示，每页 10 行且可前后翻页 | Node：`点击打开弹窗并加载第一页`、`下一页和上一页按偏移请求`；Python：`test_preview_returns_requested_page_and_metadata` | 请求 `rows=10` 与对应 `offset`，页码和按钮状态正确，最后一页不足 10 行；正常、边界 |
| 非法分页参数、越界、请求失败和恶意文本可安全处理 | Python：`test_preview_rejects_invalid_page_parameters`、`test_preview_out_of_range_returns_empty_page`；Node：失败重试、特殊字符转义、旧响应隔离 | 非法参数返回 400，越界返回空页，失败可重试，HTML 特殊字符不执行；异常、安全、并发 |
| 旧 API 调用和训练列配置保持兼容 | Python：`test_preview_default_call_remains_first_page`；Node：`预览分页不重置训练列选择`；前端完整套件 | 不带分页参数仍返回 5 行，翻页不重置列选择，切换数据集从第一页开始；兼容、回归 |

### 测试执行结果

| 阶段 | 结果 | 说明 |
|---|---|---|
| `node --test frontend/dataset-preview.test.cjs` | **8 通过，0 失败** | 弹窗、分页、关闭、异步竞态、错误重试和安全转义 |
| `node --test frontend/*.test.cjs` | **100 通过，0 失败** | 当前共享工作区前端回归套件 |
| Python 3.12：`pytest tests/test_dataset_preview_pagination.py -q` | **6 通过，0 失败** | 1 条既有 SQLAlchemy 弃用警告 |
| 内联 JavaScript `vm.Script` 解析 | **通过** | 当前候选页面脚本可解析 |
| 前端容器：`docker compose up -d --no-build --no-deps frontend` | **通过** | 容器健康；`curl http://127.0.0.1:8000/healthz` 返回 `ok`；页面包含 `previewModal`、`previewPrev`、`previewNext` |
| `git diff --check` | **通过** | 无空白错误 |
| `docker compose up --build -d` | **未完成** | trainer/worker 构建下载 PyTorch 483.7 MB 时出现 SSL record layer failure，重试后下载内容 SHA256 与 requirements 锁定值不一致；已停止长时间重试。前端镜像已完成构建并单独启动 |
| 后端完整回归（Python 3.12 镜像） | **507 通过、33 失败、24 跳过** | 失败集中在只挂载 `backend/` 时缺少 `/scripts`、`/docker-compose.yml`，以及并行任务涉及的 worker、NCCL、KMeans/远程环境；不能作为完整回归通过结论 |

### 已知问题与下次验证建议

- 默认 Compose 全量重建仍受 PyTorch 镜像传输和哈希不一致阻塞；依赖镜像稳定后应重新执行标准构建并运行完整 pytest。
- 本次最新弹窗版本未完成 Safari 中点击按钮后的人工交互验收，Node 测试和静态服务检查已覆盖相同交互逻辑；应在稳定的训练工作台标签页补做打开弹窗、翻页和关闭的浏览器验收。
- 完整后端回归中的失败与本次分页逻辑无关，需在完整项目根目录挂载并隔离其他并行变更后重新分类验证。

## 本次变更测试结果：远程服务器状态与实际 worker 容器校准（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：`d9f542d60a5cab25287cae4b732593426d2a26f8`。
- **提交信息**：Reconcile remote worker deployment state。
- **测试日期 / 分支**：2026-10-30 / `emdash/hot-boxes-clean-y9u6p`。
- **变更范围**：远程 worker 操作执行器和远程 worker 回归测试。部署代码遇到“远端 worker 容器/配置缺失”时自动转为完整安装；生命周期操作确认缺失时将 MySQL 服务器记录校准为 `deployed=false`、`status=not_deployed`，页面下次刷新显示“部署”。无新增依赖、数据库迁移或配置修改。

### 验收标准—测试用例映射

| 验收标准 | 测试层级、前置条件与输入 | 预期结果与场景 |
|---|---|---|
| 部署代码与远端实际一致 | `test_execute_remote_operation_recovers_deploy_when_worker_container_is_missing`；模拟已部署记录、远端部署返回“未找到容器” | 自动按 `deploy → install` 重试，完整安装成功后操作完成、服务器在线、日志记录自动校准；正常、异常、回归 |
| 完整恢复失败不继续显示已部署 | `test_execute_remote_operation_keeps_missing_state_when_recovery_fails`；部署和自动安装均返回配置缺失 | 操作失败但持久化为 `deployed=false`、`not_deployed`；异常、状态冲突 |
| 启动/重启等生命周期操作校准缺失容器 | `test_execute_remote_operation_marks_missing_worker_as_undeployed`；重启返回“未找到 worker 容器” | 操作失败，保留原始错误，服务器记录可直接进入下一次部署；异常、兼容 |
| 已有容器的既有语义保持不变 | `test_execute_remote_operation_persists_success_and_failure`、心跳成功/超时用例及完整远程 worker/API 套件 | 正常部署、失败、心跳缺失和成功心跳结果保持原行为；正常、边界、回归 |

### 测试执行结果

| 阶段 / 实际命令 | 通过 | 失败 | 错误 / 跳过 | 结论 |
|---|---:|---:|---:|---|
| Python 3.12 专项：`docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py -q -k 'execute_remote_operation_recovers_deploy_when_worker_container_is_missing or execute_remote_operation_marks_missing_worker_as_undeployed or execute_remote_operation_keeps_missing_state_when_recovery_fails or execute_remote_operation_persists_success_and_failure or execute_remote_operation_records_missing_worker_heartbeat or execute_remote_operation_records_healthy_worker_heartbeat'` | 6 | 0 | 0 / 148 未选 | 通过 |
| Python 3.12 远程/API 回归：`docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/test_remote_workers.py tests/test_api.py -q --tb=short` | 239 | 0 | 0 / 0 | 通过 |
| Python 3.12 完整套件：`docker run --rm --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q --tb=short` | 565 | 0 | 2 / 24 | 已执行用例 100% 通过 |
| `git diff --check`（业务代码与正式测试） | 通过 | — | — | 无空白错误 |
| `docker compose up --build -d` | — | 1 | 构建中止 | trainer/worker 安装 PyTorch 2.15.0 时镜像下载仅收到约 7.9/483.7 MB，SHA256 与 requirements 锁定值不一致；已停止重试，不能标记完整 Compose 构建通过 |

### 关键模块与环境验证

- 远程 worker 模块：专项 6/6，包含自动恢复、恢复失败、状态校准、既有成功/失败和心跳分支。
- API 与远程操作：239/239 通过。
- 完整后端套件：565 通过、24 跳过、0 失败、0 错误；跳过项为既有真实 Redis/S3/GPU/NCCL 等环境依赖。
- 替代验证使用现有 trainer 镜像中的 **Python 3.12.15**，挂载当前源码执行；未使用宿主 Python 3.9 结果替代容器验证。
- 完整 Compose 构建未通过的原因是外部 PyTorch 镜像传输内容哈希不匹配，与本次远程状态代码无关；前端镜像在同一次构建中已完成，但不能代表整套服务构建成功。

### 已知问题、重测与回滚

- 稳定依赖源可用后，应重新执行 `docker compose up --build -d`，确认 trainer、worker、frontend 全部从当前源码重建并启动。
- 若远端容器被手动删除，第一次点击“部署代码”会触发完整镜像构建，耗时可能明显高于复用现有镜像的普通代码部署。
- 修改远程操作状态机、`remote_worker.sh` 容器错误文本、服务器状态持久化或部署按钮行为时，需重跑远程 worker 专项、API 套件和完整后端套件。
- 回滚可撤销提交 `d9f542d` 并重新构建平台；无数据迁移。

## 本次变更测试结果：训练资源选择与异构设备兼容（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：`d92d1c0`（Improve training workbench interactions）；核心实现提交为 `9601e84`，异构设备补充提交为 `d252c8a`。
- **测试日期 / 分支**：2026-10-30 / `emdash/hot-boxes-clean-y9u6p`。
- **范围**：训练表单可自动分配或指定在线计算槽，并筛选 CUDA、ROCm、XPU、MPS、CPU 后端；API 在创建任务前校验槽位状态和任务要求，worker 仅由指定槽领取并在租约后复核；失败重试与暂停恢复保留选择。异构联合训练按成员能力选择 NCCL/Gloo，Supervisor 按槽位隔离设备可见性。训练台增加配置错误定位、任务搜索/状态筛选、详情请求竞态保护和日志跟随控制。无新增运行时依赖、数据库迁移或训练数据格式变更。

### 验收标准—测试用例映射

| 验收标准 | 可执行用例 | 预期与场景 |
|---|---|---|
| 用户可自动调度或指定在线、启用且兼容的计算槽；无效选择不能创建任务 | API：`test_training_resource_selection_persists_hint`、`test_training_resource_selection_rejects_invalid_without_job`、`test_training_resource_selection_gpu_vram_boundary`、`test_training_resource_selection_dependency_failure_is_redacted`；前端：`frontend/training-resources.test.cjs` | CPU/GPU 槽展示容量和状态；离线、停用、失效及任务不兼容的槽阻止提交；忙碌槽明确排队；恶意标签安全转义。正常、边界、异常、安全 |
| 指定任务只由目标槽执行，租约竞争或策略变化后重新核验 | Worker：`test_selected_training_resource_executes_on_target_only`、`test_selected_training_resource_kept_on_auto_retry`、`test_selected_training_resource_rechecked_after_lease` | 非目标 worker 不领取；目标槽完成分类/回归并生成指标、`best.pt`、预处理产物和日志；自动重试保留目标，禁用/改选/degraded 竞争不启动训练。正常、并发、回归 |
| 手动重试和暂停恢复保留或明确更新分配策略 | JobManager：`test_manual_retry_requeues_failed_job_with_selected_assignment`、`test_selected_resource_manual_retry_replaces_or_clears_policy`、`test_selected_resource_survives_resume_and_redispatch` | 指定槽、自动重派、联合训练整组重试规则保持一致；暂停恢复继续等待原指定槽。状态冲突、兼容、回归 |
| 任务资源提示和可用设备后端约束得到执行 | `test_device_info_reports_simulated_cross_platform_backends`、`test_member_selection_supports_each_device_backend`、`test_incompatible_members_are_rejected` | CUDA、ROCm、XPU、MPS、CPU 的厂商、设备字符串及能力映射正确；未知后端、无效索引和不支持架构被拒绝。正常、边界、异常 |
| 异构联合训练通信与设备隔离正确 | `test_mixed_accelerators_use_gloo`、`test_cpu_slot_hides_inherited_gpu_visibility`、`test_supervisor_sets_backend_specific_device_visibility` | 仅全 NVIDIA CUDA 使用 NCCL，混合设备使用 Gloo；HIP/ROCR、CUDA、oneAPI 可见性按槽位隔离，CPU 清理继承变量。兼容、安全、回归 |
| 远程 worker 构建匹配后端并拒绝不支持的容器目标 | `test_remote_build_selects_non_cuda_gpu_backend`、`test_remote_build_rejects_mps_docker_deployment` | ROCm/XPU 使用对应构建参数，MPS 明确要求 macOS 原生 worker。配置边界、兼容 |
| 训练台交互清晰且避免旧响应覆盖新选择 | `frontend/training-resources.test.cjs`、`frontend/workbench-interactions.test.cjs` | 表单校验和定位、资源失效提示、任务搜索筛选、日志跟随/回到最新、标签键盘操作及异步竞态均有断言。正常、异常、并发、可访问性 |
| 训练、预测、产物和既有数据布局保持兼容 | 完整 `backend/tests/` | 分类/回归训练产物、指标、预测、checkpoint、旧/新数据布局和远程管理回归通过。兼容、回归 |

### 测试执行结果

| 阶段与实际命令 | 结果 |
|---|---|
| Python 3.12.15 相关回归：`docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer python -m pytest tests/test_device.py tests/test_worker.py tests/test_parallel.py tests/test_api.py -q -p no:cacheprovider --basetemp=/tmp/selectable-resources-related --tb=short` | **203 passed，6 skipped，0 failed**；覆盖 API、worker、设备和联合训练 |
| Python 3.12.15 完整回归：`docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/selectable-resources-full --tb=short -rs` | **571 passed，24 skipped，0 failed**；2 条既有弃用警告 |
| 前端完整套件：`node --test frontend/*.test.cjs` | **100 passed，0 failed**；包括资源选择与工作台交互 |
| `git diff --check` | 通过；无空白错误 |
| 标准容器重建：`docker compose up --build -d` | 首次等待依赖安装时手动停止；第二次复用已缓存 wheel 后 **通过**，trainer、worker、frontend 均构建并启动 |
| HTTP 与资源冒烟：`curl --fail --silent http://127.0.0.1:8000/api/health`、`curl --fail --silent http://127.0.0.1:8000/api/workers` | 通过；health 返回 `{"ok":true}`，workers 返回在线 CPU 槽及设备后端字段；工作台包含资源选择控件 |

### 已知限制

- Apple MPS 只能在 macOS 原生 worker 使用；Linux Docker 不透传 MPS，Windows GPU 使用 WSL2 对应运行时。
- 当前环境没有 NVIDIA、AMD、Intel 或 Apple GPU，真实 CUDA/ROCm/XPU/MPS 算子和 NCCL 仅由协议模拟覆盖；应在对应硬件和驱动环境补跑。
- 24 项完整套件跳过项依赖真实 Redis、S3、Supervisor 多进程环境或双 NVIDIA GPU/NCCL；均未配置，不视作通过。
- 标准 Compose 首次安装依赖耗时较长；缓存 wheel 后第二次完整构建成功。
- 临时等价验证文件和容器不纳入仓库；未执行 `git push`。

## 本次变更测试结果：Linux 训练日志实时返回（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：`6331ec3df34b918e6f22e15c77eb1441bcdf11b5`。
- **提交信息**：Cover live worker log publication。
- **测试日期 / 分支**：2026-10-30 / `emdash/hot-boxes-clean-y9u6p`。
- **范围与技术**：训练日志 API 与训练工作台日志轮询；FastAPI、原生 JavaScript、Worker 集成测试、Node.js 内置测试、pytest、Docker Compose。
- **行为**：日志接口禁止浏览器/反向代理缓存；前端日志请求显式 `cache: "no-store"`，并从详情刷新中拆出独立 1 秒轮询；同一任务慢请求不重叠，切换任务后旧请求不会阻塞新任务或回填旧日志。Worker 仍沿用逐条发布到权威状态存储的既有语义。
- **缺陷复现**：修复前新增 API 回归用例失败，运行中日志响应没有 `Cache-Control`；修复后运行中增量读取、响应头与实际 Worker 发布测试均通过。

### 验收标准—测试用例映射

| 验收标准 | 层级、用例与前置条件 | 输入、预期和场景 |
|---|---|---|
| 训练未完成时可读取新增日志 | pytest `test_running_job_logs_are_immediately_visible_and_uncacheable`；local 状态存储，任务保持 `running` | 首次读取启动日志，追加 epoch 日志后按 offset 读取新增行；内容连续且状态仍为 running；正常、边界、兼容 |
| 真实 Worker 在完成前发布 epoch 日志 | pytest `test_running_job_publishes_epoch_log_before_completion`；真实 CPU Worker 与真实训练循环 | 300 epoch 任务运行中读取 `[epoch 1/300]`，断言任务尚未 completed；训练链路、回归 |
| Linux/代理不会复用旧日志响应 | 同一 API 用例 + Compose curl 核对 | `/api/jobs/{id}/logs` 返回 `Cache-Control: no-store, no-cache, must-revalidate`、`Pragma: no-cache`、`Expires: 0`；部署、兼容 |
| 前端日志持续轮询且不重复/不串任务 | Node `frontend/workbench-interactions.test.cjs` 新增日志缓存、慢请求锁、任务切换和独立定时器用例 | 23 个工作台交互用例全部通过；正常、异常、状态竞争、回归 |

### 测试执行结果

| 阶段 | 总数 | 通过 | 失败 | 跳过 | 说明 |
|---|---:|---:|---:|---:|---|
| 日志 API 定向 pytest | 7 | 7 | 0 | 0 | 运行中日志、清除、旧数据库回退、分页 |
| Worker 实时日志定向 pytest | 1 | 1 | 0 | 0 | 真实 CPU 训练 Worker，运行中读取 epoch 日志 |
| 后端完整 pytest | 597 | 573 | 0 | 24 | Python 3.12.15；执行用例通过率 100% |
| 前端完整 Node 套件 | 103 | 103 | 0 | 0 | 含工作台、资源、数据集、服务器/凭证页面 |
| Compose 重建 | 3 镜像 / 4 服务 | 3 构建 | 0 | 0 | trainer、worker、frontend 重建；SeaweedFS 保持 healthy |
| 部署健康与内容核对 | 4 | 4 | 0 | 0 | `/api/health`、页面脚本、实际日志接口响应头 |

完整 pytest 的 24 项跳过包括未配置真实 Redis 的注册/恢复场景、未配置 S3 的 2 项、Supervisor Redis E2E、真实 NVIDIA GPU/NCCL 场景。保留既有 SQLAlchemy 与 Pandas 弃用警告。未执行真实远程 Linux 服务器部署；Compose 使用当前本地服务完成重建与 HTTP 核对。

### 实际命令与结果

```bash
docker run --rm --pull=never --network none --mount type=bind,src=$PWD,dst=/workspace --workdir /workspace/backend mtp-resource-deps:validation python -m pytest tests/test_api.py -q -p no:cacheprovider -k 'running_job_logs_are_immediately_visible_and_uncacheable or job_logs_' --tb=short
docker run --rm --pull=never --network none --mount type=bind,src=$PWD,dst=/workspace --workdir /workspace/backend mtp-resource-deps:validation python -m pytest tests/test_worker.py -q -p no:cacheprovider -k running_job_publishes_epoch_log_before_completion --tb=short
node --test frontend/*.test.cjs
docker run --rm --pull=never --network none --mount type=bind,src=$PWD,dst=/workspace --workdir /workspace/backend mtp-resource-deps:validation python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/live-logs-full-2 --tb=short -rs
docker compose up --build -d
curl --fail --silent http://127.0.0.1:8000/api/health
curl --silent --show-error -D - -o /dev/null 'http://127.0.0.1:8000/api/jobs/job-20261029-175056-436cae/logs?offset=0'
git diff --check
```

- 复现阶段 API 测试按预期失败：响应缺少 `Cache-Control`；加入防缓存头后定向测试通过。
- Compose 默认构建成功，trainer、worker、frontend 镜像重新构建并启动；frontend 健康检查通过，复用的 SeaweedFS 保持 healthy。
- 生产入口 `/api/health` 返回 200；已有完成任务的日志接口返回 `cache-control: no-store, no-cache, must-revalidate`、`pragma: no-cache`、`expires: 0`。
- 本次未执行 `git push`。未新增调试文件或运行时产物提交。

### 已知问题与后续建议

- 真实 Redis/S3/GPU/NCCL 场景仍需在对应环境补验；本次完整套件的 24 个跳过项沿用仓库既有条件。
- 日志轮询仅在“日志”标签页激活时运行；切换到日志页会立即拉取当前 offset，避免无意义后台请求。
- 回滚可撤销 `311f6ff`、`a89f81f`、`6331ec3` 三个本次代码/测试提交并重新执行 Compose 构建；无数据迁移。

## 本次变更测试结果：联合训练节点不可选修复（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：`b283356`（Enable local nodes for joint training）。
- **测试日期 / 分支**：2026-10-30 / `emdash/hot-boxes-clean-y9u6p`。
- **变更范围**：两份 Compose 的本机 worker 默认上报 `worker` 服务名；训练页细分节点不可选原因，并为缺少互联地址的节点提供服务器管理入口；README 说明本机槽与跨机器槽的网络要求。无依赖、数据库或数据格式改动。
- **根因**：`parallel.host` 为空会导致训练页禁用复选框，后端也会拒绝缺少 Gloo rendezvous 地址的成员。本机 Compose worker 原先没有设置 `WORKER_DISTRIBUTED_HOST`。
- **修复行为**：同一 Compose 网络内的本机 worker 槽使用服务 DNS 名 `worker`，可选择多个本机槽联合训练。远端槽没有可达地址时仍禁选，页面给出原因与配置入口，不猜测跨机器地址。

### 验收标准—测试用例映射

| 验收标准 | 可执行用例与前置条件 | 预期结果与场景 |
|---|---|---|
| 本机 Compose worker 上报联合训练地址并允许选择两个槽 | pytest `test_detect_capabilities_reports_configured_parallel_host`、`test_compose_separates_local_worker_image_from_remote_default`；Node 联合训练节点选择用例 | 两个本机槽地址为 `worker`、Gloo 可用，前端允许提交并包含两个槽与各自批大小；正常、配置、兼容 |
| 未配置地址的远端槽不能进入训练，且有明确修复入口 | Node 联合训练节点选择用例；远端地址为空 | 远端复选框禁用并显示“缺少可达的联合训练互联地址”，提供服务器管理链接；边界、安全、回归 |
| 节点离线、停用、通信不兼容及非法批大小继续被拒绝 | `frontend/parallel-training.test.cjs` 节点状态用例；后端联合训练能力校验用例 | 无效节点不能提交，服务端保留协议/能力校验；异常、兼容 |
| 两个实际 Compose worker 槽可完成同一模型训练 | 运行中的 Compose Redis/S3/worker 与示例分类数据；POST `/api/train` 选择 `cpu0`、`cpu1`，随后轮询任务、读日志和指标 | Gloo 双槽训练完成，最终准确率 0.6167，日志记录两个槽和全局批大小 3；训练链路、部署 |
| 既有训练、产物和接口行为回归 | Python 3.12 完整 `backend/tests/`；Node 前端完整套件 | 训练、状态、日志、指标/模型产物、数据格式、设备选择及管理 API 保持通过；回归 |

### 测试执行结果

| 阶段与实际命令 | 结果 |
|---|---|
| `node --test frontend/parallel-training.test.cjs` | **10 passed，0 failed** |
| `node --test frontend/*.test.cjs` | **104 passed，0 failed** |
| `pytest tests/test_worker.py -q` | **48 passed，4 skipped，0 failed**；4 项依赖真实 Redis |
| `pytest tests/test_parallel.py -q` | **55 passed，2 skipped，0 failed**；2 项依赖真实 GPU/NCCL |
| `pytest tests/test_remote_workers.py -q` | **157 passed，0 failed** |
| `pytest tests/ -q` | **574 passed，24 skipped，0 failed**；两条既有 SQLAlchemy/Pandas 弃用警告 |
| `docker compose up --build -d` | trainer、worker、frontend 三个镜像构建并启动成功；SeaweedFS 保持 healthy |
| 运行服务核对 | `/api/health` 返回 `{"ok":true}`；worker 心跳显示两个本机槽地址为 `worker`、在线且启用，未配置地址的远端槽仍为空 |
| Compose 实际两槽训练 | 1 个分类任务完成，Gloo、批大小 2+1、1 epoch，最终 accuracy 0.6167；训练日志和指标已读取。清理时任务 API 已返回 404，随后核对对象存储任务前缀为空 |

首次将 worker、联合训练和远程 worker 套件合并为一次长运行时观察到两个失败标记；该命令的 `--rm` 容器退出后没有保留完整失败详情。三个套件分开重跑均通过，随后 Python 完整套件也通过；未删除或放宽任何测试。

### 实际命令与环境

```bash
node --test frontend/parallel-training.test.cjs
node --test frontend/*.test.cjs
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer python -m pytest tests/test_worker.py -q -p no:cacheprovider --basetemp=/tmp/joint-worker --tb=short -rs
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer python -m pytest tests/test_parallel.py -q -p no:cacheprovider --basetemp=/tmp/joint-parallel --tb=short -rs
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer python -m pytest tests/test_remote_workers.py -q -p no:cacheprovider --basetemp=/tmp/joint-remote --tb=short -rs
docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/joint-node-selection-full --tb=short -rs
docker compose up --build -d
curl --fail --silent http://127.0.0.1:8000/api/health
curl --fail --silent http://127.0.0.1:8000/api/workers
git diff --check
```

测试容器使用 Python 3.12。临时任务使用现有示例 CSV；任务在清理请求时已不在任务 API 中，对象存储前缀检查也为空。调试响应文件和 pytest 临时目录均位于 `/tmp`，交付前清理。

### 已知限制与回滚

- `worker` 只在本 Compose 网络可解析，不能用作跨机器地址。远端受管 worker 需在服务器管理页填写可被所有成员双向访问的训练网地址并重新部署。
- 本机 Compose worker 若要和远端节点混合训练，还需按部署网络设置主机可达地址及网络模式；本次不自动推断宿主机 LAN IP。
- 24 项跳过包含真实 Redis、S3、Supervisor 多进程与 GPU/NCCL 环境门控项；没有将其视为通过。
- 回滚可撤销 `b283356` 和本报告提交，再执行 Compose 重建；无数据迁移。
## 本次变更测试结果：训练资源独占与状态同步（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：`bfe6455`（Synchronize training resource occupancy）。
- **测试日期 / 分支**：2026-10-30 / `emdash/hot-boxes-clean-y9u6p`。
- **范围**：训练 Worker 心跳、CPU/GPU 槽位租约、资源 API 状态、训练工作台和服务器资源卡片；不改变任务数据格式、模型产物格式或新增依赖。
- **行为**：训练领取后立即显示准备中/训练中；槽位租约期间其他训练只能排队；空闲心跳不能覆盖占用状态；旧执行者不能覆盖新任务；完成、失败、暂停和取消后清理任务、轮次和槽位占用。

### 验收标准—测试用例映射

| 验收标准 | 可执行验证 | 结果与场景 |
|---|---|---|
| 训练状态与资源卡片及时同步 | `test_controller_publishes_progress_before_first_periodic_heartbeat`、`test_resource_api_reports_exclusive_reservations`；前端状态映射测试 | 通过；首次周期心跳前即显示任务和轮次；正常、边界、兼容 |
| 选定资源训练期间独占，竞争任务排队 | `test_reserved_slot_does_not_poll_competing_jobs_or_publish_idle`、`test_slot_is_exclusive_and_released_with_clean_status`、`test_slot_competition_has_exactly_one_owner` | 通过；同槽并发只有一个租约持有者，后续任务保持 queued；并发、状态冲突、回归 |
| 旧执行者不能覆盖新任务 | `test_stale_controller_cannot_replace_successor_resource_status`、`test_old_heartbeat_cannot_overwrite_reallocated_slot`、Redis/内存双实现 guarded heartbeat 测试 | 通过；租约 fencing 和原子心跳校验生效；异常、安全 |
| 原训练链路保持可用 | `python -m pytest tests/ -q`、真实 API CPU 双任务串行冒烟、模型/指标/日志检查 | 后端完整回归 617 通过、31 跳过；容器服务健康；真实训练槽位结束后恢复 idle |
| 页面正确表达占用和排队 | `node --test frontend/*.test.cjs`（隔离候选快照） | 119 通过；状态中文化、忙碌排队提示、XSS 转义和旧页面回归通过 |

### 测试执行结果

| 阶段 / 实际命令 | 通过 | 失败 | 跳过 | 说明 |
|---|---:|---:|---:|---|
| 后端完整回归 `python -m pytest tests/ -q -p no:cacheprovider` | 617 | 0 | 31 | Python 3.12.15；2 条既有弃用警告；跳过项为未配置 Redis/S3/NVIDIA GPU/NCCL 等条件环境 |
| Redis/内存租约专项回归 | 40 | 0 | 0 | 覆盖原子心跳、槽位竞争、失租和重新分配 |
| 前端隔离候选回归 `node --test frontend/*.test.cjs` | 119 | 0 | 0 | 只包含本次页面状态变更及已有前端测试 |
| 容器重建 `docker compose up --build -d` | 4 服务 | 0 | 0 | trainer、worker、frontend、SeaweedFS 均启动 |
| HTTP 健康检查 | 2 | 0 | 0 | `/api/health` 和 `/api/workers` 返回 200；空闲槽为 `idle`、无任务、无占用 |
| 真实 CPU 串行训练冒烟 | 2 | 0 | 0 | 两个任务指定同一 CPU 槽；先运行任务占用槽位，后续任务排队，前者释放后才执行；产物、指标、日志可读 |

专项测试使用的实际命令：

```bash
docker run --rm --name mtp-slot-sync-full --pull=never --network none \
  --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest \
  python -m pytest tests/ -q -p no:cacheprovider \
    --basetemp=/tmp/mtp-slot-sync-full --tb=short -rs

docker run --rm --pull=never --network container:mtp-slot-sync-redis \
  --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 \
  --env MTP_TEST_REDIS=redis://127.0.0.1:6379/15 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest \
  python -m pytest tests/test_coordination.py tests/test_worker.py -q \
    -p no:cacheprovider -k 'redis or heartbeat or slot_competition or reallocated_slot' \
    --basetemp=/tmp/mtp-slot-sync-redis-tests --tb=short -rs

node --test --test-reporter=tap /tmp/mtp-slot-sync-KDq9gF/snapshot/frontend/*.test.cjs
docker compose up --build -d
```

- 先运行缺陷回归得到 7 项预期失败，确认长心跳窗口仍显示 idle、已有槽租约仍会发布空闲、旧心跳可覆盖新状态；修复后这 7 项全部通过。前端新增状态用例在实现前 11 项失败，合并实现后候选快照全套 119 项通过。
- 一次把专用 `MTP_TEST_REDIS` 直接用于完整 API 回归的尝试得到 4 项超时失败、126 项通过和 1 项跳过，随后人工中断剩余用例。该环境变量会让 `backend/tests/conftest.py` 将 API 测试切换为分布式模式，而该运行没有为 API E2E 启动 Worker；四项任务因此留在队列。移除该变量重新跑完整嵌入式套件后 617 项通过、31 项跳过；Redis 专项另用独立测试容器运行，40 项通过。
- API 真实训练串行冒烟运行两个指定同一 CPU 槽的分类任务：观察到首项 running 时后项 queued，资源 API 显示占用槽、执行任务和训练状态；首项完成时间早于后项开始时间。两个任务均成功，训练指标、日志和模型端点验证通过；测试任务及数据集随后删除，线上槽位恢复 idle。

### 已知问题与限制

- 当前环境没有 NVIDIA GPU、真实多机 Redis/S3 和 NCCL，因此 GPU 利用率及多卡通信仍按既有条件测试跳过。
- 主机 CPU 指标仍是整机口径；同一主机多个槽的百分比不能相加。
- 训练状态依赖协调平面心跳；协调平面完全不可用时，租约/watchdog 的既有接管语义仍需在真实 Redis 环境补验。

### 下次测试建议

- 在真实 Redis 多 worker 环境执行跨进程槽位抢占、worker 崩溃接管和网络抖动测试。
- 在真实 GPU 节点验证 GPU 槽独占、显存限制和训练吞吐。
- 修改槽位租约、心跳状态或资源选择逻辑时，重跑本节专项、完整后端套件和前端套件。

## 本次变更测试结果：移除训练页管理快捷入口（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：`2ca97d3`（Remove management shortcuts from training views）。
- **测试日期 / 分支**：2026-10-30 / `emdash/hot-boxes-clean-y9u6p`。
- **变更范围**：训练台计算资源栏移除“管理服务器”入口；添加服务器弹窗移除“管理凭证”入口。保留服务器/凭证独立管理页导航、凭证选择和刷新；无后端接口、依赖或数据格式变更。

### 验收标准—测试用例映射

| 验收标准 | 可执行验证 | 结果与场景 |
|---|---|---|
| 训练台和服务器弹窗不再出现这两个快捷入口 | `frontend/server-modal.test.cjs` 的“工作台和服务器弹窗不再提供跨页管理入口”；`test_remote_server_dialog_groups_fields_and_keeps_actions_outside_scroll_area` | 通过；页面结构、正常、兼容 |
| 凭证为空时校验焦点可用，刷新和已有凭证流程保留 | Node `server-modal.test.cjs`；`test_remote_server_dialog_groups_fields_and_keeps_actions_outside_scroll_area` | 通过；空状态、焦点、刷新、回归 |
| 容器提供已更新的静态页面 | `docker compose up --build -d`、`/api/health`、训练台和服务器页 HTTP 内容核对 | 通过；4 个服务运行，健康检查成功 |

### 测试执行结果

| 阶段 / 实际命令 | 通过 | 失败 | 跳过 | 说明 |
|---|---:|---:|---:|---|
| 前端专项 `node --test frontend/server-modal.test.cjs` | 33 | 0 | 0 | 覆盖服务器/凭证弹窗和页面入口 |
| JS 语法 `node --check backend/static/remote-management.js` | 1 | 0 | 0 | 通过 |
| 后端相关 `python3 -m pytest backend/tests/test_api.py -q -k 'remote_server_dialog or management_tabs' --tb=short` | 3 | 0 | 85 | 宿主 Python 3.9.6；1 条既有 SQLAlchemy 弃用警告 |
| 前端完整 `node --test frontend/*.test.cjs` | 98 | 22 | 0 | 22 项失败均在工作台测试 fixture 初始化时报 `document.addEventListener is not a function`；该工作区同时有未提交的训练工作台交互改动 |
| Python 3.12 后端完整 `python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/mtp-remove-management-links --tb=short -rs` | 615 | 2 | 31 | 1 项静态契约断言期待旧 CSS 缓存版本（工作区并行修改已更新版本）；1 项 Gloo 多进程异常退出，单独重试该测试后 3 个参数用例通过 |
| 容器重建及健康检查 | 4 服务 | 0 | 0 | trainer、worker、frontend、SeaweedFS 均运行；`/api/health` 返回 `{"ok":true}` |

Python 3.12 完整套件实际运行于已构建的 trainer 镜像：

```bash
docker run --rm --pull=never --network none \
  --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --workdir /workspace/backend \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 \
  emdash-hot-boxes-clean-y9u6p-trainer:latest \
  python -m pytest tests/ -q -p no:cacheprovider \
    --basetemp=/tmp/mtp-remove-management-links --tb=short -rs
```

完整前端套件的失败属于并行工作区测试夹具与训练台交互改动不匹配；完整后端套件的 CSS 版本失败也由同一并行工作区的静态资源版本更新触发。该环境下完整套件仍有未通过项，不能据此判定全套回归通过；本次相关前后端专项均通过。

### 已知限制与下次测试建议

- 全量测试需在训练工作台并行改动及其测试夹具、CSS 缓存版本契约同步后重跑。
- 本次仅移除入口，不影响经独立页面顶部导航访问服务器管理和凭证管理。

## 本次变更测试结果：聚焦本次训练并保留完整训练记录（2026-10-30）

### Git 基准点与范围

- **Commit Hash**：a5fdf78（Preserve training page navigation cleanup）。
- **测试日期 / 分支**：2026-10-30 / emdash/hot-boxes-clean-y9u6p。
- **变更范围**：训练台主区默认聚焦运行中/排队中/暂停中的任务，仅显示当前任务；“全部训练”弹窗保留全部历史搜索、状态筛选和切换；详情新增完整训练记录页，展示参数、生命周期、执行资源与分段、逐轮指标、性能及错误。未修改 API、数据格式或依赖。

### 验收标准—测试用例映射

| 验收标准 | 可执行验证 | 结果与覆盖场景 |
|---|---|---|
| 当前/活跃训练优先呈现，主区不随历史任务增长 | frontend/workbench-interactions.test.cjs：“首次聚焦活跃任务，历史弹窗保留完整卡片并复用焦点” | 通过；活跃优先、已选任务保持、无活跃时回退、单卡主区、轮询保焦 |
| 全部训练仍可检索、筛选和切换 | 同文件：“搜索和状态筛选组合在完整训练弹窗中工作…”、“历史选择关闭弹窗并切换当前训练…”、“Escape 关闭历史弹窗并恢复入口焦点” | 通过；组合筛选、空集合、恶意名称转义、历史选择、键盘关闭与焦点恢复 |
| 训练过程详细且兼容旧任务 | 同文件：“训练记录展示配置、完整指标、性能和跨节点执行段…”、“旧任务缺少时间、性能和执行段时记录页提供明确空状态” | 通过；配置/摘要/指标/吞吐、执行分段、失败原因、恶意文本转义、缺字段兼容 |
| 页面行为与已有日志/预测/标签兼容 | node --test frontend/*.test.cjs | 124 项通过；包括日志轮询、任务切换、预测响应隔离及键盘标签导航回归 |
| 服务提供最新页面 | docker compose up --build -d、训练页内容核对、/api/health | 4 个服务运行；页面含“本次训练”“全部训练”“训练记录”，健康接口返回 {"ok":true} |

### 测试执行结果

| 阶段 / 实际命令 | 通过 | 失败 | 跳过 | 说明 |
|---|---:|---:|---:|---|
| 训练台专项 node --test frontend/workbench-interactions.test.cjs | 27 | 0 | 0 | 覆盖本次聚焦、历史弹窗和完整记录 |
| 前端完整 node --test frontend/*.test.cjs | 124 | 0 | 0 | Node 内置测试 |
| 后端完整 docker run --rm --pull=never --network none --mount type=bind,src="$PWD",dst=/workspace,readonly --workdir /workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 emdash-hot-boxes-clean-y9u6p-trainer:latest python -m pytest tests/ -q -p no:cacheprovider --basetemp=/tmp/mtp-training-focus-full --tb=short -rs | 617 | 0 | 31 | Python 3.12；跳过项依赖 Redis、S3、真实 GPU/NCCL；2 条既有弃用警告 |
| 页面脚本语法与差异检查 | 2 | 0 | 0 | Node vm.Script 校验内联脚本，git diff --check 通过 |
| 容器重建和 HTTP 核对 | 4 服务 | 0 | 0 | docker compose up --build -d 成功；健康接口和静态页面均可访问 |

### 已知限制与下次测试建议

- 当前没有可用的浏览器自动化会话，因此未完成人工桌面/移动端截图检查；已完成响应内容、交互测试和服务健康核对。
- Redis/S3/GPU/NCCL 条件测试未在本环境配置，相关分布式和硬件路径仍需在对应环境验证。
- 若未来更改训练详情 API 或记录格式，应补充 API 兼容测试；本次使用的现有详情字段已由完整后端回归覆盖。
