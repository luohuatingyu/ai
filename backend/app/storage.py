"""产物仓储抽象层：训练状态与服务器解耦的关键。

设计要点：
- 所有训练产物（job.json / metrics / 日志 / checkpoint / 模型权重）通过统一的
  key 访问，key 形如 "jobs/{job_id}/best.pt"、"datasets/{ds_id}.csv"；
- 两个实现：
  * LocalArtifactStore  —— 本地文件系统（单机模式，与旧版 data/ 目录布局完全一致）；
  * S3ArtifactStore     —— S3 兼容对象存储（多机共享状态平面，SeaweedFS/OSS/COS/S3 均可）；
- GPU/服务器只是"临时租客"：谁持有任务租约谁写入，其余节点随时可释放。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import urlsplit

import app.config as config

# 合法 key：仅允许字母/数字/下划线/点/横线/斜杠，禁止 ".." 段（防路径穿越）
_KEY_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")


def validate_key(key: str) -> str:
    """校验产物 key 的合法性，非法 key（含路径穿越等）直接抛错。"""
    if not key or not _KEY_RE.match(key):
        raise ValueError(f"非法的产物 key: {key!r}")
    if ".." in key.split("/"):
        raise ValueError(f"产物 key 不允许包含 '..': {key!r}")
    return key


def _oss_region_from_endpoint(endpoint: str) -> Optional[str]:
    """从阿里云 OSS/S3 endpoint 提取 region，避免 SDK 使用路径式区域探测。"""
    endpoint_url = endpoint if "://" in endpoint else f"//{endpoint}"
    hostname = (urlsplit(endpoint_url).hostname or "").lower()
    suffix = ".aliyuncs.com"
    if not hostname.endswith(suffix):
        return None

    domain = hostname[:-len(suffix)]
    if domain.startswith("s3.oss-"):
        region = domain[len("s3.oss-"):]
    elif domain.startswith("oss-"):
        region = domain[len("oss-"):]
    else:
        return None
    if region.endswith("-internal"):
        region = region[:-len("-internal")]
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+", region):
        return None
    return region


class ArtifactStore:
    """产物仓储接口：Local 与 S3 两个后端实现同一套语义。

    写入语义统一为"原子写"：读者永远看不到写了一半的文件——
    Local 用 tmp + os.replace，S3 依赖单次 PUT 的原子性。
    """

    # ---------------- 字节/文本 ----------------

    def read_bytes(self, key: str) -> Optional[bytes]:
        """读取产物内容；不存在返回 None。"""
        raise NotImplementedError

    def write_bytes(self, key: str, data: bytes) -> None:
        """原子写入产物内容（整文件覆盖）。"""
        raise NotImplementedError

    def read_text(self, key: str, encoding: str = "utf-8") -> Optional[str]:
        """读取文本内容；不存在返回 None。"""
        data = self.read_bytes(key)
        return None if data is None else data.decode(encoding, errors="replace")

    def append_text(self, key: str, text: str, encoding: str = "utf-8") -> None:
        """追加文本（日志/metrics 使用）；key 不存在时等同写入。"""
        current = self.read_text(key, encoding) or ""
        self.write_bytes(key, (current + text).encode(encoding))

    # ---------------- 文件 ----------------

    def write_file(self, key: str, local_path: Path) -> None:
        """将本地文件原子上传到仓储。"""
        self.write_bytes(key, Path(local_path).read_bytes())

    def read_file(self, key: str, local_path: Path) -> Path:
        """将产物下载到本地路径（worker 拉取 checkpoint/数据集用）。"""
        data = self.read_bytes(key)
        if data is None:
            raise FileNotFoundError(f"产物不存在: {key}")
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(local_path.parent), delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, local_path)  # 原子落盘，避免半文件
        return local_path

    # ---------------- 元数据 ----------------

    def exists(self, key: str) -> bool:
        """产物是否存在。"""
        raise NotImplementedError

    def delete(self, key: str) -> None:
        """删除产物（不存在时静默）。"""
        raise NotImplementedError

    def list_keys(self, prefix: str) -> List[str]:
        """列出指定前缀下的所有 key（不含目录占位）。"""
        raise NotImplementedError

    def delete_prefix(self, prefix: str) -> int:
        """删除指定前缀下全部产物，返回删除数量（任务删除时清理整个目录）。"""
        count = 0
        for key in self.list_keys(prefix):
            self.delete(key)
            count += 1
        return count

    # ---------------- JSON 便捷方法 ----------------

    def read_json(self, key: str) -> Optional[Any]:
        """读取 JSON 产物并反序列化；不存在或损坏返回 None。"""
        text = self.read_text(key)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def write_json(self, key: str, obj: Any) -> None:
        """将对象序列化为 JSON 并原子写入（中文原样、缩进 2）。"""
        self.write_bytes(
            key, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        )


class LocalArtifactStore(ArtifactStore):
    """本地文件系统实现：key 直接映射为 root/key，与旧版 data/ 布局完全兼容。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        """key -> 本地绝对路径（含合法性校验）。"""
        return self.root / validate_key(key)

    def read_bytes(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写临时文件再 rename，进程中途崩溃不会留下半文件
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_dir():
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def list_keys(self, prefix: str) -> List[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        if base.is_file():
            return [prefix]
        keys: List[str] = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                keys.append(str(path.relative_to(self.root)))
        return keys


class S3ArtifactStore(ArtifactStore):
    """S3 兼容对象存储实现（SeaweedFS / 阿里云 OSS S3 网关 / 腾讯云 COS / AWS S3 等）。

    依赖 minio SDK（惰性导入：未安装/未启用时本地模式不受影响）。
    minio 客户端是通用 S3 协议客户端，可与任意 S3 兼容服务互通。
    单次 put_object 即原子覆盖，读方永远看不到半写状态。
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket: Optional[str] = None,
        secure: Optional[bool] = None,
    ):
        # 惰性导入：保持 local 模式零依赖
        from minio import Minio  # noqa: WPS433

        resolved_endpoint = endpoint or config.S3_ENDPOINT
        self._client = Minio(
            resolved_endpoint,
            access_key=access_key or config.S3_ACCESS_KEY,
            secret_key=secret_key or config.S3_SECRET_KEY,
            secure=config.S3_SECURE if secure is None else secure,
            region=_oss_region_from_endpoint(resolved_endpoint),
        )
        self.bucket = bucket or config.S3_BUCKET
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """启动时确保 bucket 存在（不存在则创建）。"""
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)

    def read_bytes(self, key: str) -> Optional[bytes]:
        validate_key(key)
        try:
            response = self._client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception:
            return None

    def write_bytes(self, key: str, data: bytes) -> None:
        validate_key(key)
        from io import BytesIO

        self._client.put_object(self.bucket, key, BytesIO(data), length=len(data))

    def exists(self, key: str) -> bool:
        validate_key(key)
        try:
            self._client.stat_object(self.bucket, key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        validate_key(key)
        try:
            self._client.remove_object(self.bucket, key)
        except Exception:
            pass  # 不存在时静默

    def list_keys(self, prefix: str) -> List[str]:
        validate_key(prefix)
        keys: List[str] = []
        for obj in self._client.list_objects(self.bucket, prefix=prefix, recursive=True):
            if not obj.object_name.endswith("/"):
                keys.append(obj.object_name)
        return keys


# ---------------------------------------------------------------------------
# 仓储工厂（进程内单例）
# ---------------------------------------------------------------------------

_store_instance: Optional[ArtifactStore] = None


def get_store() -> ArtifactStore:
    """按配置返回仓储单例：STORAGE_BACKEND=s3 用对象存储，否则本地文件。"""
    global _store_instance
    if _store_instance is None:
        if config.STORAGE_BACKEND == "s3":
            _store_instance = S3ArtifactStore()
        else:
            _store_instance = LocalArtifactStore(config.DATA_DIR)
    return _store_instance


def reset_store() -> None:
    """重置仓储单例（测试用）。"""
    global _store_instance
    _store_instance = None


# ---------------------------------------------------------------------------
# 任务/数据集产物的 key 约定
# ---------------------------------------------------------------------------

def job_key(job_id: str, name: str) -> str:
    """任务产物 key：jobs/{job_id}/{name}（与旧版本地目录布局一一对应）。"""
    return f"jobs/{job_id}/{name}"


def checkpoint_key(job_id: str, epoch: int) -> str:
    """版本化 checkpoint key：按 epoch 命名，天然可回退、无覆盖半写风险。"""
    return job_key(job_id, f"checkpoints/ckpt-e{epoch:04d}.pt")


_CKPT_RE = re.compile(r"checkpoints/ckpt-e(\d+)\.pt$")


def parse_checkpoint_epoch(key: str) -> Optional[int]:
    """从 checkpoint key 解析 epoch 号；非 checkpoint key 返回 None。"""
    m = _CKPT_RE.search(key)
    return int(m.group(1)) if m else None


def find_latest_checkpoint(store: ArtifactStore, job_id: str) -> Optional[Tuple[int, str]]:
    """查找任务最新的版本化 checkpoint，返回 (epoch, key)。

    按 epoch 取最大值；同一 epoch 多个副本不可能出现（key 唯一）。
    未找到返回 None（任务尚未存档，从头训练）。
    """
    prefix = job_key(job_id, "checkpoints/")
    best: Optional[Tuple[int, str]] = None
    for key in store.list_keys(prefix):
        epoch = parse_checkpoint_epoch(key)
        if epoch is not None and (best is None or epoch > best[0]):
            best = (epoch, key)
    return best


def prune_checkpoints(store: ArtifactStore, job_id: str, keep: int) -> None:
    """只保留最近 keep 个版本化 checkpoint，其余删除（控制对象存储占用）。"""
    prefix = job_key(job_id, "checkpoints/")
    items = []
    for key in store.list_keys(prefix):
        epoch = parse_checkpoint_epoch(key)
        if epoch is not None:
            items.append((epoch, key))
    items.sort()
    for _, key in items[:-keep] if keep > 0 else []:
        store.delete(key)


# ---------------------------------------------------------------------------
# 数据集在仓储中的同步与解析
# ---------------------------------------------------------------------------

def sync_dataset_to_store(ds_id: str, local_csv: Path) -> bool:
    """数据集入库时同步一份到对象存储（多机 worker 需要自行拉取数据）。

    local 后端本身就在共享文件系统上，无需同步，直接返回 False。
    同步失败不阻塞主流程（上传接口不受影响），返回 False 并由调用方记录。
    """
    store = get_store()
    if isinstance(store, LocalArtifactStore):
        return False
    try:
        store.write_file(f"datasets/{ds_id}.csv", local_csv)
        return True
    except Exception:
        return False


def resolve_dataset_csv_local(ds_id: str) -> Path:
    """本地布局下查找数据集 CSV，兼容新旧两种存储结构。

    旧扁平格式：{DATASETS_DIR}/{ds_id}.csv（优先，保持历史行为）
    新结构：processed/、raw/、数据集目录副本（按序回退）
    找不到时返回旧扁平格式路径（由训练器抛出明确的文件不存在错误）。
    """
    if "/" in ds_id or ".." in ds_id or not _KEY_RE.match(ds_id):
        raise ValueError(f"非法数据集 ID: {ds_id!r}")
    candidates = [
        config.DATASETS_DIR / f"{ds_id}.csv",                # 旧扁平格式
        config.DATASETS_DIR / "processed" / f"{ds_id}.csv",  # 新结构：processed 目录
        config.DATASETS_DIR / "raw" / f"{ds_id}.csv",        # 新结构：raw 目录
        config.DATASETS_DIR / ds_id / f"{ds_id}.csv",        # 新结构：数据集目录副本
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def resolve_dataset_for_worker(ds_id: str, scratch_dir: Path) -> Path:
    """worker 视角解析数据集：本地布局直接用，对象存储则下载到 scratch。"""
    store = get_store()
    if isinstance(store, LocalArtifactStore):
        return resolve_dataset_csv_local(ds_id)
    key = f"datasets/{ds_id}.csv"
    if not store.exists(key):
        raise FileNotFoundError(
            f"数据集 {ds_id} 未同步到对象存储（请先运行迁移脚本 scripts/migrate_to_object_storage.py）"
        )
    return store.read_file(key, Path(scratch_dir) / f"{ds_id}.csv")
