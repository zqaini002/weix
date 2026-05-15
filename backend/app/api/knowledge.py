"""知识库管理 API。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.auth import verify_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(verify_token)],
)


class KnowledgeAddRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    topic: str = Field(default="manual")
    priority: str = Field(default="medium")


class KnowledgeResponse(BaseModel):
    id: str
    text: str
    topic: str = ""
    priority: str = ""


def _get_vs():
    """延迟获取 VectorStoreManager（通过 WeixAgent）。"""
    try:
        from app.ai.vector_store import VectorStoreManager
        return VectorStoreManager()
    except Exception as exc:
        logger.warning(f"VectorStoreManager 初始化失败: {exc}")
        return None


@router.get("")
async def list_knowledge(
    limit: int = Query(100, ge=1, le=500),
):
    """获取知识库文档列表。"""
    vs = _get_vs()
    if vs is None:
        return {"documents": [], "total": 0, "error": "向量存储不可用"}

    docs = vs.list_knowledge(limit=limit)
    items = [
        KnowledgeResponse(
            id=d["id"],
            text=d["text"],
            topic=d.get("metadata", {}).get("topic", ""),
            priority=d.get("metadata", {}).get("priority", ""),
        )
        for d in docs
    ]
    return {"documents": items, "total": len(items)}


@router.post("/add")
async def add_knowledge(req: KnowledgeAddRequest):
    """添加知识库文档。"""
    vs = _get_vs()
    if vs is None:
        return {"success": False, "error": "向量存储不可用"}

    try:
        from app.ai.embeddings import EmbeddingManager

        em = EmbeddingManager(provider="local")
        embedding = em.embed_query(req.text)
        vs.add_knowledge(
            texts=[req.text],
            embeddings=[embedding],
            metadatas=[{"source": "api", "topic": req.topic, "priority": req.priority}],
        )
        return {"success": True, "message": "文档已添加"}
    except Exception as exc:
        logger.error(f"添加知识文档失败: {exc}")
        return {"success": False, "error": str(exc)}


@router.delete("/{doc_id}")
async def delete_knowledge(doc_id: str):
    """删除知识库文档。"""
    vs = _get_vs()
    if vs is None:
        return {"success": False, "error": "向量存储不可用"}

    ok = vs.delete_knowledge(doc_id)
    return {"success": ok, "message": "已删除" if ok else "删除失败"}
