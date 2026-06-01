from __future__ import annotations
import os.path
import winreg
import hashlib
import hmac
from typing import Dict, List, Optional, Any
from wdecipher.wx_db import CORE_DB_TYPES, get_wx_dbs
from wdecipher.utils import read_file, win_env_checker
from wdecipher.utils.win32_process import *

__all__ = [
    "get_wx_pids",
    "get_wxid",
    "get_wx_dir",
    "verify_db_key",
    "get_db_key",
    "get_wx_dbs",
    "get_wx_info",
    "get_wx_infos"
]

@win_env_checker()
def get_wx_pids() -> List[int]:
    """Returns all currently running WeChat process ids.

    :return: List of WeChat process id.
    """
    return [pid for pid, name in get_current_processes() if name == "WeChat.exe"]

@win_env_checker()
def get_wxid(process) -> Optional[str]:
    """Retrieves the wxid of the specified WeChat process login.

    :param process: Specifies the WeChat process to be retrieved.
    :return: A wxid string if successful, None otherwise.
    """
    ids = []
    for addr in search_memory(process, pattern=br"\\Msg\\FTSContact"):
        wxid = read_memory_string(process, addr - 30, size=80, encoding=None)
        wxid = wxid.split(b"\\Msg")[0].split(b"\\")[-1]
        wxid = wxid.decode("utf-8", errors="ignore")
        ids.append(wxid)
    return max(ids, key=ids.count) if ids else None

@win_env_checker()
def get_wx_dir_by_regedit(wxid: str) -> Optional[str]:
    """Find the working directory of the specified WeChat id in the Windows registry.

    :param wxid: Specifies the working directory in which to look for wxid.
    :return: A working directory if successful, None otherwise.
    """
    # noinspection PyBroadException
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key=r"Software\Tencent\WeChat", reserved=0, access=winreg.KEY_READ)
        wx_dir, _ = winreg.QueryValueEx(key, __name="FileSavePath")
        winreg.CloseKey(key)
    except Exception:
        wx_dir = None

    if wx_dir is None:
        # noinspection PyBroadException
        try:
            home = os.environ.get("USERPROFILE")
            path = os.sep.join((home, "AppData", "Roaming", "Tencent", "WeChat", "All Users", "config", "3ebffe94.ini"))
            wx_dir = read_file(path, encoding="utf-8")
        except Exception:
            wx_dir = None

    if wx_dir is None:
        # noinspection PyBroadException
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key=r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            wx_dir, _ = winreg.QueryValueEx(key, __name="Personal")
            winreg.CloseKey(key)
            paths = os.path.split(wx_dir)
            if "%" in paths[0]:
                wx_dir = os.environ.get(paths[0].replace("%", ""))
                wx_dir = os.path.join(wx_dir, *paths[1:])
        except Exception:
            wx_dir = os.path.join(os.environ.get("USERPROFILE"), "Documents")

    wx_dir = os.path.join(wx_dir, "WeChat Files", wxid)
    return wx_dir if os.path.exists(wx_dir) else None

@win_env_checker()
def get_wx_dir_by_memory_searching(wxid: str, process) -> Optional[str]:
    """Find the working directory of the specified WeChat id in the memory space of the
    WeChat process.

    :param wxid: Specifies the working directory in which to look for wxid.
    :param process: Specifies the process's memory space to search.
    :return: A working directory if successful, None otherwise.
    """
    dirs = []
    pattern = wxid.encode() + br"\\Msg\\FTSContact"
    for addr in search_memory(process, pattern, max_search=10):
        wx_dir = read_memory_string(process, addr - 260 + 50, size=260, encoding=None)
        wx_dir = wx_dir.split(b"\\Msg")[0].split(b"\00")[-1]
        dirs.append(wx_dir.decode("utf-8", errors="ignore"))
    return max(dirs, key=dirs.count) if dirs else None

@win_env_checker()
def get_wx_dir(wxid: str, process=None) -> Optional[str]:
    """Finds the working directory of the specified WeChat id.

    :param wxid: Specifies the working directory in which to look for wxid.
    :param process: Specifies the process's memory space to search (default: None).
    :return: A working directory if successful, None otherwise.
    """
    wx_dir = get_wx_dir_by_regedit(wxid)
    if wx_dir is None and process is not None:
        wx_dir = get_wx_dir_by_memory_searching(wxid, process)
    return wx_dir

def verify_db_key(db_key: bytes, db_path: str) -> bool:
    """Verifies that the key is valid by decrypting the specified database.

    :param db_key: Specifies the key to be Verified.
    :param db_path: Specifies the database to use for decryption.
    :return: True if the password is valid, otherwise False.
    """
    data = read_file(db_path, encoding=None, count=5000)
    salt = data[:16]
    pk = hashlib.pbkdf2_hmac(hash_name="sha1", password=db_key, salt=salt, iterations=64000, dklen=32)
    first_page = data[16:4096]
    mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])
    pk = hashlib.pbkdf2_hmac(hash_name="sha1", password=pk, salt=mac_salt, iterations=2, dklen=32)
    hash_mac = hmac.new(pk, first_page[:-32], hashlib.sha1)
    hash_mac.update(b"\x01\x00\x00\x00")
    return hash_mac.digest() == first_page[-32:-12]

@win_env_checker()
def get_db_key_by_memory_searching(process, wx_dir: str, addr_len: int = 8) -> Optional[bytes]:
    """Retrieves the key to decrypt the database in the memory space of WeChatWin.dll.

    :param process: Specifies the WeChat process to be retrieved.
    :param wx_dir: Specify the working directory of the WeChat id corresponding to the WeChat process.
    :param addr_len: Specifies the addressing length of the memory address (default: 8bits).
    :return: A key string if successful, None otherwise.
    """
    begin_addr, end_addr = 0x7FFFFFFFFFFFFFFF, 0
    for module in get_memory_maps(process):
        if module.FileName and "WeChatWin.dll" in module.FileName:
            b, e = module.BaseAddress, module.BaseAddress + module.RegionSize
            begin_addr = b if b < begin_addr else begin_addr
            end_addr = e if e > end_addr else begin_addr

    addrs = []
    addresses = search_memory(process, "iphone\x00".encode(), max_search=2, begin_addr=begin_addr, end_addr=end_addr)
    if len(addresses) >= 2: addrs += addresses
    addresses = search_memory(process, "android\x00".encode(), max_search=2, begin_addr=begin_addr, end_addr=end_addr)
    if len(addresses) >= 2: addrs += addresses
    addresses = search_memory(process, "ipad\x00".encode(), max_search=2, begin_addr=begin_addr, end_addr=end_addr)
    if len(addresses) >= 2: addrs += addresses
    if len(addrs) == 0: return None

    addrs.sort()
    db_path = get_wx_dbs(wx_dir, db_types=CORE_DB_TYPES[2])[0]["path"]
    for addr in addrs[::-1]:
        for j in range(addr, addr - 2000, -addr_len):
            key = read_memory_string(process, j, size=32, indirect=True, addr_len=addr_len, encoding=None)
            if key is None: continue
            if verify_db_key(key, db_path):
                return key
    return None

@win_env_checker()
def get_db_key_by_offset(process, wx_dir: str, offset_map: Dict[str, int]) -> Optional[bytes]:
    """Retrieves the database decryption key by memory offset.

    :param process: Specifies the WeChat process to be retrieved.
    :param wx_dir: Specify the working directory of the WeChat id corresponding to the WeChat process.
    :param offset_map: Specifies WeChat version and offset mapping table.
    :return: A key string if successful, None otherwise.
    """
    path = get_executable_path(process)
    version = get_file_version(path)
    offset = offset_map.get(version, None)
    assert offset, f"The WeChat version {version} is not supported"

    base_addr = 0
    for module in get_memory_maps(process):
        if module.FileName and 'WeChatWin.dll' in module.FileName:
            base_addr = module.BaseAddress
            break

    db_key_addr = base_addr + offset
    addr_len = get_executable_bits(path) // 8
    db_key = read_memory_string(process, db_key_addr, size=32, indirect=True, addr_len=addr_len, encoding=None)

    db_path = get_wx_dbs(wx_dir, db_types=CORE_DB_TYPES[2])[0]["path"]
    if db_key and verify_db_key(db_key, db_path):
        return db_key
    return None

@win_env_checker()
def get_db_key(process, wx_dir: str, **kwargs: Any) -> Optional[bytes]:
    """Retrieves the database decryption key.

    :param process: Specifies the WeChat process to be retrieved.
    :param wx_dir: Specify the working directory of the WeChat id corresponding to the WeChat process.
    :param kwargs: Specify additional parameter configuration for retrieving.
    :return: A key string if successful, None otherwise.
    """
    db_key = None
    if offset_map := kwargs.pop("offset_map", None) is not None:
        db_key = get_db_key_by_offset(process, wx_dir, offset_map)
    return db_key if db_key else get_db_key_by_memory_searching(process, wx_dir, kwargs.pop("addr_len", 8))

@win_env_checker()
def get_wx_info(pid: int) -> Dict[str, str]:
    """Returns the account information of the current online WeChat process (including primarily
    process id, WeChat id, WeChat working directory, and database decryption key).

    :param pid: A running WeChat process id.
    :return: List of WeChat account information.
    """
    try:
        data = {"pid": pid}
        process = open_process(0x0400 | 0x0010, False, pid)
        path = get_executable_path(process)
        version = get_file_version(path)
        data["version"] = version

        addr_len = get_executable_bits(path) // 8
        data["wxid"] = (wxid := get_wxid(process))
        data["wx_dir"] = (wx_dir := get_wx_dir(wxid, process))
        data["db_key"] = get_db_key(process, wx_dir, addr_len=addr_len).hex()
        close_handle(process)
        return data
    except Exception as e:
        raise e

@win_env_checker()
def get_wx_infos() -> List[Dict[str, str]]:
    """Returns all currently running WeChat account information.

    :return: List of WeChat account information.
    """
    return [get_wx_info(pid) for pid in get_wx_pids()]
