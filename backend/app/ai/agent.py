"""LangChain Agent 引擎。

提供 WeixAgent（会话型 AI 助手）和 SummaryAgent（摘要/分析型 Agent），
基于 LangGraph create_react_agent + RAG + 长期记忆 + AI 自省。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, AIMessage

from app.ai.models import LLMConfig, create_llm
from app.ai.prompts import (
    STATISTICS_PROMPT,
    SUMMARY_PROMPT,
    format_prompt,
    get_prompt_for_context,
)
from app.ai.tools import create_tools
from app.utils.paths import get_data_dir

logger = logging.getLogger(__name__)

MAX_RETRIES: int = 3
AGENT_TIMEOUT: int = 60

CHECKPOINTS_FILE = "checkpoints.json"


def _checkpoints_path() -> str:
    """获取 checkpoints 持久化文件路径。"""
    return str(get_data_dir() / CHECKPOINTS_FILE)


class WeixAgent:
    """微信 AI 助手主入口 — 完整 AI 工程化版本。

    集成能力：
    - LangGraph ReAct Agent (create_react_agent)
    - RAG 检索增强 (知识库 + 长期记忆 + AI 自省)
    - 短期记忆 (MemorySaver, thread_id 隔离)
    - 长期记忆 (ChromaDB 向量存储 + SQLite 结构化)
    - 嵌入向量 (DeepSeek/OpenAI Embedding)
    - 输入/输出安全 (guard.py)
    - Checkpoint 持久化 (JSON 文件, 重启不丢失)
    """

    def __init__(
        self,
        llm_config: LLMConfig | None = None,
        memory: Any = None,
        tools: list | None = None,
    ) -> None:
        self.llm_config = llm_config
        self.llm = create_llm(llm_config)

        self.tools = tools if tools is not None else create_tools()

        # 嵌入向量管理器（延迟初始化，避免无 API key 时崩溃）
        self._embedding_manager = None
        self._vector_store = None
        self._rag = None

        # 记忆管理器
        if memory is None:
            from app.ai.memory import ConversationMemory
            self.memory = ConversationMemory(k=20, llm=self.llm)
        else:
            self.memory = memory

        # Checkpointer: 先尝试从磁盘加载，否则新建
        self._checkpointer = MemorySaver()
        self._load_checkpoints()

        logger.info(
            f"WeixAgent initialized: provider={self.llm_config.provider if self.llm_config else 'default'}, "
            f"tools={len(self.tools)}, memory_k={getattr(self.memory, 'k', 'N/A')}"
        )

    # ------------------------------------------------------------------
    # 延迟初始化（RAG 组件）
    # ------------------------------------------------------------------

    def _ensure_rag(self) -> None:
        """延迟初始化 RAG 相关组件。"""
        if self._rag is not None:
            return

        try:
            from app.ai.embeddings import get_embedding_manager
            from app.ai.vector_store import get_vector_store
            from app.ai.rag import RAGPipeline

            provider = "local"
            self._embedding_manager = get_embedding_manager(provider=provider)
            self._vector_store = get_vector_store()
            self._rag = RAGPipeline(self._embedding_manager, self._vector_store)
            logger.info("RAG pipeline initialized")
        except Exception as exc:
            logger.warning(f"RAG initialization failed (non-fatal): {exc}")
            self._rag = None

    # ------------------------------------------------------------------
    # Agent 创建
    # ------------------------------------------------------------------

    def _create_agent(self, session_id: str, is_group: bool, context: dict):
        """创建 LangGraph ReAct Agent。

        每次调用动态生成 system prompt（含 RAG 上下文），
        通过 checkpointer 的 thread_id 实现 session 隔离。
        """
        clean_context = {k: v for k, v in context.items() if k != "is_group"}
        system_template = get_prompt_for_context(
            is_group=is_group,
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **clean_context,
        )

        # 注入 persona（用户语言风格）
        persona_text = self._get_persona_prompt(is_group=is_group)
        guard_mode = "assistant" if is_group or not persona_text else "self"
        from app.ai.guard import get_hardened_system_prompt
        system_template = get_hardened_system_prompt(
            system_template,
            persona_mode=guard_mode,
        )
        if persona_text:
            system_template = system_template.rstrip() + "\n\n" + persona_text

        # 注入用户自定义 system prompt（前端 AIConfig 中配置）
        from app.config import get_config
        user_prompt = get_config().ai.get("system_prompt", "")
        if user_prompt and user_prompt.strip():
            system_template = system_template.rstrip() + "\n\n## 用户自定义\n" + user_prompt.strip()

        agent = create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=system_template,
            checkpointer=self._checkpointer,
        )
        return agent

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------

    async def chat(
        self,
        message: str,
        session_id: str = "",
        context: dict | None = None,
    ) -> str:
        """处理用户消息，返回 AI 回复。

        完整链路: 消毒 → RAG 检索 → 记忆注入 → Agent 调用 → 输出去重 → 持久化
        """
        from app.ai.guard import sanitize_user_input, check_output_safety

        from app.ai.counter import increment as increment_ai_counter
        increment_ai_counter()

        safe_message, warnings = sanitize_user_input(message)
        if warnings:
            logger.warning(f"Input warnings for session={session_id}: {warnings}")

        context = dict(context) if context else {}
        is_group = context.pop("is_group", False)
        user_wxid = context.get("user_wxid", context.get("user_name", "unknown"))
        room_id = context.get("room_id", "")

        if not session_id:
            if is_group and room_id:
                session_id = f"group:{room_id}"
            elif not is_group and user_wxid:
                session_id = f"private:{user_wxid}"
            else:
                raise ValueError(
                    "无法确定 session_id，请提供 context 中的 is_group 和 room_id/user_wxid"
                )

        context.setdefault("user_name", user_wxid)
        context.setdefault("room_name", context.get("room_name", ""))
        context.setdefault("room_id", room_id)
        context.setdefault("chat_context", "无历史对话")

        # RAG 检索增强上下文
        self._ensure_rag()
        if self._rag is not None:
            try:
                rag_result = await self._rag.build_context(
                    user_message=safe_message,
                    session_id=session_id,
                    is_group=is_group,
                )
                context.setdefault("knowledge_context", rag_result.get("knowledge_docs", "（暂无知识库内容）"))
                context.setdefault("memory_context", rag_result.get("similar_conversations", "（暂无历史对话）"))
                context.setdefault("self_awareness", rag_result.get("duplicate_warning", ""))
            except Exception as exc:
                logger.warning(f"RAG context build failed: {exc}")
                context.setdefault("knowledge_context", "（知识库暂时不可用）")
                context.setdefault("memory_context", "（历史对话暂时不可用）")
                context.setdefault("self_awareness", "")
        else:
            context.setdefault("knowledge_context", "（知识库未初始化）")
            context.setdefault("memory_context", "（历史对话不可用）")
            context.setdefault("self_awareness", "")

        logger.info(f"Chat: session={session_id}, is_group={is_group}, msg_len={len(message)}")

        agent = self._create_agent(session_id, is_group, context)
        config = {"configurable": {"thread_id": session_id}}

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        agent.invoke,
                        {"messages": [HumanMessage(content=safe_message)]},
                        config,
                    ),
                    timeout=AGENT_TIMEOUT,
                )

                output = self._extract_output(result)

                if not check_output_safety(output):
                    logger.warning(f"AI output safety check failed: session={session_id}")

                logger.info(
                    f"Chat success: session={session_id}, output_len={len(output)}, attempt={attempt}"
                )

                # 记录对话轮次
                self.memory.record_turn(session_id, message, output)

                # AI 自省去重检查 + 记住回复
                if self._rag is not None and output:
                    try:
                        is_dup = await self._rag.check_duplicate_and_remember(session_id, output)
                        if is_dup:
                            logger.info(f"Similar response detected for session={session_id}")
                    except Exception:
                        pass

                # 自动摘要触发
                await self.memory.maybe_summarize(session_id)

                # 持久化 checkpoint
                self._save_checkpoints()

                return output

            except asyncio.TimeoutError:
                logger.warning(
                    f"Agent timed out after {AGENT_TIMEOUT}s (attempt {attempt}/{MAX_RETRIES})"
                )
                last_error = asyncio.TimeoutError(f"Agent 执行超时 ({AGENT_TIMEOUT}s)")
                if attempt < MAX_RETRIES:
                    self._discard_checkpoint(session_id)
                    agent = self._create_agent(session_id, is_group, context)

            except Exception as e:
                logger.warning(f"Agent error (attempt {attempt}/{MAX_RETRIES}): {e}")
                last_error = e
                if attempt < MAX_RETRIES:
                    self._discard_checkpoint(session_id)
                    agent = self._create_agent(session_id, is_group, context)
                    await asyncio.sleep(1 * attempt)

        error_msg = "抱歉，AI 服务暂时不可用。请稍后再试。"
        if last_error:
            error_msg += f"\n（错误详情: {last_error}）"
        logger.error(
            f"All {MAX_RETRIES} retries failed for session={session_id}: {last_error}"
        )
        return error_msg

    def chat_sync(
        self,
        message: str,
        session_id: str = "",
        context: dict | None = None,
    ) -> str:
        """同步版本的消息处理。"""
        return asyncio.run(self.chat(message, session_id, context))

    # ------------------------------------------------------------------
    # Checkpoint 持久化
    # ------------------------------------------------------------------

    def _save_checkpoints(self) -> None:
        """将 MemorySaver 中的 checkpoints 序列化到 JSON 文件。"""
        try:
            path = _checkpoints_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)

            data = {}
            for thread_id, checkpoint in self._checkpointer._checkpoints.items():
                data[thread_id] = {
                    "checkpoint": checkpoint.get("checkpoint", {}),
                    "metadata": checkpoint.get("metadata", {}),
                    "channel_values": checkpoint.get("channel_values", {}),
                }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.debug(f"Failed to save checkpoints: {exc}")

    def _load_checkpoints(self) -> None:
        """从 JSON 文件恢复 checkpoints 到 MemorySaver。"""
        path = _checkpoints_path()
        if not os.path.exists(path):
            logger.debug("No checkpoints file found, starting fresh")
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data:
                self._checkpointer._checkpoints.update(data)
                logger.info(f"Loaded {len(data)} checkpoints from {path}")
        except Exception as exc:
            logger.warning(f"Failed to load checkpoints: {exc}")

    def _discard_checkpoint(self, session_id: str) -> None:
        """丢弃指定会话的 LangGraph checkpoint，避免失败工具调用污染重试。"""
        try:
            checkpoints = getattr(self._checkpointer, "_checkpoints", None)
            if isinstance(checkpoints, dict) and session_id in checkpoints:
                checkpoints.pop(session_id, None)
                self._save_checkpoints()
                logger.info("Discarded checkpoint for failed session=%s", session_id)
        except Exception as exc:
            logger.debug("Failed to discard checkpoint for %s: %s", session_id, exc)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_output(result: dict) -> str:
        """从 LangGraph agent 返回结果中提取最终 AI 回复文本。"""
        if not isinstance(result, dict):
            return str(result).strip()

        messages = result.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                content = msg.content or ""
                if content.strip():
                    return content.strip()

        output = result.get("output", "")
        return str(output).strip()

    # ------------------------------------------------------------------
    # Persona (语言风格)
    # ------------------------------------------------------------------

    _distiller = None

    @classmethod
    def _get_persona_prompt(cls, is_group: bool = False) -> str:
        """获取缓存的 persona prompt（类级别缓存）。"""
        if cls._distiller is None:
            try:
                from app.ai.style_distiller import StyleDistiller
                cls._distiller = StyleDistiller()
            except Exception:
                return ""

        if cls._distiller.has_persona:
            return cls._distiller.build_prompt(is_group=is_group)
        return ""

    def clear_session(self, session_id: str) -> None:
        """清除指定会话的记忆。"""
        self.memory.clear_memory(session_id)
        logger.info(f"Session cleared: {session_id}")

    # ------------------------------------------------------------------
    # 资源管理
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """关闭时保存 checkpoint 到磁盘。"""
        self._save_checkpoints()
        logger.info("WeixAgent checkpoint saved")


class SummaryAgent:
    """摘要与分析 Agent。"""

    def __init__(self, llm_config: LLMConfig | None = None) -> None:
        self.llm_config = llm_config
        self.llm = create_llm(llm_config)
        logger.info(
            f"SummaryAgent initialized: provider={self.llm_config.provider if self.llm_config else 'default'}"
        )

    async def generate_summary(
        self,
        messages: list[dict] | list[str] | str,
        style: str = "concise",
        max_length: int = 200,
    ) -> str:
        conversation_text = self._normalize_messages(messages)
        prompt = format_prompt(
            SUMMARY_PROMPT,
            conversation=conversation_text,
            style=style,
            max_length=str(max_length),
        )
        return await self._invoke_with_retry(prompt)

    async def analyze_statistics(
        self, stats_data: str | dict, style: str = "friendly"
    ) -> str:
        if isinstance(stats_data, dict):
            import json
            stats_text = json.dumps(stats_data, ensure_ascii=False, indent=2)
        else:
            stats_text = str(stats_data)

        prompt = format_prompt(STATISTICS_PROMPT, stats_data=stats_text, style=style)
        return await self._invoke_with_retry(prompt)

    @staticmethod
    def _normalize_messages(messages: list[dict] | list[str] | str) -> str:
        if isinstance(messages, str):
            return messages

        lines: list[str] = []
        for msg in messages:
            if isinstance(msg, dict):
                sender = msg.get("sender", msg.get("sender_name", "unknown"))
                content = msg.get("content", "")
                lines.append(f"{sender}: {content}")
            elif isinstance(msg, str):
                lines.append(msg)
            else:
                lines.append(str(msg))
        return "\n".join(lines)

    async def _invoke_with_retry(
        self, prompt: str, max_retries: int = MAX_RETRIES
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                messages = [HumanMessage(content=prompt)]
                response = await asyncio.wait_for(
                    asyncio.to_thread(self.llm.invoke, messages),
                    timeout=AGENT_TIMEOUT,
                )
                content = (
                    response.content if hasattr(response, "content") else str(response)
                )
                return str(content).strip()

            except asyncio.TimeoutError:
                logger.warning(f"SummaryAgent timed out (attempt {attempt}/{max_retries})")
                last_error = asyncio.TimeoutError("LLM 调用超时")
            except Exception as e:
                logger.warning(f"SummaryAgent error (attempt {attempt}/{max_retries}): {e}")
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(1 * attempt)

        raise RuntimeError(f"SummaryAgent 调用失败（{max_retries} 次重试后）: {last_error}")

    def generate_summary_sync(
        self, messages: list[dict] | list[str] | str,
        style: str = "concise", max_length: int = 200,
    ) -> str:
        return asyncio.run(self.generate_summary(messages, style, max_length))

    def analyze_statistics_sync(
        self, stats_data: str | dict, style: str = "friendly"
    ) -> str:
        return asyncio.run(self.analyze_statistics(stats_data, style))
