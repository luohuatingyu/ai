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

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from app import config, jobstore  # noqa: E402
from app.storage import get_store  # noqa: E402
from app.storage import job_key  # noqa: E402
from tests.conftest import job_status, wait_until  # noqa: E402


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


def test_train_e2e_regression(client, make_dataset):
    ds = make_dataset(kind="regression")
    r = client.post("/api/train", json=_train_payload(ds, epochs=3,
                                                      task="regression"))
    assert r.status_code == 200
    job = r.json()["job_id"]
    assert wait_until(lambda: job_status(get_store(), job) == "completed", 60)
    detail = client.get(f"/api/jobs/{job}").json()
    assert "val_r2" in detail["summary"]["final"]


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
