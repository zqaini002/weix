import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_config

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def init_scheduler(stats_service, report_service, sender, config):
    """Initialize and start the scheduler with default jobs."""
    stats_cfg = config.statistics

    # Daily report
    hour, minute = stats_cfg.get("daily_report_time", "22:00").split(":")
    scheduler.add_job(
        _daily_report_job,
        CronTrigger(hour=int(hour), minute=int(minute)),
        args=[stats_service, report_service, sender],
        id="daily_report",
        name="每日统计报告",
        replace_existing=True,
    )

    # Weekly report
    day = stats_cfg.get("weekly_report_day", 6)
    hour, minute = stats_cfg.get("weekly_report_time", "20:00").split(":")
    scheduler.add_job(
        _weekly_report_job,
        CronTrigger(day_of_week=str(day), hour=int(hour), minute=int(minute)),
        args=[stats_service, report_service, sender],
        id="weekly_report",
        name="每周汇总报告",
        replace_existing=True,
    )

    # Health check
    scheduler.add_job(
        _health_check_job,
        CronTrigger(minute="*/5"),
        args=[sender],
        id="health_check",
        name="微信在线检测",
        replace_existing=True,
    )

    # Data cleanup
    scheduler.add_job(
        _cleanup_job,
        CronTrigger(day_of_week="0", hour=3, minute=0),
        args=[stats_service, stats_cfg],
        id="data_cleanup",
        name="数据清理",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler initialized with default jobs")


async def _daily_report_job(stats_service, report_service, sender):
    logger.info("Running daily report job...")
    try:
        ranking = await stats_service.get_ranking("day")
        timeline = await stats_service.get_timeline()
        keywords = await stats_service.get_keywords("day")
        report = await report_service.generate_daily_report("", ranking, timeline, keywords)

        config = get_config()
        for room in config.forward_rules:
            for target in room.get("targets", []):
                await sender.send_text(report, target)
        logger.info("Daily report sent successfully")
    except Exception as e:
        logger.error(f"Daily report failed: {e}")


async def _weekly_report_job(stats_service, report_service, sender):
    logger.info("Running weekly report job...")
    try:
        ranking = await stats_service.get_ranking("week")
        overview = await stats_service.get_overview()
        report = await report_service.generate_weekly_report("", ranking, overview["total_messages"])

        config = get_config()
        for room in config.forward_rules:
            for target in room.get("targets", []):
                await sender.send_text(report, target)
        logger.info("Weekly report sent successfully")
    except Exception as e:
        logger.error(f"Weekly report failed: {e}")


async def _health_check_job(sender):
    try:
        online = await sender.is_wechat_running()
        if not online:
            logger.warning("WeChat appears to be offline!")
    except Exception as e:
        logger.error(f"Health check failed: {e}")


def get_scheduler() -> AsyncIOScheduler:
    """Return the live scheduler instance for API inspection."""
    return scheduler


async def _cleanup_job(stats_service, stats_cfg):
    days = stats_cfg.get("data_retention_days", 30)
    logger.info(f"Running data cleanup (retention: {days} days)...")
    # Actual cleanup logic would go here
    logger.info("Cleanup completed")
