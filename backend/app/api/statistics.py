from fastapi import APIRouter, Depends, Query

from app.api.auth import verify_token
from app.deps import get_statistics_service, get_report_service
from app.models.schemas import StatisticsOverview

router = APIRouter(prefix="/api/statistics", tags=["statistics"], dependencies=[Depends(verify_token)])


@router.get("/ranking")
async def get_ranking(
    period: str = Query("day", pattern="^(day|week|month)$"),
    room_id: str = Query(""),
    limit: int = Query(20),
    service=Depends(get_statistics_service),
):
    ranking = await service.get_ranking(period, room_id, limit)
    return {"period": period, "ranking": ranking}


@router.get("/timeline")
async def get_timeline(
    date: str = Query(""),
    room_id: str = Query(""),
    service=Depends(get_statistics_service),
):
    timeline = await service.get_timeline(date, room_id)
    return {"date": date, "timeline": timeline}


@router.get("/keywords")
async def get_keywords(
    period: str = Query("week", pattern="^(day|week|month)$"),
    room_id: str = Query(""),
    limit: int = Query(30),
    service=Depends(get_statistics_service),
):
    keywords = await service.get_keywords(period, room_id, limit)
    return {"period": period, "keywords": keywords}


@router.post("/summary/generate")
async def generate_summary(
    room_id: str = Query(""),
    service=Depends(get_statistics_service),
    report_service=Depends(get_report_service),
):
    ranking = await service.get_ranking("day", room_id, 10)
    timeline = await service.get_timeline("", room_id)
    keywords = await service.get_keywords("day", room_id, 20)
    report = await report_service.generate_daily_report(room_id, ranking, timeline, keywords)
    return {"summary": report}


@router.get("/overview", response_model=StatisticsOverview)
async def get_overview(service=Depends(get_statistics_service)):
    overview = await service.get_overview()
    ranking = await service.get_ranking("day", "", 5)
    timeline = await service.get_timeline()
    keywords = await service.get_keywords("week", "", 10)
    return StatisticsOverview(
        total_messages=overview["total_messages"],
        active_users=overview["active_users"],
        active_rooms=overview["active_rooms"],
        ranking=ranking,
        timeline=timeline,
        keywords=keywords,
    )
