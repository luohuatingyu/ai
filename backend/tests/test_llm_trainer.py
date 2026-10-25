"""LLM 训练模式、Hub 安全边界及模型产物测试。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

pytest.importorskip("transformers")
pytest.importorskip("datasets")

from app import llm_trainer
from app.routers import jobs as jobs_router
from app.routers.schemas import TrainRequest
from app.trainer import load_trainer_state


def _tiny_base_model(path: Path) -> Path:
    """创建仅用于测试的本地 GPT-2 tokenizer 与小型因果语言模型。"""
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

    path.mkdir(parents=True, exist_ok=True)
    backend = Tokenizer(models.BPE(unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    backend.decoder = decoders.ByteLevel()
    backend.train_from_iterator(
        [f"prompt {i} accepted answer rejected response text" for i in range(40)],
        trainer=trainers.BpeTrainer(
            vocab_size=64,
            min_frequency=1,
            special_tokens=["[PAD]", "[UNK]", "[BOS]", "[EOS]"],
        ),
    )
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=backend,
        bos_token="[BOS]", eos_token="[EOS]", unk_token="[UNK]", pad_token="[PAD]",
    )
    tokenizer.save_pretrained(path)
    config = GPT2Config(
        vocab_size=len(tokenizer), n_positions=32, n_ctx=32,
        n_embd=32, n_layer=1, n_head=4,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
        pad_token_id=tokenizer.pad_token_id,
    )
    GPT2LMHeadModel(config).save_pretrained(path, safe_serialization=True)
    return path


def _use_local_test_models(monkeypatch):
    """仅在单元测试中允许 AutoModel 从临时目录读取本地微型模型。"""
    original = llm_trainer._safe_hub_id

    def accept_local(value: str, label: str) -> str:
        if Path(value).is_dir():
            return value
        return original(value, label)

    monkeypatch.setattr(llm_trainer, "_safe_hub_id", accept_local)


def _params(model_path: Path, dataset_id: str = "local-text") -> dict:
    """返回微型本地模型测试使用的固定参数。"""
    return {
        "dataset_id": dataset_id,
        "dataset_source": "local",
        "base_model": str(model_path),
        "max_samples": 100,
        "max_seq_len": 16,
        "batch_size": 2,
        "epochs": 1,
        "lr": 1e-3,
        "seed": 7,
        "text_column": "text",
        "prompt_column": "prompt",
        "chosen_column": "chosen",
        "rejected_column": "rejected",
        "model_hidden_size": 32,
        "model_layers": 1,
        "model_heads": 4,
        "generation_tokens": 2,
        "ppo_updates": 1,
    }


def _write_rows(path: Path, rows: list[dict]) -> Path:
    """将测试样本写成本地 CSV 文件。"""
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _run(task: str, data_path: Path, params: dict, job_dir: Path, resume_state=None) -> dict:
    """经公共 trainer dispatcher 用 CPU 微型模型训练并返回 summary。"""
    import threading
    from app.trainer import run_training

    return run_training(
        dataset_path=data_path,
        target="",
        task=task,
        params=params,
        cancel_event=threading.Event(),
        pause_event=threading.Event(),
        log_fn=lambda message: None,
        job_dir=job_dir,
        resume_state=resume_state,
    )


def test_pretraining_from_scratch_saves_metrics_bundle_and_generates(tmp_path):
    """从零训练会生成 tokenizer/模型、loss/perplexity、可下载模型包和续写。"""
    rows = [{"text": f"machine learning corpus sentence number {i} " * 2} for i in range(50)]
    data_path = _write_rows(tmp_path / "corpus.csv", rows)
    params = {
        **_params(tmp_path),
        "base_model": None,
        "epochs": 2,
        "vocab_size": 48,
        "model_hidden_size": 32,
        "model_layers": 1,
        "model_heads": 4,
    }
    result = _run("llm_pretraining", data_path, params, tmp_path / "pretraining")

    metric = result["final"]
    assert np.isfinite(metric["train_loss"])
    assert np.isfinite(metric["val_loss"])
    assert np.isfinite(metric["perplexity"]) and metric["perplexity"] >= 1
    assert (tmp_path / "pretraining" / "best_model.zip").is_file()
    assert load_trainer_state(tmp_path / "pretraining" / "checkpoints" / "ckpt-e0002.pt")["kind"] == "llm_trainer_state"
    repeated = _run("llm_pretraining", data_path, params, tmp_path / "pretraining-repeat")
    assert repeated["final"]["train_loss"] == result["final"]["train_loss"]
    completion = llm_trainer.generate_text(
        tmp_path / "pretraining" / "best_model.zip", "machine learning",
        max_new_tokens=2, temperature=0.0, top_p=0.9,
    )
    assert isinstance(completion, str)
    assert completion == llm_trainer.generate_text(
        tmp_path / "pretraining" / "best_model.zip", "machine learning",
        max_new_tokens=2, temperature=0.0, top_p=0.9,
    )


def test_full_parameter_sft_changes_weights_and_preserves_generation(tmp_path, monkeypatch):
    """全参数 SFT 更新模型权重并能重载模型包生成文本。"""
    from transformers import AutoModelForCausalLM

    model_path = _tiny_base_model(tmp_path / "base")
    _use_local_test_models(monkeypatch)
    before = AutoModelForCausalLM.from_pretrained(model_path).state_dict()
    rows = [{"text": f"assistant: response example number {i} " * 2} for i in range(20)]
    data_path = _write_rows(tmp_path / "sft.csv", rows)
    params = _params(model_path)
    result = _run("llm_sft", data_path, params, tmp_path / "sft")

    assert np.isfinite(result["final"]["val_loss"])
    assert np.isfinite(result["final"]["perplexity"])
    bundle = tmp_path / "sft" / "best_model.zip"
    assert bundle.is_file()
    generated = llm_trainer.generate_text(bundle, "assistant:", max_new_tokens=2, temperature=0.7, top_p=0.9)
    assert isinstance(generated, str)
    extracted = tmp_path / "sft" / "loaded-model"
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(extracted)
    after = AutoModelForCausalLM.from_pretrained(extracted).state_dict()
    assert any(not torch.equal(before[name], after[name]) for name in before)


def test_dpo_uses_preference_pairs_and_emits_finite_metrics(tmp_path, monkeypatch):
    """DPO 使用 prompt/chosen/rejected 偏好对并输出有限 loss 与准确率。"""
    model_path = _tiny_base_model(tmp_path / "base")
    _use_local_test_models(monkeypatch)
    rows = [
        {"prompt": f"question {i}", "chosen": f"accepted answer {i}", "rejected": f"bad answer {i}"}
        for i in range(12)
    ]
    data_path = _write_rows(tmp_path / "dpo.csv", rows)
    params = _params(model_path)
    params["dpo_beta"] = 0.1
    result = _run("llm_dpo", data_path, params, tmp_path / "dpo")

    assert np.isfinite(result["final"]["train_loss"])
    assert 0.0 <= result["final"]["preference_accuracy"] <= 1.0
    assert (tmp_path / "dpo" / "best_model.zip").is_file()


def test_ppo_runs_reward_and_kl_update_and_restores_checkpoint(tmp_path, monkeypatch):
    """PPO 对 mini-batch 更新策略，记录 reward/KL，并可从断点继续训练。"""
    from transformers import GPT2Config, GPT2ForSequenceClassification

    model_path = _tiny_base_model(tmp_path / "base")
    reward_path = tmp_path / "reward"
    reward_path.mkdir()
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_path)
    tokenizer.save_pretrained(reward_path)
    reward_config = GPT2Config.from_pretrained(model_path, num_labels=1, pad_token_id=tokenizer.pad_token_id)
    GPT2ForSequenceClassification(reward_config).save_pretrained(reward_path, safe_serialization=True)
    _use_local_test_models(monkeypatch)
    rows = [{"prompt": f"write a short answer {i}"} for i in range(12)]
    data_path = _write_rows(tmp_path / "ppo.csv", rows)
    params = _params(model_path)
    params.update({"reward_model": str(reward_path), "generation_tokens": 2, "ppo_updates": 1})
    job_dir = tmp_path / "ppo"
    first = _run("llm_ppo", data_path, params, job_dir)
    assert np.isfinite(first["final"]["reward"])
    assert np.isfinite(first["final"]["kl"])
    checkpoint = load_trainer_state(job_dir / "checkpoints" / "ckpt-e0001.pt")

    params["epochs"] = 2
    resumed = _run("llm_ppo", data_path, params, job_dir, resume_state=checkpoint)
    assert resumed["resumed_from"] == 1
    assert resumed["epochs_done"] == 2
    assert np.isfinite(resumed["final"]["reward"])


def test_hub_load_is_restricted_to_valid_ids_and_disables_remote_code(monkeypatch):
    """Hub 数据和基座模型均关闭 remote code，并拒绝 URL 形式的仓库标识。"""
    import datasets
    import transformers

    captured = {}

    class FakeDataset:
        """模拟 Hugging Face Dataset 返回本地化记录。"""

        def to_list(self):
            return [{"text": "safe sample"}]

    def fake_load_dataset(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return FakeDataset()

    monkeypatch.setattr(datasets, "load_dataset", fake_load_dataset)
    rows = llm_trainer._load_rows(
        None,
        {"dataset_source": "hub", "dataset_id": "org/corpus", "dataset_split": "train"},
        "llm_pretraining",
        lambda message: None,
    )
    assert rows == [{"text": "safe sample"}]
    assert captured["trust_remote_code"] is False
    assert captured["split"] == "train[:5000]"

    model_calls = []

    class FakeTokenizer:
        """模拟无需网络下载的 Hub tokenizer。"""

        pad_token_id = 1
        eos_token = "[EOS]"
        unk_token = "[UNK]"
        pad_token = None

    def fake_from_pretrained(*args, **kwargs):
        model_calls.append((args, kwargs))
        return FakeTokenizer() if len(model_calls) == 1 else object()

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", fake_from_pretrained)
    monkeypatch.setattr(transformers.AutoModelForCausalLM, "from_pretrained", fake_from_pretrained)
    llm_trainer._load_base_components("org/base-model", {})
    assert len(model_calls) == 2
    assert all(call[1]["trust_remote_code"] is False for call in model_calls)
    assert all("token" not in call[1] for call in model_calls)
    with pytest.raises(ValueError, match="Hugging Face"):
        llm_trainer._load_rows(
            None,
            {"dataset_source": "hub", "dataset_id": "https://example.com/data"},
            "llm_pretraining",
            lambda message: None,
        )


def test_llm_api_accepts_hub_task_and_forces_single_gpu_resource_hint(monkeypatch):
    """Hub 训练无需本地数据集元信息，并始终分派到单 GPU worker。"""
    captured = {}

    def submit(spec):
        captured.update(spec)
        return {"id": "job-test-llm"}

    monkeypatch.setattr(jobs_router.manager, "submit", submit)
    request = TrainRequest(
        dataset_id="org/corpus",
        dataset_source="hub",
        task="llm_sft",
        base_model="org/base-model",
        require_gpu=False,
    )
    response = jobs_router.start_training(request)
    assert response == {"job_id": "job-test-llm"}
    assert captured["resource_hint"]["require_gpu"] is True
    assert captured["params"]["dataset_source"] == "hub"
    assert captured["params"]["lr"] == pytest.approx(2e-5)
    with pytest.raises(ValueError):
        TrainRequest(dataset_id="https://bad.example/data", dataset_source="hub", task="llm_pretraining")
    with pytest.raises(ValueError):
        TrainRequest(dataset_id="org/prefs", dataset_source="hub", task="llm_ppo", base_model="org/base")


def test_generate_api_returns_text_only_for_llm_tasks(tmp_path, monkeypatch):
    """文本生成端点从任务模型包生成文本，并拒绝非 LLM 任务。"""
    from app.routers.schemas import GenerateRequest

    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"tiny model archive")
    record = {"spec": {"task": "llm_sft"}}
    monkeypatch.setattr(jobs_router.manager, "get", lambda job_id: record)
    monkeypatch.setattr(jobs_router.manager.store, "exists", lambda key: True)
    monkeypatch.setattr(jobs_router.manager.store, "read_file", lambda key, path: bundle)
    monkeypatch.setattr(llm_trainer, "generate_text", lambda *args, **kwargs: "generated answer")

    response = jobs_router.generate(
        "job-test-llm",
        GenerateRequest(prompt="question", max_new_tokens=3),
    )
    assert response == {"prompt": "question", "generated_text": "generated answer"}
    monkeypatch.setattr(jobs_router.manager, "get", lambda job_id: {"spec": {"task": "regression"}})
    with pytest.raises(Exception, match="不是 LLM 任务"):
        jobs_router.generate("job-test-regression", GenerateRequest(prompt="question"))


def test_worker_executes_llm_job_and_persists_model_and_checkpoint(
    tmp_path, store, q, ls, make_job, spawn_worker
):
    """CPU 测试 worker 可执行 LLM job 并同步指标、断点及模型包产物。"""
    from app import config, jobstore
    from app.storage import job_key
    from tests.conftest import job_status, wait_until

    dataset_id = "llm-worker-corpus"
    dataset_path = config.DATASETS_DIR / f"{dataset_id}.csv"
    _write_rows(
        dataset_path,
        [{"text": f"worker training corpus sample {index} " * 2} for index in range(40)],
    )
    job_id = make_job(dataset_id, epochs=1, task="llm_pretraining")
    record = jobstore.read_job(store, job_id)
    record["spec"]["target"] = ""
    record["spec"]["resource_hint"] = {"require_gpu": False, "min_vram_mb": 0}
    record["spec"]["params"].update({
        "dataset_source": "local",
        "text_column": "text",
        "max_samples": 40,
        "max_seq_len": 16,
        "vocab_size": 48,
        "model_hidden_size": 32,
        "model_layers": 1,
        "model_heads": 4,
        "batch_size": 2,
        "seed": 7,
    })
    jobstore.write_job(store, record)
    q.push(job_id)
    worker = spawn_worker("llm-cpu-worker")

    assert wait_until(lambda: job_status(store, job_id) == "completed", timeout=45)
    assert store.exists(job_key(job_id, "best_model.zip"))
    assert store.exists(job_key(job_id, "checkpoints/ckpt-e0001.pt"))
    assert jobstore.read_metrics(store, job_id)[0]["perplexity"] >= 1
    worker.stop()


def test_jsonl_upload_imports_as_dataset_table(tmp_path):
    """JSONL 上传按行解析并转换为现有兼容的数据集 CSV 布局。"""
    from app.dataset_importer import DatasetImporter

    source = tmp_path / "preference.jsonl"
    source.write_text(
        json.dumps({"prompt": "question", "chosen": "yes", "rejected": "no"}) + "\n"
        + json.dumps({"prompt": "question 2", "chosen": "good", "rejected": "bad"}) + "\n",
        encoding="utf-8",
    )
    imported = DatasetImporter(tmp_path / "datasets")._import_single_file(source, "", [], True)
    canonical = pd.read_csv(tmp_path / "datasets" / "processed" / f"{imported['id']}.csv")
    assert list(canonical.columns) == ["prompt", "chosen", "rejected"]
    assert canonical.to_dict(orient="records")[0]["chosen"] == "yes"
