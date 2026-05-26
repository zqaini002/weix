"""
Weix GUI Launcher -- PyQt6 图形化启动器
功能: 启动/停止 uvicorn 后端服务，实时日志，状态指示，自动打开浏览器
"""
import sys
import os
import subprocess
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
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
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
# 日志读取线程
# ============================================================
class LogReaderThread(QThread):
    """从子进程 stdout 实时读取日志。"""
    log_received = pyqtSignal(str)
    process_exited = pyqtSignal(int)

    def __init__(self, process: subprocess.Popen):
        super().__init__()
        self._process = process
        self._running = True

    def run(self):
        try:
            for line in iter(self._process.stdout.readline, ''):
                if not self._running:
                    break
                if line:
                    self.log_received.emit(line.rstrip('\n\r'))
        except Exception:
            pass
        finally:
            if self._process:
                retcode = self._process.wait()
                self.process_exited.emit(retcode)

    def stop(self):
        self._running = False


# ============================================================
# 健康检查线程
# ============================================================
class HealthCheckThread(QThread):
    """轮询 /api/health 检测服务是否就绪。"""
    health_ok = pyqtSignal()
    health_failed = pyqtSignal(str)

    def __init__(self, url: str, interval_ms: int):
        super().__init__()
        self._url = url
        self._interval = interval_ms / 1000.0
        self._running = True

    def run(self):
        while self._running:
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
# 主窗口
# ============================================================
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._process: subprocess.Popen | None = None
        self._log_thread: LogReaderThread | None = None
        self._health_thread: HealthCheckThread | None = None
        self._state = ServiceState.STOPPED
        self._startup_timer_count = 0

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
            ServiceState.STOPPED:  ("已停止",   "#888888", True,  False, False),
            ServiceState.STARTING: ("启动中...", "#f0ad4e", False, True,  False),
            ServiceState.RUNNING:  ("运行中",   "#5cb85c", False, True,  True),
            ServiceState.ERROR:    ("错误",     "#d9534f", True,  False, False),
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
    # 启动服务
    # --------------------------------------------------------
    def _on_start(self):
        self._log_text.clear()
        self._startup_timer_count = 0
        self._log("正在启动 Weix 服务...")
        self._update_ui_state(ServiceState.STARTING)

        if getattr(sys, 'frozen', False):
            # 打包后: 用 launcher 同目录的 python 子进程启动 uvicorn
            exe_dir = Path(sys.executable).parent
            cmd = [
                sys.executable, "-m", "uvicorn",
                "app.main:app",
                "--host", SERVER_HOST,
                "--port", str(SERVER_PORT),
            ]
            cwd = str(exe_dir)
        else:
            # 开发环境
            cmd = [
                sys.executable, "-m", "uvicorn",
                "app.main:app",
                "--host", SERVER_HOST,
                "--port", str(SERVER_PORT),
            ]
            cwd = str(Path(__file__).parent)

        self._log(f"工作目录: {cwd}")
        self._log(f"启动命令: {' '.join(cmd)}")

        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                cwd=cwd,
                creationflags=creation_flags,
            )

            self._log_thread = LogReaderThread(self._process)
            self._log_thread.log_received.connect(self._log)
            self._log_thread.process_exited.connect(self._on_process_exited)
            self._log_thread.start()

            self._health_thread = HealthCheckThread(HEALTH_URL, HEALTH_CHECK_INTERVAL_MS)
            self._health_thread.health_ok.connect(self._on_health_ok)
            self._health_thread.health_failed.connect(self._on_health_failed)
            self._health_thread.start()

            self._statusbar.showMessage("正在等待服务就绪...")

        except Exception as e:
            self._log(f"启动异常: {e}")
            self._update_ui_state(ServiceState.ERROR)

    def _on_health_ok(self):
        self._update_ui_state(ServiceState.RUNNING)
        self._statusbar.showMessage(f"服务运行中 -- {BROWSER_URL}")
        self._log("=== 服务已就绪 ===")
        QTimer.singleShot(500, self._open_browser)

    def _on_health_failed(self, reason: str):
        if self._state == ServiceState.STARTING:
            self._startup_timer_count += 1
            if self._startup_timer_count % 5 == 0:
                self._statusbar.showMessage(f"等待服务就绪... ({reason})")

    # --------------------------------------------------------
    # 停止服务
    # --------------------------------------------------------
    def _on_stop(self):
        if self._process is None:
            return

        self._update_ui_state(ServiceState.STOPPING)
        self._statusbar.showMessage("正在停止服务...")
        self._log("正在停止服务...")

        if self._health_thread:
            self._health_thread.stop()

        try:
            self._process.terminate()
        except Exception:
            pass

        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._log("进程未响应，强制终止")
            self._process.kill()
            self._process.wait()

    def _on_process_exited(self, retcode: int):
        if self._log_thread:
            self._log_thread.stop()

        if self._state == ServiceState.STOPPING:
            self._log(f"服务已停止 (退出码: {retcode})")
            self._update_ui_state(ServiceState.STOPPED)
            self._statusbar.showMessage("服务已停止")
        elif retcode != 0:
            self._log(f"服务异常退出 (退出码: {retcode})")
            self._update_ui_state(ServiceState.ERROR)
            self._statusbar.showMessage(f"服务异常退出 (退出码: {retcode})")
        else:
            self._update_ui_state(ServiceState.STOPPED)

        self._process = None

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

    # --------------------------------------------------------
    # 窗口关闭
    # --------------------------------------------------------
    def closeEvent(self, event):
        if self._process and self._process.poll() is None:
            reply = QMessageBox.question(
                self, "确认退出",
                "服务仍在运行，确定要停止并退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            self._on_stop()
        event.accept()


# ============================================================
# 入口
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Weix")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
