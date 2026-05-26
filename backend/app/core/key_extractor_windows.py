"""Windows 平台 WeChat 数据库密钥提取器。

通过 ctypes 调用 Windows API (kernel32.ReadProcessMemory)
扫描 WeChat.exe 进程内存，提取 SQLCipher 数据库加密密钥。
"""

import ctypes
import ctypes.wintypes
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import psutil

from app.core.base import BaseKeyExtractor

logger = logging.getLogger(__name__)

# --- Win32 API 常量与结构体定义 ---

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_ALL_ACCESS = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION

# 内存信息常量
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_READWRITE = 0x04
PAGE_READONLY = 0x02

# 系统信息
STILL_ACTIVE = 259


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.wintypes.DWORD),
        ("Protect", ctypes.wintypes.DWORD),
        ("Type", ctypes.wintypes.DWORD),
    ]


class SYSTEM_INFO(ctypes.Structure):
    _fields_ = [
        ("wProcessorArchitecture", ctypes.wintypes.WORD),
        ("wReserved", ctypes.wintypes.WORD),
        ("dwPageSize", ctypes.wintypes.DWORD),
        ("lpMinimumApplicationAddress", ctypes.c_void_p),
        ("lpMaximumApplicationAddress", ctypes.c_void_p),
        ("dwActiveProcessorMask", ctypes.wintypes.LPVOID),
        ("dwNumberOfProcessors", ctypes.wintypes.DWORD),
        ("dwProcessorType", ctypes.wintypes.DWORD),
        ("dwAllocationGranularity", ctypes.wintypes.DWORD),
        ("wProcessorLevel", ctypes.wintypes.WORD),
        ("wProcessorRevision", ctypes.wintypes.WORD),
    ]


# --- Windows 密钥提取器 ---

class WindowsKeyExtractor(BaseKeyExtractor):
    """Windows 平台 WeChat 密钥提取器。

    功能:
    1. 查找 WeChat.exe 进程 PID
    2. 扫描进程内存匹配 SQLCipher 密钥
    3. 验证密钥有效性
    4. 持久化密钥到 JSON 文件
    """

    # 密钥正则模式: 64 hex (key) + 32 hex (salt) = 96 hex chars
    KEY_PATTERN = re.compile(rb"([0-9A-Fa-f]{64})([0-9A-Fa-f]{32})")

    # 数据库文件名模式
    DB_NAMES = [
        "MSG.db", "MicroMsg.db", "Misc.db", "Emotion.db",
        "Sns.db", "Media.db", "BizChatMsg.db", "Function.db",
        "OpenIMContact.db", "OpenIMMedia.db", "OpenIMMsg.db",
    ]

    # WeChat 数据目录路径模式
    DATA_DIR_PATTERN = "%USERPROFILE%/Documents/WeChat Files"

    def __init__(self):
        self._kernel32 = ctypes.windll.kernel32
        self._setup_ctypes()
        self._keys: dict[str, str] = {}
        self._all_keys_file = Path("data/all_keys.json")

    def _setup_ctypes(self):
        """配置 ctypes 函数签名，防止 64 位系统指针截断。"""
        import ctypes.wintypes
        SIZE_T = ctypes.c_size_t
        HANDLE = ctypes.wintypes.HANDLE
        LPVOID = ctypes.wintypes.LPVOID
        DWORD = ctypes.wintypes.DWORD
        BOOL = ctypes.wintypes.BOOL

        self._kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
        self._kernel32.OpenProcess.restype = HANDLE

        self._kernel32.GetSystemInfo.argtypes = [ctypes.POINTER(SYSTEM_INFO)]
        self._kernel32.GetSystemInfo.restype = None

        self._kernel32.VirtualQueryEx.argtypes = [HANDLE, LPVOID, ctypes.POINTER(MEMORY_BASIC_INFORMATION), SIZE_T]
        self._kernel32.VirtualQueryEx.restype = SIZE_T

        self._kernel32.ReadProcessMemory.argtypes = [HANDLE, LPVOID, LPVOID, SIZE_T, ctypes.POINTER(SIZE_T)]
        self._kernel32.ReadProcessMemory.restype = BOOL

        self._kernel32.CloseHandle.argtypes = [HANDLE]
        self._kernel32.CloseHandle.restype = BOOL

    # --- 公共接口 ---

    def find_wechat_process(self) -> Optional[int]:
        """查找 WeChat.exe 进程，返回 PID 或 None。"""
        logger.info("正在查找 WeChat.exe 进程...")
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] and proc.info["name"].lower() == "wechat.exe":
                    pid: int = proc.info["pid"]
                    logger.info(f"找到 WeChat.exe 进程，PID: {pid}")
                    return pid
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            logger.error(f"查找 WeChat 进程时出错: {exc}")

        logger.warning("未找到正在运行的 WeChat.exe 进程")
        return None

    def scan_memory_for_keys(self, pid: int) -> dict[str, str]:
        """扫描进程内存，提取密钥。

        Args:
            pid: WeChat 进程 PID。

        Returns:
            字典，映射 db_name -> hex_key_string。
        """
        logger.info(f"开始扫描进程 {pid} 的内存...")

        h_process = self._open_process(pid)
        if not h_process:
            return {}

        try:
            sysinfo = self._get_system_info()
            addresses = self._get_memory_regions(h_process, sysinfo)

            found_keys: dict[str, str] = {}
            seen_keys: set[str] = set()

            for start_addr, region_size in addresses:
                try:
                    buffer = self._read_process_memory(
                        h_process, start_addr, region_size
                    )
                    if buffer is None:
                        continue

                    for match in self.KEY_PATTERN.finditer(buffer):
                        hex_key = match.group(1).decode("ascii").upper()
                        hex_salt = match.group(2).decode("ascii").upper()

                        # 去重
                        full_key = hex_key + hex_salt
                        if full_key in seen_keys:
                            continue
                        seen_keys.add(full_key)

                        logger.info(
                            f"发现候选密钥: key={hex_key[:16]}... salt={hex_salt[:8]}..."
                        )

                        # 尝试验证
                        key_bytes = bytes.fromhex(hex_key)
                        db_path = self._find_msg_db()
                        if db_path and self.verify_key(key_bytes, db_path):
                            logger.info("密钥验证成功!")
                            found_keys["MSG"] = hex_key
                            # 找到有效密钥后继续扫描，收集所有可能的密钥
                            break

                except Exception as exc:
                    logger.debug(f"读取内存区域 {hex(start_addr)} 失败: {exc}")
                    continue

            if found_keys:
                self._keys = found_keys
                self._save_keys()

            logger.info(f"扫描完成，共找到 {len(found_keys)} 个有效密钥")
            return found_keys

        finally:
            self._kernel32.CloseHandle(h_process)

    def verify_key(self, key: bytes, db_path: str) -> bool:
        """验证密钥是否可解密数据库。

        Args:
            key: 原始密钥字节 (32 bytes)。
            db_path: 数据库文件路径。

        Returns:
            True 表示密钥有效。
        """
        if not os.path.exists(db_path):
            logger.warning(f"数据库文件不存在: {db_path}")
            return False

        try:
            with open(db_path, "rb") as f:
                page1 = f.read(4096)

            if len(page1) < 4096:
                logger.warning("数据库页面大小不足 4096 字节")
                return False

            # 尝试使用不同的迭代次数派生密钥并解密 page 1
            from Crypto.Hash import HMAC, SHA512
            from Crypto.Protocol.KDF import PBKDF2
            from Crypto.Cipher import AES

            salt = page1[16:32]  # page 1 的 salt 位于偏移量 16-31
            iv = page1[:16]
            encrypted_page = page1[16:4096 - 48]  # 去除 48 字节保留区

            # 常见的迭代次数: SQLCipher 4 默认 256000, 旧版本 64000
            for iterations in [256000, 64000, 4000]:
                try:
                    derived = PBKDF2(
                        key, salt, dkLen=64, count=iterations,
                        hmac_hash_module=SHA512,
                    )
                    aes_key = derived[:32]
                    hmac_key = derived[32:64]  # 用于 HMAC 验证

                    cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
                    decrypted = cipher.decrypt(encrypted_page)

                    # 验证 SQLite 文件头
                    if decrypted[:16] == b"SQLite format 3\x00":
                        logger.info(
                            f"密钥验证成功 (iterations={iterations})"
                        )
                        return True

                    # 备选: 使用 HMAC 验证
                    # SQLCipher 在保留区存储页面的 HMAC
                    reserved = page1[4096 - 48:4096]
                    page_hmac = reserved[:32]
                    calculated_hmac = HMAC.new(
                        hmac_key,
                        iv + decrypted[:len(encrypted_page)],
                        SHA512,
                    ).digest()[:32]
                    if calculated_hmac == page_hmac:
                        logger.info(
                            f"密钥 HMAC 验证成功 (iterations={iterations})"
                        )
                        return True

                except Exception as exc:
                    logger.debug(f"迭代 {iterations} 尝试失败: {exc}")
                    continue

            return False

        except Exception as exc:
            logger.error(f"验证密钥时出错: {exc}")
            return False

    # --- 内部方法 ---

    def _open_process(self, pid: int) -> Optional[int]:
        """打开进程获取句柄。"""
        h_process = self._kernel32.OpenProcess(
            PROCESS_ALL_ACCESS, False, pid
        )
        if not h_process:
            logger.error(f"无法打开进程 {pid}，错误码: {ctypes.get_last_error()}")
            return None
        return h_process

    def _get_system_info(self) -> SYSTEM_INFO:
        """获取系统信息。"""
        sysinfo = SYSTEM_INFO()
        self._kernel32.GetSystemInfo(ctypes.byref(sysinfo))
        return sysinfo

    def _get_memory_regions(
        self, h_process: int, sysinfo: SYSTEM_INFO
    ) -> list[tuple[int, int]]:
        """枚举进程的可读内存区域。

        Returns:
            (起始地址, 区域大小) 元组列表。
        """
        regions: list[tuple[int, int]] = []
        mbi = MEMORY_BASIC_INFORMATION()
        address = sysinfo.lpMinimumApplicationAddress
        max_address = sysinfo.lpMaximumApplicationAddress

        while address < max_address:
            result = self._kernel32.VirtualQueryEx(
                h_process,
                address,
                ctypes.byref(mbi),
                ctypes.sizeof(mbi),
            )
            if result == 0:
                break

            addr = mbi.BaseAddress
            size = mbi.RegionSize

            # 只处理已提交、可读的私有内存区域
            if (
                mbi.State == MEM_COMMIT
                and mbi.Type == MEM_PRIVATE
                and (mbi.Protect & (PAGE_READONLY | PAGE_READWRITE))
            ):
                regions.append((addr, size))

            address = addr + size

        return regions

    def _read_process_memory(
        self, h_process: int, address: int, size: int
    ) -> Optional[bytes]:
        """读取进程内存。

        Args:
            h_process: 进程句柄。
            address: 起始地址。
            size: 读取大小。

        Returns:
            读取的字节数据，失败返回 None。
        """
        if size <= 0 or size > 100 * 1024 * 1024:  # 限制单次最大 100MB
            return None

        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)

        result = self._kernel32.ReadProcessMemory(
            h_process,
            ctypes.c_void_p(address),
            buffer,
            ctypes.c_size_t(size),
            ctypes.byref(bytes_read),
        )

        if result == 0:
            return None

        return buffer.raw[:bytes_read.value]

    def _find_msg_db(self) -> Optional[str]:
        """查找 MSG.db 数据库文件路径。"""
        data_root = self._find_wechat_data_dir()
        if not data_root:
            return None

        for entry in os.scandir(data_root):
            if entry.is_dir():
                msg_dir = os.path.join(entry.path, "Msg")
                msg_db = os.path.join(msg_dir, "MSG.db")
                if os.path.exists(msg_db):
                    return msg_db
                # 也检查 Multi 子目录
                multi_dir = os.path.join(msg_dir, "Multi")
                if os.path.exists(multi_dir):
                    for sub_entry in os.scandir(multi_dir):
                        candidate = os.path.join(sub_entry.path, "MSG.db")
                        if os.path.exists(candidate):
                            return candidate

        return None

    def _find_wechat_data_dir(self) -> Optional[str]:
        """查找微信数据根目录。"""
        userprofile = os.getenv("USERPROFILE", "")
        if not userprofile:
            return None

        base = os.path.join(userprofile, "Documents", "WeChat Files")
        return base if os.path.exists(base) else None

    def _save_keys(self) -> None:
        """持久化密钥到 JSON 文件。"""
        self._all_keys_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._all_keys_file, "w", encoding="utf-8") as f:
                json.dump(self._keys, f, indent=2)
            logger.info(f"密钥已保存到 {self._all_keys_file}")
        except Exception as exc:
            logger.error(f"保存密钥失败: {exc}")

    def load_keys(self) -> dict[str, str]:
        """从 JSON 文件加载已保存的密钥。"""
        if self._all_keys_file.exists():
            try:
                with open(self._all_keys_file, "r", encoding="utf-8") as f:
                    self._keys = json.load(f)
                logger.info(f"已加载 {len(self._keys)} 个密钥")
            except Exception as exc:
                logger.error(f"加载密钥失败: {exc}")
        return self._keys
