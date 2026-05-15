import logging
from collections import Counter
from datetime import datetime, timedelta

try:
    import jieba
    _has_jieba = True
except ImportError:
    jieba = None
    _has_jieba = False
from sqlalchemy import select, func, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Message, ChatStatistic

logger = logging.getLogger(__name__)


class StatisticsService:
    """Chat statistics analysis."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_ranking(
        self, period: str = "day", room_id: str = "", limit: int = 20
    ) -> list[dict]:
        """Get user message count ranking."""
        now = datetime.now()
        if period == "day":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            start = now - timedelta(days=7)
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        query = (
            select(
                Message.sender_wxid,
                Message.sender_name,
                func.count(Message.id).label("cnt"),
            )
            .where(Message.create_time >= start)
            .group_by(Message.sender_wxid)
            .order_by(func.count(Message.id).desc())
            .limit(limit)
        )
        if room_id:
            query = query.where(Message.room_id == room_id)

        result = await self.session.execute(query)
        return [
            {"user_wxid": r[0], "user_name": r[1] or r[0], "message_count": r[2]}
            for r in result
        ]

    async def get_timeline(self, date: str = "", room_id: str = "") -> list[dict]:
        """Get 24-hour message distribution."""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        query = (
            select(extract("hour", Message.create_time).label("hour"), func.count(Message.id))
            .where(func.date(Message.create_time) == date)
            .group_by("hour")
            .order_by("hour")
        )
        if room_id:
            query = query.where(Message.room_id == room_id)

        result = await self.session.execute(query)
        hour_map = {int(r[0]): r[1] for r in result}
        return [{"hour": h, "count": hour_map.get(h, 0)} for h in range(24)]

    async def get_keywords(self, period: str = "week", room_id: str = "", limit: int = 30) -> list[dict]:
        """Extract high-frequency keywords using jieba + TF-IDF."""
        now = datetime.now()
        if period == "week":
            start = now - timedelta(days=7)
        else:
            start = now - timedelta(days=30)

        query = select(Message.content).where(
            Message.create_time >= start,
            Message.msg_type == 1,
        )
        if room_id:
            query = query.where(Message.room_id == room_id)

        result = await self.session.execute(query)
        contents = [r[0] for r in result if r[0]]

        if not contents:
            return []

        # jieba word segmentation + frequency counting
        stop_words = {"的", "了", "是", "我", "你", "他", "她", "它", "们", "这", "那", "在", "不", "和", "就", "都", "也", "要", "会", "有", "很", "还", "吗", "吧", "啊", "呢", "哦"}
        all_words = []

        if _has_jieba:
            for text in contents:
                words = jieba.cut(text)
                all_words.extend(w for w in words if len(w) >= 2 and w not in stop_words)
        else:
            for text in contents:
                import re
                words = re.findall(r'[一-鿿]{2,}', text)
                all_words.extend(w for w in words if w not in stop_words)

        counter = Counter(all_words)
        total = sum(counter.values()) or 1
        return [
            {"word": word, "count": count, "score": round(count / total, 4)}
            for word, count in counter.most_common(limit)
        ]

    async def compute_daily_stats(self, room_id: str = ""):
        """Compute and store daily chat statistics."""
        today = datetime.now().strftime("%Y-%m-%d")
        ranking = await self.get_ranking("day", room_id)

        for item in ranking:
            stat = ChatStatistic(
                room_id=room_id,
                user_wxid=item["user_wxid"],
                user_name=item["user_name"],
                message_count=item["message_count"],
                stat_date=today,
                stat_type="daily",
            )
            self.session.add(stat)
        await self.session.commit()
        logger.info(f"Daily stats computed for {room_id or 'all rooms'}: {len(ranking)} users")

    async def get_overview(self) -> dict:
        """Get overall statistics overview."""
        today = datetime.now().strftime("%Y-%m-%d")
        total = await self._count_messages()
        active_users = await self._count_distinct("sender_wxid", today)
        active_rooms = await self._count_distinct("room_id", today, exclude_empty=True)

        return {
            "total_messages": total,
            "active_users": active_users,
            "active_rooms": active_rooms,
        }

    async def _count_messages(self, date: str = "") -> int:
        q = select(func.count(Message.id))
        if date:
            q = q.where(func.date(Message.create_time) == date)
        result = await self.session.execute(q)
        return result.scalar() or 0

    async def _count_distinct(self, column: str, date: str = "", exclude_empty: bool = False) -> int:
        col = getattr(Message, column)
        q = select(func.count(func.distinct(col)))
        if date:
            q = q.where(func.date(Message.create_time) == date)
        if exclude_empty:
            q = q.where(col != "")
        result = await self.session.execute(q)
        return result.scalar() or 0
