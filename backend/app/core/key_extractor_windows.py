"""Windows 平台 WeChat 数据库密钥提取器。

通过 ctypes 调用 Windows API (kernel32.ReadProcessMemory)
扫描 WeChat.exe 进程内存，提取 SQLCipher 数据库加密密钥。
"""

import ctypes
import ctypes.wintypes
import concurrent.futures
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import threading
import time
from typing import Optional

import psutil

from app.core.base import BaseKeyExtractor
from app.core.wechat_paths_windows import (
    find_wechat_data_dirs,
    get_available_drives,
    get_wechat_exe_path,
    read_wechat_install_path_from_registry,
)
from app.utils.paths import get_data_dir

logger = logging.getLogger(__name__)

# --- Win32 API 常量与结构体定义 ---
PAGE_SIZE = 4096
RESERVED_SIZE = 80
LEGACY_RESERVED_SIZE = 48
SQLITE_HEADER = b"SQLite format 3\x00"

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_ALL_ACCESS = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION

# 内存信息常量
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_READWRITE = 0x04
PAGE_READONLY = 0x02
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100
PAGE_NOCACHE = 0x200
PAGE_WRITECOMBINE = 0x400
READABLE_PROTECTIONS = (
    PAGE_READONLY
    | PAGE_READWRITE
    | PAGE_WRITECOPY
    | PAGE_EXECUTE_READ
    | PAGE_EXECUTE_READWRITE
    | PAGE_EXECUTE_WRITECOPY
)


def _verify_sqlcipher_passphrase(passphrase: bytes, page1: bytes) -> bool:
    """验证 SQLCipher 原始 passphrase 是否匹配 page 1 HMAC。"""
    if len(passphrase) != 32 or len(page1) < PAGE_SIZE:
        return False
    try:
        from Crypto.Hash import SHA1, SHA512
        from Crypto.Protocol.KDF import PBKDF2

        salt = page1[:16]
        for iterations, hash_module, digestmod, reserve_size in [
            (256000, SHA512, hashlib.sha512, RESERVED_SIZE),
            (64000, SHA1, hashlib.sha1, LEGACY_RESERVED_SIZE),
            (4000, SHA1, hashlib.sha1, LEGACY_RESERVED_SIZE),
        ]:
            mac_salt = bytes(x ^ 0x3A for x in salt)
            aes_key = PBKDF2(
                passphrase,
                salt,
                dkLen=32,
                count=iterations,
                hmac_hash_module=hash_module,
            )
            mac_key = PBKDF2(
                aes_key,
                mac_salt,
                dkLen=32,
                count=2,
                hmac_hash_module=hash_module,
            )
            if _verify_page_hmac(page1, mac_key, reserve_size, digestmod):
                return True
    except Exception:
        return False
    return False


def _verify_page_hmac(
    page1: bytes,
    mac_key: bytes,
    reserve_size: int,
    digestmod,
) -> bool:
    if reserve_size == RESERVED_SIZE:
        stored = page1[PAGE_SIZE - reserve_size + 16:PAGE_SIZE]
        data = page1[16:PAGE_SIZE - reserve_size + 16]
    else:
        first_page = page1[16:PAGE_SIZE]
        stored = first_page[-32:-12]
        data = first_page[:-32]
    calculated_hmac = hmac.new(mac_key, data, digestmod)
    calculated_hmac.update(struct.pack("<I", 1))
    return calculated_hmac.digest() == stored


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
    WCDB_KEY_PATTERN = re.compile(rb"x'([0-9A-Fa-f]{64,192})'")
    V4_KEY_STUB_PATTERN = re.compile(rb".{6}\x00{2}\x00{8}\x20\x00{7}\x2f\x00{7}", re.S)

    # 数据库文件名模式
    DB_NAMES = [
        "MSG.db", "MicroMsg.db", "Misc.db", "Emotion.db",
        "Sns.db", "Media.db", "BizChatMsg.db", "Function.db",
        "OpenIMContact.db", "OpenIMMedia.db", "OpenIMMsg.db",
    ]
    SCAN_TIMEOUT_SECONDS = 60
    MAX_CANDIDATE_KEYS = 200
    MAX_V4_CANDIDATE_KEYS = 1000
    MAX_SCAN_REGION_SIZE = 512 * 1024 * 1024
    SCAN_CHUNK_SIZE = 2 * 1024 * 1024
    SCAN_CHUNK_OVERLAP = 4096

    def __init__(self):
        self._kernel32 = ctypes.windll.kernel32
        self._setup_ctypes()
        self._keys: dict[str, str] = {}
        self._all_keys_file = get_data_dir() / "all_keys.json"
        self._data_dirs_cache: Optional[list[str]] = None

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

    def find_wechat_processes(self) -> list[int]:
        """查找可能持有数据库密钥的微信进程 PID 列表。"""
        logger.info("正在查找 WeChat.exe 进程...")
        wechat_names = ["wechat.exe", "weixin.exe", "wechatapp.exe"]
        found_names = []
        candidates = []
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline", "ppid"]):
                pname = (proc.info["name"] or "").lower()
                # 收集所有可能是微信的进程名
                if "wechat" in pname or "weixin" in pname or "tencent" in pname:
                    found_names.append(f"{proc.info['name']} (PID: {proc.info['pid']})")
                if pname in wechat_names:
                    cmdline = " ".join(proc.info.get("cmdline") or [])
                    if self._is_wechat_helper_process(pname, cmdline):
                        continue
                    candidates.append(proc)

            if candidates:
                ordered = sorted(candidates, key=self._wechat_process_score, reverse=True)
                pids = [int(proc.info["pid"]) for proc in ordered]
                logger.info(
                    "找到微信进程: %s",
                    [f"{proc.info['name']} PID:{proc.info['pid']}" for proc in ordered],
                )
                return pids
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            logger.error(f"查找 WeChat 进程时出错: {exc}")

        if found_names:
            logger.warning(f"未匹配到已知微信进程名，但发现以下相关进程: {found_names}")
        else:
            logger.warning("未找到任何微信相关进程，请确认微信已启动")
        return []

    def find_wechat_process(self) -> Optional[int]:
        """查找 WeChat.exe 主进程，返回 PID 或 None。"""
        pids = self.find_wechat_processes()
        return pids[0] if pids else None

    @staticmethod
    def _is_wechat_helper_process(process_name: str, cmdline: str) -> bool:
        lower_cmd = cmdline.lower()
        if process_name == "weixin.exe" and "--type=" in lower_cmd:
            return True
        if process_name in {"wechatapp.exe", "wechatappex.exe"}:
            return True
        return False

    @staticmethod
    def _wechat_process_score(proc: psutil.Process) -> tuple[int, float]:
        """Prefer processes likely to hold local database state."""
        candidate_pids = set()
        try:
            candidate_pids = {
                p.info["pid"]
                for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower()
                in {"wechat.exe", "weixin.exe", "wechatapp.exe", "wechatappex.exe"}
            }
        except Exception:
            pass

        cmdline = " ".join(proc.info.get("cmdline") or [])
        lower_cmd = cmdline.lower()
        score_value = 0
        try:
            rss_mb = proc.memory_info().rss / 1024 / 1024
            score_value += min(200, int(rss_mb // 8))
        except Exception:
            pass
        if "--wechat-files-path" in lower_cmd:
            score_value += 140
        if "--type=" not in lower_cmd:
            score_value += 240
        if (proc.info.get("name") or "").lower() == "weixin.exe":
            score_value += 90
        if proc.info.get("ppid") not in candidate_pids:
            score_value += 20
        if "--type=wxpublic" in lower_cmd or "--type=wxutility" in lower_cmd:
            score_value -= 60
        if "--type=renderer" in lower_cmd:
            score_value -= 30
        if any(flag in lower_cmd for flag in ("--type=wxocr", "--type=wxplayer")):
            score_value -= 80
        exe_path = ""
        try:
            exe_path = proc.exe().lower()
        except Exception:
            exe_path = ""
        if "\\xwechat\\xplugin\\" in exe_path:
            score_value -= 250
        try:
            if proc.create_time():
                score_value += min(60, max(0, int(time.time() - proc.create_time()) // 60))
        except Exception:
            pass
        return score_value, -float(proc.info["pid"])

    @staticmethod
    def _select_wechat_main_process(candidates: list[psutil.Process]) -> psutil.Process:
        """Prefer the main WeChat process over plugin/utility child processes."""
        candidate_pids = {proc.info["pid"] for proc in candidates}

        def score(proc: psutil.Process) -> tuple[int, float]:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            score_value = 0
            if "--type=" not in cmdline:
                score_value += 100
            if proc.info.get("ppid") not in candidate_pids:
                score_value += 50
            try:
                if proc.create_time():
                    # Older root process is usually the logged-in main client.
                    score_value += max(0, int(time.time() - proc.create_time()) // 60)
            except Exception:
                pass
            try:
                if proc.name().lower() == "weixin.exe":
                    score_value += 5
            except Exception:
                pass
            return score_value, -float(proc.info["pid"])

        return max(candidates, key=score)

    def scan_memory_for_keys(
        self,
        pid: int,
        stop_event: Optional[threading.Event] = None,
    ) -> dict[str, str]:
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
            started_at = time.monotonic()
            sysinfo = self._get_system_info()
            addresses = self._get_memory_regions(h_process, sysinfo)

            found_keys: dict[str, str] = {}
            seen_keys: set[str] = set()
            db_infos = self._collect_validation_dbs()
            salt_to_infos: dict[str, list[dict[str, object]]] = {}
            for info in db_infos:
                salt_to_infos.setdefault(str(info["salt"]), []).append(info)
            db_path = self._select_message_validation_db(db_infos)
            if not db_path:
                logger.warning("未找到可用于验证密钥的微信消息数据库")
                return {}

            logger.info(
                "使用数据库验证候选密钥: %s (共 %d 个加密 DB salt)",
                db_path,
                len(salt_to_infos),
            )

            logger.info("已跳过 DbKey hook，使用纯内存扫描...")

            candidate_count = 0
            hex_candidate_count = 0
            cancelled = False

            wcdb_keys, wcdb_candidates, wcdb_cancelled = self._scan_wcdb_cached_keys(
                h_process,
                addresses,
                salt_to_infos,
                started_at,
                stop_event,
            )
            candidate_count += wcdb_candidates
            if wcdb_keys:
                found_keys.update(wcdb_keys)
                logger.info(f"通过 WCDB 缓存匹配到 {len(wcdb_keys)} 个数据库密钥")
            if wcdb_cancelled:
                logger.info("密钥扫描已取消")
                return {}

            v4_keys = self._collect_v4_binary_key_candidates(
                h_process,
                addresses,
                started_at,
                stop_event,
            )
            if v4_keys:
                candidate_count += len(v4_keys)
                key_bytes = self._find_valid_v4_key(
                    v4_keys,
                    db_path,
                    started_at,
                    stop_event,
                )
                if key_bytes:
                    logger.info("密钥验证成功!")
                    found_keys[self._key_path_for_db(db_path)] = key_bytes.hex().upper()

            for start_addr, region_size in addresses:
                if self._has_message_key(found_keys):
                    break
                if stop_event is not None and stop_event.is_set():
                    cancelled = True
                    break
                if time.monotonic() - started_at > self.SCAN_TIMEOUT_SECONDS:
                    logger.warning(
                        f"密钥扫描超时 ({self.SCAN_TIMEOUT_SECONDS}s)，提前停止"
                    )
                    break
                try:
                    for _chunk_addr, buffer in self._iter_region_chunks(
                        h_process,
                        start_addr,
                        region_size,
                    ):
                        for match in self.KEY_PATTERN.finditer(buffer):
                            hex_key = match.group(1).decode("ascii").upper()
                            hex_salt = match.group(2).decode("ascii").upper()

                            # 去重
                            full_key = hex_key + hex_salt
                            if full_key in seen_keys:
                                continue
                            if stop_event is not None and stop_event.is_set():
                                cancelled = True
                                break
                            seen_keys.add(full_key)
                            candidate_count += 1
                            hex_candidate_count += 1
                            if hex_candidate_count > self.MAX_CANDIDATE_KEYS:
                                break

                            logger.debug(
                                f"发现候选密钥: key={hex_key[:16]}... salt={hex_salt[:8]}..."
                            )

                            # 尝试验证
                            key_bytes = bytes.fromhex(hex_key)
                            if db_path and self.verify_key(key_bytes, db_path):
                                logger.info("密钥验证成功!")
                                found_keys[self._key_path_for_db(db_path)] = hex_key
                                # 找到有效密钥后继续扫描，收集所有可能的密钥
                                break

                        if cancelled or hex_candidate_count > self.MAX_CANDIDATE_KEYS:
                            break

                    if cancelled:
                        break

                except Exception as exc:
                    logger.debug(f"读取内存区域 {hex(start_addr)} 失败: {exc}")
                    continue

            if cancelled:
                logger.info("密钥扫描已取消")
                return {}

            if found_keys:
                self._keys = found_keys
                self._save_keys()
            else:
                self._log_windows_4x_key_info_hint(db_infos)

            logger.info(
                f"扫描完成，候选密钥 {candidate_count} 个，有效密钥 {len(found_keys)} 个"
            )
            return found_keys

        finally:
            self._kernel32.CloseHandle(h_process)

    def _scan_wcdb_cached_keys(
        self,
        h_process: int,
        addresses: list[tuple[int, int]],
        salt_to_infos: dict[str, list[dict[str, object]]],
        started_at: float,
        stop_event: Optional[threading.Event],
    ) -> tuple[dict[str, str], int, bool]:
        """Scan WCDB SQLCipher cached key strings: x'<64hex_key><32hex_salt>'."""
        if not salt_to_infos:
            return {}, 0, False

        found: dict[str, str] = {}
        remaining_salts = set(salt_to_infos)
        seen: set[str] = set()
        candidate_count = 0

        for start_addr, region_size in addresses:
            if stop_event is not None and stop_event.is_set():
                return found, candidate_count, True
            if not remaining_salts:
                break
            if time.monotonic() - started_at > self.SCAN_TIMEOUT_SECONDS:
                logger.warning(f"密钥扫描超时 ({self.SCAN_TIMEOUT_SECONDS}s)，提前停止")
                break
            try:
                chunks = self._iter_region_chunks(h_process, start_addr, region_size)
            except Exception as exc:
                logger.debug(f"准备读取内存区域 {hex(start_addr)} 失败: {exc}")
                continue

            for _chunk_addr, buffer in chunks:
                if not buffer:
                    continue

                for match in self.WCDB_KEY_PATTERN.finditer(buffer):
                    if stop_event is not None and stop_event.is_set():
                        return found, candidate_count, True
                    hex_blob = match.group(1).decode("ascii").lower()
                    for enc_key_hex, salt_hex in self._iter_wcdb_key_salt_pairs(hex_blob):
                        salts_to_try = [salt_hex] if salt_hex else list(remaining_salts)
                        for candidate_salt in salts_to_try:
                            if candidate_salt not in remaining_salts:
                                continue
                            dedup = enc_key_hex + candidate_salt
                            if dedup in seen:
                                continue
                            seen.add(dedup)
                            candidate_count += 1
                            enc_key = bytes.fromhex(enc_key_hex)
                            for info in salt_to_infos[candidate_salt]:
                                if self._verify_direct_aes_key(enc_key, info["page1"]):  # type: ignore[arg-type]
                                    rel_path = str(info["rel_path"])
                                    found[rel_path] = enc_key_hex.upper()
                                    remaining_salts.discard(candidate_salt)
                                    logger.info(
                                        "WCDB 缓存密钥验证成功: %s",
                                        rel_path,
                                    )
                                    break
                            if candidate_salt not in remaining_salts:
                                break

        if candidate_count:
            logger.info(f"WCDB 缓存扫描候选 {candidate_count} 个，有效 {len(found)} 个")
        return found, candidate_count, False

    @staticmethod
    def _iter_wcdb_key_salt_pairs(hex_blob: str):
        if len(hex_blob) == 96:
            yield hex_blob[:64], hex_blob[64:96]
        elif len(hex_blob) == 64:
            yield hex_blob, ""
        elif len(hex_blob) > 96 and len(hex_blob) % 2 == 0:
            yield hex_blob[:64], hex_blob[-32:]

    def _collect_v4_binary_key_candidates(
        self,
        h_process: int,
        addresses: list[tuple[int, int]],
        started_at: float,
        stop_event: Optional[threading.Event],
    ) -> list[bytes]:
        """Collect WeChat 4.x binary key candidates from pointer stubs."""
        keys: list[bytes] = []
        seen_addresses: set[int] = set()
        seen_keys: set[bytes] = set()

        for start_addr, region_size in addresses:
            if stop_event is not None and stop_event.is_set():
                break
            if time.monotonic() - started_at > self.SCAN_TIMEOUT_SECONDS:
                break
            if len(keys) >= self.MAX_V4_CANDIDATE_KEYS:
                break
            try:
                for _chunk_addr, buffer in self._iter_region_chunks(
                    h_process,
                    start_addr,
                    region_size,
                ):
                    if not buffer:
                        continue
                    for match in self.V4_KEY_STUB_PATTERN.finditer(buffer):
                        if len(keys) >= self.MAX_V4_CANDIDATE_KEYS:
                            break
                        pre_address = int.from_bytes(
                            buffer[match.start():match.start() + 8],
                            "little",
                        )
                        if pre_address in seen_addresses:
                            continue
                        seen_addresses.add(pre_address)
                        key = self._read_process_memory(h_process, pre_address, 32)
                        if not key or len(key) != 32 or key in seen_keys:
                            continue
                        seen_keys.add(key)
                        keys.append(key)
            except Exception as exc:
                logger.debug(f"扫描 WeChat 4.x key stub 失败: {exc}")
                continue

        if keys:
            logger.info(f"发现 WeChat 4.x 二进制候选密钥: {len(keys)} 个")
        return keys

    def _find_valid_v4_key(
        self,
        keys: list[bytes],
        db_path: str,
        started_at: float,
        stop_event: Optional[threading.Event],
    ) -> Optional[bytes]:
        """并行验证 WeChat 4.x 候选 passphrase。"""
        try:
            with open(db_path, "rb") as f:
                page1 = f.read(PAGE_SIZE)
        except OSError as exc:
            logger.warning(f"读取验证数据库失败: {exc}")
            return None

        if len(page1) < PAGE_SIZE:
            logger.warning(f"数据库页面大小不足 {PAGE_SIZE} 字节")
            return None

        # direct AES 先快速过一遍，兼容少数已派生 key 的内存布局。
        for key in keys:
            if stop_event is not None and stop_event.is_set():
                return None
            if time.monotonic() - started_at > self.SCAN_TIMEOUT_SECONDS:
                logger.warning(f"密钥扫描超时 ({self.SCAN_TIMEOUT_SECONDS}s)，提前停止")
                return None
            if self._verify_direct_aes_key(key, page1):
                return key

        workers = max(1, min(8, (os.cpu_count() or 2) // 2 or 1))
        logger.info(f"开始并行验证 {len(keys)} 个 WeChat 4.x 候选密钥 (workers={workers})")
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        future_to_key = {
            pool.submit(_verify_sqlcipher_passphrase, key, page1): key
            for key in keys
        }
        try:
            for future in concurrent.futures.as_completed(
                future_to_key,
                timeout=max(1.0, self.SCAN_TIMEOUT_SECONDS - (time.monotonic() - started_at)),
            ):
                if stop_event is not None and stop_event.is_set():
                    return None
                if future.result():
                    logger.info("密钥 HMAC 验证成功 (PBKDF2)")
                    return future_to_key[future]
        except concurrent.futures.TimeoutError:
            logger.warning(f"密钥扫描超时 ({self.SCAN_TIMEOUT_SECONDS}s)，提前停止")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        return None

    @staticmethod
    def _derive_mac_key(
        enc_key: bytes,
        salt: bytes,
        hash_name: str = "sha512",
    ) -> bytes:
        """派生 SQLCipher 4 HMAC 校验密钥。"""
        mac_salt = bytes(b ^ 0x3A for b in salt)
        return hashlib.pbkdf2_hmac(hash_name, enc_key, mac_salt, 2, dklen=32)

    def _verify_direct_aes_key(self, key: bytes, page1: bytes) -> bool:
        """验证候选是否为已派生的 AES-256 页面密钥。"""
        try:
            from Crypto.Cipher import AES

            salt = page1[:16]
            for reserve_size, hash_name, digestmod in [
                (RESERVED_SIZE, "sha512", hashlib.sha512),
                (LEGACY_RESERVED_SIZE, "sha1", hashlib.sha1),
            ]:
                encrypted_page = page1[16:PAGE_SIZE - reserve_size]
                reserved = page1[PAGE_SIZE - reserve_size:PAGE_SIZE]
                iv = reserved[:16]

                cipher = AES.new(key, AES.MODE_CBC, iv=iv)
                decrypted = cipher.decrypt(encrypted_page)
                mac_key = self._derive_mac_key(key, salt, hash_name)
                if (
                    self._looks_like_decrypted_page1(decrypted)
                    and _verify_page_hmac(page1, mac_key, reserve_size, digestmod)
                ):
                    return True
        except Exception as exc:
            logger.debug(f"direct AES 验证失败: {exc}")
        return False

    @staticmethod
    def _looks_like_decrypted_page1(decrypted: bytes) -> bool:
        """识别 SQLCipher page 1 解密后的常见明文形态。"""
        return (
            decrypted[:16] == SQLITE_HEADER
            or decrypted[:2] == b"\x10\x00"
        )

    def verify_key(
        self,
        key: bytes,
        db_path: str,
        allow_raw_pbkdf2: bool = True,
    ) -> bool:
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
                page1 = f.read(PAGE_SIZE)

            if len(page1) < PAGE_SIZE:
                logger.warning(f"数据库页面大小不足 {PAGE_SIZE} 字节")
                return False

            from Crypto.Hash import SHA1, SHA512
            from Crypto.Protocol.KDF import PBKDF2
            from Crypto.Cipher import AES

            salt = page1[:16]  # SQLCipher salt 位于数据库前 16 字节

            # WeChat 4.x 的内存候选通常已经是 AES-256 页面密钥，不能再次 PBKDF2。
            if self._verify_direct_aes_key(key, page1):
                logger.info("密钥验证成功 (direct AES)")
                return True

            if not allow_raw_pbkdf2:
                return False

            # 旧格式/明文十六进制候选兜底：候选可能是 SQLCipher 原始 passphrase。
            for iterations, hash_module, digestmod, reserve_size in [
                (256000, SHA512, hashlib.sha512, RESERVED_SIZE),
                (64000, SHA1, hashlib.sha1, LEGACY_RESERVED_SIZE),
                (4000, SHA1, hashlib.sha1, LEGACY_RESERVED_SIZE),
            ]:
                try:
                    aes_key = PBKDF2(
                        key, salt, dkLen=32, count=iterations,
                        hmac_hash_module=hash_module,
                    )
                    mac_salt = bytes(x ^ 0x3A for x in salt)
                    hmac_key = PBKDF2(
                        aes_key,
                        mac_salt,
                        dkLen=32,
                        count=2,
                        hmac_hash_module=hash_module,
                    )

                    encrypted_page = page1[16:PAGE_SIZE - reserve_size]
                    reserved = page1[PAGE_SIZE - reserve_size:PAGE_SIZE]
                    iv = reserved[:16]
                    cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
                    decrypted = cipher.decrypt(encrypted_page)

                    if (
                        self._looks_like_decrypted_page1(decrypted)
                        and _verify_page_hmac(page1, hmac_key, reserve_size, digestmod)
                    ):
                        logger.info(
                            f"密钥验证成功 (iterations={iterations})"
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

            # 只处理已提交、可读的内存区域。新版 Windows Weixin 的 WCDB
            # 缓存串不一定落在 MEM_PRIVATE，按保护位过滤更接近公开工具。
            if mbi.State == MEM_COMMIT and self._is_readable_protection(mbi.Protect):
                regions.append((addr, size))

            address = addr + size

        return regions

    @staticmethod
    def _is_readable_protection(protect: int) -> bool:
        if protect & PAGE_GUARD:
            return False
        base = protect & ~(PAGE_GUARD | PAGE_NOCACHE | PAGE_WRITECOMBINE)
        return bool(base & READABLE_PROTECTIONS)

    def _iter_region_chunks(
        self,
        h_process: int,
        address: int,
        size: int,
    ):
        """Yield overlapping chunks from a memory region."""
        if size <= 0 or size > self.MAX_SCAN_REGION_SIZE:
            return

        offset = 0
        previous_tail = b""
        while offset < size:
            chunk_size = min(self.SCAN_CHUNK_SIZE, size - offset)
            data = self._read_process_memory(h_process, address + offset, chunk_size)
            if data:
                scan_data = previous_tail + data
                yield address + offset - len(previous_tail), scan_data
                previous_tail = data[-self.SCAN_CHUNK_OVERLAP:]
            if chunk_size <= self.SCAN_CHUNK_OVERLAP:
                offset += chunk_size
            else:
                offset += chunk_size - self.SCAN_CHUNK_OVERLAP

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
        return self._find_validation_db(message_only=True)

    def _collect_validation_dbs(self) -> list[dict[str, object]]:
        """Collect encrypted DB metadata used for salt-based key matching."""
        infos: list[dict[str, object]] = []
        seen: set[str] = set()
        data_roots = self._find_wechat_data_dirs()
        for data_root in data_roots:
            try:
                for root, _dirs, files in os.walk(data_root):
                    for fname in files:
                        lower = fname.lower()
                        if not lower.endswith(".db") or lower.endswith(("-wal", "-shm")):
                            continue
                        full_path = os.path.join(root, fname)
                        key = os.path.normcase(os.path.normpath(full_path))
                        if key in seen:
                            continue
                        seen.add(key)
                        try:
                            if os.path.getsize(full_path) < PAGE_SIZE:
                                continue
                            with open(full_path, "rb") as f:
                                page1 = f.read(PAGE_SIZE)
                        except OSError:
                            continue
                        if len(page1) < PAGE_SIZE or page1.startswith(SQLITE_HEADER):
                            continue
                        infos.append({
                            "path": full_path,
                            "rel_path": self._key_path_for_db(
                                full_path,
                                data_roots=data_roots,
                            ),
                            "salt": page1[:16].hex().lower(),
                            "page1": page1,
                        })
            except OSError as exc:
                logger.debug(f"扫描微信数据目录失败 ({data_root}): {exc}")

        logger.info(f"收集到 {len(infos)} 个加密数据库用于密钥匹配")
        return infos

    @staticmethod
    def _select_message_validation_db(
        db_infos: list[dict[str, object]],
    ) -> Optional[str]:
        if not db_infos:
            return None

        def priority(info: dict[str, object]) -> tuple[int, str]:
            rel_path = str(info["rel_path"]).replace("\\", "/").lower()
            basename = os.path.basename(str(info["path"])).lower()
            if rel_path.endswith("message/message_0.db") or basename == "message_0.db":
                return (0, str(info["path"]))
            if basename == "msg.db":
                return (1, str(info["path"]))
            if basename == "favorite_fts.db":
                return (2, str(info["path"]))
            if basename == "head_image.db":
                return (3, str(info["path"]))
            return (9, str(info["path"]))

        return str(min(db_infos, key=priority)["path"])

    def _find_validation_db(self, message_only: bool = False) -> Optional[str]:
        """查找用于验证密钥的数据库文件路径。"""
        infos = self._collect_validation_dbs()
        if message_only:
            infos = [
                info for info in infos
                if os.path.basename(str(info["path"])).lower()
                in {"msg.db", "message_0.db"}
            ]
        selected = self._select_message_validation_db(infos)
        if not selected:
            return None
        return selected

    def _key_path_for_db(
        self,
        db_path: str,
        data_roots: Optional[list[str]] = None,
    ) -> str:
        """Return a stable key cache path relative to the account db root."""
        norm_db = os.path.normpath(db_path)
        for data_root in data_roots or self._find_wechat_data_dirs():
            try:
                rel = os.path.relpath(norm_db, data_root)
            except ValueError:
                continue
            if rel.startswith(".."):
                continue
            parts = rel.split(os.sep)
            if "db_storage" in parts:
                idx = parts.index("db_storage")
                return "/".join(parts[idx + 1:])
            return "/".join(parts)
        return os.path.basename(norm_db)

    @staticmethod
    def _has_message_key(keys: dict[str, str]) -> bool:
        for key_path in keys:
            normalized = key_path.replace("\\", "/").lower()
            if normalized.endswith("message/message_0.db") or normalized.endswith("msg.db"):
                return True
        return False

    def validate_cached_keys(self, keys: dict[str, str]) -> dict[str, str]:
        """Keep only cached keys that still decrypt their matching current DB."""
        if not keys:
            return {}

        db_infos = self._collect_validation_dbs()
        valid: dict[str, str] = {}
        for info in db_infos:
            rel_path = str(info["rel_path"])
            hex_key = self._lookup_key_for_rel_path(keys, rel_path, str(info["path"]))
            if not hex_key:
                continue
            try:
                key_bytes = bytes.fromhex(hex_key)
            except ValueError:
                continue
            if self.verify_key(key_bytes, str(info["path"])):  # type: ignore[arg-type]
                valid[rel_path] = hex_key.upper()

        if valid:
            self._keys = valid
            if valid != keys:
                self._save_keys()
        return valid

    @staticmethod
    def _lookup_key_for_rel_path(
        keys: dict[str, str],
        rel_path: str,
        full_path: str,
    ) -> Optional[str]:
        normalized_rel = rel_path.replace("\\", "/").lower()
        basename = os.path.basename(full_path).lower()
        for key_path, hex_key in keys.items():
            normalized_key = key_path.replace("\\", "/").lower()
            if "/" in normalized_key:
                if normalized_rel.endswith(normalized_key):
                    return hex_key
            elif normalized_key == basename:
                return hex_key
        return None

    def _find_wechat_data_dirs(self) -> list[str]:
        """查找微信数据根目录列表。"""
        if self._data_dirs_cache is not None:
            return self._data_dirs_cache
        data_dirs = find_wechat_data_dirs()
        for item in data_dirs:
            logger.info(f"找到微信数据目录: {item.path} ({item.source})")
        if not data_dirs:
            logger.warning("未找到微信数据目录")
        self._data_dirs_cache = [item.path for item in data_dirs]
        return self._data_dirs_cache

    def _find_wechat_data_dir(self) -> Optional[str]:
        """查找微信数据根目录。按优先级扫描多个常见位置。"""
        data_dirs = self._find_wechat_data_dirs()
        return data_dirs[0] if data_dirs else None

    def _log_windows_4x_key_info_hint(
        self,
        db_infos: list[dict[str, object]],
    ) -> None:
        """Emit a precise diagnostic when known memory formats do not work."""
        message_db = self._select_message_validation_db(db_infos)
        key_info_files = []
        appdata = os.getenv("APPDATA", "")
        if appdata:
            login_dir = os.path.join(appdata, "Tencent", "xwechat", "login")
            if os.path.isdir(login_dir):
                for root, _dirs, files in os.walk(login_dir):
                    for name in files:
                        if name.lower() == "key_info.dat":
                            key_info_files.append(os.path.join(root, name))

        if key_info_files:
            logger.warning(
                "未能从公开 WCDB 内存缓存格式提取到有效密钥；检测到 Windows "
                "Weixin 4.x key_info.dat (%s)。当前版本可能使用新的 "
                "LoginKeyInfoTable/key_info_data 密钥链路，暂未支持自动解码。",
                key_info_files[0],
            )
        elif message_db:
            logger.warning(
                "已找到消息数据库 %s，但未能从公开 WCDB 内存缓存格式提取到有效密钥。",
                message_db,
            )

    def _read_wechat_install_path_from_registry(self) -> Optional[str]:
        """从 Windows 注册表读取微信安装路径。"""
        return read_wechat_install_path_from_registry()

    def _get_wechat_exe_path(self) -> Optional[str]:
        """从正在运行的微信进程获取 exe 完整路径。"""
        return get_wechat_exe_path()

    @staticmethod
    def _get_available_drives() -> list[str]:
        """获取所有可用的本地磁盘盘符。"""
        return get_available_drives()

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
        env_key = os.getenv("WEIX_WECHAT_DB_KEY", "").strip()
        if env_key:
            env_key = env_key.removeprefix("0x").strip()
            if re.fullmatch(r"[0-9A-Fa-f]{64}", env_key):
                self._keys = {"message_0.db": env_key.upper()}
                logger.info("已从环境变量 WEIX_WECHAT_DB_KEY 加载数据库密钥")
                return self._keys
            logger.warning("WEIX_WECHAT_DB_KEY 格式无效，应为 64 位十六进制字符串")

        if self._all_keys_file.exists():
            try:
                with open(self._all_keys_file, "r", encoding="utf-8") as f:
                    self._keys = json.load(f)
                logger.info(f"已加载 {len(self._keys)} 个密钥")
            except Exception as exc:
                logger.error(f"加载密钥失败: {exc}")
                self._keys = {}
        else:
            self._keys = {}
        return self._keys

    def clear_keys(self) -> None:
        """清空内存和磁盘上的密钥缓存。"""
        self._keys = {}
        try:
            self._all_keys_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"删除密钥缓存失败: {exc}")
