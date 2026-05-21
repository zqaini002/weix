#!/usr/bin/env python3
"""Capture a screen image, optionally cropped by logical screen coordinates."""

from __future__ import annotations

import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == script_dir:
    sys.path.pop(0)

import pyautogui


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _cg_image_to_pil(cg_image):
    """将 Quartz CGImage 转为 PIL Image。"""
    import Quartz
    from PIL import Image

    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(cg_image)
    data_provider = Quartz.CGImageGetDataProvider(cg_image)
    data = Quartz.CGDataProviderCopyData(data_provider)

    return Image.frombuffer(
        "RGBA",
        (width, height),
        bytes(data),
        "raw",
        "BGRA",
        bytes_per_row,
        1,
    ).copy()


def _capture_screen_with_quartz():
    """使用 Quartz 直接截屏，避开 pyautogui 在 macOS 上的 screencapture 子进程。"""
    import Quartz

    cg_image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectInfinite,
        Quartz.kCGWindowListOptionOnScreenOnly,
        Quartz.kCGNullWindowID,
        Quartz.kCGWindowImageDefault,
    )
    if cg_image is None:
        raise RuntimeError("quartz_capture_returned_none")

    return _cg_image_to_pil(cg_image)


def _owner_aliases(owner_name: str) -> set[str]:
    aliases = {owner_name}
    if owner_name == "微信":
        aliases.add("WeChat")
    elif owner_name == "WeChat":
        aliases.add("微信")
    return aliases


def _window_match_score(
    bounds: dict,
    rect: tuple[float, float, float, float] | None,
) -> float:
    if rect is None:
        return -(float(bounds.get("Width", 0)) * float(bounds.get("Height", 0)))
    x, y, width, height = rect
    return (
        abs(float(bounds.get("X", 0)) - x)
        + abs(float(bounds.get("Y", 0)) - y)
        + abs(float(bounds.get("Width", 0)) - width)
        + abs(float(bounds.get("Height", 0)) - height)
    )


def _capture_window_with_quartz(
    owner_name: str,
    rect: tuple[float, float, float, float] | None,
):
    """按窗口 owner 捕获窗口本体，避免全屏裁剪截到遮挡窗口。"""
    import Quartz

    owner_names = _owner_aliases(owner_name)
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly,
        Quartz.kCGNullWindowID,
    ) or []
    candidates = []
    for window_info in windows:
        if window_info.get("kCGWindowOwnerName") not in owner_names:
            continue
        if window_info.get("kCGWindowLayer") != 0:
            continue
        bounds = window_info.get("kCGWindowBounds") or {}
        window_id = window_info.get("kCGWindowNumber")
        if not window_id or not bounds:
            continue
        candidates.append((_window_match_score(bounds, rect), window_id, bounds))

    if not candidates:
        return None

    _score, window_id, bounds = min(candidates, key=lambda item: item[0])
    image_options = getattr(Quartz, "kCGWindowImageBoundsIgnoreFraming", 0)
    image_options |= getattr(Quartz, "kCGWindowImageBestResolution", 0)
    cg_image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        image_options,
    )
    if cg_image is None:
        return None
    return _cg_image_to_pil(cg_image), bounds


def _capture_screen_image():
    try:
        return pyautogui.screenshot()
    except Exception as screenshot_exc:
        try:
            return _capture_screen_with_quartz()
        except Exception as quartz_exc:
            _fail(
                "screenshot_failed:"
                f"{type(screenshot_exc).__name__}:{screenshot_exc}; "
                f"quartz_failed:{type(quartz_exc).__name__}:{quartz_exc}"
            )


def _parse_rect(args: list[str]) -> tuple[float, float, float, float] | None:
    if not args:
        return None
    if len(args) != 4:
        _fail(
            "usage: screenshot_helper.py <output_path> "
            "[x y width height] [--window-owner owner]"
        )
    try:
        x, y, width, height = (float(value) for value in args)
    except ValueError:
        _fail("invalid_rect")
    return x, y, width, height


def _parse_args(
    argv: list[str],
) -> tuple[Path, tuple[float, float, float, float] | None, str | None]:
    if len(argv) < 2:
        _fail(
            "usage: screenshot_helper.py <output_path> "
            "[x y width height] [--window-owner owner]"
        )

    output_path = Path(argv[1])
    args = list(argv[2:])
    window_owner = None
    if "--window-owner" in args:
        owner_index = args.index("--window-owner")
        if owner_index + 1 >= len(args):
            _fail(
                "usage: screenshot_helper.py <output_path> "
                "[x y width height] [--window-owner owner]"
            )
        window_owner = args[owner_index + 1]
        del args[owner_index:owner_index + 2]

    rect = _parse_rect(args)
    return output_path, rect, window_owner


def _clamp_rect(
    rect: tuple[float, float, float, float] | None,
    screen_width: float,
    screen_height: float,
) -> tuple[float, float, float, float]:
    if rect is None:
        return 0.0, 0.0, screen_width, screen_height

    x, y, width, height = rect
    left = max(0.0, x)
    top = max(0.0, y)
    right = min(screen_width, x + width)
    bottom = min(screen_height, y + height)

    if right - left < 1.0 or bottom - top < 1.0:
        _fail("invalid_rect")
    return left, top, right - left, bottom - top


def _crop_logical_rect(
    image,
    rect: tuple[float, float, float, float],
    logical_width: float,
    logical_height: float,
):
    crop_x, crop_y, crop_width, crop_height = _clamp_rect(
        rect,
        logical_width,
        logical_height,
    )

    scale_x = image.size[0] / logical_width
    scale_y = image.size[1] / logical_height
    left = int(round(crop_x * scale_x))
    top = int(round(crop_y * scale_y))
    right = int(round((crop_x + crop_width) * scale_x))
    bottom = int(round((crop_y + crop_height) * scale_y))
    return image.crop((left, top, right, bottom)), (
        crop_x,
        crop_y,
        crop_width,
        crop_height,
    )


def main(argv: list[str]) -> int:
    output_path, rect, window_owner = _parse_args(argv)

    if window_owner:
        try:
            window_capture = _capture_window_with_quartz(window_owner, rect)
        except Exception:
            window_capture = None
        if window_capture is not None:
            window_bounds = None
            if isinstance(window_capture, tuple):
                window_image, window_bounds = window_capture
            else:
                window_image = window_capture

            output_image = window_image
            output_rect = None
            if rect is not None and window_bounds is not None:
                relative_rect = (
                    rect[0] - float(window_bounds.get("X", 0)),
                    rect[1] - float(window_bounds.get("Y", 0)),
                    rect[2],
                    rect[3],
                )
                output_image, output_rect = _crop_logical_rect(
                    window_image,
                    relative_rect,
                    float(window_bounds.get("Width", window_image.size[0])),
                    float(window_bounds.get("Height", window_image.size[1])),
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_image.save(output_path)
            if output_rect is not None:
                print(f"{rect[0]},{rect[1]},{rect[2]},{rect[3]}")
            elif rect is None:
                print(f"0.0,0.0,{window_image.size[0]},{window_image.size[1]}")
            else:
                print(f"{rect[0]},{rect[1]},{rect[2]},{rect[3]}")
            return 0

    image = _capture_screen_image()
    try:
        logical_size = pyautogui.size()
    except Exception as exc:
        _fail(f"screen_size_failed:{type(exc).__name__}:{exc}")
    logical_width = float(logical_size.width)
    logical_height = float(logical_size.height)
    cropped, (crop_x, crop_y, crop_width, crop_height) = _crop_logical_rect(
        image,
        rect,
        logical_width,
        logical_height,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(output_path)
    print(f"{crop_x},{crop_y},{crop_width},{crop_height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
