"""微信消息轮询监听器。

通过异步轮询数据库获取新消息，经过去重和解析后
推送到 asyncio.Queue 供消息处理流水线消费。

不继承任何基类，作为独立的监听组件使用。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.config import get_config
from app.core.base import WeChatMessage

logger = logging.getLogger(__name__)

# 消息类型常量
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3
MSG_TYPE_VOICE = 34
MSG_TYPE_VIDEO = 43
MSG_TYPE_CARD = 49
MSG_TYPE_SYSTEM = 10000

# 系统消息子类型 (msg_type=10000 时通过 content 中的 XML 判断)
SYSTEM_MSG_PATTERNS = {
    "revokemsg": "撤回消息",
    "sysmsg": "系统通知",
    "voipmsg": "音视频通话",
    "appmsg": "应用消息",
}


@dataclass
class MonitorConfig:
    """消息监听器配置。"""

    poll_interval: float = 2.0  # 轮询间隔 (秒)
    max_batch_size: int = 100  # 单次批量最大消息数
    dedup_window: int = 3600  # 去重窗口 (秒)
    retry_delay: float = 5.0  # 错误重试延迟 (秒)
    queue_maxsize: int = 10000  # 消息队列最大容量
    sent_message_ttl: float = 300.0  # 机器人已发送消息去重窗口 (秒)


class MessageMonitor:
    """微信消息异步轮询监听器。

    功能:
    - 从配置读取轮询间隔
    - 调用 db_reader 查询新消息
    - msg_id 去重
    - 解析消息类型 (群聊/私聊, 文本/图片等)
    - 通过 asyncio.Queue 推送消息

    使用方式:
        monitor = MessageMonitor(db_reader)
        await monitor.start()
        async for msg in monitor:
            await process(msg)
    """

    def __init__(
        self,
        db_reader,
        config: Optional[MonitorConfig] = None,
    ):
        """
        Args:
            db_reader: DB 读取器实例 (BaseDBReader 子类)。
            config: 监听器配置，为 None 时从全局配置读取。
        """
        self._db_reader = db_reader

        if config is None:
            app_config = get_config()
            monitor_cfg = (
                app_config.monitor if hasattr(app_config, "monitor") else {}
            )
            config = MonitorConfig(
                poll_interval=float(
                    monitor_cfg.get("poll_interval", 2.0)
                ),
            )

        self._config = config
        self._queue: asyncio.Queue[WeChatMessage] = asyncio.Queue(
            maxsize=config.queue_maxsize
        )
        self._seen_ids: dict[str, float] = {}  # msg_id -> add_time
        self._last_timestamp: int = 0
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._stats = MonitorStats()
        self._sent_messages: list[tuple[str, str, float]] = []

    # --- 公共接口 ---

    async def start(self, lookback_seconds: float = 60.0) -> None:
        """启动消息监听。

        在当前 event loop 中创建后台轮询任务。

        Args:
            lookback_seconds: 回看时间（秒）。初始时间戳往前推这么多秒，
                              以捕获启动前不久发送的消息。默认 60 秒。
        """
        if self._running:
            logger.warning("监听器已在运行")
            return

        self._running = True
        self._last_timestamp = int((time.time() - lookback_seconds) * 1000)
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"消息监听器已启动 (轮询间隔: {self._config.poll_interval}s, "
            f"回看: {lookback_seconds}s)"
        )

    async def stop(self) -> None:
        """停止消息监听。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("消息监听器已停止")
        self._log_stats()

    async def get_message(self) -> WeChatMessage:
        """从队列获取一条消息 (阻塞)。

        Returns:
            下一条待处理的 WeChatMessage。
        """
        return await self._queue.get()

    async def __aiter__(self):
        """异步迭代器，yield 消息队列中的每条消息。"""
        while self._running or not self._queue.empty():
            try:
                msg = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                yield msg
            except asyncio.TimeoutError:
                continue

    # --- 属性 ---

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def total_processed(self) -> int:
        return self._stats.total_processed

    def remember_sent_message(self, receiver: str, content: str) -> None:
        """记录机器人刚发出的消息，避免后续 DB 轮询回灌入队。"""
        normalized = self._normalize_content(content)
        if not receiver or not normalized:
            return
        self._sent_messages.append((str(receiver), normalized, time.monotonic()))
        self._cleanup_sent_messages()

    # --- 内部轮询逻辑 ---

    async def _poll_loop(self) -> None:
        """后台轮询循环。

        定期查询数据库中的新消息，去重后推送到队列。
        """
        while self._running:
            try:
                messages = await self._poll_once()
                for msg in messages:
                    if self._should_process(msg):
                        try:
                            self._queue.put_nowait(msg)
                            self._stats.total_processed += 1
                            logger.info(
                                f"消息入队 | sender={msg.sender} | "
                                f"is_group={msg.is_group} | "
                                f"content={msg.content[:60]}"
                            )
                        except asyncio.QueueFull:
                            logger.warning("消息队列已满，丢弃消息")
                            self._stats.dropped += 1

                self._cleanup_dedup()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"轮询异常: {exc}", exc_info=True)
                self._stats.errors += 1
                # 出错后等待重试延迟再继续
                await asyncio.sleep(self._config.retry_delay)
                continue

            await asyncio.sleep(self._config.poll_interval)

    async def _poll_once(self) -> list[WeChatMessage]:
        """执行一次轮询查询。

        Returns:
            新消息列表。
        """
        try:
            # 将查询操作在线程池中运行 (避免阻塞 event loop)
            loop = asyncio.get_running_loop()
            messages = await loop.run_in_executor(
                None,
                self._db_reader.query_messages_since,
                self._last_timestamp,
            )
            return messages
        except Exception as exc:
            logger.error(f"查询消息失败: {exc}")
            return []

    def _should_process(self, msg: WeChatMessage) -> bool:
        """判断是否应处理该消息。

        Args:
            msg: 微信消息。

        Returns:
            True 表示应该处理。
        """
        # 消息去重
        if msg.msg_id in self._seen_ids:
            return False

        # 记录已见
        self._seen_ids[msg.msg_id] = time.monotonic()

        # 更新最后时间戳
        if msg.create_time:
            msg_ts = int(msg.create_time.timestamp() * 1000)
            if msg_ts > self._last_timestamp:
                self._last_timestamp = msg_ts + 1  # +1 避免重复取最后一条

        # 跳过系统消息 (可配置)
        if msg.msg_type == MSG_TYPE_SYSTEM:
            self._stats.system_skipped += 1
            return False

        # 跳过空消息
        if not msg.content and msg.msg_type != MSG_TYPE_IMAGE:
            return False

        if self._is_recent_sent_message(msg):
            logger.debug(
                "跳过机器人已发送消息回灌 | receiver=%s | content=%s",
                msg.room_id if msg.is_group else msg.sender,
                msg.content[:50],
            )
            return False

        if not self._passes_chat_acl(msg):
            return False

        return True

    def _is_recent_sent_message(self, msg: WeChatMessage) -> bool:
        """判断消息是否是机器人刚发出去后被数据库轮询读回来的内容。"""
        receiver = msg.room_id if msg.is_group else msg.sender
        content = self._normalize_content(msg.content)
        if not receiver or not content:
            return False

        self._cleanup_sent_messages()
        for sent_receiver, sent_content, _sent_at in self._sent_messages:
            if sent_receiver == str(receiver) and sent_content == content:
                return True
        return False

    def _cleanup_sent_messages(self) -> None:
        """清理过期的机器人发送记录。"""
        now = time.monotonic()
        ttl = self._config.sent_message_ttl
        self._sent_messages = [
            item for item in self._sent_messages
            if now - item[2] <= ttl
        ]

    @staticmethod
    def _normalize_content(content: str) -> str:
        """统一消息文本用于去重匹配。"""
        return str(content or "").strip()

    @staticmethod
    def _passes_chat_acl(msg: WeChatMessage) -> bool:
        """入队前按聊天权限过滤，避免非目标消息污染自动回复队列。"""
        config = get_config().auto_reply
        if not config.get("enabled", True):
            return False

        if msg.is_group:
            mode = config.get("group_chat_mode", "whitelist")
            if mode == "none":
                return False
            if mode == "whitelist":
                whitelist = config.get("group_whitelist", [])
                room_id = msg.room_id or msg.sender
                return room_id in whitelist or str(room_id) in whitelist
            return True

        mode = config.get("private_chat_mode", "whitelist")
        if mode == "none":
            return False
        if mode == "whitelist":
            whitelist = config.get("private_whitelist", [])
            return msg.sender in whitelist or str(msg.sender) in whitelist
        return True

    def _cleanup_dedup(self) -> None:
        """清理过期的去重记录。"""
        now = time.monotonic()
        expired = [
            msg_id
            for msg_id, add_time in self._seen_ids.items()
            if now - add_time > self._config.dedup_window
        ]
        for msg_id in expired:
            del self._seen_ids[msg_id]

    def _log_stats(self) -> None:
        """输出监听统计信息。"""
        logger.info(
            f"消息监听统计: "
            f"total_processed={self._stats.total_processed}, "
            f"dropped={self._stats.dropped}, "
            f"system_skipped={self._stats.system_skipped}, "
            f"errors={self._stats.errors}"
        )

    # --- 消息解析辅助方法 ---

    @staticmethod
    def classify_message(msg: WeChatMessage) -> str:
        """分类消息类型。

        Returns:
            "text", "image", "voice", "video", "card", "system", "unknown"
        """
        type_map = {
            MSG_TYPE_TEXT: "text",
            MSG_TYPE_IMAGE: "image",
            MSG_TYPE_VOICE: "voice",
            MSG_TYPE_VIDEO: "video",
            MSG_TYPE_CARD: "card",
            MSG_TYPE_SYSTEM: "system",
        }
        return type_map.get(msg.msg_type, "unknown")

    @staticmethod
    def is_system_revoke(msg: WeChatMessage) -> bool:
        """判断是否为撤回消息系统通知。"""
        if msg.msg_type != MSG_TYPE_SYSTEM:
            return False
        return "<revokemsg>" in msg.content

    @staticmethod
    def parse_xml_content(content: str, tag: str) -> str:
        """从消息 XML 内容中提取指定标签的值 (简单解析)。

        Args:
            content: XML 字符串。
            tag: 标签名。

        Returns:
            标签内的文本内容。
        """
        import re
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, content, re.DOTALL)
        return match.group(1) if match else ""


@dataclass
class MonitorStats:
    """监听器统计信息。"""

    total_processed: int = 0
    dropped: int = 0
    system_skipped: int = 0
    errors: int = 0
    last_poll_time: float = 0.0
    start_time: float = field(default_factory=time.monotonic)

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def messages_per_minute(self) -> float:
        uptime = self.uptime_seconds
        if uptime <= 0:
            return 0.0
        return (self.total_processed / uptime) * 60
