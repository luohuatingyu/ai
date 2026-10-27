"""存储平面测试：ArtifactStore 原子写、checkpoint 版本化、数据集双布局解析。

对应验收标准：
- 单机零依赖（STORAGE_BACKEND=local）可完整运行（文件即权威状态平面）；
- checkpoint 版本化 key 与保留策略（支持损坏回退）；
- 数据集两套存储布局（旧扁平 / 新结构）读写兼容。
"""
from __future__ import annotations

import json

import pytest

from app import config, jobstore
from app.storage import (
    ArtifactStoreUnavailableError,
    S3ArtifactStore,
    checkpoint_key,
    get_store,
    job_key,
    parse_checkpoint_epoch,
    prune_checkpoints,
    resolve_dataset_csv_local,
    sync_dataset_to_store,
)


# ---------------------------------------------------------------------------
# 基础读写（AC：本地存储权威、原子覆盖）
# ---------------------------------------------------------------------------

def test_write_read_bytes_text_roundtrip(store):
    """文本/二进制写读一致，exists/list_keys/delete 语义正确。"""
    st = store
    st.write_bytes(job_key("j1", "log.txt"), "hello\n".encode("utf-8"))
    assert st.read_text(job_key("j1", "log.txt")) == "hello\n"
    st.write_bytes(job_key("j1", "best.pt"), b"\x00\x01bin")
    assert st.read_bytes(job_key("j1", "best.pt")) == b"\x00\x01bin"
    assert st.exists(job_key("j1", "best.pt"))
    assert not st.exists(job_key("j1", "missing"))
    keys = set(st.list_keys("jobs/j1/"))
    assert "jobs/j1/log.txt" in keys and "jobs/j1/best.pt" in keys
    st.delete(job_key("j1", "best.pt"))
    assert not st.exists(job_key("j1", "best.pt"))


def test_write_json_roundtrip(store):
    """JSON 写读往返（任务记录的存储形态）；缺失 key 返回 None。"""
    st = store
    payload = {"id": "j2", "status": "queued", "nested": {"a": 1}}
    st.write_json(job_key("j2", "job.json"), payload)
    assert st.read_json(job_key("j2", "job.json")) == payload
    assert st.read_json(job_key("j2", "missing.json")) is None


def test_overwrite_is_full_replacement(store):
    """整文件覆盖写：后写内容完整替换（无追加残留），metrics 回滚依赖此语义。"""
    st = store
    st.write_bytes(job_key("j3", "metrics.jsonl"), "line1\nline2\n".encode("utf-8"))
    st.write_bytes(job_key("j3", "metrics.jsonl"), "only\n".encode("utf-8"))
    assert st.read_text(job_key("j3", "metrics.jsonl")) == "only\n"


def test_append_text(store):
    """追加写：日志通道语义（多次 append 按序保留）。"""
    st = store
    st.append_text(job_key("j4", "log.txt"), "a\n")
    st.append_text(job_key("j4", "log.txt"), "b\n")
    assert st.read_text(job_key("j4", "log.txt")) == "a\nb\n"


def test_s3_outage_is_not_treated_as_missing():
    """S3 网络或鉴权故障必须向上抛出，不能伪装成产物不存在。"""
    class FailingClient:
        def get_object(self, *args, **kwargs):
            raise ConnectionError("connection reset")

        def stat_object(self, *args, **kwargs):
            raise ConnectionError("connection reset")

        def remove_object(self, *args, **kwargs):
            raise ConnectionError("connection reset")

    st = object.__new__(S3ArtifactStore)
    st._client = FailingClient()
    st.bucket = "test-bucket"

    with pytest.raises(ArtifactStoreUnavailableError, match="读取对象存储"):
        st.read_bytes("jobs/j4/log.txt")
    with pytest.raises(ArtifactStoreUnavailableError, match="检查对象存储"):
        st.exists("jobs/j4/log.txt")
    with pytest.raises(ArtifactStoreUnavailableError, match="删除对象存储"):
        st.delete("jobs/j4/log.txt")


def test_s3_missing_object_keeps_missing_semantics():
    """仅 NoSuchKey 继续表示对象不存在，避免把正常缺失当成基础设施故障。"""
    class MissingObject(Exception):
        code = "NoSuchKey"

    class MissingClient:
        def get_object(self, *args, **kwargs):
            raise MissingObject()

        def stat_object(self, *args, **kwargs):
            raise MissingObject()

        def remove_object(self, *args, **kwargs):
            raise MissingObject()

    st = object.__new__(S3ArtifactStore)
    st._client = MissingClient()
    st.bucket = "test-bucket"

    assert st.read_bytes("jobs/j4/missing.txt") is None
    assert st.exists("jobs/j4/missing.txt") is False
    st.delete("jobs/j4/missing.txt")


def test_dataset_sync_propagates_remote_failure(monkeypatch, tmp_path):
    """S3 同步失败应让导入调用方回滚，不能留下无法被远端 worker 使用的数据集。"""
    class FailingRemoteStore:
        def write_file(self, *args, **kwargs):
            raise ArtifactStoreUnavailableError("对象存储暂时不可用")

    csv = tmp_path / "dataset.csv"
    csv.write_text("feature,target\n1,0\n", encoding="utf-8")
    monkeypatch.setattr("app.storage.get_store", lambda: FailingRemoteStore())

    with pytest.raises(ArtifactStoreUnavailableError, match="暂时不可用"):
        sync_dataset_to_store("ds-remote-failure", csv)


# ---------------------------------------------------------------------------
# checkpoint 版本化（AC：损坏可回退、保留最近 N 个）
# ---------------------------------------------------------------------------

def test_checkpoint_key_parsing(store):
    """版本化 key 生成与 epoch 解析往返；非法 key 返回 None。"""
    key = checkpoint_key("j5", 42)
    assert key == "jobs/j5/checkpoints/ckpt-e0042.pt"
    assert parse_checkpoint_epoch(key) == 42
    assert parse_checkpoint_epoch("jobs/j5/best.pt") is None


def test_prune_checkpoints_keeps_latest_n(store):
    """保留最近 N 个版本化 checkpoint（旧版本自动清理，可回退窗口受控）。"""
    st = store
    for e in (1, 2, 3, 4, 5):
        st.write_bytes(checkpoint_key("j6", e), b"ckpt-%d" % e)
    prune_checkpoints(st, "j6", 3)
    left = sorted(parse_checkpoint_epoch(k) for k in st.list_keys("jobs/j6/checkpoints/"))
    assert left == [3, 4, 5]


def test_prune_checkpoints_keep_zero_or_negative_keeps_all(store):
    """keep<=0 视为不清理（防御性：配置错误不丢数据）。"""
    st = store
    for e in (1, 2):
        st.write_bytes(checkpoint_key("j7", e), b"c")
    prune_checkpoints(st, "j7", 0)
    assert len(list(st.list_keys("jobs/j7/checkpoints/"))) == 2


# ---------------------------------------------------------------------------
# 数据集双布局兼容（AC：旧扁平 {id}.csv + meta 与新结构 processed/raw 等价可读）
# ---------------------------------------------------------------------------

def test_dataset_layout_flat(store, tmp_path):
    """旧扁平布局：{id}.csv 可被解析。"""
    csv = config.DATASETS_DIR / "ds-flat.csv"
    csv.write_text("a,y\n1,0\n2,1\n", encoding="utf-8")
    assert resolve_dataset_csv_local("ds-flat") == csv


def test_dataset_layout_flat_preferred_over_processed(store):
    """双布局并存时旧扁平路径优先（与 resolve_dataset_csv_local 实现一致）。"""
    flat = config.DATASETS_DIR / "ds-x.csv"
    flat.write_text("old\n", encoding="utf-8")
    processed = config.DATASETS_DIR / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    (processed / "ds-x.csv").write_text("new\n", encoding="utf-8")
    assert resolve_dataset_csv_local("ds-x") == flat


def test_dataset_layout_processed_used_when_flat_absent(store):
    """仅新结构 processed/ 存在时正常解析（双格式兼容）。"""
    processed = config.DATASETS_DIR / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    (processed / "ds-p.csv").write_text("p\n", encoding="utf-8")
    assert resolve_dataset_csv_local("ds-p") == processed / "ds-p.csv"


def test_dataset_layout_raw_fallback(store):
    """新结构：processed 缺失时回退 raw/。"""
    raw = config.DATASETS_DIR / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "ds-y.csv").write_text("raw\n", encoding="utf-8")
    assert resolve_dataset_csv_local("ds-y") == raw / "ds-y.csv"


def test_dataset_id_validation(store):
    """路径穿越/非法 ID 直接拒绝（安全边界）。"""
    with pytest.raises(ValueError):
        resolve_dataset_csv_local("../etc/passwd")
    with pytest.raises(ValueError):
        resolve_dataset_csv_local("a/b")


def test_sync_dataset_local_noop(store):
    """local 后端无需同步（本身即共享权威），返回 False。"""
    csv = config.DATASETS_DIR / "ds-s.csv"
    csv.write_text("x\n", encoding="utf-8")
    assert sync_dataset_to_store("ds-s", csv) is False


# ---------------------------------------------------------------------------
# jobstore：记录 CRUD / metrics 回滚（续训一致性协议）
# ---------------------------------------------------------------------------

def test_jobstore_crud_and_update(store):
    """read/write/update 记录；update 合并字段并保证 id 不变。"""
    from tests.conftest import make_job_record

    jobstore.write_job(store, make_job_record("jc1"))
    rec = jobstore.read_job(store, "jc1")
    assert rec["status"] == "queued"
    updated = jobstore.update_job(store, "jc1", status="running", worker_id="w1")
    assert updated["status"] == "running" and updated["worker_id"] == "w1"
    assert updated["spec"]["params"]["seed"] == 42  # 未触碰字段保留
    with pytest.raises(KeyError):
        jobstore.update_job(store, "no-such", status="running")


def test_truncate_metrics_to_epoch(store):
    """metrics 回滚到 checkpoint epoch：丢弃孤儿记录，保留 <= epoch 的历史。"""
    from tests.conftest import make_job_record

    jobstore.write_job(store, make_job_record("jc2"))
    lines = "".join(json.dumps({"epoch": e, "val_loss": 1.0 / e}) + "\n"
                    for e in range(1, 7))
    store.write_bytes(job_key("jc2", "metrics.jsonl"), lines.encode("utf-8"))
    out = jobstore.truncate_metrics_to_epoch(store, "jc2", 4)
    epochs = [r["epoch"] for r in jobstore.parse_metrics_text(out)]
    assert epochs == [1, 2, 3, 4]
    # 幂等：再次截断内容不变
    assert jobstore.truncate_metrics_to_epoch(store, "jc2", 4) == out


def test_truncate_metrics_bad_lines_tolerated(store):
    """半行/坏行静默跳过（崩溃时可能留下不完整 JSON 行）。"""
    from tests.conftest import make_job_record

    jobstore.write_job(store, make_job_record("jc3"))
    store.write_bytes(job_key("jc3", "metrics.jsonl"),
                      '{"epoch":1}\n{"epoch":2\n{"epoch":3}\n'.encode("utf-8"))
    out = jobstore.truncate_metrics_to_epoch(store, "jc3", 2)
    assert [r["epoch"] for r in jobstore.parse_metrics_text(out)] == [1]


def test_job_log_helpers(store):
    """任务日志追加/读取（带时间戳行）。"""
    from tests.conftest import make_job_record

    jobstore.write_job(store, make_job_record("jc4"))
    jobstore.append_job_log(store, "jc4", "第一步")
    jobstore.append_job_log(store, "jc4", "第二步")
    text = jobstore.read_job_log(store, "jc4")
    assert "第一步" in text and "第二步" in text


@pytest.mark.parametrize(
    ("endpoint", "expected_region"),
    [
        ("oss-cn-shanghai.aliyuncs.com", "cn-shanghai"),
        ("oss-cn-shanghai-internal.aliyuncs.com:443", "cn-shanghai"),
        ("s3.oss-cn-shanghai.aliyuncs.com", "cn-shanghai"),
        ("s3.oss-cn-hangzhou.aliyuncs.com", "cn-hangzhou"),
        ("s3.oss-cn-shanghai-internal.aliyuncs.com:443", "cn-shanghai"),
        ("seaweedfs:8333", None),
        ("s3.oss-accelerate.aliyuncs.com", None),
    ],
)
def test_s3_store_infers_region_only_for_regional_oss_endpoints(
    monkeypatch, endpoint, expected_region
):
    """阿里云区域端点跳过路径式区域探测，其他 S3 服务保持 SDK 默认行为。"""
    from app.storage import S3ArtifactStore

    captured = {}

    class CapturingMinio:
        def __init__(self, endpoint, **kwargs):
            captured["endpoint"] = endpoint
            captured.update(kwargs)

        def bucket_exists(self, bucket):
            return True

    monkeypatch.setattr("minio.Minio", CapturingMinio)
    S3ArtifactStore(
        endpoint=endpoint,
        access_key="test-access",
        secret_key="test-secret",
        bucket="test-bucket",
        secure=True,
    )

    assert captured["endpoint"] == endpoint
    assert captured["region"] == expected_region


# ---------------------------------------------------------------------------
# S3 后端集成（需 MTP_TEST_S3 指向 S3 兼容端点，如 SeaweedFS；默认跳过）
# ---------------------------------------------------------------------------

def test_s3_dataset_sync_and_worker_resolve(store, monkeypatch, tmp_path):
    """S3 模式数据集同步链路：入库 CSV → sync_dataset_to_store → worker 拉取（需 MTP_TEST_S3）。"""
    import os

    endpoint = os.environ.get("MTP_TEST_S3")
    if not endpoint:
        pytest.skip("未配置 MTP_TEST_S3（S3 兼容端点），跳过 S3 数据集同步测试")
    from app.storage import (
        S3ArtifactStore,
        resolve_dataset_for_worker,
        sync_dataset_to_store,
    )

    s3 = S3ArtifactStore(endpoint=endpoint, access_key="minioadmin",
                         secret_key="minioadmin", bucket="mtp-pytest-s3", secure=False)
    monkeypatch.setattr("app.storage.get_store", lambda: s3)
    ds_id = "ds-sync-test"
    csv = config.DATASETS_DIR / f"{ds_id}.csv"
    csv.write_text("a,target\n1,0\n2,1\n", encoding="utf-8")
    try:
        assert sync_dataset_to_store(ds_id, csv) is True
        pulled = resolve_dataset_for_worker(ds_id, tmp_path)
        assert pulled.read_text(encoding="utf-8") == "a,target\n1,0\n2,1\n"
    finally:
        s3.delete(f"datasets/{ds_id}.csv")


def test_s3_backend_roundtrip():
    """S3 兼容对象存储：写/读/存在性/列表/删除往返（SeaweedFS / OSS / COS 通用）。"""
    import os

    endpoint = os.environ.get("MTP_TEST_S3")
    if not endpoint:
        pytest.skip("未配置 MTP_TEST_S3（S3 兼容端点），跳过 S3 后端集成测试")
    from app.storage import S3ArtifactStore

    st = S3ArtifactStore(endpoint=endpoint, access_key="minioadmin",
                         secret_key="minioadmin", bucket="mtp-pytest-s3", secure=False)
    key = "jobs/it-s3/test.txt"
    try:
        st.write_bytes(key, "s3-roundtrip".encode("utf-8"))
        assert st.read_text(key) == "s3-roundtrip"
        assert st.exists(key)
        assert key in st.list_keys("jobs/it-s3/")
        st.delete(key)
        assert not st.exists(key)
    finally:
        for k in st.list_keys("jobs/it-s3/"):
            st.delete(k)  # 清理测试遗留对象
