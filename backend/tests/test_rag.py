import asyncio
import sys
import types

chromadb_module = types.ModuleType("chromadb")
chromadb_config_module = types.ModuleType("chromadb.config")

class FakeChromaSettings:
    def __init__(self, **_kwargs):
        pass

chromadb_config_module.Settings = FakeChromaSettings
sys.modules.setdefault("chromadb", chromadb_module)
sys.modules.setdefault("chromadb.config", chromadb_config_module)

from app.ai.rag import RAGPipeline
from app.ai.vector_store import VectorStoreManager


class FakeEmbeddings:
    def embed_query(self, _text):
        return [0.1, 0.2, 0.3]

    async def embed_query_async(self, text):
        return self.embed_query(text)

    async def embed_async(self, texts):
        return [self.embed_query(t) for t in texts]


class FakeVectorStore:
    def __init__(self):
        self.similar_calls = []

    def search_knowledge(self, _query_embedding, k=3):
        return []

    async def search_knowledge_async(self, query_embedding, k=3, threshold=0.6):
        return self.search_knowledge(query_embedding, k)

    def search_similar_conversations(
        self,
        _query_embedding,
        k=3,
        session_id="",
        exclude_session="",
    ):
        self.similar_calls.append(
            {
                "k": k,
                "session_id": session_id,
                "exclude_session": exclude_session,
            }
        )
        if session_id == "private:B":
            return []
        if exclude_session == "private:B":
            return ["A 的隐私摘要"]
        return []

    async def search_similar_conversations_async(
        self, query_embedding, k=3, session_id="", exclude_session=""
    ):
        return self.search_similar_conversations(
            query_embedding, k, session_id, exclude_session
        )

    def get_recent_responses(self, _session_id, k=5):
        return []


def test_rag_context_does_not_inject_other_private_session_memory():
    vector_store = FakeVectorStore()
    rag = RAGPipeline(FakeEmbeddings(), vector_store)

    context = asyncio.run(
        rag.build_context(
            user_message="在吗",
            session_id="private:B",
            is_group=False,
        )
    )

    assert vector_store.similar_calls == [
        {"k": 3, "session_id": "private:B", "exclude_session": ""}
    ]
    assert "A 的隐私摘要" not in context["similar_conversations"]


def test_vector_store_filters_conversation_memory_by_session_id():
    class FakeConversationMemory:
        def __init__(self):
            self.query_kwargs = None

        def query(self, **kwargs):
            self.query_kwargs = kwargs
            return {
                "ids": [["current", "other"]],
                "documents": [["当前会话摘要", "其他会话摘要"]],
                "metadatas": [[
                    {"session_id": "private:B"},
                    {"session_id": "private:A"},
                ]],
                "distances": [[0.1, 0.1]],
            }

    memory = FakeConversationMemory()
    store = VectorStoreManager.__new__(VectorStoreManager)
    store.conversation_memory = memory

    docs = store.search_similar_conversations(
        [0.1, 0.2, 0.3],
        session_id="private:B",
    )

    assert memory.query_kwargs["where"] == {"session_id": "private:B"}
    assert docs == ["当前会话摘要"]
