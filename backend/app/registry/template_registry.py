"""脚本模板注册表：内置模板、用户模板、实例化"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import app.config as config
from app.db_utils import (
    create_script_template,
    get_script_template,
    list_script_templates,
    update_script_template,
    delete_script_template,
    record_template_instantiation,
)
from app.database import db_session


@dataclass
class TemplateParameter:
    """模板参数定义"""
    name: str
    type: str  # string/number/integer/boolean/select/dataset_column
    label: str
    description: str = ""
    default: Any = None
    required: bool = True
    options: List[str] = field(default_factory=list)  # select 类型的选项
    validation: Dict = field(default_factory=dict)  # 验证规则


@dataclass
class ScriptTemplate:
    """脚本模板"""
    id: str
    name: str
    description: str
    category: str  # classification/regression/clustering/time_series/llm_pretrain/llm_sft/llm_dpo/custom
    task_type: str
    content: str  # Jinja2 模板
    parameters: List[TemplateParameter]
    dataset_requirements: Dict[str, Any] = field(default_factory=dict)
    is_builtin: bool = False
    created_by: Optional[str] = None
    created_at: float = 0
    updated_at: float = 0
    
    def render(self, params: Dict[str, Any]) -> str:
        """渲染模板"""
        from jinja2 import Template, Environment, BaseLoader
        env = Environment(loader=BaseLoader(), autoescape=False)
        template = env.from_string(self.content)
        return template.render(**params)
    
    def validate_params(self, params: Dict[str, Any]) -> List[str]:
        """验证参数并就地补齐默认值，返回错误列表。

        有默认值的参数未提供时自动填充默认值；仅"必填且无默认"的
        参数缺失才报错，保证模板可用部分参数快速实例化。
        """
        errors = []
        for param in self.parameters:
            if param.name not in params:
                if param.default is not None:
                    params[param.name] = param.default
                    continue
                if param.required:
                    errors.append(f"缺少必填参数: {param.name}")
                continue
            value = params[param.name]
            # 类型检查
            if param.type == "integer" and not isinstance(value, int):
                try:
                    params[param.name] = int(value)
                except (ValueError, TypeError):
                    errors.append(f"参数 {param.name} 必须是整数")
            elif param.type == "number" and not isinstance(value, (int, float)):
                try:
                    params[param.name] = float(value)
                except (ValueError, TypeError):
                    errors.append(f"参数 {param.name} 必须是数字")
            elif param.type == "boolean" and not isinstance(value, bool):
                if isinstance(value, str):
                    params[param.name] = value.lower() in ("true", "1", "yes")
                else:
                    errors.append(f"参数 {param.name} 必须是布尔值")
            elif param.type == "select" and param.options:
                if value not in param.options:
                    errors.append(f"参数 {param.name} 值必须是以下之一: {param.options}")
        return errors


class TemplateRegistry:
    """模板注册表"""
    
    def __init__(self):
        self._builtin_templates: Dict[str, ScriptTemplate] = {}
        self._load_builtin_templates()
    
    def _load_builtin_templates(self):
        """加载内置模板"""
        # 分类模板
        self._builtin_templates["builtin-classification"] = ScriptTemplate(
            id="builtin-classification",
            name="分类任务基础模板",
            description="支持 MLP/XGBoost/RF/SVM 等多种模型的分类训练",
            category="classification",
            task_type="classification",
            content=self._get_classification_template(),
            parameters=[
                TemplateParameter("dataset_id", "string", "数据集 ID", "训练数据集标识", required=True),
                TemplateParameter("target", "dataset_column", "目标列", "分类标签列", required=True),
                TemplateParameter("model_type", "select", "模型类型", "选择算法", default="mlp",
                    options=["mlp", "logistic_regression", "decision_tree", "random_forest", 
                            "gradient_boosting", "svm", "knn", "xgboost"]),
                TemplateParameter("epochs", "integer", "训练轮数", default=20, validation={"min": 1, "max": 500}),
                TemplateParameter("hidden_size", "integer", "隐藏层大小", default=128, validation={"min": 32, "max": 2048}),
                TemplateParameter("hidden_layers", "integer", "隐藏层数", default=2, validation={"min": 1, "max": 10}),
                TemplateParameter("lr", "number", "学习率", default=1e-3, validation={"min": 1e-6, "max": 1}),
                TemplateParameter("batch_size", "integer", "批大小", default=256, validation={"min": 16, "max": 8192}),
                TemplateParameter("dropout", "number", "Dropout 率", default=0.15, validation={"min": 0, "max": 0.5}),
                TemplateParameter("optimizer", "select", "优化器", default="adamw",
                    options=["adamw", "adam", "sgd", "rmsprop"]),
                TemplateParameter("scheduler", "select", "学习率调度", default="none",
                    options=["none", "step", "cosine"]),
                TemplateParameter("seed", "integer", "随机种子", default=42),
            ],
            dataset_requirements={"task": "classification", "min_rows": 10, "target_type": "categorical"},
            is_builtin=True,
        )
        
        # 回归模板
        self._builtin_templates["builtin-regression"] = ScriptTemplate(
            id="builtin-regression",
            name="回归任务基础模板",
            description="支持 MLP/线性回归/RF/XGBoost 等回归训练",
            category="regression",
            task_type="regression",
            content=self._get_regression_template(),
            parameters=[
                TemplateParameter("dataset_id", "string", "数据集 ID", required=True),
                TemplateParameter("target", "dataset_column", "目标列", "回归目标列", required=True),
                TemplateParameter("model_type", "select", "模型类型", default="mlp",
                    options=["mlp", "linear_regression", "decision_tree", "random_forest",
                            "gradient_boosting", "svm", "knn", "xgboost"]),
                TemplateParameter("epochs", "integer", "训练轮数", default=20),
                TemplateParameter("hidden_size", "integer", "隐藏层大小", default=128),
                TemplateParameter("hidden_layers", "integer", "隐藏层数", default=2),
                TemplateParameter("lr", "number", "学习率", default=1e-3),
                TemplateParameter("batch_size", "integer", "批大小", default=256),
                TemplateParameter("dropout", "number", "Dropout 率", default=0.15),
                TemplateParameter("optimizer", "select", "优化器", default="adamw",
                    options=["adamw", "adam", "sgd", "rmsprop"]),
                TemplateParameter("scheduler", "select", "学习率调度", default="none",
                    options=["none", "step", "cosine"]),
                TemplateParameter("seed", "integer", "随机种子", default=42),
            ],
            dataset_requirements={"task": "regression", "min_rows": 10, "target_type": "numeric"},
            is_builtin=True,
        )
        
        # 聚类模板
        self._builtin_templates["builtin-clustering"] = ScriptTemplate(
            id="builtin-clustering",
            name="K-Means 聚类模板",
            description="K-Means 聚类训练，支持自动确定聚类数",
            category="clustering",
            task_type="clustering",
            content=self._get_clustering_template(),
            parameters=[
                TemplateParameter("dataset_id", "string", "数据集 ID", required=True),
                TemplateParameter("n_clusters", "integer", "聚类数", default=4, validation={"min": 2, "max": 64}),
                TemplateParameter("max_iter", "integer", "最大迭代次数", default=100, validation={"min": 10, "max": 500}),
                TemplateParameter("seed", "integer", "随机种子", default=42),
            ],
            dataset_requirements={"task": "clustering", "min_rows": 2},
            is_builtin=True,
        )
        
        # 时序预测模板
        self._builtin_templates["builtin-time_series"] = ScriptTemplate(
            id="builtin-time_series",
            name="时序预测模板",
            description="单变量时序预测，支持 MLP/LSTM/GRU",
            category="time_series",
            task_type="time_series_forecasting",
            content=self._get_time_series_template(),
            parameters=[
                TemplateParameter("dataset_id", "string", "数据集 ID", required=True),
                TemplateParameter("target", "dataset_column", "目标列", required=True),
                TemplateParameter("time_column", "dataset_column", "时间列", required=True),
                TemplateParameter("lookback", "integer", "回看窗口", default=24, validation={"min": 2, "max": 168}),
                TemplateParameter("forecast_horizon", "integer", "预测步数", default=1, validation={"min": 1, "max": 24}),
                TemplateParameter("model_type", "select", "模型类型", default="mlp",
                    options=["mlp", "lstm", "gru"]),
                TemplateParameter("epochs", "integer", "训练轮数", default=50),
                TemplateParameter("hidden_size", "integer", "隐藏层大小", default=128),
                TemplateParameter("lr", "number", "学习率", default=1e-3),
                TemplateParameter("seed", "integer", "随机种子", default=42),
            ],
            dataset_requirements={"task": "time_series", "min_rows": 50, "has_time_column": True},
            is_builtin=True,
        )
        
        # LLM SFT 模板
        self._builtin_templates["builtin-llm_sft"] = ScriptTemplate(
            id="builtin-llm_sft",
            name="LLM 监督微调 (SFT) 模板",
            description="基于 LoRA 的 LLM 监督微调，支持 Qwen/Llama/Baichuan 等",
            category="llm_sft",
            task_type="llm_sft",
            content=self._get_llm_sft_template(),
            parameters=[
                TemplateParameter("dataset_id", "string", "数据集 ID", required=True),
                TemplateParameter("base_model", "string", "基础模型", default="Qwen/Qwen2-7B-Instruct"),
                TemplateParameter("text_column", "dataset_column", "文本列", required=True),
                TemplateParameter("max_seq_len", "integer", "最大序列长度", default=2048, validation={"min": 512, "max": 8192}),
                TemplateParameter("lora_rank", "integer", "LoRA 秩", default=16, validation={"min": 4, "max": 128}),
                TemplateParameter("lora_alpha", "integer", "LoRA Alpha", default=32),
                TemplateParameter("lora_dropout", "number", "LoRA Dropout", default=0.1),
                TemplateParameter("epochs", "integer", "训练轮数", default=3, validation={"min": 1, "max": 10}),
                TemplateParameter("batch_size", "integer", "批大小", default=4, validation={"min": 1, "max": 32}),
                TemplateParameter("lr", "number", "学习率", default=2e-5),
                TemplateParameter("seed", "integer", "随机种子", default=42),
            ],
            dataset_requirements={"task": "llm_sft", "min_rows": 100, "has_text_column": True},
            is_builtin=True,
        )
        
        # LLM DPO 模板
        self._builtin_templates["builtin-llm_dpo"] = ScriptTemplate(
            id="builtin-llm_dpo",
            name="LLM 偏好对齐 (DPO) 模板",
            description="Direct Preference Optimization 偏好对齐训练",
            category="llm_dpo",
            task_type="llm_dpo",
            content=self._get_llm_dpo_template(),
            parameters=[
                TemplateParameter("dataset_id", "string", "数据集 ID", required=True),
                TemplateParameter("base_model", "string", "基础模型", default="Qwen/Qwen2-7B-Instruct"),
                TemplateParameter("prompt_column", "dataset_column", "Prompt 列", required=True),
                TemplateParameter("chosen_column", "dataset_column", "Chosen 列", required=True),
                TemplateParameter("rejected_column", "dataset_column", "Rejected 列", required=True),
                TemplateParameter("max_seq_len", "integer", "最大序列长度", default=2048),
                TemplateParameter("lora_rank", "integer", "LoRA 秩", default=16),
                TemplateParameter("beta", "number", "DPO Beta", default=0.1),
                TemplateParameter("epochs", "integer", "训练轮数", default=3),
                TemplateParameter("batch_size", "integer", "批大小", default=4),
                TemplateParameter("lr", "number", "学习率", default=5e-6),
                TemplateParameter("seed", "integer", "随机种子", default=42),
            ],
            dataset_requirements={"task": "llm_dpo", "min_rows": 100, "has_preference_columns": True},
            is_builtin=True,
        )
    
    def get_builtin_templates(self) -> List[ScriptTemplate]:
        return list(self._builtin_templates.values())
    
    def get_template(self, template_id: str) -> Optional[ScriptTemplate]:
        if template_id in self._builtin_templates:
            return self._builtin_templates[template_id]
        
        with db_session() as db:
            return self._db_to_template(get_script_template(db, template_id))
    
    def list_templates(
        self, 
        limit: int = 50, 
        offset: int = 0,
        category: str = None,
        is_builtin: bool = None,
    ) -> tuple[List[ScriptTemplate], int]:
        templates = list(self._builtin_templates.values())
        
        if category:
            templates = [t for t in templates if t.category == category]
        if is_builtin is not None:
            templates = [t for t in templates if t.is_builtin == is_builtin]
        
        with db_session() as db:
            db_templates, db_total = list_script_templates(db, limit, offset, category, is_builtin)
            templates.extend([self._db_to_template(t) for t in db_templates])
        
        total = len(templates)
        templates = templates[offset:offset + limit]
        return templates, total
    
    def create_template(
        self,
        name: str,
        category: str,
        task_type: str,
        content: str,
        parameters: List[Dict],
        description: str = "",
        dataset_requirements: Dict = None,
        created_by: str = None,
    ) -> ScriptTemplate:
        with db_session() as db:
            template = create_script_template(
                db=db,
                name=name,
                category=category,
                task_type=task_type,
                content=content,
                parameters=parameters,
                description=description,
                dataset_requirements=dataset_requirements,
                is_builtin=False,
                created_by=created_by,
            )
        return self._db_to_template(template)
    
    def update_template(self, template_id: str, **updates) -> Optional[ScriptTemplate]:
        if template_id in self._builtin_templates:
            raise ValueError("内置模板不可修改")
        
        with db_session() as db:
            template = update_script_template(db, template_id, **updates)
            return self._db_to_template(template) if template else None
    
    def delete_template(self, template_id: str) -> bool:
        if template_id in self._builtin_templates:
            raise ValueError("内置模板不可删除")
        
        with db_session() as db:
            return delete_script_template(db, template_id)
    
    def instantiate(
        self,
        template_id: str,
        params: Dict[str, Any],
        instantiated_type: str = "training_job",
        created_by: str = None,
    ) -> Dict[str, Any]:
        """实例化模板为可执行配置"""
        template = self.get_template(template_id)
        if not template:
            raise ValueError(f"模板不存在: {template_id}")
        
        # 验证参数
        errors = template.validate_params(params)
        if errors:
            raise ValueError(f"参数验证失败: {', '.join(errors)}")
        
        # 渲染模板
        rendered = template.render(params)
        
        # 记录实例化
        with db_session() as db:
            record_template_instantiation(
                db=db,
                template_id=template_id,
                instantiated_type=instantiated_type,
                instantiated_id=f"{instantiated_type}-{uuid.uuid4().hex[:8]}",
                parameters=params,
                created_by=created_by,
            )
        
        return {
            "template_id": template_id,
            "rendered_content": rendered,
            "parameters": params,
            "instantiated_type": instantiated_type,
        }
    
    def _db_to_template(self, db_template) -> Optional[ScriptTemplate]:
        if not db_template:
            return None
        return ScriptTemplate(
            id=db_template.id,
            name=db_template.name,
            description=db_template.description or "",
            category=db_template.category,
            task_type=db_template.task_type,
            content=db_template.content,
            parameters=[TemplateParameter(**p) for p in (db_template.parameters or [])],
            dataset_requirements=db_template.dataset_requirements or {},
            is_builtin=db_template.is_builtin,
            created_by=db_template.created_by,
            created_at=db_template.created_at.timestamp() if db_template.created_at else 0,
            updated_at=db_template.updated_at.timestamp() if db_template.updated_at else 0,
        )
    
    # ========== 内置模板内容 ==========
    
    def _get_classification_template(self) -> str:
        return '''# 分类任务训练脚本
# 数据集: {{dataset_id}} (目标列: {{target}})
# 模型: {{model_type}}

import pandas as pd
import torch
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.storage import get_store, resolve_dataset_for_worker
from app.config import WORKER_SCRATCH_DIR

# 1. 准备数据
dataset_path = resolve_dataset_for_worker("{{dataset_id}}", WORKER_SCRATCH_DIR / "{{dataset_id}}")

# 2. 训练配置
spec = TrainingSpec(
    task="classification",
    target="{{target}}",
    dataset_id="{{dataset_id}}",
    params={
        "model_type": "{{model_type}}",
        "epochs": {{epochs}},
        "hidden_size": {{hidden_size}},
        "hidden_layers": {{hidden_layers}},
        "lr": {{lr}},
        "batch_size": {{batch_size}},
        "dropout": {{dropout}},
        "optimizer": "{{optimizer}}",
        "scheduler": "{{scheduler}}",
        "seed": {{seed}},
    },
)

# 3. 执行训练
context = TrainingContext(
    job_id="job-{{dataset_id}}-{{ts}}",
    job_dir=WORKER_SCRATCH_DIR / "job-{{dataset_id}}-{{ts}}",
    store=get_store(),
    dataset_path=dataset_path,
    device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
)

summary = run_training(spec, context)
print("训练完成:", summary)
'''
    
    def _get_regression_template(self) -> str:
        return '''# 回归任务训练脚本
# 数据集: {{dataset_id}} (目标列: {{target}})
# 模型: {{model_type}}

import pandas as pd
import torch
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.storage import get_store, resolve_dataset_for_worker
from app.config import WORKER_SCRATCH_DIR

dataset_path = resolve_dataset_for_worker("{{dataset_id}}", WORKER_SCRATCH_DIR / "{{dataset_id}}")

spec = TrainingSpec(
    task="regression",
    target="{{target}}",
    dataset_id="{{dataset_id}}",
    params={
        "model_type": "{{model_type}}",
        "epochs": {{epochs}},
        "hidden_size": {{hidden_size}},
        "hidden_layers": {{hidden_layers}},
        "lr": {{lr}},
        "batch_size": {{batch_size}},
        "dropout": {{dropout}},
        "optimizer": "{{optimizer}}",
        "scheduler": "{{scheduler}}",
        "seed": {{seed}},
    },
)

context = TrainingContext(
    job_id="job-{{dataset_id}}-{{ts}}",
    job_dir=WORKER_SCRATCH_DIR / "job-{{dataset_id}}-{{ts}}",
    store=get_store(),
    dataset_path=dataset_path,
    device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
)

summary = run_training(spec, context)
print("训练完成:", summary)
'''
    
    def _get_clustering_template(self) -> str:
        return '''# K-Means 聚类训练脚本
# 数据集: {{dataset_id}}
# 聚类数: {{n_clusters}}

import pandas as pd
import torch
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.storage import get_store, resolve_dataset_for_worker
from app.config import WORKER_SCRATCH_DIR

dataset_path = resolve_dataset_for_worker("{{dataset_id}}", WORKER_SCRATCH_DIR / "{{dataset_id}}")

spec = TrainingSpec(
    task="clustering",
    dataset_id="{{dataset_id}}",
    params={
        "n_clusters": {{n_clusters}},
        "epochs": {{max_iter}},
        "seed": {{seed}},
    },
)

context = TrainingContext(
    job_id="job-{{dataset_id}}-{{ts}}",
    job_dir=WORKER_SCRATCH_DIR / "job-{{dataset_id}}-{{ts}}",
    store=get_store(),
    dataset_path=dataset_path,
    device=torch.device("cpu"),  # KMeans 使用 CPU
)

summary = run_training(spec, context)
print("聚类完成:", summary)
'''
    
    def _get_time_series_template(self) -> str:
        return '''# 时序预测训练脚本
# 数据集: {{dataset_id}} (目标: {{target}}, 时间: {{time_column}})
# 回看: {{lookback}}, 预测: {{forecast_horizon}}

import pandas as pd
import torch
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.storage import get_store, resolve_dataset_for_worker
from app.config import WORKER_SCRATCH_DIR

dataset_path = resolve_dataset_for_worker("{{dataset_id}}", WORKER_SCRATCH_DIR / "{{dataset_id}}")

spec = TrainingSpec(
    task="time_series_forecasting",
    target="{{target}}",
    dataset_id="{{dataset_id}}",
    params={
        "time_column": "{{time_column}}",
        "lookback": {{lookback}},
        "forecast_horizon": {{forecast_horizon}},
        "model_type": "{{model_type}}",
        "epochs": {{epochs}},
        "hidden_size": {{hidden_size}},
        "lr": {{lr}},
        "seed": {{seed}},
    },
)

context = TrainingContext(
    job_id="job-{{dataset_id}}-{{ts}}",
    job_dir=WORKER_SCRATCH_DIR / "job-{{dataset_id}}-{{ts}}",
    store=get_store(),
    dataset_path=dataset_path,
    device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
)

summary = run_training(spec, context)
print("时序预测训练完成:", summary)
'''
    
    def _get_llm_sft_template(self) -> str:
        return '''# LLM 监督微调 (SFT) 训练脚本
# 基础模型: {{base_model}}
# 数据集: {{dataset_id}} (文本列: {{text_column}})

import torch
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.storage import get_store, resolve_dataset_for_worker
from app.config import WORKER_SCRATCH_DIR

dataset_path = resolve_dataset_for_worker("{{dataset_id}}", WORKER_SCRATCH_DIR / "{{dataset_id}}")

spec = TrainingSpec(
    task="llm_sft",
    dataset_id="{{dataset_id}}",
    params={
        "base_model": "{{base_model}}",
        "text_column": "{{text_column}}",
        "max_seq_len": {{max_seq_len}},
        "lora_rank": {{lora_rank}},
        "lora_alpha": {{lora_alpha}},
        "lora_dropout": {{lora_dropout}},
        "epochs": {{epochs}},
        "batch_size": {{batch_size}},
        "lr": {{lr}},
        "seed": {{seed}},
    },
)

context = TrainingContext(
    job_id="job-{{dataset_id}}-{{ts}}",
    job_dir=WORKER_SCRATCH_DIR / "job-{{dataset_id}}-{{ts}}",
    store=get_store(),
    dataset_path=dataset_path,
    device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
)

summary = run_training(spec, context)
print("SFT 微调完成:", summary)
'''
    
    def _get_llm_dpo_template(self) -> str:
        return '''# LLM 偏好对齐 (DPO) 训练脚本
# 基础模型: {{base_model}}
# 数据集: {{dataset_id}} (Prompt: {{prompt_column}}, Chosen: {{chosen_column}}, Rejected: {{rejected_column}})

import torch
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.storage import get_store, resolve_dataset_for_worker
from app.config import WORKER_SCRATCH_DIR

dataset_path = resolve_dataset_for_worker("{{dataset_id}}", WORKER_SCRATCH_DIR / "{{dataset_id}}")

spec = TrainingSpec(
    task="llm_dpo",
    dataset_id="{{dataset_id}}",
    params={
        "base_model": "{{base_model}}",
        "prompt_column": "{{prompt_column}}",
        "chosen_column": "{{chosen_column}}",
        "rejected_column": "{{rejected_column}}",
        "max_seq_len": {{max_seq_len}},
        "lora_rank": {{lora_rank}},
        "beta": {{beta}},
        "epochs": {{epochs}},
        "batch_size": {{batch_size}},
        "lr": {{lr}},
        "seed": {{seed}},
    },
)

context = TrainingContext(
    job_id="job-{{dataset_id}}-{{ts}}",
    job_dir=WORKER_SCRATCH_DIR / "job-{{dataset_id}}-{{ts}}",
    store=get_store(),
    dataset_path=dataset_path,
    device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
)

summary = run_training(spec, context)
print("DPO 对齐完成:", summary)
'''