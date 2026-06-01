import binascii
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import threading
import winreg
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import psutil
import pymem
import blackboxprotobuf
import lz4.block
import zstandard
import xmltodict

from Crypto.Cipher import AES
from Crypto.Util import Padding

ReadProcessMemory = ctypes.windll.kernel32.ReadProcessMemory
void_p = ctypes.c_void_p

def _get_base_dir():
    """兼容 PyInstaller 打包后的路径"""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "wxutil")
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = _get_base_dir()
wechat_dump_rs = os.path.join(BASE_DIR, "tools", "wechat-dump-rs.exe")
db_key_hook_cmd = os.path.join(BASE_DIR, "tools", "DbkeyHookCMD.exe")


def get_wechat_install_path(version: int = 3) -> str:
    if version == 3:
        reg_path = r"Software\Tencent\WeChat"
    elif version == 4:
        reg_path = r"Software\Tencent\Weixin"
    else:
        raise ValueError(f"Not support WeChat version: {version}")

    for KEY in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
        try:
            key = winreg.OpenKey(KEY, reg_path)
            install_path, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            return install_path
        except FileNotFoundError:
            continue
        except Exception as e:
            raise e


def to_wechat_v3_version(value: int) -> str:
    """输入整数或十六进制字符串，返回 x.y.z.w 格式版本号"""
    a = (value >> 24) & 0xFF
    b = (value >> 16) & 0xFF
    c = (value >> 8) & 0xFF
    d = value & 0xFF
    if a >= 0x60:
        a = a - 0x60
    return f"{a}.{b}.{c}.{d}"


def to_wechat_v4_version(value: int) -> str:
    version = hex(value)
    ver_str = version[5:]
    major = int(ver_str[0], 16)
    minor = int(ver_str[1], 16)
    build = int(ver_str[2], 16)
    patch = int(ver_str[3:], 16)
    return f"{major}.{minor}.{build}.{patch}"


def get_wechat_version(version: int = 3) -> str:
    if version == 3:
        reg_path = r"Software\Tencent\WeChat"
        to_wechat_version = to_wechat_v3_version
    elif version == 4:
        reg_path = r"Software\Tencent\Weixin"
        to_wechat_version = to_wechat_v4_version
    else:
        raise ValueError(f"Not support WeChat version: {version}")

    for KEY in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
        try:
            key = winreg.OpenKey(KEY, reg_path)
            version, _ = winreg.QueryValueEx(key, "Version")
            winreg.CloseKey(key)
            return to_wechat_version(version)
        except FileNotFoundError:
            continue
        except Exception as e:
            raise e


def wechat_dump(options: Dict) -> subprocess.CompletedProcess:
    cmd_args = []
    for k, v in options.items():
        if v is not None:
            cmd_args.append(k)
            cmd_args.append(v)
    return subprocess.run([wechat_dump_rs, *cmd_args], capture_output=True)


def get_wx_info(version: str = "v3", pid: int = None) -> Dict:
    if version == "v3":
        result = wechat_dump({"-p": pid, "--vv": "3"})
    elif version == "v4":
        result = wechat_dump({"-p": pid, "--vv": "4"})
    else:
        raise ValueError(f"Not support version: {version}")

    stdout = result.stdout.decode()
    if not stdout:
        raise Exception("Please login wechat.")
    else:
        stderr = result.stderr.decode()
        if "panicked" in stderr:
            raise Exception(stderr)

        pid = int(re.findall("ProcessId: (.*?)\n", stdout)[0])
        version = re.findall("WechatVersion: (.*?)\n", stdout)[0]
        account = re.findall("AccountName: (.*?)\n", stdout)[0]
        data_dir = re.findall("DataDir: (.*?)\n", stdout)[0]
        key = re.findall("key: (.*?)\n", stdout)[0]
        return {
            "pid": pid,
            "version": version,
            "account": account,
            "data_dir": data_dir,
            "key": key,
        }


def get_exe_bit(file_path: str) -> int:
    with open(file_path, "rb") as f:
        if f.read(2) != b"MZ":
            return 64

        f.seek(60)
        pe_offset = int.from_bytes(f.read(4), "little")
        f.seek(pe_offset + 4)
        machine = int.from_bytes(f.read(2), "little")
        return 32 if machine == 0x14C else 64 if machine == 0x8664 else 64


def pattern_scan_all(
    handle: int, pattern: bytes, *, return_multiple: bool = False, find_num: int = 100
) -> Union[int, List[int]]:
    next_region = 0
    found = []
    user_space_limit = 0x7FFFFFFF0000 if sys.maxsize > 2**32 else 0x7FFF0000
    while next_region < user_space_limit:
        try:
            next_region, page_found = pymem.pattern.scan_pattern_page(
                handle, next_region, pattern, return_multiple=return_multiple
            )
        except Exception:
            break

        if not return_multiple and page_found:
            return page_found

        if page_found:
            found += page_found

        if len(found) > find_num:
            break

    return found


def get_info_wxid(h_process: int) -> Union[str, None]:
    addrs = pattern_scan_all(
        h_process, rb"\\Msg\\FTSContact", return_multiple=True, find_num=100
    )
    wxids = []
    for addr in addrs:
        array = ctypes.create_string_buffer(80)
        if ReadProcessMemory(h_process, void_p(addr - 30), array, 80, 0) == 0:
            return None
        raw = bytes(array).split(b"\\Msg")[0].split(b"\\")[-1]
        wxids.append(raw.decode("utf-8", errors="ignore"))

    return max(wxids, key=wxids.count) if wxids else None


def get_info_file_path_base_wxid(h_process: int, wxid: str) -> Union[str, None]:
    addrs = pattern_scan_all(
        h_process,
        wxid.encode() + rb"\\Msg\\FTSContact",
        return_multiple=True,
        find_num=10,
    )
    file_paths = []
    for addr in addrs:
        buffer_len = 260
        array = ctypes.create_string_buffer(buffer_len)
        if (
            ReadProcessMemory(
                h_process, void_p(addr - buffer_len + 50), array, buffer_len, 0
            )
            == 0
        ):
            return None
        raw = bytes(array).split(b"\\Msg")[0].split(b"\00")[-1]
        file_paths.append(raw.decode("utf-8", errors="ignore"))

    return max(file_paths, key=file_paths.count) if file_paths else None


def get_info_file_path(wxid: str = "all") -> Union[str, None]:
    if not wxid:
        return None

    is_w_dir = False

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat", 0, winreg.KEY_READ
        )
        value, _ = winreg.QueryValueEx(key, "FileSavePath")
        winreg.CloseKey(key)
        w_dir = value
        is_w_dir = True
    except Exception:
        w_dir = "MyDocument:"

    if not is_w_dir:
        try:
            user_profile = os.environ.get("USERPROFILE")
            path_3ebffe94 = os.path.join(
                user_profile,
                "AppData",
                "Roaming",
                "Tencent",
                "WeChat",
                "All Users",
                "config",
                "3ebffe94.ini",
            )
            with open(path_3ebffe94, "r", encoding="utf-8") as f:
                w_dir = f.read()

        except Exception:
            w_dir = "MyDocument:"

    if w_dir == "MyDocument:":
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            documents_path = winreg.QueryValueEx(key, "Personal")[0]
            winreg.CloseKey(key)
            documents_paths = os.path.split(documents_path)
            if "%" in documents_paths[0]:
                w_dir = os.environ.get(documents_paths[0].replace("%", ""))
                w_dir = os.path.join(w_dir, os.path.join(*documents_paths[1:]))
            else:
                w_dir = documents_path
        except Exception:
            profile = os.environ.get("USERPROFILE")
            w_dir = os.path.join(profile, "Documents")

    msg_dir = os.path.join(w_dir, "WeChat Files")

    if wxid == "all" and os.path.exists(msg_dir):
        return msg_dir

    filePath = os.path.join(msg_dir, wxid)
    return filePath if os.path.exists(filePath) else None


def get_key(pid: int, db_path: str, addr_len: int) -> Union[str, None]:
    def read_key_bytes(
        h_process: int, address: int, address_len: int = 8
    ) -> Union[bytes, None]:
        array = ctypes.create_string_buffer(address_len)
        if ReadProcessMemory(h_process, void_p(address), array, address_len, 0) == 0:
            return None
        key_addr = int.from_bytes(array, "little")
        key_buf = ctypes.create_string_buffer(32)
        if ReadProcessMemory(h_process, void_p(key_addr), key_buf, 32, 0) == 0:
            return None
        return bytes(key_buf)

    def verify_key(key: bytes, wx_db_path: str) -> bool:
        KEY_SIZE = 32
        DEFAULT_PAGESIZE = 4096
        DEFAULT_ITER = 64000

        with open(wx_db_path, "rb") as file:
            blist = file.read(5000)

        salt = blist[:16]
        byte_key = hashlib.pbkdf2_hmac("sha1", key, salt, DEFAULT_ITER, KEY_SIZE)
        first_page = blist[16:DEFAULT_PAGESIZE]

        mac_salt = bytes([s ^ 58 for s in salt])
        mac_key = hashlib.pbkdf2_hmac("sha1", byte_key, mac_salt, 2, KEY_SIZE)
        hash_mac = hmac.new(mac_key, first_page[:-32], hashlib.sha1)
        hash_mac.update(b"\x01\x00\x00\x00")

        return hash_mac.digest() == first_page[-32:-12]

    micro_msg_path = os.path.join(db_path, "MSG", "MicroMsg.db")
    pm = pymem.Pymem(pid)
    module_name = "WeChatWin.dll"

    type_patterns = ["iphone\x00", "android\x00", "ipad\x00"]
    type_addrs = []

    for type_pattern in type_patterns:
        addrs = pm.pattern_scan_module(
            type_pattern.encode(), module_name, return_multiple=True
        )
        if len(addrs) >= 2:
            type_addrs.extend(addrs)

    if not type_addrs:
        return None

    for i in sorted(type_addrs, reverse=True):
        for j in range(i, i - 2000, -addr_len):
            key_bytes = read_key_bytes(pm.process_handle, j, addr_len)
            if isinstance(key_bytes, bytes) and verify_key(key_bytes, micro_msg_path):
                return key_bytes.hex()

    return None


def read_info(pid: Optional[int] = None) -> Union[List[Dict[str, str]], None]:
    process_name = "WeChat.exe"
    if pid is None:
        wechat_processes = [
            p
            for p in psutil.process_iter(["name", "exe", "pid"])
            if p.name() == process_name
        ]
    else:
        wechat_processes = [
            p
            for p in psutil.process_iter(["name", "exe", "pid"])
            if p.name() == process_name and p.pid == pid
        ]

    if not wechat_processes:
        return None

    result = []
    for process in wechat_processes:
        tmp_rd = {}
        tmp_rd["pid"] = str(process.pid)
        Handle = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, process.pid)
        addr_len = get_exe_bit(process.exe()) // 8
        wxid = get_info_wxid(Handle)
        tmp_rd["wxid"] = wxid
        file_path = get_info_file_path_base_wxid(Handle, wxid) if wxid != None else None
        if file_path == None and wxid != None:
            file_path = get_info_file_path(wxid)
        tmp_rd["file_path"] = file_path
        tmp_rd["key"] = (
            get_key(process.pid, file_path, addr_len) if file_path != None else None
        )
        result.append(tmp_rd)

    return result


def decrypt_db_file_v3(path: str, pkey: str) -> bytes:
    IV_SIZE = 16
    HMAC_SHA1_SIZE = 20
    KEY_SIZE = 32
    ROUND_COUNT = 64000
    PAGE_SIZE = 4096
    SALT_SIZE = 16
    SQLITE_HEADER = b"SQLite format 3"

    with open(path, "rb") as f:
        buf = f.read()

    # 如果开头是 SQLite Header，说明不需要解密
    if buf.startswith(SQLITE_HEADER):
        return buf

    decrypted_buf = bytearray()

    # 读取 salt
    salt = buf[:SALT_SIZE]
    mac_salt = bytes([b ^ 0x3A for b in salt])

    # 生成 key
    pass_bytes = binascii.unhexlify(pkey)
    key = hashlib.pbkdf2_hmac("sha1", pass_bytes, salt, ROUND_COUNT, dklen=KEY_SIZE)

    # 生成 mac_key
    mac_key = hashlib.pbkdf2_hmac("sha1", key, mac_salt, 2, dklen=KEY_SIZE)

    # 写入 sqlite header + 0x00
    decrypted_buf.extend(SQLITE_HEADER)
    decrypted_buf.append(0x00)

    # 计算每页保留字节长度
    reserve = IV_SIZE + HMAC_SHA1_SIZE
    if reserve % AES.block_size != 0:
        reserve = ((reserve // AES.block_size) + 1) * AES.block_size

    total_page = len(buf) // PAGE_SIZE

    for cur_page in range(total_page):
        offset = SALT_SIZE if cur_page == 0 else 0
        start = cur_page * PAGE_SIZE
        end = start + PAGE_SIZE

        if all(b == 0 for b in buf[start:end]):
            decrypted_buf.extend(buf[start:end])
            break

        # HMAC-SHA1 校验
        mac = hmac.new(mac_key, digestmod=hashlib.sha1)
        mac.update(buf[start + offset : end - reserve + IV_SIZE])
        mac.update((cur_page + 1).to_bytes(4, byteorder="little"))
        hash_mac = mac.digest()

        hash_mac_start_offset = end - reserve + IV_SIZE
        hash_mac_end_offset = hash_mac_start_offset + len(hash_mac)
        if hash_mac != buf[hash_mac_start_offset:hash_mac_end_offset]:
            raise ValueError("Hash verification failed")

        # AES-256-CBC 解密
        iv = buf[end - reserve : end - reserve + IV_SIZE]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted_page = cipher.decrypt(buf[start + offset : end - reserve])
        decrypted_buf.extend(decrypted_page)
        decrypted_buf.extend(buf[end - reserve : end])  # 保留 reserve 部分

    return bytes(decrypted_buf)


def decrypt_db_file_v4(path: str, pkey: str) -> bytes:
    IV_SIZE = 16
    HMAC_SHA256_SIZE = 64
    KEY_SIZE = 32
    AES_BLOCK_SIZE = 16
    ROUND_COUNT = 256000
    PAGE_SIZE = 4096
    SALT_SIZE = 16
    SQLITE_HEADER = b"SQLite format 3"

    with open(path, "rb") as f:
        buf = f.read()

    # 如果开头是 SQLITE_HEADER，说明不需要解密
    if buf.startswith(SQLITE_HEADER):
        return buf

    decrypted_buf = bytearray()
    salt = buf[:SALT_SIZE]
    mac_salt = bytes([b ^ 0x3A for b in salt])

    pass_bytes = bytes.fromhex(pkey)

    key = hashlib.pbkdf2_hmac("sha512", pass_bytes, salt, ROUND_COUNT, KEY_SIZE)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, KEY_SIZE)

    # 写入 SQLite 头
    decrypted_buf.extend(SQLITE_HEADER)
    decrypted_buf.append(0x00)

    reserve = IV_SIZE + HMAC_SHA256_SIZE
    if reserve % AES_BLOCK_SIZE != 0:
        reserve = ((reserve // AES_BLOCK_SIZE) + 1) * AES_BLOCK_SIZE

    total_page = len(buf) // PAGE_SIZE

    for cur_page in range(total_page):
        offset = SALT_SIZE if cur_page == 0 else 0
        start = cur_page * PAGE_SIZE
        end = start + PAGE_SIZE

        # 计算 HMAC-SHA512
        mac_data = buf[start + offset : end - reserve + IV_SIZE]
        page_num_bytes = (cur_page + 1).to_bytes(4, byteorder="little")
        mac = hmac.new(mac_key, mac_data + page_num_bytes, hashlib.sha512).digest()

        hash_mac_start_offset = end - reserve + IV_SIZE
        hash_mac_end_offset = hash_mac_start_offset + len(mac)
        if mac != buf[hash_mac_start_offset:hash_mac_end_offset]:
            raise ValueError(f"Hash verification failed on page {cur_page + 1}")

        iv = buf[end - reserve : end - reserve + IV_SIZE]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted_page = cipher.decrypt(buf[start + offset : end - reserve])

        decrypted_buf.extend(decrypted_page)
        decrypted_buf.extend(buf[end - reserve : end])

    return bytes(decrypted_buf)


def get_db_key(pkey: str, path: str, version: str) -> str:
    KEY_SIZE = 32
    ROUND_COUNT_V4 = 256000
    ROUND_COUNT_V3 = 64000
    SALT_SIZE = 16

    # 读取数据库文件的前 16 个字节作为 salt
    with open(path, "rb") as f:
        salt = f.read(SALT_SIZE)

    # 将十六进制的 pkey 解码为 bytes
    pass_bytes = binascii.unhexlify(pkey)

    # 根据版本选择哈希算法和迭代次数
    if version.startswith("3"):
        key = hashlib.pbkdf2_hmac(
            "sha1", pass_bytes, salt, ROUND_COUNT_V3, dklen=KEY_SIZE
        )
    elif version.startswith("4"):
        key = hashlib.pbkdf2_hmac(
            "sha512", pass_bytes, salt, ROUND_COUNT_V4, dklen=KEY_SIZE
        )
    else:
        raise ValueError(f"Not support version: {version}")

    return binascii.hexlify(key + salt).decode()


def parse_xml(xml: str) -> Dict[str, Any]:
    return xmltodict.parse(xml)


def deserialize_bytes_extra(bytes_extra: Optional[bytes]) -> Dict[str, Any]:
    bytes_extra_message_type = {
        "1": {
            "type": "message",
            "message_typedef": {
                "1": {"type": "int", "name": ""},
                "2": {"type": "int", "name": ""},
            },
            "name": "1",
        },
        "3": {
            "type": "message",
            "message_typedef": {
                "1": {"type": "int", "name": ""},
                "2": {"type": "str", "name": ""},
            },
            "name": "3",
            "alt_typedefs": {
                "1": {
                    "1": {"type": "int", "name": ""},
                    "2": {"type": "message", "message_typedef": {}, "name": ""},
                },
                "2": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {
                            "13": {"type": "fixed32", "name": ""},
                            "12": {"type": "fixed32", "name": ""},
                        },
                        "name": "",
                    },
                },
                "3": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {"15": {"type": "fixed64", "name": ""}},
                        "name": "",
                    },
                },
                "4": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {
                            "15": {"type": "int", "name": ""},
                            "14": {"type": "fixed32", "name": ""},
                        },
                        "name": "",
                    },
                },
                "5": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {
                            "12": {"type": "fixed32", "name": ""},
                            "7": {"type": "fixed64", "name": ""},
                            "6": {"type": "fixed64", "name": ""},
                        },
                        "name": "",
                    },
                },
                "6": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {
                            "7": {"type": "fixed64", "name": ""},
                            "6": {"type": "fixed32", "name": ""},
                        },
                        "name": "",
                    },
                },
                "7": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {"12": {"type": "fixed64", "name": ""}},
                        "name": "",
                    },
                },
                "8": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {
                            "6": {"type": "fixed64", "name": ""},
                            "12": {"type": "fixed32", "name": ""},
                        },
                        "name": "",
                    },
                },
                "9": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {
                            "15": {"type": "int", "name": ""},
                            "12": {"type": "fixed64", "name": ""},
                            "6": {"type": "int", "name": ""},
                        },
                        "name": "",
                    },
                },
                "10": {
                    "1": {"type": "int", "name": ""},
                    "2": {
                        "type": "message",
                        "message_typedef": {
                            "6": {"type": "fixed32", "name": ""},
                            "12": {"type": "fixed64", "name": ""},
                        },
                        "name": "",
                    },
                },
            },
        },
    }
    if bytes_extra is None or not isinstance(bytes_extra, bytes):
        raise TypeError("BytesExtra must be bytes")

    deserialize_data, message_type = blackboxprotobuf.decode_message(
        bytes_extra, bytes_extra_message_type
    )
    return deserialize_data


def decompress_compress_content(data: Optional[bytes]) -> str:
    if data is None or not isinstance(data, bytes):
        raise TypeError("Data must be bytes")
    try:
        dst = lz4.block.decompress(data, uncompressed_size=len(data) << 8)
        dst = dst.replace(b"\x00", b"")
        uncompressed_data = dst.decode("utf-8", errors="ignore")
        return uncompressed_data
    except Exception:
        return data.decode("utf-8", errors="ignore")


def decompress(data):
    try:
        dctx = zstandard.ZstdDecompressor()
        x = dctx.decompress(data).strip(b"\x00").strip()
        return x.decode("utf-8").strip()
    except:
        return data


def decrypt_dat_v3(input_path: str, xor_key: int) -> bytes:
    with open(input_path, "rb") as f:
        data = f.read()
    return bytes(b ^ xor_key for b in data)


def decrypt_dat_v4(input_path: str, xor_key: int, aes_key: bytes) -> bytes:
    with open(input_path, "rb") as f:
        header, data = f.read(0xF), f.read()
        signature, aes_size, xor_size = struct.unpack("<6sLLx", header)
        aes_size += AES.block_size - aes_size % AES.block_size

        aes_data = data[:aes_size]
        raw_data = data[aes_size:]

    cipher = AES.new(aes_key, AES.MODE_ECB)
    decrypted_data = Padding.unpad(cipher.decrypt(aes_data), AES.block_size)

    if xor_size > 0:
        raw_data = data[aes_size:-xor_size]
        xor_data = data[-xor_size:]
        xored_data = bytes(b ^ xor_key for b in xor_data)
    else:
        xored_data = b""

    return decrypted_data + raw_data + xored_data


def decrypt_dat(input_file: str) -> int:
    with open(input_file, "rb") as f:
        signature = f.read(6)

    if signature == b"\x07\x08V1\x08\x07":
        return 1
    elif signature == b"\x07\x08V2\x08\x07":
        return 2
    else:
        return 0


# 定义必要的常量
PROCESS_ALL_ACCESS = 0x1F0FFF
PAGE_READWRITE = 0x04
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000

# Constants
IV_SIZE = 16
HMAC_SHA256_SIZE = 64
HMAC_SHA512_SIZE = 64
KEY_SIZE = 32
AES_BLOCK_SIZE = 16
ROUND_COUNT = 256000
PAGE_SIZE = 4096
SALT_SIZE = 16

finish_flag = False


# 定义 MEMORY_BASIC_INFORMATION 结构
class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_ulong),
        ("Protect", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
    ]


# Windows API Constants
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

# Load Windows DLLs
kernel32 = ctypes.windll.kernel32


def open_process(pid: int) -> int:
    """打开目标进程"""
    return ctypes.windll.kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)


def read_process_memory(
    process_handle: int, address: int, size: int
) -> Optional[bytes]:
    """读取目标进程内存"""
    buffer = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t(0)
    success = ctypes.windll.kernel32.ReadProcessMemory(
        process_handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(bytes_read)
    )
    if not success:
        return None
    return buffer.raw


def get_memory_regions(process_handle: int) -> List[Tuple[int, int]]:
    """获取所有内存区域"""
    regions = []
    mbi = MEMORY_BASIC_INFORMATION()
    address = 0
    while ctypes.windll.kernel32.VirtualQueryEx(
        process_handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
    ):
        if mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE:
            regions.append((mbi.BaseAddress, mbi.RegionSize))
        address += mbi.RegionSize
    return regions


# 导入 Windows API 函数
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE

# ReadProcessMemory = kernel32.ReadProcessMemory
# ReadProcessMemory.argtypes = [
#     wintypes.HANDLE,
#     wintypes.LPCVOID,
#     wintypes.LPVOID,
#     ctypes.c_size_t,
#     ctypes.POINTER(ctypes.c_size_t),
# ]
# ReadProcessMemory.restype = wintypes.BOOL

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL


@lru_cache
def verify(encrypted: bytes, key: bytes) -> bool:
    aes_key = key[:16]
    cipher = AES.new(aes_key, AES.MODE_ECB)
    text = cipher.decrypt(encrypted)

    if text.startswith(b"\xff\xd8\xff"):
        return True
    else:
        return False


def search_memory_chunk(process_handle, base_address, region_size, encrypted, rules):
    """搜索单个内存块"""
    memory = read_process_memory(process_handle, base_address, region_size)
    if not memory:
        return None

    matches = rules.match(data=memory)
    if matches:
        for match in matches:
            if match.rule == "AesKey":
                for string in match.strings:
                    for instance in string.instances:
                        content = instance.matched_data[1:-1]
                        if verify(encrypted, content):
                            return content[:16]
    return None


def get_aes_key(encrypted: bytes, pid: int) -> Any:
    process_handle = open_process(pid)
    if not process_handle:
        return Exception("无法打开进程：{pid}")

    # 编译YARA规则
    rules_key = r"""
    rule AesKey {
        strings:
            $pattern = /[^a-z0-9][a-z0-9]{32}[^a-z0-9]/
        condition:
            $pattern
    }
    """
    import yara

    rules = yara.compile(source=rules_key)

    # 获取内存区域
    process_infos = get_memory_regions(process_handle)

    # 创建线程池
    found_result = threading.Event()
    result = [None]

    def process_chunk(args):
        if found_result.is_set():
            return None
        base_address, region_size = args
        res = search_memory_chunk(
            process_handle, base_address, region_size, encrypted, rules
        )
        if res:
            result[0] = res
            found_result.set()
        return res

    with ThreadPoolExecutor(max_workers=min(32, len(process_infos))) as executor:
        executor.map(process_chunk, process_infos)

    CloseHandle(process_handle)
    return result[0]


def dump_wechat_info_v4(encrypted: bytes, pid: int) -> bytes:
    process_handle = open_process(pid)
    if not process_handle:
        raise Exception(f"无法打开微信进程: {pid}")

    result = get_aes_key(encrypted, pid)
    if isinstance(result, bytes):
        return result[:16]
    else:
        raise Exception("未找到 AES 密钥")


def sort_template_files_by_date(template_files):
    """
    根据文件路径中的 YYYY-MM 部分，从大到小（降序）排序文件列表。

    Args:
        template_files (list): 包含文件路径字符串的列表，例如：
                               "{weixin_dir}/msg/attach/.../2025-06/Img/...dat"

    Returns:
        list: 按照日期从大到小排序后的文件路径列表。
    """

    def get_date_from_path(filepath):
        """
        从文件路径中提取 YYYY-MM 格式的日期字符串。
        """
        # 使用正则表达式查找形如 "YYYY-MM" 的模式
        # r'(\d{4}-\d{2})' 匹配四个数字-两个数字，并将其捕获为一个组
        match = re.search(r"(\d{4}-\d{2})", str(filepath))
        if match:
            return match.group(1)  # 返回捕获到的日期字符串
        else:
            # 如果没有找到日期模式，可以根据需要处理。
            # 例如，返回一个非常小的字符串，使其在降序排序时排在最后，
            # 或者抛出错误。这里假设所有路径都包含日期。
            # print(f"警告：路径中未找到 YYYY-MM 格式的日期: {filepath}")
            return "0000-00"  # 返回一个默认值，确保排序行为可预测

    # 使用 sorted() 函数进行排序，key 参数指定了用于比较的函数，
    # reverse=True 表示降序排序（从大到小）
    sorted_files = sorted(template_files, key=get_date_from_path, reverse=True)
    return sorted_files


def find_key(
    weixin_dir: pathlib.Path,
    version: int = 4,
    xor_key_: Optional[int] = None,
    aes_key_: Optional[bytes] = None,
):
    """
    遍历目录下文件, 找到至多 16 个 (.*)_t.dat 文件,
    收集最后两位字节, 选择出现次数最多的两个字节.
    """
    assert version in [3, 4]

    # 查找所有 _t.dat 结尾的文件
    template_files = sort_template_files_by_date(list(weixin_dir.rglob("*_t.dat")))

    if not template_files:
        raise Exception("未找到模板文件")

    # 收集所有文件最后两个字节
    last_bytes_list = []
    for file in template_files[:16]:
        try:
            with open(file, "rb") as f:
                # 读取最后两个字节
                f.seek(-2, 2)
                last_bytes = f.read(2)
                last_bytes_list.append(last_bytes)
        except Exception as e:
            continue

    if not last_bytes_list:
        raise Exception("对于 XOR, 未能成功读取任何模板文件")

    # 使用 Counter 统计最常见的字节组合
    counter = Counter(last_bytes_list)
    most_common = counter.most_common(1)[0][0]

    x, y = most_common
    if (xor_key := x ^ 0xFF) == y ^ 0xD9:
        pass
    else:
        raise Exception("未能找到 XOR 密钥")

    if xor_key_:
        if xor_key_ == xor_key:
            return xor_key_, aes_key_
        else:
            raise Exception

    if version == 3:
        return xor_key, b"cfcd208495d565ef"

    for file in template_files:
        with open(file, "rb") as f:
            # 检查文件头
            if f.read(6) != b"\x07\x08V2\x08\x07":
                continue

            # 检查文件尾
            f.seek(-2, 2)
            if f.read(2) != most_common:
                continue

            # 读取 AES 密钥
            f.seek(0xF)
            ciphertext = f.read(16)
            break
    else:
        raise Exception("对于 AES, 未能成功读取任何模板文件")

    try:
        pm = pymem.Pymem("Weixin.exe")
        pid = pm.process_id
        assert isinstance(pid, int)
    except:
        raise Exception("找不到微信进程")

    aes_key = dump_wechat_info_v4(ciphertext, pid)
    return xor_key, aes_key


CONFIG_FILE = "config.json"


def read_key_from_config() -> Tuple[int, bytes]:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            key_dict = json.loads(f.read())

        x, y = key_dict["xor"], key_dict["aes"]
        return x, y.encode()[:16]

    return 0, b""


def store_key(xor_k: int, aes_k: bytes) -> None:
    key_dict = {
        "xor": xor_k,
        "aes": aes_k.decode(),
    }

    with open(CONFIG_FILE, "w") as f:
        f.write(json.dumps(key_dict))


def decrypt_file(file_path: str, xor_key: int, aes_key: bytes) -> bytes:
    version = decrypt_dat(file_path)
    if version == 0:
        data = decrypt_dat_v3(file_path, xor_key)
    elif version == 1:
        data = decrypt_dat_v4(file_path, xor_key, b"cfcd208495d565ef")
    elif version == 2:
        data = decrypt_dat_v4(file_path, xor_key, aes_key)
    else:
        raise Exception(f"Not support version: {version}")
    return data


def get_image_info(data: bytes) -> Union[Tuple[str, int], None]:
    JPEG = (0xFF, 0xD8, 0xFF)
    PNG = (0x89, 0x50, 0x4E)
    BMP = (0x42, 0x4D)
    GIF = (0x47, 0x49, 0x46)
    IMAGE_FORMAT_FEATURE = [JPEG, PNG, BMP, GIF]
    IMAGE_FORMAT = {0: "jpg", 1: "png", 2: "bmp", 3: "gif"}

    for i, FORMAT_FEATURE in enumerate(IMAGE_FORMAT_FEATURE):
        result = []
        image_feature = data[: len(FORMAT_FEATURE)]
        for j, format_feature in enumerate(FORMAT_FEATURE):
            result.append(image_feature[j] ^ format_feature)

        sum = result[0]
        for k in result:
            sum ^= k

        if sum == 0:
            return IMAGE_FORMAT[i], result[0]


def decode_image_data(data: bytes, key: int) -> bytes:
    image_data = []
    for byte in data:
        image_data.append(byte ^ key)
    return bytes(image_data)


def decode_image(src_file: str, output_path: str = ".") -> Tuple[str, str]:
    src_file = pathlib.Path(src_file)
    output_path = pathlib.Path(output_path)
    dat_filename = src_file.name.replace(".dat", "")
    with open(src_file, "rb") as dat_file:
        data = dat_file.read()

    suffix, key = get_image_info(data)
    image_data = decode_image_data(data, key)

    image_filename = output_path / f"{dat_filename}.{suffix}"
    with open(image_filename, "wb") as f:
        f.write(image_data)

    return str(src_file.absolute()), str(image_filename.absolute())


"""
 Description: 修改微信内存版本, 原理参考: https://blog.csdn.net/Scoful/article/details/139330910
"""
WECHAT_VERSION_OFFSET = {
    "3.6.0.18": [0x22300E0, 0x223D90C, 0x223D9E8, 0x2253E4C, 0x2255AA4, 0x22585D4]
}


def modify_wechat_version(old_version: str, new_version: str) -> None:
    pm = pymem.Pymem("WeChat.exe")
    WeChatWinDll = pymem.process.module_from_name(
        pm.process_handle, "WeChatWin.dll"
    ).lpBaseOfDll
    original_version_hex = version_to_hex(old_version)
    new_version_hex = version_to_hex(new_version)

    for offset in WECHAT_VERSION_OFFSET[old_version]:
        addr = WeChatWinDll + offset
        addr_value = pm.read_uint(addr)
        if addr_value == original_version_hex:
            pm.write_uint(addr, new_version_hex)


def version_to_hex(version: str) -> int:
    result = "0x6"
    version_list = version.split(".")

    for i in range(len(version_list)):
        if i == 0:
            result += f"{int(version_list[i]):x}"
            continue
        result += f"{int(version_list[i]):02x}"

    return int(result, 16)


def cmd(
    args, input_values=None, on_input=None, encoding="gbk", chunk_size=1024, **kwargs
):
    if input_values is not None and not isinstance(input_values, dict):
        raise TypeError("input_values must be a dict.")

    if on_input is not None and not callable(on_input):
        raise TypeError("on_input must be callable.")

    input_values = {} if input_values is None else input_values

    class CmdResult:
        def __init__(self, gen):
            self.gen = gen
            self.return_code = None
            self.output = None

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.gen)

        def __str__(self):
            return f"<CmdResult return_code={self.return_code} output={bytes(self.output)}>"

    def generator():
        with subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            text=False,
            **kwargs,
        ) as process:
            output = bytearray()
            while True:
                chunk = process.stdout.read(chunk_size)
                if not chunk:
                    break
                chunk = cast(bytes, chunk)
                yield chunk.decode(encoding)
                output.extend(chunk)
                output_lines = output.splitlines(keepends=True)
                last_line = bytes(output_lines[-1]).rstrip(b"\r\n")
                input_value = None
                if last_line in input_values:
                    input_value = input_values[last_line]
                if on_input is not None:
                    input_value = on_input(last_line)
                if input_value is not None:
                    input_value += b"\n"
                    yield input_value.decode(encoding)
                    output.extend(input_value)
                    process.stdin.write(input_value)
                    process.stdin.flush()

        cmd_result.return_code = process.returncode
        cmd_result.output = output

    cmd_result = CmdResult(generator())
    return cmd_result


def get_wechat4_key():
    execute_cmd = cmd([db_key_hook_cmd])
    list(execute_cmd)
    output = bytes(execute_cmd.output).decode("gbk")
    key = re.findall("获取到DbKey：(.*?)\r\r\n", output)[0]
    return key


if __name__ == "__main__":
    print(get_wechat4_key())
