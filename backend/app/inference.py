"""独立预测队列、持久状态及受计算槽租约保护的子进程执行。"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from app import config
from app.storage import LocalArtifactStore, get_store, job_key

QUEUE_TIMEOUT = 3600
RUN_TIMEOUT = 300
TERMINAL = {"completed", "failed"}


def inference_key(inference_id: str, name: str = "request.json") -> str:
    """只允许服务生成的预测编号访问独立存储命名空间。"""
    if not re.fullmatch(r"infer-[0-9a-f]{32}", inference_id):
        raise ValueError("非法预测编号")
    return f"inferences/{inference_id}/{name}"


class InferenceService:
    """持久记录与队列分离；失租的预测终止，不迁移到其它资源。"""

    def __init__(self, store, queue, lease) -> None:
        """复用训练的存储和租约，预测使用独立队列与记录。"""
        self.store, self.queue, self.lease = store, queue, lease

    def validate_worker(self, worker_id: str, task: str) -> dict:
        """提交及执行前检查资源，繁忙计算槽可排队但不能静默换机。"""
        worker = next((w for w in self.lease.list_workers() if w.get("worker_id") == worker_id), None)
        if not worker or not worker.get("active"):
            raise ValueError("所选预测 Worker 不存在或已离线")
        if not self.lease.is_worker_enabled(worker_id, default=not bool(worker.get("managed"))):
            raise ValueError("所选预测 Worker 已停用")
        gpu = worker.get("gpu") or {}
        backend = worker.get("device_backend") or gpu.get("backend") or "cpu"
        if worker.get("state") in {"degraded", "recovering"} or gpu.get("healthy") is False:
            raise ValueError("所选预测 Worker 设备异常")
        if backend not in {"cpu", "cuda", "rocm", "xpu", "mps"}:
            raise ValueError("所选预测 Worker 计算后端不支持")
        if task == "clustering" and backend != "cpu":
            raise ValueError("KMeans 聚类预测仅支持 CPU")
        if backend != "cpu" and not gpu.get("available"):
            raise ValueError("所选预测 Worker 加速设备不可用")
        return worker

    def submit(self, job_id: str, task: str, worker_id: str, payload: dict) -> dict:
        """先保存不可变请求再入队，状态查询不返回用户输入或内部 token。"""
        worker = self.validate_worker(worker_id, task)
        inference_id = f"infer-{uuid.uuid4().hex}"
        record = {"id": inference_id, "job_id": job_id, "task": task,
                  "worker_id": worker_id, "machine_id": worker.get("machine_id"),
                  "status": "queued", "created_at": time.time(),
                  "payload": payload, "timeout": RUN_TIMEOUT}
        self.store.write_json(inference_key(inference_id), record)
        self.queue.inference_queue(worker_id).push(inference_id)
        return self.get(inference_id)

    def get(self, inference_id: str) -> dict | None:
        """按执行 token 读取结果；独立失败标记永久隔离失租执行者的迟到写入。"""
        record = self.store.read_json(inference_key(inference_id))
        if not record:
            return None
        execution = self.store.read_json(inference_key(inference_id, "execution.json")) or {}
        result = {}
        if execution.get("token"):
            result = self.store.read_json(inference_key(inference_id, f"result-{execution['token']}.json")) or {}
        failure = self.store.read_json(inference_key(inference_id, "failure.json")) or {}
        view = {**record, **execution, **result, **failure}
        for name in ("payload", "token", "timeout"):
            view.pop(name, None)
        if failure:
            view.pop("result", None)
        return view

    def recover(self) -> None:
        """补投丢失队列项，失租或超时后永久失败，不重跑不回退资源。"""
        for key in self.store.list_keys("inferences/"):
            if not key.endswith("/request.json"):
                continue
            try:
                self._recover_record(key)
            except Exception:
                # 单个损坏记录或存储失败不能阻断其他预测的恢复。
                logging.getLogger(__name__).warning("预测记录恢复失败：%s", key, exc_info=True)

    def _recover_record(self, key: str) -> None:
        """独立恢复一条记录，异常由外层巡检隔离。"""
        inference_id = key.split("/")[1]
        record = self.get(inference_id)
        if not record or record["status"] in TERMINAL:
            return
        # 与执行者竞争同一租约；持有者仍在运行时绝不修改其状态。
        token = uuid.uuid4().hex
        if not self.lease.acquire(inference_id, token, config.WORKER_LEASE_TTL):
            return
        try:
            record = self.get(inference_id)
            if record["status"] in TERMINAL:
                return
            queue = self.queue.inference_queue(record["worker_id"])
            if record["status"] == "running" or time.time() - record["created_at"] > QUEUE_TIMEOUT:
                error = "预测执行租约丢失，请重新提交" if record["status"] == "running" else "预测排队超时，请检查所选 Worker"
                self.store.write_json(inference_key(inference_id, "failure.json"),
                                      {"status": "failed", "error": error, "finished_at": time.time()})
                queue.remove(inference_id)
            elif inference_id not in queue.snapshot():
                queue.push(inference_id)
        finally:
            self.lease.release(inference_id, token)

    def execute(self, worker, inference_id: str) -> None:
        """独占训练共用的槽位，子进程终止后才释放资源。"""
        from app.parallel_worker import slot_key
        from app.worker import JobController

        token = uuid.uuid4().hex
        lease = self.lease
        if not lease.acquire(inference_id, token, config.WORKER_LEASE_TTL):
            return
        slot = slot_key(worker.worker_id)
        controller = JobController(lease, inference_id, token, worker.worker_id,
                                   config.WORKER_HEARTBEAT_INTERVAL, config.WORKER_LEASE_TTL)
        started = False
        try:
            record = self.store.read_json(inference_key(inference_id))
            if not record or self.get(inference_id)["status"] != "queued":
                return
            if record["worker_id"] != worker.worker_id:
                self.queue.inference_queue(record["worker_id"]).push(inference_id)
                return
            if not lease.acquire(slot, token, config.WORKER_LEASE_TTL):
                self.queue.inference_queue(worker.worker_id).push(inference_id)
                return
            controller.resources.append((slot, token))
            controller.resource_sampler = worker._resource_sampler
            worker._controller = controller
            controller.start(publish=False)
            self.validate_worker(worker.worker_id, record["task"])
            execution = {"token": token, "status": "running", "started_at": time.time(),
                         "worker_id": worker.worker_id, "machine_id": worker.caps.get("machine_id"),
                         "hostname": worker.caps.get("hostname"), "slot": worker.caps.get("slot")}
            controller.ensure_owned()
            self.store.write_json(inference_key(inference_id, "execution.json"), execution)
            started = True
            controller.set_progress(0, "predicting")
            result = run_prediction_process(record, worker.caps, self.store, controller, worker._shutdown)
            controller.ensure_owned()
            self.store.write_json(inference_key(inference_id, f"result-{token}.json"),
                                  {**result, "status": "completed", "finished_at": time.time()})
        except Exception as exc:
            # 失租后停止写入，由 watchdog 写独立失败标记，防止旧结果覆盖新状态。
            try:
                controller.ensure_owned()
                name = f"result-{token}.json" if started else "failure.json"
                self.store.write_json(inference_key(inference_id, name),
                                      {"status": "failed", "error": str(exc)[:1000], "finished_at": time.time()})
            except Exception:
                pass
        finally:
            controller.stop()
            worker._controller = None
            for key, owned_token in controller.resources:
                lease.release(key, owned_token)
            lease.release(inference_id, token)


def run_prediction_process(record: dict, caps: dict, store, controller, shutdown) -> dict:
    """将下载、模型加载和计算全部纳入超时，关闭子进程后清理私有临时目录。"""
    config.WORKER_SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="inference-", dir=config.WORKER_SCRATCH_DIR) as directory:
        root = Path(directory)
        request = {**record, "caps": caps,
                   "local_store": str(store.root) if isinstance(store, LocalArtifactStore) else None}
        request_path, output_path = root / "request.json", root / "result.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-m", "app.inference", str(request_path), str(output_path)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + record["timeout"]
        try:
            while process.poll() is None:
                controller.ensure_owned()
                if shutdown.is_set() or controller.pause_event.is_set():
                    raise RuntimeError("预测 Worker 已停止")
                if time.monotonic() >= deadline:
                    raise TimeoutError("预测执行超时（包含模型下载和加载）")
                time.sleep(0.05)
            controller.ensure_owned()
            if not output_path.exists():
                raise RuntimeError(f"预测子进程异常退出（{process.returncode}）")
            result = json.loads(output_path.read_text(encoding="utf-8"))
            if process.returncode or result.get("error"):
                raise RuntimeError(result.get("error") or "预测子进程失败")
            return result
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def predict_request(request: dict, root: Path) -> dict:
    """在所选 Worker 的真实设备上预测，保留训练保存的预处理与模型。"""
    from app.device import device_info
    from app.trainer import predict_row

    caps = request["caps"]
    gpu = caps.get("gpu") or {}
    backend = caps.get("device_backend") or gpu.get("backend") or "cpu"
    device_name = str(caps.get("device") or gpu.get("device") or "cpu")
    index = int(device_name.split(":")[-1]) if ":" in device_name else 0
    info = device_info(backend, index)
    if not info["healthy"]:
        raise RuntimeError("所选设备健康检查失败，未切换计算资源")
    store = LocalArtifactStore(Path(request["local_store"])) if request.get("local_store") else get_store()
    llm = request["task"].startswith("llm_")
    artifact = "best_model.zip" if llm else "best.pt"
    path = store.read_file(job_key(request["job_id"], artifact), root / artifact)
    payload = request["payload"]
    if llm:
        from app.llm_trainer import generate_text

        result = {"prompt": payload["prompt"],
                  "generated_text": generate_text(path, device=info["device"], **payload)}
    else:
        result = predict_row(path, device=info["device"], **payload)
    return {"result": result, "device": info["device"], "device_backend": info["backend"],
            "device_name": info["name"]}


def main() -> int:
    """子进程只读取服务生成的请求文件，错误通过私有结果文件交回父进程。"""
    source, target = map(Path, sys.argv[1:3])
    try:
        result = predict_request(json.loads(source.read_text(encoding="utf-8")), source.parent)
        code = 0
    except Exception as exc:
        result, code = {"error": str(exc)[:1000]}, 1
    target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
