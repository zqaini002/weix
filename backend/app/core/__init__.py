"""微信机器人核心数据层模块。

导出:
- 基础数据类: WeChatMessage
- 平台自适应: Platform
- 消息监听: MessageMonitor
- 防检测: AntiDetect
"""

from app.core.base import WeChatMessage, BaseKeyExtractor, BaseDBReader, BaseMessageSender
from app.core.platform import Platform
from app.core.message_monitor import MessageMonitor, MonitorConfig, MonitorStats
from app.core.anti_detect import AntiDetect, AntiDetectConfig

__all__ = [
    "WeChatMessage",
    "BaseKeyExtractor",
    "BaseDBReader",
    "BaseMessageSender",
    "Platform",
    "MessageMonitor",
    "MonitorConfig",
    "MonitorStats",
    "AntiDetect",
    "AntiDetectConfig",
]
