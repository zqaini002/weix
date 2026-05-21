"""Windows 平台消息发送器测试。

验证 WindowsSender 通过 pyautogui 模拟键盘鼠标操作微信 GUI。
所有测试 mock pyautogui 调用，验证操作序列而非实际 GUI 行为。
"""

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pyautogui
import pyperclip
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def mock_pyautogui():
    """全局 mock pyautogui，防止测试中误操作真实 GUI。"""
    with (
        patch("pyautogui.press", MagicMock()),
        patch("pyautogui.hotkey", MagicMock()),
        patch("pyautogui.click", MagicMock()),
        patch("pyautogui.size", MagicMock(return_value=(1920, 1080))),
        patch("pyperclip.copy", MagicMock()),
    ):
        yield


class FakeWindow:
    """模拟 pygetwindow 窗口对象。"""
    def __init__(self, left=0, top=0, width=1200, height=800):
        self.left = left
        self.top = top
        self.width = width
        self.height = height

    def activate(self):
        pass


def _make_sender():
    """创建带 mock 窗口的 WindowsSender。"""
    from app.core.sender_windows import WindowsSender

    sender = WindowsSender()
    sender._window_activate_delay = 0
    sender._search_result_delay = 0
    sender._type_delay = 0
    sender._skip_search_ttl = 60
    return sender


def _mock_window(sender, window=None):
    """注入假窗口。"""
    if window is None:
        window = FakeWindow()
    sender._find_wechat_window = MagicMock(return_value=window)
    return window


@pytest.mark.asyncio
async def test_send_text_full_search_flow():
    """完整搜索发送：Ctrl+F → 粘贴名称 → Enter → 粘贴消息 → Enter。"""
    sender = _make_sender()
    _mock_window(sender)

    ok = await sender.send_text("你好", "文件传输助手")

    assert ok is True
    # 验证搜索快捷键
    pyautogui.hotkey.assert_any_call("ctrl", "f")
    # 验证消息已复制到剪贴板
    pyperclip.copy.assert_any_call("你好")
    # 验证消息粘贴
    pyautogui.hotkey.assert_any_call("ctrl", "v")


@pytest.mark.asyncio
async def test_send_text_skip_search_same_receiver():
    """同接收者在 TTL 内应跳过搜索。"""
    sender = _make_sender()
    _mock_window(sender)

    # 第一次：完整搜索
    await sender.send_text("第一条", "文件传输助手")
    pyautogui.hotkey.assert_any_call("ctrl", "f")

    # 重置 mock 调用记录
    pyautogui.hotkey.reset_mock()

    # 第二次：同接收者，应跳过搜索
    await sender.send_text("第二条", "文件传输助手")

    # 不应有 Ctrl+F
    for call_args in pyautogui.hotkey.call_args_list:
        args = call_args[0] if call_args[0] else call_args[1]
        assert args != ("ctrl", "f"), "同接收者不应触发搜索"


@pytest.mark.asyncio
async def test_send_text_group_chat_always_full_search():
    """群聊始终使用完整搜索，不跳过。"""
    sender = _make_sender()
    _mock_window(sender)

    # 两次群聊，每次都应有 Ctrl+F
    await sender.send_text("消息1", "测试群", is_group=True)
    pyautogui.hotkey.assert_any_call("ctrl", "f")

    pyautogui.hotkey.reset_mock()

    await sender.send_text("消息2", "测试群", is_group=True)
    # 群聊不跳过搜索
    found_search = False
    for call_args in pyautogui.hotkey.call_args_list:
        args = call_args[0] if call_args[0] else call_args[1]
        if args == ("ctrl", "f"):
            found_search = True
    assert found_search, "群聊应始终执行搜索"


@pytest.mark.asyncio
async def test_send_text_empty_msg_returns_false():
    """空消息直接返回 False。"""
    sender = _make_sender()

    ok = await sender.send_text("", "wxid_test")
    assert ok is False

    ok = await sender.send_text("hello", "")
    assert ok is False


@pytest.mark.asyncio
async def test_send_text_no_wechat_window():
    """微信未运行时应返回 False。"""
    sender = _make_sender()
    sender._find_wechat_window = MagicMock(return_value=None)

    ok = await sender.send_text("你好", "测试")
    assert ok is False


@pytest.mark.asyncio
async def test_open_chat_searches_without_sending():
    """open_chat 应执行搜索但不发送消息。"""
    sender = _make_sender()
    _mock_window(sender)

    ok = await sender.open_chat("小号")
    assert ok is True
    pyautogui.hotkey.assert_any_call("ctrl", "f")
    # 不应复制消息文本
    for call_args in pyperclip.copy.call_args_list:
        if call_args[0]:
            assert call_args[0][0] == "小号"


def test_reset_search_state():
    """reset_search_state 清空免搜索状态。"""
    sender = _make_sender()
    sender._last_receiver = "someone"
    sender._last_send_time = time.monotonic()

    sender.reset_search_state()
    assert sender._last_receiver == ""
    assert sender._last_send_time == 0.0


@pytest.mark.asyncio
async def test_is_wechat_running_true():
    """有微信窗口时返回 True。"""
    sender = _make_sender()
    _mock_window(sender)

    running = await sender.is_wechat_running()
    assert running is True


@pytest.mark.asyncio
async def test_is_wechat_running_false():
    """无微信窗口时返回 False。"""
    sender = _make_sender()
    sender._find_wechat_window = MagicMock(return_value=None)

    running = await sender.is_wechat_running()
    assert running is False


def test_global_lock_serialization():
    """验证全局锁确保 GUI 操作串行。"""
    from app.core.sender_windows import WindowsSender

    lock = WindowsSender._gui_lock
    assert isinstance(lock, threading.Lock)
    assert lock.acquire(blocking=False)  # 锁未被持有
    lock.release()
