"""Notebook API：会话管理、单元格执行、变量查看"""
from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.compute.session_manager import get_session_manager, SessionManager
from app.compute.kernel_executor import ExecutionResult

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


# 请求模型
class CreateSessionRequest(BaseModel):
    name: str
    owner: str = ""
    dataset_id: Optional[str] = None
    bound_workers: List[str] = []
    kernel_type: str = "python"
    metadata: dict = {}


class ExecuteCellRequest(BaseModel):
    cell_id: str
    cell_index: int
    code: str
    cell_type: str = "code"
    mode: str = "single"
    distributed_config: dict = {}


class UpdateCellRequest(BaseModel):
    cell_id: str
    source: str
    cell_type: str = "code"
    cell_index: int


# 会话管理

@router.post("/sessions")
def create_session(req: CreateSessionRequest) -> dict:
    """创建 Notebook 会话"""
    manager: SessionManager = get_session_manager()
    session = manager.create_session(
        name=req.name,
        owner=req.owner,
        dataset_id=req.dataset_id,
        bound_workers=req.bound_workers,
        kernel_type=req.kernel_type,
        metadata=req.metadata,
    )
    return {"session_id": session.id, "status": session.status}


@router.get("/sessions")
def list_sessions(
    owner: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出会话"""
    manager: SessionManager = get_session_manager()
    sessions, total = manager.list_sessions(owner, status, limit, offset)
    return {
        "sessions": [
            {
                "id": s.id,
                "name": s.name,
                "owner": s.owner,
                "dataset_id": s.dataset_id,
                "bound_workers": s.bound_workers,
                "status": s.status,
                "kernel_type": s.kernel_type,
                "created_at": s.created_at,
                "last_activity": s.last_activity,
            }
            for s in sessions
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    """获取会话详情"""
    manager: SessionManager = get_session_manager()
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    
    cells = manager.get_cells(session_id)
    
    return {
        "id": session.id,
        "name": session.name,
        "owner": session.owner,
        "dataset_id": session.dataset_id,
        "bound_workers": session.bound_workers,
        "status": session.status,
        "kernel_type": session.kernel_type,
        "metadata": session.metadata,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "last_activity": session.last_activity,
        "cells": cells,
    }


@router.post("/sessions/{session_id}/kernel/start")
def start_kernel(session_id: str) -> dict:
    """启动内核"""
    manager: SessionManager = get_session_manager()
    success = manager.start_kernel(session_id)
    if not success:
        raise HTTPException(500, "内核启动失败")
    return {"ok": True, "status": "idle"}


@router.post("/sessions/{session_id}/kernel/stop")
def stop_kernel(session_id: str) -> dict:
    """停止内核"""
    manager: SessionManager = get_session_manager()
    success = manager.stop_kernel(session_id)
    return {"ok": success, "status": "stopped"}


@router.post("/sessions/{session_id}/cells/execute")
def execute_cell(session_id: str, req: ExecuteCellRequest) -> dict:
    """执行单元格"""
    manager: SessionManager = get_session_manager()
    
    try:
        result = manager.execute_cell(
            session_id=session_id,
            cell_id=req.cell_id,
            cell_index=req.cell_index,
            code=req.code,
            cell_type=req.cell_type,
            mode=req.mode,
            distributed_config=req.distributed_config,
        )
        
        return {
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "error_type": result.error_type,
            "traceback": result.traceback,
            "variables": result.variables,
            "execution_count": result.execution_count,
            "execution_time_ms": result.execution_time_ms,
        }
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/sessions/{session_id}/cells/interrupt")
def interrupt_execution(session_id: str) -> dict:
    """中断执行"""
    manager: SessionManager = get_session_manager()
    success = manager.interrupt_execution(session_id)
    return {"ok": success}


@router.post("/sessions/{session_id}/cells")
def save_cell(session_id: str, req: UpdateCellRequest) -> dict:
    """保存/更新单元格"""
    manager: SessionManager = get_session_manager()
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    
    from app.db_utils import save_notebook_cell
    from app.database import db_session
    
    with db_session() as db:
        cell = save_notebook_cell(
            db=db,
            session_id=session_id,
            cell_index=req.cell_index,
            cell_type=req.cell_type,
            source=req.source,
            status="pending",
        )
    
    return {"cell_id": cell.id, "cell_index": cell.cell_index}


@router.put("/sessions/{session_id}/notebook")
def save_notebook(session_id: str, cells: List[dict]) -> dict:
    """保存整个 Notebook"""
    manager: SessionManager = get_session_manager()
    success = manager.save_notebook(session_id, cells)
    if not success:
        raise HTTPException(404, "会话不存在")
    return {"ok": True, "cells_count": len(cells)}


@router.get("/sessions/{session_id}/variables")
def get_variables(session_id: str) -> dict:
    """获取会话变量"""
    manager: SessionManager = get_session_manager()
    variables = manager.get_variables(session_id)
    return {"variables": variables}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, force: bool = False) -> dict:
    """删除会话"""
    manager: SessionManager = get_session_manager()
    try:
        success = manager.delete_session(session_id, force=force)
        return {"ok": success}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


# WebSocket 实时输出（可选）
@router.websocket("/sessions/{session_id}/ws")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket 实时推送执行输出"""
    await websocket.accept()
    manager: SessionManager = get_session_manager()
    session = manager.get_session(session_id)
    
    if not session:
        await websocket.close(code=4004, reason="会话不存在")
        return
    
    try:
        while True:
            data = await websocket.receive_json()
            # 处理客户端消息（如订阅特定单元格输出）
            await websocket.send_json({"type": "ack", "data": data})
    except WebSocketDisconnect:
        pass