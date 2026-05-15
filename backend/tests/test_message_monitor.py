import os
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.base import WeChatMessage
from app.core.message_monitor import MessageMonitor, MonitorConfig


class DummyDBReader:
    def query_messages_since(self, timestamp: int):
        return []


def _monitor() -> MessageMonitor:
    return MessageMonitor(DummyDBReader(), MonitorConfig())


def _msg(sender: str, *, room_id: str = "", is_group: bool = False) -> WeChatMessage:
    return WeChatMessage(
        msg_id=f"{sender}:{room_id or 'private'}",
        msg_type=1,
        content="测试",
        sender=sender,
        room_id=room_id,
        create_time=datetime.fromtimestamp(1778673000),
        is_group=is_group,
    )


def test_should_process_skips_private_not_in_whitelist(monkeypatch):
    monkeypatch.setattr(
        "app.core.message_monitor.get_config",
        lambda: SimpleNamespace(
            auto_reply={
                "enabled": True,
                "private_chat_mode": "whitelist",
                "private_whitelist": ["wxid_allowed"],
            }
        ),
    )

    assert _monitor()._should_process(_msg("wxid_blocked")) is False


def test_should_process_keeps_private_in_whitelist(monkeypatch):
    monkeypatch.setattr(
        "app.core.message_monitor.get_config",
        lambda: SimpleNamespace(
            auto_reply={
                "enabled": True,
                "private_chat_mode": "whitelist",
                "private_whitelist": ["wxid_allowed"],
            }
        ),
    )

    assert _monitor()._should_process(_msg("wxid_allowed")) is True


def test_should_process_skips_group_not_in_whitelist(monkeypatch):
    monkeypatch.setattr(
        "app.core.message_monitor.get_config",
        lambda: SimpleNamespace(
            auto_reply={
                "enabled": True,
                "group_chat_mode": "whitelist",
                "group_whitelist": ["allowed@chatroom"],
            }
        ),
    )

    msg = _msg("member_wxid", room_id="blocked@chatroom", is_group=True)

    assert _monitor()._should_process(msg) is False


def test_should_process_skips_recent_bot_sent_message(monkeypatch):
    monkeypatch.setattr(
        "app.core.message_monitor.get_config",
        lambda: SimpleNamespace(
            auto_reply={
                "enabled": True,
                "private_chat_mode": "all",
            }
        ),
    )
    monitor = _monitor()
    monitor.remember_sent_message("wxid_receiver", "AI 回复")

    msg = _msg("wxid_receiver")
    msg.content = "AI 回复"

    assert monitor._should_process(msg) is False
