"""Embedding 管理器。

将文本转换为向量，用于语义检索和去重检测。

支持的 Embedding 后端（按优先级）：
1. local — 本地 sentence-transformers 模型（默认，离线可用，零 API 依赖）
2. siliconflow — SiliconFlow API (BAAI/bge-large-zh-v1.5)
3. openai — OpenAI text-embedding-3-small
4. dashscope — 阿里云 DashScope text-embedding-v3

注意：DeepSeek 不支持 embeddings API，不要使用。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCAL_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
LOCAL_EMBEDDING_DIMENSION = 384

PROVIDER_EMBEDDING_MODELS: dict[str, str] = {
    "siliconflow": "BAAI/bge-large-zh-v1.5",
    "openai": "text-embedding-3-small",
    "dashscope": "text-embedding-v3",
    "local": LOCAL_EMBEDDING_MODEL,
}

PROVIDER_EMBEDDING_URLS: dict[str, str] = {
    "siliconflow": "https://api.siliconflow.cn/v1",
    "openai": "https://api.openai.com/v1",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}

PROVIDER_DIMENSIONS: dict[str, int] = {
    "local": 384,
    "siliconflow": 1024,
    "openai": 1536,
    "dashscope": 1024,
}

DEFAULT_BATCH_SIZE = 20


def _sentence_transformers_cache_dirs() -> list[Path]:
    """返回 sentence-transformers/HuggingFace 常见本地缓存目录。"""
    candidates: list[Path] = []
    for env_name in ("SENTENCE_TRANSFORMERS_HOME", "HF_HOME"):
        value = os.getenv(env_name)
        if value:
            candidates.append(Path(value))

    home = Path.home()
    candidates.extend([
        home / ".cache" / "torch" / "sentence_transformers",
        home / ".cache" / "huggingface" / "hub",
    ])
    return candidates


def can_load_local_embedding(model: str = LOCAL_EMBEDDING_MODEL) -> bool:
    """判断本地 embedding 模型是否可离线加载。"""
    model_dir_name = f"sentence-transformers_{model.replace('/', '_')}"
    hub_dir_name = f"models--sentence-transformers--{model.replace('/', '--')}"
    for cache_dir in _sentence_transformers_cache_dirs():
        if (cache_dir / model_dir_name).exists() or (cache_dir / hub_dir_name).exists():
            return True
    return False


def get_local_embedding_cache_status(model: str = LOCAL_EMBEDDING_MODEL) -> str:
    """返回本地 embedding 模型缓存状态文案。"""
    if can_load_local_embedding(model):
        return "已缓存"
    return "未缓存，将在后台自动下载"


class EmbeddingManager:
    """Embedding 管理器。

    默认使用本地 sentence-transformers 模型（离线、零 API 成本）。
    可通过 provider 参数切换到 API 后端。

    Attributes:
        _provider: 当前后端名称。
        _client: embedding 客户端（SentenceTransformer 或 OpenAIEmbeddings）。
        _dimension: embedding 向量维度。
    """

    def __init__(
        self,
        provider: str = "local",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._provider = provider
        self._model = model or PROVIDER_EMBEDDING_MODELS.get(provider, LOCAL_EMBEDDING_MODEL)
        self._dimension = PROVIDER_DIMENSIONS.get(provider, LOCAL_EMBEDDING_DIMENSION)
        self._client = None
        self._api_key = api_key
        self._base_url = base_url

        logger.info(
            f"EmbeddingManager configured: provider={provider}, model={self._model}, dim={self._dimension}"
        )

    # ------------------------------------------------------------------
    # 延迟初始化
    # ------------------------------------------------------------------

    def _ensure_client(self):
        """延迟初始化 embedding 客户端（首次调用时才加载模型/创建连接）。"""
        if self._client is not None:
            return

        if self._provider == "local":
            self._init_local()
        else:
            self._init_api()

    def _init_local(self) -> None:
        """初始化本地 sentence-transformers 模型。"""
        try:
            from sentence_transformers import SentenceTransformer

            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

            allow_download = not can_load_local_embedding(self._model)
            if allow_download:
                logger.info(
                    "本地 Embedding 模型未找到，开始后台下载: %s。"
                    "首次下载可能需要几分钟，请保持网络连接。",
                    self._model,
                )
            else:
                logger.info("正在加载本地 Embedding 模型: %s", self._model)

            self._client = SentenceTransformer(
                self._model,
                local_files_only=not allow_download,
            )
            self._dimension = self._client.get_embedding_dimension()
            logger.info(
                "本地 Embedding 模型已就绪: %s, dim=%s",
                self._model,
                self._dimension,
            )
        except Exception as exc:
            logger.error(
                "本地 Embedding 模型加载/下载失败: %s。请检查网络，"
                "或预先下载 sentence-transformers/%s。",
                exc,
                self._model,
            )
            raise

    def _init_api(self) -> None:
        """初始化 API 后端。"""
        try:
            from langchain_openai import OpenAIEmbeddings
            from app.config import get_config

            config = get_config()
            ai_cfg = config.ai if hasattr(config, "ai") else {}

            api_key = self._api_key or ai_cfg.get("api_key", "") or os.getenv("DEEPSEEK_API_KEY", "")
            base_url = self._base_url or PROVIDER_EMBEDDING_URLS.get(
                self._provider, ""
            )

            kwargs: dict = {
                "model": self._model,
                "openai_api_key": api_key,
                "openai_api_base": base_url,
            }

            self._client = OpenAIEmbeddings(**kwargs)
            logger.info(
                f"API embedding client initialized: provider={self._provider}, model={self._model}"
            )
        except Exception as exc:
            logger.error(f"Failed to initialize API embedding client: {exc}")
            raise

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def dimension(self) -> int:
        self._ensure_client()
        return self._dimension

    # ------------------------------------------------------------------
    # Embedding API
    # ------------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本转向量。"""
        if not texts:
            return []

        self._ensure_client()

        embeddings = []
        for i in range(0, len(texts), DEFAULT_BATCH_SIZE):
            batch = texts[i : i + DEFAULT_BATCH_SIZE]

            if self._provider == "local":
                batch_embeddings = self._client.encode(
                    batch, normalize_embeddings=True
                ).tolist()
            else:
                batch_embeddings = self._client.embed_documents(batch)

            embeddings.extend(batch_embeddings)

        logger.debug(f"Embedded {len(texts)} texts, dim={self._dimension}")
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        """单条查询文本转向量。"""
        if not text:
            self._ensure_client()
            return [0.0] * self._dimension

        self._ensure_client()

        if self._provider == "local":
            vec = self._client.encode(text, normalize_embeddings=True).tolist()
            if isinstance(vec[0], list):
                vec = vec[0]
            return vec
        else:
            return self._client.embed_query(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量转向量的别名方法。"""
        return self.embed(texts)

    async def embed_query_async(self, text: str) -> list[float]:
        """embed_query 的异步版本（线程池中执行，避免阻塞事件循环）。"""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.embed_query, text)

    async def embed_async(self, texts: list[str]) -> list[list[float]]:
        """embed 的异步版本（线程池中执行）。"""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.embed, texts)


# ------------------------------------------------------------------
# 全局单例
# ------------------------------------------------------------------

_embedding_manager_instances: dict[str, EmbeddingManager] = {}
_em_lock = __import__("threading").Lock()


def get_embedding_manager(provider: str = "local") -> EmbeddingManager:
    """获取按 provider 缓存的全局单例 EmbeddingManager（线程安全）。"""
    if provider not in _embedding_manager_instances:
        with _em_lock:
            if provider not in _embedding_manager_instances:
                _embedding_manager_instances[provider] = EmbeddingManager(provider=provider)
    return _embedding_manager_instances[provider]
