"""统一设备适配层测试：覆盖 CPU 默认、后端别名和不可用后端错误。"""
from __future__ import annotations

import pytest
import torch

from app import device


def test_cpu_device_info_is_healthy_and_describes_gloo():
    """无 GPU 环境仍返回完整能力结构，且 CPU 可用于 Gloo 通信。"""
    info = device.device_info("cpu")
    assert info["backend"] == "cpu"
    assert info["device"] == "cpu"
    assert info["vendor"] == "cpu"
    assert info["available"] is True
    assert info["healthy"] is True
    assert "gloo" in info["distributed_backends"]


@pytest.mark.parametrize(
    ("backend", "vendor", "device_type"),
    [("cuda", "nvidia", "cuda"), ("rocm", "amd", "cuda"),
     ("xpu", "intel", "xpu"), ("mps", "apple", "mps"), ("cpu", "cpu", "cpu")],
)
def test_backend_mapping_is_cross_platform(backend, vendor, device_type):
    """公开后端名称映射到稳定厂商名和 PyTorch 设备类型。"""
    assert device.vendor_from_backend(backend) == vendor
    assert device._backend_device(backend).type == device_type


def test_unavailable_explicit_backend_fails_clearly(monkeypatch):
    """显式要求未安装的加速后端时不能静默改用 CPU。"""
    monkeypatch.setattr(device, "_xpu_available", lambda: False)
    with pytest.raises(ValueError, match="后端 xpu 不可用"):
        device.device_info("xpu")


def test_select_device_auto_falls_back_to_cpu(monkeypatch):
    """自动模式的驱动初始化失败沿用旧兼容行为回退 CPU。"""
    monkeypatch.setattr(device, "device_info", lambda preferred=None: (_ for _ in ()).throw(RuntimeError("driver")))
    assert device.select_device() == torch.device("cpu")


@pytest.mark.parametrize(
    ("backend", "expected_device"),
    [("cuda", "cuda:0"), ("rocm", "cuda:0"), ("xpu", "xpu:0"),
     ("mps", "mps"), ("cpu", "cpu")],
)
def test_device_info_reports_simulated_cross_platform_backends(monkeypatch, backend, expected_device):
    """无对应硬件的测试机也能验证各平台设备协议映射。"""
    availability = {
        "cuda": backend == "cuda", "rocm": backend == "rocm",
        "xpu": backend == "xpu", "mps": backend == "mps",
    }
    for name, enabled in availability.items():
        monkeypatch.setattr(device, f"_{name}_available", lambda enabled=enabled: enabled)
    monkeypatch.setattr(device, "_backend_count", lambda name: 1)
    monkeypatch.setattr(device, "_device_name", lambda name, index: f"{name}-test-device")
    monkeypatch.setattr(device, "_device_memory_mb", lambda name, index: 4096)
    monkeypatch.setattr(device, "probe_device", lambda target: True)

    info = device.device_info(backend)

    assert info["backend"] == backend
    assert info["device"] == expected_device
    assert info["vendor"] == device.vendor_from_backend(backend)
    assert info["healthy"] is True
    assert "gloo" in info["distributed_backends"]
    assert ("nccl" in info["distributed_backends"]) is (backend == "cuda")


@pytest.mark.parametrize(("backend", "index"), [("cpu", -1), ("mps", -1), ("mps", 1)])
def test_device_info_rejects_invalid_single_device_indices(monkeypatch, backend, index):
    """CPU/MPS 的固定设备和负索引在张量探测前明确拒绝。"""
    monkeypatch.setattr(device, "_mps_available", lambda: True)
    monkeypatch.setattr(device, "_backend_count", lambda _backend: 1)
    with pytest.raises(ValueError, match="设备索引"):
        device.device_info(backend, index=index)
