"""FastAPI 后端入口：组装应用、系统信息、静态页面与领域路由。"""
from __future__ import annotations

import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import app.config as config
from app.device import available_backends, device_info
from app.routers import datasets, jobs
from app.state import manager  # noqa: F401  (对外保留引用，兼容测试/调用方)

app = FastAPI(title="可视化模型训练平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 领域路由
app.include_router(datasets.router)
app.include_router(jobs.router)


# ---------------------------------------------------------------------------
# 系统信息
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/system")
def system() -> dict:
    info = device_info()
    cuda = bool(info["backend"] in {"cuda", "rocm"})
    return {
        "device": info["device"],
        "device_name": info["name"] or "未检测到 GPU",
        "cuda_available": cuda,
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "gpu_count": int(info["count"] if info["backend"] != "cpu" else 0),
        "backend": info["backend"],
        "vendor": info["vendor"],
        "memory_mb": info["memory_mb"],
        "healthy": info["healthy"],
        "available_backends": available_backends(),
    }


# ---------------------------------------------------------------------------
# 静态页面（单文件前端；本地单进程开发的兜底，容器部署由 frontend 容器托管）
# ---------------------------------------------------------------------------

STATIC_MOUNT = StaticFiles(directory=config.STATIC_DIR)
app.mount("/static", STATIC_MOUNT, name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")
