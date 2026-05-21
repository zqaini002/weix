"""测试 MacOSSender AppleScript 生成逻辑。

覆盖场景矩阵:
    私聊 × 完整搜索
    私聊 × 免搜索 (skip_search)
    群聊 × 完整搜索
    群聊 × 强制完整搜索
"""

import pytest
import sys
import os
import asyncio

# 确保 backend 在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.sender_macos import GroupChatSender, MacOSSender, PrivateChatSender


@pytest.fixture
def sender() -> MacOSSender:
    """创建兼容 sender 实例用于测试。"""
    return MacOSSender()


@pytest.fixture
def private_sender() -> PrivateChatSender:
    """创建私聊 sender 实例用于测试。"""
    return PrivateChatSender()


@pytest.fixture
def group_sender() -> GroupChatSender:
    """创建群聊 sender 实例用于测试。"""
    return GroupChatSender()


# ================================================================
# 配置验证
# ================================================================

class TestConfig:
    def test_compat_sender_has_dedicated_senders(self, sender):
        assert isinstance(sender._private_sender, PrivateChatSender)
        assert isinstance(sender._group_sender, GroupChatSender)


# ================================================================
# 诊断日志
# ================================================================

class TestDiagnostics:
    @pytest.mark.asyncio
    async def test_run_logs_full_applescript_stderr(self, private_sender, monkeypatch, caplog):
        """AppleScript 失败时应保留完整 stderr，不能截断真正的 Python traceback。"""
        class FakeProcess:
            returncode = 1

            async def communicate(self):
                return b"", ("traceback-start-" + ("x" * 260) + "-traceback-end").encode()

        async def fake_create_subprocess_exec(*args, **kwargs):
            return FakeProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        with caplog.at_level("ERROR"):
            ok = await private_sender._run("return 1", "send_text")

        assert ok is False
        assert "-traceback-end" in caplog.text


# ================================================================
# 场景 1: 私聊 × 完整搜索
# ================================================================

class TestPrivateChatFullSearch:
    def test_no_arrows_in_private_search(self, private_sender):
        """私聊搜索不应靠箭头选择结果。"""
        script = private_sender._build_script("你好", "小号", skip_search=False)
        search_part = script.split("end tell\ndelay 0.5")[0]
        assert "key code 125" not in search_part, (
            f"私聊搜索不应有下箭头"
        )

    def test_contains_search_flow(self, private_sender):
        """私聊完整搜索应包含 Cmd+F 搜索流程。"""
        script = private_sender._build_script("你好", "小号", skip_search=False)
        assert 'keystroke "f" using command down' in script
        assert "小号" in script
        assert "set frontmost to true" in script
        assert "item 1 of winPos + 40" not in script

    def test_private_full_search_confirms_first_contact_without_ocr(self, private_sender):
        """私聊完整搜索应直接确认联系人第一项，不依赖 OCR 权限。"""
        script = private_sender._build_script("你好", "小号", skip_search=False)
        assert "selectPrivateSearchResult" not in script
        assert "--prefer-contact-result" not in script
        assert "screencapture -x -R" not in script
        assert "ocr_helper.swift" not in script
        assert "keystroke tab" not in script
        search_part = script.split("end tell\ndelay 0.5")[0]
        assert "key code 36" in search_part

    def test_no_text_area_accessibility_api(self, private_sender):
        """不应使用辅助功能 API (WeChat 不暴露内部元素)。"""
        script = private_sender._build_script("你好", "小号", skip_search=False)
        assert 'text area 1 of window 1' not in script, "不应使用 text area API"
        assert 'set focused of textElem' not in script, "不应使用 accessibility focus"


# ================================================================
# 场景 2: 私聊 × 免搜索 (同一人 60s 内再次发送)
# ================================================================

class TestPrivateChatSkipSearch:
    def test_no_cmd_f_in_skip_search(self, private_sender):
        """免搜索时不应有 Cmd+F 搜索。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        assert 'keystroke "f" using command down' not in script, (
            "免搜索模式下不应有 Cmd+F"
        )

    def test_skip_search_does_not_use_text_tab_focus(self, private_sender):
        """免搜索不应使用文本 Tab 命令，避免焦点落到左侧加号。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        assert "keystroke tab" not in script

    def test_clicks_input_box_before_paste(self, private_sender):
        """免搜索重复发送时应点击输入区，避免光标焦点丢失。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        paste_index = script.index("key code 9 using command down")
        focus_index = script.rindex("focusMessageInput()", 0, paste_index)
        assert focus_index < paste_index

    def test_focus_uses_largest_wechat_window(self, private_sender):
        """聚焦输入框应使用微信主窗口，避免搜一搜浮层抢占 window 1。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        handler = script.split("end focusMessageInput", 1)[0]
        assert "repeat with candidateWindow in windows" in handler
        assert "set mainWindow to candidateWindow" in handler
        assert "set winPos to position of mainWindow" in handler
        assert "set winSize to size of mainWindow" in handler
        assert "set winPos to position of window 1" not in handler

    def test_focus_handler_tabs_into_input_before_paste(self, private_sender):
        """点击兜底后应按一次真实 Tab 键码进入输入框。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        paste_index = script.index("key code 9 using command down")
        focus_index = script.rindex("focusMessageInput()", 0, paste_index)
        handler = script.split("end focusMessageInput", 1)[0]
        assert "key code 48" in handler
        assert "keystroke tab" not in script[focus_index:paste_index]

    def test_clears_existing_draft_after_focus(self, private_sender):
        """粘贴前应清空输入框草稿，避免探测/上次失败内容被拼接发送。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        paste_index = script.index("key code 9 using command down")
        focus_index = script.rindex("focusMessageInput()", 0, paste_index)
        clear_index = script.index('keystroke "a" using command down', focus_index)
        assert focus_index < clear_index < paste_index

    def test_pastes_with_key_code(self, private_sender):
        """粘贴应使用真实 V 键码，避免 keystroke v 被微信吞掉。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        assert "key code 9 using command down" in script
        assert 'keystroke "v" using command down' not in script

    def test_sends_with_return_key_code(self, private_sender):
        """发送应使用真实 Return 键码，避免 keystroke return 被微信吞掉。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        assert "key code 36" in script
        assert "keystroke return" not in script

    def test_skip_search_includes_focus_handler(self, private_sender):
        """免搜索脚本也必须定义 focusMessageInput handler。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        assert "on focusMessageInput()" in script
        assert "on raiseMainWindow()" in script
        assert "my raiseMainWindow()" in script

    def test_focus_click_uses_right_chat_input_area(self, private_sender):
        """输入区点击点应在右侧聊天输入空白区，避免误点左侧加号。"""
        script = private_sender._build_script("再发一条", "小号", skip_search=True)
        assert "set inputPaneLeft" in script
        assert "+ 560" in script
        assert "- 420" in script
        assert "* 0.75" in script
        assert "click at {clickX, clickY}" in script
        assert "pyautogui.click" not in script
        assert "* 0.62" not in script
        assert "* 0.92" not in script


# ================================================================
# 场景 2.5: 回复后停靠到固定私聊
# ================================================================

class TestParkingChat:
    def test_open_chat_searches_without_sending_message(self, private_sender):
        """停靠动作应只进入聊天，不粘贴/发送回复正文。"""
        script = private_sender._build_open_chat_script("小号")
        assert "小号" in script
        assert 'keystroke "f" using command down' in script
        assert "selectPrivateSearchResult" not in script
        assert "--prefer-contact-result" not in script
        assert "screencapture -x -R" not in script
        assert "key code 36" in script
        assert "key code 9 using command down" not in script
        assert "my focusMessageInput()" not in script

    def test_compat_sender_can_build_private_parking_script(self, sender):
        """兼容门面应支持构建私聊停靠脚本。"""
        script = sender._build_open_chat_script("小号", is_group=False)
        assert "小号" in script
        assert "selectPrivateSearchResult" not in script
        assert "selectGroupSearchResult" not in script


# ================================================================
# 场景 3: 群聊 × 完整搜索
# ================================================================

class TestGroupChatFullSearch:
    def test_group_full_search_uses_return_without_screenshot_or_ocr(self, group_sender):
        """群聊搜索应回车确认第一条结果，不依赖截图或 OCR。"""
        script = group_sender._build_script(
            "测试消息", "贵州铜仁市129办公室工作群",
            skip_search=False,
        )
        assert "贵州铜仁市129办公室工作群" in script
        assert "screenshot_helper.py" not in script
        assert "weix_ocr_helper" not in script
        assert "ocr_helper.swift" not in script
        assert "verifyCurrentChatTitle" not in script
        assert "selectGroupSearchResult" not in script
        assert "pyautogui.click" not in script
        assert "--window-owner" not in script
        assert "--require-exact" not in script
        runtime_script = script.split('tell application "WeChat" to activate', 1)[1]
        paste_index = runtime_script.index("key code 9 using command down")
        search_return_index = runtime_script.rindex("key code 36", 0, paste_index)
        assert search_return_index < paste_index

    def test_group_full_search_confirms_result_before_sending(self, group_sender):
        """完整搜索应先回车进入群聊，再聚焦输入框发送。"""
        script = group_sender._build_script(
            "测试消息", "贵州铜仁市129办公室工作群",
            skip_search=False,
        )
        runtime_script = script.split('tell application "WeChat" to activate', 1)[1]
        search_index = runtime_script.index('keystroke "f" using command down')
        select_index = runtime_script.rindex(
            "key code 36",
            0,
            runtime_script.index("key code 9 using command down"),
        )
        paste_index = runtime_script.index("key code 9 using command down")
        assert search_index < select_index < paste_index

    def test_group_search_does_not_use_arrow_selection(self, group_sender):
        """群聊结果位置会变化，不应再靠下箭头选择。"""
        script = group_sender._build_script(
            "测试消息", "贵州铜仁市129办公室工作群",
            skip_search=False,
        )
        assert "key code 125" not in script

    def test_contains_group_name_in_search(self, group_sender):
        """搜索词应包含群名。"""
        script = group_sender._build_script(
            "测试", "贵州铜仁市129办公室工作群",
            skip_search=False,
        )
        assert "贵州铜仁市129办公室工作群" in script

    def test_group_full_search_does_not_use_text_tab_focus(self, group_sender):
        """群聊发送尾部不应使用文本 Tab 命令。"""
        script = group_sender._build_script("测试", "测试群", skip_search=False)
        assert "keystroke tab" not in script


# ================================================================
# 场景 4: 群聊 × 免搜索
# ================================================================

class TestGroupChatSkipSearch:
    @pytest.mark.asyncio
    async def test_group_sender_defaults_to_full_search_for_repeated_receiver(self, group_sender):
        """群聊默认不复用当前窗口，避免生产路径依赖当前聊天状态。"""
        scripts: list[str] = []

        async def fake_run(script: str, operation: str) -> bool:
            scripts.append(script)
            return True

        group_sender._run = fake_run

        assert await group_sender.send_text("第一条", "测试群") is True
        assert await group_sender.send_text("第二条", "测试群") is True

        assert len(scripts) == 2
        second_runtime = scripts[1].split('tell application "WeChat" to activate', 1)[1]
        search_index = second_runtime.index('keystroke "f" using command down')
        select_index = second_runtime.rindex(
            "key code 36",
            0,
            second_runtime.index("key code 9 using command down"),
        )
        paste_index = second_runtime.index("key code 9 using command down")
        assert search_index < select_index < paste_index

    def test_group_forced_skip_still_builds_full_search(self, group_sender):
        """群聊即使被要求免搜索，也应走完整搜索，避免发到当前私聊。"""
        script = group_sender._build_script("再来一条", "测试群", skip_search=True)
        assert 'keystroke "f" using command down' in script
        assert "测试群" in script
        assert "key code 125" not in script
        assert "verifyCurrentChatTitle" not in script
        assert "screenshot_helper.py" not in script
        paste_index = script.index("key code 9 using command down")
        search_index = script.index('keystroke "f" using command down')
        assert search_index < paste_index


# ================================================================
# 参数传递 / 竞态条件验证
# ================================================================

class TestParameterPassing:
    def test_no_is_group_receiver_attribute(self, sender):
        """不应存在易变的 _is_group_receiver 实例属性。"""
        assert not hasattr(sender, "_is_group_receiver"), (
            "_is_group_receiver 应为参数传递而非可变状态"
        )

    def test_is_group_avoids_screenshot_ocr_path(self, sender):
        """is_group 参数应走无截图、无 OCR 的群聊发送路径。"""
        script_group = sender._build_script(
            "x", "t", skip_search=False, is_group=True,
        )
        script_private = sender._build_script(
            "x", "t", skip_search=False, is_group=False,
        )
        assert "screenshot_helper.py" not in script_group
        assert "weix_ocr_helper" not in script_group
        assert "verifyCurrentChatTitle" not in script_group
        assert "selectGroupSearchResult" not in script_group
        assert "selectGroupSearchResult" not in script_private
        assert "verifyCurrentChatTitle" not in script_private
        assert "key code 125" not in script_private

    @pytest.mark.asyncio
    async def test_private_and_group_senders_share_ui_lock(self, private_sender, group_sender):
        """私聊和群聊操作同一个微信 UI，发送必须全局串行。"""
        import asyncio

        events = []

        async def private_run(script, operation):
            events.append("private:start")
            await asyncio.sleep(0.01)
            events.append("private:end")
            return True

        async def group_run(script, operation):
            events.append("group:start")
            await asyncio.sleep(0.01)
            events.append("group:end")
            return True

        private_sender._run = private_run
        group_sender._run = group_run

        await asyncio.gather(
            private_sender.send_text("p", "小号"),
            group_sender.send_text("g", "测试群"),
        )

        assert events in (
            ["private:start", "private:end", "group:start", "group:end"],
            ["group:start", "group:end", "private:start", "private:end"],
        )


# ================================================================
# 特殊字符处理
# ================================================================

class TestEscaping:
    def test_escapes_backslash(self, sender):
        result = sender._escape('test\\path')
        assert result == 'test\\\\path'

    def test_escapes_double_quote(self, sender):
        result = sender._escape('say "hello"')
        assert result == 'say \\"hello\\"'

    def test_escapes_newline(self, sender):
        result = sender._escape('line1\nline2')
        assert '\n' not in result


# ================================================================
# send_text 调用签名兼容性
# ================================================================

class TestSendTextSignature:
    @pytest.mark.asyncio
    async def test_is_group_default_false(self, sender):
        """is_group 默认应为 False (向后兼容)。"""
        import inspect
        sig = inspect.signature(sender.send_text)
        params = sig.parameters
        assert 'is_group' in params
        assert params['is_group'].default is False
