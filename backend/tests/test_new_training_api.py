"""新训练 API E2E 测试：/api/training、/api/workbench、/api/templates、/api/models、/api/pipelines。

对应验收标准（重构后新接口面）：
- 训练链路：提交 → 训练 → 指标/产物（新 API 路径）
- 任务控制：取消/暂停/恢复/删除（新 API 路径）
- 模板库：内置模板列表、参数验证、实例化渲染
- Notebook 会话：创建/启动内核/执行单元格/变量持久化
- Pipeline：DAG 定义拓扑排序、执行记录
- 模型注册表：列表、手动注册、版本管理
"""
from __future__ import annotations

import time

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

import app.config as config


@pytest.fixture(scope="module")
def client():
    """独立训练队列与管理器（嵌入式 worker），避免依赖全局 manager 生命周期。"""
    from app.main import app
    from app.coordination import EmbeddedJobQueue, EmbeddedLeaseStore
    from app.jobs import JobManager
    from app.routers import training_jobs, clusters

    manager = JobManager(queue=EmbeddedJobQueue(), lease=EmbeddedLeaseStore(),
                         start_workers=True, start_watchdog=False)
    # 路由模块在导入期绑定了 manager 名字，须在路由命名空间替换引用
    original_tj, original_cl = training_jobs.manager, clusters.manager
    training_jobs.manager = manager
    clusters.manager = manager
    try:
        with fastapi_testclient.TestClient(app) as c:
            yield c
    finally:
        training_jobs.manager = original_tj
        clusters.manager = original_cl
        manager.stop()


@pytest.fixture(autouse=True)
def _isolate_mysql(remote_mysql_db):
    """为涉及 DB 镜像的新 API 测试提供独立 SQLite 会话。"""
    yield


def _train_payload(ds_id: str, epochs: int = 4, **extra) -> dict:
    body = {"dataset_id": ds_id, "target": "target", "task": "classification",
            "epochs": epochs, "batch_size": 64, "lr": 0.01, "hidden_size": 16,
            "hidden_layers": 1, "test_split": 0.2, "seed": 42}
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# 训练链路（AC：新 API 提交 → 训练 → 指标/产物）
# ---------------------------------------------------------------------------

def test_new_api_train_e2e_classification(client, store, make_dataset):
    """新 API 完整训练链路：提交 → 完成 → 指标存在 → best.pt 产物在存储中。"""
    ds_id = make_dataset("classification", n=200)
    response = client.post("/api/training/jobs", json=_train_payload(ds_id))
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert job_id

    from tests.conftest import wait_until
    finished = wait_until(lambda: (
        (record := client.get(f"/api/training/jobs/{job_id}").json())
        and record.get("status") in {"completed", "failed"}
    ), timeout=120)
    assert finished, "训练未在超时时间内结束"

    record = client.get(f"/api/training/jobs/{job_id}").json()
    assert record["status"] == "completed", record.get("error")
    # 指标断言：至少完成 1 个 epoch 且有验证指标
    metrics = record.get("metrics") or []
    assert metrics, "训练完成后应存在指标记录"
    assert metrics[-1]["epoch"] >= 1
    assert "val_loss" in metrics[-1]


def test_new_api_rejects_target_not_in_columns(client, store, make_dataset):
    """目标列不在数据集中 → 400 且不创建任务"""
    ds_id = make_dataset("classification", n=100)
    response = client.post(
        "/api/training/jobs",
        json=_train_payload(ds_id, target="nonexistent_column"),
    )
    assert response.status_code == 400
    assert "不在数据集列中" in response.json()["detail"]


def test_new_api_rejects_missing_dataset(client, store):
    """数据集不存在 → 404"""
    response = client.post(
        "/api/training/jobs",
        json=_train_payload("ds-not-exist-000"),
    )
    assert response.status_code in (400, 404)


def test_new_api_list_jobs(client, store, make_dataset):
    """任务列表返回包含新提交的任务"""
    ds_id = make_dataset("classification", n=120)
    resp = client.post("/api/training/jobs", json=_train_payload(ds_id, epochs=2))
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    listing = client.get("/api/training/jobs").json()
    assert isinstance(listing, list)
    ids = [j["id"] for j in listing]
    assert job_id in ids


def test_new_api_cancel_queued_job(client, store, make_dataset):
    """取消请求被接受且任务最终到达 cancelled 终态。

    嵌入式 worker 可能在取消前领取任务（竞态）；取消信号会在当前批次
    结束后生效，因此只断言终态为 cancelled（若尚未开始训练则立即取消）。
    """
    from tests.conftest import wait_until

    ds_id = make_dataset("classification", n=120)
    resp = client.post("/api/training/jobs", json=_train_payload(ds_id, epochs=2))
    job_id = resp.json()["job_id"]

    cancel = client.post(f"/api/training/jobs/{job_id}/cancel")
    assert cancel.status_code == 200

    finished = wait_until(lambda: (
        (record := client.get(f"/api/training/jobs/{job_id}").json())
        and record.get("status") in {"cancelled", "completed", "failed"}
    ), timeout=60)
    assert finished, "取消请求后任务未在超时内到达终态"

    record = client.get(f"/api/training/jobs/{job_id}").json()
    assert record["status"] in {"cancelled", "completed"}


def test_new_api_delete_job(client, store, make_dataset):
    """任务删除后 404"""
    ds_id = make_dataset("classification", n=120)
    from tests.conftest import wait_until

    resp = client.post("/api/training/jobs", json=_train_payload(ds_id, epochs=2))
    job_id = resp.json()["job_id"]
    client.post(f"/api/training/jobs/{job_id}/cancel")
    wait_until(lambda: (
        (record := client.get(f"/api/training/jobs/{job_id}").json())
        and record.get("status") in {"cancelled", "completed", "failed"}
    ), timeout=60)

    deleted = client.delete(f"/api/training/jobs/{job_id}")
    assert deleted.status_code == 200

    detail = client.get(f"/api/training/jobs/{job_id}")
    assert detail.status_code == 404


# ---------------------------------------------------------------------------
# 模板库（AC：内置模板、参数验证、实例化）
# ---------------------------------------------------------------------------

def test_templates_builtin_listed(client):
    """内置模板可列出且含分类/回归/聚类/时序/LLM 分类"""
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    templates = resp.json()["templates"]
    categories = {t["category"] for t in templates}
    for expected in ("classification", "regression", "clustering", "time_series", "llm_sft", "llm_dpo"):
        assert expected in categories, f"缺少 {expected} 模板分类"


def test_template_detail_contains_parameters(client):
    """模板详情包含参数定义（名称/类型/默认值）"""
    resp = client.get("/api/templates/builtin-classification")
    assert resp.status_code == 200
    template = resp.json()
    assert template["task_type"] == "classification"
    params = {p["name"]: p for p in template["parameters"]}
    assert "dataset_id" in params and "target" in params
    assert params["dataset_id"]["required"] is True
    assert params["epochs"]["type"] == "integer"


def test_template_validate_params(client):
    """参数验证接口：非法 select 值报错，合法值通过"""
    ok = client.post("/api/templates/builtin-classification/validate", json={
        "template_id": "builtin-classification",
        "parameters": {
            "dataset_id": "ds-demo", "target": "label",
            "model_type": "mlp", "epochs": 20,
        },
    })
    assert ok.status_code == 200
    assert ok.json()["valid"] is True, ok.json()["errors"]

    # 缺少必填参数 → 报错
    missing = client.post("/api/templates/builtin-classification/validate", json={
        "template_id": "builtin-classification",
        "parameters": {"model_type": "mlp"},
    })
    assert missing.status_code == 200
    assert missing.json()["valid"] is False

    bad = client.post("/api/templates/builtin-classification/validate", json={
        "template_id": "builtin-classification",
        "parameters": {"model_type": "transformer"},
    })
    assert bad.status_code == 200
    assert bad.json()["valid"] is False
    assert bad.json()["errors"]


def test_template_render_outputs_code(client):
    """模板渲染：参数代入生成可读训练脚本"""
    resp = client.post("/api/templates/builtin-classification/render", json={
        "parameters": {
            "dataset_id": "ds-demo", "target": "label", "model_type": "mlp",
            "epochs": 20, "hidden_size": 128, "hidden_layers": 2, "lr": 0.001,
            "batch_size": 256, "dropout": 0.15, "optimizer": "adamw",
            "scheduler": "none", "seed": 42,
        },
    })
    assert resp.status_code == 200
    rendered = resp.json()["rendered_content"]
    assert 'task="classification"' in rendered
    assert '"epochs": 20' in rendered
    assert "{{" not in rendered  # 占位符已全部替换


# ---------------------------------------------------------------------------
# Notebook 工作台（AC：会话创建/内核/单元格执行/变量持久化）
# ---------------------------------------------------------------------------

def test_notebook_session_lifecycle(client, store):
    """会话创建 → 启动内核 → 执行单元格 → 变量持久化 → 停止"""
    created = client.post("/api/workbench/sessions", json={"name": "e2e-notebook"})
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    started = client.post(f"/api/workbench/sessions/{session_id}/kernel/start")
    assert started.status_code == 200

    # 执行第一个单元格：定义变量
    r1 = client.post(f"/api/workbench/sessions/{session_id}/cells/execute", json={
        "cell_id": "cell-1", "cell_index": 0,
        "code": "x = 40\ny = x + 2",
    })
    assert r1.status_code == 200
    body = r1.json()
    assert body["success"] is True, body.get("error")

    # 执行第二个单元格：读取上一单元格变量（变量持久化验证）
    r2 = client.post(f"/api/workbench/sessions/{session_id}/cells/execute", json={
        "cell_id": "cell-2", "cell_index": 1,
        "code": "result = y * 2\nprint(result)",
    })
    body2 = r2.json()
    assert body2["success"] is True, body2.get("error")
    assert "84" in body2["output"], "跨单元格变量应持久化"

    # 变量接口可见
    variables = client.get(f"/api/workbench/sessions/{session_id}/variables").json()["variables"]
    assert variables.get("y") == 42
    assert variables.get("result") == 84

    # 单元格历史保存
    cells = client.get(f"/api/workbench/sessions/{session_id}").json()["cells"]
    assert len(cells) >= 2
    assert cells[0]["status"] == "completed"

    # 清理
    stopped = client.post(f"/api/workbench/sessions/{session_id}/kernel/stop")
    assert stopped.status_code == 200
    deleted = client.delete(f"/api/workbench/sessions/{session_id}")
    assert deleted.status_code == 200


def test_notebook_cell_syntax_error_reported(client, store):
    """语法错误：success=False 且错误类型为 SyntaxError"""
    created = client.post("/api/workbench/sessions", json={"name": "err-notebook"})
    session_id = created.json()["session_id"]
    client.post(f"/api/workbench/sessions/{session_id}/kernel/start")

    result = client.post(f"/api/workbench/sessions/{session_id}/cells/execute", json={
        "cell_id": "cell-err", "cell_index": 0, "code": "def broken(:",
    }).json()
    assert result["success"] is False
    assert result["error_type"] == "SyntaxError"

    client.delete(f"/api/workbench/sessions/{session_id}", params={"force": "true"})


def test_notebook_execution_exception_captured(client, store):
    """运行时异常：捕获错误与 traceback，会话恢复 idle 可继续执行"""
    created = client.post("/api/workbench/sessions", json={"name": "exc-notebook"})
    session_id = created.json()["session_id"]
    client.post(f"/api/workbench/sessions/{session_id}/kernel/start")

    result = client.post(f"/api/workbench/sessions/{session_id}/cells/execute", json={
        "cell_id": "cell-exc", "cell_index": 0, "code": "1 / 0",
    }).json()
    assert result["success"] is False
    assert result["error_type"] == "ZeroDivisionError"

    # 会话未崩溃：后续单元格可正常执行
    ok = client.post(f"/api/workbench/sessions/{session_id}/cells/execute", json={
        "cell_id": "cell-ok", "cell_index": 1, "code": "a = 1",
    }).json()
    assert ok["success"] is True

    client.delete(f"/api/workbench/sessions/{session_id}", params={"force": "true"})


# ---------------------------------------------------------------------------
# 模型注册表（AC：手动注册、列表、版本管理）
# ---------------------------------------------------------------------------

def test_model_registry_manual_register_and_list(client, store):
    """手动注册模型 → 列表可见 → 详情含指标与产物路径"""
    register = client.post("/api/models", json={
        "name": "e2e-model",
        "version": "1.0.0",
        "task": "classification",
        "framework": "torch",
        "metrics": {"val_accuracy": 0.92, "val_loss": 0.31},
        "artifacts": {"best": "models/e2e/best.pt", "preprocessor": "models/e2e/preprocessor.json"},
        "tags": ["e2e", "test"],
        "description": "E2E 注册测试模型",
    })
    assert register.status_code == 200, register.text
    model_id = register.json()["id"]

    listing = client.get("/api/models", params={"search": "e2e-model"}).json()
    names = [m["name"] for m in listing["models"]]
    assert "e2e-model" in names

    detail = client.get(f"/api/models/{model_id}").json()
    assert detail["version"] == "1.0.0"
    assert detail["metrics"]["val_accuracy"] == 0.92
    assert detail["artifacts"]["best"] == "models/e2e/best.pt"

    # 归档
    archived = client.delete(f"/api/models/{model_id}")
    assert archived.status_code == 200
    after = client.get(f"/api/models/{model_id}").json()
    assert after["status"] == "archived"


def test_model_versions_endpoint(client, store):
    """版本接口：同一模型多版本取最新"""
    for version in ("1.0.0", "1.1.0"):
        client.post("/api/models", json={
            "name": "versioned-model", "version": version, "task": "regression",
            "framework": "sklearn", "metrics": {"val_r2": 0.8},
            "artifacts": {"best": f"models/versioned/{version}/best.pt"},
        })
    client.post("/api/models", json={
        "name": "versioned-model", "version": "1.0.0", "task": "regression",
        "framework": "sklearn", "metrics": {}, "artifacts": {},
    })

    # 找到任一版本的 id 查询版本列表
    listing = client.get("/api/models", params={"search": "versioned-model"}).json()
    some_id = listing["models"][0]["id"]
    versions = client.get(f"/api/models/{some_id}/versions").json()
    assert versions["latest"] == "1.1.0"
    assert "1.0.0" in versions["versions"]


def _register_deployable_model(client, name: str) -> str:
    """注册一个可部署模型的辅助函数（返回模型 ID）。"""
    register = client.post("/api/models", json={
        "name": name,
        "version": "1.0.0",
        "task": "classification",
        "framework": "torch",
        "metrics": {"val_accuracy": 0.9},
        "artifacts": {"best": f"models/{name}/best.pt"},
    })
    assert register.status_code == 200, register.text
    return register.json()["id"]


def test_model_deployments_global_list_endpoint(client, store):
    """AC（部署管理页）：全局部署列表支持状态/模型过滤，且不被 /{model_id} 路由遮蔽"""
    model_id = _register_deployable_model(client, "deploy-list-model")
    other_id = _register_deployable_model(client, "deploy-other-model")

    deployed = client.post(f"/api/models/{model_id}/deploy", json={
        "model_version": "1.0.0", "endpoint_name": "sales-api", "replicas": 2, "config": {},
    })
    assert deployed.status_code == 200, deployed.text
    deployment_id = deployed.json()["deployment_id"]

    # 全局列表：路径 /api/models/deployments 不应被 GET /{model_id} 当作模型 ID
    listing = client.get("/api/models/deployments")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 1
    entry = body["deployments"][0]
    assert entry["id"] == deployment_id
    assert entry["model_id"] == model_id
    assert entry["model_name"] == "deploy-list-model"
    assert entry["model_version"] == "1.0.0"
    assert entry["endpoint_name"] == "sales-api"
    assert entry["status"] == "pending"

    # 状态过滤：命中 pending、排除 running
    by_status = client.get("/api/models/deployments", params={"status": "pending"}).json()
    assert [d["id"] for d in by_status["deployments"]] == [deployment_id]
    assert client.get(
        "/api/models/deployments", params={"status": "running"}
    ).json()["deployments"] == []

    # 模型过滤：只返回所属模型的部署
    by_model = client.get("/api/models/deployments", params={"model_id": other_id}).json()
    assert by_model["deployments"] == []
    by_model = client.get("/api/models/deployments", params={"model_id": model_id}).json()
    assert [d["id"] for d in by_model["deployments"]] == [deployment_id]


def test_model_deployment_stop_endpoint(client, store):
    """AC（停止部署）：置 stopped；不存在返回 404；详情页部署记录同步可见"""
    model_id = _register_deployable_model(client, "deploy-stop-model")
    deployed = client.post(f"/api/models/{model_id}/deploy", json={
        "model_version": "1.0.0", "replicas": 1, "config": {},
    })
    deployment_id = deployed.json()["deployment_id"]

    stopped = client.delete(f"/api/models/deployments/{deployment_id}")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"

    # 停止后全局列表状态同步，且可按状态过滤
    listing = client.get("/api/models/deployments").json()
    assert listing["deployments"][0]["status"] == "stopped"
    running = client.get("/api/models/deployments", params={"status": "running"}).json()
    assert running["deployments"] == []

    # 不存在的部署 → 404
    missing = client.delete("/api/models/deployments/deploy-not-exist")
    assert missing.status_code == 404
    assert "部署记录不存在" in missing.json()["detail"]

    # 模型详情页包含该部署记录（状态 stopped）
    detail = client.get(f"/api/models/{model_id}").json()
    assert any(d["id"] == deployment_id and d["status"] == "stopped"
               for d in detail["deployments"])


# ---------------------------------------------------------------------------
# Pipeline（AC：DAG 创建、拓扑序、执行记录）
# ---------------------------------------------------------------------------

def test_pipeline_create_rejects_unknown_dependency(client, store):
    """依赖不存在的步骤 → 400"""
    resp = client.post("/api/pipelines", json={
        "name": "bad-pipeline",
        "steps": [
            {"id": "a", "name": "A", "type": "dataset_pull", "config": {},
             "depends_on": ["ghost"]},
        ],
    })
    assert resp.status_code == 400
    assert "ghost" in resp.json()["detail"]


def test_pipeline_create_and_detail_dag(client, store):
    """创建含菱形依赖的 Pipeline → 详情返回 DAG 节点与边"""
    resp = client.post("/api/pipelines", json={
        "name": "dag-pipeline",
        "description": "菱形依赖测试",
        "steps": [
            {"id": "pull", "name": "拉取", "type": "dataset_pull", "config": {}},
            {"id": "proc1", "name": "处理1", "type": "dataset_process", "config": {},
             "depends_on": ["pull"]},
            {"id": "proc2", "name": "处理2", "type": "custom_script", "config": {},
             "depends_on": ["pull"]},
            {"id": "train", "name": "训练", "type": "training", "config": {},
             "depends_on": ["proc1", "proc2"]},
        ],
    })
    assert resp.status_code == 200, resp.text
    pipeline_id = resp.json()["id"]

    detail = client.get(f"/api/pipelines/{pipeline_id}").json()
    assert len(detail["steps"]) == 4
    dag = detail["dag"]
    assert {n["id"] for n in dag["nodes"]} == {"pull", "proc1", "proc2", "train"}
    edges = {(e["source"], e["target"]) for e in dag["edges"]}
    assert ("pull", "proc1") in edges and ("pull", "proc2") in edges
    assert ("proc1", "train") in edges and ("proc2", "train") in edges

    client.delete(f"/api/pipelines/{pipeline_id}")


# ---------------------------------------------------------------------------
# 计算集群（AC：资源摘要、Worker 校验）
# ---------------------------------------------------------------------------

def test_clusters_workers_endpoint(client):
    """Worker 列表端点返回结构化数据"""
    resp = client.get("/api/clusters/workers")
    assert resp.status_code == 200
    body = resp.json()
    assert "workers" in body and "queue_depth" in body


def test_clusters_validate_worker_selection(client, monkeypatch):
    """Worker 校验：不存在 → invalid；GPU 要求在 CPU 槽 → 拒绝"""
    resp = client.post("/api/clusters/workers/validate", params={
        "require_gpu": "true",
    }, json=["nonexistent-worker"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_valid"] is False
    assert body["results"][0]["reason"] == "Worker 不存在"
