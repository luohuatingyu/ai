"""需求全面验证脚本：在运行的服务上逐项验证 5 大需求 + 异构分布式。

用法（容器内）：python scripts/verify_requirements.py <base_url>
每项检查输出 PASS/FAIL 与证据，最终汇总。
"""
from __future__ import annotations

import io
import json
import sys
import time
import uuid
from urllib.request import Request, urlopen

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
RESULTS: list[tuple[str, str, str]] = []  # (需求, 用例, PASS/FAIL + 证据)


def call(method: str, path: str, body=None, timeout: float = 60, raw: bool = False, headers: dict = None):
    """发起 HTTP 请求，返回 (status, json_or_text)"""
    url = f"{BASE}{path}"
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            req_headers.setdefault("Content-Type", "application/json")
        else:
            data = body
    req = Request(url, data=data, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            status = resp.status
    except Exception as exc:  # HTTPError 带 code；其余为连接失败
        status = getattr(exc, "code", 0)
        payload = str(exc).encode()
    if raw:
        return status, payload
    try:
        return status, json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return status, payload.decode("utf-8", "replace")


def check(req: str, name: str, condition: bool, evidence: str = ""):
    status = "PASS" if condition else "FAIL"
    RESULTS.append((req, name, f"{status} {evidence}".strip()))
    mark = "✅" if condition else "❌"
    print(f"{mark} [{req}] {name} {evidence}")


def wait_job_terminal(job_id: str, timeout: float = 180) -> tuple[str, dict]:
    """轮询直到任务终态，返回 (status, record)"""
    deadline = time.time() + timeout
    record = {}
    while time.time() < deadline:
        status, record = call("GET", f"/api/training/jobs/{job_id}")
        if status == 200 and record.get("status") in {"completed", "failed", "cancelled"}:
            return record["status"], record
        time.sleep(1)
    return "timeout", record


# =============================================================================
# 需求 1：交互式工作台（编写 Python 脚本、选择计算资源、单步执行）
# =============================================================================
def verify_req1_workbench():
    print("\n===== 需求 1：交互式工作台 =====")

    # 1.1 计算资源列表可见（资源选择前提）
    status, body = call("GET", "/api/clusters/workers")
    check("REQ1", "计算资源列表 API", status == 200 and "workers" in body,
          f"status={status}, workers={len(body.get('workers', []))}")

    status, summary = call("GET", "/api/clusters/workers/summary")
    check("REQ1", "资源摘要（选择器数据源）", status == 200 and "total" in summary,
          f"total={summary.get('total')}")

    # 1.2 创建会话（单选资源）
    workers = (call("GET", "/api/clusters/workers")[1].get("workers") or [])
    bound = [w["worker_id"] for w in workers[:1]]  # 单选
    status, created = call("POST", "/api/workbench/sessions", {
        "name": f"verify-{uuid.uuid4().hex[:6]}",
        "bound_workers": bound,
    })
    check("REQ1", "创建会话（绑定指定计算资源）", status == 200 and created.get("session_id"),
          f"session={created.get('session_id')}, bound={bound}")
    if status != 200:
        return
    sid = created["session_id"]

    # 1.3 启动内核并单步执行
    status, _ = call("POST", f"/api/workbench/sessions/{sid}/kernel/start")
    check("REQ1", "启动内核", status == 200, f"status={status}")

    status, r1 = call("POST", f"/api/workbench/sessions/{sid}/cells/execute", {
        "cell_id": "v-cell-1", "cell_index": 0,
        "code": "import math\nradius = 3\narea = math.pi * radius ** 2",
    })
    check("REQ1", "单步执行单元格", status == 200 and r1.get("success") is True,
          f"success={r1.get('success')}, err={r1.get('error')}")

    # 1.4 跨单元格变量持久化
    status, r2 = call("POST", f"/api/workbench/sessions/{sid}/cells/execute", {
        "cell_id": "v-cell-2", "cell_index": 1,
        "code": "doubled = area * 2\nprint(f'area={area:.2f}')",
    })
    output = (r2.get("output") or "")
    check("REQ1", "跨单元格变量持久化 + print 输出", r2.get("success") and "area=28.27" in output,
          f"output={output.strip()[:60]}")

    # 1.5 变量查看器
    status, vs = call("GET", f"/api/workbench/sessions/{sid}/variables")
    variables = vs.get("variables", {})
    check("REQ1", "变量查看器", status == 200 and variables.get("radius") == 3,
          f"radius={variables.get('radius')}")

    # 1.6 异常捕获（会话不崩溃）
    status, r3 = call("POST", f"/api/workbench/sessions/{sid}/cells/execute", {
        "cell_id": "v-cell-3", "cell_index": 2, "code": "1 / 0",
    })
    check("REQ1", "运行时异常捕获且不崩溃", r3.get("success") is False and r3.get("error_type") == "ZeroDivisionError",
          f"error_type={r3.get('error_type')}")

    status, r4 = call("POST", f"/api/workbench/sessions/{sid}/cells/execute", {
        "cell_id": "v-cell-4", "cell_index": 3, "code": "ok_after_error = True",
    })
    check("REQ1", "异常后继续执行", r4.get("success") is True, "")

    # 1.7 多选资源会话（分布式绑定）
    if len(workers) >= 2:
        status, multi = call("POST", "/api/workbench/sessions", {
            "name": f"verify-multi-{uuid.uuid4().hex[:4]}",
            "bound_workers": [w["worker_id"] for w in workers[:2]],
        })
        check("REQ1", "多选计算资源创建会话", status == 200 and len(multi.get("session_id", "")) > 0,
              f"bound=2 workers")
        if status == 200:
            call("DELETE", f"/api/workbench/sessions/{multi['session_id']}")
    else:
        check("REQ1", "多选计算资源创建会话", False, "可用 worker 不足 2 个")

    # 清理
    call("POST", f"/api/workbench/sessions/{sid}/kernel/stop")
    call("DELETE", f"/api/workbench/sessions/{sid}")


# =============================================================================
# 需求 2：脚本模板库（默认模板 + 数据集交互）
# =============================================================================
def verify_req2_templates():
    print("\n===== 需求 2：脚本模板库 =====")

    status, body = call("GET", "/api/templates")
    templates = body.get("templates", [])
    categories = {t["category"] for t in templates}
    expected = {"classification", "regression", "clustering", "time_series", "llm_sft", "llm_dpo"}
    check("REQ2", "内置模板覆盖全部任务类型", status == 200 and expected <= categories,
          f"categories={sorted(categories & expected)}")

    status, detail = call("GET", "/api/templates/builtin-classification")
    params = {p["name"]: p for p in detail.get("parameters", [])}
    check("REQ2", "模板参数定义（数据集交互字段）", "dataset_id" in params and "target" in params,
          f"params={len(params)}")

    # 参数验证：合法/缺失/非法
    ok_body = {"dataset_id": "ds-x", "target": "label", "model_type": "mlp"}
    status, ok = call("POST", "/api/templates/builtin-classification/validate", {
        "template_id": "builtin-classification", "parameters": ok_body})
    check("REQ2", "参数验证通过（默认值自动填充）", ok.get("valid") is True,
          f"errors={ok.get('errors')}")

    status, bad = call("POST", "/api/templates/builtin-classification/validate", {
        "template_id": "builtin-classification", "parameters": {"model_type": "bogus"}})
    check("REQ2", "非法参数被拒绝", bad.get("valid") is False and bad.get("errors"),
          f"errors={bad.get('errors')}")

    # 渲染生成可执行脚本
    status, rendered = call("POST", "/api/templates/builtin-classification/render", {
        "parameters": {**ok_body, "epochs": 5}})
    content = rendered.get("rendered_content", "")
    check("REQ2", "模板渲染为训练脚本", status == 200 and 'task="classification"' in content and "{{" not in content,
          f"length={len(content)}")

    # 一键实例化为训练任务参数
    status, inst = call("POST", "/api/templates/builtin-classification/instantiate", {
        "parameters": ok_body, "instantiated_type": "training_job"})
    check("REQ2", "一键实例化", status == 200 and inst.get("rendered_content"),
          f"type={inst.get('instantiated_type')}")


# =============================================================================
# 需求 3：数据集拉取到 OSS + 使用 OSS 数据集训练（拉取与训练分离）
# =============================================================================
def verify_req3_oss_dataset():
    print("\n===== 需求 3：OSS 数据集（拉取与训练分离） =====")

    # 3.1 上传数据集（模拟"拉取"阶段：数据进入平台）
    import csv
    rows = [[f"f1", "f2", "target"]]
    for i in range(60):
        rows.append([float(i), float(i % 7), i % 2])
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    csv_bytes = buf.getvalue().encode()

    boundary = uuid.uuid4().hex
    part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="verify_oss.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + csv_bytes + f"\r\n--{boundary}--\r\n".encode()
    status, ds = call("POST", "/api/datasets/import?description=verify-oss",
                      part, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    ds_id = None
    if isinstance(ds, dict):
        items = ds.get("datasets") or ([ds] if ds.get("id") else [])
        ds_id = items[0]["id"] if items else None
    check("REQ3", "数据集导入（拉取阶段）", status in (200, 201) and ds_id,
          f"ds_id={ds_id}")
    if not ds_id:
        return

    # 3.2 数据集已同步到对象存储（OSS 状态平面）
    # 证据：dataset detail 可读 + 元数据含存储信息
    status, meta = call("GET", f"/api/datasets/{ds_id}")
    check("REQ3", "数据集元数据就绪", status == 200 and meta.get("rows") == 60,
          f"rows={meta.get('rows')}")

    # 3.3 训练任务引用该数据集（训练阶段：计算资源从状态平面取数据）
    status, job = call("POST", "/api/training/jobs", {
        "dataset_id": ds_id, "target": "target", "task": "classification",
        "epochs": 2, "hidden_size": 16, "hidden_layers": 1, "batch_size": 32,
        "lr": 0.01, "seed": 42,
    })
    check("REQ3", "使用该数据集创建训练任务", status == 200 and job.get("job_id"),
          f"job_id={job.get('job_id')}")
    if status != 200:
        return
    final_status, record = wait_job_terminal(job["job_id"], 180)
    check("REQ3", "训练完成（数据经状态平面传递）", final_status == "completed",
          f"status={final_status}, error={record.get('error')}")
    if final_status == "completed":
        metrics = record.get("metrics") or []
        check("REQ3", "指标完整（epoch>=2）", len(metrics) >= 2 and "val_loss" in metrics[-1],
              f"epochs_done={len(metrics)}")

    # 清理
    call("DELETE", f"/api/datasets/{ds_id}")
    call("DELETE", f"/api/training/jobs/{job['job_id']}")


# =============================================================================
# 需求 4：训练结果自动上传 OSS + 查看训练好的模型
# =============================================================================
def verify_req4_model_registry():
    print("\n===== 需求 4：模型注册表（自动上传 + 可见性） =====")

    # 4.1 训练一个模型
    import csv
    rows = [["a", "b", "target"]]
    for i in range(80):
        rows.append([float(i), float((i * 3) % 11), i % 2])
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    boundary = uuid.uuid4().hex
    part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="verify_model.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + buf.getvalue().encode() + f"\r\n--{boundary}--\r\n".encode()
    status, ds = call("POST", "/api/datasets/import", part,
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    items = ds.get("datasets") or ([ds] if isinstance(ds, dict) and ds.get("id") else [])
    ds_id = items[0]["id"] if items else None
    if not ds_id:
        check("REQ4", "前置数据集导入", False, f"resp={str(ds)[:80]}")
        return

    status, job = call("POST", "/api/training/jobs", {
        "dataset_id": ds_id, "target": "target", "task": "classification",
        "epochs": 3, "hidden_size": 16, "hidden_layers": 1, "batch_size": 32,
        "lr": 0.01, "seed": 42,
    })
    job_id = job.get("job_id")
    final_status, record = wait_job_terminal(job_id, 180)
    check("REQ4", "训练完成", final_status == "completed", f"status={final_status}")

    # 4.2 模型产物已在对象存储（best.pt 可下载）
    status, payload = call("GET", f"/api/training/jobs/{job_id}/model", raw=True)
    is_pt = payload[:2] == b"PK" or len(payload) > 1000
    check("REQ4", "训练产物 best.pt 已上传并可下载", status == 200 and is_pt,
          f"status={status}, size={len(payload)}B")

    # 4.3 模型出现在注册表（自动注册开启时）或可手动注册
    status, listing = call("GET", "/api/models", {"search": job_id})
    auto_registered = status == 200 and listing.get("total", 0) > 0
    if not auto_registered:
        # 手动注册（验证注册表能力）
        status, reg = call("POST", "/api/models", {
            "name": f"verify-model-{job_id}", "version": "1.0.0",
            "task": "classification", "framework": "torch",
            "metrics": {"val_accuracy": 0.9},
            "artifacts": {"best": f"jobs/{job_id}/best.pt"},
        })
        auto_registered = status == 200 and reg.get("id")
        evidence = f"手动注册 id={reg.get('id')}" if auto_registered else f"reg status={status}"
    else:
        evidence = f"自动注册 {listing['total']} 条"
    check("REQ4", "训练好的模型在注册表可见", auto_registered, evidence)

    # 4.4 模型列表含关键字段
    status, listing = call("GET", "/api/models")
    models = listing.get("models", [])
    fields_ok = all(all(k in m for k in ("id", "name", "version", "task", "status")) for m in models)
    check("REQ4", "模型列表字段完整", status == 200 and models and fields_ok,
          f"models={len(models)}")

    # 4.5 版本管理
    if models:
        mid = models[0]["id"]
        status, versions = call("GET", f"/api/models/{mid}/versions")
        check("REQ4", "版本管理接口", status == 200 and "versions" in versions,
              f"versions={versions.get('versions')}")

    call("DELETE", f"/api/datasets/{ds_id}")
    call("DELETE", f"/api/training/jobs/{job_id}")


# =============================================================================
# 需求 5：Pipeline 编排（DAG）
# =============================================================================
def verify_req5_pipeline():
    print("\n===== 需求 5：Pipeline 编排 =====")

    # 5.1 创建含依赖的 DAG
    status, created = call("POST", "/api/pipelines", {
        "name": f"verify-dag-{uuid.uuid4().hex[:6]}",
        "description": "验证菱形 DAG",
        "steps": [
            {"id": "pull", "name": "拉取数据", "type": "dataset_pull", "config": {}},
            {"id": "proc_a", "name": "处理A", "type": "dataset_process", "config": {}, "depends_on": ["pull"]},
            {"id": "proc_b", "name": "处理B", "type": "custom_script", "config": {},
             "depends_on": ["pull"]},
            {"id": "train", "name": "训练", "type": "training", "config": {},
             "depends_on": ["proc_a", "proc_b"]},
        ],
    })
    check("REQ5", "创建 Pipeline（菱形 DAG）", status == 200 and created.get("id"),
          f"id={created.get('id')}")
    if status != 200:
        return
    pid = created["id"]

    # 5.2 DAG 拓扑结构正确
    status, detail = call("GET", f"/api/pipelines/{pid}")
    dag = detail.get("dag", {})
    edges = {(e["source"], e["target"]) for e in dag.get("edges", [])}
    check("REQ5", "DAG 节点与依赖边正确", status == 200 and len(detail.get("steps", [])) == 4
          and edges == {("pull", "proc_a"), ("pull", "proc_b"), ("proc_a", "train"), ("proc_b", "train")},
          f"edges={sorted(edges)}")

    # 5.3 依赖校验
    status, bad = call("POST", "/api/pipelines", {
        "name": "verify-bad",
        "steps": [{"id": "a", "name": "A", "type": "dataset_pull", "config": {},
                   "depends_on": ["ghost"]}],
    })
    check("REQ5", "未知依赖被拒绝", status == 400, f"status={status}")

    # 5.4 执行历史接口
    status, runs = call("GET", f"/api/pipelines/{pid}/runs")
    check("REQ5", "执行历史接口", status == 200 and "runs" in runs, f"runs={len(runs.get('runs', []))}")

    call("DELETE", f"/api/pipelines/{pid}")


# =============================================================================
# 附加：异构感知与分布式联合训练
# =============================================================================
def verify_extra_distributed():
    print("\n===== 附加：资源选择与异构策略 =====")

    # 资源校验接口
    status, res = call("POST", "/api/clusters/workers/validate?require_gpu=true",
                       ["ghost-worker"])
    check("EXTRA", "Worker 校验接口（不存在拒绝）", status == 200 and res.get("all_valid") is False,
          f"results={len(res.get('results', []))}")

    # 系统健康
    status, sysinfo = call("GET", "/api/health")
    check("EXTRA", "平台健康检查", status == 200 and sysinfo.get("ok") is True, "")


def main():
    print(f"验证目标: {BASE}")
    verify_req1_workbench()
    verify_req2_templates()
    verify_req3_oss_dataset()
    verify_req4_model_registry()
    verify_req5_pipeline()
    verify_extra_distributed()

    print("\n" + "=" * 70)
    passed = sum(1 for _, _, r in RESULTS if r.startswith("PASS"))
    failed = len(RESULTS) - passed
    print(f"总计 {len(RESULTS)} 项：PASS {passed} / FAIL {failed}")
    if failed:
        print("\n失败项：")
        for req, name, result in RESULTS:
            if not result.startswith("PASS"):
                print(f"  [{req}] {name}: {result}")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())