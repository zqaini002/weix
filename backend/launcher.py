"""
Weix GUI Launcher -- PyQt6 图形化启动器
功能: 启动/停止 uvicorn 后端服务，实时日志，状态指示，自动打开浏览器
"""
import sys
import os
import asyncio
import logging
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from enum import Enum, auto

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QStatusBar, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QObject
from PyQt6.QtGui import QFont, QTextCursor


# ============================================================
# 常量
# ============================================================
APP_TITLE = "Weix - 微信自动回复机器人"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
HEALTH_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/api/health"
BROWSER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
HEALTH_CHECK_INTERVAL_MS = 2000


class ServiceState(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    ERROR = auto()
    STOPPING = auto()


# ============================================================
# 日志桥接: Python logging -> PyQt 信号
# ============================================================
class LogSignalEmitter(QObject):
    """跨线程日志信号发射器。"""
    log_received = pyqtSignal(str)


_emitter = LogSignalEmitter()


class QtLogHandler(logging.Handler):
    """将 Python logging 输出转发到 PyQt 信号。"""

    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record):
        msg = self.format(record)
        _emitter.log_received.emit(msg)


# ============================================================
# 健康检查线程
# ============================================================
class HealthCheckThread(QThread):
    """轮询 /api/health 检测服务是否就绪。"""
    health_ok = pyqtSignal()
    health_failed = pyqtSignal(str)
    health_timeout = pyqtSignal()

    def __init__(self, url: str, interval_ms: int, timeout_s: int = 60):
        super().__init__()
        self._url = url
        self._interval = interval_ms / 1000.0
        self._timeout = timeout_s
        self._running = True

    def run(self):
        start = time.time()
        while self._running:
            if time.time() - start > self._timeout:
                self.health_timeout.emit()
                return
            try:
                req = urllib.request.Request(self._url, method='GET')
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        self.health_ok.emit()
                        return
            except Exception as e:
                self.health_failed.emit(str(e))
            time.sleep(self._interval)

    def stop(self):
        self._running = False


# ============================================================
# Uvicorn 服务管理
# ============================================================
_server = None
_server_thread = None
_server_error = ""


def _fix_none_streams():
    """PyInstaller --windowed 模式下 sys.stdout/stderr 为 None, 需要修复。"""
    import io
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def _run_uvicorn(host: str, port: int):
    """在当前线程中运行 uvicorn (阻塞)。"""
    global _server, _server_error
    log = logging.getLogger("launcher")

    _fix_none_streams()
    _server_error = ""

    try:
        import uvicorn
        from app.main import app as asgi_app

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        config = uvicorn.Config(
            app=asgi_app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
        _server = uvicorn.Server(config)

        # 定时检查 should_exit 标志 (从主线程设置)
        def _watchdog():
            if _server and _server.should_exit:
                log.info("收到停止信号，正在关闭...")
                return
            loop.call_later(0.5, _watchdog)

        loop.call_later(0.5, _watchdog)

        log.info("正在启动 uvicorn 服务 (%s:%d)...", host, port)
        loop.run_until_complete(_server.serve())
        log.info("uvicorn 服务已停止")

    except BaseException:
        import traceback
        _server_error = traceback.format_exc()
        log.error("uvicorn 启动失败:\n%s", _server_error)


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._health_thread: HealthCheckThread | None = None
        self._state = ServiceState.STOPPED
        self._startup_timer_count = 0

        # 连接日志信号
        _emitter.log_received.connect(self._append_log)

        self._init_ui()
        self._update_ui_state(ServiceState.STOPPED)

    def _init_ui(self):
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(700, 500)
        self.resize(800, 600)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 8)

        # --- 顶部: 状态 + 按钮 ---
        top_layout = QHBoxLayout()

        self._status_indicator = QLabel()
        self._status_indicator.setFixedSize(14, 14)
        self._status_label = QLabel()
        self._status_label.setFont(QFont("Microsoft YaHei", 11))
        top_layout.addWidget(self._status_indicator)
        top_layout.addWidget(self._status_label)
        top_layout.addStretch()

        self._btn_start = QPushButton("启动服务")
        self._btn_start.setFixedSize(120, 36)
        self._btn_start.clicked.connect(self._on_start)

        self._btn_stop = QPushButton("停止服务")
        self._btn_stop.setFixedSize(120, 36)
        self._btn_stop.clicked.connect(self._on_stop)

        self._btn_browser = QPushButton("打开浏览器")
        self._btn_browser.setFixedSize(120, 36)
        self._btn_browser.clicked.connect(self._open_browser)

        top_layout.addWidget(self._btn_start)
        top_layout.addWidget(self._btn_stop)
        top_layout.addWidget(self._btn_browser)
        layout.addLayout(top_layout)

        # --- 中部: 日志输出 ---
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Consolas", 10))
        self._log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        layout.addWidget(self._log_text, stretch=1)

        # --- 底部: 状态栏 ---
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("就绪 -- 点击「启动服务」开始")

    # --------------------------------------------------------
    # 状态管理
    # --------------------------------------------------------
    def _update_ui_state(self, state: ServiceState):
        self._state = state
        state_map = {
            ServiceState.STOPPED:  ("已停止",    "#888888", True,  False, False),
            ServiceState.STARTING: ("启动中...", "#f0ad4e", False, True,  False),
            ServiceState.RUNNING:  ("运行中",    "#5cb85c", False, True,  True),
            ServiceState.ERROR:    ("错误",      "#d9534f", True,  False, False),
            ServiceState.STOPPING: ("停止中...", "#f0ad4e", False, False, False),
        }
        text, color, start_en, stop_en, browser_en = state_map[state]
        self._status_label.setText(text)
        self._status_indicator.setStyleSheet(
            f"background-color: {color}; border-radius: 7px;"
        )
        self._btn_start.setEnabled(start_en)
        self._btn_stop.setEnabled(stop_en)
        self._btn_browser.setEnabled(browser_en)

    # --------------------------------------------------------
    # 启动服务 (进程内)
    # --------------------------------------------------------
    def _on_start(self):
        global _server_thread, _server_error

        self._log_text.clear()
        self._startup_timer_count = 0
        _server_error = ""
        self._log("正在启动 Weix 服务...")
        self._update_ui_state(ServiceState.STARTING)

        # PyInstaller --windowed: stdout/stderr 可能为 None
        _fix_none_streams()

        # 安装日志桥接 handler (捕获 uvicorn + app 的日志)
        root_logger = logging.getLogger()
        if not any(getattr(h, "_weix_qt_handler", False) for h in root_logger.handlers):
            handler = QtLogHandler()
            handler.setLevel(logging.DEBUG)
            handler._weix_qt_handler = True
            root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        logging.getLogger("uvicorn").setLevel(logging.INFO)

        # 先测试 app 模块能否正常导入
        self._log("检查 app 模块...")
        try:
            from app.main import app as asgi_app
            self._log(f"app 模块加载成功: {asgi_app.title}")
        except Exception:
            import traceback
            self._log(f"app 模块加载失败:\n{traceback.format_exc()}")
            self._update_ui_state(ServiceState.ERROR)
            return

        # 在守护线程中启动 uvicorn
        self._log("启动 uvicorn 服务器...")
        _server_thread = threading.Thread(
            target=_run_uvicorn,
            args=(SERVER_HOST, SERVER_PORT),
            daemon=True,
        )
        _server_thread.start()

        # 启动健康检查
        self._health_thread = HealthCheckThread(HEALTH_URL, HEALTH_CHECK_INTERVAL_MS)
        self._health_thread.health_ok.connect(self._on_health_ok)
        self._health_thread.health_failed.connect(self._on_health_failed)
        self._health_thread.health_timeout.connect(self._on_health_timeout)
        self._health_thread.start()

        self._statusbar.showMessage("正在等待服务就绪...")

    def _on_health_ok(self):
        self._update_ui_state(ServiceState.RUNNING)
        self._statusbar.showMessage(f"服务运行中 -- {BROWSER_URL}")
        self._log("=== 服务已就绪 ===")
        QTimer.singleShot(500, self._open_browser)

    def _on_health_failed(self, reason: str):
        if self._state == ServiceState.STARTING:
            if _server_thread and not _server_thread.is_alive():
                error = _server_error or f"服务进程已退出，最后一次健康检查失败: {reason}"
                self._log(f"错误: {error}")
                self._update_ui_state(ServiceState.ERROR)
                self._statusbar.showMessage("服务启动失败，请查看日志")
                return

            self._startup_timer_count += 1
            if self._startup_timer_count % 5 == 0:
                self._statusbar.showMessage(f"等待服务就绪... ({reason})")

    def _on_health_timeout(self):
        if self._state == ServiceState.STARTING:
            self._log("错误: 服务启动超时 (60秒)，请检查日志中的错误信息")
            self._update_ui_state(ServiceState.ERROR)
            self._statusbar.showMessage("服务启动超时")

    # --------------------------------------------------------
    # 停止服务
    # --------------------------------------------------------
    def _on_stop(self):
        global _server

        if _server is None:
            return

        self._update_ui_state(ServiceState.STOPPING)
        self._statusbar.showMessage("正在停止服务...")
        self._log("正在停止服务...")

        if self._health_thread:
            self._health_thread.stop()

        # 通知 uvicorn 退出
        _server.should_exit = True

        # 在后台等待线程结束，避免阻塞 UI
        def _wait_stop():
            global _server
            if _server_thread and _server_thread.is_alive():
                _emitter.log_received.emit("等待服务关闭...")
                _server_thread.join(timeout=10)
                if _server_thread.is_alive():
                    _emitter.log_received.emit("服务未响应，强制结束")
            _server = None
            QTimer.singleShot(0, lambda: self._on_stopped())

        threading.Thread(target=_wait_stop, daemon=True).start()

    def _on_stopped(self):
        self._update_ui_state(ServiceState.STOPPED)
        self._statusbar.showMessage("服务已停止")
        self._log("服务已停止")

    # --------------------------------------------------------
    # 打开浏览器
    # --------------------------------------------------------
    def _open_browser(self):
        webbrowser.open(BROWSER_URL)

    # --------------------------------------------------------
    # 日志输出
    # --------------------------------------------------------
    def _log(self, text: str):
        self._log_text.append(text)
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._log_text.setTextCursor(cursor)

    def _append_log(self, text: str):
        """来自 logging handler 的日志 (已在主线程)。"""
        self._log_text.append(text)
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._log_text.setTextCursor(cursor)

    # --------------------------------------------------------
    # 窗口关闭
    # --------------------------------------------------------
    def closeEvent(self, event):
        global _server
        if _server and not _server.should_exit:
            reply = QMessageBox.question(
                self, "确认退出",
                "服务仍在运行，确定要停止并退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            _server.should_exit = True
        event.accept()


# ============================================================
# 管理员权限检测
# ============================================================
def _is_admin() -> bool:
    """检查当前是否以管理员权限运行 (仅 Windows)。"""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _request_admin():
    """以管理员权限重新启动当前程序 (仅 Windows)。"""
    import ctypes
    exe = sys.executable
    # ShellExecuteW: 'runas' 会弹出 UAC 提权对话框
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe, "", "", 1
    )


# ============================================================
# 入口
# ============================================================
def main():
    # PyInstaller 打包后, 切换工作目录到 exe 所在目录
    if getattr(sys, 'frozen', False):
        os.chdir(Path(sys.executable).parent)

    # Windows: 检查管理员权限, 非管理员时询问是否提权
    if sys.platform == "win32" and not _is_admin():
        # 先创建 QApplication 才能弹 QMessageBox
        _app = QApplication(sys.argv)
        reply = QMessageBox.question(
            None, "权限请求",
            "以管理员权限运行可以自动提取微信数据库密钥。\n\n"
            "是否以管理员身份重新启动？\n"
            "（选择「否」可以继续以普通权限运行）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            _request_admin()
            sys.exit(0)
        # 用户选「否」, 继续以普通权限运行

    app = QApplication(sys.argv)
    app.setApplicationName("Weix")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
