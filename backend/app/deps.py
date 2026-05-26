import os
import sys
from pathlib import Path

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.config import get_config
from app.models.database import Base
from app.services.message_service import MessageService
from app.services.statistics_service import StatisticsService
from app.services.report_service import ReportService
from app.utils.paths import get_base_dir

_engine = None
_session_factory = None

# 项目根目录
_PROJECT_ROOT = get_base_dir()


def get_engine():
    global _engine
    if _engine is None:
        config = get_config()
        db_url = config.database.get("url", "sqlite+aiosqlite:///data/weix.db")

        # 如果是相对路径 SQLite URL，转换为绝对路径
        if "sqlite" in db_url and "///" in db_url:
            rel_path = db_url.split("///")[-1]
            abs_path = _PROJECT_ROOT / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            db_url = f"sqlite+aiosqlite:///{abs_path}"

        _engine = create_async_engine(db_url, echo=False)
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_message_service(session: AsyncSession = Depends(get_session)) -> MessageService:
    return MessageService(session)


async def get_statistics_service(session: AsyncSession = Depends(get_session)) -> StatisticsService:
    return StatisticsService(session)


async def get_report_service(session: AsyncSession = Depends(get_session)) -> ReportService:
    return ReportService(session)


async def init_database():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
