"""Hugging Face decoder-only LLM 训练、微调与文本生成。"""
from __future__ import annotations

import ast
import json
import math
import re
import shutil
import tempfile
import time
import zipfile
from io import StringIO
from itertools import islice
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import torch
from app.performance import EpochPerformance
from torch import nn

from app.device import backend_from_device, seed_device, select_device

LogFn = Callable[[str], None]
_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?$")


def _safe_hub_id(value: str, label: str) -> str:
    """校验 Hub 仓库标识，防止把任意 URL 或本地路径交给下载器。"""
    if not _REPO_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{label}必须是有效的 Hugging Face 仓库 ID")
    return value


def _load_rows(dataset_path: Path | None, params: dict, task: str, log_fn: LogFn) -> list[dict]:
    """从本地表格/JSONL 或 Hugging Face Hub 读取训练样本。"""
    # 与 API 的样本范围一致，在任何磁盘/网络读取之前拒绝无界或非法请求。
    try:
        limit = int(params.get("max_samples", 5000))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("max_samples 必须为 1 到 1000000 的整数") from exc
    if not 1 <= limit <= 1_000_000:
        raise ValueError("max_samples 必须为 1 到 1000000 的整数")
    source = str(params.get("dataset_source", "local"))
    if source == "hub":
        from datasets import load_dataset

        dataset_id = _safe_hub_id(str(params.get("dataset_id", "")), "数据集 ID")
        dataset_config = params.get("dataset_config") or None
        split = str(params.get("dataset_split", "train"))
        if "[" not in split:
            split = f"{split}[:{limit}]"
        cache_dir = str(_cache_dir())
        log_fn(f"从 Hugging Face Hub 加载数据集 {dataset_id}（split={split}）")
        data = load_dataset(
            dataset_id,
            name=dataset_config,
            split=split,
            cache_dir=cache_dir,
            trust_remote_code=False,
        )
        # Dataset 与 IterableDataset 都按样本迭代，显式切片 split 也不得绕过上限。
        rows = [dict(row) for row in islice(data, limit)]
    else:
        if dataset_path is None or not Path(dataset_path).is_file():
            raise ValueError("本地 LLM 训练数据集文件不存在")
        path = Path(dataset_path)
        log_fn(f"读取本地 LLM 语料 {path.name}")
        if path.suffix.lower() in {".jsonl", ".ndjson"}:
            # 只解析所选的非空记录，空行不消耗样本额度；保留 Pandas 的嵌套字段处理。
            with path.open("r", encoding="utf-8") as stream:
                selected = "".join(islice((line for line in stream if line.strip()), limit))
            frame = pd.read_json(StringIO(selected), lines=True) if selected else pd.DataFrame()
        elif path.suffix.lower() == ".json":
            # 普通 JSON 保持原有布局兼容；截取后再转字典，避免物化未选中的记录。
            frame = pd.read_json(path).iloc[:limit]
        else:
            frame = pd.read_csv(path, nrows=limit)
        rows = frame.to_dict(orient="records")
        log_fn(f"按 max_samples={limit} 读取样本；CSV/JSONL 类型推断仅基于选中的记录")
    if not rows:
        raise ValueError("LLM 训练数据集为空")
    log_fn(f"读取到 {len(rows)} 条样本")
    return rows


def _cache_dir() -> Path:
    """为模型和 Hub 数据集提供跨任务复用的持久缓存目录。"""
    import app.config as config

    path = config.DATA_DIR / "hf_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _as_text(value: Any) -> str:
    """把纯文本或常见 messages 对话结构统一渲染为训练文本。"""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                return _as_text(json.loads(stripped))
            except (json.JSONDecodeError, TypeError):
                try:
                    return _as_text(ast.literal_eval(stripped))
                except (ValueError, SyntaxError):
                    return value
        return value
    if isinstance(value, list):
        rendered = []
        for message in value:
            if isinstance(message, dict):
                role = str(message.get("role", message.get("from", "assistant")))
                content = message.get("content", message.get("value", ""))
                rendered.append(f"{role}: {_as_text(content)}")
            else:
                rendered.append(str(message))
        return "\n".join(rendered)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None or pd.isna(value) else str(value)


def _text_from_row(row: dict, params: dict) -> str:
    """根据用户选择的列名或常见对话列获取单条文本。"""
    column = str(params.get("text_column", "text"))
    if column in row:
        return _as_text(row[column])
    for candidate in ("messages", "text", "content"):
        if candidate in row:
            return _as_text(row[candidate])
    raise ValueError(f"找不到文本列 '{column}'（也未发现 messages/text/content 列）")


def _make_pretraining_tokenizer(texts: list[str], vocab_size: int):
    """从预训练语料构建并返回 ByteLevel BPE tokenizer。"""
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast

    special = ["[PAD]", "[UNK]", "[BOS]", "[EOS]"]
    backend = Tokenizer(models.BPE(unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    backend.decoder = decoders.ByteLevel()
    backend.train_from_iterator(
        texts,
        trainer=trainers.BpeTrainer(
            vocab_size=max(int(vocab_size), len(special) + 8),
            min_frequency=1,
            special_tokens=special,
        ),
    )
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=backend,
        bos_token="[BOS]",
        eos_token="[EOS]",
        unk_token="[UNK]",
        pad_token="[PAD]",
    )
    tokenizer.padding_side = "right"
    return tokenizer


def _load_base_components(model_id: str, params: dict):
    """安全地从 Hugging Face Hub 加载基座模型和 tokenizer。"""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_id = _safe_hub_id(model_id, "基座模型")
    tokenizer = AutoTokenizer.from_pretrained(
        model_id,
        cache_dir=str(_cache_dir()),
        trust_remote_code=False,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token
    tokenizer.padding_side = "right"
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        cache_dir=str(_cache_dir()),
        trust_remote_code=False,
    )
    return model, tokenizer


def _prepare_examples(rows: list[dict], task: str, params: dict, tokenizer, log_fn: LogFn) -> list:
    """把原始语料转换为自回归预训练、SFT、DPO 或 PPO 样本。"""
    max_length = int(params.get("max_seq_len", 512))
    if task == "llm_pretraining":
        texts = [_text_from_row(row, params).strip() for row in rows]
        tokenizer = _make_pretraining_tokenizer(texts, int(params.get("vocab_size", 8000)))
        token_stream: list[int] = []
        for text in texts:
            if text:
                token_stream.extend(tokenizer.encode(text, add_special_tokens=True))
                if tokenizer.eos_token_id is not None:
                    token_stream.append(int(tokenizer.eos_token_id))
        examples = [
            {"input_ids": token_stream[index:index + max_length]}
            for index in range(0, len(token_stream), max_length)
            if len(token_stream[index:index + max_length]) >= 2
        ]
    elif task == "llm_sft":
        examples = []
        prompt_column = params.get("prompt_column")
        completion_column = params.get("completion_column")
        for row in rows:
            if completion_column and prompt_column in row and completion_column in row:
                prompt = _as_text(row[prompt_column])
                completion = _as_text(row[completion_column])
                prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
                completion_ids = tokenizer.encode(completion, add_special_tokens=False)
                if tokenizer.eos_token_id is not None:
                    completion_ids.append(int(tokenizer.eos_token_id))
                completion_ids = completion_ids[:max_length]
                prompt_ids = prompt_ids[:max(max_length - len(completion_ids), 0)]
                ids = prompt_ids + completion_ids
                labels = [-100] * len(prompt_ids) + completion_ids[:]
            else:
                text = _text_from_row(row, params)
                ids = tokenizer.encode(text, add_special_tokens=True)[:max_length]
                labels = ids[:]
            if len(ids) >= 2 and any(label != -100 for label in labels):
                examples.append({"input_ids": ids, "labels": labels})
    elif task in {"llm_dpo", "llm_ppo"}:
        prompt_column = str(params.get("prompt_column", "prompt"))
        chosen_column = str(params.get("chosen_column", "chosen"))
        rejected_column = str(params.get("rejected_column", "rejected"))
        examples = []
        for row in rows:
            if prompt_column not in row:
                raise ValueError(f"找不到偏好数据的 prompt 列 '{prompt_column}'")
            prompt = _as_text(row[prompt_column])
            item = {"prompt": prompt}
            if task == "llm_dpo":
                for column in (chosen_column, rejected_column):
                    if column not in row:
                        raise ValueError(f"找不到偏好数据列 '{column}'")
                item["chosen"] = _as_text(row[chosen_column])
                item["rejected"] = _as_text(row[rejected_column])
                item["chosen_ids"] = _join_prompt_completion(tokenizer, prompt, item["chosen"], max_length)
                item["rejected_ids"] = _join_prompt_completion(tokenizer, prompt, item["rejected"], max_length)
            else:
                item["prompt_ids"] = tokenizer.encode(prompt, add_special_tokens=False)[-max_length:]
                if len(item["prompt_ids"]) < 1:
                    raise ValueError("PPO prompt 不能为空")
            examples.append(item)
    else:
        raise ValueError(f"不支持的 LLM 任务类型: {task}")
    if len(examples) < 2:
        raise ValueError("至少需要 2 条有效 LLM 样本")
    log_fn(f"预处理完成：{len(examples)} 条训练样本，最大序列长度 {max_length}")
    return examples, tokenizer


def _join_prompt_completion(tokenizer, prompt: str, completion: str, max_length: int) -> dict:
    """编码偏好回答并返回仅对回答部分计算概率的标签。"""
    prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
    answer_ids = tokenizer.encode(completion, add_special_tokens=False)
    if tokenizer.eos_token_id is not None:
        answer_ids.append(int(tokenizer.eos_token_id))
    answer_ids = answer_ids[:max_length]
    prompt_ids = prompt_ids[-max(max_length - len(answer_ids), 0):]
    ids = prompt_ids + answer_ids
    labels = [-100] * len(prompt_ids) + answer_ids[:]
    if len(ids) < 2 or not answer_ids:
        raise ValueError("DPO 的 prompt/chosen/rejected 编码后必须包含回答 token")
    return {"input_ids": ids, "labels": labels}


def _split_examples(examples: list, seed: int) -> tuple[list, list]:
    """按固定 seed 划分训练与验证样本，保证相同输入的切分可复现。"""
    generator = torch.Generator().manual_seed(seed)
    order = torch.randperm(len(examples), generator=generator).tolist()
    val_count = max(1, int(len(examples) * 0.1))
    if len(examples) - val_count < 1:
        val_count = 0
    val_ids = set(order[:val_count])
    train_rows = [item for index, item in enumerate(examples) if index not in val_ids]
    val_rows = [item for index, item in enumerate(examples) if index in val_ids]
    return train_rows, val_rows


def _batches(items: list, batch_size: int, seed: int, epoch: int):
    """使用固定随机种子的索引排列生成小批次。"""
    generator = torch.Generator().manual_seed(seed + epoch)
    order = torch.randperm(len(items), generator=generator).tolist()
    for start in range(0, len(order), batch_size):
        yield [items[index] for index in order[start:start + batch_size]]


def _collate(items: list[dict], pad_id: int, device: torch.device) -> dict[str, torch.Tensor]:
    """将可变长度 token 序列补齐为模型输入张量。"""
    width = max(len(item["input_ids"]) for item in items)
    input_ids = torch.full((len(items), width), int(pad_id), dtype=torch.long, pin_memory=device.type == "cuda")
    attention_mask = torch.zeros((len(items), width), dtype=torch.long, pin_memory=device.type == "cuda")
    labels = torch.full((len(items), width), -100, dtype=torch.long, pin_memory=device.type == "cuda")
    for index, item in enumerate(items):
        size = len(item["input_ids"])
        input_ids[index, :size] = torch.tensor(item["input_ids"], dtype=torch.long)
        attention_mask[index, :size] = 1
        item_labels = item.get("labels", item["input_ids"])
        labels[index, :len(item_labels)] = torch.tensor(item_labels, dtype=torch.long)
    return {
        "input_ids": input_ids.to(device, non_blocking=device.type == "cuda"),
        "attention_mask": attention_mask.to(device, non_blocking=device.type == "cuda"),
        "labels": labels.to(device, non_blocking=device.type == "cuda"),
    }


def _new_model(tokenizer, params: dict):
    """创建从随机权重开始的 GPT-2 decoder-only 因果语言模型。"""
    from transformers import GPT2Config, GPT2LMHeadModel

    size = int(params.get("model_hidden_size", 256))
    layers = int(params.get("model_layers", 4))
    heads = int(params.get("model_heads", 4))
    context = int(params.get("max_seq_len", 512))
    config = GPT2Config(
        vocab_size=len(tokenizer),
        n_positions=context,
        n_ctx=context,
        n_embd=size,
        n_layer=layers,
        n_head=heads,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
        pad_token_id=tokenizer.pad_token_id,
    )
    return GPT2LMHeadModel(config)


def _sequence_logprob(model, input_ids: torch.Tensor, attention_mask: torch.Tensor, labels: torch.Tensor):
    """计算每条序列中回答 token 的总对数概率和末 token 隐藏状态。"""
    output = model(input_ids=input_ids, attention_mask=attention_mask, output_hidden_states=True)
    log_probs = torch.log_softmax(output.logits[:, :-1, :], dim=-1)
    target_ids = input_ids[:, 1:]
    token_log_probs = log_probs.gather(-1, target_ids.unsqueeze(-1)).squeeze(-1)
    response_mask = labels[:, 1:] != -100
    sequence_logprob = (token_log_probs * response_mask).sum(dim=1)
    last_indices = attention_mask.sum(dim=1).clamp(min=1) - 1
    batch_indices = torch.arange(input_ids.shape[0], device=input_ids.device)
    final_hidden = output.hidden_states[-1][batch_indices, last_indices]
    return sequence_logprob, final_hidden


def _save_bundle(model, tokenizer, job_dir: Path) -> Path:
    """将模型权重和 tokenizer 打包为可下载、可重载的单个 ZIP 文件。"""
    bundle_dir = job_dir / "llm-model-bundle"
    shutil.rmtree(bundle_dir, ignore_errors=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(bundle_dir, safe_serialization=True)
    tokenizer.save_pretrained(bundle_dir)
    archive_path = job_dir / "best_model.zip"
    temporary = job_dir / "best_model.zip.tmp"
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle_dir).as_posix())
    temporary.replace(archive_path)
    return archive_path


def _checkpoint_state(task: str, params: dict, epoch: int, best_score: float | None,
                      best_epoch: int, model, optimizer, value_head=None) -> dict:
    """生成仅含安全基础类型和 CPU 张量的可迁移 LLM 断点。"""
    signature = {
        key: params.get(key)
        for key in (
            "dataset_id", "dataset_source", "base_model", "reward_model",
            "max_seq_len", "vocab_size", "model_hidden_size", "model_layers", "model_heads",
        )
    }
    state = {
        "kind": "llm_trainer_state",
        "task": task,
        "signature": signature,
        "epoch": int(epoch),
        "best_score": best_score,
        "best_epoch": int(best_epoch),
        "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "optimizer": _cpu_tree(optimizer.state_dict()),
        "torch_rng_state": torch.get_rng_state(),
    }
    if value_head is not None:
        state["value_head_state"] = {key: value.detach().cpu() for key, value in value_head.state_dict().items()}
    return state


def _cpu_tree(value: Any) -> Any:
    """递归将优化器状态中的张量搬到 CPU，方便跨显卡和跨主机恢复。"""
    if torch.is_tensor(value):
        return value.detach().cpu()
    if isinstance(value, dict):
        return {key: _cpu_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_cpu_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_cpu_tree(item) for item in value)
    return value


def _write_checkpoint(state: dict, job_dir: Path, epoch: int, on_checkpoint) -> None:
    """原子保存 epoch 断点并通知 worker 上传状态平面。"""
    path = job_dir / "checkpoints" / f"ckpt-e{epoch:04d}.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    torch.save(state, temporary)
    temporary.replace(path)
    if on_checkpoint is not None:
        on_checkpoint(epoch)


def _write_metric(job_dir: Path, record: dict) -> None:
    """把训练进度按 JSON Lines 追加到任务指标文件。"""
    with (job_dir / "metrics.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def _restore_checkpoint(resume_state: dict | None, task: str, params: dict, model, optimizer, value_head=None):
    """验证断点与当前模型来源兼容，并恢复模型、优化器及随机状态。"""
    if not resume_state:
        return 0, None, 0
    expected = {
        key: params.get(key)
        for key in (
            "dataset_id", "dataset_source", "base_model", "reward_model",
            "max_seq_len", "vocab_size", "model_hidden_size", "model_layers", "model_heads",
        )
    }
    if resume_state.get("task") != task or resume_state.get("signature") != expected:
        raise ValueError("LLM checkpoint 与任务类型、数据源或模型参数不兼容")
    model.load_state_dict(resume_state["state_dict"])
    optimizer.load_state_dict(resume_state["optimizer"])
    if value_head is not None and resume_state.get("value_head_state"):
        value_head.load_state_dict(resume_state["value_head_state"])
    rng_state = resume_state.get("torch_rng_state")
    if isinstance(rng_state, torch.Tensor):
        torch.set_rng_state(rng_state.cpu())
    return int(resume_state.get("epoch", 0)), resume_state.get("best_score"), int(resume_state.get("best_epoch", 0))


def _encode_pair_batch(batch: list[dict], pad_id: int, key: str, device: torch.device):
    """为 DPO chosen/rejected 回答分别构建对齐 token 张量。"""
    return _collate([item[key] for item in batch], pad_id, device)


def _dpo_loss(policy, reference, batch: list[dict], tokenizer, beta: float, device: torch.device):
    """计算标准 DPO sigmoid 目标和 chosen 偏好准确率。"""
    chosen = _encode_pair_batch(batch, tokenizer.pad_token_id, "chosen_ids", device)
    rejected = _encode_pair_batch(batch, tokenizer.pad_token_id, "rejected_ids", device)
    chosen_logp, _ = _sequence_logprob(policy, chosen["input_ids"], chosen["attention_mask"], chosen["labels"])
    rejected_logp, _ = _sequence_logprob(policy, rejected["input_ids"], rejected["attention_mask"], rejected["labels"])
    with torch.no_grad():
        ref_chosen, _ = _sequence_logprob(reference, chosen["input_ids"], chosen["attention_mask"], chosen["labels"])
        ref_rejected, _ = _sequence_logprob(reference, rejected["input_ids"], rejected["attention_mask"], rejected["labels"])
    logits = float(beta) * ((chosen_logp - rejected_logp) - (ref_chosen - ref_rejected))
    return -torch.nn.functional.logsigmoid(logits).mean(), (logits > 0).float().mean()


def _ppo_update(policy, reference, reward_model, reward_tokenizer, tokenizer, value_head,
                optimizer, prompts: list[str], params: dict, device: torch.device, parallel=None):
    """采样回答并执行带 KL 惩罚和裁剪目标的 PPO policy/value 更新。"""
    old_logprobs, ref_logprobs, rewards, prompt_tokens = [], [], [], []
    for prompt in prompts:
        encoded = tokenizer(prompt, return_tensors="pt", truncation=True,
                            max_length=max(int(params.get("max_seq_len", 512)) // 2, 1)).to(device)
        prompt_ids = encoded["input_ids"]
        with torch.no_grad():
            generated = policy.generate(
                **encoded,
                max_new_tokens=int(params.get("generation_tokens", 32)),
                do_sample=True,
                temperature=float(params.get("temperature", 0.8)),
                top_p=float(params.get("top_p", 0.9)),
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        completion_ids = generated[:, prompt_ids.shape[1]:]
        if completion_ids.numel() == 0:
            raise ValueError("PPO 生成了空回答，请增大 generation_tokens")
        full_ids = torch.cat([prompt_ids, completion_ids], dim=1)
        full_mask = torch.ones_like(full_ids)
        labels = full_ids.clone()
        labels[:, :prompt_ids.shape[1]] = -100
        with torch.no_grad():
            old_logp, _ = _sequence_logprob(policy, full_ids, full_mask, labels)
            ref_logp, _ = _sequence_logprob(reference, full_ids, full_mask, labels)
        completion = tokenizer.decode(completion_ids[0], skip_special_tokens=True)
        reward_input = reward_tokenizer(
            prompt + completion, return_tensors="pt", truncation=True,
            max_length=int(params.get("max_seq_len", 512)),
        ).to(device)
        with torch.no_grad():
            reward_output = reward_model(**reward_input)
            raw_reward = reward_output.logits.reshape(-1)[0]
        old_logprobs.append(old_logp[0])
        ref_logprobs.append(ref_logp[0])
        rewards.append(raw_reward)
        prompt_tokens.append((full_ids, full_mask, labels))

    old_logprob_tensor = torch.stack(old_logprobs).detach() if prompts else torch.empty(0, device=device)
    ref_logprob_tensor = torch.stack(ref_logprobs).detach() if prompts else torch.empty(0, device=device)
    raw_reward_tensor = torch.stack(rewards).detach() if prompts else torch.empty(0, device=device)
    kl = old_logprob_tensor - ref_logprob_tensor
    adjusted_rewards = raw_reward_tensor - float(params.get("kl_beta", 0.1)) * kl
    with torch.no_grad():
        old_values = []
        for ids, mask, _ in prompt_tokens:
            output = policy(input_ids=ids, attention_mask=mask, output_hidden_states=True)
            old_values.append(value_head(output.hidden_states[-1][:, -1, :]).reshape(()))
        advantages = adjusted_rewards - (torch.stack(old_values).detach() if prompts else torch.empty(0, device=device))
        if parallel is None:
            scale = advantages.std(unbiased=False).clamp(min=1e-6)
            advantages = (advantages - advantages.mean()) / scale
        else:
            # PPO 优势归一化必须覆盖全局批次，不能让不同大小的节点各自归一化。
            total, squares, count = parallel.totals([float(advantages.sum()), float(advantages.square().sum()), len(prompts)])
            mean = total / max(count, 1)
            scale = max(squares / max(count, 1) - mean * mean, 1e-12) ** 0.5
            advantages = (advantages - mean) / scale

    ppo_epochs = int(params.get("ppo_updates", 4))
    clip_range = float(params.get("clip_range", 0.2))
    total_loss = 0.0
    for _ in range(ppo_epochs):
        if not prompts and parallel is not None:
            optimizer.zero_grad(set_to_none=True)
            parallel.step(optimizer, 0, max_norm=1.0)
            continue
        new_logprobs, values = [], []
        for ids, mask, labels in prompt_tokens:
            new_logp, hidden = _sequence_logprob(policy, ids, mask, labels)
            new_logprobs.append(new_logp[0])
            values.append(value_head(hidden).reshape(()))
        new_logprob_tensor = torch.stack(new_logprobs)
        value_tensor = torch.stack(values)
        ratio = torch.exp((new_logprob_tensor - old_logprob_tensor).clamp(-10, 10))
        unclipped = ratio * advantages
        clipped = ratio.clamp(1.0 - clip_range, 1.0 + clip_range) * advantages
        actor_loss = -torch.minimum(unclipped, clipped).mean()
        value_loss = torch.nn.functional.mse_loss(value_tensor, adjusted_rewards)
        loss = actor_loss + 0.5 * value_loss
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        if parallel is None:
            torch.nn.utils.clip_grad_norm_(list(policy.parameters()) + list(value_head.parameters()), 1.0)
            optimizer.step()
        else:
            parallel.step(optimizer, len(prompts), max_norm=1.0)
        total_loss += float(loss.detach().cpu())
    return {
        "loss": total_loss / ppo_epochs,
        "reward": float(raw_reward_tensor.mean().cpu()) if prompts else 0.0,
        "kl": float(kl.mean().cpu()) if prompts else 0.0,
    }


def run_llm_training(task: str, dataset_path: Path | None, params: dict,
                     cancel_event, pause_event, log_fn: LogFn, job_dir: Path,
                     resume_state: dict | None = None, on_epoch_end=None,
                     on_checkpoint=None, parallel=None) -> dict:
    """执行从零预训练、全参数 SFT、DPO 或基于奖励模型的 PPO-RLHF。"""
    from transformers import AutoModelForSequenceClassification

    started_at = time.time()
    job_dir.mkdir(parents=True, exist_ok=True)
    seed = int(params.get("seed", 42))
    torch.manual_seed(seed)
    seed_device(seed, parallel.device if parallel is not None else None)
    rows = _load_rows(dataset_path, params, task, log_fn)
    cache = _cache_dir()

    if task == "llm_pretraining":
        texts = [_text_from_row(row, params).strip() for row in rows]
        tokenizer = _make_pretraining_tokenizer(texts, int(params.get("vocab_size", 8000)))
        examples, tokenizer = _prepare_examples(rows, task, params, tokenizer, log_fn)
        # 编码结果已包含训练所需内容，构建模型前释放语料和 tokenizer 训练文本。
        del rows, texts
        model = _new_model(tokenizer, params)
        reference = reward_model = reward_tokenizer = value_head = None
    else:
        model, tokenizer = _load_base_components(str(params.get("base_model", "")), params)
        examples, tokenizer = _prepare_examples(rows, task, params, tokenizer, log_fn)
        # 后续偏好/奖励模型只依赖编码样本，无需继续保留原始语料字典。
        del rows
        reference = reward_model = reward_tokenizer = value_head = None
        if task in {"llm_dpo", "llm_ppo"}:
            reference, _ = _load_base_components(str(params.get("base_model", "")), params)
            reference.eval()
            for parameter in reference.parameters():
                parameter.requires_grad_(False)
        if task == "llm_ppo":
            reward_id = _safe_hub_id(str(params.get("reward_model", "")), "奖励模型")
            from transformers import AutoTokenizer

            reward_tokenizer = AutoTokenizer.from_pretrained(
                reward_id, cache_dir=str(cache), trust_remote_code=False
            )
            reward_model = AutoModelForSequenceClassification.from_pretrained(
                reward_id, cache_dir=str(cache), trust_remote_code=False
            )
            if int(getattr(reward_model.config, "num_labels", 0)) != 1:
                raise ValueError("PPO 奖励模型必须输出单个标量分数（num_labels=1）")
            reward_model.eval()
            for parameter in reward_model.parameters():
                parameter.requires_grad_(False)
            hidden_size = int(getattr(model.config, "hidden_size", getattr(model.config, "n_embd", 0)))
            if hidden_size <= 0:
                raise ValueError("无法从基座模型读取隐藏维度，不能创建 PPO value head")
            value_head = nn.Linear(hidden_size, 1)

    train_examples, val_examples = _split_examples(examples, seed)
    device = parallel.device if parallel is not None else select_device(log_fn=log_fn)
    model.to(device)
    if reference is not None:
        reference.to(device)
    if reward_model is not None:
        reward_model.to(device)
        reward_tokenizer.padding_side = "right"
        if reward_tokenizer.pad_token_id is None:
            reward_tokenizer.pad_token = reward_tokenizer.eos_token or reward_tokenizer.unk_token
    if value_head is not None:
        value_head.to(device)
    parameters = list(model.parameters()) + (list(value_head.parameters()) if value_head is not None else [])
    optimizer = torch.optim.AdamW(parameters, lr=float(params.get("lr", 2e-5)))
    start_epoch, best_score, best_epoch = _restore_checkpoint(
        resume_state, task, params, model, optimizer, value_head
    )
    if parallel is not None:
        identity = json.dumps({"examples": examples, "vocabulary": tokenizer.get_vocab()}, sort_keys=True).encode()
        parallel.initialize([model] + ([value_head] if value_head is not None else []), identity)
        # 冻结的参考/奖励模型也必须一致，否则各节点在优化不同的目标函数。
        for fixed_model in (reference, reward_model):
            if fixed_model is not None:
                parallel.sync_module(fixed_model)
    batch_size = max(1, min(int(params.get("batch_size", 1)), 64))
    epochs = int(params.get("epochs", 1))
    beta = float(params.get("dpo_beta", 0.1))
    log_fn(f"LLM 训练设备：{device}；训练模式：{task}；优化方式：全参数 AdamW")

    epoch = start_epoch
    record = {}
    for epoch in range(start_epoch + 1, epochs + 1):
        performance = EpochPerformance(epoch, "group" if parallel is not None else "single")
        if parallel is not None and parallel.control(cancel_event, pause_event, allow_pause=True):
            if parallel.primary:
                _write_checkpoint(_checkpoint_state(task, params, epoch - 1, best_score, best_epoch, model, optimizer, value_head), job_dir, epoch - 1, on_checkpoint)
            from app.trainer import JobPaused
            raise JobPaused(f"联合训练已在 epoch {epoch - 1} 后暂停")
        if cancel_event.is_set():
            from app.trainer import JobCancelled
            raise JobCancelled()
        model.train()
        if task == "llm_ppo":
            value_head.train()
        losses, extra_metrics = [], []
        weights = []
        steps = 0
        samples_seen = 0
        train_started = time.perf_counter()
        batches = _batches(train_examples, batch_size, seed, epoch) if parallel is None else (
            [train_examples[index] for index in indices]
            for indices in parallel.indices(len(train_examples), epoch)
        )
        for batch in batches:
            steps += 1
            samples_seen += len(batch)
            if parallel is not None:
                parallel.control(cancel_event, pause_event)
            elif cancel_event.is_set() or pause_event.is_set():
                break
            if not batch and task != "llm_ppo":
                optimizer.zero_grad(set_to_none=True)
                parallel.step(optimizer, 0, max_norm=1.0)
                continue
            if task in {"llm_pretraining", "llm_sft"}:
                encoded = _collate(batch, tokenizer.pad_token_id, device)
                # 标签尚在 CPU 样本中，直接计数有效目标，避免为一个整数同步 GPU。
                weight = sum(sum(value != -100 for value in item.get("labels", item["input_ids"])[1:]) for item in batch)
                output = model(**encoded)
                loss = output.loss
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                if parallel is None:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                else:
                    parallel.step(optimizer, weight, max_norm=1.0)
                losses.append(float(loss.detach()) if device.type == "cpu" else loss.detach().to(torch.float32 if device.type == "mps" else torch.float64))
                weights.append(weight)
            elif task == "llm_dpo":
                loss, preference_accuracy = _dpo_loss(model, reference, batch, tokenizer, beta, device)
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                if parallel is None:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                else:
                    parallel.step(optimizer, len(batch), max_norm=1.0)
                losses.append(float(loss.detach()) if device.type == "cpu" else loss.detach().to(torch.float32 if device.type == "mps" else torch.float64))
                extra_metrics.append(float(preference_accuracy.detach()) if device.type == "cpu" else preference_accuracy.detach())
                weights.append(len(batch))
            else:
                metrics = _ppo_update(
                    model, reference, reward_model, reward_tokenizer, tokenizer, value_head,
                    optimizer, [item["prompt"] for item in batch], params, device, parallel=parallel,
                )
                losses.append(metrics["loss"])
                extra_metrics.append(metrics)
                weights.append(len(batch))
        if parallel is not None:
            parallel.control(cancel_event, pause_event)
        if parallel is None and cancel_event.is_set():
            from app.trainer import JobCancelled
            raise JobCancelled()
        if not losses and parallel is None:
            if pause_event.is_set():
                from app.trainer import JobPaused
                raise JobPaused("LLM 训练已暂停；最近完成的 epoch checkpoint 保留")
            raise ValueError("训练没有产生有效 batch")

        model.eval()
        if parallel is None:
            epoch_loss = float(sum(losses) / len(losses))
        else:
            loss_sum, weight_sum = parallel.totals([float(sum(loss * weight for loss, weight in zip(losses, weights))), sum(weights)])
            epoch_loss = loss_sum / max(weight_sum, 1)
            samples_seen = int(parallel.totals([samples_seen])[0])
        performance.record["train_seconds"] = time.perf_counter() - train_started
        effective_tokens = int(weight_sum if parallel is not None else sum(weights)) if task in {"llm_pretraining", "llm_sft"} else None
        record = {"epoch": epoch, "train_loss": epoch_loss}
        validation_started = time.perf_counter()
        if task in {"llm_pretraining", "llm_sft"}:
            validation_losses = []
            validation_weights = []
            with torch.no_grad():
                for batch in _batches(val_examples or train_examples, batch_size, seed, epoch + 10_000):
                    if parallel is not None and not parallel.primary:
                        break
                    encoded = _collate(batch, tokenizer.pad_token_id, device)
                    output = model(**encoded)
                    validation_losses.append(float(output.loss.cpu()))
                    validation_weights.append(int((encoded["labels"][:, 1:] != -100).sum().item()))
            if parallel is None:
                val_loss = sum(validation_losses) / len(validation_losses)
            else:
                val_loss = sum(loss * weight for loss, weight in zip(validation_losses, validation_weights)) / max(sum(validation_weights), 1)
                val_loss = parallel.json_from_primary(val_loss)
            record["val_loss"] = val_loss
            record["perplexity"] = math.exp(min(val_loss, 80.0))
            performance.record["validation_seconds"] = time.perf_counter() - validation_started
            score = -val_loss
        elif task == "llm_dpo":
            accuracy = float(sum(extra_metrics) / max(len(extra_metrics), 1))
            if parallel is not None:
                correct, count = parallel.totals([float(sum(value * weight for value, weight in zip(extra_metrics, weights))), sum(weights)])
                accuracy = correct / max(count, 1)
            record["preference_accuracy"] = accuracy
            score = -epoch_loss
        else:
            ppo_metrics = [item for item in extra_metrics if isinstance(item, dict)]
            mean_reward = sum(item["reward"] for item in ppo_metrics) / max(len(ppo_metrics), 1)
            mean_kl = sum(item["kl"] for item in ppo_metrics) / max(len(ppo_metrics), 1)
            if parallel is not None:
                reward_sum, kl_sum, count = parallel.totals([
                    sum(item["reward"] * weight for item, weight in zip(ppo_metrics, weights)),
                    sum(item["kl"] * weight for item, weight in zip(ppo_metrics, weights)), sum(weights),
                ])
                mean_reward, mean_kl = reward_sum / max(count, 1), kl_sum / max(count, 1)
            record.update({"reward": mean_reward, "kl": mean_kl})
            score = mean_reward
        record["train_loss"] = round(float(record["train_loss"]), 6)
        if parallel is None or parallel.primary:
            _write_metric(job_dir, record)
        if best_score is None or score > float(best_score):
            best_score, best_epoch = float(score), epoch
            if parallel is None or parallel.primary:
                with performance.measure("checkpoint_save_seconds"):
                    _save_bundle(model, tokenizer, job_dir)
        if parallel is None or parallel.primary:
            with performance.measure("checkpoint_save_seconds"):
                state = _checkpoint_state(task, params, epoch, best_score, best_epoch, model, optimizer, value_head)
                _write_checkpoint(state, job_dir, epoch, None)
            if on_checkpoint is not None:
                with performance.measure("checkpoint_publish_seconds"):
                    on_checkpoint(epoch)
            performance.write(job_dir, samples_seen, steps, effective_tokens)
        if on_epoch_end is not None:
            on_epoch_end(record)
        log_fn(f"[epoch {epoch}/{epochs}] train_loss={record['train_loss']:.4f}")
        if parallel is not None:
            parallel.control(cancel_event, pause_event, allow_pause=True)
        if pause_event.is_set():
            from app.trainer import JobPaused
            raise JobPaused(f"LLM 训练已在 epoch {epoch} 后暂停")

    if parallel is not None and not parallel.primary:
        return {}
    if not record:
        metric_path = job_dir / "metrics.jsonl"
        if metric_path.exists():
            lines = [line for line in metric_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                record = json.loads(lines[-1])
        if not record:
            raise ValueError("checkpoint 已达到配置 epoch，但找不到可恢复的指标记录")

    summary = {
        "task": task,
        "device": str(device),
        "device_backend": backend_from_device(device),
        "epochs_total": epoch,
        "epochs_done": epoch,
        "resumed_from": start_epoch,
        "best_epoch": best_epoch,
        "best_score": best_score,
        "final": record,
        "rows": {"train": len(train_examples), "val": len(val_examples)},
        "model": params.get("base_model") or "random-init GPT-2 decoder-only",
        "training_mode": "full_parameter",
        "duration_sec": round(time.time() - started_at, 1),
    }
    if parallel is not None:
        summary["parallel"] = {"workers": parallel.world_size, "backend": parallel.backend,
                               "batch_sizes": parallel.sizes, "global_batch_size": sum(parallel.sizes)}
    (job_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    log_fn(f"LLM 训练完成：最佳 epoch {best_epoch}，模型包 best_model.zip 已保存")
    return summary


def generate_text(bundle_path: Path, prompt: str, *, max_new_tokens: int,
                  temperature: float, top_p: float, device: str | None = None) -> str:
    """从安全解压后的模型包加载 CausalLM 并对 prompt 生成续写。"""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    bundle_path = Path(bundle_path)
    with tempfile.TemporaryDirectory(prefix="llm-generate-", dir=bundle_path.parent) as temp_dir:
        extract_dir = Path(temp_dir)
        root = extract_dir.resolve()
        with zipfile.ZipFile(bundle_path) as archive:
            for item in archive.infolist():
                target = (extract_dir / item.filename).resolve()
                if target != root and root not in target.parents:
                    raise ValueError("模型压缩包包含非法路径")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        tokenizer = AutoTokenizer.from_pretrained(
            extract_dir, trust_remote_code=False, local_files_only=True
        )
        model = AutoModelForCausalLM.from_pretrained(
            extract_dir, trust_remote_code=False, local_files_only=True
        )
        # 指定设备必须原样执行；旧同步调用继续使用原有自动选择规则。
        device = torch.device(device if device is not None else ("cuda" if torch.cuda.is_available() else "cpu"))
        model.to(device).eval()
        inputs = tokenizer(prompt, return_tensors="pt").to(device)
        generation_params = {
            "max_new_tokens": int(max_new_tokens),
            "pad_token_id": tokenizer.pad_token_id,
            "eos_token_id": tokenizer.eos_token_id,
            "do_sample": float(temperature) > 0,
        }
        if float(temperature) > 0:
            generation_params.update({"temperature": float(temperature), "top_p": float(top_p)})
        with torch.no_grad():
            output = model.generate(**inputs, **generation_params)
        return tokenizer.decode(output[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
