"""Notebook 会话管理：生命周期、内核绑定、状态持久化"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import app.config as config
from app.storage import get_store
from app.compute.kernel_executor import KernelExecutor, ExecutionResult
from app.db_utils import (
    create_notebook_session,
    get_notebook_session,
    update_notebook_session,
    list_notebook_sessions,
    delete_notebook_session,
    save_notebook_cell,
    update_notebook_cell,
    get_notebook_cells,
    delete_notebook_cells,
)
from app.database import db_session


@dataclass
class NotebookSession:
    """会话运行时状态"""
    id: str
    name: str
    owner: str
    dataset_id: Optional[str]
    bound_workers: List[str]
    status: str  # starting/running/idle/busy/stopped/error
    kernel_type: str
    metadata: Dict
    created_at: float
    updated_at: float
    last_activity: float
    
    # 运行时
    kernel: Optional[KernelExecutor] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def is_active(self) -> bool:
        return self.status in ("running", "idle", "busy")
    
    def mark_activity(self):
        self.last_activity = time.time()


class SessionManager:
    """Notebook 会话管理器"""
    
    def __init__(
        self,
        store = None,
        max_sessions: int = config.NOTEBOOK_MAX_SESSIONS,
        session_ttl: int = config.NOTEBOOK_SESSION_TTL,
        kernel_pool_size: int = config.NOTEBOOK_KERNEL_POOL_SIZE,
    ):
        self.store = store or get_store()
        self.max_sessions = max_sessions
        self.session_ttl = session_ttl
        self.kernel_pool_size = kernel_pool_size
        
        self._sessions: Dict[str, NotebookSession] = {}
        self._lock = threading.RLock()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._stop_cleanup = threading.Event()
    
    def start(self):
        """启动管理器（恢复已有会话）"""
        self._load_sessions_from_db()
        self._start_cleanup_loop()
    
    def stop(self):
        """停止管理器"""
        self._stop_cleanup.set()
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)
        
        # 停止所有内核
        with self._lock:
            for session in self._sessions.values():
                if session.kernel:
                    session.kernel.reset()
    
    def _load_sessions_from_db(self):
        """从数据库恢复会话"""
        try:
            with db_session() as db:
                sessions, _ = list_notebook_sessions(db, limit=1000)
                for s in sessions:
                    if s.status in ("running", "idle", "busy"):
                        # 恢复为 idle 状态，需要用户重新启动内核
                        update_notebook_session(db, s.id, status="idle")
                        s.status = "idle"
                    self._sessions[s.id] = NotebookSession(
                        id=s.id,
                        name=s.name,
                        owner=s.owner or "",
                        dataset_id=s.dataset_id,
                        bound_workers=s.bound_workers or [],
                        status=s.status,
                        kernel_type=s.kernel_type,
                        metadata=s.metadata or {},
                        created_at=s.created_at.timestamp() if s.created_at else time.time(),
                        updated_at=s.updated_at.timestamp() if s.updated_at else time.time(),
                        last_activity=s.last_activity.timestamp() if s.last_activity else time.time(),
                    )
        except Exception as e:
            print(f"恢复会话失败: {e}")
    
    def _start_cleanup_loop(self):
        """启动清理循环"""
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
    
    def _cleanup_loop(self):
        """定期清理过期会话"""
        while not self._stop_cleanup.wait(60):
            try:
                self._cleanup_expired_sessions()
            except Exception as e:
                print(f"会话清理异常: {e}")
    
    def _cleanup_expired_sessions(self):
        """清理过期会话"""
        now = time.time()
        expired = []
        
        with self._lock:
            for session_id, session in self._sessions.items():
                if now - session.last_activity > self.session_ttl:
                    expired.append(session_id)
        
        for session_id in expired:
            self.delete_session(session_id, force=True)
    
    # ==================== 公共 API ====================
    
    def create_session(
        self,
        name: str,
        owner: str = "",
        dataset_id: str = None,
        bound_workers: List[str] = None,
        kernel_type: str = "python",
        metadata: Dict = None,
    ) -> NotebookSession:
        """创建新会话"""
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(f"会话数量已达上限 ({self.max_sessions})")
            
            session_id = f"nb-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            
            # 保存到数据库（传入生成的会话 ID，保证内存与 DB 行一致）
            with db_session() as db:
                db_session_obj = create_notebook_session(
                    db=db,
                    name=name,
                    owner=owner,
                    dataset_id=dataset_id,
                    bound_workers=bound_workers or [],
                    kernel_type=kernel_type,
                    metadata=metadata or {},
                    session_id=session_id,
                )
            
            session = NotebookSession(
                id=session_id,
                name=name,
                owner=owner,
                dataset_id=dataset_id,
                bound_workers=bound_workers or [],
                status="starting",
                kernel_type=kernel_type,
                metadata=metadata or {},
                created_at=time.time(),
                updated_at=time.time(),
                last_activity=time.time(),
            )
            
            self._sessions[session_id] = session
            return session
    
    def get_session(self, session_id: str) -> Optional[NotebookSession]:
        """获取会话"""
        with self._lock:
            return self._sessions.get(session_id)
    
    def list_sessions(
        self, 
        owner: str = None, 
        status: str = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[NotebookSession], int]:
        """列出会话"""
        with db_session() as db:
            return list_notebook_sessions(db, limit, offset, owner, status)
    
    def start_kernel(self, session_id: str) -> bool:
        """启动内核"""
        session = self.get_session(session_id)
        if not session:
            return False
        
        with session._lock:
            if session.kernel:
                return True  # 已经运行
            
            try:
                session.status = "starting"
                self._update_session_db(session_id, status="starting")
                
                # 创建内核
                session.kernel = KernelExecutor(
                    session_id=session_id,
                    bound_workers=session.bound_workers,
                )
                
                # 恢复变量（从数据库加载单元格历史）
                self._restore_variables(session)
                
                session.status = "idle"
                session.mark_activity()
                self._update_session_db(session_id, status="idle")
                return True
                
            except Exception as e:
                session.status = "error"
                self._update_session_db(session_id, status="error", metadata={"error": str(e)})
                return False
    
    def stop_kernel(self, session_id: str) -> bool:
        """停止内核"""
        session = self.get_session(session_id)
        if not session or not session.kernel:
            return True
        
        with session._lock:
            session.kernel.reset()
            session.kernel = None
            session.status = "stopped"
            self._update_session_db(session_id, status="stopped")
            return True
    
    def execute_cell(
        self,
        session_id: str,
        cell_id: str,
        cell_index: int,
        code: str,
        cell_type: str = "code",
        mode: str = "single",
        distributed_config: Dict = None,
    ) -> ExecutionResult:
        """执行单元格"""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"会话不存在: {session_id}")
        
        if not session.kernel:
            raise RuntimeError("内核未启动，请先启动内核")
        
        if session.status == "busy":
            raise RuntimeError("会话正在执行其他单元格，请等待完成")
        
        with session._lock:
            session.status = "busy"
            session.mark_activity()
            
            # 保存单元格（running 状态），记录真实 cell_id 供后续更新
            with db_session() as db:
                saved_cell = save_notebook_cell(
                    db=db,
                    session_id=session_id,
                    cell_index=cell_index,
                    cell_type=cell_type,
                    source=code,
                    status="running",
                )
                real_cell_id = saved_cell.id
            
            try:
                # 执行代码
                result = session.kernel.execute(code, mode, distributed_config)
                
                # 更新单元格结果（用真实 cell_id）
                with db_session() as db:
                    update_notebook_cell(
                        db=db,
                        cell_id=real_cell_id,
                        outputs=[{"type": "stream", "text": result.output}] if result.output else [],
                        execution_count=result.execution_count,
                        status="completed" if result.success else "error",
                        error=result.error if not result.success else None,
                        variables_snapshot=result.variables,
                    )
                
                session.status = "idle"
                session.mark_activity()
                self._update_session_db(session_id, status="idle")
                
                result.cell_id = real_cell_id
                return result
                
            except Exception as e:
                with db_session() as db:
                    update_notebook_cell(
                        db=db,
                        cell_id=real_cell_id,
                        status="error",
                        error=str(e),
                    )
                session.status = "idle"
                self._update_session_db(session_id, status="idle")
                raise
    
    def interrupt_execution(self, session_id: str) -> bool:
        """中断执行"""
        session = self.get_session(session_id)
        if not session or not session.kernel:
            return False
        
        session.kernel.interrupt()
        return True
    
    def get_cells(self, session_id: str) -> List[Dict]:
        """获取单元格历史"""
        with db_session() as db:
            cells = get_notebook_cells(db, session_id)
            return [
                {
                    "id": c.id,
                    "cell_index": c.cell_index,
                    "cell_type": c.cell_type,
                    "source": c.source,
                    "outputs": c.outputs,
                    "execution_count": c.execution_count,
                    "status": c.status,
                    "error": c.error,
                    "variables_snapshot": c.variables_snapshot,
                }
                for c in cells
            ]
    
    def save_notebook(
        self,
        session_id: str,
        cells: List[Dict],
    ) -> bool:
        """保存整个 Notebook（覆盖单元格）"""
        session = self.get_session(session_id)
        if not session:
            return False
        
        with db_session() as db:
            # 删除旧单元格
            delete_notebook_cells(db, session_id)
            
            # 保存新单元格
            for i, cell in enumerate(cells):
                save_notebook_cell(
                    db=db,
                    session_id=session_id,
                    cell_index=i,
                    cell_type=cell.get("cell_type", "code"),
                    source=cell.get("source", ""),
                    outputs=cell.get("outputs", []),
                    execution_count=cell.get("execution_count"),
                    status=cell.get("status", "pending"),
                    variables_snapshot=cell.get("variables_snapshot"),
                )
        
        return True
    
    def delete_session(self, session_id: str, force: bool = False) -> bool:
        """删除会话"""
        session = self.get_session(session_id)
        if not session:
            return False
        
        if session.is_active() and not force:
            raise RuntimeError("会话正在运行，请先停止内核或使用 force=True")
        
        # 停止内核
        if session.kernel:
            session.kernel.reset()
        
        # 从数据库删除
        with db_session() as db:
            delete_notebook_session(db, session_id)
        
        with self._lock:
            self._sessions.pop(session_id, None)
        
        return True
    
    def get_variables(self, session_id: str) -> Dict[str, Any]:
        """获取会话变量"""
        session = self.get_session(session_id)
        if not session or not session.kernel:
            return {}
        return session.kernel.get_variables()
    
    # ==================== 内部方法 ====================
    
    def _update_session_db(self, session_id: str, **updates):
        with db_session() as db:
            update_notebook_session(db, session_id, **updates)
    
    def _restore_variables(self, session: NotebookSession):
        """从单元格历史恢复变量"""
        try:
            with db_session() as db:
                cells = get_notebook_cells(db, session.id)
                # 只恢复最近成功执行的单元格的变量
                for cell in reversed(cells):
                    if cell.status == "completed" and cell.variables_snapshot:
                        if session.kernel:
                            for k, v in cell.variables_snapshot.items():
                                session.kernel.set_variable(k, v)
                        break
        except Exception as e:
            print(f"恢复变量失败: {e}")


# 全局单例
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
        _session_manager.start()
    return _session_manager


def shutdown_session_manager():
    global _session_manager
    if _session_manager:
        _session_manager.stop()
        _session_manager = None