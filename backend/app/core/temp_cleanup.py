"""后台临时文件清理任务。"""

import asyncio
import logging

from app.core.platform import Platform

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_STALE_SECONDS = 600


async def temp_cleanup_loop(
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> None:
    """定期清理旧的微信解密临时文件。"""
    db_reader = Platform.get().db_reader
    while True:
        try:
            removed = db_reader.cleanup_temp_files(stale_seconds=stale_seconds)
            if removed:
                logger.info("已清理微信临时解密文件: %s 个", removed)
        except Exception as exc:
            logger.warning("微信临时文件清理失败: %s", exc)
        await asyncio.sleep(interval_seconds)


def start_temp_cleanup_task(
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> asyncio.Task:
    """启动后端生命周期内的临时文件定时清理任务。"""
    return asyncio.create_task(
        temp_cleanup_loop(
            interval_seconds=interval_seconds,
            stale_seconds=stale_seconds,
        ),
        name="weix-temp-cleanup",
    )
