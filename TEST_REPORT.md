# 测试报告

## Git基准点

- **Commit Hash**: a1d852a（本次测试覆盖代码与测试用例的提交）
- **提交信息**: Add page-managed SSH credentials
- **测试日期**: 2026-10-25
- **分支**: emdash/hot-boxes-clean-y9u6p

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
