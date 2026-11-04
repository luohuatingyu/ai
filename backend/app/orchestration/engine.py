"""Pipeline DAG 执行引擎"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import app.config as config
from app.storage import ArtifactStore, get_store
from app.orchestration.models import (
    Pipeline, PipelineRun, PipelineStep, PipelineStepRun,
    StepType, RunStatus, TriggerType, RetryPolicy,
)
from app.training.core import TrainingSpec, TrainingContext, run_training
from app.compute.session_manager import get_session_manager
from app.compute.resource_selector import ResourceSelector
from app.coordination import LeaseStore
from app.db_utils import (
    create_pipeline_run,
    update_pipeline_run,
    get_pipeline_run,
    create_pipeline_step_run,
    update_pipeline_step_run,
    get_pipeline_step_runs,
)
from app.database import db_session


@dataclass
class StepExecutor:
    """步骤执行器基类"""
    step: PipelineStep
    run: PipelineRun
    store: ArtifactStore
    lease_store: LeaseStore
    log_fn: Callable[[str], None]
    
    def execute(self) -> Dict[str, str]:
        """执行步骤，返回输出产物映射"""
        raise NotImplementedError


class DatasetPullExecutor(StepExecutor):
    """数据集拉取执行器"""
    
    def execute(self) -> Dict[str, str]:
        from app.dataset_manager import DatasetManager
        from app.config import DATASETS_DIR
        
        cfg = self.step.config
        dataset_id = cfg.get("dataset_id")
        source = cfg.get("source", "local")  # local/huggingface/oss
        storage_mode = cfg.get("storage_mode", "local")  # local/oss
        
        self.log_fn(f"[Pull] 拉取数据集: {dataset_id}, source={source}, mode={storage_mode}")
        
        dm = DatasetManager(DATASETS_DIR)
        
        if source == "huggingface":
            repo_id = cfg["repo_id"]
            revision = cfg.get("revision", "main")
            subset = cfg.get("subset")
            split = cfg.get("split", "train")
            
            metadata = dm.register_hub_dataset(
                repo_id=repo_id, revision=revision, subset=subset,
                split=split, storage_mode=storage_mode
            )
            metadata = dm.ensure_local(metadata["id"])
        
        elif source == "oss":
            # 从 OSS 下载
            pass
        
        # 同步到 OSS
        from app.routers.common import _sync_dataset_csv_to_store
        _sync_dataset_csv_to_store(dataset_id)
        
        return {"dataset_csv": f"datasets/{dataset_id}.csv", "dataset_id": dataset_id}


class DatasetProcessExecutor(StepExecutor):
    """数据预处理执行器"""
    
    def execute(self) -> Dict[str, str]:
        from app.data_processor import DataProcessor
        from app.dataset_manager import DatasetManager
        from app.config import DATASETS_DIR
        
        cfg = self.step.config
        dataset_id = cfg.get("dataset_id")
        operations = cfg.get("operations", [])
        
        self.log_fn(f"[Process] 预处理数据集: {dataset_id}, 操作数={len(operations)}")
        
        dm = DatasetManager(DATASETS_DIR)
        metadata = dm.ensure_local(dataset_id)
        
        # 读取原始数据
        source_candidates = [
            DATASETS_DIR / "raw" / f"{dataset_id}.csv",
            DATASETS_DIR / dataset_id / f"{dataset_id}.csv",
            DATASETS_DIR / f"{dataset_id}.csv",
        ]
        source_path = next((p for p in source_candidates if p.exists()), None)
        if not source_path:
            raise FileNotFoundError(f"数据集文件不存在: {dataset_id}")
        
        import pandas as pd
        df = pd.read_csv(source_path)
        
        # 执行预处理
        processor = DataProcessor()
        processed_df = processor.process_dataset(df, operations)
        
        # 保存
        processed_path = DATASETS_DIR / "processed" / f"{dataset_id}.csv"
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        processed_df.to_csv(processed_path, index=False)
        
        # 更新元数据
        metadata["rows"] = len(processed_df)
        metadata["columns"] = list(processed_df.columns)
        metadata["processed_at"] = time.time()
        
        from app.routers.common import _write_metadata, _sync_dataset_csv_to_store
        _write_metadata(dataset_id, metadata)
        _sync_dataset_csv_to_store(dataset_id)
        
        return {"processed_csv": f"datasets/{dataset_id}.csv", "dataset_id": dataset_id}


class TrainingExecutor(StepExecutor):
    """训练执行器"""
    
    def execute(self) -> Dict[str, str]:
        cfg = self.step.config
        spec_dict = cfg.get("spec", {})
        resource_hint = cfg.get("resource_hint", {})
        
        spec = TrainingSpec(**spec_dict)
        
        self.log_fn(f"[Train] 开始训练: task={spec.task}, target={spec.target}")
        
        # 选择计算资源
        selector = ResourceSelector(self.lease_store)
        worker_ids, strategy = selector.select_workers(resource_hint)
        
        self.log_fn(f"[Train] 选中 Worker: {worker_ids}, 策略: {strategy.mode.value}")
        
        # 准备数据集
        dataset_id = spec.dataset_id
        dataset_path = self.store.read_file(
            f"datasets/{dataset_id}.csv", 
            Path(config.WORKER_SCRATCH_DIR) / dataset_id / f"{dataset_id}.csv"
        )
        
        # 创建训练上下文
        context = TrainingContext(
            job_id=self.run.id,
            job_dir=Path(config.WORKER_SCRATCH_DIR) / self.run.id,
            store=self.store,
            dataset_path=dataset_path,
            device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
        )
        
        # 如果是分布式
        if len(worker_ids) > 1:
            from app.training.distributed import LocalDistributedLauncher
            launcher = LocalDistributedLauncher()
            summary = launcher.launch(
                job_id=self.run.id,
                spec=spec,
                strategy=strategy,
                dataset_path=str(dataset_path),
                store=self.store,
                log_fn=self.log_fn,
            )
        else:
            summary = run_training(spec, context, log_fn=self.log_fn)
        
        # 自动注册模型
        if config.MODEL_AUTO_REGISTER:
            from app.registry.model_registry import ModelRegistry
            registry = ModelRegistry(self.store, db_session())
            model = registry.register_from_training(
                job_id=self.run.id,
                summary=summary,
                name=cfg.get("model_name", f"{spec.task}-{dataset_id}"),
                tags=cfg.get("tags", []),
            )
            self.log_fn(f"[Train] 模型已注册: {model.id}")
            return {
                "model_id": model.id,
                "model_version": model.version,
                "model_path": summary.model_path,
                "summary_path": summary.summary_path,
            }
        
        return {
            "model_path": summary.model_path,
            "summary_path": summary.summary_path,
        }


class ModelUploadExecutor(StepExecutor):
    """模型上传/注册执行器"""
    
    def execute(self) -> Dict[str, str]:
        from app.registry.model_registry import ModelRegistry
        
        cfg = self.step.config
        source_step_id = cfg.get("source_step_id")  # 来自训练步骤的产物
        
        # 从上游步骤获取产物
        artifacts = self.run.artifact_map.get(source_step_id, {})
        model_path = artifacts.get("model_path")
        summary_path = artifacts.get("summary_path")
        
        if not model_path:
            raise ValueError(f"上游步骤 {source_step_id} 没有产出模型路径")
        
        self.log_fn(f"[Upload] 注册模型: {model_path}")
        
        registry = ModelRegistry(self.store, db_session())
        model = registry.register_from_training(
            job_id=self.run.id,
            summary=json.loads(self.store.read_text(summary_path) or "{}"),
            name=cfg.get("name"),
            tags=cfg.get("tags", []),
            description=cfg.get("description", ""),
        )
        
        return {
            "model_id": model.id,
            "model_version": model.version,
        }


class ModelDeployExecutor(StepExecutor):
    """模型部署执行器"""
    
    def execute(self) -> Dict[str, str]:
        from app.registry.model_registry import ModelRegistry
        
        cfg = self.step.config
        model_id = cfg.get("model_id")
        model_version = cfg.get("model_version")
        replicas = cfg.get("replicas", config.MODEL_DEPLOY_DEFAULT_REPLICAS)
        
        self.log_fn(f"[Deploy] 部署模型: {model_id} v{model_version}, replicas={replicas}")
        
        registry = ModelRegistry(self.store, db_session())
        deployment = registry.deploy(model_id, model_version, {"replicas": replicas})
        
        return {
            "deployment_id": deployment.id,
            "endpoint_url": deployment.endpoint_url,
        }


class CustomScriptExecutor(StepExecutor):
    """自定义脚本执行器"""
    
    def execute(self) -> Dict[str, str]:
        from app.compute.kernel_executor import KernelExecutor
        
        cfg = self.step.config
        script = cfg.get("script", "")
        session_id = cfg.get("session_id", f"pipeline-{self.run.id}-{self.step.id}")
        
        self.log_fn(f"[Script] 执行自定义脚本: {session_id}")
        
        kernel = KernelExecutor(session_id=session_id)
        result = kernel.execute(script)
        
        if not result.success:
            raise RuntimeError(f"脚本执行失败: {result.error}")
        
        return {"output": result.output, "variables": result.variables}


class NotebookRunExecutor(StepExecutor):
    """Notebook 执行器"""
    
    def execute(self) -> Dict[str, str]:
        from app.compute.session_manager import get_session_manager
        
        cfg = self.step.config
        session_id = cfg.get("session_id")
        
        if not session_id:
            raise ValueError("Notebook 步骤需要指定 session_id")
        
        manager = get_session_manager()
        session = manager.get_session(session_id)
        
        if not session:
            raise ValueError(f"Notebook 会话不存在: {session_id}")
        
        if not session.kernel:
            manager.start_kernel(session_id)
        
        # 执行所有单元格
        cells = manager.get_cells(session_id)
        results = []
        
        for cell in cells:
            if cell["cell_type"] == "code" and cell["source"]:
                result = session.kernel.execute(cell["source"])
                results.append({
                    "cell_index": cell["cell_index"],
                    "success": result.success,
                    "output": result.output,
                })
                if not result.success:
                    raise RuntimeError(f"单元格 {cell['cell_index']} 执行失败: {result.error}")
        
        return {"executed_cells": len(results), "results": results}


class InferenceExecutor(StepExecutor):
    """批量推理执行器"""
    
    def execute(self) -> Dict[str, str]:
        from app.inference import InferenceService
        
        cfg = self.step.config
        model_id = cfg.get("model_id")
        input_dataset_id = cfg.get("input_dataset_id")
        output_path = cfg.get("output_path", f"inference/{self.run.id}/predictions.csv")
        
        self.log_fn(f"[Inference] 批量推理: model={model_id}, input={input_dataset_id}")
        
        # 读取输入数据
        dataset_path = self.store.read_file(
            f"datasets/{input_dataset_id}.csv",
            Path(config.WORKER_SCRATCH_DIR) / input_dataset_id / f"{input_dataset_id}.csv"
        )
        
        import pandas as pd
        df = pd.read_csv(dataset_path)
        
        # 逐行推理
        predictions = []
        service = InferenceService(self.store, None, self.lease_store)
        
        for idx, row in df.iterrows():
            result = service.predict(model_id, row.to_dict())
            predictions.append(result)
        
        # 保存结果
        pred_df = pd.DataFrame(predictions)
        pred_path = Path(config.WORKER_SCRATCH_DIR) / output_path
        pred_path.parent.mkdir(parents=True, exist_ok=True)
        pred_df.to_csv(pred_path, index=False)
        
        # 上传到 OSS
        self.store.write_file(output_path, pred_path)
        
        return {"predictions_path": output_path, "count": len(predictions)}


# 步骤类型到执行器的映射
STEP_EXECUTORS = {
    StepType.DATASET_PULL: DatasetPullExecutor,
    StepType.DATASET_PROCESS: DatasetProcessExecutor,
    StepType.TRAINING: TrainingExecutor,
    StepType.MODEL_UPLOAD: ModelUploadExecutor,
    StepType.MODEL_DEPLOY: ModelDeployExecutor,
    StepType.CUSTOM_SCRIPT: CustomScriptExecutor,
    StepType.NOTEBOOK_RUN: NotebookRunExecutor,
    StepType.INFERENCE: InferenceExecutor,
}


class PipelineEngine:
    """Pipeline DAG 执行引擎"""
    
    def __init__(
        self,
        store: ArtifactStore = None,
        lease_store: LeaseStore = None,
        max_concurrent: int = config.PIPELINE_MAX_CONCURRENT_RUNS,
    ):
        self.store = store or get_store()
        self.lease_store = lease_store
        self.max_concurrent = max_concurrent
        self._running_runs: Dict[str, PipelineRun] = {}
        self._lock = threading.Lock()
    
    def execute(
        self,
        pipeline: Pipeline,
        trigger: TriggerType = TriggerType.MANUAL,
        parameters: Dict[str, Any] = None,
    ) -> PipelineRun:
        """执行 Pipeline"""
        
        # 创建运行记录
        run = PipelineRun(
            id=f"run-{uuid.uuid4().hex[:12]}",
            pipeline_id=pipeline.id,
            trigger=trigger,
            started_at=time.time(),
        )
        
        # 初始化步骤状态
        for step in pipeline.steps:
            run.step_states[step.id] = RunStatus.PENDING
        
        # 保存到数据库
        with db_session() as db:
            create_pipeline_run(db, run)
        
        self._running_runs[run.id] = run
        
        # 异步执行
        thread = threading.Thread(target=self._run_pipeline, args=(pipeline, run, parameters))
        thread.start()
        
        return run
    
    def _run_pipeline(self, pipeline: Pipeline, run: PipelineRun, parameters: Dict = None):
        """同步执行 Pipeline（内部方法）"""
        try:
            run.status = RunStatus.RUNNING
            self._update_run(run)
            
            # 获取执行层级
            levels = pipeline.get_execution_order()
            run.current_level = 0
            
            # 逐层执行
            for level_idx, level in enumerate(levels):
                run.current_level = level_idx
                self._update_run(run)
                
                # 同层并行执行
                self._execute_level(pipeline, run, level, parameters)
                
                # 检查是否有失败且不继续
                failed_steps = [s for s in level if run.step_states.get(s) == RunStatus.FAILED]
                if failed_steps:
                    step = pipeline.get_step(failed_steps[0])
                    if step and not step.continue_on_failure:
                        run.status = RunStatus.FAILED
                        run.error = f"步骤 {step.name} 失败且不允许继续"
                        self._update_run(run)
                        return
            
            run.status = RunStatus.COMPLETED
            run.finished_at = time.time()
            self._update_run(run)
            
        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = str(e)
            run.finished_at = time.time()
            self._update_run(run)
        finally:
            with self._lock:
                self._running_runs.pop(run.id, None)
    
    def _execute_level(self, pipeline: Pipeline, run: PipelineRun, level: List[str], parameters: Dict = None):
        """执行同一层级的步骤（并行）"""
        with ThreadPoolExecutor(max_workers=min(len(level), self.max_concurrent)) as executor:
            futures = {}
            for step_id in level:
                step = pipeline.get_step(step_id)
                if not step:
                    continue
                
                # 检查依赖是否满足
                if not self._check_dependencies(run, step):
                    run.step_states[step_id] = RunStatus.FAILED
                    continue
                
                # 准备输入产物
                input_artifacts = self._collect_input_artifacts(run, step)
                
                # 合并参数
                step_config = step.config.copy()
                if parameters:
                    step_config.update(parameters)
                
                # 提交执行
                future = executor.submit(self._execute_step, pipeline, run, step, input_artifacts, step_config)
                futures[future] = step_id
            
            # 等待完成
            for future in as_completed(futures):
                step_id = futures[future]
                try:
                    output_artifacts = future.result()
                    run.artifact_map[step_id] = output_artifacts
                    run.step_states[step_id] = RunStatus.COMPLETED
                except Exception as e:
                    run.step_states[step_id] = RunStatus.FAILED
                    self.log_fn(f"步骤 {step_id} 执行失败: {e}")
    
    def _execute_step(
        self, 
        pipeline: Pipeline, 
        run: PipelineRun, 
        step: PipelineStep, 
        input_artifacts: Dict,
        step_config: Dict
    ) -> Dict[str, str]:
        """执行单个步骤"""
        # 创建步骤运行记录
        step_run = PipelineStepRun(
            id=f"steprun-{uuid.uuid4().hex[:12]}",
            run_id=run.id,
            step_id=step.id,
            step_name=step.name,
            step_type=step.type,
            config=step_config,
            input_artifacts=input_artifacts,
            started_at=time.time(),
        )
        
        with db_session() as db:
            create_pipeline_step_run(db, step_run)
        
        def log_fn(msg: str):
            step_run.logs.append(msg)
            print(f"[{run.id}][{step.name}] {msg}")
        
        try:
            run.step_states[step.id] = RunStatus.RUNNING
            step_run.status = RunStatus.RUNNING
            self._update_step_run(step_run)
            
            # 获取执行器
            executor_class = STEP_EXECUTORS.get(step.type)
            if not executor_class:
                raise ValueError(f"未知步骤类型: {step.type}")
            
            executor = executor_class(step, run, self.store, self.lease_store, log_fn)
            output_artifacts = executor.execute()
            
            step_run.output_artifacts = output_artifacts
            step_run.status = RunStatus.COMPLETED
            step_run.finished_at = time.time()
            self._update_step_run(step_run)
            
            return output_artifacts
            
        except Exception as e:
            step_run.status = RunStatus.FAILED
            step_run.error = str(e)
            step_run.finished_at = time.time()
            self._update_step_run(step_run)
            raise
    
    def _check_dependencies(self, run: PipelineRun, step: PipelineStep) -> bool:
        """检查依赖是否满足"""
        for dep_id in step.depends_on:
            if run.step_states.get(dep_id) != RunStatus.COMPLETED:
                return False
        return True
    
    def _collect_input_artifacts(self, run: PipelineRun, step: PipelineStep) -> Dict[str, str]:
        """收集上游步骤的输出产物作为输入"""
        artifacts = {}
        for dep_id in step.depends_on:
            dep_artifacts = run.artifact_map.get(dep_id, {})
            for key, path in dep_artifacts.items():
                artifacts[f"{dep_id}.{key}"] = path
        return artifacts
    
    def _update_run(self, run: PipelineRun):
        with db_session() as db:
            update_pipeline_run(db, run.id, 
                status=run.status.value if isinstance(run.status, RunStatus) else run.status,
                step_states={k: v.value if isinstance(v, RunStatus) else v for k, v in run.step_states.items()},
                artifact_map=run.artifact_map,
                error=run.error,
                finished_at=datetime.fromtimestamp(run.finished_at) if run.finished_at else None,
                current_level=run.current_level,
            )
    
    def _update_step_run(self, step_run: PipelineStepRun):
        with db_session() as db:
            update_pipeline_step_run(db, step_run.id,
                status=step_run.status.value if isinstance(step_run.status, RunStatus) else step_run.status,
                output_artifacts=step_run.output_artifacts,
                error=step_run.error,
                finished_at=datetime.fromtimestamp(step_run.finished_at) if step_run.finished_at else None,
                logs=step_run.logs,
            )
    
    def get_run_status(self, run_id: str) -> Optional[PipelineRun]:
        with db_session() as db:
            return get_pipeline_run(db, run_id)
    
    def cancel_run(self, run_id: str) -> bool:
        run = self._running_runs.get(run_id)
        if run:
            run.status = RunStatus.CANCELLED
            run.finished_at = time.time()
            self._update_run(run)
            return True
        return False


class PipelineScheduler:
    """Pipeline 定时调度器"""
    
    def __init__(self, engine: PipelineEngine):
        self.engine = engine
        self._schedules: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
    
    def schedule_pipeline(self, pipeline: Pipeline):
        """调度 Pipeline（按 cron 表达式）"""
        if not pipeline.schedule:
            return
        
        # 简化：使用固定间隔轮询检查
        def check_and_run():
            # 这里应该解析 cron 表达式判断是否该运行
            # 简化版：每分钟检查一次
            self.engine.execute(pipeline, TriggerType.SCHEDULED)
            # 重新调度
            self._schedules[pipeline.id] = threading.Timer(60, check_and_run)
            self._schedules[pipeline.id].start()
        
        with self._lock:
            if pipeline.id in self._schedules:
                self._schedules[pipeline.id].cancel()
            self._schedules[pipeline.id] = threading.Timer(60, check_and_run)
            self._schedules[pipeline.id].start()
    
    def unschedule_pipeline(self, pipeline_id: str):
        with self._lock:
            if pipeline_id in self._schedules:
                self._schedules[pipeline_id].cancel()
                del self._schedules[pipeline_id]
    
    def shutdown(self):
        with self._lock:
            for timer in self._schedules.values():
                timer.cancel()
            self._schedules.clear()