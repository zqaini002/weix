"""RAG 检索增强生成管线。

在 AI 回复前注入相关上下文：
1. 检索知识库中相关文档
2. 检索相似历史对话摘要（长期记忆）
3. 检查 AI 是否说过类似内容（去重提醒）
4. 构建增强 context 注入到 system prompt
"""

from __future__ import annotations

import logging
from typing import Optional

from app.ai.embeddings import EmbeddingManager
from app.ai.vector_store import VectorStoreManager

logger = logging.getLogger(__name__)


class RAGPipeline:
    """RAG 检索增强生成管线。

    封装了知识检索 + 记忆召回 + AI 自省的全流程。

    Attributes:
        _embeddings: Embedding 管理器。
        _vector_store: 向量存储管理器。
    """

    def __init__(
        self,
        embedding_manager: EmbeddingManager,
        vector_store: VectorStoreManager,
    ) -> None:
        self._embeddings = embedding_manager
        self._vector_store = vector_store
        logger.info("RAGPipeline initialized")

    async def build_context(
        self,
        user_message: str,
        session_id: str,
        is_group: bool = False,
    ) -> dict:
        """构建 RAG 增强上下文。

        Args:
            user_message: 用户消息文本。
            session_id: 会话标识符。
            is_group: 是否群聊。

        Returns:
            包含所有 RAG 上下文的字典：
            {
                "knowledge_docs": str,        # 知识库检索结果（格式化文本）
                "similar_conversations": str,  # 相似历史对话摘要
                "duplicate_warning": str,      # AI 自省提醒
            }
        """
        query_embedding = await self._embeddings.embed_query_async(user_message)

        # 1. 检索知识库
        knowledge_docs = await self._retrieve_knowledge(query_embedding)

        # 2. 检索当前会话内的相似历史对话，避免私聊之间串记忆和人称归属混乱。
        similar_convos = await self._retrieve_similar_conversations(
            query_embedding, session_id=session_id
        )

        # 3. AI 自省去重提醒
        duplicate_warning = self._build_duplicate_warning(session_id)

        return {
            "knowledge_docs": knowledge_docs,
            "similar_conversations": similar_convos,
            "duplicate_warning": duplicate_warning,
        }

    async def _retrieve_knowledge(self, query_embedding: list[float]) -> str:
        """检索知识库并格式化为文本。"""
        try:
            docs = await self._vector_store.search_knowledge_async(query_embedding, k=3)
            if not docs:
                return "（暂无相关知识库内容）"

            lines = []
            for i, doc in enumerate(docs, 1):
                text = doc.get("text", "")
                topic = doc.get("metadata", {}).get("topic", "")
                if topic:
                    lines.append(f"{i}. [{topic}] {text}")
                else:
                    lines.append(f"{i}. {text}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(f"Knowledge retrieval failed: {exc}")
            return "（知识库检索暂时不可用）"

    async def _retrieve_similar_conversations(
        self,
        query_embedding: list[float],
        session_id: str = "",
        exclude_session: str = "",
    ) -> str:
        """检索相似历史对话摘要。"""
        try:
            summaries = await self._vector_store.search_similar_conversations_async(
                query_embedding,
                k=3,
                session_id=session_id,
                exclude_session=exclude_session,
            )
            if not summaries:
                return "（暂无相关历史对话）"

            lines = ["以下是历史中与当前话题相关的对话摘要："]
            for i, s in enumerate(summaries, 1):
                lines.append(f"  {i}. {s}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(f"Conversation retrieval failed: {exc}")
            return "（历史对话检索暂时不可用）"

    def _build_duplicate_warning(self, session_id: str) -> str:
        """构建 AI 自省去重提醒。"""
        try:
            recent = self._vector_store.get_recent_responses(session_id, k=5)
            if not recent:
                return "这是你第一次和这位用户对话，可以自由发挥。"

            lines = ["## AI 自省提醒"]
            lines.append("你在最近的对话中回复过以下内容（简化版）：")
            for i, resp in enumerate(recent, 1):
                short = resp[:80] + "..." if len(resp) > 80 else resp
                lines.append(f"  {i}. 「{short}」")
            lines.append("请避免完全重复上述内容。如果问题相同，用不同方式表达或补充新信息。")
            return "\n".join(lines)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    async def check_duplicate_and_remember(
        self,
        session_id: str,
        response: str,
    ) -> bool:
        """检查回复是否重复，并记住它。

        Args:
            session_id: 会话标识符。
            response: AI 回复。

        Returns:
            True 表示检测到重复。
        """
        try:
            embedding = await self._embeddings.embed_query_async(response)
            is_dup, similar = await self._vector_store.check_duplicate_async(embedding)
            if not is_dup:
                await self._vector_store.remember_response_async(
                    session_id, response, embedding
                )
            return is_dup
        except Exception as exc:
            logger.warning(f"Duplicate check failed: {exc}")
            return False

    async def remember_conversation(
        self,
        session_id: str,
        summary: str,
    ) -> None:
        """将对话摘要写入长期记忆。

        Args:
            session_id: 会话标识符。
            summary: 摘要文本。
        """
        try:
            embedding = await self._embeddings.embed_query_async(summary)
            await self._vector_store.add_conversation_summary_async(
                session_id, summary, embedding
            )
        except Exception as exc:
            logger.warning(f"Failed to store conversation summary: {exc}")
