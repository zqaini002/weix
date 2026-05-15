"""向量数据库管理器。

基于 ChromaDB 持久化存储，管理三个 collection：
1. knowledge_base      — 知识库文档（持久化，跨会话）
2. conversation_memory — 对话摘要向量（持久化，跨会话长期记忆）
3. ai_self_memory      — AI 已说内容缓存（用于语义去重检测）
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_config

logger = logging.getLogger(__name__)

COLLECTION_KNOWLEDGE = "knowledge_base"
COLLECTION_CONVERSATION = "conversation_memory"
COLLECTION_AI_SELF = "ai_self_memory"

DEFAULT_PERSIST_DIR = "data/chroma"
DUPLICATE_SIMILARITY_THRESHOLD = 0.85
DEFAULT_SEARCH_K = 3
AI_SELF_MEMORY_MAX = 200


def _hash_id(text: str, prefix: str = "") -> str:
    """生成短哈希 ID。"""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{sha}" if prefix else sha


def _build_project_dir() -> str:
    """获取相对于项目根目录的持久化路径。"""
    config = get_config()
    base = getattr(config, "chroma_persist_dir", "")
    if base:
        return base

    # 默认路径：项目根下的 data/chroma
    candidate = Path(__file__).parent.parent.parent.parent / "data" / "chroma"
    return str(candidate)


class VectorStoreManager:
    """向量数据库管理器。

    管理三个 ChromaDB collection：
    - knowledge_base: 知识库文档（如 FAQ、价格表、流程说明）
    - conversation_memory: 对话摘要（长期记忆，跨会话检索）
    - ai_self_memory: AI 已回复内容（语义去重）

    Attributes:
        _client: ChromaDB PersistentClient 实例。
        knowledge_base: 知识库 collection。
        conversation_memory: 对话记忆 collection。
        ai_self_memory: AI 自省 collection。
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
    ) -> None:
        persist_dir = persist_dir or _build_project_dir()

        try:
            os.makedirs(persist_dir, exist_ok=True)
        except PermissionError:
            pass

        # 权限容错：如果默认目录不可写，退回临时目录
        if not os.access(persist_dir, os.W_OK | os.R_OK):
            import tempfile
            alt_dir = os.path.join(tempfile.gettempdir(), "weix_chroma")
            os.makedirs(alt_dir, exist_ok=True)
            logger.warning(
                f"ChromaDB 目录不可写: {persist_dir}，已退回临时目录: {alt_dir}"
            )
            persist_dir = alt_dir

        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        self.knowledge_base = self._client.get_or_create_collection(
            name=COLLECTION_KNOWLEDGE,
            metadata={"hnsw:space": "cosine"},
        )
        self.conversation_memory = self._client.get_or_create_collection(
            name=COLLECTION_CONVERSATION,
            metadata={"hnsw:space": "cosine"},
        )
        self.ai_self_memory = self._client.get_or_create_collection(
            name=COLLECTION_AI_SELF,
            metadata={"hnsw:space": "cosine"},
        )

        logger.info(
            f"VectorStoreManager initialized at {persist_dir}: "
            f"kb={self.knowledge_base.count()}, "
            f"conv={self.conversation_memory.count()}, "
            f"self={self.ai_self_memory.count()}"
        )

    # ------------------------------------------------------------------
    # 知识库 (knowledge_base)
    # ------------------------------------------------------------------

    def add_knowledge(
        self,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: Optional[list[dict]] = None,
        ids: Optional[list[str]] = None,
    ) -> None:
        """添加知识库文档。

        Args:
            texts: 文档文本列表。
            embeddings: 对应的向量列表。
            metadatas: 元数据列表（source, topic, priority 等）。
            ids: 文档 ID 列表。
        """
        if not texts:
            return

        if ids is None:
            ids = [_hash_id(t, "kb") for t in texts]
        if metadatas is None:
            metadatas = [{"source": "manual", "added_at": time.time()} for _ in texts]

        self.knowledge_base.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.info(f"Added {len(texts)} documents to knowledge_base")

    def search_knowledge(
        self,
        query_embedding: list[float],
        k: int = DEFAULT_SEARCH_K,
        threshold: float = 0.6,
    ) -> list[dict]:
        """检索知识库中相关文档。

        Args:
            query_embedding: 查询向量。
            k: 返回结果数。
            threshold: 相似度阈值（低于此值的结果被过滤）。

        Returns:
            文档列表，每个包含 text, metadata, distance。
        """
        results = self.knowledge_base.query(
            query_embeddings=[query_embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        docs: list[dict] = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i]
                if isinstance(distance, (int, float)) and distance < (1.0 - threshold):
                    docs.append({
                        "id": doc_id,
                        "text": results["documents"][0][i] or "",
                        "metadata": results["metadatas"][0][i] or {},
                        "distance": distance,
                    })

        logger.debug(f"Knowledge search: {len(docs)} results (k={k})")
        return docs

    def delete_knowledge(self, doc_id: str) -> bool:
        """删除知识库文档。"""
        try:
            self.knowledge_base.delete(ids=[doc_id])
            logger.info(f"Deleted knowledge doc: {doc_id}")
            return True
        except Exception as exc:
            logger.error(f"Failed to delete knowledge doc {doc_id}: {exc}")
            return False

    def list_knowledge(self, limit: int = 100) -> list[dict]:
        """列出知识库文档。"""
        try:
            results = self.knowledge_base.get(
                limit=limit,
                include=["documents", "metadatas"],
            )
            docs = []
            if results["ids"]:
                for i, doc_id in enumerate(results["ids"]):
                    docs.append({
                        "id": doc_id,
                        "text": results["documents"][i] if results["documents"] else "",
                        "metadata": results["metadatas"][i] if results["metadatas"] else {},
                    })
            return docs
        except Exception as exc:
            logger.error(f"Failed to list knowledge: {exc}")
            return []

    # ------------------------------------------------------------------
    # 对话记忆 (conversation_memory) -- 长期记忆
    # ------------------------------------------------------------------

    def add_conversation_summary(
        self,
        session_id: str,
        summary: str,
        embedding: list[float],
    ) -> None:
        """存储对话摘要到长期记忆。

        Args:
            session_id: 会话标识符。
            summary: 摘要文本。
            embedding: 摘要的向量表示。
        """
        doc_id = _hash_id(f"{session_id}:{time.time()}", "conv")
        self.conversation_memory.add(
            ids=[doc_id],
            documents=[summary],
            embeddings=[embedding],
            metadatas=[{
                "session_id": session_id,
                "timestamp": time.time(),
                "type": "summary",
            }],
        )
        logger.debug(f"Added conversation summary for {session_id}")

    def search_similar_conversations(
        self,
        query_embedding: list[float],
        k: int = DEFAULT_SEARCH_K,
        exclude_session: str = "",
    ) -> list[str]:
        """检索相似的历史对话摘要。

        Args:
            query_embedding: 查询向量。
            k: 返回结果数。
            exclude_session: 排除的 session_id（避免检索到当前对话自己）。

        Returns:
            相似摘要的文本列表。
        """
        results = self.conversation_memory.query(
            query_embeddings=[query_embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        docs: list[str] = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] or {}
                if exclude_session and meta.get("session_id") == exclude_session:
                    continue
                distance = results["distances"][0][i] or 0
                if distance < 0.6:  # cosine distance: 0=identical, 2=opposite
                    docs.append(results["documents"][0][i] or "")

        logger.debug(f"Similar conversations: {len(docs)} results")
        return docs

    # ------------------------------------------------------------------
    # AI 自省 (ai_self_memory) -- 去重检测
    # ------------------------------------------------------------------

    def check_duplicate(
        self,
        response_embedding: list[float],
        threshold: float = DUPLICATE_SIMILARITY_THRESHOLD,
    ) -> tuple[bool, str]:
        """检查 AI 是否说过类似的话（语义去重）。

        Args:
            response_embedding: 待检查的回复向量。
            threshold: 相似度阈值，超过视为重复。

        Returns:
            (is_duplicate, similar_text): 是否重复及相似文本。
        """
        results = self.ai_self_memory.query(
            query_embeddings=[response_embedding],
            n_results=1,
            include=["documents", "distances"],
        )

        if results["ids"] and results["ids"][0]:
            distance = results["distances"][0][0] or 0
            similarity = 1.0 - distance / 2.0  # cosine distance -> similarity
            if similarity >= threshold:
                similar_text = results["documents"][0][0] or ""
                logger.debug(f"Duplicate detected: similarity={similarity:.3f}")
                return True, similar_text
        return False, ""

    def remember_response(
        self,
        session_id: str,
        response: str,
        embedding: list[float],
    ) -> None:
        """记录 AI 说过的话（用于后续去重检测）。

        Args:
            session_id: 会话标识符。
            response: AI 回复文本。
            embedding: 回复的向量表示。
        """
        doc_id = _hash_id(f"{session_id}:{time.time()}", "self")
        self.ai_self_memory.add(
            ids=[doc_id],
            documents=[response],
            embeddings=[embedding],
            metadatas=[{
                "session_id": session_id,
                "timestamp": time.time(),
            }],
        )

        # 控制容量：超过上限时删除最旧的条目
        count = self.ai_self_memory.count()
        if count > AI_SELF_MEMORY_MAX:
            self._trim_ai_self_memory()

    def _trim_ai_self_memory(self) -> None:
        """裁剪 ai_self_memory 到最大容量的一半。"""
        try:
            results = self.ai_self_memory.get(include=["metadatas"])
            if not results["ids"]:
                return

            entries = []
            for i, doc_id in enumerate(results["ids"]):
                meta = results["metadatas"][i] or {}
                ts = meta.get("timestamp", 0)
                entries.append((ts, doc_id))

            entries.sort()
            to_delete = entries[: len(entries) - AI_SELF_MEMORY_MAX // 2]
            if to_delete:
                ids_to_delete = [e[1] for e in to_delete]
                self.ai_self_memory.delete(ids=ids_to_delete)
                logger.debug(f"Trimmed {len(ids_to_delete)} entries from ai_self_memory")
        except Exception as exc:
            logger.warning(f"Failed to trim ai_self_memory: {exc}")

    def get_recent_responses(
        self,
        session_id: str,
        k: int = 5,
    ) -> list[str]:
        """获取指定会话最近的 AI 回复列表。

        Args:
            session_id: 会话标识符。
            k: 返回数量。

        Returns:
            最近回复文本列表。
        """
        try:
            results = self.ai_self_memory.get(
                include=["documents", "metadatas"],
            )

            entries: list[tuple[float, str]] = []
            if results["ids"]:
                for i, doc_id in enumerate(results["ids"]):
                    meta = results["metadatas"][i] or {}
                    if meta.get("session_id") == session_id:
                        ts = meta.get("timestamp", 0)
                        text = results["documents"][i] or ""
                        entries.append((ts, text))

            entries.sort(key=lambda x: x[0], reverse=True)
            return [text for _, text in entries[:k]]
        except Exception as exc:
            logger.warning(f"Failed to get recent responses: {exc}")
            return []
