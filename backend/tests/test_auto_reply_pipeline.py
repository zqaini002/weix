import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.auto_reply_pipeline import AutoReplyPipeline
from app.core.base import WeChatMessage


def test_source_selection_uses_verified_local_database(monkeypatch):
    pipeline = AutoReplyPipeline()
    platform = object()
    reader = object()
    monkeypatch.setattr(pipeline, "_load_keys", lambda _platform: {"message_0.db": "00" * 32})
    monkeypatch.setattr(pipeline, "_open_message_db", lambda _platform, keys: reader)
    assert pipeline._choose_source(platform, "auto") == ("database", reader)


def test_source_selection_stops_after_database_failure(monkeypatch):
    pipeline = AutoReplyPipeline()
    platform = object()
    monkeypatch.setattr(pipeline, "_load_keys", lambda _platform: {"message_0.db": "00" * 32})
    monkeypatch.setattr(pipeline, "_open_message_db", lambda _platform, keys: None)
    assert pipeline._choose_source(platform, "auto") == (None, None)
    assert pipeline._choose_source(platform, "database") == (None, None)


def test_legacy_vision_source_still_requires_database_keys(monkeypatch):
    pipeline = AutoReplyPipeline()
    monkeypatch.setattr(pipeline, "_load_keys", lambda *_: {})
    assert pipeline._choose_source(object(), "vision") == (None, None)


def test_source_stops_when_contact_names_cannot_cover_web_titles():
    assert AutoReplyPipeline._source_with_contact_names(
        "auto", "database", {}, ["任意联系人"]
    ) is None
    assert AutoReplyPipeline._source_with_contact_names(
        "auto", "database", {"wxid_friend": "其他人"}, ["任意联系人"]
    ) is None
    assert AutoReplyPipeline._source_with_contact_names(
        "auto", "database", {"wxid_friend": "任意联系人"}, ["任意联系人"]
    ) == "database"
    assert AutoReplyPipeline._source_with_contact_names(
        "database", "database", {}, ["任意联系人"]
    ) is None


def test_vision_resolves_mixed_contact_ids_and_manual_titles():
    assert AutoReplyPipeline._vision_names(
        ["wxid_friend", "手填标题"], {"wxid_friend": "朋友"}
    ) == ["朋友", "手填标题"]


@pytest.mark.asyncio
async def test_database_sender_id_passes_display_name_whitelist(monkeypatch):
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={
            "enabled": True, "private_chat_mode": "whitelist",
            "private_whitelist": ["小号"],
        }),
    )
    pipeline = AutoReplyPipeline()
    pipeline._name_map = {"wxid_friend": "小号"}
    pipeline._debounce_seconds = 999
    await pipeline._handle_message(_private_msg(content="你好"))
    for task in pipeline._buffer_timers.values():
        task.cancel()
    assert pipeline._buffer


@pytest.mark.asyncio
async def test_database_sender_title_matches_selected_contact_id(monkeypatch):
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={
            "enabled": True,
            "private_chat_mode": "whitelist",
            "private_whitelist": ["wxid_friend"],
            "reply_mode": "keyword",
        }),
    )
    pipeline = AutoReplyPipeline()
    pipeline.selected_source = "database"
    pipeline._name_map = {"wxid_friend": "朋友"}
    pipeline._debounce_seconds = 0
    sender = FakeSender()
    pipeline._sender = sender
    pipeline._rule_engine = FakeRuleEngine()
    pipeline._monitor = FakeMonitor()
    pipeline._park_after_send = False

    msg = WeChatMessage(msg_id="db:1", msg_type=1, content="你好", sender="wxid_friend")
    await pipeline._handle_message(msg)
    await pipeline._flush_buffer("wxid_friend")

    assert sender.sent[0][1] == "朋友"


@pytest.mark.asyncio
async def test_start_reports_missing_key_or_allowlist(monkeypatch):
    import app.core.auto_reply_pipeline as module
    config = SimpleNamespace(
        auto_reply={"private_whitelist": []}, ai={"api_key": ""},
        monitor={"source": "vision"}, windows_sender={},
    )
    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(module.Platform, "get", lambda: SimpleNamespace(sender=object(), is_macos=False))
    pipeline = AutoReplyPipeline()
    assert not await pipeline.start()
    assert "private_whitelist" in pipeline.disabled_reason
    config.auto_reply["private_whitelist"] = ["任意联系人"]
    pipeline = AutoReplyPipeline()
    assert not await pipeline.start()
    assert "DEEPSEEK_API_KEY" in pipeline.disabled_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("private_mode", ["whitelist", "all", "none"])
async def test_empty_web_whitelist_stays_stopped_with_database_source(monkeypatch, private_mode):
    import app.core.auto_reply_pipeline as module
    config = SimpleNamespace(
        auto_reply={"enabled": True, "private_chat_mode": private_mode, "private_whitelist": []},
        ai={"api_key": "test-key"}, monitor={"source": "database"}, windows_sender={},
    )
    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(module.Platform, "get", lambda: SimpleNamespace(sender=object(), is_macos=False))
    pipeline = AutoReplyPipeline()

    assert not await pipeline.start()
    assert "private_whitelist" in pipeline.disabled_reason


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
        lambda: SimpleNamespace(auto_reply={"reply_mode": "keyword", "group_chat_mode": "all"}),
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
        lambda: SimpleNamespace(auto_reply={"reply_mode": "ai", "private_chat_mode": "all"}),
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


@pytest.mark.asyncio
async def test_flush_buffer_respects_whitelist_removed_during_debounce(monkeypatch):
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={
            "enabled": True,
            "reply_mode": "keyword",
            "private_chat_mode": "whitelist",
            "private_whitelist": [],
        }),
    )
    sender = FakeSender()
    pipeline = AutoReplyPipeline()
    pipeline._sender = sender
    pipeline._rule_engine = FakeRuleEngine()
    pipeline._monitor = FakeMonitor()
    pipeline._park_after_send = False
    pipeline._debounce_seconds = 0
    pipeline._name_map = {"wxid_friend": "朋友"}
    pipeline._buffer["wxid_friend"] = [_private_msg()]

    await pipeline._flush_buffer("wxid_friend")

    assert sender.sent == []


@pytest.mark.asyncio
async def test_flush_buffer_refuses_ambiguous_database_contact_title(monkeypatch):
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={
            "enabled": True,
            "reply_mode": "keyword",
            "private_chat_mode": "whitelist",
            "private_whitelist": ["wxid_friend"],
        }),
    )
    sender = FakeSender()
    pipeline = AutoReplyPipeline()
    pipeline._sender = sender
    pipeline._rule_engine = FakeRuleEngine()
    pipeline._monitor = FakeMonitor()
    pipeline._park_after_send = False
    pipeline._debounce_seconds = 0
    pipeline._name_map = {"wxid_friend": "同名", "wxid_other": "同名"}
    pipeline._ambiguous_private_titles = {"同名"}
    pipeline._buffer["wxid_friend"] = [_private_msg()]

    await pipeline._flush_buffer("wxid_friend")

    assert sender.sent == []


@pytest.mark.asyncio
async def test_vision_buffer_stops_when_contact_id_removed_from_web_whitelist(monkeypatch):
    monkeypatch.setattr(
        "app.core.auto_reply_pipeline.get_config",
        lambda: SimpleNamespace(auto_reply={
            "enabled": True,
            "reply_mode": "keyword",
            "private_chat_mode": "whitelist",
            "private_whitelist": [],
        }),
    )
    sender = FakeSender()
    pipeline = AutoReplyPipeline()
    pipeline.selected_source = "vision"
    pipeline._vision_allowed_titles = {"朋友"}
    pipeline._vision_title_entries = {"朋友": "wxid_friend"}
    pipeline._name_map = {"朋友": "朋友"}
    pipeline._sender = sender
    pipeline._rule_engine = FakeRuleEngine()
    pipeline._monitor = FakeMonitor()
    pipeline._park_after_send = False
    pipeline._debounce_seconds = 0
    pipeline._buffer["朋友"] = [WeChatMessage(
        msg_id="vision:2", msg_type=1, content="你好", sender="朋友",
    )]

    await pipeline._flush_buffer("朋友")

    assert sender.sent == []


def test_clean_reply_for_wechat_removes_extra_spaces_newlines_and_emoji():
    text = AutoReplyPipeline._clean_reply_for_wechat("好 的\n\n我 知道 了  😄  ！")

    assert text == "好的我知道了！"
