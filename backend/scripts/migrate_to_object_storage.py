#!/usr/bin/env python3
"""把本地遗留数据迁移到对象存储状态平面（多机模式一次性升级工具）。

背景：旧版本所有数据都在 {DATA_DIR}/ 下（数据集 CSV + 任务产物）。
多机模式下状态平面切换为 MinIO/S3（STORAGE_BACKEND=minio），worker 从
对象存储拉数据集与 checkpoint——历史数据需要先迁移过去，否则任务
记录/断点/模型在 worker 侧不可见（本机继续可读，但无法续训/交付）。

用法（在 backend 目录）：
    STORAGE_BACKEND=minio MINIO_ENDPOINT=... python scripts/migrate_to_object_storage.py [--dry-run]

- 幂等：已存在于状态平面的 key 自动跳过，可重复执行；
- local 后端无需迁移：本地文件本身就是权威状态平面，直接退出；
- 只读源文件、只新增目标 key，不删除、不覆盖已有对象。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 保证脚本可直接运行（cd backend && python scripts/...）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.config as config  # noqa: E402
from app.storage import get_store  # noqa: E402

# 任务目录内需要迁移的平铺产物（checkpoint 单独递归处理）
JOB_FILES = ("job.json", "log.txt", "metrics.jsonl", "best.pt", "last.pt",
             "preprocessor.json", "summary.json")


def find_dataset_csvs() -> list[tuple[str, Path]]:
    """收集全部遗留数据集 CSV（新旧两种布局，按 ds_id 去重，processed 优先）。"""
    found: dict[str, Path] = {}
    order = [
        config.DATASETS_DIR / "processed",
        config.DATASETS_DIR / "raw",
        config.DATASETS_DIR,
    ]
    for base in order:
        if not base.exists():
            continue
        for csv in sorted(base.glob("*.csv")):
            found.setdefault(csv.stem, csv)
    return sorted(found.items())


def find_job_dirs() -> list[Path]:
    """收集全部遗留任务目录（data/jobs/job-*）。"""
    if not config.JOBS_DIR.exists():
        return []
    return sorted(p for p in config.JOBS_DIR.iterdir() if p.is_dir())


def collect_job_files(job_dir: Path) -> list[tuple[str, Path]]:
    """任务目录 -> (状态平面 key, 本地文件) 列表（含 checkpoints 递归）。"""
    pairs: list[tuple[str, Path]] = []
    for name in JOB_FILES:
        f = job_dir / name
        if f.is_file():
            pairs.append((f"jobs/{job_dir.name}/{name}", f))
    ckpt_dir = job_dir / "checkpoints"
    if ckpt_dir.exists():
        for f in sorted(ckpt_dir.glob("*.pt")):
            pairs.append((f"jobs/{job_dir.name}/checkpoints/{f.name}", f))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移本地遗留数据到对象存储状态平面")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要迁移的 key，不实际写入")
    args = parser.parse_args()

    store = get_store()
    local_backend = type(store).__name__ == "LocalArtifactStore"
    if local_backend:
        print("[migrate] STORAGE_BACKEND=local：本地文件即权威状态平面，无需迁移。")
        print("[migrate] 多机部署时请先配置 STORAGE_BACKEND=minio 与 MINIO_* 再运行本脚本。")
        return 0

    print(f"[migrate] 状态平面: {type(store).__name__} "
          f"endpoint={os.environ.get('MINIO_ENDPOINT', config.MINIO_ENDPOINT)} "
          f"bucket={config.MINIO_BUCKET}")

    created = skipped = failed = 0

    # ---- 数据集 ----
    datasets = find_dataset_csvs()
    print(f"[migrate] 发现本地数据集 {len(datasets)} 个")
    for ds_id, csv in datasets:
        key = f"datasets/{ds_id}.csv"
        try:
            if store.exists(key):
                skipped += 1
                print(f"  = 跳过（已存在）: {key}")
                continue
            print(f"  + {'[dry-run] ' if args.dry_run else ''}迁移: {key}  ({csv.stat().st_size} bytes)")
            if not args.dry_run:
                store.write_file(key, csv)
            created += 1
        except Exception as exc:
            failed += 1
            print(f"  ! 失败: {key}: {exc}")

    # ---- 任务产物 ----
    job_dirs = find_job_dirs()
    print(f"[migrate] 发现本地任务目录 {len(job_dirs)} 个")
    for job_dir in job_dirs:
        for key, path in collect_job_files(job_dir):
            try:
                if store.exists(key):
                    skipped += 1
                    continue
                print(f"  + {'[dry-run] ' if args.dry_run else ''}迁移: {key}")
                if not args.dry_run:
                    store.write_file(key, path)
                created += 1
            except Exception as exc:
                failed += 1
                print(f"  ! 失败: {key}: {exc}")

    print(f"[migrate] 完成: 新增 {created}，已存在跳过 {skipped}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
