"""Windows 平台消息发送器测试。

验证 WindowsSender 通过鼠标点击和微信右键粘贴菜单操作 GUI。
所有测试 mock pyautogui 调用，验证操作序列而非实际 GUI 行为。
"""

import os
import sqlite3
import sys
import threading
import time
from unittest.mock import MagicMock, patch
from PIL import Image

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
        patch("pyautogui.rightClick", MagicMock()),
        patch("pyautogui.size", MagicMock(return_value=(1920, 1080))),
        patch("pyautogui.screenshot", MagicMock(side_effect=lambda region: Image.new("RGB", (region[2], region[3])))),
        patch("pyperclip.copy", MagicMock()),
    ):
        yield


class FakeWindow:
    """模拟 pygetwindow 窗口对象。"""
    def __init__(self, left=0, top=0, width=1200, height=800, hwnd=None):
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.hwnd = hwnd

    def activate(self):
        pass


def _make_sender():
    """创建带 mock 窗口的 WindowsSender。"""
    from app.core.sender_windows import WindowsSender

    from app.core.vision_client import SearchHit
    vision = MagicMock()
    vision.locate_search_result.return_value = SearchHit(80, 100, "小号")
    vision.classify_chat_title.return_value = "小号"
    sender = WindowsSender(vision_client=vision)
    sender._confirm_visual_send = MagicMock(return_value=True)
    sender._window_activate_delay = 0
    sender._search_result_delay = 0
    sender._type_delay = 0
    sender._skip_search_ttl = 60
    sender._verify_after_send = False
    sender._snapshot_target_local_id = MagicMock(return_value=10)
    return sender


@pytest.mark.asyncio
async def test_visual_send_checks_grounded_result_and_fresh_title():
    sender = _make_sender()
    _mock_window(sender, FakeWindow(left=20, top=30))
    assert await sender.send_text("测试", "小号")
    assert (20 + 65 + 80, 30 + 30 + 100) in [c.args for c in pyautogui.click.call_args_list]
    sender._vision_client.classify_chat_title.assert_called()
    pyperclip.copy.assert_any_call("测试")


@pytest.mark.asyncio
async def test_visual_search_shows_full_contact_result_pane():
    sender = _make_sender()
    _mock_window(sender, FakeWindow(height=800))
    sender._capture_region = MagicMock(return_value=b"PNG")
    assert await sender.inspect_chat("小号", ["小号", "向崟吉"])
    assert sender._capture_region.call_args_list[0].args[0][3] == 760


@pytest.mark.asyncio
async def test_visual_send_rejects_wrong_title_and_stale_skip_state():
    sender = _make_sender()
    _mock_window(sender)
    sender._last_receiver = "小号"
    sender._last_send_time = time.monotonic()
    sender._vision_client.classify_chat_title.return_value = "向崟吉"
    assert not await sender.send_text("不得发送", "小号", force_skip=True)
    assert all(c.args != ("不得发送",) for c in pyperclip.copy.call_args_list)


@pytest.mark.asyncio
async def test_visual_send_rejects_missing_result_model_failure_and_missing_client():
    sender = _make_sender()
    _mock_window(sender)
    sender._vision_client.locate_search_result.return_value = None
    assert not await sender.send_text("不得发送", "小号")
    sender._vision_client.locate_search_result.side_effect = TimeoutError
    assert not await sender.send_text("不得发送", "小号")
    sender._vision_client = None
    assert not await sender.send_text("不得发送", "小号")
    assert all(c.args != ("不得发送",) for c in pyperclip.copy.call_args_list)


@pytest.mark.asyncio
async def test_visual_send_rejects_window_resize_before_click():
    sender = _make_sender()
    original = FakeWindow(width=1200)
    resized = FakeWindow(width=1300)
    sender._find_wechat_window = MagicMock(return_value=original)
    sender._vision_client.locate_search_result.side_effect = lambda *args: (
        setattr(sender._find_wechat_window, "return_value", resized) or
        __import__("app.core.vision_client", fromlist=["SearchHit"]).SearchHit(80, 100, "小号")
    )
    assert not await sender.send_text("不得发送", "小号")
    assert all(c.args != ("不得发送",) for c in pyperclip.copy.call_args_list)


@pytest.mark.asyncio
async def test_visual_send_returns_unconfirmed_without_self_echo():
    sender = _make_sender()
    _mock_window(sender)
    sender._confirm_visual_send.return_value = False
    assert not await sender.send_text("测试", "小号")
    sender._confirm_visual_send.assert_called_once_with("测试", "小号")


@pytest.mark.asyncio
async def test_automatic_reply_confirms_exact_database_target_without_message_screenshots():
    sender = _make_sender()
    _mock_window(sender)
    sender._verify_sent_text = MagicMock(return_value=True)
    with patch("app.core.sender_windows.time.time", return_value=1234):
        assert await sender.send_text("测试", "小号", target_id="wxid_small")
    sender._verify_sent_text.assert_called_once_with("测试", 1234, "wxid_small", required=True, after_local_id=10)
    sender._confirm_visual_send.assert_not_called()


@pytest.mark.asyncio
async def test_automatic_reply_does_not_claim_success_when_database_target_has_no_echo():
    sender = _make_sender()
    _mock_window(sender)
    sender._verify_sent_text = MagicMock(return_value=False)
    assert not await sender.send_text("测试", "小号", target_id="wxid_small")
    sender._confirm_visual_send.assert_not_called()


def test_visual_send_confirmation_requires_exact_last_self_bubble():
    from app.core.vision_client import VisualBubble
    sender = _make_sender()
    del sender._confirm_visual_send
    _mock_window(sender)
    sender._verify_visual_title = MagicMock(return_value=True)
    sender._capture_region = MagicMock(return_value=b"PNG")
    sender._vision_client.read_visible_messages = MagicMock(
        return_value=[VisualBubble("other", "测试")]
    )
    with patch("app.core.sender_windows.time.sleep", lambda _: None):
        assert not sender._confirm_visual_send("测试", "小号")
        sender._vision_client.read_visible_messages.return_value = [
            VisualBubble("self", "别的消息")
        ]
        assert not sender._confirm_visual_send("测试", "小号")
        sender._vision_client.read_visible_messages.return_value = [
            VisualBubble("self", "测试")
        ]
        assert sender._confirm_visual_send("测试", "小号")


def _mock_window(sender, window=None):
    """注入假窗口。"""
    if window is None:
        window = FakeWindow()
    sender._find_wechat_window = MagicMock(return_value=window)
    return window


def test_activation_does_not_click_an_occluding_application():
    sender = _make_sender()
    window = _mock_window(sender, FakeWindow(hwnd=123))
    sender._ensure_window_visible = MagicMock(return_value=window)
    with patch("win32gui.GetForegroundWindow", return_value=456):
        with pytest.raises(RuntimeError, match="前台"):
            sender._activate_wechat()
    pyautogui.click.assert_not_called()


@pytest.mark.asyncio
async def test_visual_result_is_not_clicked_after_focus_changes_during_model_request():
    sender = _make_sender()
    window = _mock_window(sender, FakeWindow(hwnd=123))
    sender._ensure_window_visible = MagicMock(return_value=window)
    foreground = [123]
    from app.core.vision_client import SearchHit

    def model_reply(*args):
        foreground[0] = 456
        return SearchHit(80, 100, "小号")

    sender._vision_client.locate_search_result.side_effect = model_reply
    with patch("win32gui.GetForegroundWindow", side_effect=lambda: foreground[0]):
        assert not await sender.send_text("不得发送", "小号")
    assert (65 + 80, 30 + 100) not in [c.args for c in pyautogui.click.call_args_list]
    assert all(c.args != ("不得发送",) for c in pyperclip.copy.call_args_list)


@pytest.mark.asyncio
async def test_send_text_full_search_flow():
    """完整搜索发送：点击搜索框 → 粘贴名称 → 点击结果 → 粘贴消息 → 点击发送。"""
    sender = _make_sender()
    _mock_window(sender)

    ok = await sender.send_text("你好", "小号")

    assert ok is True
    # 不使用键盘快捷键或按键，避免焦点错误导致退出/误操作。
    pyautogui.hotkey.assert_not_called()
    pyautogui.press.assert_not_called()
    # 验证消息已复制到剪贴板
    pyperclip.copy.assert_any_call("你好")
    pyperclip.copy.assert_any_call("小号")


@pytest.mark.asyncio
async def test_send_text_skip_search_same_receiver():
    """同接收者在 TTL 内应跳过搜索。"""
    sender = _make_sender()
    _mock_window(sender)

    # 第一次：完整搜索
    ok = await sender.send_text("第一条", "小号")
    assert ok is True
    pyautogui.hotkey.assert_not_called()
    pyautogui.press.assert_not_called()

    # 重置 mock 调用记录
    pyautogui.hotkey.reset_mock()

    # 第二次同接收者也必须重新搜索并验证。
    await sender.send_text("第二条", "小号")
    assert sender._vision_client.locate_search_result.call_count == 2

    pyautogui.hotkey.assert_not_called()
    pyautogui.press.assert_not_called()


@pytest.mark.asyncio
async def test_send_text_group_chat_always_full_search():
    """当前视觉发送仅允许私聊。"""
    sender = _make_sender()
    _mock_window(sender)

    assert not await sender.send_text("消息1", "测试群", is_group=True)
    assert all(c.args != ("消息1",) for c in pyperclip.copy.call_args_list)


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
    pyautogui.hotkey.assert_not_called()
    pyautogui.press.assert_not_called()
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
    assert isinstance(lock, type(threading.Lock()))
    assert lock.acquire(blocking=False)  # 锁未被持有
    lock.release()


def test_ensure_window_visible_moves_offscreen_window():
    """非最大化微信窗口跑出屏幕时，应挪回可见区域再点击发送。"""
    sender = _make_sender()
    offscreen = FakeWindow(left=1173, top=47, width=922, height=802, hwnd=123)
    refreshed = FakeWindow(left=990, top=47, width=922, height=802, hwnd=123)
    sender._find_wechat_window = MagicMock(return_value=refreshed)

    with (
        patch("win32gui.GetWindowPlacement", MagicMock(return_value=(0, 1, None, None, None))),
        patch("win32gui.SetWindowPos", MagicMock()) as set_window_pos,
    ):
        result = sender._ensure_window_visible(offscreen)

    assert result is refreshed
    set_window_pos.assert_called_once()
    args = set_window_pos.call_args[0]
    assert args[2] == 990
    assert args[3] == 47


def test_reader_has_recent_self_text_requires_target_v4_table():
    from app.core.sender_windows import WindowsSender

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE Msg_group (local_id INTEGER, create_time INTEGER, real_sender_id INTEGER, "
        "message_content TEXT, local_type INTEGER, status INTEGER, origin_source INTEGER, server_seq INTEGER)"
    )
    conn.execute(
        "CREATE TABLE Msg_private (local_id INTEGER, create_time INTEGER, real_sender_id INTEGER, "
        "message_content TEXT, local_type INTEGER, status INTEGER, origin_source INTEGER, server_seq INTEGER)"
    )
    conn.execute(
        "INSERT INTO Msg_private VALUES (1, 100, 1, '发错窗口了', 1, 3, 0, 10)"
    )

    class Reader:
        _sqlite_conn = conn

        def _has_msg_shard_tables(self):
            return True

        def _get_v4_msg_tables(self):
            return [
                ("Msg_group", "room@chatroom"),
                ("Msg_private", "wxid_friend"),
            ]

        def _is_self_sent_v4_row(self, row):
            return True

        def _decode_message_content(self, content):
            return str(content or "")

    assert (
        WindowsSender._reader_has_recent_self_text(
            Reader(),
            "发错窗口了",
            90,
            target_id="room@chatroom",
        )
        is False
    )
    assert (
        WindowsSender._reader_has_recent_self_text(
            Reader(),
            "发错窗口了",
            90,
            target_id="wxid_friend",
        )
        is True
    )


@pytest.mark.asyncio
async def test_group_send_requires_exact_id_and_database_echo():
    from app.core.vision_client import SearchHit
    sender = _make_sender()
    _mock_window(sender)
    sender._vision_client.locate_search_result.return_value = SearchHit(80, 100, "\u59731\u6d4b\u8bd5")
    sender._vision_client.classify_chat_title.return_value = "\u59731\u6d4b\u8bd5"
    sender._verify_sent_text = MagicMock(return_value=True)
    for target_id in ("", "wxid_contact"):
        assert not await sender.send_text("relay", "\u59731\u6d4b\u8bd5", is_group=True, target_id=target_id)
    sender._vision_client.locate_search_result.assert_not_called()
    with patch("app.core.sender_windows.time.time", return_value=1234):
        assert await sender.send_text("relay", "\u59731\u6d4b\u8bd5", is_group=True, target_id="123@chatroom")
    assert sender._vision_client.locate_search_result.call_args.kwargs == {"is_group": True}
    sender._verify_sent_text.assert_called_once_with("relay", 1234, "123@chatroom", required=True, after_local_id=10)
    sender._confirm_visual_send.assert_not_called()
    sender._verify_sent_text.return_value = False
    assert not await sender.send_text("relay", "\u59731\u6d4b\u8bd5", is_group=True, target_id="123@chatroom")
    sender._confirm_visual_send.assert_not_called()


def test_database_key_matching_requires_absolute_account_path():
    from app.core.sender_windows import WindowsSender
    target = "C:/wechat/account1/db_storage/message/message_0.db"
    assert WindowsSender._key_matches_db_path(target.upper(), target)
    for key_path in ("message_0.db", "message/message_0.db", "db_storage/message/message_0.db", "C:/wechat/account2/db_storage/message/message_0.db"):
        assert not WindowsSender._key_matches_db_path(key_path, target)


def test_database_confirmation_uses_platform_selected_account_keys(monkeypatch):
    from types import SimpleNamespace
    from app.core.sender_windows import WindowsSender
    from app.core.platform import Platform
    from app.core.db_reader_windows import WindowsDBReader
    from app.core.key_extractor_windows import WindowsKeyExtractor
    selected = "C:/wechat/account1/db_storage/message/message_0.db"
    other = "C:/wechat/account2/db_storage/message/message_0.db"
    keys = {selected: "aa", "C:/wechat/account1/db_storage/contact/contact.db": "bb", other: "cc"}
    extractor = SimpleNamespace(load_keys=lambda: keys, select_account_keys=WindowsKeyExtractor.select_account_keys)
    monkeypatch.setattr(Platform, "get", lambda: SimpleNamespace(key_extractor=extractor))
    monkeypatch.setattr(WindowsDBReader, "find_database_files", lambda: [other, selected])
    assert WindowsSender._find_message_db_key() == (selected, "aa")
    keys["C:/wechat/account2/db_storage/contact/contact.db"] = "dd"
    assert WindowsSender._find_message_db_key() == ("", "")


@pytest.mark.asyncio
async def test_group_inspect_and_open_require_visual_result_and_title():
    from app.core.vision_client import SearchHit
    sender = _make_sender()
    _mock_window(sender)
    sender._vision_client.locate_search_result.return_value = SearchHit(80, 100, "group")
    sender._vision_client.classify_chat_title.return_value = "group"
    assert await sender.inspect_chat("group", ["group"], is_group=True)
    assert await sender.open_chat("group", is_group=True)
    sender._vision_client.locate_search_result.return_value = None
    assert not await sender.open_chat("group", is_group=True)
    sender._vision_client = None
    assert not await sender.open_chat("group", is_group=True)


@pytest.mark.asyncio
async def test_group_send_stops_when_foreground_changes_during_search():
    from app.core.vision_client import SearchHit
    sender = _make_sender()
    window = FakeWindow(hwnd=42)
    _mock_window(sender, window)
    with patch.dict(sys.modules, {"win32gui": MagicMock(GetForegroundWindow=MagicMock(return_value=42))}):
        import win32gui
        def located(*args, **kwargs):
            win32gui.GetForegroundWindow.return_value = 99
            return SearchHit(80, 100, "group")
        sender._vision_client.locate_search_result.side_effect = located
        sender._vision_client.classify_chat_title.return_value = "group"
        sender._verify_sent_text = MagicMock(return_value=True)
        assert not await sender.send_text("never", "group", is_group=True, target_id="123@chatroom")
        assert all(c.args != ("never",) for c in pyperclip.copy.call_args_list)
        sender._verify_sent_text.assert_not_called()


def _baseline_reader():
    from types import SimpleNamespace
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE Msg_target (local_id INTEGER, create_time INTEGER, real_sender_id INTEGER, message_content TEXT, local_type INTEGER, status INTEGER, origin_source INTEGER, server_seq INTEGER)")
    return SimpleNamespace(_sqlite_conn=conn, _has_msg_shard_tables=lambda: True,
        _get_v4_msg_tables=lambda: [("Msg_target", "123@chatroom")],
        _is_self_sent_v4_row=lambda row: row["real_sender_id"] == 1,
        _decode_message_content=lambda content: content)


def test_database_confirmation_accepts_new_local_id_despite_clock_skew():
    from app.core.sender_windows import WindowsSender
    reader = _baseline_reader()
    reader._sqlite_conn.execute("INSERT INTO Msg_target VALUES (11, 50, 1, 'relay', 1, 2, 1, 99)")
    assert WindowsSender._reader_has_recent_self_text(reader, "relay", 100, "123@chatroom", after_local_id=10)
    assert not WindowsSender._reader_has_recent_self_text(reader, "relay", 100, "wrong@chatroom", after_local_id=10)
    reader._sqlite_conn.close()


def test_database_confirmation_does_not_reuse_identical_baseline_row():
    from app.core.sender_windows import WindowsSender
    reader = _baseline_reader()
    reader._sqlite_conn.execute("INSERT INTO Msg_target VALUES (10, 200, 1, 'relay', 1, 2, 1, 99)")
    assert not WindowsSender._reader_has_recent_self_text(reader, "relay", 100, "123@chatroom", after_local_id=10)
    reader._sqlite_conn.execute("INSERT INTO Msg_target VALUES (11, 200, 2, 'relay', 1, 2, 1, 99)")
    assert not WindowsSender._reader_has_recent_self_text(reader, "relay", 100, "123@chatroom", after_local_id=10)
    reader._sqlite_conn.close()


@pytest.mark.asyncio
async def test_required_database_snapshot_failure_prevents_message_composition():
    sender = _make_sender()
    _mock_window(sender)
    sender._snapshot_target_local_id = MagicMock(return_value=None)
    sender._verify_sent_text = MagicMock(return_value=False)
    sender._click_send_button = MagicMock()
    assert not await sender.send_text("never", "小号", target_id="wxid_target")
    assert all(c.args != ("never",) for c in pyperclip.copy.call_args_list)
    sender._click_send_button.assert_not_called()


def test_target_snapshot_reads_exact_table_baseline_and_closes_reader(monkeypatch):
    from app.core.sender_windows import WindowsSender
    from app.core.db_reader_windows import WindowsDBReader
    reader = _baseline_reader()
    reader._sqlite_conn.execute("INSERT INTO Msg_target VALUES (10, 200, 1, 'relay', 1, 2, 1, 99)")
    reader.open_db = MagicMock(return_value=True)
    reader.close = MagicMock()
    monkeypatch.setattr(WindowsSender, "_find_message_db_key", lambda: ("C:/fake/message_0.db", "aa"))
    monkeypatch.setattr("app.core.db_reader_windows.WindowsDBReader", lambda: reader)
    assert WindowsSender._snapshot_target_local_id("123@chatroom") == 10
    assert WindowsSender._snapshot_target_local_id("wrong@chatroom") is None
    assert reader.close.call_count == 2
    reader._sqlite_conn.close()
