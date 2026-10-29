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


def test_static_paths_not_shadowed_by_param_route(client):
    """回归：/api/datasets/tags 与 import-history 不得被 /datasets/{ds_id} 遮蔽。"""
    r = client.get("/api/datasets/tags")
    assert r.status_code == 200, f"tags 端点应可达: {r.status_code}"
    assert "tags" in r.json()
    r = client.get("/api/datasets/import-history")
    assert r.status_code == 200, f"import-history 端点应可达: {r.status_code}"
    assert "history" in r.json()


def test_train_e2e_classification(client, make_dataset):
    """正常场景：完整训练链路——提交/详情/进度/指标/summary/日志/产物。"""
    ds = make_dataset()
    r = client.post("/api/train", json=_train_payload(ds))
    assert r.status_code == 200
    job = r.json()["job_id"]

    ok = wait_until(lambda: job_status(get_store(), job) == "completed", 60)
    assert ok, f"任务应完成，实际 {job_status(get_store(), job)}"

    detail = client.get(f"/api/jobs/{job}").json()
    assert detail["status"] == "completed"
    assert len(detail["metrics"]) == 4
    assert detail["progress"]["percent"] == 100.0
    assert detail["summary"]["final"]["val_accuracy"] is not None
    assert detail["segments"], "应记录训练分段（worker_id/device/epoch 范围）"

    logs = client.get(f"/api/jobs/{job}/logs").json()
    assert logs["total"] > 0 and any("开始执行训练任务" in l for l in logs["lines"])

    # 模型可下载（经状态平面取出）
    dl = client.get(f"/api/jobs/{job}/model")
    assert dl.status_code == 200 and len(dl.content) > 0


def test_clear_job_logs_removes_file_and_database_logs(client, monkeypatch):
    """清除任务日志后，文件存储、数据库镜像和读取接口都应为空。"""
    from app import database
    from app.models import Dataset, JobLog, TrainingJob
    from app.state import manager
    from uuid import uuid4

    job_id = "clear-logs-" + uuid4().hex
    jobstore.write_job(manager.store, {"id": job_id, "status": "completed", "log_revision": 0})
    jobstore.append_job_log(manager.store, job_id, "需要清除")
    with database.SessionLocal() as db:
        db.add(Dataset(id=job_id, name="clear logs", rows=0, columns=[]))
        db.add(TrainingJob(
            id=job_id, dataset_id=job_id, dataset_name="clear logs", target="",
            task="classification", params={},
        ))
        db.add(JobLog(job_id=job_id, message="数据库中的旧日志"))
        db.commit()
    monkeypatch.setattr(config, "DB_AVAILABLE", True)
    try:
        response = client.delete(f"/api/jobs/{job_id}/logs")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert client.get(f"/api/jobs/{job_id}/logs").json() == {
            "lines": [], "next_offset": 0, "total": 0,
        }
        assert jobstore.read_job_log(manager.store, job_id) == ""
        assert jobstore.read_job(manager.store, job_id)["log_revision"] == 1
        with database.SessionLocal() as db:
            assert db.query(JobLog).filter(JobLog.job_id == job_id).count() == 0
    finally:
        jobstore.delete_job_artifacts(manager.store, job_id)


def test_clear_job_logs_returns_404_for_missing_job(client):
    """不存在的任务不得创建日志文件或返回成功。"""
    response = client.delete("/api/jobs/missing-clear-logs-test/logs")
    assert response.status_code == 404


@pytest.mark.parametrize("file_log", [None, "", "[08:42:38] worker 心跳丢失，任务重新排队\n"])
def test_job_logs_prefer_authoritative_file_with_legacy_fallback(client, monkeypatch, file_log):
    """完整文件日志优先；已清空文件不复活数据库旧日志，旧任务无文件时可回退。"""
    from app import database
    from app.models import Dataset, JobLog, TrainingJob
    from app.state import manager
    from uuid import uuid4

    job_id = "log-source-" + uuid4().hex
    jobstore.write_job(manager.store, {"id": job_id, "status": "completed"})
    if file_log is not None:
        manager.store.write_bytes(job_key(job_id, "log.txt"), file_log.encode())
    with database.SessionLocal() as db:
        db.add(Dataset(id=job_id, name=job_id, rows=0, columns=[]))
        db.add(TrainingJob(id=job_id, dataset_id=job_id, dataset_name=job_id,
                           target="", task="classification", params={}))
        db.add(JobLog(job_id=job_id, message="仅数据库保留的旧训练日志"))
        db.commit()
    monkeypatch.setattr(config, "DB_AVAILABLE", True)
    try:
        result = client.get(f"/api/jobs/{job_id}/logs").json()
        if file_log is None:
            assert result["total"] == 1
            assert "仅数据库保留的旧训练日志" in result["lines"][0]
        else:
            assert result == {"lines": file_log.splitlines(),
                              "next_offset": len(file_log.splitlines()),
                              "total": len(file_log.splitlines())}
    finally:
        jobstore.delete_job_artifacts(manager.store, job_id)


def test_job_logs_paginate_authoritative_file(client):
    """日志分页保持内容顺序，负偏移和超出末尾的偏移均有确定结果。"""
    from app.state import manager
    from uuid import uuid4

    job_id = "log-pages-" + uuid4().hex
    lines = [f"训练记录 {index}" for index in range(505)]
    jobstore.write_job(manager.store, {"id": job_id, "status": "completed"})
    manager.store.write_bytes(job_key(job_id, "log.txt"), "\n".join(lines).encode())
    try:
        for offset, start, end in [(-1, 0, 500), (500, 500, 505), (999, 505, 505)]:
            result = client.get(f"/api/jobs/{job_id}/logs?offset={offset}").json()
            assert result == {"lines": lines[start:end], "next_offset": end, "total": 505}
    finally:
        jobstore.delete_job_artifacts(manager.store, job_id)


def test_train_e2e_regression(client, make_dataset):
    ds = make_dataset(kind="regression")
    r = client.post("/api/train", json=_train_payload(ds, epochs=3,
                                                      task="regression"))
    assert r.status_code == 200
    job = r.json()["job_id"]
    assert wait_until(lambda: job_status(get_store(), job) == "completed", 60)
    detail = client.get(f"/api/jobs/{job}").json()
    assert "val_r2" in detail["summary"]["final"]


def test_train_e2e_kmeans_without_target_column(client, make_dataset):
    """KMeans API 可提交无目标列任务，完成后返回聚类指标和簇预测。"""
    ds = make_dataset(kind="clustering", n=90)
    response = client.post(
        "/api/train",
        json=_train_payload(ds, task="clustering", target=None, epochs=20, n_clusters=3),
    )
    assert response.status_code == 200, response.text
    job = response.json()["job_id"]
    assert wait_until(lambda: job_status(get_store(), job) == "completed", 60)

    detail = client.get(f"/api/jobs/{job}").json()
    assert detail["spec"]["target"] == ""
    assert detail["summary"]["n_clusters"] == 3
    assert detail["summary"]["final"]["val_silhouette"] > 0.5
    assert client.get(f"/api/jobs/{job}/model").status_code == 200
    prediction = client.post(
        f"/api/jobs/{job}/predict", json={"row": {"f1": 0.1, "f2": 0.2, "f3": -0.1}}
    )
    assert prediction.status_code == 200, prediction.text
    assert prediction.json()["task"] == "clustering"
    assert 0 <= prediction.json()["prediction"] < 3


def test_train_e2e_single_series_forecasting_and_history_validation(client, make_dataset):
    """时序预测 API 按时间列训练并要求恰好 lookback 个历史值。"""
    ds = make_dataset(kind="time_series", n=100)
    response = client.post(
        "/api/train",
        json=_train_payload(
            ds, task="time_series_forecasting", epochs=3, target="target",
            time_column="timestamp", lookback=8, forecast_horizon=2,
        ),
    )
    assert response.status_code == 200, response.text
    job = response.json()["job_id"]
    assert wait_until(lambda: job_status(get_store(), job) == "completed", 60)
    detail = client.get(f"/api/jobs/{job}").json()
    assert detail["summary"]["forecast_horizon"] == 2
    assert "val_rmse" in detail["summary"]["final"]

    prediction = client.post(
        f"/api/jobs/{job}/predict", json={"history": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]}
    )
    assert prediction.status_code == 200, prediction.text
    assert prediction.json()["task"] == "time_series_forecasting"
    assert isinstance(prediction.json()["prediction"], float)
    invalid_history = client.post(f"/api/jobs/{job}/predict", json={"history": [1.0, 2.0]})
    assert invalid_history.status_code == 400


def test_train_rejects_invalid_new_task_parameters(client, make_dataset):
    """新任务的目标/时间列及聚类数校验在创建任务前拒绝非法请求。"""
    clustering_ds = make_dataset(kind="clustering", n=60)
    invalid_clusters = client.post(
        "/api/train",
        json=_train_payload(clustering_ds, task="clustering", target=None, n_clusters=1),
    )
    assert invalid_clusters.status_code == 422
    gpu_clustering = client.post(
        "/api/train",
        json=_train_payload(
            clustering_ds, task="clustering", target=None, n_clusters=3, require_gpu=True
        ),
    )
    assert gpu_clustering.status_code == 422

    time_ds = make_dataset(kind="time_series", n=60)
    missing_time_column = client.post(
        "/api/train",
        json=_train_payload(
            time_ds, task="time_series_forecasting", target="target", time_column="missing"
        ),
    )
    assert missing_time_column.status_code == 400
    same_time_and_target = client.post(
        "/api/train",
        json=_train_payload(
            time_ds, task="time_series_forecasting", target="target", time_column="target"
        ),
    )
    assert same_time_and_target.status_code == 422


def test_predict_consistency(client, make_dataset):
    """ML 特有：同一输入对同一 best.pt 预测结果稳定；输出含概率。"""
    ds = make_dataset()
    job = client.post("/api/train", json=_train_payload(ds, epochs=3)).json()["job_id"]
    assert wait_until(lambda: job_status(get_store(), job) == "completed", 60)

    row = {"f1": 0.5, "f2": -0.3, "f3": 1.2}
    p1 = client.post(f"/api/jobs/{job}/predict", json={"row": row}).json()
    p2 = client.post(f"/api/jobs/{job}/predict", json={"row": row}).json()
    assert p1["prediction"] == p2["prediction"]
    assert p1["probabilities"] == p2["probabilities"]
    assert abs(sum(p1["probabilities"].values()) - 1.0) < 1e-6

    # 缺字段 → 容错设计：返回预测（训练中位数/众数填充）而非 500，且不静默崩溃
    pr = client.post(f"/api/jobs/{job}/predict", json={"row": {"f1": 0.1}})
    assert pr.status_code == 200, "缺失字段应按预处理容错填充"
    assert "prediction" in pr.json()
    # 完全空行 → 同样容错返回（全部取训练中位数），预测结果稳定
    pe1 = client.post(f"/api/jobs/{job}/predict", json={"row": {}}).json()
    pe2 = client.post(f"/api/jobs/{job}/predict", json={"row": {}}).json()
    assert pe1["prediction"] == pe2["prediction"]


def test_train_with_missing_dataset_400(client):
    """异常场景：目标数据集不存在 → 404。"""
    r = client.post("/api/train", json=_train_payload("no-such-ds"))
    assert r.status_code == 404


def test_train_target_not_in_columns(client, make_dataset):
    ds = make_dataset()
    r = client.post("/api/train", json=_train_payload(ds, target="not_a_col"))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 资源提示 / workers 面板（AC8/AC9 控制面）
# ---------------------------------------------------------------------------

def test_require_gpu_resource_hint_roundtrip(client, make_dataset):
    """require_gpu 透传到任务记录（分发改卡级匹配）。"""
    ds = make_dataset()
    job = client.post("/api/train", json=_train_payload(
        ds, epochs=2, require_gpu=True, min_vram_mb=8192)).json()["job_id"]
    detail = client.get(f"/api/jobs/{job}").json()
    assert detail["resource_hint"] == {"require_gpu": True, "min_vram_mb": 8192}
    # 面板数据源可用
    panel = client.get("/api/workers").json()
    assert "workers" in panel and "queue_depth" in panel


def test_managed_worker_selection_api(client):
    """外部在线槽默认待选；仅已发现且由脚本接入的槽位可加入或停用。"""
    from app.state import manager

    worker_id = f"api-managed-{time.time_ns()}"
    manager.lease.register_worker(
        worker_id,
        {"managed": True, "machine_id": "api-managed", "slot": "cpu0",
         "gpu": {"available": False}, "current_job": None},
        ttl=30,
    )
    try:
        panel = client.get("/api/workers").json()
        worker = next(item for item in panel["workers"] if item["worker_id"] == worker_id)
        assert worker["managed"] is True
        assert worker["enabled"] is False
        assert worker["current_job"] is None

        selected = client.patch(f"/api/workers/{worker_id}", json={"enabled": True})
        assert selected.status_code == 200
        assert selected.json()["enabled"] is True

        panel = client.get("/api/workers").json()
        worker = next(item for item in panel["workers"] if item["worker_id"] == worker_id)
        assert worker["enabled"] is True

        stopped = client.patch(f"/api/workers/{worker_id}", json={"enabled": False})
        assert stopped.status_code == 200
        assert "正在运行的任务会正常完成" in stopped.json()["message"]
        assert client.patch("/api/workers/not-found", json={"enabled": True}).status_code == 404
        assert client.patch(f"/api/workers/{worker_id}", json={"enabled": "yes"}).status_code == 422

        built_in_id = f"api-builtin-{time.time_ns()}"
        manager.lease.register_worker(built_in_id, {"managed": False}, ttl=30)
        try:
            conflict = client.patch(f"/api/workers/{built_in_id}", json={"enabled": False})
            assert conflict.status_code == 409
        finally:
            manager.lease.deregister_worker(built_in_id)
    finally:
        manager.lease.deregister_worker(worker_id)


def test_remote_management_is_enabled_without_admin_token(client, monkeypatch):
    """远程管理默认启用，页面可直接读取服务器目录，无需管理令牌。"""
    from app.routers import jobs as jobs_router

    monkeypatch.setattr(jobs_router.remote_config_repository, "list_remote_servers", lambda: [])
    response = client.get("/api/remote-servers")
    assert response.status_code == 200
    assert response.json() == {"servers": []}


def test_remote_operation_cancel_cancels_queued_operation(
    client, monkeypatch
):
    """无管理令牌时也可中断排队操作，且状态和服务器锁正确更新。"""
    from app.routers import jobs as jobs_router
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
    from app.routers import jobs as jobs_router
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
    from app.routers import jobs as jobs_router
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
    from app.routers import jobs as jobs_router

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
    from app.routers import jobs as jobs_router
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


def test_pause_resume_via_http(client, make_dataset):
    """HTTP 链路：running → pause → paused → resume → completed，曲线连续。"""
    ds = make_dataset(n=2000)
    job = client.post("/api/train", json=_train_payload(ds, epochs=400,
                                                        batch_size=128,
                                                        hidden_size=64,
                                                        hidden_layers=2)).json()["job_id"]
    store = get_store()
    assert wait_until(lambda: job_status(store, job) == "running", 30)
    time.sleep(2.0)  # 训练若干 epoch
    assert client.post(f"/api/jobs/{job}/pause").status_code == 200
    assert wait_until(lambda: job_status(store, job) == "paused", 40)
    paused_view = client.get(f"/api/jobs/{job}").json()
    paused_epoch = paused_view["progress"]["epoch"]
    assert paused_epoch >= 1

    assert client.post(f"/api/jobs/{job}/resume").status_code == 200
    assert wait_until(lambda: job_status(store, job) == "completed", 300)
    final = client.get(f"/api/jobs/{job}").json()
    eps = [m["epoch"] for m in final["metrics"]]
    assert eps == list(range(1, 401)), f"续训曲线必须连续: {eps[:5]}...{eps[-5:]}"
    # 预测在续训后可用（预处理一致性：列映射与训练期一致）
    row = {"f1": 0.2, "f2": 0.1, "f3": -0.1}
    assert client.post(f"/api/jobs/{job}/predict", json={"row": row}).status_code == 200


def test_cancel_via_http(client, make_dataset):
    ds = make_dataset()
    job = client.post("/api/train", json=_train_payload(ds, epochs=300,
                                                        hidden_size=64)).json()["job_id"]
    store = get_store()
    assert wait_until(lambda: job_status(store, job) == "running", 30)
    assert client.post(f"/api/jobs/{job}/cancel").status_code == 200
    assert wait_until(lambda: job_status(store, job) == "cancelled", 40)
    assert client.post(f"/api/jobs/{job}/pause").status_code in (400, 409), "终态不可暂停" 


def test_delete_via_http(client, make_dataset):
    ds = make_dataset()
    job = client.post("/api/train", json=_train_payload(ds, epochs=2)).json()["job_id"]
    store = get_store()
    assert wait_until(lambda: job_status(store, job) == "completed", 60)
    assert client.delete(f"/api/jobs/{job}").status_code == 200
    assert jobstore.read_job(store, job) is None
    assert client.get(f"/api/jobs/{job}").status_code == 404


# ---------------------------------------------------------------------------
# 数据格式双兼容（旧扁平 / 新结构 processed/）
# ---------------------------------------------------------------------------

def test_structured_layout_dataset_trains(client, store):
    """新结构（metadata/ + raw/ + processed/）数据集全链路可用（双格式兼容）。"""
    import time as _time

    import numpy as np
    import pandas as pd

    from app.dataset_manager import DatasetManager

    # 每个测试前 store 夹具会清空数据目录，须重建 DatasetManager（其 __init__ 建子目录）
    dm = DatasetManager(config.DATASETS_DIR)
    rng = np.random.RandomState(11)
    df = pd.DataFrame({"f1": rng.randn(300), "f2": rng.randn(300),
                       "target": rng.randint(0, 3, 300)})
    meta = dm.save_dataset(df, {
        "original_filename": "structured.csv",
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "column_types": {str(c): str(t) for c, t in df.dtypes.items()},
        "created_at": _time.time(),
    })
    ds = meta["id"]
    r = client.post("/api/train", json=_train_payload(ds, epochs=2))
    assert r.status_code == 200, "新结构数据集应可发起训练"
    job = r.json()["job_id"]
    assert wait_until(lambda: job_status(get_store(), job) == "completed", 60)


# ---------------------------------------------------------------------------
# 数据集管理导入 / 处理链路
# ---------------------------------------------------------------------------

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


def test_failed_job_retry_api_checks_resource_requirements_and_assignment(
    client, store, monkeypatch, request
):
    """失败任务可自动或指定 GPU 槽重试，未知/停用/不兼容资源会被拒绝。"""
    from app.coordination import EmbeddedJobQueue
    from app.state import manager
    from tests.conftest import make_job_record

    monkeypatch.setattr(manager, "queue", EmbeddedJobQueue())
    job_ids = ["job-manual-retry-gpu", "job-auto-retry", "job-retry-not-failed"]
    worker_ids = ["retry-cpu", "retry-small-gpu", "retry-big-gpu", "retry-disabled"]

    def cleanup():
        for worker_id in worker_ids:
            manager.lease.deregister_worker(worker_id)
        for job_id in job_ids:
            try:
                jobstore.delete_job_artifacts(manager.store, job_id)
            except Exception:
                pass

    request.addfinalizer(cleanup)

    workers = [
        ("retry-cpu", {"available": False, "vram_mb": 0}, True),
        ("retry-small-gpu", {"available": True, "name": "Small", "vram_mb": 4096}, True),
        ("retry-big-gpu", {"available": True, "name": "Big", "vram_mb": 8192}, True),
        ("retry-disabled", {"available": True, "name": "Disabled", "vram_mb": 16384}, False),
    ]
    for worker_id, gpu, enabled in workers:
        manager.lease.register_worker(
            worker_id,
            {"worker_id": worker_id, "machine_id": "retry-node", "slot": worker_id,
             "managed": True, "gpu": gpu, "cpu": {"cores": 4}},
            ttl=60,
        )
        manager.lease.set_worker_enabled(worker_id, enabled)

    failed = make_job_record(
        job_ids[0], require_gpu=True, min_vram_mb=8192
    )
    failed.update({"status": "failed", "error": "GPU worker lost", "retry_count": 3})
    jobstore.write_job(manager.store, failed)

    assert client.post(f"/api/jobs/{job_ids[0]}/retry", json={"worker_id": "missing-worker"}).status_code == 409
    assert client.post(f"/api/jobs/{job_ids[0]}/retry", json={"worker_id": ""}).status_code == 422
    assert client.post(
        f"/api/jobs/{job_ids[0]}/retry", json={"worker_id": "x" * 161}
    ).status_code == 422
    assert client.post(
        f"/api/jobs/{job_ids[0]}/retry", json={"worker_id": "../../;rm -rf /"}
    ).status_code == 409
    assert client.post(f"/api/jobs/{job_ids[0]}/retry", json={"worker_id": "retry-cpu"}).status_code == 409
    assert client.post(f"/api/jobs/{job_ids[0]}/retry", json={"worker_id": "retry-small-gpu"}).status_code == 409
    assert client.post(f"/api/jobs/{job_ids[0]}/retry", json={"worker_id": "retry-disabled"}).status_code == 409

    selected = client.post(
        f"/api/jobs/{job_ids[0]}/retry", json={"worker_id": "retry-big-gpu"}
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["assignment_mode"] == "worker"
    assert selected.json()["worker_id"] == "retry-big-gpu"
    selected_detail = client.get(f"/api/jobs/{job_ids[0]}").json()
    assert selected_detail["status"] == "queued"
    assert selected_detail["retry_worker_id"] == "retry-big-gpu"
    assert selected_detail["retry_count"] == 0
    assert selected_detail["manual_retry_count"] == 1
    assert selected_detail["last_error"] == "GPU worker lost"

    automatic = make_job_record(job_ids[1])
    automatic.update({"status": "failed", "error": "previous attempt failed"})
    jobstore.write_job(manager.store, automatic)
    auto_response = client.post(f"/api/jobs/{job_ids[1]}/retry", json={})
    assert auto_response.status_code == 200, auto_response.text
    assert auto_response.json()["assignment_mode"] == "auto"
    assert auto_response.json()["worker_id"] is None

    non_failed = make_job_record(job_ids[2])
    jobstore.write_job(manager.store, non_failed)
    assert client.post(f"/api/jobs/{job_ids[2]}/retry", json={}).status_code == 409
    assert client.post("/api/jobs/job-does-not-exist/retry", json={}).status_code == 404


def test_training_page_resource_retry_and_chart_style_contract():
    """训练页应提供资源轨迹、失败重试控件与浅色曲线样式。"""
    backend_dir = Path(__file__).resolve().parents[1]
    static_dir = backend_dir / "static"
    if not static_dir.is_dir():
        static_dir = backend_dir.parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    css = (static_dir / "app.css").read_text(encoding="utf-8")

    assert 'id="resourceAllocation"' in html
    assert 'id="retryResourceSelect"' in html
    assert "/api/jobs/${encodeURIComponent(jobId)}/retry" in html
    assert "state.workerSlots.filter" in html
    assert 'Chart.defaults.borderColor = "#e6ecf3"' in html
    assert ".training-page .chartbox canvas" in css
    assert ".training-page .resource-allocation-row" in css
    assert '<div class="field wide" id="llmFields" hidden>' in html
    assert ".training-page #trainForm > .formgrid > #llmFields" in css
    assert "grid-column: 1 / -1" in css
    assert ".training-page #llmFields > .formgrid" in css
    assert "repeat(auto-fit, minmax(min(100%, 210px), 1fr))" in css
    assert 'href="/app.css?v=20261029-2"' in html


def test_remote_server_feedback_toasts_render_above_add_modal():
    """远程添加表单打开时，错误提示仍应显示在弹窗上方。"""
    backend_dir = Path(__file__).resolve().parents[1]
    static_dir = backend_dir / "static"
    if not static_dir.is_dir():
        static_dir = backend_dir.parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    modal_css = html.split(".server-modal {", 1)[1].split("}", 1)[0]
    toast_css = html.split("#toastWrap {", 1)[1].split("}", 1)[0]
    modal_z_index = int(modal_css.split("z-index:", 1)[1].split(";", 1)[0])
    toast_z_index = int(toast_css.split("z-index:", 1)[1].split(";", 1)[0])

    assert toast_z_index > modal_z_index


def test_remote_server_dialog_groups_fields_and_keeps_actions_outside_scroll_area():
    """添加与编辑共用分组表单，折叠高级项时仍可从固定操作栏提交。"""
    static_dir = Path(__file__).resolve().parents[1] / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    css = (static_dir / "app.css").read_text(encoding="utf-8")
    modal = html.split('id="addServerModal"', 1)[1].split('id="remoteLogModal"', 1)[0]

    assert 'aria-describedby="addServerDescription"' in modal
    assert 'id="remoteServerAdvanced"' in modal
    advanced_tag = modal.split('id="remoteServerAdvanced"', 1)[1].split(">", 1)[0]
    assert "open" not in advanced_tag
    assert modal.index('id="remoteServerAdvanced"') < modal.index('id="remoteRedisUrl"')
    assert modal.index('id="remoteServerAdvanced"') < modal.index('id="remoteS3Endpoint"')
    assert 'id="remoteServerForm" class="server-form" novalidate' in modal
    assert 'form="remoteServerForm"' in modal.split('id="btnSubmitRemoteServer"', 1)[1].split(">", 1)[0]
    assert 'id="remoteServerError"' in modal
    assert 'id="btnManageRemoteCredentials"' in modal
    assert 'id="remoteCredentialStatus"' in modal
    assert "#addServerModal .server-modal-body" in css
    assert "overscroll-behavior: contain" in css
    assert "body.server-dialog-open" in css


def test_remote_server_dialog_protects_input_and_keyboard_navigation():
    """关闭前检查未保存内容，遮罩不清空表单，键盘仅在弹窗内移动。"""
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    backdrop_handler = html.split('$("addServerModal").addEventListener("click",', 1)[1].split("});", 1)[0]

    assert "closeAddServerModal" not in backdrop_handler
    assert "function handleRemoteServerDialogKeydown(event)" in html
    assert "function remoteServerDialogIsDirty()" in html
    assert "function validateRemoteServerForm()" in html
    assert 'addEventListener("keydown", handleRemoteServerDialogKeydown)' in html
    assert "closeAddServerModal(true)" in html


def test_remote_ssh_credential_save_button_stays_clickable_while_catalog_loads():
    """保存按钮保持可用，页面不再要求远程管理令牌。"""
    backend_dir = Path(__file__).resolve().parents[1]
    static_dir = backend_dir / "static"
    if not static_dir.is_dir():
        static_dir = backend_dir.parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    button = html.split('id="btnSaveRemoteSshSettings"', 1)[1].split(">", 1)[0]
    credential_form = html.split('id="remoteSshCredentialForm"', 1)[1].split(">", 1)[0]
    catalog_loader = html.split("async function loadRemoteSshSettings(", 1)[1].split(
        "// 清空凭证编辑表单", 1
    )[0]
    save_handler = html.split("async function saveRemoteSshSettings()", 1)[1].split(
        "// 把凭证元数据载入编辑表单", 1
    )[0]

    assert "disabled" not in button
    assert "novalidate" in credential_form
    assert '先填写远程管理令牌，再查看或管理 SSH 凭证。' not in catalog_loader
    assert "共享 Redis" not in catalog_loader
    assert "MySQL" in catalog_loader
    assert 'remoteAdminToken' not in html
    assert 'X-Worker-Admin-Token' not in html
    assert "if (!token)" not in save_handler
    assert "button.disabled = true" in save_handler
    assert "button.disabled = false" in save_handler


def test_remote_server_form_exposes_network_overrides_and_effective_config_action():
    """远程服务器页面提供 Redis/S3 覆盖、生效配置查看和编辑动作。"""
    backend_dir = Path(__file__).resolve().parents[1]
    static_dir = backend_dir / "static"
    if not static_dir.is_dir():
        static_dir = backend_dir.parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")

    for marker in (
        "页面不单独配置镜像",
        "生效配置",
        "data-remote-effective-config",
        "remoteEffectiveConfigModal",
        'id="remoteRedisUrl"',
        'id="remoteS3Endpoint"',
        'id="remoteDockerRegistry"',
        'id="remotePipIndexUrl"',
        'id="remoteAptMirror"',
        'id="clearRemoteRedisOverride"',
        "data-edit-remote-machine-id",
        'data-remote-action="deploy"',
        'deployed ? "部署代码" : "部署"',
        "更新配置",
        "部署代码",
        "同步最新 worker 代码并重建容器",
        "保存后覆盖远端配置文件，下次启动/重启时生效",
        "openEditRemoteServer",
    ):
        assert marker in html
    assert 'id="remoteWorkerImage"' not in html
    assert 'name="worker_image"' not in html


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


def test_remote_server_card_filters_lifecycle_actions_by_presence():
    """在线卡片提供停止/重启，离线卡片提供启动/重启；未部署时不显示生命周期操作。"""
    backend_dir = Path(__file__).resolve().parents[1]
    static_dir = backend_dir / "static"
    if not static_dir.is_dir():
        static_dir = backend_dir.parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    card_renderer = html.split("function workerServerCard(", 1)[1].split(
        "// 读取仅在当前会话授权的远程服务器状态", 1
    )[0]

    assert 'const deployed = Boolean(remoteServer && remoteServer.deployed);' in card_renderer
    assert 'const lifecycleControls = !deployed ? "" : online' in card_renderer
    lifecycle_controls = card_renderer.split("const lifecycleControls", 1)[1].split(
        "const serverControls", 1
    )[0]
    assert '? `<button data-remote-machine-id="${esc(machineId)}" data-remote-action="stop"' in lifecycle_controls
    online_controls, offline_controls = lifecycle_controls.split(': `<button', 1)
    assert 'data-remote-action="restart"' in online_controls
    assert 'data-remote-action="restart"' in offline_controls
    assert ': `<button data-remote-machine-id="${esc(machineId)}" data-remote-action="start"' in lifecycle_controls
    assert '配置已保存，正在写入远端；操作成功后请点击“重启”使其生效' in html
    assert '正在写入远端配置，重启后生效…' in card_renderer
    assert '配置已写入，请重启后生效' in html


def test_remote_server_cards_expose_interrupt_action_for_active_operations():
    """远程操作卡片提供中断入口并提示远端部分变更不会自动回滚。"""
    backend_dir = Path(__file__).resolve().parents[1]
    static_dir = backend_dir / "static"
    if not static_dir.is_dir():
        static_dir = backend_dir.parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    card_renderer = html.split("function workerServerCard(", 1)[1].split(
        "// 读取仅在当前会话授权的远程服务器状态", 1
    )[0]
    cancel_handler = html.split("async function cancelRemoteOperation(", 1)[1].split(
        "// 修改一个外接槽位是否接收新任务", 1
    )[0]

    assert 'data-cancel-remote-operation=' in card_renderer
    assert "const cancelRequested = Boolean(operation && operation.cancel_requested);" in card_renderer
    assert 'cancelRequested ? "正在中断…" : "中断操作"' in card_renderer
    assert 'cancelled: "最近操作已中断"' in card_renderer
    assert "不会自动回滚" in cancel_handler
    assert "/cancel" in cancel_handler


def test_remote_server_card_exposes_live_operation_logs():
    """远程服务器卡片展示部署阶段，日志窗口轮询并安全渲染实时输出。"""
    backend_dir = Path(__file__).resolve().parents[1]
    static_dir = backend_dir / "static"
    if not static_dir.is_dir():
        static_dir = backend_dir.parent / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    card_renderer = html.split("function workerServerCard(", 1)[1].split(
        "// 读取仅在当前会话授权的远程服务器状态", 1
    )[0]

    logs_handler = html.split("async function showRemoteServerLogs(", 1)[1].split(
        "// 关闭远程日志窗口", 1
    )[0]
    assert 'data-remote-server-logs="${esc(machineId)}">日志</button>' in card_renderer
    assert 'event.target.closest("[data-remote-server-logs]")' in html
    assert "operation.phase_started_at" in card_renderer
    assert "operation.log" in logs_handler
    assert "operation.output" in logs_handler
    assert "operation.stderr" in logs_handler
    assert "operation.error" in logs_handler
    assert "output.dataset.rawLog = displayLog" in logs_handler
    assert "renderRemoteLogLines(output, displayLog.split(\"\\n\"))" in logs_handler
    assert "日志会自动刷新" in logs_handler
    assert "setTimeout(refreshRemoteOperationLogs, 1500)" in logs_handler
    assert "stopRemoteLogPolling()" in logs_handler
    assert "text.textContent = line || \" \"" in html
    assert 'id="remoteLogModal"' in html
    assert 'id="btnCopyRemoteLog" type="button">复制全部</button>' in html
    assert 'id="btnClearRemoteLog" type="button">清除日志</button>' in html
    assert "async function clearRemoteOperationLogs()" in html
    assert "/logs" in logs_handler or "/logs" in html
    assert "清除执行日志失败" in html
    assert "user-select:text" in html
    assert "data-copy-remote-log-line" in html
    assert "copyRemoteLogText(line.textContent" in html
    assert 'remoteLogOutput").dataset.rawLog' in html
    assert "navigator.clipboard.writeText" in html
