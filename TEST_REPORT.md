# 测试报告

## Git基准点

- **Commit Hash**: 9b3ba84（远程服务器与 SSH 凭证 MySQL 持久化）
- **提交信息**: Persist remote management config in MySQL
- **测试日期**: 2026-10-26
- **分支**: emdash/hot-boxes-clean-y9u6p

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
