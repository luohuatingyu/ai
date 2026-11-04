"""独立预测队列、持久状态及受计算槽租约保护的子进程执行。"""
from __future__ import annotations

import json
import logging
import os
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


def prediction_environment() -> dict:
    """构造预测子进程环境：限制 BLAS/OpenMP 线程，避免小规格节点与同机协调平面抢 CPU。

    torch/sklearn/xgboost 默认按机器核心数开线程，2 vCPU 且 Redis 同机的节点一旦
    被预测压满，续租心跳就会超时；统一压到单线程是实测最有效的缓解手段。
    """
    env = dict(os.environ)
    threads = str(max(int(config.INFERENCE_NUM_THREADS), 1))
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = threads
    # transformers 分词器并行会产生大量子进程告警，对单条预测无收益。
    env["TOKENIZERS_PARALLELISM"] = "false"
    return env


def _lower_priority() -> None:
    """降低预测子进程调度优先级，避免导入和模型加载阶段抢占协调平面 CPU。"""
    nice = int(config.INFERENCE_NICE)
    if nice <= 0:
        return
    try:
        os.nice(nice)
    except (AttributeError, OSError):
        # 平台不支持谦让值时不影响预测本身。
        pass


def _host_available_mb() -> float | None:
    """读取宿主机可用内存（MB）；无 /proc 或读取失败时返回 None 表示未知。"""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            try:
                return int(parts[1]) / 1024.0
            except (IndexError, ValueError):
                return None
    return None


def ensure_free_memory(minimum_mb: int, purpose: str) -> None:
    """可用内存明显不足时在导入重量级依赖前失败，给出可操作原因而不是被 OOM 杀死。

    内存未知（无 /proc/meminfo）时不阻断预测：阈值是防冻结的保险丝，不是硬性配额。
    """
    available = _host_available_mb()
    if available is not None and available < float(minimum_mb):
        raise RuntimeError(
            f"预测可用内存不足（约 {available:.0f}MB 可用 < 需要 {minimum_mb}MB），"
            f"{purpose}可能因内存耗尽被终止；请选择资源更充足的计算槽"
        )


def _exit_reason(returncode: int, detail: str) -> str:
    """把子进程退出方式翻译为可操作原因；保留原始返回码便于排查。

    SIGKILL（-9）/SIGSEGV（-11）在小内存节点上最常见的来源是 OOM killer；
    stderr 中的分配失败信息同样指向内存不足，其余情况不做猜测。
    """
    if returncode in (-9, -11):
        return "：子进程被系统强制终止（常见于内存不足 OOM），请选择资源更充足的计算槽"
    if returncode < 0:
        return "：子进程被信号终止，请检查 Worker 资源与运行日志"
    lowered = detail.lower()
    if any(word in lowered for word in ("memoryerror", "cannot allocate memory", "std::bad_alloc", "out of memory")):
        return "：子进程输出显示内存分配失败，请选择资源更充足的计算槽"
    return ""


def _device_info(backend: str, caps: dict) -> dict:
    """解析 caps 中的设备编号并做真实设备检查（仅 GPU 与 torch 产物路径使用）。"""
    from app.device import device_info

    device_name = str(caps.get("device") or (caps.get("gpu") or {}).get("device") or "cpu")
    index = int(device_name.split(":")[-1]) if ":" in device_name else 0
    return device_info(backend, index)


def _reported_free_mb(worker: dict) -> float | None:
    """读取 Worker 最近心跳上报的可用内存；未上报时返回 None 表示未知。"""
    latest = ((worker.get("resources") or {}).get("latest") or {})
    value = latest.get("host_available_mb")
    return float(value) if isinstance(value, (int, float)) else None


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
        if not bool(worker.get("inference")):
            # 旧镜像 Worker 不消费预测队列，指派只会静默排队直至超时；
            # 提交阶段立即拒绝并给出可操作的修复指引。
            raise ValueError("所选预测 Worker 镜像过旧，不具备预测执行能力；请重新部署该服务器（同步代码）后再试")
        gpu = worker.get("gpu") or {}
        backend = worker.get("device_backend") or gpu.get("backend") or "cpu"
        if worker.get("state") in {"degraded", "recovering"} or gpu.get("healthy") is False:
            raise ValueError("所选预测 Worker 设备异常")
        if backend not in {"cpu", "cuda", "rocm", "xpu"}:
            raise ValueError("所选预测 Worker 计算后端不支持")
        if task == "clustering" and backend != "cpu":
            raise ValueError("KMeans 聚类预测仅支持 CPU")
        if backend != "cpu" and not gpu.get("available"):
            raise ValueError("所选预测 Worker 加速设备不可用")
        free_mb = _reported_free_mb(worker)
        if free_mb is not None and free_mb < config.INFERENCE_MIN_FREE_MEMORY_MB:
            raise ValueError(
                f"所选预测 Worker 可用内存不足（最近心跳约 {free_mb:.0f}MB < 最低 "
                f"{config.INFERENCE_MIN_FREE_MEMORY_MB}MB），请选择资源更充足的计算槽"
            )
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
        if not self.lease.acquire(inference_id, token, config.INFERENCE_LEASE_TTL):
            return
        try:
            record = self.get(inference_id)
            if record["status"] in TERMINAL:
                return
            queue = self.queue.inference_queue(record["worker_id"])
            if record["status"] == "running" or time.time() - record["created_at"] > QUEUE_TIMEOUT:
                if record["status"] == "running":
                    # 已运行时长帮助区分"模型加载慢但仍在跑"与"资源耗尽真的失联"。
                    elapsed = int(max(time.time() - float(record.get("started_at") or record["created_at"]), 0))
                    error = (f"预测执行租约丢失（运行 {elapsed} 秒后心跳中断），"
                             "通常为 Worker 资源不足或与协调平面失联，请重新提交")
                else:
                    error = "预测排队超时，请检查所选 Worker"
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
        # 预测子进程的导入/加载阶段可能长达数十秒，使用独立的更长租约，
        # 避免训练用的 30 秒 TTL 把"模型加载慢"误判为"Worker 失联"。
        ttl = config.INFERENCE_LEASE_TTL
        if not lease.acquire(inference_id, token, ttl):
            return
        slot = slot_key(worker.worker_id)
        controller = JobController(lease, inference_id, token, worker.worker_id,
                                   config.INFERENCE_HEARTBEAT_INTERVAL, ttl)
        started = False
        try:
            record = self.store.read_json(inference_key(inference_id))
            if not record or self.get(inference_id)["status"] != "queued":
                return
            if record["worker_id"] != worker.worker_id:
                self.queue.inference_queue(record["worker_id"]).push(inference_id)
                return
            if not lease.acquire(slot, token, ttl):
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


_STDOUT_TAIL_LIMIT = 1500


def _stderr_tail(path: Path, limit: int = _STDOUT_TAIL_LIMIT) -> str:
    """读取子进程错误输出尾部，供失败原因展示；读取失败不影响原始异常。"""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(size - limit, 0))
            return handle.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def run_prediction_process(record: dict, caps: dict, store, controller, shutdown) -> dict:
    """将下载、模型加载和计算全部纳入超时，关闭子进程后清理私有临时目录。

    子进程的 stderr 写入私有临时文件并在失败时附到异常信息上：预测进程常常
    在导入阶段就被 OOM 杀掉（返回码 -9），此时结果文件不会生成，错误输出是
    唯一能说明真实原因的线索；直接丢弃 stderr 会让用户只看到笼统的失败。

    轮询循环只读取本地租约标志，不再每 50ms 续租一次：续租由心跳线程按周期
    完成并带失联宽限，忙等续租反而会在资源紧张时放大协调平面压力，一次瞬时
    Redis 超时就足以把预测误杀（小规格节点实测 3/3 次失败的直接触发点）。
    """
    from app.worker import JobLeaseLost

    config.WORKER_SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="inference-", dir=config.WORKER_SCRATCH_DIR) as directory:
        root = Path(directory)
        request = {**record, "caps": caps,
                   "local_store": str(store.root) if isinstance(store, LocalArtifactStore) else None}
        request_path, output_path = root / "request.json", root / "result.json"
        stderr_path = root / "stderr.log"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        try:
            with stderr_path.open("wb") as stderr_log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "app.inference", str(request_path), str(output_path)],
                    stdout=subprocess.DEVNULL, stderr=stderr_log,
                    env=prediction_environment(),
                )
                deadline = time.monotonic() + record["timeout"]
                try:
                    while process.poll() is None:
                        # 租约丢失由心跳线程判定并置位；此处只读本地标志。
                        lease_lost = getattr(controller, "lease_lost", None)
                        if lease_lost is not None and lease_lost.is_set():
                            raise JobLeaseLost("预测执行期间租约已失效，停止写入并等待 watchdog 接续")
                        if shutdown.is_set() or controller.pause_event.is_set():
                            raise RuntimeError("预测 Worker 已停止")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("预测执行超时（包含模型下载和加载）")
                        time.sleep(0.05)
                    # 写结果前的原子核对保留：失租后绝不覆盖新执行者的状态。
                    controller.ensure_owned()
                    if not output_path.exists():
                        detail = _stderr_tail(stderr_path)
                        raise RuntimeError(
                            f"预测子进程异常退出（返回码 {process.returncode}）"
                            f"{_exit_reason(process.returncode, detail)}"
                        )
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
        except (RuntimeError, TimeoutError) as exc:
            # 只补充可读上下文；JobLeaseLost 等控制流异常保持原样向上传播。
            detail = _stderr_tail(stderr_path)
            if detail and detail not in str(exc):
                raise type(exc)(f"{exc}；子进程错误输出：{detail}") from exc
            raise


def predict_request(request: dict, root: Path) -> dict:
    """在所选 Worker 的真实设备上预测，保留训练保存的预处理与模型。

    先读取产物再决定加载路径：pickle 传统模型（sklearn/xgboost）全程不导入
    torch 与设备层（torch 常驻约 490MB，是小内存计算槽被拖垮的主因）；只有
    确认是 torch 产物（MLP/KMeans/时序/LLM）时才加载设备层与训练器。
    GPU 槽位仍先校验真实设备健康，任何预测都不静默改用 CPU。
    """
    from app import predictor

    caps = request["caps"]
    gpu = caps.get("gpu") or {}
    backend = caps.get("device_backend") or gpu.get("backend") or "cpu"
    llm = str(request.get("task", "")).startswith("llm_")

    info = None
    if backend != "cpu":
        info = _device_info(backend, caps)
        if not info["healthy"]:
            raise RuntimeError("所选设备健康检查失败，未切换计算资源")

    store = LocalArtifactStore(Path(request["local_store"])) if request.get("local_store") else get_store()
    artifact = "best_model.zip" if llm else "best.pt"
    path = store.read_file(job_key(request["job_id"], artifact), root / artifact)
    payload = request["payload"]

    if not llm and not predictor.is_torch_artifact(path):
        # 传统模型（pickle 产物）：CPU 推理，不导入 torch，内存门槛也低得多。
        if backend != "cpu":
            raise ValueError("传统机器学习模型预测仅支持 CPU")
        ensure_free_memory(config.INFERENCE_MIN_FREE_MEMORY_MB, "传统模型预测")
        result = predictor.predict_row(path, device="cpu", **payload)
        return {"result": result, "device": "cpu", "device_backend": "cpu", "device_name": "CPU"}

    if info is None:
        info = _device_info("cpu", caps)
        if not info["healthy"]:
            raise RuntimeError("所选设备健康检查失败，未切换计算资源")
    ensure_free_memory(config.INFERENCE_TORCH_MIN_FREE_MEMORY_MB, "torch 模型预测")
    import torch

    # 单条预测开多线程无收益，反而会在小规格节点上抢占协调平面 CPU。
    torch.set_num_threads(max(int(config.INFERENCE_NUM_THREADS), 1))
    if llm:
        from app.llm_trainer import generate_text

        result = {"prompt": payload["prompt"],
                  "generated_text": generate_text(path, device=info["device"], **payload)}
    else:
        result = predictor.predict_row(path, device=info["device"], **payload)
    return {"result": result, "device": info["device"], "device_backend": info["backend"],
            "device_name": info["name"]}


def main() -> int:
    """子进程只读取服务生成的请求文件，错误通过私有结果文件交回父进程。"""
    _lower_priority()
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
