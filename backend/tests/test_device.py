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


# ---------------------------------------------------------------------------
# seed_device 的 MPS 支持（训练可复现性验收）
# ---------------------------------------------------------------------------

def _mps_ready() -> bool:
    """判断当前环境是否具备可执行的 MPS 后端。"""
    mps = getattr(torch, "mps", None)
    return bool(mps and torch.backends.mps.is_available())


def test_seed_device_mps_makes_random_sequence_reproducible():
    """MPS 上固定 seed 后重复取随机序列必须一致，否则训练不可复现。"""
    if not _mps_ready():
        pytest.skip("当前环境无可用 MPS 后端")

    def draw():
        """固定 seed 后在 MPS 设备上取一段随机序列。"""
        device.seed_device(42, "mps")
        return torch.rand(8, device="mps").cpu()

    first = draw()
    assert torch.equal(first, draw())


@pytest.mark.parametrize("spec", ["mps", torch.device("mps")])
def test_seed_device_mps_accepts_string_and_device(spec):
    """seed_device 兼容字符串与 torch.device 两种 MPS 设备入参。"""
    if not _mps_ready():
        pytest.skip("当前环境无可用 MPS 后端")
    device.seed_device(7, spec)


def test_seed_device_mps_missing_module_is_noop(monkeypatch):
    """旧版 PyTorch 没有 torch.mps 属性时静默跳过，不阻塞训练启动。"""
    monkeypatch.delattr(torch, "mps", raising=False)
    device.seed_device(42, "mps")


def test_seed_device_mps_failure_is_swallowed(monkeypatch):
    """MPS 种子设置抛错时按既有容错语义吞掉，不影响训练启动。"""
    class BrokenMps:
        @staticmethod
        def manual_seed(_seed):
            """模拟 MPS 种子接口故障。"""
            raise RuntimeError("mps broken")

    monkeypatch.setattr(torch, "mps", BrokenMps, raising=False)
    device.seed_device(42, "mps")


def test_seed_device_cpu_and_unavailable_cuda_paths_do_not_raise():
    """CPU 与无 CUDA 环境的既有 seed 行为保持不变（回归保护）。"""
    device.seed_device(42, "cpu")
    device.seed_device(42, "cuda")
    device.seed_device(42)
