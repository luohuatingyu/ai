"""训练完成后的模型自动注册：配置开关 + 失败不阻断训练。

单一入口供两条执行路径复用（app.worker 执行槽 / compute.worker_host），
保证"训练产物自动上传 OSS + 注册表可见"语义一致。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

import app.config as config
from app.storage import get_store


def try_auto_register(
    job_id: str,
    summary: Any,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Optional[Dict[str, Any]]:
    """训练完成后自动注册模型到注册表。

    - 受 config.MODEL_AUTO_REGISTER 开关控制（默认开启）；
    - summary 为 dict（旧 worker 路径）或 TrainingSummary（新路径）均可；
    - 注册失败抛出的异常由调用方兜底（不阻断训练收尾）；
    - 返回 {id, name, version}；开关关闭或摘要缺失时返回 None。
    """
    if not config.MODEL_AUTO_REGISTER:
        return None

    if hasattr(summary, "to_dict"):
        summary_dict = summary.to_dict()
    elif isinstance(summary, dict):
        summary_dict = summary
    else:
        # 兜底：从状态平面读取 summary.json（旧 worker 写在 jobs/{id}/ 下）
        store = get_store()
        raw = (
            store.read_text(f"jobs/{job_id}/summary.json")
            or store.read_text(f"{config.MODEL_REGISTRY_OSS_PREFIX}{job_id}/summary.json")
        )
        summary_dict = json.loads(raw) if raw else {}

    if not summary_dict:
        if log_fn:
            log_fn("模型自动注册跳过: 训练摘要缺失")
        return None

    # 旧 worker 路径的 summary 缺少产物路径字段：按 job 约定补齐
    task = summary_dict.get("task", "")
    is_llm = str(task).startswith("llm_")
    best_artifact = "best_model.zip" if is_llm else "best.pt"
    summary_dict.setdefault("model_path", f"jobs/{job_id}/{best_artifact}")
    summary_dict.setdefault("preprocessor_path", f"jobs/{job_id}/preprocessor.json")
    summary_dict.setdefault("summary_path", f"jobs/{job_id}/summary.json")

    from app.registry.model_registry import ModelRegistry

    registry = ModelRegistry()
    model = registry.register_from_training(
        job_id=job_id,
        summary=summary_dict,
        name=summary_dict.get("model_name") or f"{summary_dict.get('task', 'model')}-{job_id}",
        tags=["auto"],
    )
    return {"id": model.id, "name": model.name, "version": model.version}