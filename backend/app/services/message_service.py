import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Message

logger = logging.getLogger(__name__)


class MessageService:
    """Core message processing orchestrator."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_message(self, msg: dict) -> Message:
        """Save an incoming message to the database."""
        result = await self.session.execute(
            select(Message).where(Message.msg_id == msg["msg_id"])
        )
        record = result.scalar_one_or_none()
        if record is not None:
            return record

        record = Message(
            msg_id=msg["msg_id"],
            msg_type=msg.get("msg_type", 1),
            content=msg.get("content", ""),
            sender_wxid=msg.get("sender", ""),
            sender_name=msg.get("sender_name", ""),
            room_id=msg.get("room_id", ""),
            room_name=msg.get("room_name", ""),
            is_group=msg.get("is_group", False),
            create_time=msg.get("create_time", datetime.now()),
        )
        self.session.add(record)
        await self.session.commit()
        return record

    async def get_messages(
        self, room_id: str = "", user_id: str = "", start_date: str = "", end_date: str = "",
        page: int = 1, size: int = 20,
    ) -> tuple[list[Message], int]:
        """Query messages with pagination and optional date range."""
        query = select(Message)
        count_q = select(func.count(Message.id))

        if room_id:
            query = query.where(Message.room_id == room_id)
            count_q = count_q.where(Message.room_id == room_id)
        if user_id:
            query = query.where(Message.sender_wxid == user_id)
            count_q = count_q.where(Message.sender_wxid == user_id)
        if start_date:
            query = query.where(func.date(Message.create_time) >= start_date)
            count_q = count_q.where(func.date(Message.create_time) >= start_date)
        if end_date:
            query = query.where(func.date(Message.create_time) <= end_date)
            count_q = count_q.where(func.date(Message.create_time) <= end_date)

        query = query.order_by(Message.create_time.desc()).offset((page - 1) * size).limit(size)

        total = (await self.session.execute(count_q)).scalar() or 0
        result = await self.session.execute(query)
        items = result.scalars().all()
        return list(items), total

    async def get_today_message_count(self) -> int:
        """Get today's total message count."""
        today = datetime.now().strftime("%Y-%m-%d")
        result = await self.session.execute(
            select(func.count(Message.id)).where(
                func.date(Message.create_time) == today
            )
        )
        return result.scalar() or 0

    async def get_active_rooms(self) -> int:
        """Count active rooms today."""
        today = datetime.now().strftime("%Y-%m-%d")
        result = await self.session.execute(
            select(func.count(func.distinct(Message.room_id))).where(
                Message.room_id != "",
                func.date(Message.create_time) == today,
            )
        )
        return result.scalar() or 0
