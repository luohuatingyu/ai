"""AC4/AC5：可续训训练器——断点续训等价性、暂停恢复、可复现性。

ML 平台特有场景：
- 训练链路：metrics 逐 epoch 产出、best.pt 产物断言；
- 可复现性：固定 seed 重复训练指标一致；
- 续训等价：中断续训与一口气训练指标轨迹完全一致（同设备）。
"""
from __future__ import annotations

import json
import threading

import pytest

from app import config, trainer
from app.trainer import JobCancelled, JobPaused, load_trainer_state, run_training
from tests.conftest import job_epochs


def _run(store, ds_id: str, task: str, epochs: int, tmp_dir, resume_state=None,
         cancel_event=None, pause_event=None):
    """统一参数的训练调用（与 worker 的调用方式一致）。"""
    return run_training(
        dataset_path=config.DATASETS_DIR / f"{ds_id}.csv",
        target="target",
        task=task,
        params={"epochs": epochs, "batch_size": 32, "lr": 0.01, "hidden_size": 16,
                "hidden_layers": 1, "test_split": 0.2, "seed": 42},
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
