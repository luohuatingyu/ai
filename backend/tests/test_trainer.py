"""AC4/AC5：可续训训练器——断点续训等价性、暂停恢复、可复现性。

ML 平台特有场景：
- 训练链路：metrics 逐 epoch 产出、best.pt 产物断言；
- 可复现性：固定 seed 重复训练指标一致；
- 续训等价：中断续训与一口气训练指标轨迹完全一致（同设备）。
"""
from __future__ import annotations

import json
import threading

import numpy as np
import pandas as pd
import pytest

from app import config, trainer
from app.trainer import JobCancelled, JobPaused, load_trainer_state, run_training
from tests.conftest import job_epochs


def _run(store, ds_id: str, task: str, epochs: int, tmp_dir, resume_state=None,
         cancel_event=None, pause_event=None, target="target", **extra_params):
    """统一参数的训练调用（与 worker 的调用方式一致）。"""
    return run_training(
        dataset_path=config.DATASETS_DIR / f"{ds_id}.csv",
        target=target,
        task=task,
        params={"epochs": epochs, "batch_size": 32, "lr": 0.01, "hidden_size": 16,
                "hidden_layers": 1, "test_split": 0.2, "seed": 42, **extra_params},
        cancel_event=cancel_event or threading.Event(),
        log_fn=lambda msg: None,
        job_dir=tmp_dir,
        pause_event=pause_event,
        resume_state=resume_state,
    )


def test_full_run_produces_metrics_and_artifacts(store, make_dataset, tmp_path):
    """正常场景（分类）：完整训练产出逐 epoch metrics / best.pt / preprocessor / summary。"""
    ds = make_dataset(kind="classification")
    summary = _run(store, ds, "classification", 4, tmp_path)
    assert summary["epochs_total"] == 4
    assert summary["best_epoch"] >= 1
    assert "val_accuracy" in summary["final"]
    assert (tmp_path / "best.pt").exists()
    assert (tmp_path / "preprocessor.json").exists()
    metrics = [json.loads(l) for l in
               (tmp_path / "metrics.jsonl").read_text().splitlines() if l.strip()]
    assert [m["epoch"] for m in metrics] == [1, 2, 3, 4]


def test_regression_run_produces_r2_rmse(store, make_dataset, tmp_path):
    """正常场景（回归）：R²/RMSE 指标存在。"""
    ds = make_dataset(kind="regression")
    summary = _run(store, ds, "regression", 3, tmp_path)
    assert "val_r2" in summary["final"] and "val_rmse" in summary["final"]


@pytest.mark.parametrize("task,kind", [
    ("classification", "classification"), ("regression", "regression"),
    ("clustering", "clustering"), ("time_series_forecasting", "time_series"),
])
def test_training_releases_source_frames_before_epochs(store, make_dataset, tmp_path, monkeypatch, task, kind):
    """真实训练期间不保留已转换的数据表，同时验证指标、产物和稳定预测。"""
    import weakref

    ds = make_dataset(kind=kind, n=100)
    source_frames = []
    original_features = trainer._prepare_features
    original_windows = trainer._prepare_time_series_windows

    def features(frame, target):
        """观察真实预处理的输入与结果，核心转换完全按生产逻辑执行。"""
        result = original_features(frame, target)
        source_frames.extend([weakref.ref(frame), weakref.ref(result[0])])
        return result

    def windows(frame, target, time_column, lookback, horizon):
        """记录时间序列输入表的生命周期，不改变窗口与标签。"""
        source_frames.append(weakref.ref(frame))
        return original_windows(frame, target, time_column, lookback, horizon)

    checked_epochs = []

    def on_epoch(record):
        """在实际 epoch 回调中确认原始表已释放。"""
        assert source_frames and all(reference() is None for reference in source_frames)
        checked_epochs.append(record["epoch"])

    monkeypatch.setattr(trainer, "_prepare_features", features)
    monkeypatch.setattr(trainer, "_prepare_time_series_windows", windows)
    kwargs = {"time_column": "timestamp", "lookback": 8, "forecast_horizon": 2} if task == "time_series_forecasting" else {}
    summary = run_training(
        dataset_path=config.DATASETS_DIR / f"{ds}.csv", target="" if task == "clustering" else "target",
        task=task, params={"epochs": 2, "batch_size": 16, "lr": 0.01, "hidden_size": 8,
                          "hidden_layers": 1, "test_split": 0.2, "seed": 42, "n_clusters": 3, **kwargs},
        cancel_event=threading.Event(), log_fn=lambda _: None, job_dir=tmp_path, on_epoch_end=on_epoch)
    assert checked_epochs
    assert summary["task"] == task and (tmp_path / "best.pt").stat().st_size > 0
    inputs = {"history": list(range(8))} if task == "time_series_forecasting" else {"row": {"f1": "invalid"}}
    assert trainer.predict_row(tmp_path / "best.pt", **inputs) == trainer.predict_row(tmp_path / "best.pt", **inputs)


def test_feature_memory_reduction_preserves_precision_metadata_and_source():
    """较小的特征矩阵保持原中位数精度、编码顺序与 float32 数值，不修改输入。"""
    source = pd.DataFrame({
        "number": [1.123456789, np.nan, np.inf, -np.inf, 9.987654321],
        "category": pd.Series(["b", "a", None, "b", "a"], dtype="object"), "constant": [2.0] * 5,
        "target": [0, 1, 0, 1, 0],
    })
    original = source.copy(deep=True)
    features, metadata = trainer._prepare_features(source, "target")
    pd.testing.assert_frame_equal(source, original)
    median = (1.123456789 + 9.987654321) / 2
    assert metadata["num_medians"]["number"] == median
    assert metadata["cat_modes"]["category"] == "a"
    expected = pd.DataFrame({"number": [1.123456789, median, median, median, 9.987654321], "constant": [2.] * 5})
    categories = pd.get_dummies(pd.DataFrame({"category": ["b", "a", "__缺失__", "b", "a"]}),
                                columns=["category"], prefix=["category"], prefix_sep="=", dtype=float)
    expected = pd.concat([expected, categories], axis=1)
    assert list(features.columns) == list(expected.columns)
    np.testing.assert_array_equal(features.to_numpy(), expected.to_numpy(dtype=np.float32))
    assert all(dtype == np.dtype("float32") for dtype in features.dtypes)
    assert features.memory_usage(index=False).sum() == len(source) * len(features.columns) * 4


def test_kmeans_run_produces_clusters_checkpoint_and_stable_predictions(
    store, make_dataset, tmp_path
):
    """聚类链路产出惯性/轮廓系数、checkpoint，固定 seed 下预测稳定。"""
    ds = make_dataset(kind="clustering", n=90)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = _run(store, ds, "clustering", 20, first_dir, target="", n_clusters=3)
    second = _run(store, ds, "clustering", 20, second_dir, target="", n_clusters=3)

    assert first["task"] == "clustering"
    assert first["n_clusters"] == 3
    assert sum(first["cluster_counts"].values()) == 90
    assert first["final"]["val_inertia"] >= 0
    assert first["final"]["val_silhouette"] > 0.5
    assert (first_dir / "best.pt").exists()
    assert (first_dir / "checkpoints" / f"ckpt-e{first['epochs_total']:04d}.pt").exists()
    assert [json.loads(line)["epoch"] for line in
            (first_dir / "metrics.jsonl").read_text().splitlines()] == list(
                range(1, first["epochs_total"] + 1)
            )
    input_row = {"f1": 0.2, "f2": -0.1, "f3": 0.3}
    prediction_a = trainer.predict_row(first_dir / "best.pt", input_row)
    prediction_b = trainer.predict_row(second_dir / "best.pt", input_row)
    assert prediction_a["prediction"] == prediction_b["prediction"]


def test_kmeans_pause_resume_keeps_iteration_history(store, make_dataset, tmp_path):
    """聚类在迭代边界暂停后可从中心 checkpoint 接续。"""
    ds = make_dataset(kind="clustering", n=90)
    pause = threading.Event()

    def pause_after_one(record):
        if record["epoch"] == 1:
            pause.set()

    with pytest.raises(trainer.JobPaused):
        trainer.run_training(
            dataset_path=config.DATASETS_DIR / f"{ds}.csv",
            target="", task="clustering",
            params={"epochs": 20, "n_clusters": 3, "seed": 42},
            cancel_event=threading.Event(), log_fn=lambda _message: None,
            job_dir=tmp_path, pause_event=pause, on_epoch_end=pause_after_one,
        )
    state = load_trainer_state(tmp_path / "checkpoints" / "ckpt-e0001.pt")
    summary = _run(
        store, ds, "clustering", 20, tmp_path, resume_state=state,
        target="", n_clusters=3,
    )
    epochs = [json.loads(line)["epoch"] for line in
              (tmp_path / "metrics.jsonl").read_text().splitlines()]
    assert epochs == list(range(1, summary["epochs_total"] + 1))
    assert summary["resumed_from"] == 1


def test_kmeans_rejects_more_clusters_than_distinct_rows(store, make_dataset, tmp_path):
    """不同特征样本数量不足时拒绝生成空簇或伪造聚类结果。"""
    ds = make_dataset(kind="clustering", n=12)
    frame = pd.DataFrame({"feature": [0.0] * 6 + [1.0] * 6})
    frame.to_csv(config.DATASETS_DIR / f"{ds}.csv", index=False)
    with pytest.raises(ValueError, match="不同特征样本数"):
        _run(store, ds, "clustering", 5, tmp_path, target="", n_clusters=3)


def test_timeseries_run_uses_ordered_windows_and_predicts_future_value(
    store, make_dataset, tmp_path
):
    """单序列训练按时间列排序，产出回归指标和可用的历史窗口预测。"""
    ds = make_dataset(kind="time_series", n=100)
    summary = _run(
        store, ds, "time_series_forecasting", 4, tmp_path,
        target="target", time_column="timestamp", lookback=8, forecast_horizon=2,
    )
    repeat_dir = tmp_path / "repeat"
    repeat_dir.mkdir()
    repeat = _run(
        store, ds, "time_series_forecasting", 4, repeat_dir,
        target="target", time_column="timestamp", lookback=8, forecast_horizon=2,
    )
    assert summary["task"] == "time_series_forecasting"
    assert summary["lookback"] == 8
    assert summary["forecast_horizon"] == 2
    assert summary["rows"]["train"] > summary["rows"]["val"]
    assert "val_rmse" in summary["final"] and "val_r2" in summary["final"]
    assert abs(summary["final"]["val_rmse"] - repeat["final"]["val_rmse"]) < 1e-6
    assert (tmp_path / "best.pt").exists()

    frame = pd.read_csv(config.DATASETS_DIR / f"{ds}.csv")
    series = frame["target"].tail(8).tolist()
    prediction = trainer.predict_row(tmp_path / "best.pt", history=series)
    repeated_prediction = trainer.predict_row(repeat_dir / "best.pt", history=series)
    assert prediction["task"] == "time_series_forecasting"
    assert np.isfinite(prediction["prediction"])
    assert prediction["prediction"] == repeated_prediction["prediction"]

    from app.trainer import _prepare_time_series_windows

    ordered_windows = _prepare_time_series_windows(frame, "target", "timestamp", 8, 2)
    shuffled_windows = _prepare_time_series_windows(
        frame.sample(frac=1.0, random_state=17), "target", "timestamp", 8, 2
    )
    np.testing.assert_array_equal(ordered_windows[0], shuffled_windows[0])
    np.testing.assert_array_equal(ordered_windows[1], shuffled_windows[1])


def test_timeseries_rejects_invalid_time_values_and_short_history(make_dataset, store):
    """非法时间戳和不足以形成验证窗口的数据集明确失败。"""
    from app.trainer import _prepare_time_series_windows

    ds = make_dataset(kind="time_series", n=30)
    frame = pd.read_csv(config.DATASETS_DIR / f"{ds}.csv")
    frame.loc[0, "timestamp"] = "not-a-date"
    with pytest.raises(ValueError, match="无法解析"):
        _prepare_time_series_windows(frame, "target", "timestamp", 5, 1)

    frame = pd.read_csv(config.DATASETS_DIR / f"{ds}.csv").head(10)
    with pytest.raises(ValueError, match="至少需要"):
        _prepare_time_series_windows(frame, "target", "timestamp", 5, 1)

    frame = pd.read_csv(config.DATASETS_DIR / f"{ds}.csv")
    frame.loc[1, "timestamp"] = frame.loc[0, "timestamp"]
    with pytest.raises(ValueError, match="重复时间点"):
        _prepare_time_series_windows(frame, "target", "timestamp", 5, 1)

    frame = pd.read_csv(config.DATASETS_DIR / f"{ds}.csv")
    frame["target"] = frame["target"].astype(str)
    frame.loc[0, "target"] = "not-a-number"
    with pytest.raises(ValueError, match="必须全部为数值"):
        _prepare_time_series_windows(frame, "target", "timestamp", 5, 1)


def test_resume_equals_full_run(store, make_dataset, tmp_path):
    """AC4：分两段训练（3+3）与一口气 6 epoch 的指标轨迹完全一致（同设备）。"""
    ds = make_dataset()
    # 一口气
    full_dir = tmp_path / "full"
    full_dir.mkdir()
    full = _run(store, ds, "classification", 6, full_dir)

    # 分段：先 3 epoch，再从 checkpoint 续 3
    part_dir = tmp_path / "part"
    part_dir.mkdir()
    _run(store, ds, "classification", 3, part_dir)
    ckpt = part_dir / "checkpoints" / "ckpt-e0003.pt"
    assert ckpt.exists(), "第 3 epoch 后应存在版本化 checkpoint"
    state = load_trainer_state(ckpt)
    assert state["epoch"] == 3
    resumed = _run(store, ds, "classification", 6, part_dir, resume_state=state)

    assert resumed["epochs_total"] == 6
    # 逐 epoch 指标一致（同卡同 seed 可复现；本机 CPU 即同设备）
    m_full = [json.loads(l) for l in (full_dir / "metrics.jsonl").read_text().splitlines() if l.strip()]
    m_part = [json.loads(l) for l in (part_dir / "metrics.jsonl").read_text().splitlines() if l.strip()]
    assert [m["epoch"] for m in m_part] == [1, 2, 3, 4, 5, 6]     # 无重复无锯齿
    for a, b in zip(m_full, m_part):
        assert a["epoch"] == b["epoch"]
        assert abs(a["train_loss"] - b["train_loss"]) < 1e-6
        assert abs(a["val_loss"] - b["val_loss"]) < 1e-6
    # 汇总一致
    assert full["best_epoch"] == resumed["best_epoch"]
    assert abs(full["final"]["val_accuracy"] - resumed["final"]["val_accuracy"]) < 1e-6


def test_pause_at_epoch_boundary_via_callback(store, make_dataset, tmp_path):
    """AC5（确定性版本）：用 on_epoch_end 回调在第 2 个 epoch 后置位暂停事件。"""
    ds = make_dataset()
    import threading

    pause = threading.Event()

    def on_epoch_end(rec):
        if int(rec.get("epoch", 0)) >= 2:
            pause.set()

    with pytest.raises(JobPaused):
        run_training(
            dataset_path=config.DATASETS_DIR / f"{ds}.csv",
            target="target", task="classification",
            params={"epochs": 10, "batch_size": 32, "lr": 0.01, "hidden_size": 16,
                    "hidden_layers": 1, "test_split": 0.2, "seed": 42},
            cancel_event=threading.Event(), log_fn=lambda m: None, job_dir=tmp_path,
            pause_event=pause, on_epoch_end=on_epoch_end,
        )
    state = load_trainer_state(tmp_path / "checkpoints" / "ckpt-e0002.pt")
    assert state["epoch"] == 2
    # 续训至完成，曲线连续
    resumed = _run(store, ds, "classification", 6, tmp_path, resume_state=state)
    assert resumed["epochs_total"] == 6
    epochs = [json.loads(l)["epoch"] for l in
              (tmp_path / "metrics.jsonl").read_text().splitlines() if l.strip()]
    assert epochs == [1, 2, 3, 4, 5, 6]


def test_cancel_does_not_leave_running_state(store, make_dataset, tmp_path):
    """异常场景：取消事件 → JobCancelled；取消是终态，不再有新 epoch。"""
    ds = make_dataset()
    import threading

    cancel = threading.Event()

    def on_epoch_end(rec):
        if int(rec.get("epoch", 0)) >= 1:
            cancel.set()

    with pytest.raises(JobCancelled):
        run_training(
            dataset_path=config.DATASETS_DIR / f"{ds}.csv",
            target="target", task="classification",
            params={"epochs": 5, "batch_size": 32, "lr": 0.01, "hidden_size": 16,
                    "hidden_layers": 1, "test_split": 0.2, "seed": 42},
            cancel_event=cancel, log_fn=lambda m: None, job_dir=tmp_path,
            on_epoch_end=on_epoch_end,
        )
    epochs = [json.loads(l)["epoch"] for l in
              (tmp_path / "metrics.jsonl").read_text().splitlines() if l.strip()]
    assert epochs.count(1) == 1


def test_same_seed_reproducible(store, make_dataset, tmp_path):
    """可复现性：固定 seed 下重复训练，验证指标一致。"""
    ds = make_dataset()
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir(); dir_b.mkdir()
    sa = _run(store, ds, "classification", 4, dir_a)
    sb = _run(store, ds, "classification", 4, dir_b)
    assert abs(sa["final"]["val_accuracy"] - sb["final"]["val_accuracy"]) < 1e-9
    assert abs(sa["final"]["val_loss"] - sb["final"]["val_loss"]) < 1e-9
    assert sa["best_epoch"] == sb["best_epoch"]


def test_resume_rejects_arch_mismatch(store, make_dataset, tmp_path):
    """异常场景：续训超参与存档不一致 → 明确报错（不被静默接受）。"""
    ds = make_dataset()
    _run(store, ds, "classification", 2, tmp_path)
    state = load_trainer_state(tmp_path / "checkpoints" / "ckpt-e0002.pt")
    with pytest.raises(ValueError):
        run_training(
            dataset_path=config.DATASETS_DIR / f"{ds}.csv",
            target="target", task="classification",
            params={"epochs": 4, "batch_size": 32, "lr": 0.01, "hidden_size": 16,
                    "hidden_layers": 3, "test_split": 0.2, "seed": 42},  # 层数不同
            cancel_event=threading.Event(), log_fn=lambda m: None, job_dir=tmp_path,
            resume_state=state,
        )


def test_resume_rejects_corrupt_file(store, tmp_path):
    """异常场景：损坏 checkpoint → ValueError（由 worker 回退更早版本）。"""
    bad = tmp_path / "bad-ckpt.pt"
    bad.write_bytes(b"not-a-torch-file")
    with pytest.raises(ValueError):
        load_trainer_state(bad)
