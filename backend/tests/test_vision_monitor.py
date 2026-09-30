from unittest.mock import AsyncMock, Mock
import asyncio
import time

import pytest

from app.core.vision_client import VisualBubble
from app.core.vision_monitor import VisionMessageMonitor


def make_monitor():
    sender = Mock(inspect_chat=AsyncMock(return_value=b"PNG"))
    vision = Mock(read_visible_messages=Mock())
    return VisionMessageMonitor(sender, vision, ["小号"], 0.01), sender, vision


@pytest.mark.asyncio
async def test_start_waits_for_verified_baseline_before_reporting_ready():
    monitor, sender, vision = make_monitor()
    sender.inspect_chat.side_effect = [None, b"PNG"]
    vision.read_visible_messages.return_value = [VisualBubble("other", "已有消息")]
    await monitor.start()
    try:
        assert sender.inspect_chat.await_count == 2
        assert monitor._baseline["小号"] == (VisualBubble("other", "已有消息"),)
    finally:
        await monitor.stop()


@pytest.mark.asyncio
async def test_start_rejects_unavailable_baseline():
    monitor, sender, _vision = make_monitor()
    sender.inspect_chat.return_value = None
    with pytest.raises(RuntimeError, match="baseline"):
        await monitor.start()
    assert not monitor.is_running


@pytest.mark.asyncio
async def test_baseline_then_stable_new_left_bubble_only_once():
    monitor, _sender, vision = make_monitor()
    old = [VisualBubble("other", "旧消息")]
    new = old + [VisualBubble("other", "新消息")]
    for bubbles in (old, old, new, new, new):
        vision.read_visible_messages.return_value = bubbles
        await monitor._scan_once()
    assert monitor.queue_size == 1
    msg = await monitor.get_message()
    assert (msg.sender, msg.content, msg.is_group, msg.msg_type) == ("小号", "新消息", False, 1)


@pytest.mark.asyncio
async def test_new_repeated_incoming_text_is_queued_once_while_outgoing_is_ignored():
    monitor, _sender, vision = make_monitor()
    old = [VisualBubble("other", "重复")]
    for bubbles in (old, old, old + [VisualBubble("self", "机器人回复")],
                    old + [VisualBubble("self", "机器人回复")],
                    old + [VisualBubble("self", "机器人回复"), VisualBubble("other", "重复")],
                    old + [VisualBubble("self", "机器人回复"), VisualBubble("other", "重复")]):
        vision.read_visible_messages.return_value = bubbles
        await monitor._scan_once()
    assert monitor.queue_size == 1
    assert (await monitor.get_message()).content == "重复"


@pytest.mark.asyncio
async def test_no_overlap_or_unstable_parse_does_not_enqueue():
    monitor, _sender, vision = make_monitor()
    old = [VisualBubble("other", "旧消息")]
    for bubbles in (old, old, [VisualBubble("other", "不确定1")],
                    [VisualBubble("other", "不确定2")],
                    [VisualBubble("other", "换页")], [VisualBubble("other", "换页")]):
        vision.read_visible_messages.return_value = bubbles
        await monitor._scan_once()
    assert monitor.queue_size == 0


@pytest.mark.asyncio
async def test_capture_and_model_errors_skip_without_advancing_baseline():
    monitor, sender, vision = make_monitor()
    vision.read_visible_messages.return_value = [VisualBubble("other", "旧消息")]
    await monitor._scan_once()
    await monitor._scan_once()
    sender.inspect_chat.return_value = None
    await monitor._scan_once()
    sender.inspect_chat.return_value = b"PNG"
    vision.read_visible_messages.return_value = None
    await monitor._scan_once()
    assert monitor.queue_size == 0


@pytest.mark.asyncio
async def test_sent_message_echo_is_suppressed():
    monitor, _sender, vision = make_monitor()
    vision.read_visible_messages.return_value = []
    await monitor._scan_once()
    monitor.remember_sent_message("小号", "我发的")
    bubbles = [VisualBubble("other", "我发的")]
    vision.read_visible_messages.return_value = bubbles
    await monitor._scan_once()
    await monitor._scan_once()
    assert monitor.queue_size == 0


@pytest.mark.asyncio
async def test_slow_visual_read_does_not_block_event_loop():
    monitor, _sender, vision = make_monitor()
    def slow_read(_image):
        time.sleep(0.15)
        return []
    vision.read_visible_messages.side_effect = slow_read
    scan = asyncio.create_task(monitor._scan_once())
    await asyncio.sleep(0.01)
    assert not scan.done()
    await scan
