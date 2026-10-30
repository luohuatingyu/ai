"""联合训练资源预留和独立 rank 进程；普通 worker 仍是任务状态的唯一写入者。"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def slot_key(worker_id: str) -> str:
    """为计算槽构造独立租约名，与任务租约使用不同命名空间。"""
    return "slot:" + worker_id


def select_members(lease, requested: list[dict], require_idle: bool = True) -> list[dict]:
    """校验已选节点的协议、设备和软件能力；忙碌节点保持排队，配置错误明确拒绝。"""
    workers = {str(item["worker_id"]): item for item in lease.list_workers()}
    members = []
    for selection in requested:
        worker_id = selection["worker_id"]
        worker = workers.get(worker_id)
        if worker is None or not worker.get("active"):
            raise RuntimeError(f"等待联合训练节点上线：{worker_id}")
        if not lease.is_worker_enabled(worker_id, default=not bool(worker.get("managed"))):
            raise RuntimeError(f"等待联合训练节点启用：{worker_id}")
        if require_idle and (worker.get("state") not in (None, "idle") or lease.peek_holder(slot_key(worker_id))):
            raise RuntimeError(f"等待联合训练节点空闲：{worker_id}")
        capability = worker.get("parallel") or {}
        if capability.get("protocol") != 1 or not capability.get("host") or not capability.get("gloo"):
            raise ValueError(f"节点 {worker_id} 尚未配置联合训练通信地址或 Gloo 支持")
        gpu = worker.get("gpu") or {}
        vendor = str(gpu.get("vendor") or "").lower()
        if vendor in {"", "cpu"}:
            vendor = str(capability.get("vendor") or "").lower()
        backend_value = gpu.get("backend") or capability.get("backend")
        backend = str(backend_value or ("cuda" if gpu.get("available") else "cpu")).lower()
        # 兼容旧 worker 注册信息：旧版本只上报 gpu.available，默认按 CUDA 解析。
        if gpu.get("available") and backend_value == "cpu" and vendor not in {"", "cpu", "nvidia"}:
            raise ValueError("当前节点不是 NVIDIA CUDA 后端；请使用 NVIDIA GPU 或重新登记后端能力")
        if gpu.get("available") and backend == "cpu":
            backend = {"nvidia": "cuda"}.get(vendor, "cuda")
        if gpu.get("available") and vendor == "amd" and backend == "cuda":
            raise ValueError("当前节点不是 NVIDIA CUDA 后端；请使用 NVIDIA GPU 或重新登记后端能力")
        if backend not in {"cuda", "rocm", "xpu", "mps", "cpu"}:
            raise ValueError(f"节点 {worker_id} 的计算后端 {backend} 不受支持")
        if gpu.get("available") and capability.get("capability_supported") is False:
            raise ValueError(f"节点 {worker_id} 的显卡架构不受当前 PyTorch 构建支持")
        if gpu.get("available") and not capability.get("gloo"):
            raise ValueError(f"节点 {worker_id} 不支持 Gloo，无法进行异构联合训练")
        device = str(gpu.get("device") or capability.get("device") or "")
        if backend in {"cuda", "rocm"} and (not device or device == "cpu"):
            device = "cuda:0"
        elif backend == "xpu" and (not device or device == "cpu"):
            device = "xpu:0"
        elif backend == "mps" and (not device or device == "cpu"):
            device = "mps"
        elif not device:
            device = "cpu"
        members.append({
            **selection, "machine_id": worker.get("machine_id"),
            "device": device if gpu.get("available") else "cpu",
            "device_backend": backend,
            "vendor": vendor or backend,
            "gpu_name": gpu.get("name"), "torch_version": worker.get("torch_version"),
            "vram_mb": int(gpu.get("vram_mb") or 0),
            "host": capability["host"], "port": int(capability["port"]),
            "nccl": bool(capability.get("nccl")),
        })
    if len({str(item["torch_version"]).split("+")[0] for item in members}) != 1:
        raise ValueError("联合训练节点的 PyTorch 版本必须一致（CPU/CUDA 构建后缀可以不同）")
    return members


def make_group(job_id: str, token: str, members: list[dict], timeout: int, seed: int) -> dict:
    """生成本轮执行的不可变成员清单，使用租约 token 隔离重试轮次。"""
    group_id = hashlib.sha256(token.encode()).hexdigest()[:24]
    return {
        "parallel": 1, "job_id": job_id, "token": token, "group_id": group_id,
        "members": members, "host": members[0]["host"], "port": members[0]["port"],
        "backend": "nccl" if all(
            item.get("device_backend", "cuda" if str(item.get("device", "")).startswith("cuda") else "cpu") == "cuda"
            and str(item.get("device", "")).startswith("cuda") and item["nccl"]
            for item in members
        ) else "gloo",
        "timeout": timeout, "seed": seed,
        "manifest_key": f"jobs/{job_id}/attempts/{group_id}/request.json",
    }


def run_rank_process(request: dict, controller, log_fn, on_epoch_end=None, on_checkpoint=None) -> dict:
    """监督独立通信进程，转发控制与事件；退出前回收子进程及所有临时通信文件。"""
    from app.trainer import JobCancelled, JobPaused

    directory = Path(request["job_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    request_path = directory / "parallel-request.json"
    events_path = directory / "parallel-events.jsonl"
    control_path = directory / "parallel-control"
    output_path = directory / "parallel-output.log"
    ack_path = directory / "parallel-ack"
    request.update({"events_path": str(events_path), "control_path": str(control_path), "ack_path": str(ack_path), "parent_pid": os.getpid()})
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    events_path.touch()
    result = None
    process = None
    try:
        with output_path.open("w", encoding="utf-8") as output, events_path.open(encoding="utf-8") as events:
            environment = dict(os.environ)
            # 每个 rank 进程只计算模型；任务记录和数据库日志均由父 worker 发布。
            environment.update({"USE_DATABASE": "false", "PYTHONUNBUFFERED": "1"})
            process = subprocess.Popen([sys.executable, "-m", "app.parallel_worker", str(request_path)], env=environment, stdout=output, stderr=subprocess.STDOUT)
            sampler = getattr(controller, "resource_sampler", None)
            if sampler is not None:
                sampler.set_pid(process.pid)
            previous_signal = ""
            last_progress = time.monotonic()
            output_size = 0

            def forward_control():
                """先原子发布最新控制信号，再允许训练进程继续下一个 epoch。"""
                nonlocal previous_signal
                requested = "cancel" if controller.cancel_event.is_set() else "pause" if controller.pause_event.is_set() else ""
                if requested != previous_signal:
                    temporary = control_path.with_suffix(".tmp")
                    temporary.write_text(requested, encoding="utf-8")
                    temporary.replace(control_path)
                    previous_signal = requested

            while True:
                controller.ensure_owned()
                forward_control()
                size = output_path.stat().st_size
                if size != output_size:
                    output_size, last_progress = size, time.monotonic()
                if time.monotonic() - last_progress > request["group"]["timeout"]:
                    raise RuntimeError("联合训练进程无进展 timeout；请检查节点互联或增大超时")
                # 只读取完整行；写入中的 JSON 行留到下次轮询，避免遗漏 checkpoint 回调。
                while True:
                    position = events.tell()
                    line = events.readline()
                    if not line or not line.endswith("\n"):
                        events.seek(position)
                        break
                    event = json.loads(line)
                    last_progress = time.monotonic()
                    kind, value = event["kind"], event.get("value")
                    if kind == "log":
                        log_fn(value)
                    elif kind == "epoch" and on_epoch_end:
                        on_epoch_end(value)
                    elif kind == "checkpoint" and on_checkpoint:
                        on_checkpoint(int(value))
                    elif kind == "result":
                        result = value
                    if event.get("sequence"):
                        forward_control()
                        ack_path.write_text(str(event["sequence"]), encoding="utf-8")
                if process.poll() is not None:
                    # 子进程退出后再读一次，保证最后一条事件已可见。
                    for line in events:
                        event = json.loads(line)
                        if event["kind"] == "result":
                            result = event["value"]
                        elif event["kind"] == "epoch" and on_epoch_end:
                            on_epoch_end(event["value"])
                        elif event["kind"] == "checkpoint" and on_checkpoint:
                            on_checkpoint(int(event["value"]))
                        elif event["kind"] == "log":
                            log_fn(event["value"])
                        if event.get("sequence"):
                            forward_control()
                            ack_path.write_text(str(event["sequence"]), encoding="utf-8")
                    break
                time.sleep(0.1)
        if not result:
            detail = output_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(f"联合训练进程连接中断（exit={process.returncode}）：{detail}")
        status = result["status"]
        if status == "paused":
            raise JobPaused(result.get("error", "联合训练已暂停"))
        if status == "cancelled":
            raise JobCancelled()
        if status == "invalid":
            raise ValueError(result["error"])
        if status != "completed":
            raise RuntimeError("联合训练通信或节点失败：" + result.get("error", "未知错误"))
        return result["summary"]
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        sampler = getattr(controller, "resource_sampler", None)
        if sampler is not None:
            sampler.set_pid(os.getpid())
        for path in (request_path, events_path, control_path, control_path.with_suffix(".tmp"), output_path, ack_path):
            path.unlink(missing_ok=True)


class FileEvent:
    """将父进程控制文件映射为训练器使用的 Event 接口。"""

    def __init__(self, path: str, kind: str) -> None:
        """控制文件和本地粘滞事件共同决定信号状态。"""
        self.path, self.kind = Path(path), kind
        self.local = threading.Event()

    def is_set(self) -> bool:
        """读取取消/暂停请求，未创建文件表示尚无请求。"""
        return self.local.is_set() or (self.path.exists() and self.path.read_text(encoding="utf-8") == self.kind)

    def set(self) -> None:
        """保留通过全组通信收到的暂停信号。"""
        self.local.set()


def rank_main(request_path: str) -> int:
    """子进程入口：初始化进程组、执行现有训练器，并输出结构化结果。"""
    from app.parallel import ParallelContext
    from app.trainer import JobCancelled, JobPaused, load_trainer_state, run_training
    from app.performance import configure_torch_threads

    configure_torch_threads()

    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    # Linux 父 worker 异常退出时立即结束 rank，避免遗留 GPU 计算进程。
    if sys.platform == "linux":
        import ctypes
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)
        if os.getppid() != request["parent_pid"]:
            return 1

    sequence = 0

    def emit(kind, value):
        """每条事件写入后关闭文件，使父 worker 能及时发布日志和断点。"""
        nonlocal sequence
        acknowledged = kind in {"epoch", "checkpoint"}
        if acknowledged:
            sequence += 1
        with Path(request["events_path"]).open("a", encoding="utf-8") as output:
            output.write(json.dumps({"kind": kind, "value": value, "sequence": sequence if acknowledged else None}, ensure_ascii=False) + "\n")
        if acknowledged:
            deadline = time.monotonic() + request["group"]["timeout"]
            ack = Path(request["ack_path"])
            while not ack.exists() or ack.read_text(encoding="utf-8") != str(sequence):
                if os.getppid() != request["parent_pid"] or time.monotonic() > deadline:
                    raise RuntimeError("联合训练产物发布确认 timeout")
                time.sleep(0.05)

    context = None
    try:
        context = ParallelContext(request["group"])
        context.progress = lambda: emit("progress", None)
        resume_path = request.get("resume_path")
        summary = run_training(
            dataset_path=Path(request["dataset_path"]) if request.get("dataset_path") else None,
            target=request["target"], task=request["task"], params=request["params"],
            cancel_event=FileEvent(request["control_path"], "cancel"),
            pause_event=FileEvent(request["control_path"], "pause"),
            log_fn=lambda message: emit("log", message) if context.primary else None,
            job_dir=Path(request["job_dir"]),
            resume_state=load_trainer_state(Path(resume_path)) if resume_path else None,
            on_epoch_end=lambda record: emit("epoch", record) if context.primary else None,
            on_checkpoint=lambda epoch: emit("checkpoint", epoch) if context.primary else None,
            parallel=context,
        )
        context.close(successful=True)
        emit("result", {"status": "completed", "summary": summary})
    except JobPaused as exc:
        if context:
            context.close(successful=True)
        emit("result", {"status": "paused", "error": str(exc)})
    except JobCancelled:
        if context:
            context.close(successful=True)
        emit("result", {"status": "cancelled"})
    except Exception as exc:
        emit("result", {"status": "invalid" if isinstance(exc, (ValueError, FileNotFoundError)) else "failed", "error": str(exc)})
        return 1
    finally:
        if context:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(rank_main(sys.argv[1]))
