"""Windows 平台 WeChat 消息发送器。

通过 pyautogui 模拟键盘鼠标操作微信 GUI，与 macOS AppleScript 方案对应。
支持私聊/群聊、免搜索缓存、全局锁串行化、发送后停靠。
"""

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pyautogui
import pyperclip

from app.core.base import BaseMessageSender
from app.config import get_config

logger = logging.getLogger(__name__)

# 微信窗口标题（中文/英文）
WECHAT_WINDOW_TITLES = ["微信", "WeChat"]

# 单线程 executor，保证 GUI 操作严格串行
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wx-gui")


class WindowsSender(BaseMessageSender):
    """Windows 平台消息发送器。

    通过 pyautogui 模拟键盘鼠标操作微信 GUI：
      - 完整搜索：激活微信 → Ctrl+F 搜索 → 粘贴名称 → Enter 确认 → 粘贴消息 → Enter 发送
      - 免搜索：同接收者 + 在 TTL 内 → 直接粘贴消息发送
      - 发送后停靠：切换到固定私聊，避免下一条消息搜索串扰
    """

    # 全局串行锁，确保单线程操作微信 GUI
    _gui_lock = threading.Lock()
    _global_last_activity: float = 0.0

    def __init__(self):
        config = get_config()
        win_cfg = config.windows_sender if hasattr(config, "windows_sender") else {}
        self._type_delay = win_cfg.get("type_delay", 0.3)
        self._window_activate_delay = win_cfg.get("window_activate_delay", 0.5)
        self._search_result_delay = win_cfg.get("search_result_delay", 2.0)
        self._skip_search_ttl = win_cfg.get("skip_search_ttl", 60)
        self._click_x_ratio = win_cfg.get("click_x_ratio", 0.5)
        self._click_y_ratio = win_cfg.get("click_y_ratio", 0.75)

        self._last_receiver = ""
        self._last_send_time: float = 0.0

    # --- 公共接口 ---

    async def send_text(
        self,
        msg: str,
        receiver: str,
        force_skip: bool = False,
        is_group: bool = False,
    ) -> bool:
        """发送文本消息。

        Args:
            msg: 消息内容。
            receiver: 接收者名称（用于搜索）。
            force_skip: 强制跳过搜索（macOS 兼容参数）。
            is_group: 是否为群聊（群聊始终完整搜索）。

        Returns:
            True 表示发送成功。
        """
        if not msg or not receiver:
            logger.error("消息内容或接收者为空")
            return False

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor,
            self._send_text_sync,
            msg,
            receiver,
            force_skip,
            is_group,
        )

    async def send_image(self, path: str, receiver: str) -> bool:
        logger.warning("Windows 平台暂不支持 send_image")
        return False

    async def is_wechat_running(self) -> bool:
        """检查微信进程是否在运行。"""
        return self._find_wechat_window() is not None

    async def open_chat(self, receiver: str) -> bool:
        """打开指定聊天（用于发送后停靠）。"""
        if not receiver:
            return False
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self._open_chat_sync, receiver)

    def reset_search_state(self) -> None:
        """清空免搜索状态。"""
        self._last_receiver = ""
        self._last_send_time = 0.0

    # --- 同步核心逻辑 ---

    def _send_text_sync(
        self,
        msg: str,
        receiver: str,
        force_skip: bool,
        is_group: bool,
    ) -> bool:
        """同步消息发送，在全局锁内执行。"""
        with self._gui_lock:
            try:
                # 检查微信是否在运行
                if self._find_wechat_window() is None:
                    logger.error("未找到微信窗口")
                    return False

                # 判断是否跳过搜索
                skip_search = self._should_skip_search(receiver, force_skip, is_group)

                if not skip_search:
                    self._full_search(receiver)
                else:
                    logger.info("免搜索发送 | receiver=%s", receiver)

                # 聚焦输入框 + 粘贴消息 + 发送
                self._activate_wechat()
                self._focus_message_input()
                self._clear_and_paste(msg)
                self._press_enter()

                # 更新状态
                self._last_receiver = receiver
                self._last_send_time = time.monotonic()
                WindowsSender._global_last_activity = self._last_send_time

                logger.info("消息发送成功 | receiver=%s", receiver)
                return True

            except Exception as exc:
                logger.error("消息发送失败: %s", exc)

                # 免搜索失败时重试完整搜索
                if skip_search and not force_skip:
                    logger.info("免搜索失败，重试完整搜索")
                    self.reset_search_state()
                    return self._send_text_sync(msg, receiver, force_skip=False, is_group=is_group)

                return False

    def _open_chat_sync(self, receiver: str) -> bool:
        """同步打开聊天。"""
        with self._gui_lock:
            try:
                self._full_search(receiver)
                self.reset_search_state()
                return True
            except Exception as exc:
                logger.error("打开聊天失败: %s", exc)
                return False

    # --- GUI 操作原语 ---

    @staticmethod
    def _find_wechat_window():
        """查找微信主窗口。"""
        try:
            import pygetwindow as gw
            for title in WECHAT_WINDOW_TITLES:
                windows = gw.getWindowsWithTitle(title)
                if windows:
                    return windows[0]
            return None
        except ImportError:
            # 回退：用 pyautogui 的窗口列表
            for title in WECHAT_WINDOW_TITLES:
                wins = pyautogui.getWindowsWithTitle(title)
                if wins:
                    return wins[0]
            return None

    def _activate_wechat(self) -> None:
        """激活微信窗口。"""
        win = self._find_wechat_window()
        if win is None:
            raise RuntimeError("未找到微信窗口")

        try:
            win.activate()
        except Exception:
            # pygetwindow.activate 某些版本不可靠，用点击任务栏兜底
            pass

        time.sleep(self._window_activate_delay)

    def _full_search(self, receiver: str) -> None:
        """完整搜索流程：激活 → Ctrl+F → 粘贴 → Enter → 等待加载。"""
        self._activate_wechat()

        # 清除任何已有的搜索
        pyautogui.press("escape")
        time.sleep(0.15)

        # Ctrl+F 打开搜索
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.5)

        # 全选旧搜索词 + 粘贴新名称
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyperclip.copy(receiver)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(self._search_result_delay)

        # Enter 确认第一个搜索结果
        pyautogui.press("enter")
        time.sleep(self._search_result_delay)

        # 关闭搜索框
        pyautogui.press("escape")
        time.sleep(0.15)

        logger.debug("完整搜索完成 | receiver=%s", receiver)

    def _focus_message_input(self) -> None:
        """点击消息输入区域，确保光标在输入框内。"""
        win = self._find_wechat_window()
        if win is None:
            return

        try:
            x = win.left + int(win.width * self._click_x_ratio)
            y = win.top + int(win.height * self._click_y_ratio)
        except AttributeError:
            # pyautogui 回退
            x, y = pyautogui.size()
            x = int(x * self._click_x_ratio)
            y = int(y * self._click_y_ratio)

        pyautogui.click(x, y)
        time.sleep(0.15)
        pyautogui.click(x, y)  # 双击确保焦点
        time.sleep(0.1)

    @staticmethod
    def _clear_and_paste(msg: str) -> None:
        """清空输入框并粘贴消息。"""
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyperclip.copy(msg)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)

    @staticmethod
    def _press_enter() -> None:
        """按 Enter 发送消息。"""
        time.sleep(0.15)
        pyautogui.press("enter")
        time.sleep(0.3)

    # --- 内部判断 ---

    def _should_skip_search(
        self,
        receiver: str,
        force_skip: bool,
        is_group: bool,
    ) -> bool:
        """判断是否可以跳过搜索直接发送。"""
        # 群聊始终完整搜索
        if is_group:
            return False

        # 强制跳过
        if force_skip:
            return True

        # 同接收者且在 TTL 内
        if receiver != self._last_receiver:
            return False

        other_activity = self._global_last_activity > self._last_send_time
        if other_activity:
            return False

        elapsed = time.monotonic() - self._last_send_time
        return elapsed <= self._skip_search_ttl
