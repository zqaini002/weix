"""路径工具模块（兼容 PyInstaller 打包）"""
import sys
import shutil
from pathlib import Path


def get_base_dir() -> Path:
    """获取应用基础目录

    - PyInstaller 打包后：返回 exe 所在目录（可写数据目录的根）
    - 开发环境：返回项目根目录 (backend 的父目录)
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent.parent


def get_bundle_dir() -> Path:
    """获取只读资源目录。

    PyInstaller 6 的 onedir 产物会把 --add-data 资源放在 _internal
    目录中，运行时通过 sys._MEIPASS 暴露。开发环境下它等同于项目根。
    """
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return get_base_dir()


def get_resource_dir(name: str) -> Path:
    """获取打包资源目录，优先返回 PyInstaller 解包/内部资源。"""
    bundled = get_bundle_dir() / name
    if bundled.exists():
        return bundled
    return get_base_dir() / name


def _copy_missing_tree(source: Path, target: Path) -> None:
    """复制 source 中 target 缺失的文件，不覆盖用户已修改内容。"""
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def _get_writable_dir(name: str) -> Path:
    """获取可写目录，打包后首次运行会从内置资源复制一份。"""
    target = get_base_dir() / name
    if not getattr(sys, 'frozen', False):
        target.mkdir(parents=True, exist_ok=True)
        return target

    source = get_resource_dir(name)
    if target.exists():
        if source.exists() and source != target:
            try:
                _copy_missing_tree(source, target)
            except Exception:
                pass
        return target

    try:
        if source.exists() and source != target:
            shutil.copytree(source, target)
        else:
            target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception:
        if source.exists():
            return source
        raise


def get_data_dir() -> Path:
    """获取数据目录"""
    return _get_writable_dir("data")


def get_config_dir() -> Path:
    """获取配置目录"""
    return _get_writable_dir("config")


def get_frontend_dir() -> Path:
    """获取前端静态资源目录。"""
    return get_resource_dir("frontend_dist")
