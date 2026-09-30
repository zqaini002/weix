"""Windows WeChat data directory discovery helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import psutil

logger = logging.getLogger(__name__)

WECHAT_PROCESS_NAMES = {"wechat.exe", "weixin.exe", "wechatapp.exe"}


@dataclass(frozen=True)
class WeChatDataDir:
    """A discovered WeChat data root and the reason it was considered."""

    path: str
    source: str


def iter_wechat_processes() -> Iterable[psutil.Process]:
    """Yield running Windows WeChat processes known by current client names."""
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        pname = (proc.info.get("name") or "").lower()
        if pname in WECHAT_PROCESS_NAMES:
            yield proc


def get_wechat_exe_path() -> Optional[str]:
    """Return the executable path for a running WeChat process."""
    try:
        for proc in iter_wechat_processes():
            exe_path = proc.info.get("exe")
            if exe_path and os.path.exists(exe_path):
                return exe_path
    except Exception as exc:
        logger.debug(f"获取微信进程路径失败: {exc}")
    return None


def find_wechat_data_dirs() -> list[WeChatDataDir]:
    """Return existing WeChat data roots ordered by confidence."""
    found: list[WeChatDataDir] = []
    seen: set[str] = set()
    for item in iter_wechat_data_dir_candidates():
        norm = os.path.normcase(os.path.normpath(item.path))
        if norm in seen:
            continue
        seen.add(norm)
        if _looks_like_wechat_data_root(item.path):
            found.append(item)
    return found


def iter_wechat_data_dir_candidates() -> Iterable[WeChatDataDir]:
    """Yield likely WeChat data roots from registry, process path, env, and drives."""
    for env_name in ("WEIX_WECHAT_DATA_DIR", "WEIX_WECHAT_FILES_DIR", "WECHAT_DATA_DIR"):
        env_path = os.getenv(env_name, "")
        if env_path:
            yield from _expand_candidate_roots(env_path, f"env {env_name}")

    for path, source in _read_wechat_data_paths_from_registry():
        yield from _expand_candidate_roots(path, source)

    reg_install = read_wechat_install_path_from_registry()
    if reg_install:
        logger.info(f"注册表微信安装路径: {reg_install}")
        yield from _roots_near_install_path(reg_install, "registry install")

    proc_path = get_wechat_exe_path()
    if proc_path:
        logger.info(f"微信进程路径: {proc_path}")
        yield from _roots_near_install_path(proc_path, "process exe")

    userprofile = os.getenv("USERPROFILE", "")
    documents = _get_documents_dir()
    appdata = os.getenv("APPDATA", "")
    localappdata = os.getenv("LOCALAPPDATA", "")

    common = [
        (os.path.join(documents, "WeChat Files") if documents else "", "Documents"),
        (os.path.join(documents, "xwechat_files") if documents else "", "Documents"),
        (
            os.path.join(userprofile, "Documents", "WeChat Files")
            if userprofile else "",
            "USERPROFILE Documents",
        ),
        (
            os.path.join(userprofile, "Documents", "xwechat_files")
            if userprofile else "",
            "USERPROFILE Documents",
        ),
        (os.path.join(appdata, "Tencent", "WeChat") if appdata else "", "APPDATA"),
        (
            os.path.join(appdata, "Tencent", "WeChat", "WeChat Files")
            if appdata else "",
            "APPDATA",
        ),
        (os.path.join(appdata, "Tencent", "WeChat", "xwechat_files") if appdata else "", "APPDATA"),
        (os.path.join(localappdata, "Tencent", "WeChat") if localappdata else "", "LOCALAPPDATA"),
        (
            os.path.join(localappdata, "Tencent", "WeChat", "WeChat Files")
            if localappdata else "",
            "LOCALAPPDATA",
        ),
        (
            os.path.join(localappdata, "Tencent", "WeChat", "xwechat_files")
            if localappdata else "",
            "LOCALAPPDATA",
        ),
        (r"D:\WeChat Files", "common drive"),
        (r"E:\WeChat Files", "common drive"),
        (r"D:\xwechat_files", "common drive"),
        (r"E:\xwechat_files", "common drive"),
        (r"D:\Tencent\WeChat", "common drive"),
        (r"E:\Tencent\WeChat", "common drive"),
    ]
    for path, source in common:
        if path:
            yield WeChatDataDir(os.path.normpath(path), source)

    for drive in get_available_drives():
        drive_candidates = [
            os.path.join(drive, "WeChat Files"),
            os.path.join(drive, "xwechat_files"),
            os.path.join(drive, "Tencent", "WeChat"),
            os.path.join(drive, "Tencent", "WeChat", "WeChat Files"),
            os.path.join(drive, "Tencent", "WeChat", "xwechat_files"),
        ]
        for path in drive_candidates:
            yield WeChatDataDir(os.path.normpath(path), "drive scan")
        yield from _one_level_data_dirs_on_drive(drive)


def read_wechat_install_path_from_registry() -> Optional[str]:
    """Read WeChat install path from Windows registry."""
    try:
        import winreg
    except ImportError:
        logger.debug("winreg 模块不可用")
        return None

    reg_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Tencent\WeChat"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Tencent\WeChat"),
        (winreg.HKEY_CURRENT_USER, r"Software\WOW6432Node\Tencent\WeChat"),
    ]
    value_names = ("InstallPath", "InstallDir", "Path", "")

    try:
        for hkey, subkey in reg_paths:
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    for value_name in value_names:
                        try:
                            val, _ = winreg.QueryValueEx(key, value_name)
                        except FileNotFoundError:
                            continue
                        path = _normalize_registry_path(str(val))
                        if path and os.path.exists(path):
                            return path
            except FileNotFoundError:
                continue
    except Exception as exc:
        logger.debug(f"读取注册表安装路径失败: {exc}")
    return None


def get_available_drives() -> list[str]:
    """Return available local drive roots."""
    drives = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
    return drives


def _read_wechat_data_paths_from_registry() -> Iterable[tuple[str, str]]:
    try:
        import winreg
    except ImportError:
        return

    reg_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WXWork"),
    ]
    value_names = (
        "FileSavePath",
        "DataSavePath",
        "UserDataSavePath",
        "WeChatFilesPath",
        "PersonalDataSavePath",
    )

    try:
        for hkey, subkey in reg_paths:
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    for value_name in value_names:
                        try:
                            val, _ = winreg.QueryValueEx(key, value_name)
                        except FileNotFoundError:
                            continue
                        path = _normalize_registry_path(str(val))
                        if path:
                            yield path, f"registry {value_name}"
            except FileNotFoundError:
                continue
    except Exception as exc:
        logger.debug(f"读取注册表数据目录失败: {exc}")


def _expand_candidate_roots(path: str, source: str) -> Iterable[WeChatDataDir]:
    path = os.path.normpath(os.path.expandvars(path.strip().strip('"')))
    if not path:
        return

    yield WeChatDataDir(path, source)

    name = os.path.basename(path).lower()
    if name not in {"wechat files", "xwechat_files"}:
        yield WeChatDataDir(os.path.join(path, "WeChat Files"), source)
        yield WeChatDataDir(os.path.join(path, "xwechat_files"), source)


def _roots_near_install_path(path: str, source: str) -> Iterable[WeChatDataDir]:
    current = Path(path)
    bases = []
    if current.suffix.lower() == ".exe":
        bases.extend([current.parent, current.parent.parent])
    else:
        bases.extend([current, current.parent])

    for base in bases:
        yield WeChatDataDir(str(base / "WeChat Files"), source)
        yield WeChatDataDir(str(base / "xwechat_files"), source)


def _one_level_data_dirs_on_drive(drive: str) -> Iterable[WeChatDataDir]:
    """Find custom data roots like D:\\wxjilu\\xwechat_files without deep scans."""
    try:
        with os.scandir(drive) as entries:
            for entry in entries:
                if not entry.is_dir():
                    continue
                for dirname in ("xwechat_files", "WeChat Files"):
                    candidate = os.path.join(entry.path, dirname)
                    yield WeChatDataDir(candidate, "drive shallow scan")
    except OSError as exc:
        logger.debug(f"扫描磁盘根目录失败 ({drive}): {exc}")


def _get_documents_dir() -> str:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "Personal")
            return os.path.normpath(os.path.expandvars(str(val)))
    except Exception:
        userprofile = os.getenv("USERPROFILE", "")
        return os.path.join(userprofile, "Documents") if userprofile else ""


def _looks_like_wechat_data_root(path: str) -> bool:
    if not os.path.isdir(path):
        return False

    try:
        root_name = os.path.basename(os.path.normpath(path)).lower()
        if root_name in {"wechat files", "xwechat_files"}:
            return True

        for entry in os.scandir(path):
            if not entry.is_dir():
                continue
            if entry.name.startswith("wxid_"):
                return True
            if entry.name == "All Users":
                return True
            if os.path.isdir(os.path.join(entry.path, "Msg")):
                return True
            if os.path.isdir(os.path.join(entry.path, "db_storage")):
                return True
    except OSError:
        return False

    return False


def _normalize_registry_path(value: str) -> str:
    value = value.strip().strip('"')
    if not value:
        return ""
    return os.path.normpath(os.path.expandvars(value))
