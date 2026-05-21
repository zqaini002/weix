"""macOS 平台 WeChat 消息发送器。

私聊和群聊使用不同的搜索策略，各自独立状态避免互相干扰。
"""

import asyncio
import logging
import time

import psutil

from app.core.base import BaseMessageSender
from app.config import get_config

logger = logging.getLogger(__name__)


class _BaseMacOSSender(BaseMessageSender):
    """macOS 发送器基类：共享 osascript 执行、转义等公共逻辑。"""

    # 所有发送器实例共享的"最后活动时间戳"
    # 任意发送器发送后更新，其他发送器据此判断是否需要重新搜索
    _global_last_activity: float = 0.0
    _global_send_lock: asyncio.Lock | None = None
    _global_send_lock_loop: asyncio.AbstractEventLoop | None = None

    def __init__(self):
        config = get_config()
        macos_cfg = config.macos_sender if hasattr(config, "macos_sender") else {}
        self._type_delay = macos_cfg.get("type_delay", 0.3)
        self._window_activate_delay = macos_cfg.get("window_activate_delay", 0.5)
        self._skip_search_ttl = macos_cfg.get("skip_search_ttl", 60)
        self._search_result_delay = macos_cfg.get("search_result_delay", 2.0)

        self._last_receiver = ""
        self._last_send_time: float = 0.0

    # --- 子类覆盖 ---

    def _build_search_lines(self, receiver: str) -> list[str]:
        """构建搜索选择行。子类覆盖。"""
        raise NotImplementedError

    def _build_preamble_lines(self, receiver: str) -> list[str]:
        """构建 AppleScript 顶层辅助函数。子类按需覆盖。"""
        return self._build_focus_preamble_lines()

    def _build_pre_send_guard_lines(self, receiver: str, skip_search: bool = False) -> list[str]:
        """构建发送正文前的安全校验。子类按需覆盖。"""
        return []

    # --- 公共接口 ---

    async def send_text(self, msg: str, receiver: str, force_skip: bool = False) -> bool:
        if not msg or not receiver:
            logger.error("消息内容或接收者为空")
            return False
        safe_msg = self._escape(msg)
        safe_receiver = self._escape(receiver)
        async with self._get_global_send_lock():
            return await self._do_send(safe_msg, safe_receiver, force_skip=force_skip)

    async def open_chat(self, receiver: str) -> bool:
        """只打开指定聊天，不发送消息，用作自动回复后的停靠动作。"""
        if not receiver:
            logger.error("接收者为空，无法打开聊天")
            return False
        safe_receiver = self._escape(receiver)
        async with self._get_global_send_lock():
            script = self._build_open_chat_script(safe_receiver)
            success = await self._run(script, "open_chat")
            if success:
                self.reset_search_state()
            return success

    def reset_search_state(self) -> None:
        """清空免搜索状态，确保下一次发送重新搜索目标会话。"""
        self._last_receiver = ""
        self._last_send_time = 0.0
        _BaseMacOSSender._global_last_activity = time.monotonic()

    async def _do_send(self, safe_msg: str, safe_receiver: str, force_skip: bool = False) -> bool:
        elapsed = time.monotonic() - self._last_send_time
        same_receiver = (self._last_receiver == safe_receiver)
        # 如果有其他发送器在此之后活动过，窗口可能已切换，必须完整搜索
        other_activity = _BaseMacOSSender._global_last_activity > self._last_send_time
        skip_search = self._should_skip_search(
            force_skip=force_skip,
            same_receiver=same_receiver,
            other_activity=other_activity,
            elapsed=elapsed,
        )

        if same_receiver and not skip_search:
            reason = "其他发送器活动" if other_activity else f"免搜索已过期 ({elapsed:.0f}s > {self._skip_search_ttl}s)"
            logger.info("%s，使用完整搜索", reason)

        script = self._build_script(safe_msg, safe_receiver, skip_search=skip_search)
        success = await self._run(script, "send_text")
        if success:
            self._last_receiver = safe_receiver
            self._last_send_time = time.monotonic()
            _BaseMacOSSender._global_last_activity = self._last_send_time
        else:
            self._last_receiver = ""
            self._last_send_time = 0.0
            if skip_search and not force_skip:
                logger.warning("免搜索发送失败，重试完整搜索流程")
                script = self._build_script(safe_msg, safe_receiver, skip_search=False)
                success = await self._run(script, "send_text")
                if success:
                    self._last_receiver = safe_receiver
                    self._last_send_time = time.monotonic()
        return success

    async def send_image(self, path: str, receiver: str) -> bool:
        logger.warning("macOS 平台暂不支持 send_image")
        return False

    def _should_skip_search(
        self,
        force_skip: bool,
        same_receiver: bool,
        other_activity: bool,
        elapsed: float,
    ) -> bool:
        return (
            force_skip
            or (same_receiver and not other_activity and (elapsed <= self._skip_search_ttl))
        )

    async def is_wechat_running(self) -> bool:
        try:
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] and "wechat" in proc.info["name"].lower():
                    return True
            return False
        except Exception as exc:
            logger.error(f"检查微信进程失败: {exc}")
            return False

    # --- AppleScript 构建 ---

    def _build_script(self, msg: str, receiver: str, skip_search: bool = False) -> str:
        enter_delay = round(max(self._window_activate_delay, 0.8), 1)

        tail_lines = [
            "",
            *self._build_pre_send_guard_lines(receiver, skip_search=skip_search),
            f'do shell script "printf %s " & quoted form of "{msg}" & " | pbcopy"',
            "delay 0.5",
            "",
            'tell application "System Events"',
            '    tell process "WeChat"',
            "        key code 53",
            "        delay 0.1",
            "        my focusMessageInput()",
            "        delay 0.3",
            '        keystroke "a" using command down',
            "        delay 0.2",
            "        key code 9 using command down",
            f"        delay {self._type_delay}",
            "        key code 36",
            f"        delay {self._type_delay}",
            "    end tell",
            "end tell",
        ]

        if skip_search:
            head_lines = [
                'tell application "WeChat" to activate',
                f"delay {self._window_activate_delay}",
                "my raiseMainWindow()",
                "delay 0.1",
            ]
            return "\n".join(self._build_preamble_lines(receiver) + head_lines + tail_lines)

        head_lines = [
            'tell application "WeChat" to activate',
            "delay 0.8",
            "my raiseMainWindow()",
            "delay 0.1",
            "",
            f'do shell script "printf %s " & quoted form of "{receiver}" & " | pbcopy"',
            "",
            'tell application "System Events"',
            '    tell process "WeChat"',
            "        set frontmost to true",
            "        delay 0.2",
            "        key code 53",
            "        delay 0.15",
            '        keystroke "f" using command down',
            "        delay 0.5",
            # 双重 Cmd+A 确保焦点在搜索框并全选旧文本
            '        keystroke "a" using command down',
            "        delay 0.15",
            '        keystroke "a" using command down',
            "        delay 0.15",
            '        keystroke "v" using command down',
            f"        delay {self._search_result_delay}",
            *self._build_search_lines(receiver),
            f"        delay {enter_delay * 3}",
            "        key code 53",
            "        delay 0.15",
            "    end tell",
            "end tell",
            "delay 0.5",
        ]
        return "\n".join(self._build_preamble_lines(receiver) + head_lines + tail_lines)

    def _build_open_chat_script(self, receiver: str) -> str:
        """构建只搜索并进入聊天的 AppleScript。"""
        head_lines = [
            'tell application "WeChat" to activate',
            "delay 0.8",
            "my raiseMainWindow()",
            "delay 0.1",
            "",
            f'do shell script "printf %s " & quoted form of "{receiver}" & " | pbcopy"',
            "",
            'tell application "System Events"',
            '    tell process "WeChat"',
            "        set frontmost to true",
            "        delay 0.2",
            "        key code 53",
            "        delay 0.15",
            '        keystroke "f" using command down',
            "        delay 0.5",
            '        keystroke "a" using command down',
            "        delay 0.15",
            '        keystroke "a" using command down',
            "        delay 0.15",
            '        keystroke "v" using command down',
            f"        delay {self._search_result_delay}",
            *self._build_search_lines(receiver),
            f"        delay {round(max(self._window_activate_delay, 0.8), 1) * 3}",
            "        key code 53",
            "        delay 0.15",
            "    end tell",
            "end tell",
        ]
        return "\n".join(self._build_preamble_lines(receiver) + head_lines)

    @staticmethod
    def _build_focus_preamble_lines() -> list[str]:
        """点击微信窗口真实输入框正文区域，避免发送前光标焦点丢失。"""
        return [
            "on raiseMainWindow()",
            '    tell application "System Events"',
            '        tell process "WeChat"',
            "            set mainWindow to window 1",
            "            set bestArea to 0",
            "            repeat with candidateWindow in windows",
            "                try",
            "                    set candidateSize to size of candidateWindow",
            "                    set candidateArea to (item 1 of candidateSize) * (item 2 of candidateSize)",
            "                    if candidateArea > bestArea then",
            "                        set bestArea to candidateArea",
            "                        set mainWindow to candidateWindow",
            "                    end if",
            "                end try",
            "            end repeat",
            '            perform action "AXRaise" of mainWindow',
            "        end tell",
            "    end tell",
            "end raiseMainWindow",
            "",
            "on focusMessageInput()",
            '    tell application "WeChat" to activate',
            "    delay 0.2",
            '    tell application "System Events"',
            '        tell process "WeChat"',
            "            set frontmost to true",
            "            set mainWindow to window 1",
            "            set bestArea to 0",
            "            repeat with candidateWindow in windows",
            "                try",
            "                    set candidateSize to size of candidateWindow",
            "                    set candidateArea to (item 1 of candidateSize) * (item 2 of candidateSize)",
            "                    if candidateArea > bestArea then",
            "                        set bestArea to candidateArea",
            "                        set mainWindow to candidateWindow",
            "                    end if",
            "                end try",
            "            end repeat",
            '            perform action "AXRaise" of mainWindow',
            "            set winPos to position of mainWindow",
            "            set winSize to size of mainWindow",
            "            set inputPaneLeft to (item 1 of winPos + ((item 1 of winSize) * 0.32)) as integer",
            "            set minInputX to (item 1 of winPos + 560) as integer",
            "            if minInputX > inputPaneLeft then set inputPaneLeft to minInputX",
            "            set maxInputX to (item 1 of winPos + (item 1 of winSize) - 420) as integer",
            "            if inputPaneLeft > maxInputX then set inputPaneLeft to maxInputX",
            "            set clickX to inputPaneLeft",
            "            set clickY to (item 2 of winPos + ((item 2 of winSize) * 0.75)) as integer",
            "            click at {clickX, clickY}",
            "            delay 0.12",
            "            click at {clickX, clickY}",
            "            delay 0.12",
            "            key code 48",
            "        end tell",
            "    end tell",
            "end focusMessageInput",
            "",
        ]

    async def _run(self, script: str, operation: str) -> bool:
        try:
            logger.info(f"AppleScript [{operation}] 内容:\n{script}")
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            if proc.returncode == 0:
                logger.info(f"AppleScript {operation} 执行成功")
                return True
            else:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error(f"AppleScript {operation} 失败 (code={proc.returncode}): {err_msg}")
                return False
        except asyncio.TimeoutError:
            logger.error(f"AppleScript {operation} 超时 (30s)")
            return False
        except Exception as exc:
            logger.error(f"AppleScript {operation} 异常: {exc}")
            return False

    @staticmethod
    def _escape(text: str) -> str:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return escaped.replace("\n", " ").replace("\r", " ")

    @staticmethod
    def _get_global_send_lock() -> asyncio.Lock:
        """同一个微信 UI 只能串行操作，私聊、群聊和停靠共用一把锁。"""
        loop = asyncio.get_running_loop()
        if (
            _BaseMacOSSender._global_send_lock is None
            or _BaseMacOSSender._global_send_lock_loop is not loop
        ):
            _BaseMacOSSender._global_send_lock = asyncio.Lock()
            _BaseMacOSSender._global_send_lock_loop = loop
        return _BaseMacOSSender._global_send_lock


class PrivateChatSender(_BaseMacOSSender):
    """私聊发送器: 搜索后直接确认联系人第一项，不依赖额外截图权限。"""

    def _build_search_lines(self, receiver: str) -> list[str]:
        return [
            "        key code 36",
            "        delay 0.5",
        ]


class GroupChatSender(_BaseMacOSSender):
    """群聊发送器: 每次完整搜索并回车确认第一条群聊结果。"""

    def _should_skip_search(
        self,
        force_skip: bool,
        same_receiver: bool,
        other_activity: bool,
        elapsed: float,
    ) -> bool:
        return False

    def _build_script(self, msg: str, receiver: str, skip_search: bool = False) -> str:
        return super()._build_script(msg, receiver, skip_search=False)

    def _build_search_lines(self, receiver: str) -> list[str]:
        return [
            "        key code 36",
            "        delay 0.8",
        ]


class MacOSSender(BaseMessageSender):
    """兼容旧调用的 macOS 发送器门面。

    默认按私聊发送；传入 is_group=True 时使用群聊发送器。
    """

    def __init__(self):
        self._private_sender = PrivateChatSender()
        self._group_sender = GroupChatSender()

    async def send_text(
        self,
        msg: str,
        receiver: str,
        force_skip: bool = False,
        is_group: bool = False,
    ) -> bool:
        sender = self._group_sender if is_group else self._private_sender
        return await sender.send_text(msg, receiver, force_skip=force_skip)

    async def send_image(self, path: str, receiver: str) -> bool:
        return await self._private_sender.send_image(path, receiver)

    async def is_wechat_running(self) -> bool:
        return await self._private_sender.is_wechat_running()

    async def open_chat(self, receiver: str, is_group: bool = False) -> bool:
        sender = self._group_sender if is_group else self._private_sender
        return await sender.open_chat(receiver)

    def reset_search_state(self) -> None:
        self._private_sender.reset_search_state()
        self._group_sender.reset_search_state()

    def _build_script(
        self,
        msg: str,
        receiver: str,
        skip_search: bool = False,
        is_group: bool = False,
    ) -> str:
        sender = self._group_sender if is_group else self._private_sender
        return sender._build_script(msg, receiver, skip_search=skip_search)

    def _build_open_chat_script(self, receiver: str, is_group: bool = False) -> str:
        sender = self._group_sender if is_group else self._private_sender
        return sender._build_open_chat_script(receiver)

    @staticmethod
    def _escape(text: str) -> str:
        return _BaseMacOSSender._escape(text)
