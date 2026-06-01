from contextlib import asynccontextmanager
import asyncio
import logging
import sys
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_config
from app.deps import init_database, get_session_factory
from app.utils.logger import setup_logging
from app.utils.paths import get_data_dir, get_frontend_dir

logger = logging.getLogger(__name__)
_shutdown_event = threading.Event()
_key_extractor_thread: threading.Thread | None = None
_key_extractor_done = threading.Event()
_app_loop: asyncio.AbstractEventLoop | None = None
_pipeline_start_lock: asyncio.Lock | None = None
_pipeline = None


def _try_auto_extract_keys():
    """启动时尝试自动提取微信数据库密钥。"""
    try:
        from app.core.platform import Platform
        platform = Platform.get()
        extractor = platform.key_extractor

        # 缓存存在时先验证，避免旧/错误密钥导致后续数据库打开失败。
        cache = get_data_dir() / "all_keys.json"
        if cache.exists():
            keys = extractor.load_keys() if hasattr(extractor, "load_keys") else {}
            valid_keys = (
                extractor.validate_cached_keys(keys)
                if hasattr(extractor, "validate_cached_keys")
                else keys
            )
            has_message_key = (
                extractor._has_message_key(valid_keys)
                if hasattr(extractor, "_has_message_key")
                else bool(valid_keys)
            )
            if valid_keys and has_message_key:
                logger.info("密钥已缓存且验证通过，跳过提取")
                return
            logger.warning("缓存密钥无法解密当前数据库，删除后重新提取")
            if hasattr(extractor, "clear_keys"):
                extractor.clear_keys()
            else:
                try:
                    cache.unlink()
                except OSError as exc:
                    logger.warning(f"删除无效密钥缓存失败: {exc}")

        pids = (
            extractor.find_wechat_processes()
            if hasattr(extractor, "find_wechat_processes")
            else [pid] if (pid := extractor.find_wechat_process()) else []
        )
        if not pids:
            logger.warning("未找到微信进程，跳过密钥提取")
            return

        keys = {}
        for pid in pids:
            if _shutdown_event.is_set():
                break
            logger.info(f"正在从微信进程 {pid} 提取密钥...")
            keys = extractor.scan_memory_for_keys(pid, stop_event=_shutdown_event)
            if keys:
                break
        if _shutdown_event.is_set():
            logger.info("密钥提取已取消")
            return
        if keys:
            logger.info(f"密钥提取成功: {list(keys.keys())}")
            _schedule_pipeline_start_after_keys_ready()
        else:
            if hasattr(extractor, "clear_keys"):
                extractor.clear_keys()
            logger.warning(
                "密钥提取失败（请确认微信已登录，或设置 WEIX_WECHAT_DB_KEY 手动提供 64 位十六进制数据库密钥）"
            )
    except Exception as e:
        logger.warning(f"密钥提取异常: {e}")
    finally:
        _key_extractor_done.set()


def _schedule_pipeline_start_after_keys_ready() -> None:
    """Schedule auto-reply startup from the extractor thread once keys exist."""
    if _shutdown_event.is_set():
        return
    loop = _app_loop
    if loop is None or loop.is_closed():
        return

    future = asyncio.run_coroutine_threadsafe(
        _ensure_auto_reply_pipeline_started("数据库密钥提取完成"),
        loop,
    )

    def _log_result(done_future):
        try:
            done_future.result()
        except Exception as exc:
            logger.error(f"密钥提取后启动自动回复流水线失败: {exc}")

    future.add_done_callback(_log_result)


def _start_auto_extract_keys_background():
    """后台尝试自动提取微信数据库密钥，不阻塞 HTTP 服务启动/停止。"""
    global _key_extractor_thread

    _key_extractor_done.clear()
    t = threading.Thread(
        target=_try_auto_extract_keys,
        daemon=True,
        name="wechat-key-extractor",
    )
    _key_extractor_thread = t
    t.start()


async def _start_auto_reply_pipeline(wait_for_keys: bool = True):
    """启动自动回复流水线。"""
    if wait_for_keys:
        await _wait_for_keys_if_extracting(timeout_s=25.0)
    return await _ensure_auto_reply_pipeline_started("服务启动")


async def _ensure_auto_reply_pipeline_started(reason: str):
    """Start the pipeline once, even if keys arrive after HTTP startup."""
    global _pipeline, _pipeline_start_lock

    if _shutdown_event.is_set():
        return None
    if _pipeline is not None:
        return _pipeline
    if _pipeline_start_lock is None:
        _pipeline_start_lock = asyncio.Lock()

    async with _pipeline_start_lock:
        if _shutdown_event.is_set():
            return None
        if _pipeline is not None:
            return _pipeline
        pipeline = await _create_auto_reply_pipeline(reason)
        if pipeline is not None:
            _pipeline = pipeline
        return _pipeline


async def _create_auto_reply_pipeline(reason: str):
    """Create and start a new auto-reply pipeline instance."""
    try:
        from app.core.auto_reply_pipeline import AutoReplyPipeline

        logger.info(f"尝试启动自动回复流水线: {reason}")
        pipeline = AutoReplyPipeline(session_factory=get_session_factory())
        started = await pipeline.start()
        if started:
            logger.info("自动回复流水线已启动")
            return pipeline
        logger.warning("自动回复流水线未启动")
        return None
    except Exception as e:
        logger.error(f"启动自动回复流水线失败: {e}")
        return None


async def _wait_for_keys_if_extracting(timeout_s: float) -> None:
    """Give startup key extraction a short window before starting the pipeline."""
    if not _key_extractor_thread or not _key_extractor_thread.is_alive():
        return

    logger.info(f"等待数据库密钥提取完成，最多 {timeout_s:.0f}s...")
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not _key_extractor_done.is_set():
        if _shutdown_event.is_set():
            return
        if asyncio.get_running_loop().time() >= deadline:
            logger.warning("等待数据库密钥提取超时；后台提取成功后会自动启动自动回复流水线")
            return
        await asyncio.sleep(0.5)


def _prepare_embeddings_and_knowledge():
    """后台准备 embedding 模型和知识库，不阻塞 HTTP 服务启动。"""
    import threading

    def _load():
        try:
            from app.ai.embeddings import get_embedding_manager, get_local_embedding_cache_status
            from app.ai.knowledge_seed import seed_knowledge_base
            from app.ai.vector_store import get_vector_store

            logger.info(
                "本地 Embedding 模型状态: %s",
                get_local_embedding_cache_status(),
            )

            em = get_embedding_manager(provider="local")
            _ = em.embed_query("warmup")
            logger.info("Embedding 模型预加载完成")

            vs = get_vector_store()
            count = asyncio.run(seed_knowledge_base(vs, em))
            if count > 0:
                logger.info(f"知识库种子数据初始化完成: {count} 条")
        except Exception as e:
            logger.warning(f"Embedding/知识库后台准备失败（非致命）: {e}")

    t = threading.Thread(target=_load, daemon=True, name="embedding-preloader")
    t.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _app_loop, _pipeline_start_lock, _pipeline

    _shutdown_event.clear()
    _app_loop = asyncio.get_running_loop()
    _pipeline_start_lock = asyncio.Lock()
    _pipeline = None
    setup_logging()
    get_config()
    await init_database()
    _start_auto_extract_keys_background()

    # 后台下载/预加载 embedding 模型，并在模型就绪后初始化知识库
    _prepare_embeddings_and_knowledge()

    # 启动后台临时文件清理任务
    from app.core.temp_cleanup import start_temp_cleanup_task
    cleanup_task = start_temp_cleanup_task()

    # 启动自动回复流水线
    await _start_auto_reply_pipeline()

    yield

    _shutdown_event.set()
    if _key_extractor_thread and _key_extractor_thread.is_alive():
        _key_extractor_thread.join(timeout=2.0)

    # 停止流水线
    pipeline = _pipeline
    _pipeline = None
    if pipeline:
        await pipeline.stop()
    _pipeline_start_lock = None
    _app_loop = None

    # 停止清理任务
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


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


# --- 前端静态文件服务 (必须放在所有路由之后) ---
from fastapi.staticfiles import StaticFiles

_frontend_dir = get_frontend_dir()
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


# --- 独立运行入口 ---
if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
