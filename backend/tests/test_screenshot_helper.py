"""测试 macOS 截图辅助脚本的错误输出。"""

import sys
import os
import importlib
import subprocess
import types

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fake_pyautogui = types.SimpleNamespace(
    screenshot=lambda: None,
    size=lambda: types.SimpleNamespace(width=1, height=1),
)
sys.modules.setdefault("pyautogui", fake_pyautogui)
screenshot_helper = importlib.import_module("app.core.screenshot_helper")


def test_screenshot_helper_reports_concise_capture_errors(monkeypatch, tmp_path, capsys):
    """截图失败时应输出短错误，避免 AppleScript 日志只看到被截断的 traceback。"""
    def raise_capture_error():
        raise RuntimeError("screen recording permission denied")

    monkeypatch.setattr(screenshot_helper.pyautogui, "screenshot", raise_capture_error)

    with pytest.raises(SystemExit) as exc_info:
        screenshot_helper.main([__file__, str(tmp_path / "shot.png")])

    assert exc_info.value.code == 1
    assert capsys.readouterr().err.strip().startswith(
        "screenshot_failed:RuntimeError:screen recording permission denied; quartz_failed:"
    )


def test_screenshot_helper_falls_back_to_quartz_when_pyautogui_capture_fails(
    monkeypatch,
    tmp_path,
):
    """pyautogui 底层 screencapture 失败时，应使用 Quartz 截图兜底。"""
    def raise_screencapture_error():
        raise subprocess.CalledProcessError(1, ["screencapture", "-x", "tmp.png"])

    quartz_image = Image.new("RGBA", (20, 10), (255, 0, 0, 255))

    monkeypatch.setattr(screenshot_helper.pyautogui, "screenshot", raise_screencapture_error)
    monkeypatch.setattr(
        screenshot_helper.pyautogui,
        "size",
        lambda: types.SimpleNamespace(width=20, height=10),
    )
    monkeypatch.setattr(
        screenshot_helper,
        "_capture_screen_with_quartz",
        lambda: quartz_image,
    )

    output_path = tmp_path / "shot.png"
    result = screenshot_helper.main([__file__, str(output_path), "0", "0", "10", "5"])

    assert result == 0
    assert output_path.exists()
    assert Image.open(output_path).size == (10, 5)


def test_screenshot_helper_prefers_owner_window_capture(monkeypatch, tmp_path):
    """传入窗口 owner 时应截取目标窗口本体，避免被前景窗口遮挡。"""
    screen_image = Image.new("RGBA", (20, 10), (255, 0, 0, 255))
    window_image = Image.new("RGBA", (10, 5), (0, 255, 0, 255))

    monkeypatch.setattr(screenshot_helper.pyautogui, "screenshot", lambda: screen_image)
    monkeypatch.setattr(
        screenshot_helper.pyautogui,
        "size",
        lambda: types.SimpleNamespace(width=20, height=10),
    )
    monkeypatch.setattr(
        screenshot_helper,
        "_capture_window_with_quartz",
        lambda owner_name, rect: window_image,
        raising=False,
    )

    output_path = tmp_path / "shot.png"
    result = screenshot_helper.main(
        [
            __file__,
            str(output_path),
            "0",
            "0",
            "10",
            "5",
            "--window-owner",
            "微信",
        ]
    )

    assert result == 0
    assert output_path.exists()
    assert Image.open(output_path).getpixel((0, 0)) == (0, 255, 0, 255)


def test_window_owner_capture_crops_requested_absolute_rect(monkeypatch, tmp_path):
    """窗口本体捕获后仍应按绝对坐标裁剪，供标题栏 OCR 使用。"""
    window_image = Image.new("RGBA", (100, 50), (255, 0, 0, 255))
    for x in range(10, 30):
        for y in range(5, 15):
            window_image.putpixel((x, y), (0, 255, 0, 255))

    monkeypatch.setattr(
        screenshot_helper,
        "_capture_window_with_quartz",
        lambda owner_name, rect: (
            window_image,
            {"X": 100, "Y": 50, "Width": 100, "Height": 50},
        ),
        raising=False,
    )

    output_path = tmp_path / "title.png"
    result = screenshot_helper.main(
        [
            __file__,
            str(output_path),
            "110",
            "55",
            "20",
            "10",
            "--window-owner",
            "微信",
        ]
    )

    assert result == 0
    cropped = Image.open(output_path)
    assert cropped.size == (20, 10)
    assert cropped.getpixel((0, 0)) == (0, 255, 0, 255)
