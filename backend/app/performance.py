"""低频资源采样、进程线程预算和训练阶段计时；不改变训练算法或断点格式。"""
from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import torch


def _read(path: str) -> str:
    """读取可选系统计数器；平台不支持或进程退出时返回空值。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def available_cpu_cores() -> float:
    """结合 CPU 亲和性与容器配额，避免按宿主机总核心数分配线程。"""
    try:
        cores = float(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        cores = float(os.cpu_count() or 1)
    limits = []
    try:
        quota, period = _read("/sys/fs/cgroup/cpu.max").split()
        if quota != "max" and int(period) > 0 and int(quota) > 0:
            limits.append(int(quota) / int(period))
    except (ValueError, TypeError):
        pass
    try:
        quota = int(_read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"))
        period = int(_read("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
        if quota > 0 and period > 0:
            limits.append(quota / period)
    except ValueError:
        pass
    return max(min([cores, *limits]), 0.01)


def thread_environment(environment: dict, slots: int) -> dict:
    """为各槽分摊线程预算，保留更小的用户限制并拒绝非法值。"""
    result = dict(environment)
    budget = max(int(available_cpu_cores() // max(int(slots), 1)), 1)
    keys = ("WORKER_TORCH_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
    for key in keys:
        if result.get(key):
            try:
                value = int(result[key])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{key} 必须为正整数") from exc
            if value <= 0:
                raise ValueError(f"{key} 必须为正整数")
            budget = min(budget, value)
    for key in keys:
        result[key] = str(budget)
    return result


def configure_torch_threads() -> None:
    """仅在独立 worker/rank 入口应用预算，避免改动嵌入线程的全局设置。"""
    if os.environ.get("WORKER_TORCH_THREADS"):
        value = int(os.environ["WORKER_TORCH_THREADS"])
        if value <= 0:
            raise ValueError("WORKER_TORCH_THREADS 必须为正整数")
        torch.set_num_threads(value)


def _system_counters(pid: int) -> dict:
    """读取 Linux 主机和目标进程计数；未知数据为 None，不伪装为零占用。"""
    fields = _read("/proc/stat").splitlines()
    cpu = [int(value) for value in fields[0].split()[1:9]] if fields and fields[0].startswith("cpu ") else []
    memory = {}
    for line in _read("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            memory[parts[0].rstrip(":")] = int(parts[1]) / 1024
    status = {}
    for line in _read(f"/proc/{pid}/status").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            status[parts[0].rstrip(":")] = int(parts[1])
    process_seconds = None
    stat = _read(f"/proc/{pid}/stat")
    if ")" in stat:
        values = stat.rsplit(")", 1)[1].split()
        if len(values) > 12:
            process_seconds = (int(values[11]) + int(values[12])) / os.sysconf("SC_CLK_TCK")
    elif pid == os.getpid():
        usage = os.times()
        process_seconds = usage.user + usage.system
    container = {}
    for key, paths in {
        "limit_mb": ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        "used_mb": ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    }.items():
        container[key] = None
        for path in paths:
            raw = _read(path).strip()
            if raw.isdigit() and int(raw) < 2**60:
                container[key] = int(raw) / 2**20
                break
    return {
        "cpu_total": sum(cpu) if cpu else None,
        "cpu_idle": cpu[3] + (cpu[4] if len(cpu) > 4 else 0) if len(cpu) > 3 else None,
        "process_seconds": process_seconds, "threads": status.get("Threads"),
        "rss_mb": status["VmRSS"] / 1024 if "VmRSS" in status else None,
        "host_total_mb": memory.get("MemTotal"), "host_available_mb": memory.get("MemAvailable"),
        "container_limit_mb": container["limit_mb"], "container_used_mb": container["used_mb"],
    }


def _gpu_snapshot(device: str, own_process: bool) -> dict:
    """采集设备占用与当前进程分配量；UUID 匹配避免把其他 GPU 的数据误报。"""
    result = dict.fromkeys(("utilization_percent", "used_mb", "total_mb", "allocated_mb", "reserved_mb", "peak_allocated_mb"))
    parsed = torch.device(device)
    if parsed.type == "cpu":
        return result
    api = getattr(torch, parsed.type, None)
    if own_process and api is not None and (parsed.type != "cuda" or torch.cuda.is_initialized()):
        for key, method in (("allocated_mb", "memory_allocated"), ("reserved_mb", "memory_reserved"), ("peak_allocated_mb", "max_memory_allocated")):
            try:
                result[key] = float(getattr(api, method)(parsed)) / 2**20
            except (AttributeError, RuntimeError, TypeError, AssertionError):
                pass
    if parsed.type == "cuda" and not getattr(torch.version, "hip", None):
        try:
            properties = torch.cuda.get_device_properties(parsed)
            uuid = str(getattr(properties, "uuid", ""))
            output = subprocess.run(
                ["nvidia-smi", "--query-gpu=uuid,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=0.8, check=True,
            ).stdout
            for line in output.splitlines():
                values = [value.strip() for value in line.split(",")]
                if len(values) != 4 or not uuid or values[0] != uuid:
                    continue
                for key, raw in zip(("utilization_percent", "used_mb", "total_mb"), values[1:]):
                    try:
                        value = float(raw)
                        if math.isfinite(value) and value >= 0:
                            result[key] = value
                    except ValueError:
                        pass
        except (OSError, subprocess.SubprocessError, RuntimeError, AssertionError):
            pass
    return result


class ResourceSampler:
    """独立线程每五秒采样；心跳只读缓存，慢驱动命令不会阻塞续租。"""

    def __init__(self, device: str = "cpu", interval: float = 5.0) -> None:
        """为单槽保存有限历史，不在构造时启动线程或创建 CUDA 上下文。"""
        self.device, self.interval = device, max(float(interval), 1.0)
        self.pid = os.getpid()
        self._clock = time.monotonic
        self._previous = None
        self._samples = deque(maxlen=120)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def set_pid(self, pid: int) -> None:
        """联合训练切换到真实 rank PID，清空差分基线避免进程间计算假峰值。"""
        with self._lock:
            self.pid, self._previous = int(pid), None

    def sample(self) -> dict:
        """从累计计数计算区间使用率；首样本和不支持的指标保持缺失。"""
        with self._lock:
            pid, previous = self.pid, self._previous
        now = self._clock()
        raw = _system_counters(pid)
        host_percent = process_cores = None
        if previous and now > previous[0] and previous[2] == pid:
            old = previous[1]
            if raw["cpu_total"] is not None and old["cpu_total"] is not None:
                total = raw["cpu_total"] - old["cpu_total"]
                if total > 0:
                    host_percent = min(max(100 * (1 - (raw["cpu_idle"] - old["cpu_idle"]) / total), 0), 100)
            if raw["process_seconds"] is not None and old["process_seconds"] is not None:
                process_cores = max((raw["process_seconds"] - old["process_seconds"]) / (now - previous[0]), 0)
        gpu = _gpu_snapshot(self.device, pid == os.getpid())
        value = {
            "sampled_at": time.time(), "pid": pid,
            "host_cpu_percent": host_percent, "process_cpu_cores": process_cores,
            "available_cpu_cores": available_cpu_cores(), "torch_threads": torch.get_num_threads(),
            **{key: raw[key] for key in ("threads", "rss_mb", "host_total_mb", "host_available_mb", "container_limit_mb", "container_used_mb")},
            "gpu": gpu,
        }
        with self._lock:
            if self.pid == pid:
                self._previous = (now, raw, pid)
                self._samples.append(value)
        return value

    def snapshot(self) -> dict:
        """返回最近样本和最近十分钟曲线；没有样本时不生成伪数据。"""
        with self._lock:
            return {"latest": dict(self._samples[-1]) if self._samples else None,
                    "history": [{key: item.get(key) for key in ("sampled_at", "host_cpu_percent", "process_cpu_cores", "rss_mb")}
                                | {"gpu_percent": item["gpu"]["utilization_percent"]} for item in self._samples]}

    def start(self) -> None:
        """启动采样线程，所有采样故障独立于任务和心跳处理。"""
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            """采样器故障只留下过期时间戳，由页面明确标记。"""
            while not self._stop.is_set():
                try:
                    self.sample()
                except Exception:
                    pass
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="resource-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """退出时回收采样线程，不影响租约释放和 Worker 关闭。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


class EpochPerformance:
    """独立保存性能记录，不改变原有精度指标及断点恢复比较语义。"""

    def __init__(self, epoch: int, scope: str = "single") -> None:
        """以单调时钟记录阶段耗时，阶段外开销保留在总时长中。"""
        self.started = time.perf_counter()
        self.record = {"epoch": int(epoch), "scope": scope, "train_seconds": 0.0,
                       "validation_seconds": 0.0, "checkpoint_save_seconds": 0.0,
                       "checkpoint_publish_seconds": 0.0}

    @contextmanager
    def measure(self, field: str):
        """累计阶段耗时并原样传播异常，绝不吞掉训练或上传错误。"""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record[field] = self.record.get(field, 0.0) + time.perf_counter() - started

    def write(self, directory: Path, samples: int, steps: int, tokens: int | None = None) -> dict:
        """每个已完成 epoch 追加一次吞吐统计，零耗时或未知 token 不制造速率。"""
        elapsed = self.record["train_seconds"]
        self.record.update({"samples": samples, "steps": steps, "effective_tokens": tokens,
                            "samples_per_second": samples / elapsed if elapsed > 0 else None,
                            "tokens_per_second": tokens / elapsed if tokens is not None and elapsed > 0 else None,
                            "step_ms": elapsed * 1000 / steps if steps > 0 else None,
                            "epoch_seconds": time.perf_counter() - self.started})
        with (Path(directory) / "performance.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(self.record, ensure_ascii=False, allow_nan=False) + "\n")
        return dict(self.record)
