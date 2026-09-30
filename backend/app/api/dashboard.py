from fastapi import APIRouter, Depends

from app.ai.counter import get_count as get_ai_call_count
from app.api.auth import verify_token
from app.config import get_config
from app.core.platform import Platform
from app.deps import get_message_service, get_session
from app.models.database import Order
from app.models.schemas import DashboardOverview
from sqlalchemy import select, func

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(verify_token)])


@router.get("/overview", response_model=DashboardOverview)
async def get_overview(service=Depends(get_message_service), session=Depends(get_session)):
    platform = Platform.get()
    wechat_online = await platform.sender.is_wechat_running()

    today_messages = await service.get_today_message_count()
    active_rooms = await service.get_active_rooms()

    # Pending orders
    result = await session.execute(
        select(func.count(Order.id)).where(Order.status.in_(["pending", "confirmed"]))
    )
    pending_orders = result.scalar() or 0

    return DashboardOverview(
        platform=platform.name,
        wechat_online=wechat_online,
        today_messages=today_messages,
        active_rooms=active_rooms,
        ai_calls=get_ai_call_count(),
        pending_orders=pending_orders,
    )
