"""任务记录层：job.json 的读写、状态流转、日志与指标的工具函数。

job.json 是任务元信息与状态的**权威数据源**（MySQL 仅做镜像）：
- 控制平面（API）在入队/取消/暂停/恢复时写入；
- 计算平面（Worker）持有租约期间写入训练进度；
- 双方共用本模块，保证字段与状态机一致。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, List, Optional

import app.config as config
from app.storage import ArtifactStore, job_key

# 任务状态机：paused 为可续训的"暂停"态（服务器释放/心跳丢失/用户暂停），
# queued/running 崩溃恢复时都会回到 queued 由任意 worker 接手。
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
PAUSED = "paused"
TERMINAL = {COMPLETED, FAILED, CANCELLED}


def read_job(store: ArtifactStore, job_id: str) -> Optional[dict]:
    """读取任务记录（job.json）；不存在返回 None。"""
    record = store.read_json(job_key(job_id, "job.json"))
    if record is None:
        return None
    record.setdefault("id", job_id)
    return record


def write_job(store: ArtifactStore, record: dict) -> None:
    """整写任务记录（原子覆盖）。"""
    store.write_json(job_key(str(record["id"]), "job.json"), record)


def update_job(store: ArtifactStore, job_id: str, **fields: Any) -> dict:
    """合并式更新任务记录并持久化，同时尽力镜像到数据库（DB 不可用不影响主流程）。

    训练、状态流转的关键字段（status/error/device/summary 等）经由此函数统一落盘。
    """
    record = read_job(store, job_id)
    if record is None:
        raise KeyError(job_id)
    record.update(fields)
    record["id"] = job_id
    write_job(store, record)
    _mirror_to_db(record, fields)
    return record


def _mirror_to_db(record: dict, fields: dict) -> None:
    """把状态变更镜像到 MySQL（可用时）；失败静默，不影响文件权威源。"""
    if not config.DB_AVAILABLE:
        return
    if "status" not in fields:
        return
    try:
        from app.database import SessionLocal
        from app.db_utils import update_job_status

        with SessionLocal() as db:
            update_job_status(
                db,
                str(record["id"]),
                str(fields["status"]),
                error=fields.get("error"),
                device=fields.get("device"),
                summary=fields.get("summary"),
            )
    except Exception:
        pass


def append_job_log(store: ArtifactStore, job_id: str, msg: str) -> None:
    """向任务的 log.txt 追加一行带时间戳的日志（直接写状态平面，续训时各段自然衔接）。"""
    stamp = time.strftime("%H:%M:%S")
    store.append_text(job_key(job_id, "log.txt"), f"[{stamp}] {msg}\n")


def read_job_log(store: ArtifactStore, job_id: str) -> str:
    """读取任务全部日志文本；无日志返回空串。"""
    return store.read_text(job_key(job_id, "log.txt")) or ""


def list_job_ids(store: ArtifactStore) -> List[str]:
    """列出状态平面上的全部任务 ID（按 key 解析，本地/对象存储两后端通用）。"""
    ids: List[str] = []
    for key in store.list_keys("jobs/"):
        suffix = "/job.json"
        if key.endswith(suffix):
            ids.append(key[len("jobs/"):-len(suffix)])
    return ids


def parse_metrics_text(text: str) -> List[dict]:
    """解析 metrics.jsonl 文本；坏行静默跳过（续训回滚/断点场景容忍半行）。"""
    out: List[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def read_metrics_text(store: ArtifactStore, job_id: str) -> str:
    """读取任务全部逐 epoch 指标文本；无指标返回空串。"""
    return store.read_text(job_key(job_id, "metrics.jsonl")) or ""


def read_metrics(store: ArtifactStore, job_id: str) -> List[dict]:
    """读取任务逐 epoch 指标列表。"""
    return parse_metrics_text(read_metrics_text(store, job_id))


def truncate_metrics_to_epoch(store: ArtifactStore, job_id: str, epoch: int) -> str:
    """把 metrics.jsonl 回滚截断到指定 epoch（含），并返回截断后的文本。

    续训一致性协议：checkpoint 是进度的唯一权威——接手 worker 先回滚指标，
    丢弃比 checkpoint 更新的"孤儿"记录，保证曲线连续无重复、无锯齿。
    """
    text = read_metrics_text(store, job_id)
    records = parse_metrics_text(text)
    kept = [r for r in records if int(r.get("epoch", 0)) <= int(epoch)]
    truncated = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept)
    if truncated != text:
        store.write_bytes(job_key(job_id, "metrics.jsonl"), truncated.encode("utf-8"))
    return truncated


def delete_job_artifacts(store: ArtifactStore, job_id: str) -> int:
    """删除任务的全部产物（job.json/checkpoint/metrics/日志/权重）。"""
    return store.delete_prefix(f"jobs/{job_id}/")
