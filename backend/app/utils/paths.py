"""路径工具模块（兼容 PyInstaller 打包）"""
import sys
from pathlib import Path


def get_base_dir() -> Path:
    """获取应用基础目录

    - PyInstaller 打包后：返回 exe 所在目录
    - 开发环境：返回项目根目录 (backend 的父目录)
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).resolve().parent.parent.parent


def get_data_dir() -> Path:
    """获取数据目录"""
    return get_base_dir() / "data"


def get_config_dir() -> Path:
    """获取配置目录"""
    return get_base_dir() / "config"
