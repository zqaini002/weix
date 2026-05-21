"""macOS 平台 WeChat 数据库读取器。

微信 4.x 使用 SQLCipher 4 (cipher_compatibility=4)，页面布局:
  - 每页 4096 字节，末尾保留 80 字节 (IV 16 + HMAC 64)
  - IV 位于页尾偏移 4016-4032
  - Page 1: 开头 16 字节为 salt，加密数据从偏移 16 到 4016
  - 密钥直接从进程内存提取，作为 AES-256 密钥使用 (无需 PBKDF2)
"""

import hashlib
import hmac as hmac_mod
import logging
import os
import sqlite3
import struct
import threading
import time
from datetime import datetime
from typing import Optional

from Crypto.Cipher import AES

from app.core.base import BaseDBReader, WeChatMessage

logger = logging.getLogger(__name__)

PAGE_SIZE = 4096
KEY_SIZE = 32
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVED_SIZE = 80  # IV (16) + HMAC (64)

MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3
MSG_TYPE_VOICE = 34
MSG_TYPE_CARD = 49
MSG_TYPE_SYSTEM = 10000


class MacOSDBReader(BaseDBReader):
    """macOS 平台 WeChat 数据库读取器。

    微信 4.x 使用 SQLCipher 4 加密本地数据库，密钥缓存在进程内存中
    (格式: x'<64hex_key><32hex_salt>')。密钥已预派生，直接作为 AES-256
    密钥用于页面解密，无需 PBKDF2 派生。
    """

    # macOS 微信数据目录查找路径 (新旧两种格式)
    MACOS_DATA_DIRS = [
        os.path.expanduser(
            "~/Library/Containers/com.tencent.xinWeChat/"
            "Data/Documents/xwechat_files/"
        ),
        os.path.expanduser(
            "~/Library/Containers/com.tencent.xinWeChat/"
            "Data/Library/Application Support/com.tencent.xinWeChat/"
        ),
    ]

    def __init__(self):
        self._enc_key: Optional[bytes] = None  # AES-256 密钥 (32 bytes)
        self._db_path: str = ""
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self._decrypted_path: str = ""
        self._lock = threading.Lock()
        # 缓存: 避免每次 poll 都重新扫描 600+ 表
        self._msg_table_cache: list[tuple[str, str]] | None = None  # [(table_name, username), ...]
        # 增量刷新：跟踪源文件变化
        self._source_mtime: float = 0.0
        self._source_size: int = 0
        self._last_refresh: float = 0.0

    # --- 公共接口 ---

    def open_db(self, db_path: str, key: bytes) -> bool:
        """打开并解密数据库。

        Args:
            db_path: 加密数据库文件路径。
            key: AES-256 密钥 (32 bytes，已从微信内存提取)。

        Returns:
            True 表示成功打开。
        """
        logger.info(f"打开数据库: {db_path}")

        if not os.path.exists(db_path):
            logger.error(f"数据库文件不存在: {db_path}")
            return False

        if len(key) != KEY_SIZE:
            logger.error(f"密钥长度错误: {len(key)} (期望 {KEY_SIZE})")
            return False

        self._db_path = db_path
        self._enc_key = key

        if not self._verify_key():
            logger.error("密钥验证失败 — 无法解密 page 1")
            return False

        try:
            self._decrypted_path = self._decrypt_to_temp()
            self._sqlite_conn = sqlite3.connect(
                f"file:{self._decrypted_path}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            self._sqlite_conn.row_factory = sqlite3.Row
            self._source_mtime = os.path.getmtime(db_path)
            self._source_size = os.path.getsize(db_path)
            self._last_refresh = 0.0
            logger.info("数据库打开成功 (macOS)")
            return True
        except Exception as exc:
            logger.error(f"打开数据库失败: {exc}")
            return False

    def is_message_db(self) -> bool:
        """验证当前打开的数据库是否包含消息表（Msg_%）。

        用于防止误打开 contact.db 等不含消息的非消息数据库。
        """
        if self._sqlite_conn is None:
            return False
        try:
            cursor = self._sqlite_conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'Msg_%'"
            )
            count = cursor.fetchone()[0]
            return count > 0
        except Exception:
            return False

    def is_contact_db(self) -> bool:
        """验证当前打开的数据库是否包含联系人表。"""
        if self._sqlite_conn is None:
            return False
        try:
            cursor = self._sqlite_conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name IN ('contact', 'chat_room')"
            )
            count = cursor.fetchone()[0]
            return count >= 1
        except Exception:
            return False

    # 最小刷新间隔（秒），避免频繁 WAL checkpoint 导致反复全量解密
    _MIN_REFRESH_INTERVAL = 15.0

    def _needs_refresh(self) -> bool:
        """检查是否需要刷新（不含锁，仅判断）。"""
        now = time.monotonic()
        if now - self._last_refresh < self._MIN_REFRESH_INTERVAL:
            return False
        try:
            cur_mtime = os.path.getmtime(self._db_path)
            cur_size = os.path.getsize(self._db_path)
        except OSError:
            return False
        if cur_mtime == self._source_mtime and cur_size == self._source_size:
            return False
        return True

    def _do_refresh(self) -> None:
        """执行原子刷新（调用方必须持有 _lock）。

        先解密到新临时文件，成功后再切过去，旧副本延迟清理。
        失败时保留旧连接，不影响正在进行的查询。
        """
        try:
            cur_mtime = os.path.getmtime(self._db_path)
            cur_size = os.path.getsize(self._db_path)
        except OSError:
            return

        logger.debug(
            f"源数据库已变化，刷新解密副本 "
            f"(size: {self._source_size} -> {cur_size})"
        )

        old_conn = self._sqlite_conn
        old_path = self._decrypted_path

        try:
            new_path = self._decrypt_to_temp()
            new_conn = sqlite3.connect(
                f"file:{new_path}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            new_conn.row_factory = sqlite3.Row
        except Exception as exc:
            logger.error(f"刷新解密副本失败 (保留旧副本): {exc}")
            return

        # 原子切换
        self._sqlite_conn = new_conn
        self._decrypted_path = new_path
        self._source_mtime = cur_mtime
        self._source_size = cur_size
        self._last_refresh = time.monotonic()
        self._msg_table_cache = None
        logger.debug("解密副本刷新成功")

        # 延迟清理旧副本（含 WAL/SHM/journal）
        if old_conn:
            try:
                old_conn.close()
            except Exception:
                pass
        if old_path and os.path.exists(old_path):
            try:
                os.unlink(old_path)
            except Exception:
                pass
            for suffix in ("-wal", "-shm", "-journal"):
                aux = old_path + suffix
                if os.path.exists(aux):
                    try:
                        os.unlink(aux)
                    except Exception:
                        pass

    def query_messages_since(self, timestamp: int) -> list[WeChatMessage]:
        """查询指定时间戳之后的消息。

        WeChat 4.x macOS schema: 每个会话一张 Msg_<md5> 表，
        通过 Name2Id 表映射 username -> MD5 hash。

        Args:
            timestamp: Unix 时间戳，毫秒或秒均可（自动检测）。
        """
        # 增量刷新 + 查询串行化：整个 DB 操作期间持有锁，
        # 防止其他线程的刷新关闭当前连接/删除临时文件导致 "disk image is malformed"
        with self._lock:
            if self._sqlite_conn is None:
                logger.error("数据库未打开")
                return []

            if self._needs_refresh():
                self._do_refresh()

            # 时间戳归一化: WeChat 4.x 的 create_time 是秒级
            if timestamp > 10000000000:  # 毫秒
                ts_sec = timestamp // 1000
            else:
                ts_sec = timestamp

            try:
                # 构建缓存：表名 -> username 映射
                if self._msg_table_cache is None:
                    hash_to_user: dict[str, str] = {}
                    try:
                        nc = self._sqlite_conn.execute(
                            "SELECT user_name FROM Name2Id"
                        )
                        for row in nc:
                            name = row["user_name"] or ""
                            if name:
                                h = hashlib.md5(name.encode()).hexdigest()
                                hash_to_user[h] = name
                    except Exception:
                        pass

                    tc = self._sqlite_conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name LIKE 'Msg_%'"
                    )
                    self._msg_table_cache = []
                    for r in tc:
                        tbl = r[0]
                        table_hash = tbl[4:]
                        username = hash_to_user.get(table_hash, "")
                        self._msg_table_cache.append((tbl, username))
                    logger.info(
                        f"消息表缓存已构建: {len(self._msg_table_cache)} 个会话表"
                    )
                    if not self._msg_table_cache:
                        all_tables = self._sqlite_conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                        logger.warning(
                            f"数据库中无 Msg_% 表，实际表: "
                            f"{[r[0] for r in all_tables[:20]]}"
                        )

                messages: list[WeChatMessage] = []
                scanned = 0

                for tbl, username in self._msg_table_cache:
                    try:
                        cursor = self._sqlite_conn.execute(
                            f'SELECT local_id, create_time, real_sender_id, '
                            f'message_content, local_type, source, '
                            f'status, origin_source, server_seq '
                            f'FROM "{tbl}" '
                            f'WHERE create_time > ? '
                            f'ORDER BY create_time ASC',
                            (ts_sec,),
                        )
                    except Exception:
                        continue

                    has_rows = False
                    for row in cursor:
                        has_rows = True
                        scanned += 1
                        if MacOSDBReader._is_self_sent_row(row):
                            continue

                        local_type = row["local_type"] or 0
                        if local_type != 1:
                            continue

                        content = row["message_content"] or ""
                        if isinstance(content, bytes):
                            try:
                                content = content.decode("utf-8", errors="replace")
                            except Exception:
                                content = str(content)
                        content = str(content)
                        if not content.strip():
                            continue
                        if MacOSDBReader._is_garbled(content):
                            continue

                        is_group = "@chatroom" in username
                        sender = username

                        if is_group:
                            sender = self._parse_group_sender(
                                row["source"], username
                            )

                        msg = WeChatMessage(
                            msg_id=f"{tbl}:{row['local_id']}",
                            msg_type=local_type,
                            content=content,
                            sender=sender,
                            room_id=username if is_group else "",
                            create_time=datetime.fromtimestamp(row["create_time"] or 0),
                            is_group=is_group,
                            at_list=[],
                        )
                        messages.append(msg)

                messages.sort(key=lambda m: m.create_time or datetime.min)
                if messages:
                    logger.info(
                        f"检测到 {len(messages)} 条新文本消息 "
                        f"(扫描 {len(self._msg_table_cache)} 个表, "
                        f"命中 {scanned} 行)"
                    )
                return messages

            except Exception as exc:
                logger.error(f"查询消息失败: {exc}")
                return []

    @staticmethod
    def _is_self_sent_row(row) -> bool:
        """识别当前账号自己发出的消息，避免回灌给自动回复。"""
        real_sender_id = row["real_sender_id"] or 0
        # Name2Id.rowid=1 是当前登录账号；其他 real_sender_id 是联系人或群成员。
        if real_sender_id == 1:
            return True

        status = row["status"] or 0
        origin_source = row["origin_source"] or 0
        server_seq = row["server_seq"] or 0
        return status == 2 and origin_source == 1 and server_seq == 0

    @staticmethod
    def _is_garbled(text: str) -> bool:
        """检测解码后的文本是否为乱码/二进制数据。"""
        if not text:
            return True
        # 统计不可打印字符比例
        bad = 0
        for ch in text:
            code = ord(ch)
            # U+FFFD 替换字符 + 控制字符 (除了常见空白)
            if code == 0xFFFD or (code < 0x20 and code not in (0x09, 0x0A, 0x0D)):
                bad += 1
        ratio = bad / len(text)
        return ratio > 0.3

    @staticmethod
    def _parse_group_sender(source_blob, fallback: str) -> str:
        """从 source protobuf 解析群聊消息的实际发送者。"""
        if not source_blob or not isinstance(source_blob, bytes):
            return fallback
        try:
            # 简单解析: 查找 wxid 或 @chatroom 模式
            import re
            text = source_blob.decode("utf-8", errors="replace")
            # 查找 wxid_xxx 模式
            match = re.search(r"wxid_[a-z0-9]+", text)
            if match:
                return match.group(0)
            # 查找 @openim 模式
            match = re.search(r"\d+@openim", text)
            if match:
                return match.group(0)
        except Exception:
            pass
        return fallback

    def get_contacts(self) -> list[dict]:
        """获取联系人列表 (从 contact.db 的 contact 表)。

        微信 4.x 联系人表结构:
        - username: 微信 ID (wxid_xxx 或 xxx@chatroom)
        - alias: 自定义微信号
        - nick_name: 昵称
        - remark: 备注
        - local_type: 本地类型 (0=好友, 其他值表示群聊等)

        过滤排除: 群聊、公众号、系统账号
        """
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        try:
            cursor = self._sqlite_conn.execute(
                """
                SELECT username, alias, nick_name, remark, local_type
                FROM contact
                WHERE username != ''
                  AND username NOT LIKE '%@chatroom'
                  AND username NOT LIKE 'gh_%'
                  AND username NOT IN ('notifymessage', 'weixin', 'qqmail',
                                       'medianote', 'filehelper', 'fmessage',
                                       'floatbottle', 'tmessage')
                ORDER BY nick_name
                """
            )

            contacts: list[dict] = []
            for row in cursor:
                contacts.append({
                    "wxid": row["username"] or "",
                    "alias": row["alias"] or "",
                    "nickname": row["nick_name"] or "",
                    "remark": row["remark"] or "",
                    "local_type": row["local_type"] or 0,
                })

            logger.debug(f"获取到 {len(contacts)} 个联系人")
            return contacts

        except Exception as exc:
            logger.error(f"获取联系人失败: {exc}")
            return []

    def get_chatrooms(self) -> list[dict]:
        """获取群聊列表 (从 contact.db 的 chat_room 表 + contact 表获取名称)。"""
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        try:
            # 从 contact 表获取群聊显示名 (nickname 或 remark)
            room_names: dict[str, str] = {}
            try:
                c_cursor = self._sqlite_conn.execute(
                    "SELECT username, nick_name, remark FROM contact WHERE username LIKE '%@chatroom'"
                )
                for c_row in c_cursor:
                    room_names[c_row["username"] or ""] = (
                        c_row["remark"] or c_row["nick_name"] or ""
                    )
            except Exception:
                pass

            cursor = self._sqlite_conn.execute(
                "SELECT username, owner, ext_buffer FROM chat_room WHERE username != ''"
            )

            rooms: list[dict] = []
            for row in cursor:
                room_id = row["username"] or ""
                name = room_names.get(room_id, "") or room_id
                owner = row["owner"] or ""
                rooms.append({
                    "room_id": room_id,
                    "name": name,
                    "owner": owner,
                })

            logger.debug(f"获取到 {len(rooms)} 个群聊")
            return rooms

        except Exception as exc:
            logger.error(f"获取群聊列表失败: {exc}")
            return []

    @classmethod
    def get_current_wxid(cls) -> str:
        """获取当前登录用户的 wxid。"""
        for base_dir in cls.MACOS_DATA_DIRS:
            if not os.path.exists(base_dir) or "xwechat_files" not in base_dir:
                continue
            try:
                for entry in os.scandir(base_dir):
                    if entry.is_dir() and entry.name.startswith("wxid_"):
                        wxid = entry.name.split("_6c")[0] if "_6c" in entry.name else entry.name
                        return wxid
            except OSError:
                continue
        return ""

    def get_my_messages(
        self,
        limit: int = 5000,
        since_days: int = 90,
    ) -> list[dict]:
        """提取当前用户发出的所有文本消息（用于风格分析）。

        WeChat 4.x 的 real_sender_id 对应 Name2Id.rowid，其中 rowid=1 是
        当前账号；不能把 real_sender_id=2 误认为自己，否则会混入联系人消息。

        Args:
            limit: 最大消息数。
            since_days: 提取最近 N 天的消息。

        Returns:
            [{content, create_time, room_id, is_group}, ...]
        """
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        import time as _time
        since_ts = int(_time.time()) - since_days * 86400

        with self._lock:
            if self._msg_table_cache is None:
                hash_to_user: dict[str, str] = {}
                try:
                    nc = self._sqlite_conn.execute("SELECT user_name FROM Name2Id")
                    for row in nc:
                        name = row["user_name"] or ""
                        if name:
                            h = hashlib.md5(name.encode()).hexdigest()
                            hash_to_user[h] = name
                except Exception:
                    pass

                tc = self._sqlite_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                )
                self._msg_table_cache = []
                for r in tc:
                    tbl = r[0]
                    table_hash = tbl[4:]
                    username = hash_to_user.get(table_hash, "")
                    self._msg_table_cache.append((tbl, username))

            messages: list[dict] = []

            for tbl, username in self._msg_table_cache:
                if len(messages) >= limit:
                    break

                try:
                    cursor = self._sqlite_conn.execute(
                        f'SELECT message_content, create_time, '
                        f'real_sender_id, status, origin_source, server_seq '
                        f'FROM "{tbl}" '
                        f'WHERE create_time > ? '
                        f'AND local_type = 1 '
                        f'AND (real_sender_id = 1 '
                        f'OR (status = 2 AND origin_source = 1 AND server_seq = 0)) '
                        f'ORDER BY create_time DESC '
                        f'LIMIT ?',
                        (since_ts, limit - len(messages)),
                    )
                except Exception:
                    continue

                is_group = "@chatroom" in username
                for row in cursor:
                    if not MacOSDBReader._is_self_sent_row(row):
                        continue

                    content = row["message_content"] or ""
                    if isinstance(content, bytes):
                        try:
                            content = content.decode("utf-8", errors="replace")
                        except Exception:
                            content = str(content)
                    content = str(content).strip()
                    if not content or len(content) < 2:
                        continue
                    if MacOSDBReader._is_garbled(content):
                        continue

                    messages.append({
                        "content": content,
                        "create_time": row["create_time"],
                        "room_id": username if is_group else "",
                        "is_group": is_group,
                    })

            messages.sort(key=lambda m: m["create_time"])
            logger.info(
                f"提取当前用户消息: {len(messages)} 条"
            )
            return messages

    def close(self) -> None:
        """关闭数据库连接并清理临时文件。"""
        with self._lock:
            if self._sqlite_conn:
                try:
                    self._sqlite_conn.close()
                except Exception as exc:
                    logger.debug(f"关闭数据库连接异常: {exc}")
                self._sqlite_conn = None

            if self._decrypted_path and os.path.exists(self._decrypted_path):
                try:
                    os.unlink(self._decrypted_path)
                    logger.debug("临时解密文件已清理")
                except Exception as exc:
                    logger.debug(f"清理临时文件异常: {exc}")
                # 清理关联的 WAL/SHM/journal 文件
                for suffix in ("-wal", "-shm", "-journal"):
                    aux = self._decrypted_path + suffix
                    if os.path.exists(aux):
                        try:
                            os.unlink(aux)
                        except Exception:
                            pass
                self._decrypted_path = ""

    def __del__(self) -> None:
        self.close()

    # --- 内部方法 ---

    @staticmethod
    def _derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
        """派生 HMAC 验证密钥。"""
        mac_salt = bytes(b ^ 0x3a for b in salt)
        return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SIZE)

    def _verify_key(self) -> bool:
        """验证密钥是否能解密 page 1。

        读取 page 1，用 AES-256-CBC 解密，检查解密后是否以
        SQLite 文件头开头，并通过 HMAC 验证。
        """
        if not self._enc_key:
            return False

        try:
            with open(self._db_path, "rb") as f:
                page1 = f.read(PAGE_SIZE)

            if len(page1) < PAGE_SIZE:
                logger.error("无法读取完整的 page 1")
                return False

            salt = page1[:SALT_SIZE]
            iv = page1[PAGE_SIZE - RESERVED_SIZE:PAGE_SIZE - RESERVED_SIZE + IV_SIZE]
            encrypted = page1[SALT_SIZE:PAGE_SIZE - RESERVED_SIZE]

            cipher = AES.new(self._enc_key, AES.MODE_CBC, iv=iv)
            decrypted = cipher.decrypt(encrypted)

            # 验证 SQLite 文件头
            if decrypted[:16] == b"SQLite format 3\x00":
                logger.info("密钥验证成功 (SQLite header match)")
                return True

            # HMAC 备选验证
            mac_key = self._derive_mac_key(self._enc_key, salt)
            hmac_data = page1[SALT_SIZE:PAGE_SIZE - RESERVED_SIZE + IV_SIZE]
            stored_hmac = page1[PAGE_SIZE - HMAC_SIZE:PAGE_SIZE]
            hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
            hm.update(struct.pack("<I", 1))  # page number
            if hm.digest() == stored_hmac:
                logger.info("密钥验证成功 (HMAC match)")
                return True

            logger.warning("密钥验证失败: 无法解密 page 1")
            return False

        except Exception as exc:
            logger.error(f"密钥验证失败: {exc}")
            return False

    @classmethod
    def _get_temp_dir(cls) -> str:
        """项目本地临时目录路径。"""
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "data", "tmp",
        )

    @classmethod
    def _get_temp_path(cls) -> str:
        """获取项目本地临时文件路径，自动创建目录并清理旧文件。"""
        tmp_dir = cls._get_temp_dir()
        os.makedirs(tmp_dir, exist_ok=True)
        # 清理超过 10 分钟的旧临时文件（防止崩溃残留）
        cls._cleanup_stale_temps()
        return os.path.join(
            tmp_dir,
            f"weix_decrypted_{os.getpid()}_{int(time.monotonic() * 1000)}.db",
        )

    @classmethod
    def cleanup_temp_files(cls, stale_seconds: int = 600) -> int:
        """清理过期的解密临时文件及其 WAL/SHM/journal 辅助文件。

        Returns:
            已删除的文件数量。
        """
        tmp_dir = cls._get_temp_dir()
        now = time.time()
        removed = 0
        try:
            for name in os.listdir(tmp_dir):
                if not name.startswith("weix_decrypted_"):
                    continue
                # 匹配 .db, .db-wal, .db-shm, .db-journal
                if not (
                    name.endswith(".db")
                    or name.endswith(".db-wal")
                    or name.endswith(".db-shm")
                    or name.endswith(".db-journal")
                ):
                    continue
                fpath = os.path.join(tmp_dir, name)
                try:
                    if now - os.path.getmtime(fpath) > stale_seconds:
                        os.unlink(fpath)
                        removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        if removed:
            logger.info(f"已清理 {removed} 个临时解密文件")
        return removed

    @classmethod
    def _cleanup_stale_temps(cls) -> None:
        """删除超过 10 分钟的旧临时文件（委托给 cleanup_temp_files）。"""
        cls.cleanup_temp_files(stale_seconds=600)

    def _decrypt_to_temp(self) -> str:
        """将加密数据库解密到项目本地临时文件。

        页面布局 (SQLCipher 4, cipher_compatibility=4):
        - Page 1: [salt:16][encrypted:4016-16][IV:16][HMAC:64]
        - Other:  [encrypted:4016][IV:16][HMAC:64]
        - 解密后: [SQLite header + data][zero padding 80]
        """
        if not self._enc_key:
            raise RuntimeError("AES 密钥未设置")

        file_size = os.path.getsize(self._db_path)
        total_pages = (file_size + PAGE_SIZE - 1) // PAGE_SIZE

        tmp_path = self._get_temp_path()

        try:
            with open(self._db_path, "rb") as src, open(tmp_path, "wb") as dst:
                for page_num in range(total_pages):
                    page_data = src.read(PAGE_SIZE)
                    if len(page_data) < PAGE_SIZE:
                        dst.write(page_data)
                        continue

                    iv = page_data[
                        PAGE_SIZE - RESERVED_SIZE:PAGE_SIZE - RESERVED_SIZE + IV_SIZE
                    ]

                    if page_num == 0:
                        encrypted = page_data[SALT_SIZE:PAGE_SIZE - RESERVED_SIZE]
                        cipher = AES.new(self._enc_key, AES.MODE_CBC, iv=iv)
                        decrypted = cipher.decrypt(encrypted)
                        page = (
                            b"SQLite format 3\x00"
                            + decrypted
                            + b"\x00" * RESERVED_SIZE
                        )
                    else:
                        encrypted = page_data[:PAGE_SIZE - RESERVED_SIZE]
                        cipher = AES.new(self._enc_key, AES.MODE_CBC, iv=iv)
                        decrypted = cipher.decrypt(encrypted)
                        page = decrypted + b"\x00" * RESERVED_SIZE

                    dst.write(page)

                    if page_num % 1000 == 0 and page_num > 0:
                        logger.debug(f"解密进度: {page_num}/{total_pages} 页")

            logger.info(f"数据库解密完成 ({total_pages} 页) -> {tmp_path}")
            return tmp_path

        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    @classmethod
    def find_database_files(cls, wxid: str = "") -> list[str]:
        """查找 macOS 上指定 wxid 的所有数据库文件。

        支持新旧两种微信数据目录结构:
        - 新: xwechat_files/<wxid>/db_storage/<category>/<db>.db
        - 旧: Application Support/<version>/<wxid>/Message/<db>.db
        """
        db_files: list[str] = []

        for base_dir in cls.MACOS_DATA_DIRS:
            if not os.path.exists(base_dir):
                continue

            if "xwechat_files" in base_dir:
                for wxid_entry in os.scandir(base_dir):
                    if not wxid_entry.is_dir():
                        continue
                    if wxid and wxid_entry.name != wxid:
                        continue

                    storage = os.path.join(wxid_entry.path, "db_storage")
                    if not os.path.isdir(storage):
                        continue

                    for root, _dirs, files in os.walk(storage):
                        for fname in files:
                            if fname.endswith(".db"):
                                db_files.append(os.path.join(root, fname))
            else:
                for entry in os.scandir(base_dir):
                    if not entry.is_dir():
                        continue
                    if "." not in entry.name:
                        continue

                    for wxid_entry in os.scandir(entry.path):
                        if not wxid_entry.is_dir():
                            continue
                        if wxid and wxid_entry.name != wxid:
                            continue

                        msg_dir = os.path.join(wxid_entry.path, "Message")
                        if os.path.exists(msg_dir):
                            for db_entry in os.scandir(msg_dir):
                                if db_entry.name.endswith(".db"):
                                    db_files.append(db_entry.path)

                        msg_dir_v2 = os.path.join(wxid_entry.path, "Msg")
                        if os.path.exists(msg_dir_v2):
                            for db_entry in os.scandir(msg_dir_v2):
                                if db_entry.name.endswith(".db"):
                                    db_files.append(db_entry.path)

        return db_files
