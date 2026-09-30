"""Incoming visual observation through the existing reply flow and guarded GUI send."""

from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.auto_reply_pipeline import AutoReplyPipeline
from app.core.sender_windows import WindowsSender
from app.core.vision_client import SearchHit, VisualBubble
from app.core.vision_monitor import VisionMessageMonitor


class Window:
    left, top, width, height = 0, 0, 1000, 700
    def activate(self):
        pass


class Vision:
    def __init__(self):
        self.bubbles = []
        self.title = "小号"
    def locate_search_result(self, image, target, size):
        return SearchHit(80, 100, target)
    def classify_chat_title(self, image, candidates):
        return self.title
    def read_visible_messages(self, image):
        return self.bubbles


class Rule:
    async def match(self, content):
        return {"matched": True, "reply": "已收到"}


@pytest.fixture
def setup(monkeypatch):
    import app.core.sender_windows as module
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={
            "enabled": True,
            "reply_mode": "keyword",
            "private_chat_mode": "whitelist",
            "private_whitelist": ["小号"],
        }, windows_sender={}),
    )
    vision = Vision()
    sender = WindowsSender(vision_client=vision)
    sender._find_wechat_window = lambda: Window()
    sender._activate_wechat = lambda: None
    sender._focus_search_input = lambda: (150, 50)
    sender._clear_search_input = lambda: None
    sender._focus_message_input = lambda: (600, 600)
    pasted = []
    sender._paste_text = lambda text, x, y: pasted.append(text)
    sent = []
    def click_send():
        sent.append(True)
        vision.bubbles.append(VisualBubble("self", pasted[-1]))
    sender._click_send_button = click_send
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module.pyautogui, "click", lambda *args: None)
    monkeypatch.setattr(module.pyautogui, "screenshot", lambda region: Image.new("RGB", (region[2], region[3])))
    monitor = VisionMessageMonitor(sender, vision, ["小号"], 1)
    pipeline = AutoReplyPipeline()
    pipeline._sender = sender
    pipeline._monitor = monitor
    pipeline._rule_engine = Rule()
    pipeline._name_map = {"小号": "小号"}
    pipeline._park_after_send = False
    pipeline._debounce_seconds = 0
    return pipeline, monitor, vision, pasted, sent


@pytest.mark.asyncio
async def test_incoming_vision_message_reaches_rule_and_guarded_send(setup):
    pipeline, monitor, vision, pasted, sent = setup
    vision.bubbles = [VisualBubble("other", "旧消息")]
    await monitor._scan_once()
    vision.bubbles = [VisualBubble("other", "旧消息"), VisualBubble("other", "请回复")]
    await monitor._scan_once()
    await monitor._scan_once()
    msg = await monitor.get_message()
    pipeline._buffer["小号"] = [msg]
    await pipeline._flush_buffer("小号")
    assert "已收到" in pasted
    assert sent == [True]


@pytest.mark.asyncio
async def test_wrong_open_chat_never_pastes_reply(setup):
    pipeline, monitor, vision, pasted, sent = setup
    vision.bubbles = []
    await monitor._scan_once()
    vision.bubbles = [VisualBubble("other", "新消息")]
    await monitor._scan_once()
    await monitor._scan_once()
    msg = await monitor.get_message()
    pipeline._buffer["小号"] = [msg]
    vision.title = "向崟吉"
    await pipeline._flush_buffer("小号")
    assert "已收到" not in pasted
    assert sent == []
