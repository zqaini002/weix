import os
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.auth import verify_token
from app.deps import get_session
from app.models.database import AutoReplyRule, MessageTemplate, Workflow, ForwardRule, SystemConfig
from app.models.schemas import (
    RuleCreate, RuleOut, RuleUpdate,
    TemplateCreate, TemplateOut, TemplateUpdate,
    WorkflowCreate, WorkflowOut, WorkflowUpdate,
    ForwardRuleCreate, ForwardRuleOut,
    SystemConfigUpdate, SystemConfigItem,
)
from app.config import get_config

router = APIRouter(prefix="/api", tags=["config"], dependencies=[Depends(verify_token)])


def _get_config_path() -> str:
    return os.getenv(
        "WEIX_CONFIG",
        str(Path(__file__).parent.parent.parent.parent / "config" / "config.yaml"),
    )


# --- Chat Config ---
@router.get("/config/chat")
async def get_chat_config():
    return get_config().auto_reply


@router.put("/config/chat")
async def update_chat_config(data: dict):
    cfg = get_config()
    cfg.auto_reply.update(data)

    config_path = _get_config_path()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        raw["auto_reply"].update(data)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        raise HTTPException(500, f"配置保存失败: {e}")

    return {"success": True}


# --- AI Config ---
@router.get("/config/ai")
async def get_ai_config():
    cfg = get_config().ai
    masked = {}
    for k, v in cfg.items():
        if "api_key" in k.lower() and isinstance(v, str) and len(v) > 4:
            masked[k] = "***" + v[-4:]
        else:
            masked[k] = v
    return masked


@router.put("/config/ai")
async def update_ai_config(data: dict):
    cfg = get_config()

    # 如果 api_key 以 *** 开头，说明前端未修改，保留原值
    for k, v in data.items():
        if "api_key" in k.lower() and isinstance(v, str) and v.startswith("***"):
            continue
        cfg.ai[k] = v

    # 持久化到 YAML（排除掩码值）
    config_path = _get_config_path()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        for k, v in data.items():
            if "api_key" in k.lower() and isinstance(v, str) and v.startswith("***"):
                continue
            raw.setdefault("ai", {})[k] = v
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        raise HTTPException(500, f"AI 配置保存失败: {e}")

    return {"success": True}


# --- Auto Reply Rules ---
@router.get("/rules", response_model=list[RuleOut])
async def list_rules(session=Depends(get_session)):
    result = await session.execute(select(AutoReplyRule).order_by(AutoReplyRule.priority.desc()))
    return result.scalars().all()


@router.post("/rules", response_model=RuleOut)
async def create_rule(rule: RuleCreate, session=Depends(get_session)):
    record = AutoReplyRule(**rule.model_dump())
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@router.put("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: int, data: RuleUpdate, session=Depends(get_session)):
    result = await session.execute(select(AutoReplyRule).where(AutoReplyRule.id == rule_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Rule not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(record, k, v)
    await session.commit()
    await session.refresh(record)
    return record


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, session=Depends(get_session)):
    result = await session.execute(select(AutoReplyRule).where(AutoReplyRule.id == rule_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Rule not found")
    await session.delete(record)
    await session.commit()
    return {"success": True}


# --- Templates ---
@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(session=Depends(get_session)):
    result = await session.execute(select(MessageTemplate))
    return result.scalars().all()


@router.post("/templates", response_model=TemplateOut)
async def create_template(tpl: TemplateCreate, session=Depends(get_session)):
    record = MessageTemplate(**tpl.model_dump())
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@router.put("/templates/{tpl_id}", response_model=TemplateOut)
async def update_template(tpl_id: int, data: TemplateUpdate, session=Depends(get_session)):
    result = await session.execute(select(MessageTemplate).where(MessageTemplate.id == tpl_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Template not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(record, k, v)
    await session.commit()
    await session.refresh(record)
    return record


@router.delete("/templates/{tpl_id}")
async def delete_template(tpl_id: int, session=Depends(get_session)):
    result = await session.execute(select(MessageTemplate).where(MessageTemplate.id == tpl_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Template not found")
    await session.delete(record)
    await session.commit()
    return {"success": True}


# --- Workflows ---
@router.get("/workflows", response_model=list[WorkflowOut])
async def list_workflows(session=Depends(get_session)):
    result = await session.execute(select(Workflow))
    return result.scalars().all()


@router.post("/workflows", response_model=WorkflowOut)
async def create_workflow(wf: WorkflowCreate, session=Depends(get_session)):
    record = Workflow(**wf.model_dump())
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@router.put("/workflows/{wf_id}", response_model=WorkflowOut)
async def update_workflow(wf_id: int, data: WorkflowUpdate, session=Depends(get_session)):
    result = await session.execute(select(Workflow).where(Workflow.id == wf_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Workflow not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(record, k, v)
    await session.commit()
    await session.refresh(record)
    return record


@router.delete("/workflows/{wf_id}")
async def delete_workflow(wf_id: int, session=Depends(get_session)):
    result = await session.execute(select(Workflow).where(Workflow.id == wf_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Workflow not found")
    await session.delete(record)
    await session.commit()
    return {"success": True}


# --- Forward Rules ---
@router.get("/forward-rules", response_model=list[ForwardRuleOut])
async def list_forward_rules(session=Depends(get_session)):
    result = await session.execute(select(ForwardRule))
    return result.scalars().all()


@router.post("/forward-rules", response_model=ForwardRuleOut)
async def create_forward_rule(rule: ForwardRuleCreate, session=Depends(get_session)):
    record = ForwardRule(**rule.model_dump())
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@router.put("/forward-rules/{rule_id}", response_model=ForwardRuleOut)
async def update_forward_rule(rule_id: int, data: ForwardRuleCreate, session=Depends(get_session)):
    result = await session.execute(select(ForwardRule).where(ForwardRule.id == rule_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Forward rule not found")
    for k, v in data.model_dump().items():
        setattr(record, k, v)
    await session.commit()
    await session.refresh(record)
    return record


@router.delete("/forward-rules/{rule_id}")
async def delete_forward_rule(rule_id: int, session=Depends(get_session)):
    result = await session.execute(select(ForwardRule).where(ForwardRule.id == rule_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Forward rule not found")
    await session.delete(record)
    await session.commit()
    return {"success": True}


# --- System Config ---
@router.get("/system-config")
async def get_system_config(session=Depends(get_session)):
    """获取系统配置（默认值兜底）。"""
    result = await session.execute(select(SystemConfig))
    rows = result.scalars().all()
    db_map = {r.key: r.value for r in rows}

    defaults = {
        "system_name": "Weix 微信助手",
        "system_version": "0.1.0",
        "admin_email": "",
        "log_level": "INFO",
        "data_retention_days": "30",
        "page_size": "20",
        "alert_enabled": "true",
        "alert_room_id": "",
    }
    for k, v in defaults.items():
        db_map.setdefault(k, v)
    return [{"key": k, "value": db_map[k]} for k in defaults]


@router.put("/system-config")
async def update_system_config(data: SystemConfigUpdate, session=Depends(get_session)):
    """批量更新系统配置。"""
    for item in data.items:
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == item.key)
        )
        record = result.scalar_one_or_none()
        if record:
            record.value = item.value
        else:
            session.add(SystemConfig(key=item.key, value=item.value))
    await session.commit()
    return {"success": True}


# --- Scheduler ---
@router.get("/scheduler/jobs")
async def list_jobs():
    from app.services.scheduler_service import get_scheduler
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
            "paused": job.next_run_time is None,
        })
    return jobs


@router.put("/scheduler/jobs/{job_id}")
async def update_job(job_id: str, data: dict):
    from app.services.scheduler_service import get_scheduler
    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    paused = data.get("paused")
    if paused is not None:
        if paused:
            job.pause()
        else:
            job.resume()
    return {"success": True, "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/trigger")
async def trigger_job(job_id: str):
    from app.services.scheduler_service import get_scheduler
    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    # Run the job immediately in background
    import asyncio
    asyncio.create_task(job.func(*job.args, **job.kwargs))
    return {"success": True, "job_id": job_id, "triggered": True}
