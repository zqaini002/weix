import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.auto_reply_pipeline import AutoReplyPipeline
from app.core.base import WeChatMessage


class FakeRuleEngine:
    async def match(self, content):
        return {"matched": True, "reply": "自动回复"}


class FakeSender:
    def __init__(self):
        self.sent = []
        self.opened = []

    async def send_text(self, msg, receiver, **kwargs):
        self.sent.append((msg, receiver, kwargs))
        return True

    async def open_chat(self, receiver, **kwargs):
        self.opened.append((receiver, kwargs))
        return True

    def reset_search_state(self):
        pass


class FakeMonitor:
    def __init__(self):
        self.remembered = []

    def remember_sent_message(self, receiver, reply):
        self.remembered.append((receiver, reply))


class FakeAgent:
    def __init__(self):
        self.remembered = []
        self.chats = []

    async def remember_observation(self, message, session_id, context=None):
        self.remembered.append((message, session_id, context or {}))

    async def chat(self, message, session_id, context=None):
        self.chats.append((message, session_id, context or {}))
        return "好嘞\n\n我知道了 😄"


def _group_msg(room_id="room@chatroom"):
    return WeChatMessage(
        msg_id="1",
        msg_type=1,
        content="你好",
        sender=room_id,
        room_id=room_id,
        create_time=datetime.fromtimestamp(1778673000),
        is_group=True,
    )


def _private_msg(*, is_self=False, content="你好"):
    return WeChatMessage(
        msg_id="private:1",
        msg_type=1,
        content=content,
        sender="wxid_friend",
        room_id="",
        create_time=datetime.fromtimestamp(1778673000),
        is_group=False,
        is_self=is_self,
    )


@pytest.mark.asyncio
async def test_flush_buffer_uses_platform_sender_with_is_group(monkeypatch):
    """自动回复发送应走 Platform.sender facade，不应硬编码 macOS sender。"""
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={"reply_mode": "keyword"}),
    )

    sender = FakeSender()
    pipeline = AutoReplyPipeline()
    pipeline._sender = sender
    pipeline._rule_engine = FakeRuleEngine()
    pipeline._monitor = FakeMonitor()
    pipeline._park_after_send = False
    pipeline._debounce_seconds = 0
    pipeline._name_map = {"room@chatroom": "测试群"}
    pipeline._buffer["room@chatroom"] = [_group_msg()]

    await pipeline._flush_buffer("room@chatroom")

    assert sender.sent == [
        (
            "自动回复",
            "测试群",
            {"is_group": True, "force_skip": False, "target_id": "room@chatroom"},
        )
    ]
    assert pipeline._monitor.remembered == [("room@chatroom", "自动回复")]


@pytest.mark.asyncio
async def test_flush_buffer_refuses_unsearchable_group_without_display_name(monkeypatch):
    """群聊没有可搜索显示名时应拒绝发送，不能盲发到当前窗口。"""
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={"reply_mode": "keyword"}),
    )

    sender = FakeSender()
    pipeline = AutoReplyPipeline()
    pipeline._sender = sender
    pipeline._rule_engine = FakeRuleEngine()
    pipeline._monitor = FakeMonitor()
    pipeline._park_after_send = False
    pipeline._debounce_seconds = 0
    pipeline._name_map = {}
    pipeline._buffer["room@chatroom"] = [_group_msg()]

    await pipeline._flush_buffer("room@chatroom")

    assert sender.sent == []
    assert pipeline._monitor.remembered == []


def test_merge_chatroom_name_does_not_overwrite_existing_display_name():
    name_map = {"room@chatroom": "联系人表群名"}

    AutoReplyPipeline._merge_chatroom_name(name_map, "room@chatroom", "")

    assert name_map["room@chatroom"] == "联系人表群名"


def test_open_message_db_uses_platform_specific_reader():
    class FakeReader:
        def __init__(self):
            self.opened = []
            self.closed = False

        def find_database_files(self):
            return ["C:/Users/me/MSG.db"]

        def open_db(self, path, key):
            self.opened.append((path, key))
            return True

        def is_message_db(self):
            return True

        def is_contact_db(self):
            return False

        def close(self):
            self.closed = True

    reader = FakeReader()
    platform = SimpleNamespace(db_reader=reader)

    result = AutoReplyPipeline._open_message_db(platform, {"MSG.db": "00" * 32})

    assert result is reader
    assert reader.opened == [("C:/Users/me/MSG.db", bytes.fromhex("00" * 32))]


@pytest.mark.asyncio
async def test_handle_self_message_only_records_memory(monkeypatch):
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(
            auto_reply={
                "enabled": True,
                "private_chat_mode": "all",
            }
        ),
    )

    sender = FakeSender()
    agent = FakeAgent()
    pipeline = AutoReplyPipeline()
    pipeline._sender = sender
    pipeline._ai_agent = agent
    pipeline._monitor = FakeMonitor()
    pipeline._park_after_send = False
    pipeline._debounce_seconds = 0
    pipeline._name_map = {"wxid_friend": "朋友"}

    await pipeline._handle_message(_private_msg(is_self=True, content="我刚说的"))

    assert pipeline._buffer == {}
    assert sender.sent == []
    assert agent.chats == []
    assert agent.remembered == [
        (
            "我刚说的",
            "private:wxid_friend",
            {
                "is_group": False,
                "user_name": "朋友",
                "user_wxid": "wxid_friend",
                "room_id": "",
                "room_name": "",
                "speaker": "self",
            },
        )
    ]
    assert pipeline._format_recent_context("private:wxid_friend") == "我: 我刚说的"


@pytest.mark.asyncio
async def test_flush_buffer_cleans_reply_before_sending(monkeypatch):
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={"reply_mode": "ai"}),
    )

    sender = FakeSender()
    agent = FakeAgent()
    pipeline = AutoReplyPipeline()
    pipeline._sender = sender
    pipeline._ai_agent = agent
    pipeline._monitor = FakeMonitor()
    pipeline._park_after_send = False
    pipeline._debounce_seconds = 0
    pipeline._name_map = {"wxid_friend": "朋友"}
    pipeline._buffer["wxid_friend"] = [_private_msg(content="在吗")]

    await pipeline._flush_buffer("wxid_friend")

    assert sender.sent == [
        (
            "好嘞我知道了",
            "朋友",
            {"is_group": False, "force_skip": False, "target_id": "wxid_friend"},
        )
    ]
    assert "\n" not in sender.sent[0][0]
    assert "😄" not in sender.sent[0][0]


def test_clean_reply_for_wechat_removes_extra_spaces_newlines_and_emoji():
    text = AutoReplyPipeline._clean_reply_for_wechat("好 的\n\n我 知道 了  😄  ！")

    assert text == "好的我知道了！"
