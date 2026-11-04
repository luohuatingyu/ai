"""LLM 任务实现（预训练/SFT/DPO/PPO）"""
from __future__ import annotations

import json
import os
import torch
import torch.nn as nn
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from torch.utils.data import DataLoader, Dataset

from app.training.core import BaseTask, register_task, TrainingSpec, TrainingContext
from app.predictor import PICKLE_FORMAT


@dataclass
class LLMConfig:
    """LLM 模型配置"""
    vocab_size: int = 32000
    hidden_size: int = 768
    num_layers: int = 12
    num_heads: int = 12
    max_seq_len: int = 2048
    dropout: float = 0.1
    use_flash_attn: bool = True


class _LLMDataset(Dataset):
    """LLM 训练数据集"""
    
    def __init__(self, data_path: str, tokenizer, max_seq_len: int, 
                 text_column: str = "text", prompt_column: str = None,
                 completion_column: str = None, chosen_column: str = None,
                 rejected_column: str = None):
        self.data_path = data_path
        self.tokenizer = tokenizer
        self.max_seq_len = max_seq_len
        self.text_column = text_column
        self.prompt_column = prompt_column
        self.completion_column = completion_column
        self.chosen_column = chosen_column
        self.rejected_column = rejected_column
        self._load_data()
    
    def _load_data(self):
        import pandas as pd
        df = pd.read_csv(self.data_path)
        
        if self.text_column and self.text_column in df.columns:
            # 预训练/续写格式
            self.texts = df[self.text_column].astype(str).tolist()
            self.mode = "pretrain"
        elif self.prompt_column and self.completion_column:
            # SFT 格式
            self.prompts = df[self.prompt_column].astype(str).tolist()
            self.completions = df[self.completion_column].astype(str).tolist()
            self.mode = "sft"
        elif self.chosen_column and self.rejected_column:
            # DPO 格式
            self.prompts = df.get(self.prompt_column, df.get("prompt", [""] * len(df))).astype(str).tolist()
            self.chosen = df[self.chosen_column].astype(str).tolist()
            self.rejected = df[self.rejected_column].astype(str).tolist()
            self.mode = "dpo"
        else:
            raise ValueError("数据集格式不匹配：需要 text_column 或 prompt+completion 或 chosen+rejected")
    
    def __len__(self):
        if self.mode == "pretrain":
            return len(self.texts)
        return len(self.prompts)
    
    def __getitem__(self, idx):
        if self.mode == "pretrain":
            text = self.texts[idx]
            encoding = self.tokenizer(
                text, 
                max_length=self.max_seq_len,
                truncation=True,
                padding="max_length",
                return_tensors="pt"
            )
            input_ids = encoding["input_ids"].squeeze(0)
            attention_mask = encoding["attention_mask"].squeeze(0)
            labels = input_ids.clone()
            labels[attention_mask == 0] = -100
            return {"input_ids": input_ids, "attention_mask": attention_mask, "labels": labels}
        
        elif self.mode == "sft":
            prompt = self.prompts[idx]
            completion = self.completions[idx]
            full_text = prompt + completion
            encoding = self.tokenizer(
                full_text,
                max_length=self.max_seq_len,
                truncation=True,
                padding="max_length",
                return_tensors="pt"
            )
            input_ids = encoding["input_ids"].squeeze(0)
            attention_mask = encoding["attention_mask"].squeeze(0)
            
            # 计算 prompt 长度，仅对 completion 计算 loss
            prompt_encoding = self.tokenizer(prompt, add_special_tokens=False)
            prompt_len = len(prompt_encoding["input_ids"])
            labels = input_ids.clone()
            labels[:prompt_len] = -100
            labels[attention_mask == 0] = -100
            
            return {"input_ids": input_ids, "attention_mask": attention_mask, "labels": labels}
        
        elif self.mode == "dpo":
            prompt = self.prompts[idx]
            chosen = self.chosen[idx]
            rejected = self.rejected[idx]
            
            chosen_text = prompt + chosen
            rejected_text = prompt + rejected
            
            chosen_enc = self.tokenizer(chosen_text, max_length=self.max_seq_len, truncation=True, padding="max_length", return_tensors="pt")
            rejected_enc = self.tokenizer(rejected_text, max_length=self.max_seq_len, truncation=True, padding="max_length", return_tensors="pt")
            
            return {
                "chosen_input_ids": chosen_enc["input_ids"].squeeze(0),
                "chosen_attention_mask": chosen_enc["attention_mask"].squeeze(0),
                "rejected_input_ids": rejected_enc["input_ids"].squeeze(0),
                "rejected_attention_mask": rejected_enc["attention_mask"].squeeze(0),
            }


@register_task
class LLMTask(BaseTask):
    task_name = "llm"
    supported_models = ["llama", "qwen", "baichuan", "chatglm", "custom"]
    
    def __init__(self, spec: TrainingSpec, context: TrainingContext):
        super().__init__(spec, context)
        self.llm_config = self._parse_llm_config()
        self.tokenizer = None
        self.model = None
    
    def _parse_llm_config(self) -> LLMConfig:
        params = self.spec.params
        return LLMConfig(
            vocab_size=int(params.get("vocab_size", 32000)),
            hidden_size=int(params.get("model_hidden_size", 768)),
            num_layers=int(params.get("model_layers", 12)),
            num_heads=int(params.get("model_heads", 12)),
            max_seq_len=int(params.get("max_seq_len", 2048)),
            dropout=float(params.get("dropout", 0.1)),
        )
    
    def prepare_data(self) -> tuple:
        spec = self.spec
        context = self.context
        log = self.log
        params = spec.params
        
        # 加载 tokenizer
        from transformers import AutoTokenizer
        base_model = params.get("base_model", "hf-internal-testing/llama-tokenizer")
        self.tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        
        task_subtype = spec.task  # llm_pretraining/llm_sft/llm_dpo/llm_ppo
        
        dataset = _LLMDataset(
            context.dataset_path,
            self.tokenizer,
            self.llm_config.max_seq_len,
            text_column=params.get("text_column", "text"),
            prompt_column=params.get("prompt_column"),
            completion_column=params.get("completion_column"),
            chosen_column=params.get("chosen_column"),
            rejected_column=params.get("rejected_column"),
        )
        
        log(f"LLM 数据集加载: {len(dataset)} 样本, 模式={dataset.mode}, 任务={task_subtype}")
        
        # 简单分割
        n = len(dataset)
        split_idx = int(n * 0.95)
        train_indices = list(range(split_idx))
        val_indices = list(range(split_idx, n))
        
        train_subset = torch.utils.data.Subset(dataset, train_indices)
        val_subset = torch.utils.data.Subset(dataset, val_indices)
        
        batch_size = int(params.get("batch_size", 4))
        train_loader = DataLoader(train_subset, batch_size=batch_size, shuffle=(dataset.mode == "pretrain"))
        val_loader = DataLoader(val_subset, batch_size=batch_size, shuffle=False)
        
        preprocessor = {
            "tokenizer_name": base_model,
            "max_seq_len": self.llm_config.max_seq_len,
            "task_subtype": task_subtype,
        }
        
        metadata = {
            "rows": {"total": n, "train": len(train_indices), "val": len(val_indices)},
            "features": self.llm_config.vocab_size,
            "classes": None,
        }
        
        return train_loader, val_loader, preprocessor, metadata
    
    def build_model(self, preprocessor: Dict, metadata: Dict) -> torch.nn.Module:
        spec = self.spec
        params = spec.params
        task_subtype = spec.task
        
        # 使用 transformers 加载基础模型
        from transformers import AutoModelForCausalLm, AutoConfig
        base_model = params.get("base_model", "hf-internal-testing/tiny-random-LlamaForCausalLM")
        
        if task_subtype == "llm_sft" or task_subtype == "llm_dpo":
            # SFT/DPO: 加载预训练模型，可能加载 LoRA
            model = AutoModelForCausalLM.from_pretrained(
                base_model,
                torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
                trust_remote_code=True,
            )
            
            # LoRA 配置
            lora_rank = int(params.get("lora_rank", 16))
            lora_alpha = int(params.get("lora_alpha", 32))
            if lora_rank > 0:
                from peft import LoraConfig, get_peft_model
                lora_config = LoraConfig(
                    r=lora_rank,
                    lora_alpha=lora_alpha,
                    target_modules=params.get("lora_target_modules", ["q_proj", "v_proj", "k_proj", "o_proj"]),
                    lora_dropout=float(params.get("lora_dropout", 0.1)),
                    bias="none",
                    task_type="CAUSAL_LM",
                )
                model = get_peft_model(model, lora_config)
                model.print_trainable_parameters()
        
        elif task_subtype == "llm_pretraining":
            # 预训练：从头初始化或从小模型开始
            config = AutoConfig.from_pretrained(base_model, trust_remote_code=True)
            config.vocab_size = self.llm_config.vocab_size
            config.hidden_size = self.llm_config.hidden_size
            config.num_hidden_layers = self.llm_config.num_layers
            config.num_attention_heads = self.llm_config.num_heads
            config.max_position_embeddings = self.llm_config.max_seq_len
            model = AutoModelForCausalLM.from_config(config, trust_remote_code=True)
        
        elif task_subtype == "llm_ppo":
            # PPO: 需要 policy model, reward model, value model
            raise NotImplementedError("PPO 训练暂未实现")
        
        self.model = model
        return model.to(self.context.device)
    
    def build_optimizer(self, model: torch.nn.Module) -> torch.optim.Optimizer:
        params = self.spec.params
        lr = float(params.get("lr", 2e-5))
        weight_decay = float(params.get("weight_decay", 0.01))
        
        # 分组参数：decay 和 no_decay
        decay_params = []
        no_decay_params = []
        for n, p in model.named_parameters():
            if not p.requires_grad:
                continue
            if any(nd in n for nd in ["bias", "LayerNorm", "layernorm", "norm"]):
                no_decay_params.append(p)
            else:
                decay_params.append(p)
        
        optimizer_grouped_parameters = [
            {"params": decay_params, "weight_decay": weight_decay},
            {"params": no_decay_params, "weight_decay": 0.0},
        ]
        
        return torch.optim.AdamW(optimizer_grouped_parameters, lr=lr, betas=(0.9, 0.95), eps=1e-8)
    
    def build_scheduler(self, optimizer: torch.optim.Optimizer):
        params = self.spec.params
        epochs = int(params.get("epochs", 3))
        scheduler_name = str(params.get("scheduler", "cosine"))
        
        if scheduler_name == "cosine":
            from transformers import get_cosine_schedule_with_warmup
            num_training_steps = epochs * 100  # 估算
            num_warmup_steps = int(num_training_steps * 0.03)
            return get_cosine_schedule_with_warmup(optimizer, num_warmup_steps, num_training_steps)
        
        return None
    
    def train_step(self, model: torch.nn.Module, batch, optimizer) -> Dict[str, float]:
        device = self.context.device
        task_subtype = self.spec.task
        
        if task_subtype == "llm_pretraining" or task_subtype == "llm_sft":
            return self._train_step_causal_lm(model, batch, optimizer, device)
        elif task_subtype == "llm_dpo":
            return self._train_step_dpo(model, batch, optimizer, device)
        
        return {"train_loss": 0.0}
    
    def _train_step_causal_lm(self, model, batch, optimizer, device) -> Dict[str, float]:
        input_ids = batch["input_ids"].to(device)
        attention_mask = batch["attention_mask"].to(device)
        labels = batch["labels"].to(device)
        
        optimizer.zero_grad()
        outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
        loss = outputs.loss
        loss.backward()
        
        # 梯度裁剪
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        
        return {"train_loss": loss.item(), "train_perplexity": torch.exp(loss).item()}
    
    def _train_step_dpo(self, model, batch, optimizer, device) -> Dict[str, float]:
        # DPO loss 实现
        beta = float(self.spec.params.get("beta", 0.1))
        
        chosen_input_ids = batch["chosen_input_ids"].to(device)
        chosen_attention_mask = batch["chosen_attention_mask"].to(device)
        rejected_input_ids = batch["rejected_input_ids"].to(device)
        rejected_attention_mask = batch["rejected_attention_mask"].to(device)
        
        optimizer.zero_grad()
        
        # 计算 chosen log probs
        chosen_outputs = model(input_ids=chosen_input_ids, attention_mask=chosen_attention_mask)
        chosen_logits = chosen_outputs.logits[:, :-1, :]
        chosen_labels = chosen_input_ids[:, 1:]
        chosen_log_probs = self._get_log_probs(chosen_logits, chosen_labels)
        
        # 计算 rejected log probs
        rejected_outputs = model(input_ids=rejected_input_ids, attention_mask=rejected_attention_mask)
        rejected_logits = rejected_outputs.logits[:, :-1, :]
        rejected_labels = rejected_input_ids[:, 1:]
        rejected_log_probs = self._get_log_probs(rejected_logits, rejected_labels)
        
        # DPO loss
        logits_diff = chosen_log_probs - rejected_log_probs
        loss = -nn.functional.logsigmoid(beta * logits_diff).mean()
        
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        
        return {"train_loss": loss.item(), "train_chosen_reward": chosen_log_probs.mean().item(), "train_rejected_reward": rejected_log_probs.mean().item()}
    
    def _get_log_probs(self, logits, labels):
        log_probs = nn.functional.log_softmax(logits, dim=-1)
        return log_probs.gather(dim=-1, index=labels.unsqueeze(-1)).squeeze(-1).sum(dim=-1)
    
    def validate(self, model: torch.nn.Module, val_loader: DataLoader) -> Dict[str, float]:
        device = self.context.device
        task_subtype = self.spec.task
        model.eval()
        
        total_loss = 0.0
        total_tokens = 0
        
        with torch.no_grad():
            for batch in val_loader:
                if task_subtype in ("llm_pretraining", "llm_sft"):
                    input_ids = batch["input_ids"].to(device)
                    attention_mask = batch["attention_mask"].to(device)
                    labels = batch["labels"].to(device)
                    
                    outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                    loss = outputs.loss
                    n_tokens = (labels != -100).sum().item()
                    
                    total_loss += loss.item() * n_tokens
                    total_tokens += n_tokens
                
                elif task_subtype == "llm_dpo":
                    chosen_input_ids = batch["chosen_input_ids"].to(device)
                    chosen_attention_mask = batch["chosen_attention_mask"].to(device)
                    rejected_input_ids = batch["rejected_input_ids"].to(device)
                    rejected_attention_mask = batch["rejected_attention_mask"].to(device)
                    
                    beta = float(self.spec.params.get("beta", 0.1))
                    
                    chosen_outputs = model(input_ids=chosen_input_ids, attention_mask=chosen_attention_mask)
                    chosen_logits = chosen_outputs.logits[:, :-1, :]
                    chosen_labels = chosen_input_ids[:, 1:]
                    chosen_log_probs = self._get_log_probs(chosen_logits, chosen_labels)
                    
                    rejected_outputs = model(input_ids=rejected_input_ids, attention_mask=rejected_attention_mask)
                    rejected_logits = rejected_outputs.logits[:, :-1, :]
                    rejected_labels = rejected_input_ids[:, 1:]
                    rejected_log_probs = self._get_log_probs(rejected_logits, rejected_labels)
                    
                    logits_diff = chosen_log_probs - rejected_log_probs
                    loss = -nn.functional.logsigmoid(beta * logits_diff).mean()
                    
                    total_loss += loss.item() * chosen_input_ids.size(0)
                    total_tokens += chosen_input_ids.size(0)
        
        avg_loss = total_loss / max(total_tokens, 1)
        perplexity = torch.exp(torch.tensor(avg_loss)).item()
        
        return {"val_loss": avg_loss, "val_perplexity": perplexity}
    
    def save_model(self, model: torch.nn.Module, preprocessor: Dict, path: Path, is_best: bool = False):
        # 保存为 HuggingFace 格式
        save_dir = path.parent / (path.stem + "_hf")
        save_dir.mkdir(parents=True, exist_ok=True)
        
        if hasattr(model, "module"):
            model_to_save = model.module
        else:
            model_to_save = model
        
        model_to_save.save_pretrained(save_dir)
        if self.tokenizer:
            self.tokenizer.save_pretrained(save_dir)
        
        # 同时保存为 .pt 格式兼容现有预测流程
        import tempfile
        import shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            model_to_save.save_pretrained(tmpdir)
            if self.tokenizer:
                self.tokenizer.save_pretrained(tmpdir)
            shutil.make_archive(str(path.with_suffix("")), 'zip', tmpdir)
        
        # 预处理器
        preprocessor.update({
            "model_path": str(save_dir),
            "tokenizer_path": str(save_dir),
        })
    
    def load_model(self, path: Path) -> tuple:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        model_dir = path.parent / (path.stem + "_hf")
        model = AutoModelForCausalLM.from_pretrained(model_dir, trust_remote_code=True)
        tokenizer = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
        return model, {"tokenizer": tokenizer, "model_dir": str(model_dir)}, {}