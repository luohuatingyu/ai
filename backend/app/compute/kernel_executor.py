"""代码执行内核：支持 Notebook 单元格执行、变量持久化、分布式执行"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback
import types
import uuid
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
import threading
import time

import app.config as config
from app.storage import get_store


@dataclass
class ExecutionResult:
    """执行结果"""
    success: bool
    output: str = ""
    error: str = ""
    error_type: str = ""
    traceback: str = ""
    variables: Dict[str, Any] = field(default_factory=dict)
    execution_count: int = 0
    execution_time_ms: float = 0
    cell_id: str = ""


class VariableStore:
    """变量存储：会话级变量持久化"""
    
    def __init__(self, session_id: str, max_size: int = config.NOTEBOOK_MAX_VARIABLES_SIZE):
        self.session_id = session_id
        self.max_size = max_size
        self.variables: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._serializable_types = (int, float, str, bool, list, dict, tuple, type(None))
    
    def get(self, name: str) -> Any:
        with self._lock:
            return self.variables.get(name)
    
    def set(self, name: str, value: Any):
        with self._lock:
            # 只存储可序列化的对象
            if self._is_serializable(value):
                self.variables[name] = value
            else:
                # 存储类型信息
                self.variables[name] = f"<{type(value).__name__} object at {hex(id(value))}>"
    
    def update(self, vars_dict: Dict[str, Any]):
        with self._lock:
            for k, v in vars_dict.items():
                if self._is_serializable(v):
                    self.variables[k] = v
    
    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return self.variables.copy()
    
    def clear(self):
        with self._lock:
            self.variables.clear()
    
    def _is_serializable(self, obj: Any) -> bool:
        """检查对象是否可 JSON 序列化。

        模块/函数/类/方法等运行时对象一律拒绝（它们无法安全存入
        JSON 列，且对"变量快照"而言没有值）；其余对象尝试 JSON 编码。
        """
        if isinstance(obj, (types.ModuleType, types.FunctionType,
                            types.BuiltinFunctionType, types.MethodType,
                            type)):
            return False
        try:
            json.dumps(obj)
            return True
        except (TypeError, ValueError):
            return False
    
    def snapshot(self) -> Dict[str, Any]:
        """获取可序列化的变量快照"""
        with self._lock:
            snapshot = {}
            for k, v in self.variables.items():
                try:
                    json.dumps(v, default=str)
                    snapshot[k] = v
                except (TypeError, ValueError):
                    snapshot[k] = f"<{type(v).__name__}>"
            return snapshot


class KernelExecutor:
    """
    代码执行内核：非 Jupyter，轻量级执行引擎
    
    特性：
    - 单元格代码执行（exec）
    - 变量持久化（跨单元格共享）
    - 标准输出/错误捕获
    - 超时控制
    - 分布式执行支持（多 Worker 并行）
    """
    
    def __init__(
        self, 
        session_id: str, 
        bound_workers: List[str] = None,
        timeout: float = config.NOTEBOOK_CELL_TIMEOUT,
    ):
        self.session_id = session_id
        self.bound_workers = bound_workers or []
        self.timeout = timeout
        self.variable_store = VariableStore(session_id)
        self.execution_count = 0
        self._namespace: Dict[str, Any] = {}
        self._init_namespace()
        self._lock = threading.Lock()
    
    def _init_namespace(self):
        """初始化执行命名空间"""
        self._namespace = {
            "__name__": "__main__",
            "__session_id__": self.session_id,
            "print": print,
            "len": len,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "sum": sum,
            "max": max,
            "min": min,
            "abs": abs,
            "round": round,
            "sorted": sorted,
            "reversed": reversed,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "Exception": Exception,
            "ValueError": ValueError,
            "TypeError": TypeError,
            "KeyError": KeyError,
            "IndexError": IndexError,
            "RuntimeError": RuntimeError,
        }
        
        # 预导入常用库
        try:
            import numpy as np
            self._namespace["np"] = np
            self._namespace["numpy"] = np
        except ImportError:
            pass
        
        try:
            import pandas as pd
            self._namespace["pd"] = pd
            self._namespace["pandas"] = pd
        except ImportError:
            pass
        
        try:
            import torch
            self._namespace["torch"] = torch
        except ImportError:
            pass
        
        try:
            import matplotlib.pyplot as plt
            self._namespace["plt"] = plt
        except ImportError:
            pass
        
        # 平台特定 API
        self._namespace["store"] = get_store()
        self._namespace["config"] = config
        self._namespace["DATA_DIR"] = config.DATA_DIR
        self._namespace["WORKER_SCRATCH_DIR"] = config.WORKER_SCRATCH_DIR
    
    def execute(
        self, 
        code: str, 
        mode: str = "single",
        distributed_config: Dict = None,
    ) -> ExecutionResult:
        """
        执行代码片段
        
        Args:
            code: 要执行的 Python 代码
            mode: "single" 单机 / "distributed" 分布式
            distributed_config: 分布式执行配置
        
        Returns:
            ExecutionResult
        """
        start_time = time.perf_counter()
        self.execution_count += 1
        
        # 准备命名空间：注入变量存储中的变量
        with self._lock:
            namespace = self._namespace.copy()
            namespace.update(self.variable_store.get_all())
        
        # 捕获输出
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        result = ExecutionResult(
            success=False,
            execution_count=self.execution_count,
        )
        
        try:
            if mode == "distributed" and self.bound_workers:
                result = self._execute_distributed(code, distributed_config or {})
            else:
                # 单机执行
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    # 设置超时（使用信号或线程）
                    exec(code, namespace)
                
                result.success = True
            
            # 更新变量存储（提取新定义的变量）
            self._capture_new_variables(namespace)
            
        except SyntaxError as e:
            result.error = f"语法错误: {e.msg}"
            result.error_type = "SyntaxError"
            result.traceback = traceback.format_exc()
        except TimeoutError:
            result.error = f"执行超时 ({self.timeout}秒)"
            result.error_type = "TimeoutError"
        except Exception as e:
            result.error = str(e)
            result.error_type = type(e).__name__
            result.traceback = traceback.format_exc()
        finally:
            result.output = stdout_capture.getvalue() + stderr_capture.getvalue()
            result.execution_time_ms = (time.perf_counter() - start_time) * 1000
            result.variables = self.variable_store.snapshot()
        
        return result
    
    def _capture_new_variables(self, namespace: Dict[str, Any]):
        """捕获执行后新增/修改的变量"""
        # 排除内置和模块
        skip_keys = set(self._namespace.keys())
        skip_prefixes = ("_", "__")
        
        new_vars = {}
        for k, v in namespace.items():
            if k in skip_keys or k.startswith(skip_prefixes):
                continue
            if callable(v) and not isinstance(v, type):
                continue  # 跳过函数/类
            if hasattr(v, "__module__") and v.__module__ not in ("__main__", "builtins", None):
                continue  # 跳过导入的模块
            
            new_vars[k] = v
        
        if new_vars:
            self.variable_store.update(new_vars)
    
    def _execute_distributed(self, code: str, config: Dict) -> ExecutionResult:
        """分布式执行：在多个 Worker 上并行运行"""
        # 这里简化：实际应通过协调平面启动多进程
        # 生成启动脚本，通过 SSH 或 Kubernetes 在各 Worker 执行
        raise NotImplementedError("分布式执行待实现：需集成协调平面启动多进程")
    
    def interrupt(self):
        """中断当前执行（发送信号到执行线程）"""
        # 实际实现需要在单独线程/进程中执行代码
        pass
    
    def get_variables(self) -> Dict[str, Any]:
        """获取当前变量快照"""
        return self.variable_store.snapshot()
    
    def set_variable(self, name: str, value: Any):
        """手动设置变量"""
        self.variable_store.set(name, value)
    
    def reset(self):
        """重置内核状态"""
        with self._lock:
            self.variable_store.clear()
            self.execution_count = 0
            self._init_namespace()


class DistributedKernelExecutor(KernelExecutor):
    """分布式内核执行器：协调多 Worker 执行"""
    
    def __init__(
        self, 
        session_id: str, 
        bound_workers: List[str],
        master_addr: str = "localhost",
        master_port: int = 29500,
        timeout: float = config.NOTEBOOK_CELL_TIMEOUT,
    ):
        super().__init__(session_id, bound_workers, timeout)
        self.master_addr = master_addr
        self.master_port = master_port
        self.world_size = len(bound_workers)
    
    def _execute_distributed(self, code: str, config: Dict) -> ExecutionResult:
        """在多 Worker 上执行分布式代码"""
        import subprocess
        import tempfile
        
        # 生成分布式启动脚本
        script = self._generate_distributed_script(code, config)
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name
        
        try:
            # 使用 torchrun 启动
            cmd = [
                "torchrun",
                f"--nproc_per_node={self.world_size}",
                f"--master_addr={self.master_addr}",
                f"--master_port={self.master_port}",
                script_path,
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            exec_result = ExecutionResult(
                success=result.returncode == 0,
                output=result.stdout + result.stderr,
                execution_count=self.execution_count,
            )
            
            if result.returncode != 0:
                exec_result.error = f"分布式执行失败 (exit code {result.returncode})"
                exec_result.error_type = "DistributedExecutionError"
            
            return exec_result
            
        finally:
            os.unlink(script_path)
    
    def _generate_distributed_script(self, code: str, config: Dict) -> str:
        """生成分布式执行脚本"""
        return f"""
import os
import torch
import torch.distributed as dist

# 初始化分布式环境
dist.init_process_group(backend="nccl", init_method="env://")
rank = dist.get_rank()
world_size = dist.get_world_size()
local_rank = int(os.environ.get("LOCAL_RANK", rank))
torch.cuda.set_device(local_rank)

# 用户代码
{code}

# 同步
dist.barrier()
dist.destroy_process_group()
"""