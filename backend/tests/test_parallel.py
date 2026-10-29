"""异构联合训练：真实通信进程、加权梯度、断点、调度与资源边界。"""
from __future__ import annotations

import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch
import torch.multiprocessing as mp

from app import config, jobstore
from app.parallel import ParallelContext
from app.parallel_worker import make_group, run_rank_process, select_members, slot_key
from app.routers.schemas import TrainRequest
from app.trainer import JobCancelled, JobPaused, predict_row
from app.worker import GroupMemberController, JobLeaseLost, TrainingWorker, detect_capabilities
from conftest import wait_until


def _port() -> int:
    """为隔离测试选择本机未占用的通信端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _group(sizes=(2, 1), devices=None, backend="gloo") -> dict:
    """生成测试进程共享的固定成员与通信参数。"""
    devices = devices or ["cpu"] * len(sizes)
    return {"members": [{"worker_id": f"test-{index}", "batch_size": size, "device": devices[index]}
                        for index, size in enumerate(sizes)],
            "host": "127.0.0.1", "port": _port(), "backend": backend, "timeout": 15, "seed": 19}


def _gradient_rank(rank: int, group: dict, directory: str, mismatch: bool = False, fault=None) -> None:
    """在真实 rank 中训练线性模型，空分片也必须参与通信并得到一致参数。"""
    torch.set_num_threads(1)
    device = group["members"][rank]["device"]
    context = ParallelContext({**group, "rank": rank, "device": device})
    progress = []
    context.progress = lambda: progress.append(True)
    try:
        model = torch.nn.Linear(2, 1, bias=False, dtype=torch.float64).to(device)
        with torch.no_grad():
            model.weight.fill_(0.2)
        optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
        context.initialize([model], b"wrong" if mismatch and rank else b"same-data")
        if fault == "exit" and rank == 1:
            return
        cancelled = threading.Event()
        if fault == "cancel" and rank == 1:
            cancelled.set()
        context.control(cancelled, threading.Event())
        x = torch.arange(14, dtype=torch.float64).reshape(7, 2).to(device) / 10
        y = x.sum(dim=1, keepdim=True)
        snapshots = []
        for indices in context.indices(len(x), 1):
            optimizer.zero_grad(set_to_none=True)
            if indices:
                torch.nn.functional.mse_loss(model(x[indices]), y[indices]).backward()
            if fault == "nan" and rank == 1:
                model.weight.grad = torch.full_like(model.weight, float("nan"))
            context.step(optimizer, len(indices))
            snapshots.append(model.weight.detach().cpu().clone())
        torch.save(snapshots, Path(directory) / f"rank-{rank}.pt")
        (Path(directory) / f"progress-{rank}.txt").write_text(str(len(progress)))
        context.close(successful=True)
    finally:
        context.close()


@pytest.mark.parametrize("sizes", [(2, 1), (1, 5), (3, 3, 3)])
def test_weighted_gradients_equal_global_batch_with_empty_tail(tmp_path, sizes):
    """正常/边界：不同本地批大小、尾批空节点均等价于合并数据的一次优化器更新。"""
    group = _group(sizes)
    mp.spawn(_gradient_rank, args=(group, str(tmp_path)), nprocs=len(sizes), join=True)
    model = torch.nn.Linear(2, 1, bias=False, dtype=torch.float64)
    with torch.no_grad():
        model.weight.fill_(0.2)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    x = torch.arange(14, dtype=torch.float64).reshape(7, 2) / 10
    y = x.sum(dim=1, keepdim=True)
    order = torch.randperm(7, generator=torch.Generator().manual_seed(20)).tolist()
    expected = []
    for start in range(0, len(x), sum(sizes)):
        indices = order[start:start + sum(sizes)]
        optimizer.zero_grad()
        torch.nn.functional.mse_loss(model(x[indices]), y[indices]).backward()
        optimizer.step()
        expected.append(model.weight.detach().clone())
    for rank in range(len(sizes)):
        actual = torch.load(tmp_path / f"rank-{rank}.pt", weights_only=True)
        assert len(actual) == len(expected)
        # 所有节点均需报告进展，避免参与节点在长 epoch 内被误判为超时。
        assert int((tmp_path / f"progress-{rank}.txt").read_text()) == len(expected) + 1
        for value, reference in zip(actual, expected):
            torch.testing.assert_close(value, reference, rtol=1e-12, atol=1e-12)


def test_different_data_is_rejected_by_all_ranks(tmp_path):
    """异常：节点数据不一致时失败，不得静默生成一个错误模型。"""
    with pytest.raises(mp.ProcessRaisedException, match="不一致"):
        mp.spawn(_gradient_rank, args=(_group(), str(tmp_path), True), nprocs=2, join=True)
    assert not list(tmp_path.glob("rank-*.pt"))


@pytest.mark.parametrize("fault,match", [("exit", "[Cc]onnection|closed"), ("nan", "非有限梯度"), ("cancel", "JobCancelled")])
def test_rank_failure_stops_the_group_without_publishing_model(tmp_path, fault, match):
    """异常：节点退出或出现 NaN 梯度时整组终止，不发布半更新的模型。"""
    with pytest.raises(mp.ProcessRaisedException, match=match):
        mp.spawn(_gradient_rank, args=(_group(), str(tmp_path), False, fault), nprocs=2, join=True)
    assert not list(tmp_path.glob("rank-*.pt"))


@pytest.mark.skipif(not torch.cuda.is_available(), reason="需要真实 NVIDIA GPU 验证 CPU/GPU 混合通信")
def test_real_cuda_cpu_mixed_training(tmp_path):
    """硬件集成：GPU 与 CPU 同时计算，最终每一步参数完全相同。"""
    group = _group(devices=["cuda:0", "cpu"])
    mp.spawn(_gradient_rank, args=(group, str(tmp_path)), nprocs=2, join=True)
    left, right = [torch.load(tmp_path / f"rank-{rank}.pt", weights_only=True) for rank in (0, 1)]
    for a, b in zip(left, right):
        torch.testing.assert_close(a, b, rtol=0, atol=0)


@pytest.mark.skipif(torch.cuda.device_count() < 2, reason="需要至少两张真实 NVIDIA GPU 验证 NCCL")
def test_real_nccl_training(tmp_path):
    """硬件集成：两张可见 GPU 使用 NCCL 聚合并得到一致模型。"""
    group = _group(devices=["cuda:0", "cuda:1"], backend="nccl")
    mp.spawn(_gradient_rank, args=(group, str(tmp_path)), nprocs=2, join=True)
    left, right = [torch.load(tmp_path / f"rank-{rank}.pt", weights_only=True) for rank in (0, 1)]
    for a, b in zip(left, right):
        torch.testing.assert_close(a, b, rtol=0, atol=0)


class _Controller:
    """训练器集成测试的本地控制端；资源租约另由 worker 集成测试覆盖。"""

    def __init__(self):
        """初始化两个可跨线程设置的控制信号。"""
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()

    def ensure_owned(self):
        """独立训练器测试不连接协调服务。"""


def _run_group(directory, dataset, task, epochs=2, pause_after=None, resume=None, sizes=(3, 2)):
    """通过生产 rank 进程入口运行小型任务，保留主节点产物供业务断言。"""
    group = _group(sizes)
    params = {"epochs": epochs, "batch_size": 5, "lr": 0.01, "hidden_size": 4,
              "hidden_layers": 0, "test_split": 0.2, "seed": 19, "time_column": "time",
              "lookback": 3, "forecast_horizon": 1, "dataset_id": "test-data",
              "dataset_source": "local", "vocab_size": 64, "model_hidden_size": 16,
              "model_heads": 2, "model_layers": 1, "max_seq_len": 16, "max_samples": 30,
              "text_column": "text"}
    controls = [_Controller() for _ in sizes]

    def on_epoch(record):
        """在完整 epoch 发布时请求暂停，验证正式进程间控制通道。"""
        if record["epoch"] == pause_after:
            controls[0].pause_event.set()

    with ThreadPoolExecutor(max_workers=len(sizes)) as executor:
        futures = [executor.submit(run_rank_process, {
            "group": {**group, "rank": rank, "device": "cpu"},
            "dataset_path": str(dataset), "target": "target", "task": task, "params": params,
            "job_dir": str(directory / f"rank-{rank}"), "resume_path": str(resume) if resume else None,
        }, controls[rank], lambda message: None, on_epoch if rank == 0 else None) for rank in range(len(sizes))]
        results, errors = [], []
        for future in futures:
            try:
                results.append(future.result(timeout=60))
            except JobPaused as exc:
                errors.append(exc)
        return results, errors


@pytest.mark.parametrize("task", ["classification", "regression", "time_series_forecasting", "llm_pretraining"])
def test_training_artifacts_metrics_and_predictions(tmp_path, task):
    """训练器集成：真实双进程训练产生业务指标、模型和稳定的预测结果。"""
    dataset = tmp_path / "dataset.csv"
    pd.DataFrame({"x": np.arange(21) / 21, "target": np.arange(21) % 2,
                  "time": pd.date_range("2024-01-01", periods=21).astype(str),
                  "text": [f"training sample number {index} for model" for index in range(21)]}).to_csv(dataset, index=False)
    results, errors = _run_group(tmp_path / "run", dataset, task, epochs=1)
    assert not errors
    summary = results[0]
    assert summary["parallel"]["workers"] == 2
    assert summary["parallel"]["global_batch_size"] == 5
    root = tmp_path / "run" / "rank-0"
    metrics = [json.loads(line) for line in (root / "metrics.jsonl").read_text().splitlines()]
    assert [record["epoch"] for record in metrics] == [1]
    assert np.isfinite(metrics[0]["train_loss"])
    assert (root / "checkpoints" / "ckpt-e0001.pt").exists()
    if task == "llm_pretraining":
        assert (root / "best_model.zip").stat().st_size > 0
        assert metrics[0]["perplexity"] > 0
    else:
        model = root / "best.pt"
        assert model.exists()
        arguments = {"history": [0, 1, 0]} if task == "time_series_forecasting" else {"row": {"x": 0.3}}
        assert predict_row(model, **arguments) == predict_row(model, **arguments)
        if task != "classification":
            assert np.isfinite(summary["final"]["val_rmse"])
    assert not list((tmp_path / "run").rglob("parallel-*"))


@pytest.mark.parametrize("task", ["classification", "llm_pretraining"])
def test_pause_resume_matches_uninterrupted_and_repeatable(tmp_path, task):
    """恢复/可复现：固定成员与 seed 的中断接续与完整训练一致。"""
    dataset = tmp_path / "dataset.csv"
    frame = pd.DataFrame({"x": np.arange(27) / 27, "target": np.arange(27) % 2})
    if task.startswith("llm_"):
        frame["text"] = [f"training sample number {index} for model" for index in range(27)]
    frame.to_csv(dataset, index=False)
    _run_group(tmp_path / "full", dataset, task, epochs=3)
    _, paused = _run_group(tmp_path / "resumed", dataset, task, epochs=3, pause_after=1)
    assert len(paused) == 2
    checkpoint = tmp_path / "resumed" / "rank-0" / "checkpoints" / "ckpt-e0001.pt"
    assert checkpoint.exists()
    results, errors = _run_group(tmp_path / "resumed", dataset, task, epochs=3, resume=checkpoint)
    assert not errors and results[0]["resumed_from"] == 1
    expected = torch.load(tmp_path / "full" / "rank-0" / "checkpoints" / "ckpt-e0003.pt", weights_only=True)
    actual = torch.load(tmp_path / "resumed" / "rank-0" / "checkpoints" / "ckpt-e0003.pt", weights_only=True)
    for name, value in expected["state_dict"].items():
        torch.testing.assert_close(value, actual["state_dict"][name], rtol=0, atol=0)
    assert (tmp_path / "full" / "rank-0" / "metrics.jsonl").read_text() == (tmp_path / "resumed" / "rank-0" / "metrics.jsonl").read_text()


@pytest.mark.parametrize("members", [[{"worker_id": "a", "batch_size": 1}],
    [{"worker_id": "a", "batch_size": 1}, {"worker_id": "a", "batch_size": 2}],
    [{"worker_id": "../a", "batch_size": 1}, {"worker_id": "b", "batch_size": 2}],
    [{"worker_id": "a", "batch_size": 0}, {"worker_id": "b", "batch_size": 2}]])
def test_invalid_parallel_requests_are_rejected(members):
    """API 边界与安全：单成员、重复成员、路径输入及非法批大小均被拒绝。"""
    with pytest.raises(ValueError):
        TrainRequest(dataset_id="ds", target="target", parallel_workers=members)


def _caps(worker_id, port):
    """提供真实 CPU 槽和测试专用回环互联地址。"""
    caps = detect_capabilities(worker_id, machine_id=worker_id, slot="cpu0")
    caps["parallel"].update({"host": "127.0.0.1", "port": port})
    return caps


@pytest.mark.parametrize("layout", ["flat", "structured"])
@pytest.mark.parametrize("task", ["classification", "regression"])
def test_workers_complete_one_joint_job(store, q, ls, make_dataset, monkeypatch, layout, task):
    """API 端到端：双格式数据提交、轮询、日志、指标和预测均使用唯一联合模型。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.jobs import JobManager
    from app.routers import jobs as routes

    manager = JobManager(store=store, queue=q, lease=ls, start_workers=False, start_watchdog=False)
    monkeypatch.setattr(routes, "manager", manager)
    api = FastAPI()
    api.include_router(routes.router)
    client = TestClient(api)
    workers = [TrainingWorker(store, q, ls, capabilities=_caps(f"joint-{i}", _port()), idle_poll=0.1) for i in range(2)]
    threads = [threading.Thread(target=worker.run_forever, daemon=True) for worker in workers]
    for thread in threads:
        thread.start()
    try:
        assert wait_until(lambda: len(ls.list_workers()) == 2, 5)
        dataset = make_dataset(kind=task, n=25)
        if layout == "structured":
            (config.DATASETS_DIR / "metadata").mkdir(exist_ok=True)
            (config.DATASETS_DIR / "processed").mkdir(exist_ok=True)
            (config.DATASETS_DIR / f"{dataset}.csv").rename(config.DATASETS_DIR / "processed" / f"{dataset}.csv")
            (config.DATASETS_DIR / f"{dataset}.meta.json").rename(config.DATASETS_DIR / "metadata" / f"{dataset}.json")
        response = client.post("/api/train", json={"dataset_id": dataset, "target": "target", "task": task,
            "epochs": 2, "hidden_layers": 0, "parallel_timeout": 15, "parallel_workers": [
                {"worker_id": worker.worker_id, "batch_size": size} for worker, size in zip(workers, [3, 2])]})
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/jobs/{job_id}").json()["status"] in {"completed", "failed"}, 45)
        result = client.get(f"/api/jobs/{job_id}").json()
        assert result["status"] == "completed", result
        assert len(result["segments"]) == 1
        assert len(result["segments"][0]["members"]) == 2
        assert result["summary"]["parallel"]["workers"] == 2
        assert store.exists(f"jobs/{job_id}/best.pt")
        assert result["progress"]["percent"] == 100
        assert [metric["epoch"] for metric in result["metrics"]] == [1, 2]
        final = result["summary"]["final"]
        if task == "classification":
            assert 0 <= final["val_accuracy"] <= 1
        else:
            assert np.isfinite(final["val_r2"]) and np.isfinite(final["val_rmse"])
        logs = client.get(f"/api/jobs/{job_id}/logs").json()
        assert any("联合训练启动" in line for line in logs["lines"])
        for row in [{"f1": 0.3, "f2": 0.1, "f3": -0.1}, {}, {"f1": "invalid"}]:
            first = client.post(f"/api/jobs/{job_id}/predict", json={"row": row})
            assert first.status_code == 200, first.text
            assert first.json() == client.post(f"/api/jobs/{job_id}/predict", json={"row": row}).json()
            if not row or row.get("f1") == "invalid":
                checkpoint = store.read_file(f"jobs/{job_id}/best.pt", config.WORKER_SCRATCH_DIR / "verify.pt")
                medians = torch.load(checkpoint, weights_only=True)["preprocessor"]["num_medians"]
                assert first.json() == client.post(f"/api/jobs/{job_id}/predict", json={"row": medians}).json()
        assert wait_until(lambda: all(ls.peek_holder(slot_key(worker.worker_id)) is None for worker in workers), 5)
    finally:
        client.close()
        manager.stop()
        for worker in workers:
            worker.request_shutdown()
        for thread in threads:
            thread.join(timeout=20)
        assert all(not thread.is_alive() for thread in threads)


def test_member_validation_and_backend_selection(ls):
    """能力兼容：GPU 型号可以不同；版本、禁用节点和未配置地址不可静默兼容。"""
    requests = [{"worker_id": f"node-{i}", "batch_size": i + 1} for i in range(2)]
    for index, name in enumerate(["RTX 3090", "RTX 4090"]):
        caps = _caps(f"node-{index}", _port())
        caps["gpu"].update({"available": True, "name": name})
        caps["parallel"]["nccl"] = True
        ls.register_worker(caps["worker_id"], caps, 30)
    members = select_members(ls, requests)
    assert make_group("job-a", "token", members, 30, 42)["backend"] == "nccl"
    members[1]["device"] = "cpu"
    assert make_group("job-a", "token", members, 30, 42)["backend"] == "gloo"
    ls.set_worker_enabled("node-1", False)
    with pytest.raises(RuntimeError, match="启用"):
        select_members(ls, requests)
    ls.set_worker_enabled("node-1", True)
    ls.heartbeat_worker("node-1", {"torch_version": "0.0.1"}, 30)
    with pytest.raises(ValueError, match="版本"):
        select_members(ls, requests)


def _llm_rank(rank, group, task, dataset, params, directory):
    """使用本地微型基座隔离 Hub 下载，真实执行多进程 SFT/DPO/PPO 训练。"""
    from app import llm_trainer
    from app.trainer import run_training
    torch.set_num_threads(1)
    original = llm_trainer._safe_hub_id
    llm_trainer._safe_hub_id = lambda value, label: value if Path(value).is_dir() else original(value, label)
    context = ParallelContext({**group, "rank": rank, "device": "cpu"})
    try:
        summary = run_training(Path(dataset), "", task, params, threading.Event(), lambda message: None,
                               Path(directory) / f"rank-{rank}", parallel=context)
        if rank == 0:
            assert np.isfinite(summary["final"]["train_loss"])
        context.close(successful=True)
    finally:
        context.close()


@pytest.mark.parametrize("task", ["llm_sft", "llm_dpo", "llm_ppo"])
def test_parallel_llm_finetuning_and_empty_tail(tmp_path, task):
    """训练器集成：各微调分支真实同步梯度，PPO 同步 value head 和全局优势统计。"""
    from test_llm_trainer import _params, _tiny_base_model
    from transformers import AutoTokenizer, GPT2Config, GPT2ForSequenceClassification

    model = _tiny_base_model(tmp_path / "base")
    params = _params(model)
    reward = tmp_path / "reward"
    if task == "llm_ppo":
        tokenizer = AutoTokenizer.from_pretrained(model)
        tokenizer.save_pretrained(reward)
        reward_config = GPT2Config.from_pretrained(model, num_labels=1, pad_token_id=tokenizer.pad_token_id)
        GPT2ForSequenceClassification(reward_config).save_pretrained(reward)
        params["reward_model"] = str(reward)
    dataset = tmp_path / "llm.csv"
    pd.DataFrame([{"text": f"training answer number {index}", "prompt": f"question {index}",
                   "chosen": f"accepted answer {index}", "rejected": f"bad answer {index}"} for index in range(7)]).to_csv(dataset, index=False)
    group = _group(sizes=(2, 3))
    mp.spawn(_llm_rank, args=(group, task, str(dataset), params, str(tmp_path / "run")), nprocs=2, join=True)
    root = tmp_path / "run" / "rank-0"
    assert (root / "best_model.zip").exists()
    assert not (tmp_path / "run" / "rank-1" / "best_model.zip").exists()
    summary = json.loads((root / "summary.json").read_text())
    if task == "llm_dpo":
        assert 0 <= summary["final"]["preference_accuracy"] <= 1
    elif task == "llm_ppo":
        assert np.isfinite(summary["final"]["reward"])
        checkpoint = torch.load(root / "checkpoints" / "ckpt-e0001.pt", weights_only=True)
        assert checkpoint["value_head_state"]
    else:
        assert np.isfinite(summary["final"]["perplexity"])


def test_group_reservation_rolls_back_if_member_is_busy(store, q, ls, make_dataset, make_job):
    """调度冲突：无法凑齐整组时释放先前预留，不占用部分节点等待。"""
    selected = [{"worker_id": "a", "batch_size": 1}, {"worker_id": "z", "batch_size": 1}]
    for item in selected:
        ls.register_worker(item["worker_id"], _caps(item["worker_id"], _port()), 30)
    assert ls.acquire(slot_key("z"), "another-job", 30)
    job_id = make_job(make_dataset(n=25))
    record = jobstore.read_job(store, job_id)
    record["spec"]["resource_hint"]["parallel_workers"] = selected
    jobstore.write_job(store, record)
    worker = TrainingWorker(store, q, ls, capabilities=_caps("a", _port()))
    worker._execute_job(job_id)
    assert jobstore.read_job(store, job_id)["status"] == "queued"
    assert ls.peek_holder(slot_key("a")) is None
    assert ls.peek_holder(slot_key("z")) == "another-job"
    assert ls.peek_holder(job_id) is None
    assert job_id in q.snapshot()


def test_member_does_not_renew_or_overwrite_replaced_leader(ls):
    """租约安全：参与节点不能延长主节点生命，也不能跟随过期执行轮次继续训练。"""
    assert ls.acquire("job-a", "leader-old", 30)
    assert ls.acquire(slot_key("follower"), "assignment", 30)
    controller = GroupMemberController(ls, {"job_id": "job-a", "token": "leader-old"}, "follower", "assignment")
    controller.ensure_owned()
    ls.release("job-a", "leader-old")
    assert ls.acquire("job-a", "leader-new", 30)
    with pytest.raises(JobLeaseLost):
        controller.ensure_owned()
    assert ls.peek_holder("job-a") == "leader-new"
    assert controller.lease_lost.is_set()


def test_api_allows_cpu_llm_parallel_but_preserves_single_gpu_default(monkeypatch, ls):
    """API 兼容：显式联合训练可使用 CPU，旧 LLM 请求仍要求 GPU。"""
    from app.routers import jobs as routes
    selected = [{"worker_id": f"api-{i}", "batch_size": 1} for i in range(2)]
    for item in selected:
        ls.register_worker(item["worker_id"], _caps(item["worker_id"], _port()), 30)
    captured = []
    monkeypatch.setattr(routes.manager, "lease", ls)
    monkeypatch.setattr(routes.manager, "submit", lambda spec: captured.append(spec) or {"id": "test-job"})
    routes.start_training(TrainRequest(dataset_id="example/corpus", dataset_source="hub", task="llm_pretraining", parallel_workers=selected))
    assert captured[-1]["resource_hint"]["require_gpu"] is False
    assert captured[-1]["resource_hint"]["parallel_workers"] == selected
    routes.start_training(TrainRequest(dataset_id="example/corpus", dataset_source="hub", task="llm_pretraining"))
    assert captured[-1]["resource_hint"]["require_gpu"] is True


def test_missing_rank_times_out_and_cleans_subprocess_files(tmp_path):
    """超时：节点未加入时有界失败，回收 rank 进程和临时通信文件。"""
    group = _group()
    group["timeout"] = 2
    with pytest.raises(RuntimeError, match="timeout|Timed out|timed out"):
        run_rank_process({"job_dir": str(tmp_path), "group": {**group, "rank": 0, "device": "cpu"}}, _Controller(), lambda message: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("overrides", [
    {"parallel_workers": [{"worker_id": f"w-{i}", "batch_size": 1} for i in range(33)]},
    {"parallel_workers": [{"worker_id": "a", "batch_size": 4097}, {"worker_id": "b", "batch_size": 1}]},
    {"parallel_timeout": 9}, {"parallel_timeout": 3601}, {"task": "clustering"},
    {"task": "llm_pretraining", "parallel_workers": [{"worker_id": "a", "batch_size": 65}, {"worker_id": "b", "batch_size": 1}]},
])
def test_parallel_schema_limits_and_unsupported_tasks(overrides):
    """边界：节点数量、批大小、超时和不适用任务在接单前明确拒绝。"""
    payload = {"dataset_id": "ds", "target": "target", "parallel_workers": [
        {"worker_id": "a", "batch_size": 1}, {"worker_id": "b", "batch_size": 1}], **overrides}
    with pytest.raises(ValueError):
        TrainRequest(**payload)


def test_parallel_schema_accepts_upper_bound():
    """边界：明确支持的最大成员数、批大小和超时不被额外限制。"""
    request = TrainRequest(dataset_id="ds", target="target", parallel_timeout=3600,
        parallel_workers=[{"worker_id": f"w-{i}", "batch_size": 4096} for i in range(32)])
    assert len(request.parallel_workers) == 32 and request.parallel_timeout == 3600


@pytest.mark.parametrize("condition,match", [
    ("offline", "上线"), ("disabled", "启用"), ("address", "地址"),
    ("gloo", "Gloo"), ("vendor", "NVIDIA"), ("architecture", "架构"),
])
def test_incompatible_members_are_rejected(ls, condition, match):
    """资源安全：不能绕过停用开关，也不能把不支持的设备静默当成可训练 GPU。"""
    caps = _caps("node", _port())
    if condition == "address":
        caps["parallel"]["host"] = ""
    if condition == "gloo":
        caps["parallel"]["gloo"] = False
    if condition in {"vendor", "architecture"}:
        caps["gpu"]["available"] = True
        caps["parallel"].update({"vendor": "amd" if condition == "vendor" else "nvidia",
                                 "capability_supported": condition != "architecture"})
    if condition != "offline":
        ls.register_worker("node", caps, 30)
    if condition == "disabled":
        ls.set_worker_enabled("node", False)
    with pytest.raises((ValueError, RuntimeError), match=match):
        select_members(ls, [{"worker_id": "node", "batch_size": 1}])


@pytest.mark.parametrize("restriction", [{"require_gpu": True}, {"min_vram_mb": 1}])
def test_api_rejects_cpu_members_with_gpu_restrictions(monkeypatch, ls, restriction):
    """API 异常：显式 GPU 限制与 CPU 成员冲突时不创建任务。"""
    from fastapi import HTTPException
    from app.routers import jobs as routes
    selected = [{"worker_id": f"api-{i}", "batch_size": 1} for i in range(2)]
    for item in selected:
        ls.register_worker(item["worker_id"], _caps(item["worker_id"], _port()), 30)
    monkeypatch.setattr(routes.manager, "lease", ls)
    with pytest.raises(HTTPException) as error:
        routes.start_training(TrainRequest(dataset_id="example/corpus", dataset_source="hub",
            task="llm_pretraining", parallel_workers=selected, **restriction))
    assert error.value.status_code == 400


def test_group_retry_cannot_be_assigned_to_single_worker(store, q, ls, make_dataset, make_job):
    """兼容与状态冲突：原组失败后只能整组重试，拒绝单槽覆盖且保持原记录。"""
    from app.jobs import JobManager
    job_id = make_job(make_dataset())
    record = jobstore.read_job(store, job_id)
    record["spec"]["resource_hint"]["parallel_workers"] = [
        {"worker_id": "a", "batch_size": 1}, {"worker_id": "b", "batch_size": 1}]
    record["status"] = "failed"
    jobstore.write_job(store, record)
    manager = JobManager(store=store, queue=q, lease=ls, start_workers=False, start_watchdog=False)
    try:
        with pytest.raises(ValueError, match="整组重试"):
            manager.retry(job_id, worker_id="a")
        assert manager.get(job_id)["status"] == "failed"
        assert manager.retry(job_id)["status"] == "queued"
        assert job_id in q.snapshot()
    finally:
        manager.stop()


def test_cpu_slot_hides_inherited_gpu_visibility(monkeypatch):
    """设备兼容：有 GPU 的机器启动 CPU 槽时也必须实际使用 CPU。"""
    from types import SimpleNamespace
    from app.worker import WorkerSupervisor
    launched = []
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    monkeypatch.setattr("app.worker._gpu_count", lambda: 2)
    monkeypatch.setattr("app.worker.subprocess.Popen", lambda args, env: launched.append(env) or SimpleNamespace(pid=1))
    supervisor = WorkerSupervisor(None, None, None, log=lambda message: None)
    supervisor._spawn("cpu0", None)
    supervisor._spawn("gpu1", "1")
    assert [environment["CUDA_VISIBLE_DEVICES"] for environment in launched] == ["", "1"]
