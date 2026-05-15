import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ReportService:
    """Generate formatted reports from statistics data."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def generate_daily_report(
        self, room_id: str, ranking: list[dict], timeline: list[dict], keywords: list[dict]
    ) -> str:
        """Generate a daily chat report in markdown format."""
        today = datetime.now().strftime("%Y年%m月%d日")
        lines = [
            f"📊 **{today} 聊天统计报告**",
            "",
            "━━━━━━━━━━━━━━━━",
            "",
            "🏆 **发言排行 TOP 5**",
        ]

        for i, item in enumerate(ranking[:5], 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1]
            lines.append(f"  {medal} {item['user_name']}: {item['message_count']} 条")

        # Peak hours
        peak_hours = sorted(timeline, key=lambda x: x["count"], reverse=True)[:3]
        lines.extend([
            "",
            "⏰ **活跃时段**",
            f"  最活跃: {peak_hours[0]['hour']}:00 前后 ({peak_hours[0]['count']} 条消息)" if peak_hours else "  暂无数据",
        ])

        # Keywords
        if keywords:
            top_words = "、".join(k["word"] for k in keywords[:8])
            lines.extend([
                "",
                "🔑 **热门关键词**",
                f"  {top_words}",
            ])

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━",
            f"📌 数据截止: {datetime.now().strftime('%H:%M')}",
        ])

        return "\n".join(lines)

    async def generate_weekly_report(
        self, room_id: str, ranking: list[dict], total_messages: int
    ) -> str:
        """Generate a weekly summary report."""
        week_start = datetime.now().strftime("%m月%d日")
        lines = [
            f"📈 **本周聊天周报** ({week_start} - {datetime.now().strftime('%m月%d日')})",
            "",
            f"💬 本周总消息数: {total_messages}",
            "",
            "🏆 **本周活跃 TOP 5**",
        ]

        for i, item in enumerate(ranking[:5], 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1]
            lines.append(f"  {medal} {item['user_name']}: {item['message_count']} 条")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━",
            "感谢大家本周的活跃参与! 🎉",
        ])

        return "\n".join(lines)

    async def format_order_notify(self, order: dict) -> str:
        """Format an order notification for forwarding."""
        return (
            f"🎯 **新陪玩订单** #{order.get('order_id', '')}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"游戏：{order.get('game', '')} | 段位：{order.get('rank', '')}\n"
            f"时长：{order.get('hours', 0)}h | 预算：¥{order.get('budget', 0)}/h\n"
            f"下单人：{order.get('user_name', '')}\n"
            f"备注：{order.get('notes', '无')}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"回复「接单 {order.get('order_id', '')}」抢单"
        )
