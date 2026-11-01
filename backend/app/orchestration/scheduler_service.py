"""Pipeline 定时调度服务：独立进程入口。

职责：
- 启动 PipelineScheduler，轮询数据库中带 schedule 的 active Pipeline
- 按 cron 间隔触发 PipelineEngine.execute
- 与 API 进程解耦：API 只负责 CRUD，调度由本进程负责
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import app.config as config
from app.database import db_session
from app.db_utils import list_pipelines
from app.orchestration.engine import PipelineEngine, PipelineScheduler
from app.orchestration.models import Pipeline, TriggerType
from app.storage import get_store
from app.coordination import build_coordination


class SchedulerService:
    """定时调度服务主循环"""

    def __init__(self, poll_interval: float = 30.0):
        self.poll_interval = max(float(poll_interval), 10.0)
        self.engine = PipelineEngine(store=get_store())
        queue, lease, _ = build_coordination()
        self.engine.lease_store = lease
        self.scheduler = PipelineScheduler(self.engine)
        self._stop = threading.Event()
        self._last_run_at: dict[str, float] = {}  # pipeline_id -> 上次触发时间
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """启动调度循环"""
        self._thread = threading.Thread(target=self._loop, name="pipeline-scheduler", daemon=True)
        self._thread.start()
        print("Pipeline 调度服务已启动", flush=True)

    def stop(self):
        """停止调度服务"""
        self._stop.set()
        self.scheduler.shutdown()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        """主循环：轮询带 schedule 的 active Pipeline 并触发"""
        while not self._stop.wait(self.poll_interval):
            try:
                self._poll_once()
            except Exception as exc:  # 调度异常不得中断服务
                print(f"调度轮询异常: {exc}", flush=True)

    def _poll_once(self):
        """一轮轮询：检查所有 active 且有 schedule 的 Pipeline"""
        with db_session() as db:
            pipelines, _ = list_pipelines(db, limit=1000, status="active")

        now = time.time()
        for record in pipelines:
            schedule = record.schedule
            if not schedule:
                continue
            interval = self._parse_schedule_interval(schedule)
            if interval is None:
                continue
            pipeline_id = record.id
            last = self._last_run_at.get(pipeline_id, 0)
            if now - last >= interval:
                self._last_run_at[pipeline_id] = now
                pipeline = self._to_pipeline(record)
                try:
                    run = self.engine.execute(pipeline, TriggerType.SCHEDULED)
                    print(
                        f"已触发 Pipeline {record.name}({pipeline_id}) -> run={run.id}",
                        flush=True,
                    )
                except Exception as exc:
                    print(f"触发 Pipeline {pipeline_id} 失败: {exc}", flush=True)

    @staticmethod
    def _parse_schedule_interval(schedule: str) -> Optional[float]:
        """解析调度表达式为最小间隔秒数。

        支持两种格式：
        - "@every <seconds>"：固定间隔
        - 5 段 cron：解析最小粒度字段（分/时/日/月/周）估算触发间隔
        """
        schedule = (schedule or "").strip()
        if schedule.startswith("@every "):
            try:
                return float(schedule[len("@every "):])
            except ValueError:
                return None
        # 5 段 cron：以分钟为最小单位
        fields = schedule.split()
        if len(fields) != 5:
            return None
        # 简化：若分钟字段是 */N 或数字，N 分钟触发一次
        minute_field = fields[0]
        if minute_field.startswith("*/"):
            try:
                return float(minute_field[2:]) * 60
            except ValueError:
                return None
        if minute_field.isdigit():
            return float(minute_field) * 60
        return 3600.0  # 默认每小时检查一次

    @staticmethod
    def _to_pipeline(record) -> Pipeline:
        """DB 记录转 Pipeline 对象"""
        from app.orchestration.models import PipelineStep

        steps = []
        for s in record.steps or []:
            steps.append(PipelineStep(**s))
        return Pipeline(
            id=record.id,
            name=record.name,
            description=record.description or "",
            steps=steps,
            schedule=record.schedule,
        )


def main():
    """调度服务入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline 定时调度服务")
    parser.add_argument("--poll-interval", type=float, default=30.0)
    args = parser.parse_args()

    service = SchedulerService(poll_interval=args.poll_interval)
    service.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        service.stop()
        print("调度服务已停止", flush=True)


if __name__ == "__main__":
    main()