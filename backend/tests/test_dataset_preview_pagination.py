"""数据集预览分页 API：覆盖正常页、边界页、非法参数和旧调用兼容。"""
from __future__ import annotations

import pandas as pd
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture(scope="module")
def client():
    """提供独立的 FastAPI 测试客户端。"""
    from app.main import app, manager

    with fastapi_testclient.TestClient(app) as test_client:
        yield test_client
    manager.stop()


def _save_dataset(store) -> str:
    """写入六行数据，保证可验证跨页顺序和最后一页边界。"""
    from app import config
    from app.dataset_manager import DatasetManager

    manager = DatasetManager(config.DATASETS_DIR)
    metadata = manager.save_dataset(
        pd.DataFrame({"id": list(range(6)), "label": ["a", "b", "c", "d", "e", "f"]}),
        {"original_filename": "paged.csv"},
    )
    return metadata["id"]


def test_preview_returns_requested_page_and_metadata(client, store):
    """正常场景：每页只返回请求范围，并提供稳定分页元数据。"""
    dataset_id = _save_dataset(store)

    first = client.get(f"/api/datasets/{dataset_id}?rows=2&offset=0")
    second = client.get(f"/api/datasets/{dataset_id}?rows=2&offset=2")
    last = client.get(f"/api/datasets/{dataset_id}?rows=2&offset=4")

    assert first.status_code == second.status_code == last.status_code == 200
    assert [row["id"] for row in first.json()["preview"]] == [0, 1]
    assert [row["id"] for row in second.json()["preview"]] == [2, 3]
    assert [row["id"] for row in last.json()["preview"]] == [4, 5]
    assert first.json()["preview_page"] == 1
    assert second.json()["preview_page"] == 2
    assert last.json()["preview_page"] == 3
    assert last.json()["preview_page_count"] == 3
    assert first.json()["preview_has_previous"] is False
    assert first.json()["preview_has_next"] is True
    assert last.json()["preview_has_previous"] is True
    assert last.json()["preview_has_next"] is False


def test_preview_default_call_remains_first_page(client, store):
    """兼容场景：未传分页参数的旧调用仍返回前五行。"""
    dataset_id = _save_dataset(store)
    response = client.get(f"/api/datasets/{dataset_id}")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["preview"]] == [0, 1, 2, 3, 4]
    assert response.json()["preview_page"] == 1
    assert response.json()["preview_limit"] == 5


@pytest.mark.parametrize(
    "query",
    ["rows=0", "rows=51", "offset=-1"],
)
def test_preview_rejects_invalid_page_parameters(client, store, query):
    """异常和安全场景：拒绝非法页大小或负偏移，避免绕过读取限制。"""
    dataset_id = _save_dataset(store)
    response = client.get(f"/api/datasets/{dataset_id}?{query}")

    assert response.status_code == 400


def test_preview_out_of_range_returns_empty_page(client, store):
    """边界场景：超出末尾返回空页并保留总页数，不泄漏其他页数据。"""
    dataset_id = _save_dataset(store)
    response = client.get(f"/api/datasets/{dataset_id}?rows=2&offset=6")

    assert response.status_code == 200
    body = response.json()
    assert body["preview"] == []
    assert body["preview_page_count"] == 3
    assert body["preview_has_previous"] is True
    assert body["preview_has_next"] is False
