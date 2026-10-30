"""性能首批验收：资源口径、线程预算、采样隔离和真实训练吞吐。"""
import json
import os
import subprocess
import time

import pytest

from app import performance


@pytest.mark.parametrize("cores,slots,override,expected", [(16,4,None,4), (2,8,None,1), (0.5,2,None,1), (16,2,"3",3), (16,2,"32",8)])
def test_thread_budget_bounds(monkeypatch, cores, slots, override, expected):
    """线程总预算按槽分摊，显式较小预算保留，超额配置受可用核心限制。"""
    monkeypatch.setattr(performance, "available_cpu_cores", lambda: cores)
    env = {"KEEP": "yes"}
    if override:
        env["OMP_NUM_THREADS"] = override
    result = performance.thread_environment(env, slots)
    assert result["KEEP"] == "yes"
    assert {result[key] for key in ("WORKER_TORCH_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")} == {str(expected)}
    assert "WORKER_TORCH_THREADS" not in env


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1,2"])
def test_invalid_budget_rejected(value):
    """非法线程预算在启动前报错，不把坏参数传给底层运行库。"""
    with pytest.raises(ValueError, match="必须为正整数"):
        performance.thread_environment({"OMP_NUM_THREADS": value}, 2)


def test_cpu_quota_respected(monkeypatch):
    """配额小于亲和性核心数时，以容器预算分配计算资源。"""
    monkeypatch.setattr(performance.os, "sched_getaffinity", lambda _: set(range(16)))
    monkeypatch.setattr(performance, "_read", lambda path: "150000 100000" if path.endswith("cpu.max") else "")
    assert performance.available_cpu_cores() == 1.5


def test_resource_deltas_and_history(monkeypatch):
    """首次采样不报告假零；CPU 核占用、主机百分比和历史容量正确。"""
    tick = [0]
    def counters(pid):
        """用系统边界计数模拟两核繁忙；差分和输出仍由真实采样逻辑计算。"""
        i = tick[0]
        return {"cpu_total": 1000 + i * 100, "cpu_idle": 500 + i * 25,
                "process_seconds": i * 10, "threads": 4, "rss_mb": 128,
                "host_total_mb": 8192, "host_available_mb": 4096,
                "container_limit_mb": None, "container_used_mb": None}
    monkeypatch.setattr(performance, "_system_counters", counters)
    sampler = performance.ResourceSampler()
    sampler._clock = lambda: tick[0] * 5
    assert sampler.sample()["host_cpu_percent"] is None
    tick[0] = 1
    row = sampler.sample()
    assert row["host_cpu_percent"] == 75
    assert row["process_cpu_cores"] == 2
    assert row["rss_mb"] == 128 and row["gpu"]["utilization_percent"] is None
    sampler.set_pid(os.getpid() + 10)
    tick[0] = 2
    assert sampler.sample()["process_cpu_cores"] is None
    for i in range(3, 125):
        tick[0] = i
        sampler.sample()
    assert len(sampler.snapshot()["history"]) == 120
    json.dumps(sampler.snapshot(), allow_nan=False)


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("nvidia-smi", .8)])
def test_missing_gpu_metrics_remain_unknown(monkeypatch, failure):
    """驱动工具缺失或超时不会影响训练，不能把未知利用率显示成零。"""
    class Properties:
        uuid = "GPU-test"
    monkeypatch.setattr(performance.torch.cuda, "get_device_properties", lambda _: Properties())
    def unavailable(*args, **kwargs):
        """隔离驱动探测失败。"""
        raise failure
    monkeypatch.setattr(performance.subprocess, "run", unavailable)
    assert all(value is None for value in performance._gpu_snapshot("cuda:0", False).values())


def test_gpu_metrics_match_uuid(monkeypatch):
    """逻辑卡号不能导致采到别的物理 GPU，只有 UUID 匹配的数据被采用。"""
    class Properties:
        uuid = "GPU-selected"
    monkeypatch.setattr(performance.torch.cuda, "get_device_properties", lambda _: Properties())
    monkeypatch.setattr(performance.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "GPU-other, 99, 999, 1000\nGPU-selected, 42, 1024, 8192\n"))
    row = performance._gpu_snapshot("cuda:0", False)
    assert row["utilization_percent"] == 42 and row["used_mb"] == 1024
    assert row["allocated_mb"] is None


def test_checkpoint_timing_and_errors(tmp_path):
    """阶段计时包含实际保存开销，失败原样传播，成功记录可序列化。"""
    meter = performance.EpochPerformance(1)
    with meter.measure("train_seconds"):
        time.sleep(.002)
    with meter.measure("checkpoint_save_seconds"):
        (tmp_path / "checkpoint").write_bytes(b"model")
    with pytest.raises(RuntimeError, match="upload"):
        with meter.measure("checkpoint_publish_seconds"):
            raise RuntimeError("upload")
    row = meter.write(tmp_path, samples=10, steps=2, tokens=50)
    assert row["samples_per_second"] > 0 and row["tokens_per_second"] == pytest.approx(row["samples_per_second"] * 5)
    assert row["checkpoint_save_seconds"] > 0
    assert row["checkpoint_publish_seconds"] > 0
    assert json.loads((tmp_path / "performance.jsonl").read_text()) == row


def test_empty_measurements_do_not_invent_rates(tmp_path):
    """没有完整训练批次时不返回无穷大吞吐或虚假的 token 吞吐。"""
    row = performance.EpochPerformance(1).write(tmp_path, 0, 0)
    assert row["samples_per_second"] is None and row["tokens_per_second"] is None and row["step_ms"] is None


def test_sampler_shutdown_and_failure_isolation(monkeypatch):
    """驱动采样故障不会阻塞读取缓存，停止时采样线程得到回收。"""
    sampler = performance.ResourceSampler()
    def fail():
        """模拟采样后端失败。"""
        raise OSError("driver unavailable")
    monkeypatch.setattr(sampler, "sample", fail)
    sampler.start()
    thread = sampler._thread
    assert sampler.snapshot() == {"latest": None, "history": []}
    sampler.stop()
    assert not thread.is_alive()


@pytest.mark.parametrize("task,kind", [("classification", "classification"), ("regression", "regression"), ("clustering", "clustering"), ("time_series_forecasting", "time_series")])
def test_real_training_records_throughput(store, make_dataset, tmp_path, task, kind):
    """真实训练、验证与产物发布产生独立性能文件，不向精度指标混入时钟值。"""
    import threading
    from app import config, trainer
    dataset = make_dataset(kind=kind, n=90)
    published = []
    def checkpoint(epoch):
        """模拟外部发布耗时，断言训练器真实调用发布边界。"""
        time.sleep(.002)
        published.append(epoch)
    trainer.run_training(
        dataset_path=config.DATASETS_DIR / f"{dataset}.csv", target="" if task == "clustering" else "target", task=task,
        params={"epochs": 2, "batch_size": 16, "lr": .01, "hidden_size": 8, "hidden_layers": 1,
                "test_split": .2, "seed": 42, "n_clusters": 3, "time_column": "timestamp", "lookback": 8, "forecast_horizon": 2},
        cancel_event=threading.Event(), pause_event=threading.Event(), log_fn=lambda _: None,
        job_dir=tmp_path, on_checkpoint=checkpoint)
    rows = [json.loads(line) for line in (tmp_path / "performance.jsonl").read_text().splitlines()]
    metrics = [json.loads(line) for line in (tmp_path / "metrics.jsonl").read_text().splitlines()]
    assert [row["epoch"] for row in rows] == [row["epoch"] for row in metrics]
    assert all(row["samples_per_second"] > 0 and row["samples"] > 0 and row["steps"] > 0 for row in rows)
    assert all(row["tokens_per_second"] is None for row in rows)
    assert rows[-1]["checkpoint_publish_seconds"] >= .002 and published[-1] == rows[-1]["epoch"]
    assert rows[-1]["checkpoint_save_seconds"] > 0
    assert "train_seconds" not in metrics[-1]
    assert (tmp_path / "best.pt").is_file()


def test_real_process_sampler():
    """Linux 实际进程 RSS 与线程信息可读，第二个样本给出有限 CPU 差分。"""
    sampler = performance.ResourceSampler()
    sampler.sample()
    sum(index * index for index in range(10000))
    row = sampler.sample()
    assert row["process_cpu_cores"] >= 0
    if os.path.exists("/proc/self/status"):
        assert row["rss_mb"] > 0 and row["threads"] > 0
        assert row["host_total_mb"] >= row["host_available_mb"] > 0
