"""统一计算设备适配。

设备字符串统一使用 PyTorch 能识别的形式（``cuda[:N]``、``xpu[:N]``、
``mps``、``cpu``），同时保留 ``backend``/``vendor`` 字段区分 NVIDIA CUDA、
AMD ROCm、Intel XPU 和 Apple MPS。这样 checkpoint 和联合训练协议不需要知道
具体驱动实现，远端 worker 也可以在不同操作系统上复用同一套训练代码。
"""
from __future__ import annotations

import os
from typing import Any, Callable

import torch

LogFn = Callable[[str], None]


def _xpu_module() -> Any | None:
    """返回可选的 torch.xpu 模块；旧版 PyTorch 没有该属性时安全降级。"""
    return getattr(torch, "xpu", None)


def _mps_available() -> bool:
    """探测 MPS 是否已构建且可用，兼容没有 MPS 属性的 Linux/Windows。"""
    try:
        backend = getattr(torch.backends, "mps", None)
        return bool(backend and backend.is_built() and backend.is_available())
    except Exception:
        return False


def _xpu_available() -> bool:
    """探测 Intel XPU，避免直接访问不存在的 torch.xpu 属性。"""
    xpu = _xpu_module()
    try:
        return bool(xpu and xpu.is_available())
    except Exception:
        return False


def _rocm_available() -> bool:
    """ROCm 通过 PyTorch 的 CUDA API 暴露，但由 torch.version.hip 区分。"""
    try:
        return bool(getattr(torch.version, "hip", None) and torch.cuda.is_available())
    except Exception:
        return False


def _cuda_available() -> bool:
    """探测 NVIDIA CUDA，排除 ROCm 构建以便调用方得到正确 vendor。"""
    try:
        return bool(torch.cuda.is_available() and not getattr(torch.version, "hip", None))
    except Exception:
        return False


def available_backends() -> list[str]:
    """按优先级返回当前进程可使用的设备后端。"""
    result: list[str] = []
    if _cuda_available():
        result.append("cuda")
    if _rocm_available():
        result.append("rocm")
    if _xpu_available():
        result.append("xpu")
    if _mps_available():
        result.append("mps")
    result.append("cpu")
    return result


def _backend_device(backend: str, index: int = 0) -> torch.device:
    """把公开后端名转换为 PyTorch 设备对象。"""
    normalized = backend.strip().lower()
    if normalized in {"cuda", "rocm", "amd", "nvidia"}:
        return torch.device("cuda", index)
    if normalized in {"xpu", "intel"}:
        return torch.device("xpu", index)
    if normalized in {"mps", "apple"}:
        return torch.device("mps")
    return torch.device("cpu")


def backend_from_device(device: torch.device | str) -> str:
    """根据设备对象推导公开后端名；CUDA/ROCm 依据当前 PyTorch 构建区分。"""
    value = torch.device(device)
    if value.type == "cuda":
        return "rocm" if getattr(torch.version, "hip", None) else "cuda"
    if value.type == "xpu":
        return "xpu"
    if value.type == "mps":
        return "mps"
    return "cpu"


def vendor_from_backend(backend: str) -> str:
    """将后端映射到硬件厂商名称。"""
    return {
        "cuda": "nvidia",
        "rocm": "amd",
        "xpu": "intel",
        "mps": "apple",
        "cpu": "cpu",
    }.get(backend, backend)


def _backend_count(backend: str) -> int:
    """读取设备数量；单设备后端（MPS/CPU）固定为 1。"""
    try:
        if backend in {"cuda", "rocm"}:
            return int(torch.cuda.device_count())
        if backend == "xpu":
            xpu = _xpu_module()
            return int(xpu.device_count()) if xpu is not None else 0
        if backend == "mps":
            return 1
    except Exception:
        return 0
    return 1


def _device_name(backend: str, index: int) -> str | None:
    """读取设备名称，驱动初始化失败时返回空值而非阻塞 worker 启动。"""
    try:
        if backend in {"cuda", "rocm"}:
            return str(torch.cuda.get_device_name(index))
        if backend == "xpu":
            xpu = _xpu_module()
            return str(xpu.get_device_name(index)) if xpu is not None else None
        if backend == "mps":
            return "Apple GPU (MPS)"
    except Exception:
        return None
    return "CPU"


def _device_memory_mb(backend: str, index: int) -> int:
    """返回显存容量；统一内存后端没有可靠 API 时报告 0。"""
    try:
        if backend in {"cuda", "rocm"}:
            return int(torch.cuda.get_device_properties(index).total_memory // 2**20)
        if backend == "xpu":
            xpu = _xpu_module()
            if xpu is not None and hasattr(xpu, "get_device_properties"):
                properties = xpu.get_device_properties(index)
                value = getattr(properties, "total_memory", 0)
                return int(value // 2**20)
    except Exception:
        pass
    return 0


def device_info(preferred: str | None = None, index: int = 0) -> dict[str, Any]:
    """探测设备并返回稳定的后端、设备、厂商和健康字段。

    ``preferred`` 可由 worker 环境或任务资源提示指定；不可用时抛出清晰的
    ``ValueError``，自动模式则按 CUDA/ROCm、XPU、MPS、CPU 顺序选择。
    """
    requested = (preferred or os.environ.get("WORKER_DEVICE", "")).strip().lower()
    aliases = {"auto": "", "gpu": "", "amd": "rocm", "nvidia": "cuda", "intel": "xpu", "apple": "mps"}
    requested = aliases.get(requested, requested)
    if requested:
        if requested not in {"cuda", "rocm", "xpu", "mps", "cpu"}:
            raise ValueError(f"不支持的计算后端：{preferred}")
        candidates = [requested]
    else:
        candidates = available_backends()
    availability = {
        "cuda": _cuda_available(), "rocm": _rocm_available(),
        "xpu": _xpu_available(), "mps": _mps_available(), "cpu": True,
    }
    backend = next((item for item in candidates if availability.get(item, False)), None)
    if backend is None:
        raise ValueError(f"计算后端 {preferred} 不可用，请检查驱动和 PyTorch 构建")
    count = _backend_count(backend)
    if index < 0 or index >= max(count, 1):
        raise ValueError(f"计算后端 {backend} 的设备索引 {index} 不存在（共 {count} 个）")
    device = _backend_device(backend, index)
    return {
        "backend": backend,
        "device": str(device),
        "vendor": vendor_from_backend(backend),
        "name": _device_name(backend, index),
        "count": count,
        "memory_mb": _device_memory_mb(backend, index),
        "available": True,
        "healthy": probe_device(device),
        "distributed_backends": ["gloo"] + (["nccl"] if backend == "cuda" else []),
    }


def probe_device(device: torch.device | str) -> bool:
    """执行一次微型张量运算，验证设备可实际用于训练。"""
    try:
        target = torch.device(device)
        value = torch.tensor([2.0], device=target)
        return bool(float((value * 2).to("cpu").item()) == 4.0)
    except Exception:
        return False


def select_device(log_fn: LogFn | None = None, preferred: str | None = None) -> torch.device:
    """选择训练设备；显式后端失败时回退 CPU，自动模式保持兼容旧行为。"""
    try:
        info = device_info(preferred)
        device = torch.device(info["device"])
        if not info["healthy"]:
            raise RuntimeError(f"{info['backend']} 设备微型运算失败")
        if log_fn:
            log_fn(f"成功启动 {info['vendor']} {info['backend']} 设备：{info['name']} ({device})")
        return device
    except Exception as exc:
        if preferred and preferred.strip().lower() not in {"", "auto", "cpu"}:
            raise RuntimeError(f"计算后端 {preferred} 初始化失败：{exc}") from exc
        if log_fn:
            log_fn(f"GPU/XPU/MPS 初始化失败：{exc}，回退到 CPU 训练")
        return torch.device("cpu")


def set_device(device: torch.device | str) -> None:
    """设置当前进程设备索引，MPS/CPU 无需显式设置。"""
    value = torch.device(device)
    try:
        if value.type == "cuda":
            torch.cuda.set_device(value)
        elif value.type == "xpu":
            xpu = _xpu_module()
            if xpu is not None and hasattr(xpu, "set_device"):
                xpu.set_device(value)
    except Exception as exc:
        raise RuntimeError(f"无法设置计算设备 {value}：{exc}") from exc


def seed_device(seed: int, device: torch.device | str | None = None) -> None:
    """为 CUDA/ROCm、Intel XPU 和 MPS 设置可用的设备随机种子。"""
    value = torch.device(device) if device is not None else None
    try:
        if value is None or value.type == "cuda":
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
        elif value.type == "xpu":
            xpu = _xpu_module()
            if xpu is not None and hasattr(xpu, "manual_seed_all"):
                xpu.manual_seed_all(seed)
    except Exception:
        # 随机种子增强不能阻塞训练启动，CPU seed 仍由调用方设置。
        return


def device_rng_state(device: torch.device | str) -> Any | None:
    """读取可选设备随机状态，供跨设备 checkpoint 尽力恢复。"""
    value = torch.device(device)
    try:
        if value.type == "cuda" and torch.cuda.is_available():
            return torch.cuda.get_rng_state_all()
        if value.type == "xpu":
            xpu = _xpu_module()
            if xpu is not None and hasattr(xpu, "get_rng_state_all"):
                return xpu.get_rng_state_all()
    except Exception:
        return None
    return None


def restore_device_rng_state(device: torch.device | str, states: Any, log_fn: LogFn | None = None) -> None:
    """恢复可选设备随机状态；设备数不匹配时记录并继续训练。"""
    if not states:
        return
    value = torch.device(device)
    try:
        if value.type == "cuda" and torch.cuda.is_available():
            if torch.cuda.device_count() >= len(states):
                torch.cuda.set_rng_state_all([state.cpu() for state in states])
        elif value.type == "xpu":
            xpu = _xpu_module()
            if xpu is not None and hasattr(xpu, "set_rng_state_all"):
                xpu.set_rng_state_all(states)
    except Exception as exc:
        if log_fn:
            log_fn(f"{backend_from_device(value)} 随机数状态恢复跳过：{exc}")
