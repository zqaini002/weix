"""对话记忆管理模块。

为每个会话维护独立的对话历史，使用 LangGraph MemorySaver 实现持久化。
会话隔离通过 thread_id 完成。

当历史消息超过阈值时，自动调用 LLM 进行摘要压缩，摘要写入向量库作为长期记忆。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_K: int = 20
AUTO_SUMMARY_THRESHOLD: int = 40


class ConversationMemory:
    """对话记忆管理器。

    作为管理 wrapper，跟踪每个 session 的消息计数和摘要状态。
    实际消息持久化由 LangGraph MemorySaver 完成。

    Attributes:
        k: 记忆窗口大小。
        llm: 可选的 LLM 实例，用于自动摘要压缩。
        vector_store: 可选的向量存储（摘要写入长期记忆）。
    """

    def __init__(
        self,
        k: int = DEFAULT_MEMORY_K,
        llm: Any = None,
        vector_store: Any = None,
    ) -> None:
        self.k = k
        self.llm = llm
        self._vector_store = vector_store
        self._message_counts: dict[str, int] = {}
        self._session_summaries: dict[str, str] = {}
        self._pending_summarize: set[str] = set()

    # ------------------------------------------------------------------
    # 会话 ID 生成
    # ------------------------------------------------------------------

    @staticmethod
    def make_session_id(is_group: bool, identifier: str) -> str:
        """生成标准化的 session_id。"""
        prefix = "group" if is_group else "private"
        return f"{prefix}:{identifier}"

    # ------------------------------------------------------------------
    # 消息追踪
    # ------------------------------------------------------------------

    def record_turn(
        self,
        session_id: str,
        user_message: str,
        ai_response: str,
    ) -> None:
        """记录一轮对话（计数追踪）。"""
        self._message_counts[session_id] = (
            self._message_counts.get(session_id, 0) + 1
        )

    def get_turn_count(self, session_id: str) -> int:
        """获取指定会话的对话轮数。"""
        return self._message_counts.get(session_id, 0)

    # ------------------------------------------------------------------
    # 自动摘要（真正执行 LLM 压缩）
    # ------------------------------------------------------------------

    async def maybe_summarize(self, session_id: str) -> str | None:
        """当轮数达到阈值时，执行 LLM 摘要压缩。

        1. 从 checkpointer 获取消息历史
        2. 调用 SummaryAgent 生成摘要
        3. 摘要写入向量库（长期记忆）
        4. 重置计数
        """
        count = self._message_counts.get(session_id, 0)
        if count < AUTO_SUMMARY_THRESHOLD:
            return None
        if session_id in self._pending_summarize:
            return None
        if not self.llm:
            logger.debug(f"Skip auto-summary for {session_id}: no LLM configured")
            return None

        self._pending_summarize.add(session_id)
        try:
            from app.ai.agent import SummaryAgent

            agent = SummaryAgent()

            # 从已有摘要 + 最近消息构建摘要输入
            existing_summary = self._session_summaries.get(session_id, "")
            if existing_summary:
                conversation = (
                    f"已有摘要: {existing_summary}\n"
                    f"最近新增了约 {count} 轮对话。请生成合并摘要。"
                )
            else:
                conversation = f"对话已进行了约 {count} 轮。请基于当前上下文生成摘要。"

            summary = await agent.generate_summary(
                messages=conversation,
                style="concise",
                max_length=300,
            )

            if summary:
                self._session_summaries[session_id] = summary
                self._message_counts[session_id] = 0

                # 写入向量库（长期记忆）
                if self._vector_store:
                    try:
                        from app.ai.embeddings import EmbeddingManager
                        em = EmbeddingManager(provider="local")
                        embedding = em.embed_query(summary)
                        self._vector_store.add_conversation_summary(
                            session_id, summary, embedding
                        )
                    except Exception as exc:
                        logger.warning(f"Failed to store summary in vector DB: {exc}")

                logger.info(
                    f"Auto-summarized session {session_id}: "
                    f"summary_len={len(summary)}"
                )
            return summary
        except Exception as e:
            logger.error(f"Auto-summarization failed for {session_id}: {e}")
            return None
        finally:
            self._pending_summarize.discard(session_id)

    # ------------------------------------------------------------------
    # 摘要管理
    # ------------------------------------------------------------------

    def set_summary(self, session_id: str, summary: str) -> None:
        """设置会话的摘要文本。"""
        self._session_summaries[session_id] = summary

    def get_summary(self, session_id: str) -> str:
        """获取会话的摘要文本。"""
        return self._session_summaries.get(session_id, "")

    # ------------------------------------------------------------------
    # 记忆清除
    # ------------------------------------------------------------------

    def clear_memory(self, session_id: str) -> None:
        """清除指定会话的全部记忆追踪数据。"""
        self._message_counts.pop(session_id, None)
        self._session_summaries.pop(session_id, None)
        logger.info(f"Cleared memory for session: {session_id}")

    def clear_all(self) -> None:
        """清除所有会话的记忆追踪数据。"""
        for session_id in list(self._message_counts.keys()):
            self.clear_memory(session_id)
        logger.info("Cleared all conversation memories")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def get_all_sessions(self) -> list[str]:
        """获取所有活跃的会话 ID 列表。"""
        return list(self._message_counts.keys())

    def memory_stats(self) -> dict[str, int]:
        """获取各会话的消息统计。"""
        return dict(self._message_counts)
