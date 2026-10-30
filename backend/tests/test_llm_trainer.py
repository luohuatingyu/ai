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
    performance = [json.loads(line) for line in (tmp_path / "pretraining" / "performance.jsonl").read_text().splitlines()]
    assert [row["epoch"] for row in performance] == [1, 2]
    assert all(row["effective_tokens"] > 0 and row["tokens_per_second"] > 0 for row in performance)
    assert all(row["samples"] == result["rows"]["train"] for row in performance)
    assert performance[-1]["checkpoint_save_seconds"] > 0
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


@pytest.mark.parametrize("suffix", [".csv", ".jsonl", ".ndjson", ".json"])
@pytest.mark.parametrize("count", [1, 3, 8])
def test_llm_loading_preserves_selected_rows(tmp_path, suffix, count):
    """限量读取保留样本内容与顺序，兼容不足、等于和超过上限的四种格式。"""
    rows = [{"text": f"样本 {index}, 包含引号\"和换行\n下一行", "index": index} for index in range(count)]
    path = tmp_path / f"corpus{suffix}"
    if suffix == ".csv":
        pd.DataFrame(rows).to_csv(path, index=False)
    elif suffix == ".json":
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    messages = []
    selected = llm_trainer._load_rows(path, {"max_samples": 3}, "llm_pretraining", messages.append)
    assert selected == rows[:3]
    assert f"读取到 {min(count, 3)} 条样本" in messages


@pytest.mark.parametrize("suffix", [".csv", ".jsonl", ".ndjson"])
def test_llm_sample_limit_stops_before_unused_records(tmp_path, suffix):
    """超过上限的损坏尾部不应被解析，采样边界内的真实内容必须保持一致。"""
    path = tmp_path / f"corpus{suffix}"
    if suffix == ".csv":
        path.write_text('text,index\nfirst sample,0\nsecond sample,1\nunused,bad,extra\n', encoding="utf-8")
    else:
        path.write_text('{"text":"first sample","index":0}\n{"text":"second sample","index":1}\n{broken\n', encoding="utf-8")
    assert llm_trainer._load_rows(path, {"max_samples": 2}, "llm_sft", lambda _: None) == [
        {"text": "first sample", "index": 0}, {"text": "second sample", "index": 1}]


@pytest.mark.parametrize("limit", [0, -1, 1_000_001, "invalid", None])
def test_llm_loading_rejects_invalid_sample_limit(tmp_path, limit):
    """非法样本上限在读文件和访问 Hub 之前报错，避免意外全量读取。"""
    with pytest.raises(ValueError, match="max_samples"):
        llm_trainer._load_rows(tmp_path / "missing.csv", {"max_samples": limit}, "llm_sft", lambda _: None)


@pytest.mark.parametrize("suffix,content", [
    (".csv", "text,index\n"), (".jsonl", ""), (".json", "[]"),
])
def test_llm_loading_rejects_empty_corpus(tmp_path, suffix, content):
    """空语料保留明确错误，不能启动无样本训练。"""
    path = tmp_path / f"empty{suffix}"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="为空"):
        llm_trainer._load_rows(path, {"max_samples": 3}, "llm_sft", lambda _: None)


def test_llm_loading_preserves_nested_messages(tmp_path):
    """JSONL 对话、缺失字段与空值保持原样，不把嵌套结构转换成字符串。"""
    rows = [{"messages": [{"role": "user", "content": "你好"}], "text": None},
            {"messages": [], "text": "回答"}]
    path = tmp_path / "messages.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    selected = llm_trainer._load_rows(path, {"max_samples": 2}, "llm_sft", lambda _: None)
    assert [row["messages"] for row in selected] == [row["messages"] for row in rows]
    assert pd.isna(selected[0]["text"]) and selected[1]["text"] == "回答"


def test_llm_jsonl_blank_lines_do_not_consume_sample_limit(tmp_path):
    """JSONL 的空白行不计入样本上限，超过上限的非法记录不会被读取。"""
    path = tmp_path / "blank.jsonl"
    path.write_text('\n {"text":"first"}\n\n {"text":"second"}\n {invalid}\n', encoding="utf-8")
    assert llm_trainer._load_rows(path, {"max_samples": 2}, "llm_sft", lambda _: None) == [
        {"text": "first"}, {"text": "second"}]


@pytest.mark.parametrize("suffix,content", [
    (".csv", 'text,index\nfirst,0\nsecond,1,extra\n'),
    (".jsonl", '{"text":"first"}\n{broken}\n'),
    (".json", '[{"text":"first"},broken]'),
])
def test_llm_loading_rejects_malformed_selected_records(tmp_path, suffix, content):
    """采样范围内的损坏记录必须报错，不能为了省内存静默跳过。"""
    path = tmp_path / f"broken{suffix}"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        llm_trainer._load_rows(path, {"max_samples": 3}, "llm_sft", lambda _: None)


def test_llm_loading_reports_missing_file_and_accepts_maximum_limit(tmp_path):
    """合法最大上限不要求文件达到该行数，缺失文件保留明确错误。"""
    path = tmp_path / "tiny.csv"
    path.write_text("text\nsmall corpus\n", encoding="utf-8")
    assert llm_trainer._load_rows(path, {"max_samples": 1_000_000}, "llm_sft", lambda _: None) == [{"text": "small corpus"}]
    with pytest.raises(ValueError, match="文件不存在"):
        llm_trainer._load_rows(tmp_path / "missing.csv", {"max_samples": 2}, "llm_sft", lambda _: None)


@pytest.mark.parametrize("task", ["llm_pretraining", "llm_sft"])
def test_llm_training_releases_raw_records_after_encoding(tmp_path, monkeypatch, task):
    """实际预训练/微调在切分编码样本前释放原始记录，并完成模型与指标保存。"""
    import weakref

    class RawRows(list):
        """支持弱引用的记录列表，用于观察语料生命周期。"""

    references = []
    original_load = llm_trainer._load_rows
    original_split = llm_trainer._split_examples

    def load(*args, **kwargs):
        """真实解析输入，仅增加生命周期观察。"""
        rows = RawRows(original_load(*args, **kwargs))
        references.append(weakref.ref(rows))
        return rows

    def split(examples, seed):
        """核心编码和切分保留原样，断言原始语料已可回收。"""
        assert references and all(reference() is None for reference in references)
        return original_split(examples, seed)

    monkeypatch.setattr(llm_trainer, "_load_rows", load)
    monkeypatch.setattr(llm_trainer, "_split_examples", split)
    model_path = _tiny_base_model(tmp_path / "base")
    _use_local_test_models(monkeypatch)
    params = {**_params(model_path), "vocab_size": 48}
    if task == "llm_pretraining":
        params["base_model"] = None
    path = _write_rows(tmp_path / "text.csv", [{"text": f"training corpus sentence {i}"} for i in range(10)])
    result = _run(task, path, params, tmp_path / "job")
    assert np.isfinite(result["final"]["val_loss"])
    assert (tmp_path / "job" / "best_model.zip").stat().st_size > 0


@pytest.mark.parametrize("iterable", [False, True])
def test_hub_loading_limits_before_materializing_rows(monkeypatch, iterable):
    """真实 Dataset 和迭代式返回值都只将选中样本转为 Python 记录。"""
    import datasets

    rows = [{"text": f"sample {index}"} for index in range(5)]
    source = datasets.Dataset.from_list(rows)
    if iterable:
        def records():
            """上限之后不可消费，用于捕获全量物化回归。"""
            yield from rows[:2]
            raise AssertionError("读取了超过 max_samples 的 Hub 记录")
        source = records()
    monkeypatch.setattr(datasets, "load_dataset", lambda *args, **kwargs: source)
    assert llm_trainer._load_rows(None, {"dataset_source": "hub", "dataset_id": "org/corpus",
        "dataset_split": "train[:5]", "max_samples": 2}, "llm_sft", lambda _: None) == rows[:2]


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

        def __iter__(self):
            """按真实 Dataset 接口提供记录，保留安全参数断言。"""
            return iter([{"text": "safe sample"}])

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
