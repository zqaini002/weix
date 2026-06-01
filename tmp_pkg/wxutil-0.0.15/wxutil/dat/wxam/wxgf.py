import ctypes
import sys
from ctypes import c_int64, c_int, byref, create_string_buffer, POINTER
from pathlib import Path
from typing import Literal

if getattr(sys, 'frozen', False):
    dll_path = Path(sys._MEIPASS) / "wxutil" / "dat" / "wxam" / "VoipEngine.dll"
else:
    dll_path = Path(__file__).parent / "VoipEngine.dll"
voip_engine = ctypes.WinDLL(str(dll_path))
wxam_dec_wxam2pic_5 = voip_engine.wxam_dec_wxam2pic_5
wxam_dec_wxam2pic_5.argtypes = [
    c_int64,
    c_int,
    c_int64,
    POINTER(c_int),
    c_int64,
]
wxam_dec_wxam2pic_5.restype = c_int64


class WxAMConfig(ctypes.Structure):
    """WXAM解码配置结构体 (32字节)"""

    _fields_ = [
        ("mode", c_int),
        ("reserved", c_int),
    ]


def wxam_to_image(data: bytes, format: Literal["jpeg", "gif"] = "jpeg") -> bytes:
    """
    将WXAM文件转换为图片
    """
    assert format in ["jpeg", "gif"]
    # 设置解码配置
    config = WxAMConfig()
    config.mode = {"jpeg": 0, "gif": 3}[format]

    # 准备输入缓冲区
    input_buffer = create_string_buffer(data, len(data))

    # 准备输出缓冲区 - 给足够大的空间
    max_output_size = 52 * 1024 * 1024
    output_buffer = create_string_buffer(max_output_size)
    output_size = c_int(max_output_size)

    # 调用解码函数
    result = wxam_dec_wxam2pic_5(
        ctypes.addressof(input_buffer),  # WXAM数据
        len(data),  # 数据大小
        ctypes.addressof(output_buffer),  # 输出缓冲区
        byref(output_size),  # 输出大小
        ctypes.addressof(config),  # 配置
    )

    # 检查结果
    if result == 0:
        actual_size = output_size.value
        return output_buffer.raw[:actual_size]
    else:
        return b""
