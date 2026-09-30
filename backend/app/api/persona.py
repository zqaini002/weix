"""Persona API — 聊天风格分析与个性管理。"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import verify_token
from app.config import get_config

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/persona",
    tags=["persona"],
    dependencies=[Depends(verify_token)],
)


def _normalize_db_key_path(path: str) -> str:
    return path.replace("\\", "/").lower()


def _key_matches_db_path(key_path: str, full_path: str) -> bool:
    normalized_key = _normalize_db_key_path(key_path)
    normalized_full = _normalize_db_key_path(full_path)
    basename = os.path.basename(full_path)
    if "/" in normalized_key:
        return normalized_full.endswith(normalized_key)
    return os.path.normcase(key_path) == os.path.normcase(basename)


class PersonaUpdateRequest(BaseModel):
    """人工编辑 persona skill 的请求体。"""

    meta: dict = Field(default_factory=dict)
    self_memory: str = ""
    persona: str = ""
    private_prompt: str = ""
    group_prompt: str = ""
    mode: str = "contextual"


@router.get("")
async def get_persona():
    """获取当前缓存的 persona skill。"""
    try:
        from app.ai.style_distiller import StyleDistiller
        d = StyleDistiller()
        if d.has_persona:
            return {
                "ready": True,
                "mode": d.mode,
                "meta": d.meta,
                "self_memory": d.self_memory_md,
                "persona": d.persona_md,
                "private_prompt": d.build_prompt(is_group=False),
                "group_prompt": d.build_prompt(is_group=True),
            }
        return {
            "ready": False,
            "mode": "contextual",
            "meta": {},
            "self_memory": "",
            "persona": "",
            "private_prompt": "",
            "group_prompt": "",
        }
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


@router.post("/analyze")
async def analyze_persona(force: bool = False):
    """提取用户消息并分析语言风格。

    Args:
        force: 是否强制重新分析（忽略缓存）。
    """
    try:
        from app.core.platform import Platform
        from app.ai.style_distiller import StyleDistiller

        # 1. 打开微信 DB 提取用户消息
        platform = Platform.get()
        extractor = platform.key_extractor
        if hasattr(extractor, "load_keys"):
            keys = extractor.load_keys()
        else:
            keys = getattr(extractor, "_keys", {})

        if not keys:
            return {"success": False, "error": "未获取数据库密钥，请以 sudo 启动服务"}

        from app.core.db_reader_macos import MacOSDBReader

        reader = MacOSDBReader()
        all_dbs = reader.find_database_files()

        msg_db_path = None
        msg_key = None
        for full_path in all_dbs:
            db_name = os.path.basename(full_path)
            for key_path, hex_key in keys.items():
                if _key_matches_db_path(key_path, full_path):
                    if "message_0.db" in key_path or "message_0.db" in db_name:
                        msg_db_path = full_path
                        msg_key = hex_key
                        break
            if msg_db_path:
                break

        if not msg_db_path:
            return {"success": False, "error": "未找到消息数据库"}

        reader.open_db(msg_db_path, bytes.fromhex(msg_key))

        # 2. 提取用户消息
        cfg = get_config()
        ai_cfg = cfg.ai if isinstance(cfg.ai, dict) else {}
        message_limit = int(ai_cfg.get("persona_message_limit", 0))
        since_days = int(ai_cfg.get("persona_since_days", 90))
        # limit=0 表示提取全部消息，传一个大值给 DB reader
        db_limit = message_limit if message_limit > 0 else 100000
        raw_messages = reader.get_my_messages(limit=db_limit, since_days=since_days)
        reader.close()

        if not raw_messages:
            return {"success": False, "error": "未提取到用户消息，请确认微信已登录"}

        contents = [m["content"] for m in raw_messages]

        # 3. LLM 分析
        d = StyleDistiller()
        persona = await d.analyze(contents, force=force)
        from app.ai.agent import WeixAgent
        WeixAgent._distiller = None
        logger.info("Persona skill updated; WeixAgent distiller cache reset")

        meta = persona.get("meta", {})
        sample_size = meta.get("sample_size") or len(contents)

        return {
            "success": True,
            "total_messages": len(raw_messages),
            "sample_size": sample_size,
            "mode": persona.get("mode", "contextual"),
            "meta": meta,
            "self_memory": persona.get("self_memory_md", ""),
            "persona": persona.get("persona_md", ""),
            "private_prompt": persona.get("runtime_prompt_private", ""),
            "group_prompt": persona.get("runtime_prompt_group", ""),
        }

    except Exception as exc:
        logger.error(f"Persona analysis failed: {exc}", exc_info=True)
        return {"success": False, "error": str(exc)}


@router.put("")
async def update_persona(payload: PersonaUpdateRequest):
    """保存人工编辑后的 persona skill。"""
    try:
        from app.ai.style_distiller import StyleDistiller
        d = StyleDistiller()
        skill = d.save_edits(
            self_memory_md=payload.self_memory,
            persona_md=payload.persona,
            runtime_prompt_private=payload.private_prompt,
            runtime_prompt_group=payload.group_prompt,
            meta=payload.meta,
            mode=payload.mode,
        )

        from app.ai.agent import WeixAgent
        WeixAgent._distiller = None
        logger.info("Persona skill manually updated; WeixAgent distiller cache reset")

        return {
            "success": True,
            "ready": True,
            "mode": skill.get("mode", "contextual"),
            "meta": skill.get("meta", {}),
            "self_memory": skill.get("self_memory_md", ""),
            "persona": skill.get("persona_md", ""),
            "private_prompt": skill.get("runtime_prompt_private", ""),
            "group_prompt": skill.get("runtime_prompt_group", ""),
        }
    except Exception as exc:
        logger.error(f"Persona update failed: {exc}", exc_info=True)
        return {"success": False, "error": str(exc)}


@router.delete("")
async def clear_persona():
    """清除缓存的 persona，恢复默认 AI 风格。"""
    try:
        from app.ai.style_distiller import StyleDistiller
        d = StyleDistiller()
        d.clear_cache()
        # 同时清除 agent 类缓存
        from app.ai.agent import WeixAgent
        WeixAgent._distiller = None
        return {"success": True, "message": "Persona 已清除，将恢复默认风格"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
