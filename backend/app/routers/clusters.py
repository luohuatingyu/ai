"""计算集群/Worker 管理 API：资源查看、选择、监控"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.coordination import LeaseStore
from app.compute.resource_selector import ResourceSelector, get_available_resources
from app.state import manager

router = APIRouter(prefix="/api/clusters", tags=["clusters"])


@router.get("/workers")
def list_workers() -> dict:
    """列出所有计算 Worker（卡片级）"""
    workers = []
    for item in manager.lease.list_workers():
        info = dict(item)
        info["machine_id"] = info.get("machine_id") or str(info.get("worker_id", "")).rsplit("-", 1)[0] or None
        gpu = info.get("gpu") or {}
        info["device_label"] = f"GPU: {gpu.get('name')}" if gpu.get("available") else "CPU"
        
        state = "idle"
        current_job = None
        epoch = None
        if not info.get("active"):
            state = "offline"
        else:
            state = str(info.get("state") or "idle")
            current_job = info.get("current_job") or None
            epoch = info.get("epoch") if info.get("epoch") is not None else None
        
        info["state"] = state
        info["current_job"] = current_job
        info["epoch"] = epoch
        info["occupied"] = manager.lease.peek_holder("slot:" + str(info.get("worker_id", ""))) is not None
        
        if info.get("active") and info["occupied"] and state in {"idle", "disabled"}:
            info["state"] = "reserved"
        
        info["managed"] = bool(info.get("managed", False))
        info["enabled"] = manager.lease.is_worker_enabled(
            str(info.get("worker_id", "")), default=not info["managed"]
        )
        
        import time as _time
        info["heartbeat_age_sec"] = (
            round(_time.time() - float(info.get("last_seen", _time.time())), 1)
            if info.get("last_seen") else None
        )
        workers.append(info)
    
    workers.sort(key=lambda x: (str(x.get("machine_id")), str(x.get("slot")), str(x.get("worker_id"))))
    
    return {
        "workers": workers,
        "queue_depth": manager.queue.size(),
        "ts": time.time(),
    }


@router.get("/workers/summary")
def get_workers_summary() -> dict:
    """获取资源摘要（供前端选择器使用）"""
    return get_available_resources(manager.lease)


@router.get("/workers/{worker_id}")
def get_worker_detail(worker_id: str) -> dict:
    """获取单个 Worker 详情"""
    worker = next(
        (item for item in manager.lease.list_workers()
         if str(item.get("worker_id", "")) == worker_id),
        None,
    )
    if worker is None:
        raise HTTPException(404, "Worker 不存在")
    
    return worker


@router.post("/workers/select")
def select_workers(
    require_gpu: bool = False,
    min_vram_mb: int = 0,
    device_backend: str = "auto",
    max_workers: int = 1,
    strategy: str = "auto",
) -> dict:
    """根据资源需求自动选择 Worker"""
    selector = ResourceSelector(manager.lease)
    
    resource_hint = {
        "require_gpu": require_gpu,
        "min_vram_mb": min_vram_mb,
        "device_backend": device_backend,
    }
    
    try:
        worker_ids, strategy_obj = selector.select_workers(resource_hint, strategy, max_workers)
        return {
            "selected_workers": worker_ids,
            "strategy": {
                "mode": strategy_obj.mode.value,
                "dp_degree": strategy_obj.dp_degree,
                "pp_degree": strategy_obj.pp_degree,
                "tp_degree": strategy_obj.tp_degree,
                "world_size": strategy_obj.world_size,
            },
        }
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/workers/validate")
def validate_worker_selection(
    worker_ids: List[str],
    require_gpu: bool = False,
    min_vram_mb: int = 0,
    device_backend: str = "auto",
) -> dict:
    """验证指定的 Worker 选择是否满足要求"""
    resource_hint = {
        "require_gpu": require_gpu,
        "min_vram_mb": min_vram_mb,
        "device_backend": device_backend,
    }
    
    selector = ResourceSelector(manager.lease)
    all_workers = manager.lease.list_workers()
    worker_map = {w["worker_id"]: w for w in all_workers}
    
    results = []
    for wid in worker_ids:
        if wid not in worker_map:
            results.append({"worker_id": wid, "valid": False, "reason": "Worker 不存在"})
            continue
        
        worker = worker_map[wid]
        gpu = worker.get("gpu", {})
        
        valid = True
        reasons = []
        
        if require_gpu and not gpu.get("available"):
            valid = False
            reasons.append("需要 GPU 但该 Worker 无可用 GPU")
        
        if min_vram_mb > 0 and int(gpu.get("vram_mb", 0)) < min_vram_mb:
            valid = False
            reasons.append(f"显存不足: 需要 {min_vram_mb}MB, 实际 {gpu.get('vram_mb', 0)}MB")
        
        if device_backend != "auto":
            actual_backend = str(gpu.get("backend") or worker.get("device_backend") or ("cuda" if gpu.get("available") else "cpu"))
            if device_backend != actual_backend:
                valid = False
                reasons.append(f"后端不匹配: 需要 {device_backend}, 实际 {actual_backend}")
        
        results.append({
            "worker_id": wid,
            "valid": valid,
            "reasons": reasons,
            "specs": {
                "device": gpu.get("device", "cpu") if gpu.get("available") else "cpu",
                "gpu_name": gpu.get("name"),
                "vram_mb": gpu.get("vram_mb"),
            }
        })
    
    all_valid = all(r["valid"] for r in results)
    return {"all_valid": all_valid, "results": results}


@router.patch("/workers/{worker_id}/enable")
def set_worker_enabled(worker_id: str, enabled: bool) -> dict:
    """启用/禁用 Worker"""
    worker = next(
        (item for item in manager.lease.list_workers()
         if str(item.get("worker_id", "")) == worker_id),
        None,
    )
    if worker is None:
        raise HTTPException(404, "Worker 不存在")
    
    if not bool(worker.get("managed", False)):
        raise HTTPException(409, "本机内置计算槽由平台自动管理")
    
    manager.lease.set_worker_enabled(worker_id, enabled)
    return {
        "ok": True,
        "worker_id": worker_id,
        "enabled": enabled,
        "message": "计算槽已加入资源池" if enabled else "已停止接收新任务；正在运行的任务会正常完成",
    }


# 需要导入 time
import time
from typing import List