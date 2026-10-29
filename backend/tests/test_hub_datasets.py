"""Hub 维护、按需缓存、训练与共享存储的行为测试；仅隔离外部 Hub 网络。"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import datasets
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import config
from app.dataset_manager import DatasetManager
from app.storage import ArtifactStore, get_store, job_key, resolve_dataset_for_worker
from tests.conftest import job_status, wait_until

SHA = "a" * 40


@pytest.fixture(scope="module")
def client():
    """使用独立训练队列，避免停止其他 API 测试共用的管理器。"""
    from app.main import app
    from app.coordination import EmbeddedJobQueue, EmbeddedLeaseStore
    from app.jobs import JobManager
    import app.routers.jobs as jobs

    manager = JobManager(queue=EmbeddedJobQueue(), lease=EmbeddedLeaseStore(),
                         start_workers=True, start_watchdog=False)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(jobs, "manager", manager)
            with TestClient(app) as api:
                yield api
    finally:
        manager.stop()


@pytest.fixture
def hub(monkeypatch, store):
    """用真实流式 Dataset 替换外部 Hub，保留校验、CSV、存储和训练逻辑。"""
    import huggingface_hub
    import app.routers.common as common
    import app.routers.datasets as routes

    manager = DatasetManager(config.DATASETS_DIR)
    monkeypatch.setattr(routes, "dataset_manager", manager)
    monkeypatch.setattr(common, "dataset_manager", manager)
    state = {"manager": manager, "loads": [], "infos": [], "gated": False,
             "rows": [{"feature": i % 2, "target": i % 2} for i in range(120)],
             "error": None, "info_error": None, "features": None}

    def dataset_info(self, repo_id, **kwargs):
        """替代仓库元信息请求并断言不会使用本机登录凭证。"""
        state["infos"].append((repo_id, kwargs))
        assert kwargs["token"] is False and kwargs["timeout"] == 20
        if state["info_error"]:
            raise state["info_error"]
        return SimpleNamespace(sha=SHA, private=False, gated=state["gated"])

    def generate():
        """把固定行交给真实的 Hugging Face 流式迭代器。"""
        yield from state["rows"]

    def load_dataset(repo_id, **kwargs):
        """仅替换网络边界，记录所有数据下载参数。"""
        state["loads"].append((repo_id, kwargs))
        if state["error"]:
            raise state["error"]
        return datasets.IterableDataset.from_generator(generate, features=state["features"])

    monkeypatch.setattr(huggingface_hub.HfApi, "dataset_info", dataset_info)
    monkeypatch.setattr(datasets, "load_dataset", load_dataset)
    return state


def _register(client, **extra):
    """按平台请求模型建立一个默认远程引用。"""
    payload = {"repo_id": "test/public", "storage_mode": "remote"}
    payload.update(extra)
    return client.post("/api/datasets/hub", json=payload)


def test_remote_reference_pins_revision_without_downloading(client, hub):
    """保存和查询列表仅访问仓库元信息，不创建 CSV 或触发数据拉取。"""
    result = _register(client, revision="v1", subset="plain", split="test")
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["hub"] == {"repo_id": "test/public", "revision": SHA,
                            "requested_revision": "v1", "subset": "plain", "split": "test"}
    assert saved["cache_status"] == "remote" and saved["columns"] == []
    listed = client.get("/api/datasets").json()["datasets"]
    assert listed[0]["id"] == saved["id"] and not hub["loads"]
    assert not list(config.DATASETS_DIR.rglob("*.csv"))


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_import_and_first_preview_cache_data(client, hub, mode):
    """立即下载与首次预览得到相同表格，后续预览和显式下载复用缓存。"""
    result = _register(client, storage_mode=mode)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert len(hub["loads"]) == (1 if mode == "local" else 0)
    detail = client.get(f"/api/datasets/{saved['id']}").json()
    assert detail["cache_status"] == "ready" and detail["rows"] == 120
    assert detail["preview"][0] == {"feature": 0, "target": 0}
    assert detail["columns"] == ["feature", "target"]
    for _ in range(2):
        assert client.post(f"/api/datasets/{saved['id']}/materialize").status_code == 200
        assert client.get(f"/api/datasets/{saved['id']}").status_code == 200
    assert len(hub["loads"]) == 1
    kwargs = hub["loads"][0][1]
    assert kwargs["revision"] == SHA and kwargs["streaming"] is True
    assert kwargs["trust_remote_code"] is False and kwargs["token"] is False
    assert (config.DATASETS_DIR / "raw" / f"{saved['id']}.csv").is_file()
    assert (config.DATASETS_DIR / "processed" / f"{saved['id']}.csv").is_file()


@pytest.mark.parametrize("action", ["stats", "profile", "process", "worker"])
def test_first_use_downloads_for_each_data_consumer(client, hub, tmp_path, action):
    """统计、画像、处理和本地执行槽首次使用远程引用均能解析真实数据。"""
    ds_id = _register(client).json()["id"]
    if action == "process":
        response = client.post(f"/api/datasets/{ds_id}/process", json={"operations": []})
        assert response.status_code == 200, response.text
    elif action == "worker":
        path = resolve_dataset_for_worker(ds_id, tmp_path)
        assert path.is_file() and len(path.read_text().splitlines()) == 121
    else:
        response = client.get(f"/api/datasets/{ds_id}/{action}")
        assert response.status_code == 200, response.text
        if action == "stats":
            assert response.json()["total_rows"] == 120
    assert len(hub["loads"]) == 1
    assert hub["manager"].get_dataset(ds_id)["cache_status"] == "ready"


@pytest.mark.parametrize("extra", [
    {"repo_id": ""}, {"repo_id": "https://example.com/data"}, {"repo_id": "../secret"},
    {"repo_id": "a/../../b"}, {"repo_id": "a" * 194}, {"split": ""},
    {"split": "train[:5]"}, {"revision": ""}, {"revision": "x\nsecret"},
    {"subset": " "}, {"storage_mode": "other"}, {"tags": [""]},
])
def test_invalid_sources_do_not_reach_hub(client, hub, extra):
    """空值、超长、路径和非法选项在网络访问前被拒绝。"""
    assert _register(client, **extra).status_code == 422
    assert not hub["infos"] and not hub["loads"]
    assert not list(hub["manager"].metadata_dir.glob("*.json"))


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_hub_metadata_failures_are_clear_and_redacted(client, hub, status):
    """仓库不存在、需授权和依赖故障返回明确状态，不回显远端敏感内容。"""
    error = RuntimeError("hf_secret /private/path")
    error.response = SimpleNamespace(status_code=status)
    hub["info_error"] = error
    response = _register(client)
    assert response.status_code == (status if status in {403, 404} else 403 if status == 401 else 503)
    assert "hf_secret" not in response.text and "/private/path" not in response.text
    assert not list(hub["manager"].metadata_dir.glob("*.json"))


def test_gated_dataset_is_rejected(client, hub):
    """首期明确拒绝需授权的数据集，不存入不可使用的引用。"""
    hub["gated"] = "auto"
    assert _register(client).status_code == 403
    assert not hub["loads"]


@pytest.mark.parametrize("error, expected", [(TimeoutError("secret"), 503), (ValueError("secret"), 400)])
def test_download_failure_retains_reference_and_retry_works(client, hub, error, expected):
    """下载失败不留下 CSV，保存可见错误；重试同一个 ID 后恢复使用。"""
    hub["error"] = error
    assert _register(client, storage_mode="local").status_code == expected
    saved = client.get("/api/datasets").json()["datasets"][0]
    assert saved["cache_status"] == "error" and "secret" not in saved["last_error"]
    assert not list(config.DATASETS_DIR.rglob("*.csv"))
    hub["error"] = None
    assert client.post(f"/api/datasets/{saved['id']}/materialize").json()["cache_status"] == "ready"


@pytest.mark.parametrize("setting, limit, expected", [
    ("max_rows", 2, 200), ("max_rows", 1, 400),
    ("max_columns", 2, 200), ("max_columns", 1, 400),
    ("max_file_size", 26, 200), ("max_file_size", 25, 400),
])
def test_download_limits_are_exact_without_truncation(client, hub, setting, limit, expected):
    """行、列和 UTF-8 文件大小的临界值允许，超限则完整失败且不截断。"""
    hub["rows"] = [{"feature": 0, "target": 0}, {"feature": 1, "target": 1}]
    setattr(hub["manager"], setting, limit)
    response = _register(client, storage_mode="local")
    assert response.status_code == expected, response.text
    saved = client.get("/api/datasets").json()["datasets"][0]
    assert saved["cache_status"] == ("ready" if expected == 200 else "error")
    if expected == 200:
        assert saved["rows"] == 2 and saved["file_size"] == 26
    else:
        assert not list(config.DATASETS_DIR.rglob("*.csv"))


@pytest.mark.parametrize("kind", ["empty", "media", "nested_media", "text"])
def test_supported_text_and_unsupported_data(client, hub, kind):
    """空集合、媒体字段失败，嵌套对话文本以标准 JSON 保持结构。"""
    if kind == "empty":
        hub["rows"] = []
    elif kind == "media":
        hub["features"] = datasets.Features({"image": datasets.Image()})
    elif kind == "nested_media":
        hub["rows"] = [{"messages": {"image": b"binary"}}]
    else:
        hub["rows"] = [{"messages": [{"role": "user", "content": "你好\n世界"}]}]
    response = _register(client, storage_mode="local")
    assert response.status_code == (200 if kind == "text" else 400), response.text
    if kind == "text":
        preview = client.get(f"/api/datasets/{response.json()['id']}").json()["preview"]
        assert json.loads(preview[0]["messages"]) == hub["rows"][0]["messages"]
    else:
        assert not list(config.DATASETS_DIR.rglob("*.csv"))


def test_concurrent_downloads_share_one_cache(client, hub, monkeypatch):
    """两个同时请求只下载一次，后续请求复用同一缓存。"""
    ds_id = _register(client).json()["id"]
    manager = hub["manager"]
    original = manager._stream_hub_csv
    started, release = threading.Event(), threading.Event()

    def slow_download(metadata, output):
        """在真实转换前暂停，制造两个进程/线程共享引用的状态冲突。"""
        started.set()
        assert release.wait(10)
        return original(metadata, output)

    monkeypatch.setattr(manager, "_stream_hub_csv", slow_download)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.ensure_local, ds_id)
        assert started.wait(5)
        second = pool.submit(manager.ensure_local, ds_id)
        release.set()
        assert first.result()["cache_status"] == second.result()["cache_status"] == "ready"
    assert len(hub["loads"]) == 1


def test_delete_waits_for_inflight_download(client, hub, monkeypatch):
    """删除等待下载释放锁，最终不留下文件，也不允许数据集复活。"""
    ds_id = _register(client).json()["id"]
    manager = hub["manager"]
    original = manager._stream_hub_csv
    started, release, deleting = threading.Event(), threading.Event(), threading.Event()

    def slow_download(metadata, output):
        """保持下载中的状态，直到删除请求已经发起。"""
        started.set()
        assert release.wait(10)
        return original(metadata, output)

    def delete():
        """在另一线程进入删除流程。"""
        deleting.set()
        return manager.delete_dataset(ds_id)

    monkeypatch.setattr(manager, "_stream_hub_csv", slow_download)
    with ThreadPoolExecutor(max_workers=2) as pool:
        download = pool.submit(manager.ensure_local, ds_id)
        assert started.wait(5)
        removal = pool.submit(delete)
        assert deleting.wait(5)
        assert not removal.done()
        release.set()
        assert download.result()["cache_status"] == "ready"
        assert removal.result()
    assert manager.get_dataset(ds_id) is None
    assert not list(config.DATASETS_DIR.rglob("*.csv"))
    with pytest.raises(HTTPException) as missing:
        manager.ensure_local(ds_id)
    assert missing.value.status_code == 404


def test_object_store_sync_failure_rolls_back_and_remote_worker_reads_cache(client, hub, monkeypatch, tmp_path):
    """同步失败保留引用且不发布缓存；重试后远端执行槽可从共享仓储取得同一 CSV。"""
    class RemoteStore(ArtifactStore):
        """模拟外部 S3 网络，实际运行公共仓储的文件落盘实现。"""

        def __init__(self):
            """初始化可恢复的外部存储故障。"""
            self.objects = {}
            self.fail = True

        def write_bytes(self, key, value):
            """故障恢复后原子保存对象。"""
            if self.fail:
                raise OSError("storage secret")
            self.objects[key] = value

        def read_bytes(self, key):
            """向远端执行槽返回保存的真实 CSV 内容。"""
            return self.objects.get(key)

        def exists(self, key):
            """查询远端对象存在性。"""
            return key in self.objects

    remote = RemoteStore()
    monkeypatch.setattr("app.storage.get_store", lambda: remote)
    ds_id = _register(client).json()["id"]
    response = client.post(f"/api/datasets/{ds_id}/materialize")
    assert response.status_code == 503 and "storage secret" not in response.text
    assert hub["manager"].get_dataset(ds_id)["cache_status"] == "error"
    assert not list(config.DATASETS_DIR.rglob("*.csv"))
    remote.fail = False
    assert client.post(f"/api/datasets/{ds_id}/materialize").status_code == 200
    csv_path = resolve_dataset_for_worker(ds_id, tmp_path / "remote-worker")
    assert csv_path.read_bytes() == remote.objects[f"datasets/{ds_id}.csv"]
    assert len(csv_path.read_text().splitlines()) == 121


def test_hub_reference_trains_reproducibly_and_predicts(client, hub):
    """远程引用直接提交训练，验证指标、日志、权重、重复训练与缺失输入的稳定预测。"""
    ds_id = _register(client).json()["id"]
    payload = {"dataset_id": ds_id, "target": "target", "task": "classification",
               "epochs": 2, "batch_size": 32, "hidden_size": 8, "hidden_layers": 1, "seed": 42}
    summaries = []
    for _ in range(2):
        response = client.post("/api/train", json=payload)
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]
        assert wait_until(lambda: job_status(get_store(), job_id) in {"completed", "failed"}, 30)
        detail = client.get(f"/api/jobs/{job_id}").json()
        assert detail["status"] == "completed", detail
        assert len(detail["metrics"]) == 2 and detail["summary"]["final"]["val_accuracy"] is not None
        assert get_store().exists(job_key(job_id, "best.pt"))
        assert client.get(f"/api/jobs/{job_id}/logs").json()["lines"]
        summaries.append(detail["summary"]["final"]["val_accuracy"])
        for row in ({"feature": 1}, {}, {"feature": "invalid"}):
            first = client.post(f"/api/jobs/{job_id}/predict", json={"row": row})
            second = client.post(f"/api/jobs/{job_id}/predict", json={"row": row})
            assert first.status_code == second.status_code == 200
            assert first.json()["prediction"] == second.json()["prediction"]
    assert summaries[0] == summaries[1] and len(hub["loads"]) == 1
