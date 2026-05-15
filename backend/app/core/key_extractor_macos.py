"""macOS 平台 WeChat 数据库密钥提取器。

通过 C 辅助程序 (mach_helper) 调用 Mach VM API 扫描 WeChat 进程内存，
提取 SQLCipher 数据库加密密钥。

WeChat 4.x 在进程内存中缓存派生密钥，格式为:
  x'<64 hex key><32 hex salt>'

通过匹配内存中的 salt 与数据库文件头的 salt 来验证密钥，
比 PBKDF2 尝试快数个数量级。

Python ctypes + libffi 在 ARM64 macOS 上调用 mach_vm_region 存在
调用约定兼容问题 (SIGSEGV)，因此将 Mach VM 操作封装到独立 C 程序中。
需要 root 权限运行。
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

import psutil

from app.core.base import BaseKeyExtractor

logger = logging.getLogger(__name__)


class MacOSKeyExtractor(BaseKeyExtractor):
    """macOS 平台 WeChat 密钥提取器。

    功能:
    1. 查找 WeChat.app 进程 PID
    2. 扫描数据目录中所有加密 .db 文件，提取 salt
    3. 通过 Mach VM API 扫描进程内存中的密钥模式
    4. 通过 salt 匹配验证密钥
    5. 持久化密钥到 JSON 文件

    要求:
    - 需要 root 权限或 SIP 关闭
    """

    # 微信 macOS 数据目录 (现代路径)
    XWECHAT_FILES_DIR = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/"
        "Data/Documents/xwechat_files/"
    )

    # 密钥持久化文件
    ALL_KEYS_FILE = Path("data/all_keys.json")

    DB_NAMES = [
        "MSG.db", "MicroMsg.db", "Misc.db", "Emotion.db",
        "Sns.db", "Media.db", "BizChatMsg.db", "Function.db",
        "OpenIMContact.db", "OpenIMMedia.db", "OpenIMMsg.db",
    ]

    def __init__(self):
        self._keys: dict[str, str] = {}
        self._helper_path = Path(__file__).resolve().parent / "mach_helper"
        self._ensure_helper()

    def _ensure_helper(self) -> None:
        """确保 C helper 程序已编译且可执行。"""
        if self._helper_path.exists() and os.access(self._helper_path, os.X_OK):
            return

        src_path = self._helper_path.with_suffix(".c")
        if not src_path.exists():
            logger.warning(f"mach_helper 源码不存在: {src_path}")
            return

        logger.info(f"正在编译 mach_helper: {src_path}")
        try:
            result = subprocess.run(
                ["cc", "-O2", "-o",
                 str(self._helper_path), str(src_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                logger.info("mach_helper 编译成功")
            else:
                logger.error(
                    f"mach_helper 编译失败: {result.stderr.strip()}"
                )
        except Exception as exc:
            logger.error(f"编译 mach_helper 时出错: {exc}")

    # --- 公共接口 ---

    def find_wechat_process(self) -> Optional[int]:
        """查找 WeChat.app 进程，返回 PID 或 None。"""
        logger.info("正在查找 WeChat.app 进程...")
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] and proc.info["name"].lower() in (
                    "wechat", "wechat.app", "wechat.appex",
                    "wechathelper", "wechathelper_renderer",
                ):
                    pid: int = proc.info["pid"]
                    if proc.info["name"].lower() == "wechat":
                        logger.info(f"找到 WeChat.app 进程，PID: {pid}")
                        return pid
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] and "wechat" in proc.info["name"].lower():
                    pid = proc.info["pid"]
                    logger.info(f"找到 WeChat 相关进程，PID: {pid}")
                    return pid
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            logger.error(f"查找 WeChat 进程时出错: {exc}")

        logger.warning("未找到正在运行的 WeChat 进程")
        return None

    def collect_db_salts(self) -> dict[str, str]:
        """收集所有加密 .db 文件的 salt。

        遍历 xwechat_files 目录下所有 db_storage 子目录,
        读取每个 .db 文件的 salt (前 16 字节)。

        Returns:
            字典，映射 relative_path -> salt_hex。
        """
        salts: dict[str, str] = {}

        if not os.path.isdir(self.XWECHAT_FILES_DIR):
            logger.warning(f"微信数据目录不存在: {self.XWECHAT_FILES_DIR}")
            return salts

        for wxid_entry in os.scandir(self.XWECHAT_FILES_DIR):
            if not wxid_entry.is_dir():
                continue

            storage = os.path.join(wxid_entry.path, "db_storage")
            if not os.path.isdir(storage):
                continue

            for root, _dirs, files in os.walk(storage):
                for fname in files:
                    if not fname.endswith(".db"):
                        continue

                    full_path = os.path.join(root, fname)
                    try:
                        with open(full_path, "rb") as f:
                            header = f.read(16)

                        # 跳过未加密的数据库
                        if header[:15] == b"SQLite format 3\x00":
                            continue

                        rel_path = os.path.relpath(full_path, storage)
                        salt_hex = header.hex()
                        salts[rel_path] = salt_hex
                        logger.debug(
                            f"  {rel_path}: salt={salt_hex}"
                        )
                    except (OSError, IOError) as exc:
                        logger.debug(f"无法读取 {full_path}: {exc}")

        logger.info(f"收集到 {len(salts)} 个加密数据库的 salt")
        return salts

    def scan_memory_for_keys(self, pid: int) -> dict[str, str]:
        """扫描进程内存，提取密钥。

        通过 C 辅助程序 mach_helper 调用 Mach VM API，
        扫描 WeChat 进程内存中的 x'<64hex_key><32hex_salt>' 模式。

        使用 salt 匹配来验证密钥 —— 内存中找到的 salt 必须与
        某个数据库文件头的 salt 一致。

        Args:
            pid: WeChat 进程 PID。

        Returns:
            字典，映射 db_name -> hex_key_string。
        """
        logger.info(f"开始扫描进程 {pid} 的内存 (macOS)...")

        if os.geteuid() != 0:
            logger.warning(
                "非 root 权限运行，Mach VM 内存读取可能会失败。"
                "请使用 sudo 运行。"
            )

        # 先收集数据库 salt 用于匹配
        db_salts = self.collect_db_salts()
        if not db_salts:
            logger.warning("未找到加密数据库，跳过扫描")
            return {}

        # 构建 salt -> db_path 反向索引
        salt_to_db: dict[str, str] = {}
        for db_path, salt_hex in db_salts.items():
            normalized = salt_hex.lower()
            if normalized in salt_to_db:
                # 优先保留 message 相关的
                if "message" in db_path.lower():
                    salt_to_db[normalized] = db_path
            else:
                salt_to_db[normalized] = db_path

        if not self._helper_path.exists():
            logger.error("mach_helper 不存在，无法扫描内存")
            return {}

        found_keys: dict[str, str] = {}
        seen_keys: set[str] = set()

        try:
            result = subprocess.run(
                [str(self._helper_path), str(pid)],
                capture_output=True, text=True, timeout=120,
            )

            if result.returncode == 2:
                logger.warning("mach_helper: task_for_pid 失败 (需要 root)")
                return {}
            if result.returncode != 0:
                logger.warning(
                    f"mach_helper 退出码 {result.returncode}: "
                    f"{result.stderr.strip()}"
                )
                return {}

            # 解析候选密钥 (96 hex chars = 64 key + 32 salt)
            candidates = [
                line.strip()
                for line in result.stdout.strip().split("\n")
                if line.strip() and len(line.strip()) == 96
            ]

            logger.info(f"mach_helper 找到 {len(candidates)} 个候选密钥")

            for candidate in candidates:
                if candidate in seen_keys:
                    continue
                seen_keys.add(candidate)

                hex_key = candidate[:64].lower()
                hex_salt = candidate[64:96].lower()

                # Salt 匹配
                if hex_salt in salt_to_db:
                    db_path = salt_to_db[hex_salt]
                    logger.info(
                        f"密钥 salt 匹配: {db_path} "
                        f"(key={hex_key[:16]}...)"
                    )
                    found_keys[db_path] = hex_key
                else:
                    logger.debug(
                        f"未匹配 salt: key={hex_key[:16]}... "
                        f"salt={hex_salt[:16]}..."
                    )

        except subprocess.TimeoutExpired:
            logger.error("mach_helper 执行超时")
        except FileNotFoundError:
            logger.error(f"mach_helper 不可执行: {self._helper_path}")
        except Exception as exc:
            logger.error(f"调用 mach_helper 失败: {exc}")

        if found_keys:
            self._keys = found_keys
            self._save_keys()
            logger.info(
                f"扫描完成，共匹配 {len(found_keys)} 个数据库密钥"
            )
        else:
            logger.warning("未匹配到任何有效密钥")

        return found_keys

    # --- 密钥验证 (PBKDF2 后备) ---

    # SQLCipher 4 页面布局常量
    _PAGE_SIZE = 4096
    _SALT_SIZE = 16
    _IV_SIZE = 16
    _HMAC_SIZE = 64
    _RESERVED_SIZE = 80  # IV(16) + HMAC(64)

    @staticmethod
    def _derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
        """派生 HMAC 验证密钥 (SQLCipher 4)。"""
        import hashlib
        mac_salt = bytes(b ^ 0x3a for b in salt)
        return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=32)

    def verify_key(self, key: bytes, db_path: str) -> bool:
        """验证密钥是否可解密数据库。

        微信 4.x macOS 使用 SQLCipher 4 页面布局:
        - IV 位于页尾偏移 4016-4032
        - Page 1 加密数据从偏移 16 开始
        - 密钥已预派生，直接用于 AES-256-CBC

        Args:
            key: AES-256 密钥字节 (32 bytes)。
            db_path: 数据库文件路径。

        Returns:
            True 表示密钥有效。
        """
        import hashlib
        import hmac as hmac_mod
        import struct

        if not os.path.exists(db_path):
            logger.warning(f"数据库文件不存在: {db_path}")
            return False

        try:
            with open(db_path, "rb") as f:
                page1 = f.read(self._PAGE_SIZE)

            if len(page1) < self._PAGE_SIZE:
                return False

            from Crypto.Cipher import AES

            salt = page1[:self._SALT_SIZE]
            iv = page1[
                self._PAGE_SIZE - self._RESERVED_SIZE:
                self._PAGE_SIZE - self._RESERVED_SIZE + self._IV_SIZE
            ]
            encrypted = page1[self._SALT_SIZE:self._PAGE_SIZE - self._RESERVED_SIZE]

            cipher = AES.new(key, AES.MODE_CBC, iv=iv)
            decrypted = cipher.decrypt(encrypted)

            if decrypted[:16] == b"SQLite format 3\x00":
                logger.info("密钥验证成功 (SQLite header match)")
                return True

            # HMAC 备选验证
            mac_key = self._derive_mac_key(key, salt)
            hmac_data = page1[
                self._SALT_SIZE:self._PAGE_SIZE - self._RESERVED_SIZE + self._IV_SIZE
            ]
            stored_hmac = page1[self._PAGE_SIZE - self._HMAC_SIZE:self._PAGE_SIZE]
            hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
            hm.update(struct.pack("<I", 1))
            if hm.digest() == stored_hmac:
                logger.info("密钥验证成功 (HMAC match)")
                return True

            return False

        except Exception as exc:
            logger.error(f"验证密钥时出错: {exc}")
            return False

    # --- 密钥持久化 ---

    def _save_keys(self) -> None:
        """持久化密钥到 JSON 文件 (兼容 wechat-decrypt 格式)。"""
        self.ALL_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.ALL_KEYS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._keys, f, indent=2)
            logger.info(f"密钥已保存到 {self.ALL_KEYS_FILE}")
        except Exception as exc:
            logger.error(f"保存密钥失败: {exc}")

    def load_keys(self) -> dict[str, str]:
        """从 JSON 文件加载已保存的密钥。"""
        if self.ALL_KEYS_FILE.exists():
            try:
                with open(self.ALL_KEYS_FILE, "r", encoding="utf-8") as f:
                    self._keys = json.load(f)
                logger.info(f"已加载 {len(self._keys)} 个密钥")
            except Exception as exc:
                logger.error(f"加载密钥失败: {exc}")
        return self._keys
