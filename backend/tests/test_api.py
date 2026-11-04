"""API 端到端（AC1/AC8/AC9 控制面）+ ML 平台特有场景。

- 训练链路：提交 → 训练 → 指标（accuracy/R²）/产物（best.pt）断言；
- 预测一致性：同一输入对同一 best.pt 的预测结果稳定；
- 数据格式兼容：旧扁平布局在 API 全链路可用；
- 预处理一致性：续训后预测列映射与训练期一致（含 one-hot/数值列）。
"""
from __future__ import annotations

import json
import io
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from app import config, jobstore  # noqa: E402
from app.storage import get_store  # noqa: E402
from app.storage import job_key  # noqa: E402
from tests.conftest import job_status, wait_until  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_remote_management_mysql(remote_mysql_db):
    """为 API 测试提供独立 SQLite 会话，覆盖 MySQL 远程配置仓储。"""
    yield


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient（嵌入式模式：manager 内嵌 worker 线程）。"""
    from app.main import app, manager

    with fastapi_testclient.TestClient(app) as c:
        yield c
    manager.stop()


def _train_payload(ds_id: str, epochs: int = 4, **extra) -> dict:
    body = {"dataset_id": ds_id, "target": "target", "task": "classification",
            "epochs": epochs, "batch_size": 64, "lr": 0.01, "hidden_size": 16,
            "hidden_layers": 1, "test_split": 0.2, "seed": 42}
    body.update(extra)
    return body


def _enable_remote_mysql(monkeypatch) -> None:
    """为当前远程管理用例开启 MySQL 配置仓储。"""
    monkeypatch.setattr(config, "USE_DATABASE", True)


# ---------------------------------------------------------------------------
# 训练链路（AC：提交 → 训练 → 指标/产物）
# ---------------------------------------------------------------------------

def test_health_and_system(client):
    assert client.get("/api/health").status_code == 200
    sysinfo = client.get("/api/system").json()
    assert "device" in sysinfo


def test_workers_api_reports_code_fingerprint_and_framework_capabilities(client):
    """能力上报链路：/api/workers 透传代码指纹与框架能力，供页面比对与排查。"""
    workers = client.get("/api/clusters/workers").json()["workers"]
    assert workers, "嵌入式模式至少应有一个计算槽"
    for worker in workers:
        assert isinstance(worker.get("code_fingerprint"), str) and worker["code_fingerprint"]
        versions = worker.get("framework_versions") or {}
        assert "torch" in versions and "xgboost" in versions
        assert "torch" in (worker.get("frameworks") or [])


@pytest.mark.parametrize("state,active,occupied,expected", [
    ("idle", True, True, "reserved"),
    ("disabled", True, True, "reserved"),
    ("starting", True, True, "starting"),
    ("training", True, True, "training"),
    ("idle", True, False, "idle"),
    ("training", False, True, "offline"),
])
def test_resource_api_reports_exclusive_reservations(client, monkeypatch, ls, state, active, occupied, expected):
    """资源 API 以真实槽位租约补齐首次心跳窗口，离线仍优先显示离线。"""
    from app.state import manager

    monkeypatch.setattr(manager, "lease", ls)
    ls.register_worker("api-sync-cpu", {"state": state}, 30)
    if occupied:
        assert ls.acquire("slot:api-sync-cpu", "owner", 30)
    if not active:
        ls._workers["api-sync-cpu"]["last_seen"] = time.time() - 1000
    response = client.get("/api/clusters/workers")
    assert response.status_code == 200
    worker, = response.json()["workers"]
    assert worker["state"] == expected and worker["occupied"] is occupied
    assert worker["active"] is active


def test_static_paths_not_shadowed_by_param_route(client):
    """回归：/api/datasets/tags 与 import-history 不得被 /datasets/{ds_id} 遮蔽。"""
    r = client.get("/api/datasets/tags")
    assert r.status_code == 200, f"tags 端点应可达: {r.status_code}"
    assert "tags" in r.json()
    r = client.get("/api/datasets/import-history")
    assert r.status_code == 200, f"import-history 端点应可达: {r.status_code}"
    assert "history" in r.json()


def test_remote_operation_cancel_cancels_queued_operation(
    client, monkeypatch
):
    """无管理令牌时也可中断排队操作，且状态和服务器锁正确更新。"""
    from app.routers import remote as jobs_router
    from app.state import manager

    operation_id = "d" * 32
    server = {"machine_id": "remote-cancel-api", "status": "provisioning"}
    monkeypatch.setattr(
        jobs_router.remote_config_repository,
        "get_remote_server",
        lambda _machine_id: dict(server),
    )
    monkeypatch.setattr(
        jobs_router.remote_config_repository,
        "save_remote_server",
        lambda updated: server.update(updated),
    )
    manager.lease.set_remote_operation(
        operation_id,
        {
            "operation_id": operation_id,
            "machine_id": server["machine_id"],
            "action": "install",
            "status": "queued",
        },
    )

    cancelled = client.post(f"/api/remote-operations/{operation_id}/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"
    assert manager.lease.get_remote_operation(operation_id)["status"] == "cancelled"
    assert manager.lease.is_remote_operation_cancel_requested(operation_id) is False
    assert server["status"] == "error"


def test_remote_operation_logs_can_be_cleared_without_changing_state(client):
    """清除远程执行日志后保留操作状态和阶段元数据。"""
    from app import remote_workers
    from app.state import manager

    operation_id = "a" * 32
    manager.lease.set_remote_operation(
        operation_id,
        {
            "operation_id": operation_id,
            "machine_id": "remote-clear-logs-api",
            "action": "restart",
            "status": "running",
            "phase": "执行远程部署脚本",
            "log": "旧阶段输出",
            "output": "旧标准输出",
            "stderr": "旧错误输出",
            "error": "旧错误",
        },
    )

    response = client.delete(f"/api/remote-operations/{operation_id}/logs")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "operation_id": operation_id}
    cleared = manager.lease.get_remote_operation(operation_id)
    assert cleared["status"] == "running"
    assert cleared["phase"] == "执行远程部署脚本"
    assert cleared["log"] == ""
    assert cleared["output"] == ""
    assert cleared["stderr"] == ""
    assert cleared["error"] is None

    remote_workers._append_remote_operation_log(operation_id, "新阶段", "清除后新产生的日志")
    updated = manager.lease.get_remote_operation(operation_id)
    assert updated["status"] == "running"
    assert "清除后新产生的日志" in updated["log"]


def test_remote_operation_log_clear_returns_404_for_missing_operation(client):
    """不存在的远程操作不得创建记录或返回清除成功。"""
    # 使用与其它操作用例不同的 ID，避免跨模块共享协调存储时误命中已取消操作。
    response = client.delete(f"/api/remote-operations/{'9' * 32}/logs")

    assert response.status_code == 404


def test_remote_operation_cancel_marks_active_operation_as_cancelling(client, monkeypatch):
    """执行中的操作收到取消请求后保留忙碌状态，直到 SSH 执行器确认退出。"""
    from app import remote_workers
    from app.state import manager

    operation_id = "e" * 32
    manager.lease.set_remote_operation(
        operation_id,
        {
            "operation_id": operation_id,
            "machine_id": "remote-active-cancel-api",
            "action": "restart",
            "status": "running",
        },
    )
    cancellation_event = remote_workers._register_remote_operation(operation_id)
    try:
        response = client.post(f"/api/remote-operations/{operation_id}/cancel")
        assert response.status_code == 202
        assert response.json()["status"] == "cancelling"
        status = client.get(f"/api/remote-operations/{operation_id}").json()
        assert status["status"] == "running"
        assert status["cancel_requested"] is True
        assert cancellation_event.is_set()
    finally:
        remote_workers._unregister_remote_operation(operation_id, cancellation_event)


def test_remote_operation_cancel_clears_stale_running_state(client, monkeypatch):
    """原执行线程已消失时，管理员仍可中断遗留 running 状态并解锁服务器。"""
    from app.routers import remote as jobs_router
    from app.state import manager

    operation_id = "f" * 32
    server = {"machine_id": "remote-stale-cancel-api", "status": "running"}
    monkeypatch.setattr(
        jobs_router.remote_config_repository,
        "get_remote_server",
        lambda _machine_id: dict(server),
    )
    monkeypatch.setattr(
        jobs_router.remote_config_repository,
        "save_remote_server",
        lambda updated: server.update(updated),
    )
    manager.lease.set_remote_operation(
        operation_id,
        {
            "operation_id": operation_id,
            "machine_id": server["machine_id"],
            "action": "restart",
            "status": "running",
        },
    )

    response = client.post(f"/api/remote-operations/{operation_id}/cancel")

    assert response.status_code == 202
    assert response.json()["status"] == "cancelled"
    assert manager.lease.get_remote_operation(operation_id)["status"] == "cancelled"
    assert manager.lease.is_remote_operation_cancel_requested(operation_id) is True
    assert server["status"] == "error"
    manager.lease.clear_remote_operation_cancel(operation_id)


def test_remote_server_api_separates_registration_deployment_and_config_update(client, monkeypatch, request):
    """新增只登记配置；已部署修改配置和部署代码分别触发独立远程动作。"""
    from app.routers import remote as jobs_router
    from app import remote_workers
    from app import remote_config_repository
    from app.state import manager
    from cryptography.fernet import Fernet

    machine_id = f"remote-api-{time.time_ns()}"
    calls = []
    _enable_remote_mysql(monkeypatch)
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "REDIS_URL", "redis://redis.example.net:6379/0")
    monkeypatch.setattr(config, "WORKER_REMOTE_REDIS_URL", "")
    monkeypatch.setattr(config, "WORKER_REMOTE_S3_ENDPOINT", "")
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "registry.example.net/platform/worker:unit")
    monkeypatch.setattr(config, "S3_ENDPOINT", "platform-s3.example.net:9000")
    monkeypatch.setattr(config, "S3_BUCKET", "platform-artifacts")
    monkeypatch.setattr(config, "S3_SECURE", True)
    monkeypatch.setattr(manager, "distributed", True)
    manager.lease.delete_remote_ssh_credentials()
    request.addfinalizer(manager.lease.delete_remote_ssh_credentials)
    credential = client.post(
        "/api/remote-ssh-credentials",
        json={
            "name": "API test credential",
            "username": "credential-user",
            "auth_method": "password",
            "password": "synthetic-test-password",
        },
    )
    assert credential.status_code == 200, credential.text
    credential_id = credential.json()["credentials"][0]["id"]
    monkeypatch.setattr(jobs_router, "remote_configuration_errors", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        remote_workers,
        "run_remote_action",
        lambda server, action, _operation_id=None: calls.append((server["machine_id"], action, server["username"])) or {"stdout": "done"},
    )
    payload = {
        "machine_id": machine_id,
        "host": "compute.example.net",
        "username": "worker",
        "ssh_credential_id": credential_id,
        "port": 2222,
        "mode": "gpu",
        "cpu_slots": 0,
        "remote_redis_url": "rediss://worker:synthetic-redis-password@redis.example.net:6380/4",
        "remote_s3_endpoint": "remote-storage.example.net:9000",
        "docker_registry": "docker.m.daocloud.io",
        "pip_index_url": "https://mirrors.aliyun.com/pypi/simple/",
        "apt_mirror": "mirrors.aliyun.com",
    }
    added = client.post("/api/remote-servers", json=payload)
    assert added.status_code == 201
    assert "operation_id" not in added.json()
    assert calls == []
    assert added.json()["server"]["deployed"] is False

    stored_server = remote_config_repository.get_remote_server(machine_id)
    assert stored_server["remote_redis_url_encrypted"] != payload["remote_redis_url"]
    assert payload["remote_redis_url"] not in repr(stored_server)

    servers = client.get("/api/remote-servers").json()["servers"]
    server = next(item for item in servers if item["machine_id"] == machine_id)
    assert server["host"] == payload["host"]
    assert server["username_override"] == "worker"
    assert server["ssh_credential_id"] == credential_id
    assert server["remote_s3_endpoint_override"] == payload["remote_s3_endpoint"]
    assert server["docker_registry"] == "docker.m.daocloud.io"
    assert server["pip_index_url"] == "https://mirrors.aliyun.com/pypi/simple/"
    assert server["apt_mirror"] == "mirrors.aliyun.com"
    assert "worker_image_override" not in server
    assert server["has_remote_redis_url_override"] is True
    assert server["deployed"] is False
    assert "remote_redis_url_encrypted" not in server
    assert payload["remote_redis_url"] not in repr(server)
    assert "private_key" not in server and "token" not in server

    effective = client.get(f"/api/remote-servers/{machine_id}/effective-config")
    assert effective.status_code == 200, effective.text
    effective_config = effective.json()["config"]
    assert effective_config["worker_image"] == "registry.example.net/platform/worker:unit"
    assert effective_config["worker_image_source"] == "WORKER_REMOTE_IMAGE"
    assert effective_config["redis_endpoint"] == "rediss://redis.example.net:6380/4"
    assert effective_config["redis_source"] == "server_override"
    assert effective_config["redis_auth_configured"] is True
    assert effective_config["s3_endpoint"] == "remote-storage.example.net:9000"
    assert effective_config["s3_source"] == "server_override"
    assert "synthetic-redis-password" not in repr(effective_config)

    edited = client.patch(
        f"/api/remote-servers/{machine_id}",
        json={
            "remote_redis_url": "",
            "remote_s3_endpoint": "",
        },
    )
    assert edited.status_code == 200, edited.text
    edited_server = remote_config_repository.get_remote_server(machine_id)
    assert "worker_image_override" not in edited_server
    assert edited_server["remote_s3_endpoint_override"] is None
    assert "remote_redis_url_encrypted" not in edited_server
    assert edited.json()["server"]["has_remote_redis_url_override"] is False
    assert calls == []

    fallback_config = client.get(
        f"/api/remote-servers/{machine_id}/effective-config"
    ).json()["config"]
    assert fallback_config["redis_endpoint"] == "redis://redis.example.net:6379/0"
    assert fallback_config["redis_source"] == "REDIS_URL"
    assert fallback_config["s3_endpoint"] == "platform-s3.example.net:9000"
    assert fallback_config["s3_source"] == "S3_ENDPOINT"
    assert client.get("/api/remote-servers/missing-effective-config/effective-config").status_code == 404

    assert client.post("/api/remote-servers", json=payload).status_code == 409

    deployed = client.post(f"/api/remote-servers/{machine_id}/install")
    assert deployed.status_code == 202
    operation_id = deployed.json()["operation_id"]
    operation = client.get(f"/api/remote-operations/{operation_id}").json()
    assert operation["status"] == "completed"
    assert operation["output"] == "done"
    assert calls == [(machine_id, "install", "worker")]
    servers = client.get("/api/remote-servers").json()["servers"]
    server = next(item for item in servers if item["machine_id"] == machine_id)
    assert server["deployed"] is True
    assert "remote_redis_url_encrypted" not in server
    assert payload["remote_redis_url"] not in repr(server)
    assert "private_key" not in server and "token" not in server

    updated = client.patch(
        f"/api/remote-servers/{machine_id}",
        json={
            "remote_s3_endpoint": "storage-v3.example.net:9000",
            "docker_registry": "docker.m.daocloud.io",
            "apt_mirror": "",
        },
    )
    assert updated.status_code == 202, updated.text
    update_operation = client.get(
        f"/api/remote-operations/{updated.json()['operation_id']}"
    ).json()
    assert update_operation["status"] == "completed"
    assert update_operation["action"] == "config"
    assert calls[-1] == (machine_id, "config", "worker")
    edited_record = remote_config_repository.get_remote_server(machine_id)
    assert edited_record["remote_s3_endpoint_override"] == "storage-v3.example.net:9000"
    assert edited_record["docker_registry"] == "docker.m.daocloud.io"
    assert edited_record["apt_mirror"] is None
    assert edited_record["pip_index_url"] == "https://mirrors.aliyun.com/pypi/simple/"

    for action in ("install", "deploy", "start", "stop", "restart"):
        result = client.post(f"/api/remote-servers/{machine_id}/{action}")
        assert result.status_code == 202
        current = client.get(
            f"/api/remote-operations/{result.json()['operation_id']}"
        ).json()
        assert current["status"] == "completed"
        expected_remote_action = "deploy" if action == "install" else action
        assert calls[-1] == (machine_id, expected_remote_action, "worker")
        current_server = next(
            item for item in client.get("/api/remote-servers").json()["servers"]
            if item["machine_id"] == machine_id
        )
        assert current_server["deployed"] is True

    assert client.post(
        f"/api/remote-servers/missing-{machine_id}/start"
    ).status_code == 404
    remote_config_repository.delete_remote_server(machine_id)


def test_remote_server_api_rejects_shell_injection_before_ssh(client, monkeypatch):
    """非法主机字段由请求模型拒绝，不进入 SSH 执行器。"""
    from app.routers import remote as jobs_router

    monkeypatch.setattr(jobs_router, "remote_configuration_errors", lambda *_args, **_kwargs: [])
    response = client.post(
        "/api/remote-servers",
        json={
            "machine_id": "remote-injection-test",
            "host": "compute.example.net;touch /tmp/pwned",
            "username": "worker",
            "port": 22,
            "mode": "cpu",
            "cpu_slots": 2,
        },
    )
    assert response.status_code == 422


def test_remote_server_uses_credential_username_when_server_username_is_blank(client, monkeypatch, request):
    """未填写服务器用户名则使用凭证用户名，并在服务器记录中保存凭证引用。"""
    from cryptography.fernet import Fernet
    from app import remote_workers
    from app import remote_config_repository
    from app.routers import remote as jobs_router
    from app.state import manager

    _enable_remote_mysql(monkeypatch)
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "REDIS_URL", "redis://redis.example.net:6379/0")
    monkeypatch.setattr(manager, "distributed", True)
    manager.lease.delete_remote_ssh_credentials()
    request.addfinalizer(manager.lease.delete_remote_ssh_credentials)
    credential_response = client.post(
        "/api/remote-ssh-credentials",
        json={
            "name": "default username test",
            "username": "credential-user",
            "auth_method": "password",
            "password": "synthetic-password",
        },
    )
    assert credential_response.status_code == 200, credential_response.text
    credential_id = credential_response.json()["credentials"][0]["id"]
    monkeypatch.setattr(jobs_router, "remote_configuration_errors", lambda *_args, **_kwargs: [])
    run_calls = []
    monkeypatch.setattr(
        remote_workers,
        "run_remote_action",
        lambda server, action, _operation_id=None: run_calls.append((server["username"], action)) or {"stdout": "done"},
    )

    machine_id = f"remote-default-user-{time.time_ns()}"
    response = client.post(
        "/api/remote-servers",
        json={
            "machine_id": machine_id,
            "host": "compute.example.net",
            "ssh_credential_id": credential_id,
            "port": 22,
            "mode": "cpu",
            "cpu_slots": 2,
        },
    )

    assert response.status_code == 201, response.text
    assert run_calls == []
    assert response.json()["server"]["deployed"] is False
    server = remote_config_repository.get_remote_server(machine_id)
    assert server["username"] == "credential-user"
    assert server["username_override"] is None
    assert server["ssh_credential_id"] == credential_id
    remote_config_repository.delete_remote_server(machine_id)


def test_remote_ssh_credential_catalog_supports_all_auth_modes_without_secret_echo(client, monkeypatch, request):
    """页面可加密管理三类 SSH 凭证，目录接口不回传秘密且引用中的凭证不可删除。"""
    from cryptography.fernet import Fernet
    from app import remote_config_repository
    from app.state import manager

    _enable_remote_mysql(monkeypatch)
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", encryption_key)
    monkeypatch.setattr(config, "REDIS_URL", "redis://redis.example.net:6379/0")
    monkeypatch.setattr(manager, "distributed", True)
    manager.lease.delete_remote_ssh_credentials()
    request.addfinalizer(manager.lease.delete_remote_ssh_credentials)

    secret_password = "synthetic-password-secret"
    private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic-key-material\n-----END OPENSSH PRIVATE KEY-----"
    payloads = [
        {"name": "password-only", "username": "pw-user", "auth_method": "password", "password": secret_password},
        {"name": "key-only", "username": "key-user", "auth_method": "key", "private_key": private_key},
        {
            "name": "key-then-password",
            "username": "both-user",
            "auth_method": "key_password",
            "private_key": private_key,
            "password": secret_password,
            "passphrase": "synthetic-passphrase",
            "make_default": True,
        },
    ]
    created = []
    for payload in payloads:
        response = client.post("/api/remote-ssh-credentials", json=payload)
        assert response.status_code == 200, response.text
        created.append(response.json())
        assert secret_password not in response.text
        assert private_key not in response.text

    listed = client.get("/api/remote-ssh-credentials")
    assert listed.status_code == 200
    catalog = listed.json()
    assert [item["auth_method"] for item in catalog["credentials"]] == ["password", "key", "key_password"]
    assert catalog["default_credential_id"] == catalog["credentials"][2]["id"]
    assert secret_password not in listed.text and private_key not in listed.text

    ciphertext = remote_config_repository.get_ssh_credentials_ciphertext()
    decrypted = Fernet(encryption_key.encode()).decrypt(ciphertext.encode()).decode()
    decrypted_catalog = json.loads(decrypted)
    assert secret_password in decrypted
    assert decrypted_catalog["credentials"][1]["private_key"].startswith(private_key.splitlines()[0])

    referenced_id = catalog["credentials"][2]["id"]
    machine_id = f"credential-reference-{time.time_ns()}"
    remote_config_repository.save_remote_server(
        {"machine_id": machine_id, "ssh_credential_id": referenced_id}
    )
    blocked = client.delete(f"/api/remote-ssh-credentials/{referenced_id}")
    assert blocked.status_code == 409
    remote_config_repository.delete_remote_server(machine_id)

    updated = client.put(
        f"/api/remote-ssh-credentials/{created[0]['credentials'][0]['id']}",
        json={
            "name": "password-only-updated",
            "username": "pw-user-new",
            "auth_method": "password",
            "password": "replacement-secret",
        },
    )
    assert updated.status_code == 200, updated.text
    assert "replacement-secret" not in updated.text
    deleted = client.delete(f"/api/remote-ssh-credentials/{created[1]['credentials'][1]['id']}")
    assert deleted.status_code == 200
    assert len(deleted.json()["credentials"]) == 2


def test_remote_ssh_settings_encrypt_credentials_and_never_return_secrets(client, monkeypatch, request):
    """SSH 设置无需管理令牌即可加密持久化，读取/清除接口不回传秘密。"""
    from cryptography.fernet import Fernet

    from app.remote_workers import _platform_ssh_credentials
    from app import remote_config_repository
    from app.state import manager

    _enable_remote_mysql(monkeypatch)
    password = "platform SSH password with spaces and $ chars"
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", encryption_key)
    monkeypatch.setattr(config, "REDIS_URL", "redis://redis.example.net:6379/0")
    monkeypatch.setattr(manager, "distributed", True)
    manager.lease.delete_remote_ssh_credentials()
    request.addfinalizer(manager.lease.delete_remote_ssh_credentials)

    payload = {"auth_method": "password", "password": password}
    saved = client.put("/api/remote-ssh-settings", json=payload)
    assert saved.status_code == 200, saved.text
    ciphertext = remote_config_repository.get_ssh_credentials_ciphertext()
    assert ciphertext and password not in ciphertext
    assert password.encode() not in ciphertext.encode()
    assert Fernet(encryption_key.encode()).decrypt(ciphertext.encode()).decode().find(password) >= 0
    assert saved.json() == {
        "source": "page",
        "auth_method": "password",
        "configured": True,
        "can_configure": True,
    }

    readback = client.get("/api/remote-ssh-settings")
    assert readback.status_code == 200
    assert password not in readback.text
    assert _platform_ssh_credentials()["password"] == password

    cleared = client.delete("/api/remote-ssh-settings")
    assert cleared.status_code == 200, cleared.text
    assert remote_config_repository.get_ssh_credentials_ciphertext() is None
    assert cleared.json()["source"] == "none"
    assert password not in cleared.text


def test_remote_ssh_settings_save_private_key_without_echo(client, monkeypatch, request):
    """页面密钥保存后返回状态而非私钥文本，MySQL 中只保留加密内容。"""
    from cryptography.fernet import Fernet
    from app import remote_config_repository

    from app.state import manager

    _enable_remote_mysql(monkeypatch)
    private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "test-key-material-that-must-not-be-echoed\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", encryption_key)
    monkeypatch.setattr(config, "REDIS_URL", "redis://redis.example.net:6379/0")
    monkeypatch.setattr(manager, "distributed", True)
    manager.lease.delete_remote_ssh_credentials()
    request.addfinalizer(manager.lease.delete_remote_ssh_credentials)

    response = client.put(
        "/api/remote-ssh-settings",
        json={"auth_method": "key", "private_key": private_key},
    )

    assert response.status_code == 200, response.text
    assert private_key not in response.text
    assert "test-key-material-that-must-not-be-echoed" not in response.text
    ciphertext = remote_config_repository.get_ssh_credentials_ciphertext()
    assert ciphertext and private_key not in ciphertext
    stored = json.loads(Fernet(encryption_key.encode()).decrypt(ciphertext.encode()).decode())
    assert stored["credentials"][0]["private_key"] == private_key + "\n"


@pytest.mark.parametrize(
    "payload",
    [
        {"auth_method": "password", "password": ""},
        {"auth_method": "password", "password": "first\nsecond"},
        {"auth_method": "key", "private_key": "not a key"},
        {"auth_method": "key", "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"},
    ],
)
def test_remote_ssh_settings_reject_invalid_credentials(client, monkeypatch, request, payload):
    """空密码、多行密码及不完整/非法密钥在保存前拒绝。"""
    from app import remote_config_repository

    _enable_remote_mysql(monkeypatch)
    from cryptography.fernet import Fernet
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())

    response = client.put(
        "/api/remote-ssh-settings",
        json=payload,
    )
    assert response.status_code == 422
    attempted_secret = payload.get("password") or payload.get("private_key") or ""
    if attempted_secret:
        assert attempted_secret not in response.text
    assert remote_config_repository.get_ssh_credentials_ciphertext() is None


def test_remote_ssh_settings_require_mysql_and_encryption_key(client, monkeypatch, request):
    """未启用 MySQL 或未设置有效加密密钥时页面不能持久化凭证。"""
    from app.state import manager

    payload = {"auth_method": "password", "password": "test-password"}
    manager.lease.delete_remote_ssh_credentials()
    request.addfinalizer(manager.lease.delete_remote_ssh_credentials)

    monkeypatch.setattr(config, "USE_DATABASE", False)
    monkeypatch.setattr(manager, "distributed", False)
    no_database = client.put("/api/remote-ssh-settings", json=payload)
    assert no_database.status_code == 503
    assert "MySQL" in no_database.text

    monkeypatch.setattr(config, "USE_DATABASE", True)
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", "")
    no_key = client.put("/api/remote-ssh-settings", json=payload)
    assert no_key.status_code == 503
    assert "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY" in no_key.text
    from app import remote_config_repository
    remote_config_repository.delete_ssh_credentials()


def test_remote_ssh_settings_redacts_mysql_read_failure(client, monkeypatch):
    """MySQL 读取异常以脱敏 503 返回，不泄漏底层错误内容。"""
    from app import remote_workers

    def fail_status():
        raise OSError("redis password leaked")

    monkeypatch.setattr(remote_workers, "remote_ssh_settings_status", fail_status)
    response = client.get("/api/remote-ssh-settings")

    assert response.status_code == 503
    assert "redis password leaked" not in response.text
    assert "MySQL" in response.text


def test_import_json_dataset(client, store):
    """正常场景：JSON 记录数组导入后可预览，并保留列值。"""
    content = b'[{"feature": 1, "target": "a"}, {"feature": 2, "target": "b"}]'
    response = client.post(
        "/api/datasets/import",
        files={"file": ("records.json", content, "application/json")},
    )

    assert response.status_code == 200, response.text
    dataset_id = response.json()["id"]
    detail = client.get(f"/api/datasets/{dataset_id}").json()
    assert detail["columns"] == ["feature", "target"]
    assert detail["rows"] == 2
    assert detail["preview"][1]["feature"] == 2


@pytest.mark.parametrize(
    "filename,content",
    [("broken.json", b"{"), ("table.parquet", b"not-a-supported-format")],
)
def test_import_rejects_invalid_or_unsupported_files(client, store, filename, content):
    """异常场景：格式错误或不支持的文件返回 400，且不创建数据集。"""
    response = client.post(
        "/api/datasets/import",
        files={"file": (filename, content, "application/octet-stream")},
    )

    assert response.status_code == 400
    assert client.get("/api/datasets").json()["total"] == 0


def test_import_rolls_back_when_object_store_sync_fails(client, store, monkeypatch):
    """异常场景：远端同步失败时回滚本地数据集和导入历史。"""
    content = b'[{"feature": 1, "target": "a"}]'

    def fail_sync(_dataset_id):
        raise OSError("对象存储不可用")

    monkeypatch.setattr("app.routers.datasets._sync_dataset_csv_to_store", fail_sync)
    response = client.post(
        "/api/datasets/import",
        files={"file": ("records.json", content, "application/json")},
    )

    assert response.status_code == 503
    assert client.get("/api/datasets").json()["total"] == 0
    assert client.get("/api/datasets/import-history").json()["total"] == 0


def test_batch_import_syncs_json_and_csv_datasets(client, store):
    """正常场景：批量接口接受不同格式并为每个数据集建立可访问记录。"""
    files = [
        ("files", ("records.json", b'[{"feature": 1, "target": "a"}]', "application/json")),
        ("files", ("records.csv", b"feature,target\n2,b\n", "text/csv")),
    ]
    response = client.post("/api/datasets/batch-import", files=files)

    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["success"]) == 2
    assert result["failed"] == []
    for item in result["success"]:
        detail = client.get(f"/api/datasets/{item['dataset_id']}")
        assert detail.status_code == 200
        assert detail.json()["rows"] == 1


@pytest.mark.parametrize("archive_name,archive_kind", [("rows.zip", "zip"), ("rows.tar.gz", "tar.gz")])
def test_import_archive_dataset(client, store, archive_name, archive_kind):
    """正常场景：ZIP 与 TAR.GZ 解包后导入 CSV，且返回可访问的数据集。"""
    csv_bytes = b"feature,target\n1,a\n2,b\n"
    payload = io.BytesIO()
    if archive_kind == "zip":
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("nested/records.csv", csv_bytes)
    else:
        info = tarfile.TarInfo("nested/records.csv")
        info.size = len(csv_bytes)
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            archive.addfile(info, io.BytesIO(csv_bytes))

    response = client.post(
        "/api/datasets/import",
        files={"file": (archive_name, payload.getvalue(), "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["imported_count"] == 1
    dataset_id = result["datasets"][0]["id"]
    assert client.get(f"/api/datasets/{dataset_id}").json()["rows"] == 2


def test_import_archive_rejects_unsafe_member_path(client, store):
    """安全场景：压缩包成员不能通过路径穿越写出导入临时目录。"""
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../outside.csv", "feature,target\n1,a\n")

    response = client.post(
        "/api/datasets/import",
        files={"file": ("unsafe.zip", payload.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
    assert "路径" in response.json()["detail"]
    assert client.get("/api/datasets").json()["total"] == 0


def test_import_rejects_oversized_upload(client, store, monkeypatch):
    """边界场景：上传超过限额的文件按块写入时立即拒绝，不创建数据集。"""
    monkeypatch.setattr(config, "DATASET_MAX_FILE_SIZE", 64)
    content = b"feature,target\n" + b"1,a\n" * 64
    response = client.post(
        "/api/datasets/import",
        files={"file": ("big.csv", content, "text/csv")},
    )

    assert response.status_code == 400
    assert "过大" in response.json()["detail"]
    assert client.get("/api/datasets").json()["total"] == 0


def test_validate_endpoint_rejects_oversized_upload_and_cleans_temp(client, store, monkeypatch):
    """边界场景：校验接口同样受限，且不残留临时文件。"""
    from app.state import dataset_importer

    monkeypatch.setattr(config, "DATASET_MAX_FILE_SIZE", 32)
    response = client.post(
        "/api/datasets/validate",
        files={"file": ("big.csv", b"feature,target\n" + b"1,a\n" * 32, "text/csv")},
    )

    assert response.status_code == 400
    assert list(dataset_importer.temp_dir.glob("*")) == []


def test_import_rejects_row_limit(client, store, monkeypatch):
    """边界场景：超过行数上限的数据集拒绝入库。"""
    monkeypatch.setattr(config, "DATASET_MAX_ROWS", 1)
    response = client.post(
        "/api/datasets/import",
        files={"file": ("rows.csv", b"feature,target\n1,a\n2,b\n", "text/csv")},
    )

    assert response.status_code == 400
    assert "行数过多" in response.json()["detail"]
    assert client.get("/api/datasets").json()["total"] == 0


def test_import_rejects_column_limit(client, store, monkeypatch):
    """边界场景：超过列数上限的数据集拒绝入库。"""
    monkeypatch.setattr(config, "DATASET_MAX_COLUMNS", 1)
    response = client.post(
        "/api/datasets/import",
        files={"file": ("wide.csv", b"feature,target\n1,a\n", "text/csv")},
    )

    assert response.status_code == 400
    assert "列数过多" in response.json()["detail"]


def test_batch_import_rejects_too_many_files(client, store, monkeypatch):
    """边界场景：批量导入文件数超过上限时整体拒绝。"""
    monkeypatch.setattr(config, "DATASET_MAX_IMPORT_FILES", 1)
    files = [
        ("files", ("a.csv", b"feature,target\n1,a\n", "text/csv")),
        ("files", ("b.csv", b"feature,target\n2,b\n", "text/csv")),
    ]
    response = client.post("/api/datasets/batch-import", files=files)

    assert response.status_code == 400
    assert "文件过多" in response.json()["detail"]


def test_import_archive_rejects_too_many_members(client, store, monkeypatch):
    """安全场景：压缩包成员数超过上限时拒绝解压（防解压炸弹）。"""
    monkeypatch.setattr(config, "DATASET_MAX_ARCHIVE_FILES", 2)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for index in range(3):
            archive.writestr(f"f{index}.csv", "feature,target\n1,a\n")

    response = client.post(
        "/api/datasets/import",
        files={"file": ("many.zip", payload.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
    assert "成员过多" in response.json()["detail"]
    assert client.get("/api/datasets").json()["total"] == 0


def test_import_archive_rejects_expanded_size(client, store, monkeypatch):
    """安全场景：解压总大小超过上限时拒绝（放开压缩比限制以单独验证）。"""
    monkeypatch.setattr(config, "DATASET_MAX_ARCHIVE_EXPANDED_BYTES", 1024)
    monkeypatch.setattr(config, "DATASET_MAX_COMPRESSION_RATIO", 100000)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("rows.csv", "feature,target\n" + "1,a\n" * 500)

    response = client.post(
        "/api/datasets/import",
        files={"file": ("expanded.zip", payload.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
    assert "解压总大小" in response.json()["detail"]


def test_import_archive_rejects_compression_bomb_ratio(client, store, monkeypatch):
    """安全场景：高压缩比（展开远大于压缩体积）被识别为压缩炸弹拒绝。"""
    monkeypatch.setattr(config, "DATASET_MAX_COMPRESSION_RATIO", 1)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("rows.csv", "feature,target\n" + "1,a\n" * 500)

    response = client.post(
        "/api/datasets/import",
        files={"file": ("bomb.zip", payload.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
    assert "压缩比" in response.json()["detail"]


def test_import_archive_rejects_oversized_member(client, store, monkeypatch):
    """安全场景：单个成员解压后超过上限时拒绝。"""
    monkeypatch.setattr(config, "DATASET_MAX_ARCHIVE_MEMBER_BYTES", 32)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("rows.csv", "feature,target\n" + "1,a\n" * 50)

    response = client.post(
        "/api/datasets/import",
        files={"file": ("member.zip", payload.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
    assert "成员" in response.json()["detail"]


def test_import_archive_rejects_too_many_data_files(client, store, monkeypatch):
    """边界场景：压缩包内数据文件数超过导入上限时拒绝，避免静默截断。"""
    monkeypatch.setattr(config, "DATASET_MAX_IMPORT_FILES", 1)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("a.csv", "feature,target\n1,a\n")
        archive.writestr("b.csv", "feature,target\n2,b\n")

    response = client.post(
        "/api/datasets/import",
        files={"file": ("two.zip", payload.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
    assert "数据文件过多" in response.json()["detail"]


def test_process_dataset_supports_legacy_flat_layout(client, store):
    """兼容场景：旧扁平布局也能处理，结果元数据和预览同步更新。"""
    import pandas as pd

    dataset_id = "legacy-process-test"
    frame = pd.DataFrame({"feature": [1, 1, 2], "target": ["a", "a", "b"]})
    frame.to_csv(config.DATASETS_DIR / f"{dataset_id}.csv", index=False)
    config.DATASETS_DIR.joinpath(f"{dataset_id}.meta.json").write_text(
        json.dumps({"id": dataset_id, "name": "legacy.csv", "rows": 3,
                    "columns": ["feature", "target"], "tags": [], "description": ""}),
        encoding="utf-8",
    )

    response = client.post(
        f"/api/datasets/{dataset_id}/process",
        json={"operations": [{"type": "remove_duplicates", "params": {}}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["original_rows"] == 3
    assert response.json()["processed_rows"] == 2
    assert client.get(f"/api/datasets/{dataset_id}").json()["rows"] == 2
    legacy_metadata = json.loads(
        config.DATASETS_DIR.joinpath(f"{dataset_id}.meta.json").read_text(encoding="utf-8")
    )
    assert legacy_metadata["rows"] == 2


def test_reprocessing_legacy_dataset_starts_from_original_file(client, store):
    """兼容场景：旧扁平数据集重复处理仍从原始 CSV 开始。"""
    import pandas as pd

    dataset_id = "legacy-reprocess-test"
    frame = pd.DataFrame({"feature": [1, 1, 2], "target": ["a", "a", "b"]})
    frame.to_csv(config.DATASETS_DIR / f"{dataset_id}.csv", index=False)
    config.DATASETS_DIR.joinpath(f"{dataset_id}.meta.json").write_text(
        json.dumps({"id": dataset_id, "name": "legacy-reprocess.csv", "rows": 3,
                    "columns": ["feature", "target"], "tags": [], "description": ""}),
        encoding="utf-8",
    )

    first = client.post(
        f"/api/datasets/{dataset_id}/process",
        json={"operations": [{"type": "select_columns", "params": {"columns": ["feature"]}}]},
    )
    assert first.status_code == 200, first.text
    assert client.get(f"/api/datasets/{dataset_id}").json()["columns"] == ["feature"]

    second = client.post(
        f"/api/datasets/{dataset_id}/process",
        json={"operations": [{"type": "remove_duplicates", "params": {}}]},
    )
    assert second.status_code == 200, second.text
    detail = client.get(f"/api/datasets/{dataset_id}").json()
    assert detail["columns"] == ["feature", "target"]
    assert detail["rows"] == 2


def test_process_dataset_supports_structured_layout(client, store):
    """正常场景：新结构数据集处理后写入 processed/ 并同步详情。"""
    import pandas as pd

    from app.dataset_manager import DatasetManager

    manager = DatasetManager(config.DATASETS_DIR)
    frame = pd.DataFrame({"feature": [1, 1, 2], "target": ["a", "a", "b"]})
    metadata = {
        "original_filename": "structured.csv",
        "rows": len(frame),
        "columns": list(frame.columns),
        "column_types": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "created_at": time.time(),
    }
    dataset = manager.save_dataset(frame, metadata)

    response = client.post(
        f"/api/datasets/{dataset['id']}/process",
        json={"operations": [{"type": "remove_duplicates", "params": {}}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["processed_rows"] == 2
    assert (config.DATASETS_DIR / "processed" / f"{dataset['id']}.csv").exists()
    assert client.get(f"/api/datasets/{dataset['id']}").json()["rows"] == 2


def test_process_dataset_returns_clear_error_for_unknown_operation(client, store):
    """异常场景：非法操作返回 400，原始数据不变。"""
    import pandas as pd

    dataset_id = "invalid-operation-test"
    frame = pd.DataFrame({"feature": [1, 2], "target": ["a", "b"]})
    frame.to_csv(config.DATASETS_DIR / f"{dataset_id}.csv", index=False)
    config.DATASETS_DIR.joinpath(f"{dataset_id}.meta.json").write_text(
        json.dumps({"id": dataset_id, "name": "invalid.csv", "rows": 2,
                    "columns": ["feature", "target"], "tags": [], "description": ""}),
        encoding="utf-8",
    )

    response = client.post(
        f"/api/datasets/{dataset_id}/process",
        json={"operations": [{"type": "missing_operation", "params": {}}]},
    )

    assert response.status_code == 400
    assert len(pd.read_csv(config.DATASETS_DIR / f"{dataset_id}.csv")) == 2


def test_process_dataset_rolls_back_when_object_store_sync_fails(client, store, monkeypatch):
    """异常场景：对象存储同步失败时，不暴露未同步的处理结果。"""
    import pandas as pd

    dataset_id = "sync-failure-process-test"
    frame = pd.DataFrame({"feature": [1, 1, 2], "target": ["a", "a", "b"]})
    frame.to_csv(config.DATASETS_DIR / f"{dataset_id}.csv", index=False)
    config.DATASETS_DIR.joinpath(f"{dataset_id}.meta.json").write_text(
        json.dumps({"id": dataset_id, "name": "sync-failure.csv", "rows": 3,
                    "columns": ["feature", "target"], "tags": [], "description": ""}),
        encoding="utf-8",
    )

    def fail_sync(_dataset_id):
        raise OSError("对象存储不可用")

    monkeypatch.setattr("app.routers.datasets._sync_dataset_csv_to_store", fail_sync)
    response = client.post(
        f"/api/datasets/{dataset_id}/process",
        json={"operations": [{"type": "remove_duplicates", "params": {}}]},
    )

    assert response.status_code == 503
    assert not (config.DATASETS_DIR / "processed" / f"{dataset_id}.csv").exists()
    assert client.get(f"/api/datasets/{dataset_id}").json()["rows"] == 3


def test_upload_csv_with_integer_columns_is_serializable_and_listed(client, store):
    """回归：CSV 整数列上传后元数据可序列化，并能在列表和预览中读取。"""
    from app.state import dataset_manager

    for directory in (
        dataset_manager.raw_dir,
        dataset_manager.processed_dir,
        dataset_manager.temp_dir,
        dataset_manager.metadata_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    payload = b"feature,target\n1,no\n2,yes\n"
    response = client.post(
        "/api/datasets",
        files={"file": ("integer-metadata.csv", payload, "text/csv")},
    )

    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded["rows"] == 2
    assert uploaded["columns"] == ["feature", "target"]
    assert isinstance(uploaded["memory_usage"], int)

    datasets = client.get("/api/datasets").json()["datasets"]
    listed = next(item for item in datasets if item["id"] == uploaded["id"])
    assert listed["rows"] == 2
    assert listed["columns"] == ["feature", "target"]

    preview = client.get(f"/api/datasets/{uploaded['id']}").json()
    assert preview["preview"] == [
        {"feature": 1, "target": "no"},
        {"feature": 2, "target": "yes"},
    ]


def test_remote_server_rejects_invalid_repository_sources():
    """镜像加速/pip/apt 源格式非法时拒绝，合法时接受。"""
    from pydantic import ValidationError

    from app.routers.schemas import RemoteServerRequest

    with pytest.raises(ValidationError):
        RemoteServerRequest(machine_id="m1", host="h.example.net", ssh_credential_id="c1", docker_registry="https://bad.example")
    with pytest.raises(ValidationError):
        RemoteServerRequest(machine_id="m1", host="h.example.net", ssh_credential_id="c1", pip_index_url="not-a-url")
    with pytest.raises(ValidationError):
        RemoteServerRequest(machine_id="m1", host="h.example.net", ssh_credential_id="c1", apt_mirror="mirrors.aliyun.com/debian")

    ok = RemoteServerRequest(
        machine_id="m1",
        host="h.example.net",
        ssh_credential_id="c1",
        docker_registry="docker.m.daocloud.io",
        pip_index_url="https://mirrors.aliyun.com/pypi/simple/",
        apt_mirror="mirrors.aliyun.com",
    )
    assert ok.docker_registry == "docker.m.daocloud.io"
    assert ok.pip_index_url == "https://mirrors.aliyun.com/pypi/simple/"
    assert ok.apt_mirror == "mirrors.aliyun.com"


def test_job_manager_startup_tolerates_storage_outage(tmp_path, monkeypatch):
    """对象存储不可达时，重启恢复降级为告警，控制面仍能完成初始化。"""
    from app import jobstore
    from app.coordination import EmbeddedJobQueue, EmbeddedLeaseStore
    from app.jobs import JobManager
    from app.storage import ArtifactStoreUnavailableError, LocalArtifactStore

    store = LocalArtifactStore(tmp_path / "recover-state")

    def fail_list_ids(store):
        """模拟启动时任务列表读取连接故障。"""
        raise ArtifactStoreUnavailableError("TLS connection reset")

    monkeypatch.setattr(jobstore, "list_job_ids", fail_list_ids)
    try:
        manager = JobManager(store=store, queue=EmbeddedJobQueue(),
                             lease=EmbeddedLeaseStore(), start_workers=False)
    finally:
        monkeypatch.undo()

    def fail_read_job(store, job_id):
        """模拟任务列表可读但单个任务文件读取故障。"""
        raise ArtifactStoreUnavailableError("TLS connection reset")

    jobstore.write_job(store, {"id": "stub-job", "status": "queued"})
    monkeypatch.setattr(jobstore, "list_job_ids", lambda store: ["stub-job"])
    monkeypatch.setattr(jobstore, "read_job", fail_read_job)
    try:
        manager = JobManager(store=store, queue=EmbeddedJobQueue(),
                             lease=EmbeddedLeaseStore(), start_workers=False)
    finally:
        monkeypatch.undo()
        manager.stop()


def test_remote_management_is_enabled_without_admin_token(client, monkeypatch):
    """远程管理默认启用，页面可直接读取服务器目录，无需管理令牌。"""
    from app.routers import remote as jobs_router

    monkeypatch.setattr(jobs_router.remote_config_repository, "list_remote_servers", lambda: [])
    response = client.get("/api/remote-servers")
    assert response.status_code == 200
    assert response.json() == {"servers": []}

