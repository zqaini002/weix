"""Poll verified private chats visually and enqueue only unambiguous new text."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from app.core.base import WeChatMessage
from app.core.vision_client import VisualBubble

logger = logging.getLogger(__name__)


class VisionMessageMonitor:
    def __init__(self, sender, vision_client, chat_names: list[str], poll_interval: float):
        self._sender = sender
        self._vision = vision_client
        self._chat_names = list(dict.fromkeys(chat_names))
        self._poll_interval = max(0.1, float(poll_interval))
        self._queue: asyncio.Queue[WeChatMessage] = asyncio.Queue(maxsize=100)
        self._baseline: dict[str, tuple[VisualBubble, ...]] = {}
        self._pending: dict[str, tuple[VisualBubble, ...]] = {}
        self._sent_text: dict[str, set[str]] = defaultdict(set)
        self._sequence: dict[str, int] = defaultdict(int)
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    async def start(self, lookback_seconds: float = 0) -> None:
        if self._running:
            return
        self._running = True
        # Never report readiness until every requested chat has a verified baseline.
        try:
            for attempt in range(3):
                await self._scan_once()
                if all(name in self._baseline for name in self._chat_names):
                    break
                if attempt < 2:
                    await asyncio.sleep(self._poll_interval)
            else:
                raise RuntimeError("vision baseline unavailable for one or more chats")
        except BaseException:
            self._running = False
            raise
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def get_message(self) -> WeChatMessage:
        return await self._queue.get()

    def remember_sent_message(self, receiver: str, content: str) -> None:
        if receiver in self._chat_names and content.strip():
            self._sent_text[receiver].add(content.strip())

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._scan_once()
            except Exception:
                logger.warning("视觉消息轮询失败")
            await asyncio.sleep(self._poll_interval)

    async def _scan_once(self) -> None:
        for name in self._chat_names:
            try:
                image = await self._sender.inspect_chat(name, self._chat_names)
                if not image:
                    self._pending.pop(name, None)
                    continue
                parsed = await asyncio.to_thread(self._vision.read_visible_messages, image)
                if parsed is None:
                    self._pending.pop(name, None)
                    continue
                visible = tuple(parsed)
                if name not in self._baseline:
                    self._baseline[name] = visible
                    continue
                if visible == self._baseline[name]:
                    self._pending.pop(name, None)
                    continue
                if self._pending.get(name) != visible:
                    self._pending[name] = visible
                    continue
                self._pending.pop(name, None)
                previous = self._baseline[name]
                self._baseline[name] = visible
                overlap = self._overlap(previous, visible)
                if previous and overlap == 0:
                    logger.warning("视觉消息缺少可验证的历史重叠，跳过 | receiver=%s", name)
                    continue
                for bubble in visible[overlap:]:
                    if bubble.side != "other":
                        continue
                    content = bubble.content.strip()
                    if content in self._sent_text[name]:
                        continue
                    self._sequence[name] += 1
                    message = WeChatMessage(
                        msg_id=f"vision:{name}:{self._sequence[name]}",
                        msg_type=1,
                        content=content,
                        sender=name,
                    )
                    try:
                        self._queue.put_nowait(message)
                    except asyncio.QueueFull:
                        logger.warning("视觉消息队列已满 | receiver=%s", name)
            except Exception:
                self._pending.pop(name, None)
                logger.warning("视觉消息扫描失败 | receiver=%s", name)

    @staticmethod
    def _overlap(previous: tuple[VisualBubble, ...], current: tuple[VisualBubble, ...]) -> int:
        for count in range(min(len(previous), len(current)), 0, -1):
            if previous[-count:] == current[:count]:
                return count
        return 0
