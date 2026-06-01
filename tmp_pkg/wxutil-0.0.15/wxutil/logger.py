import os
import sys

from loguru import logger

logger = logger.bind(name="wxutil")
logger.add(
    sink=sys.stdout,
    format=os.environ.get(
        "WXUTIL_LOG_FORMAT",
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <level>{message}</level>",
    ),
    filter=lambda record: record["extra"].get("name") == "wxutil",
    level=os.environ.get("WXUTIL_LOG_LEVEL", "DEBUG"),
)
