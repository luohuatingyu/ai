# 测试报告

## 本次变更测试结果：远程操作日志显示脚本输出（2026-10-28）

**变更范围**：远程服务器执行日志增加 `[远程服务器启动配置]` 摘要，并把远端脚本最终 stdout/stderr 追加到 `operation.log`，确保配置更新后的实际配置摘要在日志窗口可见；训练任务日志增加 `[训练任务启动配置]` 摘要。所有日志均对连接凭证、令牌和密钥字段脱敏。

**测试环境**：Docker Compose，容器 Python 3.12.15。完整测试基于当前已提交版本临时归档运行；工作区预先存在、未纳入本次提交的 `backend/app/storage.py` 和 `backend/tests/test_storage.py` 修改未参与测试。

**测试执行结果**：
- API 与远程 Worker 定向测试：129 通过、0 失败、0 错误、0 跳过。
- 完整测试套件：267 通过、6 跳过、0 失败、0 错误。跳过项由 Redis、S3 与 Supervisor 子进程环境条件控制。
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

**关键模块测试**：API 与远程 Worker 129/129 通过；完整套件 267/273 通过。

**已知问题与限制**：未连接真实 SSH 服务器；远程启动配置和脚本输出通过隔离执行器及脱敏断言验证。

**下次测试建议**：连接一台测试 Linux 主机，确认真实 start/restart 后日志窗口能看到启动配置摘要，并核对目标机实际生效的 worker `.env` 与日志脱敏结果。

**Git基准点**：`8a65551`（Include remote script output in operation logs）。

## Git基准点

- **Commit Hash**: 8a65551（远程操作日志显示脚本输出）
- **提交信息**: Include remote script output in operation logs
- **测试日期**: 2026-10-27
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
