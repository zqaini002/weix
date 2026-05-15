from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_config
from app.deps import init_database, get_session_factory
from app.utils.logger import setup_logging

logger = logging.getLogger(__name__)


def _try_auto_extract_keys():
    """启动时尝试自动提取微信数据库密钥。"""
    from pathlib import Path

    # 已缓存则跳过
    if Path("data/all_keys.json").exists():
        logger.info("密钥已缓存，跳过提取")
        return

    try:
        from app.core.platform import Platform
        platform = Platform.get()
        extractor = platform.key_extractor
        pid = extractor.find_wechat_process()
        if not pid:
            logger.warning("未找到微信进程，跳过密钥提取")
            return

        logger.info(f"正在从微信进程 {pid} 提取密钥...")
        keys = extractor.scan_memory_for_keys(pid)
        if keys:
            logger.info(f"密钥提取成功: {list(keys.keys())}")
        else:
            logger.warning("密钥提取失败（需要 root 权限或微信已登录）")
    except Exception as e:
        logger.warning(f"密钥提取异常: {e}")


async def _start_auto_reply_pipeline():
    """启动自动回复流水线。"""
    try:
        from app.core.auto_reply_pipeline import AutoReplyPipeline

        pipeline = AutoReplyPipeline(session_factory=get_session_factory())
        await pipeline.start()
        logger.info("自动回复流水线已启动")
        return pipeline
    except Exception as e:
        logger.error(f"启动自动回复流水线失败: {e}")
        return None


def _preload_embeddings():
    """后台线程预加载 embedding 模型，避免首条消息延迟 4~16s。"""
    import threading

    def _load():
        try:
            from app.ai.embeddings import EmbeddingManager
            em = EmbeddingManager(provider="local")
            _ = em.embed_query("warmup")
            logger.info("Embedding 模型预加载完成")
        except Exception as e:
            logger.warning(f"Embedding 预加载失败（非致命）: {e}")

    t = threading.Thread(target=_load, daemon=True)
    t.start()


async def _seed_knowledge():
    """启动时初始化知识库种子数据。"""
    try:
        from app.ai.embeddings import EmbeddingManager
        from app.ai.vector_store import VectorStoreManager
        from app.ai.knowledge_seed import seed_knowledge_base

        em = EmbeddingManager()
        vs = VectorStoreManager()
        count = await seed_knowledge_base(vs, em)
        if count > 0:
            logger.info(f"知识库种子数据初始化完成: {count} 条")
    except Exception as e:
        logger.warning(f"知识库种子初始化失败（非致命）: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    get_config()
    await init_database()
    _try_auto_extract_keys()

    # 初始化知识库种子数据（首次启动）
    await _seed_knowledge()

    # 预加载 embedding 模型（避免首条消息延迟）
    _preload_embeddings()

    # 启动自动回复流水线
    pipeline = await _start_auto_reply_pipeline()

    yield

    # 停止流水线
    if pipeline:
        await pipeline.stop()


app = FastAPI(title="Weix - WeChat Bot", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
from app.api.auth import router as auth_router
from app.api.messages import router as messages_router
from app.api.statistics import router as statistics_router
from app.api.config import router as config_router
from app.api.dashboard import router as dashboard_router
from app.api.platform_api import router as platform_api_router
from app.api.knowledge import router as knowledge_router
from app.api.persona import router as persona_router

app.include_router(auth_router)
app.include_router(messages_router)
app.include_router(statistics_router)
app.include_router(config_router)
app.include_router(dashboard_router)
app.include_router(platform_api_router)
app.include_router(knowledge_router)
app.include_router(persona_router)


@app.get("/api/health")
async def health():
    config = get_config()
    return {"status": "ok", "platform": config.get_platform()}
