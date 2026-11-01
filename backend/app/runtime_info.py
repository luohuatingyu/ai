"""计算节点运行信息：源码文件白名单、代码指纹与依赖能力探测。

本模块必须保持轻量：worker 容器启动时即导入，且远程部署脚本按同一份白名单
打包源码；因此这里不得引入 torch 等重型依赖（依赖版本用惰性 import 探测）。

用途：
- ``RUNTIME_MODULE_FILES``：远程部署与代码指纹共用的运行文件清单，避免两处
  白名单漂移导致远程节点缺少新文件而启动失败；
- ``code_fingerprint``：对运行文件内容取摘要，平台与各计算槽可直接比对是否
  运行同一份代码（旧节点未同步时立即可见）；
- ``framework_versions`` / ``available_frameworks``：上报可用的训练框架能力，
  服务端据此拒绝把任务派给缺少依赖的节点。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# 计算节点运行所需的源码文件（相对 app/ 目录）；worker-entrypoint.sh 为启动脚本。
RUNTIME_MODULE_FILES = (
    "__init__.py", "config.py", "coordination.py", "storage.py", "jobstore.py",
    "device.py", "performance.py", "trainer.py", "llm_trainer.py", "worker.py",
    "inference.py", "predictor.py", "runtime_info.py", "worker-entrypoint.sh",
)
# 联合训练模块：支持该功能的版本才随包部署，兼容未启用该功能的旧版本。
OPTIONAL_RUNTIME_MODULE_FILES = ("parallel.py", "parallel_worker.py")

# 训练任务可能依赖的框架能力名（与 required_framework 的返回值保持一致）。
FRAMEWORK_TORCH = "torch"
FRAMEWORK_SKLEARN = "sklearn"
FRAMEWORK_XGBOOST = "xgboost"


def runtime_file_names(app_root: Path) -> tuple[str, ...]:
    """返回应随计算节点部署的文件名：必需的 + 当前版本存在的可选项。"""
    names = list(RUNTIME_MODULE_FILES)
    names.extend(
        name for name in OPTIONAL_RUNTIME_MODULE_FILES
        if (app_root / name).exists() or (app_root / name).is_symlink()
    )
    return tuple(names)


def code_fingerprint(app_root: Path) -> str:
    """对运行文件内容计算短摘要，用于判断平台与计算节点是否运行同一份代码。

    读取失败（文件缺失/不可读）时返回 "unknown"，不阻断 worker 启动：
    指纹只用于展示与排查，不作为功能前提。
    """
    digest = hashlib.sha256()
    try:
        for name in sorted(runtime_file_names(app_root)):
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update((app_root / name).read_bytes())
            digest.update(b"\0")
    except OSError:
        return "unknown"
    return digest.hexdigest()[:12]


def framework_versions() -> dict[str, str]:
    """探测训练框架版本；未安装的框架记为空字符串，不抛异常。"""
    versions: dict[str, str] = {}
    for module_name, key in (("torch", FRAMEWORK_TORCH),
                             ("sklearn", FRAMEWORK_SKLEARN),
                             ("xgboost", FRAMEWORK_XGBOOST),
                             ("numpy", "numpy")):
        try:
            module = __import__(module_name)
            versions[key] = str(getattr(module, "__version__", "") or "")
        except Exception:  # noqa: BLE001 - 缺依赖是正常状态，仅记录为空
            versions[key] = ""
    return versions


def available_frameworks(versions: dict[str, str] | None = None) -> list[str]:
    """按依赖探测结果列出可用的训练框架能力名。"""
    detected = versions if versions is not None else framework_versions()
    return [name for name in (FRAMEWORK_TORCH, FRAMEWORK_SKLEARN, FRAMEWORK_XGBOOST)
            if detected.get(name)]
