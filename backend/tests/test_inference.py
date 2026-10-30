"""预测资源选择、租约隔离与真实模型推理回归测试。"""
import threading
import time
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app import config, jobstore
from app.coordination import EmbeddedJobQueue, EmbeddedLeaseStore
from app.inference import InferenceService, inference_key
from app.jobs import JobManager
from app.storage import LocalArtifactStore
from app.storage import job_key
from app.parallel_worker import slot_key
from app.worker import TrainingWorker, JobLeaseLost


def make_worker(service, caps):
    """使用真实 Worker 调度器，隔离硬件定时探测。"""
    return TrainingWorker(store=service.store, queue=service.queue,
                          lease_store=service.lease, capabilities=caps,
                          gpu_probe_enabled=False, log=lambda _: None)


def api_client(service, monkeypatch, task="classification"):
    """挂载真实路由及独立管理器，不启动应用后台线程。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import jobs

    manager = JobManager(store=service.store, queue=service.queue, lease=service.lease,
                         start_workers=False, start_watchdog=False)
    monkeypatch.setattr(jobs, "manager", manager)
    for ident in ("job-a", "job-b"):
        jobstore.write_job(service.store, {"id": ident, "status": "completed", "spec": {"task": task}})
    app = FastAPI()
    app.include_router(jobs.router)
    return TestClient(app)


@pytest.fixture
def setup_inference(tmp_path):
    """为每个用例隔离预测存储、队列及计算槽。"""
    store = LocalArtifactStore(tmp_path / "store")
    queue, lease = EmbeddedJobQueue(), EmbeddedLeaseStore()
    caps = {"worker_id": "chosen", "machine_id": "machine-a",
            "hostname": "host-a", "slot": "cpu0", "device_backend": "cpu",
            "device": "cpu", "state": "idle",
            "gpu": {"available": False, "healthy": True}}
    lease.register_worker("chosen", caps, 60)
    return InferenceService(store, queue, lease), caps


@pytest.mark.parametrize("state", ["degraded", "recovering"])
def test_reject_unhealthy_worker(setup_inference, state):
    """异常或恢复中的计算槽不能接收预测。"""
    service, caps = setup_inference
    service.lease.register_worker("chosen", {**caps, "state": state}, 60)
    with pytest.raises(ValueError, match="设备异常"):
        service.submit("job-a", "classification", "chosen", {"row": {}})
    assert service.queue.inference_queue("chosen").snapshot() == []


def test_recovery_skips_corrupt_record(setup_inference):
    """单个缺字段记录不能阻止其他预测恢复入队。"""
    service, _ = setup_inference
    service.store.write_json(inference_key("infer-" + "0" * 32), {"broken": True})
    record = service.submit("job-a", "classification", "chosen", {"row": {}})
    service.queue.inference_queue("chosen").remove(record["id"])
    service.recover()
    assert service.queue.inference_queue("chosen").snapshot() == [record["id"]]


def test_prediction_recovery_failure_does_not_block_training(setup_inference, monkeypatch):
    """预测存储异常时训练巡检仍补投丢失的训练队列项。"""
    service, _ = setup_inference
    manager = JobManager(store=service.store, queue=service.queue, lease=service.lease,
                         start_workers=False, start_watchdog=False)
    jobstore.write_job(service.store, {"id": "job-a", "status": "queued", "spec": {}})

    def unavailable():
        """模拟外部存储暂时不可用。"""
        raise OSError("prediction storage unavailable")

    monkeypatch.setattr(manager.inference, "recover", unavailable)
    manager._watchdog_once()
    assert service.queue.snapshot() == ["job-a"]


@pytest.mark.parametrize("mode", ["missing", "offline", "disabled", "unsupported", "gpu_missing", "gpu_unhealthy", "kmeans_gpu"])
def test_resource_rejection_does_not_fallback(setup_inference, mode):
    """拒绝无效目标且不创建记录或改投其他机器。"""
    service, caps = setup_inference
    worker_id, task = "chosen", "classification"
    if mode == "missing":
        worker_id = "missing"
    elif mode == "offline":
        service.lease._workers["chosen"]["last_seen"] = 0
    elif mode == "disabled":
        service.lease.set_worker_enabled("chosen", False)
    else:
        caps["device_backend"] = "unsupported" if mode == "unsupported" else "cuda"
        caps["gpu"] = {"available": mode != "gpu_missing", "healthy": mode != "gpu_unhealthy"}
        service.lease.register_worker("chosen", caps, 60)
        task = "clustering" if mode == "kmeans_gpu" else task
    with pytest.raises(ValueError):
        service.submit("job-a", task, worker_id, {"row": {}})
    assert not service.store.list_keys("inferences/")


def test_busy_worker_queue_isolation_and_private_payload(setup_inference):
    """繁忙槽可排队，按 Worker 隔离且公开状态不泄漏输入。"""
    service, caps = setup_inference
    service.lease.register_worker("chosen", {**caps, "state": "running"}, 60)
    record = service.submit("job-a", "classification", "chosen", {"row": {"secret": 42}})
    assert record["status"] == "queued"
    assert not {"payload", "token", "timeout"}.intersection(record)
    assert not service.queue.snapshot()
    assert not service.queue.inference_queue("other").snapshot()
    assert service.queue.inference_queue("chosen").pop(timeout=0) == record["id"]


@pytest.mark.parametrize("mode", ["expired_queue", "lost_running", "owned_running"])
def test_recovery_timeout_ownership_and_late_result(setup_inference, mode):
    """超时和失租永久失败，持租任务不变，迟到结果不能复活。"""
    service, _ = setup_inference
    record = service.submit("job-a", "classification", "chosen", {"row": {}})
    ident = record["id"]
    if mode == "expired_queue":
        private = service.store.read_json(inference_key(ident))
        private["created_at"] = 0
        service.store.write_json(inference_key(ident), private)
    else:
        service.store.write_json(inference_key(ident, "execution.json"), {"status": "running", "token": "old"})
    if mode == "owned_running":
        service.lease.acquire(ident, "old", 60)
    service.recover()
    if mode == "owned_running":
        assert service.get(ident)["status"] == "running"
    else:
        assert service.get(ident)["status"] == "failed"
        service.store.write_json(inference_key(ident, "result-old.json"), {"status": "completed", "result": 42})
        assert service.get(ident)["status"] == "failed"
        assert "result" not in service.get(ident)
        assert not service.queue.inference_queue("chosen").snapshot()


def test_execution_routes_and_shares_training_slot(setup_inference, monkeypatch):
    """错误节点不执行，繁忙训练槽排队，完成后展示真实节点并释放租约。"""
    service, caps = setup_inference
    record = service.submit("job-a", "classification", "chosen", {"row": {}})
    ident = record["id"]
    queue = service.queue.inference_queue("chosen")
    assert queue.pop(timeout=0) == ident
    service.execute(make_worker(service, {**caps, "worker_id": "other"}), ident)
    assert queue.pop(timeout=0) == ident
    assert service.lease.acquire(slot_key("chosen"), "training", 60)
    worker = make_worker(service, caps)
    service.execute(worker, ident)
    assert service.get(ident)["status"] == "queued"
    assert queue.pop(timeout=0) == ident
    service.lease.release(slot_key("chosen"), "training")

    def prediction(*args):
        """仅隔离模型进程，验证真实调度与训练槽互斥。"""
        assert service.get(ident)["status"] == "running"
        assert not service.lease.acquire(slot_key("chosen"), "competitor", 60)
        return {"result": {"prediction": 7}, "device": "cpu", "device_backend": "cpu"}

    monkeypatch.setattr("app.inference.run_prediction_process", prediction)
    service.execute(worker, ident)
    result = service.get(ident)
    assert result["status"] == "completed" and result["result"] == {"prediction": 7}
    assert [result[k] for k in ("machine_id", "hostname", "worker_id", "slot", "device")] == [
        "machine-a", "host-a", "chosen", "cpu0", "cpu"]
    assert service.lease.peek_holder(ident) is None
    assert service.lease.peek_holder(slot_key("chosen")) is None
    assert worker._controller is None


def test_prediction_training_fairness(setup_inference, monkeypatch):
    """两个队列均有任务时交替领取，避免预测或训练永久饥饿。"""
    service, caps = setup_inference
    worker = make_worker(service, caps)
    calls = []
    monkeypatch.setattr(worker, "_execute_job", lambda ident: calls.append(ident))
    monkeypatch.setattr(worker.inference, "execute", lambda owner, ident: calls.append(ident))
    for number in range(2):
        service.queue.push(f"train-{number}")
        service.queue.inference_queue("chosen").push(f"predict-{number}")
    for _ in range(4):
        worker._run_forever_iteration(60)
    assert calls == ["predict-0", "train-0", "predict-1", "train-1"]


def test_lost_lease_cannot_publish_result(setup_inference, monkeypatch):
    """计算结束后失租也不能发布结果，巡检将其永久标记失败。"""
    service, caps = setup_inference
    ident = service.submit("job-a", "classification", "chosen", {"row": {}})["id"]

    def lose_lease(record, worker_caps, store, controller, shutdown):
        """模拟租约服务撤销执行所有权。"""
        token = service.lease.peek_holder(ident)
        service.lease.release(ident, token)
        return {"result": 42}

    monkeypatch.setattr("app.inference.run_prediction_process", lose_lease)
    service.execute(make_worker(service, caps), ident)
    assert "result" not in service.get(ident)
    assert not any("result-" in key for key in service.store.list_keys("inferences/"))
    service.recover()
    assert service.get(ident)["status"] == "failed"
    assert service.lease.peek_holder(slot_key("chosen")) is None


@pytest.mark.parametrize("reason", ["timeout", "shutdown", "pause", "lease"])
def test_prediction_subprocess_is_reaped(setup_inference, monkeypatch, tmp_path, reason):
    """超时、停机、暂停和失租均终止真实子进程并清除含输入的临时文件。"""
    from app.inference import run_prediction_process

    service, caps = setup_inference
    original_popen = subprocess.Popen
    children = []

    def slow_process(*args, **kwargs):
        """用实际长耗时子进程替代外部模型下载。"""
        process = original_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(process)
        return process

    def ensure_owned():
        """仅在失租分支模拟外部租约失效。"""
        if reason == "lease":
            raise JobLeaseLost("lost lease")

    monkeypatch.setattr("app.inference.subprocess.Popen", slow_process)
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(config, "WORKER_SCRATCH_DIR", scratch)
    shutdown, pause = threading.Event(), threading.Event()
    if reason == "shutdown":
        shutdown.set()
    if reason == "pause":
        pause.set()
    controller = SimpleNamespace(ensure_owned=ensure_owned, pause_event=pause)
    with pytest.raises((RuntimeError, TimeoutError, JobLeaseLost)):
        run_prediction_process({"timeout": 0.05}, caps, service.store, controller, shutdown)
    assert len(children) == 1 and children[0].poll() is not None
    assert list(scratch.iterdir()) == []


def test_prediction_api_compatibility_privacy_and_ownership(setup_inference, monkeypatch):
    """旧接口保持同步结果，新接口返回任务且拒绝跨模型读取和非法输入。"""
    service, _ = setup_inference
    client = api_client(service, monkeypatch)
    service.store.write_bytes(job_key("job-a", "best.pt"), b"mock-model-boundary")
    monkeypatch.setattr("app.routers.jobs.predict_row", lambda *a, **k: {"prediction": 8})
    legacy = client.post("/api/jobs/job-a/predict", json={"row": {}})
    assert legacy.status_code == 200 and legacy.json() == {"prediction": 8}
    accepted = client.post("/api/jobs/job-a/predict", json={"row": {"secret": 42}, "worker_id": "chosen"})
    assert accepted.status_code == 202
    ident = accepted.json()["id"]
    public = client.get(f"/api/jobs/job-a/inferences/{ident}")
    assert public.status_code == 200 and public.json()["status"] == "queued"
    assert not {"payload", "token", "timeout"}.intersection(public.json())
    assert "secret" not in public.text
    assert client.get(f"/api/jobs/job-b/inferences/{ident}").status_code == 404
    assert client.get("/api/jobs/job-a/inferences/infer-" + "0" * 32).status_code == 404
    assert client.get("/api/jobs/job-a/inferences/invalid").status_code == 400
    for worker_id, status in [("", 422), ("x" * 161, 422), ("unknown", 409)]:
        response = client.post("/api/jobs/job-a/predict", json={"row": {}, "worker_id": worker_id})
        assert response.status_code == status
    assert client.post("/api/jobs/job-b/predict", json={"row": {}, "worker_id": "chosen"}).status_code == 400


def test_device_failure_never_falls_back(setup_inference, monkeypatch, tmp_path):
    """执行阶段设备不可用时直接失败，不加载模型或自动切到 CPU。"""
    from app.inference import predict_request

    monkeypatch.setattr("app.device.device_info", lambda *a: {"healthy": False})
    with pytest.raises(RuntimeError, match="未切换计算资源"):
        predict_request({"caps": {"device_backend": "cuda", "device": "cuda:0"}}, tmp_path)


@pytest.mark.parametrize("task,kind", [
    ("classification", "classification"), ("regression", "regression"),
    ("clustering", "clustering"), ("time_series_forecasting", "time_series"),
])
def test_real_model_api_worker_predictions(setup_inference, make_dataset, monkeypatch, tmp_path, task, kind):
    """真实训练产物通过 API 排队和 Worker 子进程预测，重复结果与原预处理保持一致。"""
    import json
    from app.trainer import predict_row
    from tests.test_trainer import _run

    service, caps = setup_inference
    ds = make_dataset(kind=kind, n=100)
    training_dir = tmp_path / "training"
    training_dir.mkdir()
    extra = {"time_column": "timestamp", "lookback": 8, "forecast_horizon": 2} if task == "time_series_forecasting" else {}
    summary = _run(service.store, ds, task, 2, training_dir,
                   target="" if task == "clustering" else "target", n_clusters=3, **extra)
    assert (training_dir / "best.pt").stat().st_size > 0
    assert (training_dir / "metrics.jsonl").read_text().strip()
    if task == "classification":
        assert 0 <= summary["final"]["val_accuracy"] <= 1
    elif task == "regression":
        assert "val_r2" in summary["final"] and summary["final"]["val_rmse"] >= 0
    service.store.write_file(job_key("job-a", "best.pt"), training_dir / "best.pt")
    client = api_client(service, monkeypatch, task)
    payload = {"history": list(range(8))} if task == "time_series_forecasting" else {"row": {"f1": "invalid"}}
    expected = json.loads(json.dumps(predict_row(training_dir / "best.pt", **payload)))
    for _ in range(2):
        accepted = client.post("/api/jobs/job-a/predict", json={**payload, "worker_id": "chosen"})
        assert accepted.status_code == 202, accepted.text
        ident = accepted.json()["id"]
        assert service.queue.inference_queue("chosen").pop(timeout=0) == ident
        service.execute(make_worker(service, caps), ident)
        response = client.get(f"/api/jobs/job-a/inferences/{ident}")
        assert response.status_code == 200
        record = response.json()
        assert record["status"] == "completed", record
        assert record["result"] == expected
        assert record["device"] == "cpu" and record["device_backend"] == "cpu"
        assert record["worker_id"] == "chosen" and record["machine_id"] == "machine-a"
        assert not {"payload", "token", "timeout"}.intersection(record)


def test_real_llm_generation_on_selected_worker(setup_inference, monkeypatch, tmp_path):
    """微型语言模型真实训练后以确定性解码重复生成，兼容旧同步接口。"""
    from app.llm_trainer import generate_text
    from tests.test_llm_trainer import _params, _write_rows, _run

    service, caps = setup_inference
    path = _write_rows(tmp_path / "corpus.csv", [
        {"text": f"machine learning corpus sentence number {i} " * 2} for i in range(50)])
    training_dir = tmp_path / "llm"
    training_dir.mkdir()
    summary = _run("llm_pretraining", path, {**_params(tmp_path), "base_model": None, "vocab_size": 48}, training_dir)
    assert summary and (training_dir / "best_model.zip").stat().st_size > 0
    service.store.write_file(job_key("job-a", "best_model.zip"), training_dir / "best_model.zip")
    client = api_client(service, monkeypatch, "llm_pretraining")
    payload = {"prompt": "machine learning", "max_new_tokens": 2, "temperature": 0.0, "top_p": 0.9}
    expected = {"prompt": payload["prompt"], "generated_text": generate_text(training_dir / "best_model.zip", device="cpu", **payload)}
    legacy = client.post("/api/jobs/job-a/generate", json=payload)
    assert legacy.status_code == 200 and legacy.json() == expected
    for _ in range(2):
        accepted = client.post("/api/jobs/job-a/generate", json={**payload, "worker_id": "chosen"})
        assert accepted.status_code == 202, accepted.text
        ident = accepted.json()["id"]
        assert service.queue.inference_queue("chosen").pop(timeout=0) == ident
        service.execute(make_worker(service, caps), ident)
        record = client.get(f"/api/jobs/job-a/inferences/{ident}").json()
        assert record["status"] == "completed", record
        assert record["result"] == expected and record["device"] == "cpu"
