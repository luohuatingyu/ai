"""Excel 导入 API 回归：覆盖现代与旧版工作簿及损坏输入。"""
from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    """提供数据集导入接口客户端。"""
    with TestClient(app) as test_client:
        yield test_client


def _xlsx_payload() -> bytes:
    """创建含两行样例记录的 xlsx 工作簿字节。"""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["feature", "target"])
    sheet.append([1, "a"])
    sheet.append([2, "b"])
    payload = io.BytesIO()
    workbook.save(payload)
    return payload.getvalue()


def _assert_imported_dataset(client: TestClient, response) -> None:
    """校验导入结果、行列元数据和预览值。"""
    assert response.status_code == 200, response.text
    dataset_id = response.json()["id"]
    detail_response = client.get(f"/api/datasets/{dataset_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["rows"] == 2
    assert detail["columns"] == ["feature", "target"]
    assert set(detail["column_types"]) == {"feature", "target"}
    assert detail["preview"][1]["feature"] == 2
    assert detail["preview"][1]["target"] == "b"


def test_import_xlsx_dataset(client, store):
    """正常场景：xlsx 工作簿导入后可读取行列、类型和预览。"""
    response = client.post(
        "/api/datasets/import",
        files={"file": ("records.xlsx", _xlsx_payload(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    _assert_imported_dataset(client, response)


def test_import_legacy_xls_dataset(client, store):
    """兼容场景：旧版 xls 工作簿经 xlrd 引擎导入并保留数据。"""
    payload = (Path(__file__).parent / "fixtures" / "legacy_dataset.xls").read_bytes()
    response = client.post(
        "/api/datasets/import",
        files={"file": ("records.xls", payload, "application/vnd.ms-excel")},
    )

    _assert_imported_dataset(client, response)


@pytest.mark.parametrize(
    "filename,content",
    [("broken.xlsx", b"not an xlsx workbook"), ("broken.xls", b"not an xls workbook")],
)
def test_import_rejects_corrupt_excel_without_creating_dataset(client, store, filename, content):
    """异常场景：损坏的 Excel 返回 400，且不产生数据集记录。"""
    response = client.post(
        "/api/datasets/import",
        files={"file": (filename, content, "application/octet-stream")},
    )

    assert response.status_code == 400
    assert client.get("/api/datasets").json()["total"] == 0
