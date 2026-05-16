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


def _parse_rect(args: list[str]) -> tuple[float, float, float, float] | None:
    if not args:
        return None
    if len(args) != 4:
        _fail("usage: screenshot_helper.py <output_path> [x y width height]")
    try:
        x, y, width, height = (float(value) for value in args)
    except ValueError:
        _fail("invalid_rect")
    return x, y, width, height


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


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 6):
        _fail("usage: screenshot_helper.py <output_path> [x y width height]")

    output_path = Path(argv[1])
    rect = _parse_rect(argv[2:])

    image = pyautogui.screenshot()
    logical_size = pyautogui.size()
    logical_width = float(logical_size.width)
    logical_height = float(logical_size.height)
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.crop((left, top, right, bottom)).save(output_path)
    print(f"{crop_x},{crop_y},{crop_width},{crop_height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
