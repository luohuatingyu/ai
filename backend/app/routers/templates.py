"""脚本模板 API：模板库、实例化、参数验证"""
from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import db_session
from app.registry.template_registry import TemplateRegistry, TemplateParameter

router = APIRouter(prefix="/api/templates", tags=["templates"])

_template_registry = TemplateRegistry()


# 请求模型
class CreateTemplateRequest(BaseModel):
    name: str
    category: str
    task_type: str
    content: str
    parameters: List[dict]
    description: str = ""
    dataset_requirements: dict = {}


class InstantiateTemplateRequest(BaseModel):
    parameters: dict
    instantiated_type: str = "training_job"  # training_job/notebook_session/pipeline_step
    created_by: Optional[str] = None


class ValidateParamsRequest(BaseModel):
    template_id: str
    parameters: dict


@router.get("")
def list_templates(
    category: Optional[str] = None,
    is_builtin: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出模板。

    内置模板常驻内存不依赖数据库；用户模板从 DB 读取，DB 不可用时
    降级为仅内置模板（与平台"文件优先、DB 可选镜像"哲学一致）。
    """
    templates = list(_template_registry._builtin_templates.values())

    if category:
        templates = [t for t in templates if t.category == category]
    if is_builtin is not None:
        templates = [t for t in templates if t.is_builtin == is_builtin]

    if is_builtin is None or is_builtin is False:
        try:
            with db_session() as db:
                db_templates, db_total = _template_registry.list_templates(
                    limit=10000, category=category, is_builtin=False
                )
            templates.extend(db_templates)
        except Exception:
            # DB 不可用：仅返回内置模板，不让基础浏览功能崩溃
            pass

    total = len(templates)
    templates = templates[offset:offset + limit]
    return {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "task_type": t.task_type,
                "is_builtin": t.is_builtin,
                "parameters": [
                    {"name": p.name, "type": p.type, "label": p.label, "description": p.description,
                     "default": p.default, "required": p.required, "options": p.options}
                    for p in t.parameters
                ],
                "dataset_requirements": t.dataset_requirements,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            for t in templates
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/categories")
def get_template_categories() -> dict:
    """获取模板分类"""
    templates = _template_registry.get_builtin_templates()
    categories = {}
    for t in templates:
        if t.category not in categories:
            categories[t.category] = {"name": t.category, "count": 0, "tasks": []}
        categories[t.category]["count"] += 1
        if t.task_type not in categories[t.category]["tasks"]:
            categories[t.category]["tasks"].append(t.task_type)
    return {"categories": list(categories.values())}


@router.get("/{template_id}")
def get_template(template_id: str) -> dict:
    """获取模板详情"""
    template = _template_registry.get_template(template_id)
    if not template:
        raise HTTPException(404, "模板不存在")
    
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "category": template.category,
        "task_type": template.task_type,
        "content": template.content,
        "parameters": [
            {"name": p.name, "type": p.type, "label": p.label, "description": p.description,
             "default": p.default, "required": p.required, "options": p.options, "validation": p.validation}
            for p in template.parameters
        ],
        "dataset_requirements": template.dataset_requirements,
        "is_builtin": template.is_builtin,
        "created_by": template.created_by,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


@router.post("")
def create_template(req: CreateTemplateRequest, created_by: str = "") -> dict:
    """创建用户模板"""
    try:
        template = _template_registry.create_template(
            name=req.name,
            category=req.category,
            task_type=req.task_type,
            content=req.content,
            parameters=req.parameters,
            description=req.description,
            dataset_requirements=req.dataset_requirements,
            created_by=created_by,
        )
        return {"id": template.id, "message": "模板创建成功"}
    except Exception as e:
        raise HTTPException(400, f"创建模板失败: {e}")


@router.put("/{template_id}")
def update_template(template_id: str, req: CreateTemplateRequest) -> dict:
    """更新用户模板"""
    try:
        template = _template_registry.update_template(
            template_id,
            name=req.name,
            category=req.category,
            task_type=req.task_type,
            content=req.content,
            parameters=req.parameters,
            description=req.description,
            dataset_requirements=req.dataset_requirements,
        )
        if not template:
            raise HTTPException(404, "模板不存在")
        return {"message": "模板更新成功"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{template_id}")
def delete_template(template_id: str) -> dict:
    """删除用户模板"""
    try:
        success = _template_registry.delete_template(template_id)
        if not success:
            raise HTTPException(404, "模板不存在")
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{template_id}/validate")
def validate_parameters(req: ValidateParamsRequest) -> dict:
    """验证模板参数"""
    template = _template_registry.get_template(req.template_id)
    if not template:
        raise HTTPException(404, "模板不存在")
    
    errors = template.validate_params(req.parameters)
    return {"valid": len(errors) == 0, "errors": errors}


@router.post("/{template_id}/instantiate")
def instantiate_template(template_id: str, req: InstantiateTemplateRequest) -> dict:
    """实例化模板为可执行配置"""
    try:
        result = _template_registry.instantiate(
            template_id=template_id,
            params=req.parameters,
            instantiated_type=req.instantiated_type,
            created_by=req.created_by,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{template_id}/render")
def render_template(template_id: str, req: InstantiateTemplateRequest) -> dict:
    """渲染模板（仅返回渲染后的代码）"""
    template = _template_registry.get_template(template_id)
    if not template:
        raise HTTPException(404, "模板不存在")
    
    errors = template.validate_params(req.parameters)
    if errors:
        raise HTTPException(400, f"参数验证失败: {', '.join(errors)}")
    
    rendered = template.render(req.parameters)
    return {"rendered_content": rendered}