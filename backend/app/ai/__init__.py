"""AI 引擎模块 - LLM、RAG、记忆、安全、Embedding 一体化管理。"""

from app.ai.agent import WeixAgent, SummaryAgent
from app.ai.models import LLMConfig, create_llm
from app.ai.memory import ConversationMemory
from app.ai.embeddings import EmbeddingManager
from app.ai.vector_store import VectorStoreManager
from app.ai.rag import RAGPipeline
from app.ai.prompts import (
    SYSTEM_PROMPT,
    GROUP_CHAT_PROMPT,
    PRIVATE_CHAT_PROMPT,
    SUMMARY_PROMPT,
    STATISTICS_PROMPT,
)
from app.ai.tools import (
    search_web,
    get_weather,
    get_current_time,
    calculate,
    query_statistics,
    create_tools,
)

__all__ = [
    "WeixAgent",
    "SummaryAgent",
    "LLMConfig",
    "create_llm",
    "ConversationMemory",
    "EmbeddingManager",
    "VectorStoreManager",
    "RAGPipeline",
    "SYSTEM_PROMPT",
    "GROUP_CHAT_PROMPT",
    "PRIVATE_CHAT_PROMPT",
    "SUMMARY_PROMPT",
    "STATISTICS_PROMPT",
    "search_web",
    "get_weather",
    "get_current_time",
    "calculate",
    "query_statistics",
    "create_tools",
]
