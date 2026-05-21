"""Windows 平台消息发送器测试。

验证 WindowsSender 通过 httpx 调用 WCF HTTP API 的行为，
包括重试、错误处理和接口兼容性。
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeResponse:
    """模拟 httpx Response。"""
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data


class FakeAsyncClient:
    """模拟 httpx.AsyncClient，记录请求并返回预设响应。"""
    def __init__(self, responses: list[FakeResponse] | None = None):
        self.responses = responses or []
        self.requests: list[tuple[str, dict]] = []
        self._call_count = 0
        self.is_closed = False

    async def post(self, url: str, json: dict | None = None, timeout=None):
        self.requests.append((url, json or {}))
        if self._call_count < len(self.responses):
            resp = self.responses[self._call_count]
            self._call_count += 1
            return resp
        return FakeResponse(200, {"status": 0})

    async def aclose(self):
        self.is_closed = True


def _make_sender(responses=None):
    """创建注入 fake client 的 WindowsSender。"""
    from app.core.sender_windows import WindowsSender

    sender = WindowsSender()
    sender._client = FakeAsyncClient(responses)
    return sender


@pytest.mark.asyncio
async def test_send_text_calls_wcf_api():
    sender = _make_sender([FakeResponse(200, {"status": 0})])
    ok = await sender.send_text("你好", "wxid_test")

    assert ok is True
    assert len(sender._client.requests) == 1
    url, payload = sender._client.requests[0]
    assert "/wcf/send_txt" in url
    assert payload["msg"] == "你好"
    assert payload["receiver"] == "wxid_test"


@pytest.mark.asyncio
async def test_send_text_with_is_group_sets_notify_all():
    sender = _make_sender([FakeResponse(200, {"status": 0})])
    ok = await sender.send_text("群发消息", "room@chatroom", is_group=True)

    assert ok is True
    _, payload = sender._client.requests[0]
    assert payload["aters"] == "notify@all"


@pytest.mark.asyncio
async def test_send_text_empty_msg_returns_false():
    from app.core.sender_windows import WindowsSender

    sender = WindowsSender()
    ok = await sender.send_text("", "wxid_test")
    assert ok is False


@pytest.mark.asyncio
async def test_send_text_retry_on_timeout():
    import httpx

    sender = _make_sender([])
    # 替换 instance 的 post 方法为抛超时异常
    async def raise_timeout(self, url, json=None, timeout=None):
        raise httpx.TimeoutException("timeout")
    sender._client.post = raise_timeout.__get__(sender._client, FakeAsyncClient)
    sender._max_retries = 2

    ok = await sender.send_text("test", "wxid_test")
    assert ok is False


@pytest.mark.asyncio
async def test_open_chat_noop():
    from app.core.sender_windows import WindowsSender

    sender = WindowsSender()
    ok = await sender.open_chat("任意联系人")
    assert ok is True


def test_reset_search_state_noop():
    from app.core.sender_windows import WindowsSender

    sender = WindowsSender()
    sender.reset_search_state()


@pytest.mark.asyncio
async def test_is_wechat_running_wcf_login_check():
    sender = _make_sender([FakeResponse(200, {"status": 1})])
    running = await sender.is_wechat_running()
    assert running is True


@pytest.mark.asyncio
async def test_get_contacts_returns_list():
    sender = _make_sender([
        FakeResponse(200, {"data": [{"wxid": "wxid_1", "nickname": "测试"}]})
    ])
    contacts = await sender.get_contacts()
    assert isinstance(contacts, list)
    assert len(contacts) == 1
    assert contacts[0]["wxid"] == "wxid_1"


@pytest.mark.asyncio
async def test_close_client():
    from app.core.sender_windows import WindowsSender

    sender = WindowsSender()
    await sender.close()
