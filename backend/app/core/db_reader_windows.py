"""Windows 平台 WeChat 数据库读取器。

使用 pycryptodome 的 AES-256-CBC 解密 SQLCipher4 加密的 SQLite 数据库页面，
以只读模式读取微信消息和联系人数据。
"""

import hashlib
import hmac as hmac_mod
import logging
import os
import sqlite3
import struct
import tempfile
import threading
import time
from datetime import datetime
from typing import ClassVar, Optional

from Crypto.Cipher import AES
from Crypto.Hash import SHA512
from Crypto.Protocol.KDF import PBKDF2

from app.core.base import BaseDBReader, WeChatMessage
from app.core.wechat_paths_windows import find_wechat_data_dirs

logger = logging.getLogger(__name__)

# SQLCipher4 页面大小
PAGE_SIZE = 4096
KEY_SIZE = 32
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
# SQLCipher4 HMAC-SHA512: IV(16) + HMAC(64)
RESERVED_SIZE = 80
SQLITE_HEADER = b"SQLite format 3\x00"
# 消息类型常量
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3
MSG_TYPE_VOICE = 34
MSG_TYPE_CARD = 49
MSG_TYPE_SYSTEM = 10000


class WindowsDBReader(BaseDBReader):
    """Windows 平台 WeChat 数据库读取器。

    使用 pycryptodome 的 AES-256-CBC 手动解密 SQLCipher4 加密的数据库页面。
    在每个查询中按需解密所需页面，避免全量解密。
    """

    # Windows 微信数据目录（常见路径，运行时会动态补充注册表和进程路径）
    WINDOWS_DATA_DIRS: ClassVar[list[str]] = [
        os.path.expandvars(r"%USERPROFILE%\Documents\WeChat Files"),
        os.path.expandvars(r"%USERPROFILE%\Documents\xwechat_files"),
        os.path.expandvars(r"%APPDATA%\Tencent\WeChat"),
        os.path.expandvars(r"%APPDATA%\Tencent\WeChat\WeChat Files"),
        os.path.expandvars(r"%APPDATA%\Tencent\WeChat\xwechat_files"),
        os.path.expandvars(r"%LOCALAPPDATA%\Tencent\WeChat"),
        os.path.expandvars(r"%LOCALAPPDATA%\Tencent\WeChat\WeChat Files"),
        os.path.expandvars(r"%LOCALAPPDATA%\Tencent\WeChat\xwechat_files"),
        r"D:\WeChat Files",
        r"D:\xwechat_files",
        r"E:\WeChat Files",
        r"E:\xwechat_files",
    ]

    def __init__(self):
        self._key: Optional[bytes] = None
        self._aes_key: Optional[bytes] = None
        self._hmac_key: Optional[bytes] = None
        self._db_path: str = ""
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self._decrypted_path: str = ""
        self._iterations: int = 256000
        self._reserve_size: int = RESERVED_SIZE
        self._hmac_hash: str = "sha512"
        self._key_mode: str = ""
        self._lock = threading.Lock()
        self._msg_table_cache: Optional[list[tuple[str, str]]] = None

    # --- 公共接口 ---

    def open_db(self, db_path: str, key: bytes) -> bool:
        """打开并解密数据库。

        Args:
            db_path: 加密数据库文件路径。
            key: 原始密钥字节 (32 bytes)。

        Returns:
            True 表示成功打开。
        """
        logger.info(f"打开数据库: {db_path}")

        if not os.path.exists(db_path):
            logger.error(f"数据库文件不存在: {db_path}")
            return False

        self._db_path = db_path
        self._key = key

        # 派生加密密钥
        if not self._derive_key():
            return False

        # 解密整个数据库到临时文件
        try:
            self._decrypted_path = self._decrypt_to_temp()
            self._sqlite_conn = sqlite3.connect(
                f"file:{self._decrypted_path}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            self._sqlite_conn.row_factory = sqlite3.Row
            logger.info("数据库打开成功")
            return True
        except Exception as exc:
            logger.error(f"打开数据库失败: {exc}")
            return False

    def query_messages_since(self, timestamp: int) -> list[WeChatMessage]:
        """查询指定时间戳之后的消息。

        Args:
            timestamp: Unix 时间戳。

        Returns:
            WeChatMessage 列表。
        """
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        if self._has_msg_shard_tables():
            return self._query_v4_messages_since(timestamp)

        try:
            cursor = self._sqlite_conn.execute(
                """
                SELECT msg_id, msg_content, msg_type, msg_talker,
                       msg_create_time, chatroom_id, at_list
                FROM MSG
                WHERE msg_create_time > ?
                ORDER BY msg_create_time ASC
                """,
                (timestamp,),
            )

            messages: list[WeChatMessage] = []
            for row in cursor:
                msg_type = row["msg_type"] or 0
                is_group = False
                room_id = ""
                sender = row["msg_talker"] or ""

                # 判断群聊: talker 以 @chatroom 结尾
                if sender and "@chatroom" in str(sender):
                    is_group = True
                    room_id = str(sender)

                # 解析 @ 列表
                at_list: list[str] = []
                at_raw = row["at_list"] or ""
                if at_raw:
                    try:
                        at_list = str(at_raw).split(",")
                    except Exception:
                        pass

                content = row["msg_content"] or ""
                # 处理二进制内容
                if isinstance(content, bytes):
                    try:
                        content = content.decode("utf-8", errors="replace")
                    except Exception:
                        content = str(content)

                msg = WeChatMessage(
                    msg_id=str(row["msg_id"] or ""),
                    msg_type=msg_type,
                    content=str(content),
                    sender=str(sender),
                    room_id=room_id,
                    create_time=datetime.fromtimestamp(
                        (row["msg_create_time"] or 0) / 1000.0
                    ),
                    is_group=is_group,
                    at_list=at_list,
                )
                messages.append(msg)

            logger.debug(
                f"查询到 {len(messages)} 条消息 (timestamp > {timestamp})"
            )
            return messages

        except Exception as exc:
            logger.error(f"查询消息失败: {exc}")
            return []

    def _query_v4_messages_since(self, timestamp: int) -> list[WeChatMessage]:
        """查询 Windows Weixin 4.x Msg_<md5> 分表消息。"""
        if self._sqlite_conn is None:
            return []

        ts_sec = timestamp // 1000 if timestamp > 10000000000 else timestamp
        try:
            msg_tables = self._get_v4_msg_tables()
            messages: list[WeChatMessage] = []
            scanned = 0

            for table, username in msg_tables:
                try:
                    cursor = self._sqlite_conn.execute(
                        f'SELECT local_id, create_time, real_sender_id, '
                        f'message_content, local_type, source, '
                        f'status, origin_source, server_seq '
                        f'FROM "{table}" '
                        f'WHERE create_time > ? '
                        f'ORDER BY create_time ASC',
                        (ts_sec,),
                    )
                except Exception:
                    continue

                for row in cursor:
                    scanned += 1
                    if self._is_self_sent_v4_row(row):
                        continue
                    local_type = row["local_type"] or 0
                    if local_type != MSG_TYPE_TEXT:
                        continue

                    content = self._decode_message_content(row["message_content"])
                    if not content.strip() or self._is_garbled(content):
                        continue

                    is_group = "@chatroom" in username
                    sender = username
                    if is_group:
                        sender = self._parse_group_sender(row["source"], username)

                    messages.append(
                        WeChatMessage(
                            msg_id=f"{table}:{row['local_id']}",
                            msg_type=local_type,
                            content=content,
                            sender=sender,
                            room_id=username if is_group else "",
                            create_time=datetime.fromtimestamp(row["create_time"] or 0),
                            is_group=is_group,
                            at_list=[],
                        )
                    )

            messages.sort(key=lambda msg: msg.create_time or datetime.min)
            if messages:
                logger.info(
                    f"检测到 {len(messages)} 条 Windows 4.x 新文本消息 "
                    f"(扫描 {len(msg_tables)} 个表, 命中 {scanned} 行)"
                )
            return messages
        except Exception as exc:
            logger.error(f"查询 Windows 4.x 消息失败: {exc}")
            return []

    def get_contacts(self) -> list[dict]:
        """获取联系人列表。

        Returns:
            联系人字典列表，包含 wxid, name, remark, type 等字段。
        """
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        try:
            if self._is_v4_contact_schema():
                return self._get_contacts_v4()

            cursor = self._sqlite_conn.execute(
                """
                SELECT UserName, Alias, NickName, Remark, Type,
                       HeadImgUrl, ChatRoomType
                FROM Contact
                WHERE UserName != ''
                ORDER BY NickName
                """
            )

            contacts: list[dict] = []
            for row in cursor:
                contact = {
                    "wxid": row["UserName"] or "",
                    "alias": row["Alias"] or "",
                    "nickname": row["NickName"] or "",
                    "remark": row["Remark"] or "",
                    "type": row["Type"] or 0,
                    "head_img_url": row["HeadImgUrl"] or "",
                    "chatroom_type": row["ChatRoomType"] or 0,
                }
                contacts.append(contact)

            logger.debug(f"获取到 {len(contacts)} 个联系人")
            return contacts

        except Exception as exc:
            logger.error(f"获取联系人失败: {exc}")
            return []

    def _get_contacts_v4(self) -> list[dict]:
        """微信 4.x schema: contact 表，小写列名。"""
        cursor = self._sqlite_conn.execute(
            """
            SELECT username, alias, nick_name, remark, local_type,
                   big_head_url, small_head_url, chat_room_type
            FROM contact
            WHERE username != '' AND delete_flag = 0
            ORDER BY nick_name
            """
        )
        contacts: list[dict] = []
        for row in cursor:
            contact = {
                "wxid": row["username"] or "",
                "alias": row["alias"] or "",
                "nickname": row["nick_name"] or "",
                "remark": row["remark"] or "",
                "type": row["local_type"] or 0,
                "head_img_url": row["big_head_url"] or row["small_head_url"] or "",
                "chatroom_type": row["chat_room_type"] or 0,
            }
            contacts.append(contact)
        logger.debug(f"获取到 {len(contacts)} 个联系人 (V4 schema)")
        return contacts

    def get_chatrooms(self) -> list[dict]:
        """获取群聊列表。

        Returns:
            群聊字典列表。
        """
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        try:
            if self._is_v4_contact_schema():
                return self._get_chatrooms_v4()

            cursor = self._sqlite_conn.execute(
                """
                SELECT ChatRoomName, UserNameList, DisplayNameList,
                       ChatRoomOwner, MemberCount
                FROM ChatRoom
                WHERE ChatRoomName != ''
                """
            )

            rooms: list[dict] = []
            for row in cursor:
                room = {
                    "room_id": row["ChatRoomName"] or "",
                    "members": (row["UserNameList"] or "").split(";"),
                    "display_names": (row["DisplayNameList"] or "").split(";"),
                    "owner": row["ChatRoomOwner"] or "",
                    "member_count": row["MemberCount"] or 0,
                }
                rooms.append(room)

            logger.debug(f"获取到 {len(rooms)} 个群聊")
            return rooms

        except Exception as exc:
            logger.error(f"获取群聊列表失败: {exc}")
            return []

    def _get_chatrooms_v4(self) -> list[dict]:
        """微信 4.x schema: chat_room + chatroom_member + contact。"""
        rooms: list[dict] = []
        room_rows = self._sqlite_conn.execute(
            "SELECT id, username, owner FROM chat_room WHERE username != ''"
        ).fetchall()

        for rr in room_rows:
            room_id = rr["username"] or ""
            room_pk = rr["id"]
            owner = rr["owner"] or ""
            if not room_id:
                continue

            member_count = self._sqlite_conn.execute(
                "SELECT COUNT(*) FROM chatroom_member WHERE room_id = ?",
                (room_pk,),
            ).fetchone()[0]

            # 群名来源：contact 表的 nick_name（群主设置的真实群名）
            name = ""
            contact_row = self._sqlite_conn.execute(
                "SELECT nick_name, remark FROM contact WHERE username = ?",
                (room_id,),
            ).fetchone()
            if contact_row:
                name = contact_row["nick_name"] or contact_row["remark"] or ""

            # 没有群名时用前几个成员昵称拼凑
            if not name:
                member_names = self._sqlite_conn.execute(
                    """
                    SELECT c.nick_name
                    FROM chatroom_member cm
                    JOIN contact c ON c.id = cm.member_id
                    WHERE cm.room_id = ?
                    LIMIT 5
                    """,
                    (room_pk,),
                ).fetchall()
                names = [m["nick_name"] for m in member_names if m["nick_name"]]
                if names:
                    name = "、".join(names[:5])
                    if member_count > 5:
                        name += f"...({member_count}人)"

            rooms.append({
                "room_id": room_id,
                "members": [],
                "display_names": [],
                "owner": owner,
                "member_count": member_count,
                "name": name,
            })

        logger.debug(f"获取到 {len(rooms)} 个群聊 (V4 schema)")
        return rooms

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

    # --- 平台通用方法 ---

    def is_message_db(self) -> bool:
        """验证当前打开的数据库是否包含消息表（MSG 或 Msg_% 表）。"""
        if self._sqlite_conn is None:
            return False
        try:
            cursor = self._sqlite_conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND (name='MSG' OR name LIKE 'Msg_%')"
            )
            count = cursor.fetchone()[0]
            return count > 0
        except Exception:
            return False

    def _is_v4_contact_schema(self) -> bool:
        """检测是否为微信 4.x 联系人 schema（小写表名）。"""
        if self._sqlite_conn is None:
            return False
        try:
            cursor = self._sqlite_conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='contact'"
            )
            return cursor.fetchone() is not None
        except Exception:
            return False

    def is_contact_db(self) -> bool:
        """验证当前打开的数据库是否包含联系人表。"""
        if self._sqlite_conn is None:
            return False
        try:
            cursor = self._sqlite_conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name IN ('Contact', 'ChatRoom', 'contact', 'chat_room')"
            )
            count = cursor.fetchone()[0]
            return count >= 1
        except Exception:
            return False

    @classmethod
    def get_current_wxid(cls) -> str:
        """获取当前登录用户的 wxid。

        通过扫描微信数据目录中 wxid_ 开头的文件夹获取。
        """
        for expanded in cls._get_data_dirs():
            try:
                for entry in os.scandir(expanded):
                    if not entry.is_dir():
                        continue
                    if entry.name.startswith("wxid_"):
                        return entry.name
                    if os.path.isdir(os.path.join(entry.path, "db_storage")):
                        return entry.name
                    if os.path.isdir(os.path.join(entry.path, "Msg")):
                        return entry.name
            except OSError:
                continue
        return ""

    @classmethod
    def find_database_files(cls, wxid: str = "") -> list[str]:
        """查找 Windows 上指定 wxid 的所有 .db 数据库文件。

        支持两种目录结构:
        - WeChat Files/<wxid>/Msg/...
        - xwechat_files/<wxid>/db_storage/<category>/<db>.db
        """
        db_files: list[str] = []

        for expanded in cls._get_data_dirs():
            try:
                for wxid_entry in os.scandir(expanded):
                    if not wxid_entry.is_dir():
                        continue
                    if wxid and wxid_entry.name != wxid:
                        continue

                    storage = os.path.join(wxid_entry.path, "db_storage")
                    if os.path.isdir(storage):
                        for root, _dirs, files in os.walk(storage):
                            for fname in files:
                                if fname.endswith(".db"):
                                    db_files.append(os.path.join(root, fname))
                        continue

                    msg_dir = os.path.join(wxid_entry.path, "Msg")
                    if os.path.isdir(msg_dir):
                        for root, _dirs, files in os.walk(msg_dir):
                            for fname in files:
                                if fname.endswith(".db"):
                                    db_files.append(os.path.join(root, fname))
            except OSError:
                continue

        return db_files

    @classmethod
    def _get_data_dirs(cls) -> list[str]:
        """Return discovered Windows WeChat data roots plus legacy fallbacks."""
        discovered = [item.path for item in find_wechat_data_dirs()]
        candidates = discovered + [
            os.path.expandvars(path) for path in cls.WINDOWS_DATA_DIRS
        ]

        data_dirs: list[str] = []
        seen: set[str] = set()
        for path in candidates:
            if not path:
                continue
            norm = os.path.normpath(path)
            key = os.path.normcase(norm)
            if key in seen or not os.path.isdir(norm):
                continue
            seen.add(key)
            data_dirs.append(norm)
        return data_dirs

    @classmethod
    def cleanup_temp_files(cls, stale_seconds: int = 600) -> int:
        """清理过期的解密临时文件及其 WAL/SHM/journal 辅助文件。

        Returns:
            已删除的文件数量。
        """
        tmp_dir = tempfile.gettempdir()
        now = time.time()
        removed = 0
        try:
            for name in os.listdir(tmp_dir):
                if not name.startswith("weix_decrypted_"):
                    continue
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

    def get_my_messages(
        self,
        limit: int = 5000,
        since_days: int = 90,
    ) -> list[dict]:
        """提取当前用户发出的所有文本消息（用于风格分析）。

        Windows 版 MSG 表中需要根据 talker 和 is_sender 字段判断。
        """
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        if self._has_msg_shard_tables():
            return self._get_my_v4_messages(limit=limit, since_days=since_days)

        since_ts = int(time.time()) - since_days * 86400

        try:
            cursor = self._sqlite_conn.execute(
                """
                SELECT msg_content, msg_create_time, msg_talker
                FROM MSG
                WHERE msg_create_time > ?
                AND msg_type = ?
                AND is_sender = 1
                ORDER BY msg_create_time DESC
                LIMIT ?
                """,
                (since_ts, MSG_TYPE_TEXT, limit),
            )

            messages: list[dict] = []
            for row in cursor:
                talker = row["msg_talker"] or ""
                is_group = "@chatroom" in str(talker)

                content = row["msg_content"] or ""
                if isinstance(content, bytes):
                    try:
                        content = content.decode("utf-8", errors="replace")
                    except Exception:
                        content = str(content)
                content = str(content).strip()
                if not content or len(content) < 2:
                    continue

                messages.append({
                    "content": content,
                    "create_time": row["msg_create_time"],
                    "room_id": str(talker) if is_group else "",
                    "is_group": is_group,
                })

            logger.info(f"提取当前用户消息: {len(messages)} 条")
            return messages

        except Exception as exc:
            logger.error(f"提取当前用户消息失败: {exc}")
            return []

    def _get_my_v4_messages(
        self,
        limit: int = 5000,
        since_days: int = 90,
    ) -> list[dict]:
        """提取 Windows Weixin 4.x 当前用户发出的文本消息。"""
        if self._sqlite_conn is None:
            return []

        since_ts = int(time.time()) - since_days * 86400
        messages: list[dict] = []

        try:
            for table, username in self._get_v4_msg_tables():
                if len(messages) >= limit:
                    break
                try:
                    cursor = self._sqlite_conn.execute(
                        f'SELECT message_content, create_time, real_sender_id, '
                        f'status, origin_source, server_seq '
                        f'FROM "{table}" '
                        f'WHERE create_time > ? '
                        f'AND local_type = 1 '
                        f'ORDER BY create_time DESC '
                        f'LIMIT ?',
                        (since_ts, limit - len(messages)),
                    )
                except Exception:
                    continue

                is_group = "@chatroom" in username
                for row in cursor:
                    if not self._is_self_sent_v4_row(row):
                        continue
                    content = self._decode_message_content(row["message_content"]).strip()
                    if not content or len(content) < 2 or self._is_garbled(content):
                        continue
                    messages.append({
                        "content": content,
                        "create_time": row["create_time"],
                        "room_id": username if is_group else "",
                        "is_group": is_group,
                    })

            messages.sort(key=lambda item: item["create_time"])
            logger.info(f"提取 Windows 4.x 当前用户消息: {len(messages)} 条")
            return messages
        except Exception as exc:
            logger.error(f"提取 Windows 4.x 当前用户消息失败: {exc}")
            return []

    # --- 内部方法 ---

    def _has_msg_shard_tables(self) -> bool:
        if self._sqlite_conn is None:
            return False
        try:
            row = self._sqlite_conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'Msg_%' LIMIT 1"
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def _get_v4_msg_tables(self) -> list[tuple[str, str]]:
        if self._sqlite_conn is None:
            return []
        if self._msg_table_cache is not None:
            return self._msg_table_cache

        hash_to_user: dict[str, str] = {}
        try:
            cursor = self._sqlite_conn.execute("SELECT user_name FROM Name2Id")
            for row in cursor:
                username = row["user_name"] or ""
                if username:
                    hash_to_user[hashlib.md5(username.encode()).hexdigest()] = username
        except Exception:
            pass

        self._msg_table_cache = []
        cursor = self._sqlite_conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'Msg_%'"
        )
        for row in cursor:
            table = row[0]
            username = hash_to_user.get(table[4:], "")
            self._msg_table_cache.append((table, username))
        logger.info(f"Windows 4.x 消息表缓存已构建: {len(self._msg_table_cache)} 个会话表")
        return self._msg_table_cache

    @staticmethod
    def _is_self_sent_v4_row(row) -> bool:
        real_sender_id = row["real_sender_id"] or 0
        if real_sender_id == 1:
            return True
        status = row["status"] or 0
        origin_source = row["origin_source"] or 0
        server_seq = row["server_seq"] or 0
        return status == 2 and origin_source == 1 and server_seq == 0

    @staticmethod
    def _decode_message_content(content) -> str:
        if content is None:
            return ""
        if isinstance(content, bytes):
            try:
                return content.decode("utf-8", errors="replace")
            except Exception:
                return str(content)
        return str(content)

    @staticmethod
    def _is_garbled(text: str) -> bool:
        if not text:
            return True
        bad = 0
        for ch in text:
            code = ord(ch)
            if code == 0xFFFD or (code < 0x20 and code not in (0x09, 0x0A, 0x0D)):
                bad += 1
        return bad / len(text) > 0.3

    @staticmethod
    def _parse_group_sender(source_blob, fallback: str) -> str:
        if not source_blob or not isinstance(source_blob, bytes):
            return fallback
        try:
            import re

            text = source_blob.decode("utf-8", errors="replace")
            match = re.search(r"wxid_[a-z0-9]+", text)
            if match:
                return match.group(0)
            match = re.search(r"\d+@openim", text)
            if match:
                return match.group(0)
        except Exception:
            pass
        return fallback

    @staticmethod
    def _derive_mac_key(enc_key: bytes, salt: bytes, hash_name: str = "sha512") -> bytes:
        """派生 SQLCipher 4 HMAC 校验密钥。"""
        mac_salt = bytes(b ^ 0x3A for b in salt)
        return hashlib.pbkdf2_hmac(hash_name, enc_key, mac_salt, 2, dklen=KEY_SIZE)

    @staticmethod
    def _looks_like_decrypted_page1(decrypted: bytes) -> bool:
        """识别 SQLCipher page 1 解密后的常见明文形态。"""
        return (
            decrypted[:16] == SQLITE_HEADER
            or decrypted[:2] == b"\x10\x00"
        )

    @staticmethod
    def _rebuild_page1(decrypted: bytes) -> bytes:
        """把 page 1 解密片段还原成普通 SQLite page。"""
        if decrypted[:16] == SQLITE_HEADER:
            padding = b"\x00" * (PAGE_SIZE - len(decrypted))
            return decrypted + padding
        return SQLITE_HEADER + decrypted + b"\x00" * RESERVED_SIZE

    def _derive_key(self) -> bool:
        """从原始密钥派生 AES 和 HMAC 密钥。

        新版 WeChat 4.x 内存中的 key 通常已是 AES-256 页面密钥；
        旧格式则可能还需要 PBKDF2 派生。这里按 direct AES -> PBKDF2
        的顺序尝试，确保和提取器验证逻辑一致。
        """
        if not self._key:
            logger.error("缺少原始密钥")
            return False

        try:
            with open(self._db_path, "rb") as f:
                page1 = f.read(PAGE_SIZE)

            if len(page1) < PAGE_SIZE:
                logger.error("无法读取完整的 page 1")
                return False

            salt = page1[:16]  # SQLCipher salt
            direct_modes = [
                (80, "sha512"),
                (48, "sha1"),
            ]
            for reserve_size, hash_name in direct_modes:
                try:
                    reserved = page1[PAGE_SIZE - reserve_size:PAGE_SIZE]
                    iv = reserved[:IV_SIZE]
                    encrypted = page1[SALT_SIZE:PAGE_SIZE - reserve_size]
                    cipher = AES.new(self._key, AES.MODE_CBC, iv=iv)
                    decrypted = cipher.decrypt(encrypted)
                    hmac_ok = self._verify_page_hmac(
                        page1,
                        self._derive_mac_key(self._key, salt, hash_name),
                        reserve_size,
                        hash_name,
                    )
                    if self._looks_like_decrypted_page1(decrypted) and hmac_ok:
                        self._aes_key = self._key
                        self._hmac_key = self._derive_mac_key(self._key, salt, hash_name)
                        self._iterations = 0
                        self._reserve_size = reserve_size
                        self._hmac_hash = hash_name
                        self._key_mode = "direct"
                        logger.info(
                            f"密钥验证成功 (direct AES, reserve={reserve_size}, hmac={hash_name})"
                        )
                        return True
                except Exception as exc:
                    logger.debug(f"direct AES 验证失败: {exc}")

            # 尝试多种迭代次数
            for iterations, hash_name, reserve_size in [
                (256000, "sha512", 80),
                (64000, "sha1", 48),
                (4000, "sha1", 48),
            ]:
                try:
                    hmac_module = SHA512 if hash_name == "sha512" else __import__(
                        "Crypto.Hash.SHA1", fromlist=["SHA1"]
                    )
                    derived = PBKDF2(
                        self._key,
                        salt,
                        dkLen=KEY_SIZE,
                        count=iterations,
                        hmac_hash_module=hmac_module,
                    )
                    mac_key = PBKDF2(
                        derived,
                        bytes(b ^ 0x3A for b in salt),
                        dkLen=KEY_SIZE,
                        count=2,
                        hmac_hash_module=hmac_module,
                    )
                    # 验证: 使用派生密钥解密 page 1
                    reserved = page1[PAGE_SIZE - reserve_size:PAGE_SIZE]
                    iv = reserved[:IV_SIZE]
                    encrypted = page1[SALT_SIZE:PAGE_SIZE - reserve_size]
                    aes_key = derived
                    cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
                    decrypted = cipher.decrypt(encrypted)

                    if self._looks_like_decrypted_page1(decrypted) and self._verify_page_hmac(
                        page1, mac_key, reserve_size, hash_name
                    ):
                        self._aes_key = aes_key
                        self._hmac_key = mac_key
                        self._iterations = iterations
                        self._reserve_size = reserve_size
                        self._hmac_hash = hash_name
                        self._key_mode = "pbkdf2"
                        logger.info(
                            f"密钥派生成功 (iterations={iterations}, hmac={hash_name}, reserve={reserve_size})"
                        )
                        return True

                except Exception as exc:
                    logger.debug(
                        f"迭代 {iterations} 密钥派生失败: {exc}"
                    )
                    continue

            logger.error("所有迭代次数的密钥派生均失败")
            return False

        except Exception as exc:
            logger.error(f"密钥派生失败: {exc}")
            return False

    @staticmethod
    def _verify_page_hmac(
        page: bytes,
        mac_key: bytes,
        reserve_size: int,
        hash_name: str,
    ) -> bool:
        if hash_name == "sha512":
            digestmod = hashlib.sha512
            stored = page[PAGE_SIZE - reserve_size + IV_SIZE:PAGE_SIZE]
            data = page[SALT_SIZE:PAGE_SIZE - reserve_size + IV_SIZE]
        else:
            digestmod = hashlib.sha1
            first = page[SALT_SIZE:PAGE_SIZE]
            stored = first[-32:-12]
            data = first[:-32]

        calculated = hmac_mod.new(mac_key, data, digestmod)
        calculated.update(struct.pack("<I", 1))
        return calculated.digest() == stored

    def _decrypt_to_temp(self) -> str:
        """将整个加密数据库解密到临时文件。

        Returns:
            临时文件路径。
        """
        if not self._aes_key:
            raise RuntimeError("AES 密钥未派生")

        file_size = os.path.getsize(self._db_path)
        total_pages = (file_size + PAGE_SIZE - 1) // PAGE_SIZE

        tmp = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False, prefix="weix_decrypted_"
        )
        tmp_path = tmp.name

        try:
            with open(self._db_path, "rb") as src:
                for page_num in range(total_pages):
                    page_data = src.read(PAGE_SIZE)
                    if len(page_data) < PAGE_SIZE:
                        # 最后一页可能不足 4096 字节，直接写入
                        tmp.write(page_data)
                        continue

                    reserved = page_data[PAGE_SIZE - self._reserve_size:PAGE_SIZE]
                    iv = reserved[:16]
                    if page_num == 0:
                        encrypted = page_data[SALT_SIZE:PAGE_SIZE - self._reserve_size]
                    else:
                        encrypted = page_data[:PAGE_SIZE - self._reserve_size]

                    cipher = AES.new(self._aes_key, AES.MODE_CBC, iv=iv)
                    decrypted = cipher.decrypt(encrypted)

                    if page_num == 0:
                        decrypted_page = self._rebuild_page1(decrypted)
                    else:
                        decrypted_page = decrypted + b"\x00" * self._reserve_size
                    tmp.write(decrypted_page)

                    if page_num % 1000 == 0 and page_num > 0:
                        logger.debug(
                            f"解密进度: {page_num}/{total_pages} 页"
                        )

            logger.info(
                f"数据库解密完成 ({total_pages} 页) -> {tmp_path}"
            )
            tmp.close()
            return tmp_path

        except Exception:
            # 出错时清理临时文件
            tmp.close()
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    # --- 辅助查询方法 ---

    def get_table_info(self, table_name: str) -> list[dict]:
        """获取表结构信息 (PRAGMA table_info)。"""
        if self._sqlite_conn is None:
            return []

        try:
            cursor = self._sqlite_conn.execute(
                f"PRAGMA table_info({table_name})"
            )
            return [dict(row) for row in cursor]
        except Exception as exc:
            logger.error(f"获取表 {table_name} 结构失败: {exc}")
            return []

    def get_messages_by_type(
        self, msg_type: int, limit: int = 100
    ) -> list[WeChatMessage]:
        """按类型查询消息。

        Args:
            msg_type: 消息类型。
            limit: 最大条数。

        Returns:
            WeChatMessage 列表。
        """
        if self._sqlite_conn is None:
            return []

        if self._has_msg_shard_tables():
            return self._get_v4_messages_by_type(msg_type, limit)

        try:
            cursor = self._sqlite_conn.execute(
                """
                SELECT msg_id, msg_content, msg_type, msg_talker,
                       msg_create_time, chatroom_id
                FROM MSG
                WHERE msg_type = ?
                ORDER BY msg_create_time DESC
                LIMIT ?
                """,
                (msg_type, limit),
            )

            messages: list[WeChatMessage] = []
            for row in cursor:
                messages.append(
                    WeChatMessage(
                        msg_id=str(row["msg_id"] or ""),
                        msg_type=row["msg_type"] or 0,
                        content=str(row["msg_content"] or ""),
                        sender=str(row["msg_talker"] or ""),
                        room_id=str(row["chatroom_id"] or ""),
                        create_time=datetime.fromtimestamp(
                            (row["msg_create_time"] or 0) / 1000.0
                        ),
                    )
                )
            return messages

        except Exception as exc:
            logger.error(f"按类型查询消息失败: {exc}")
            return []

    def get_messages_by_talker(
        self, talker: str, limit: int = 100
    ) -> list[WeChatMessage]:
        """查询指定会话的消息。

        Args:
            talker: 会话 wxid。
            limit: 最大条数。

        Returns:
            WeChatMessage 列表。
        """
        if self._sqlite_conn is None:
            return []

        if self._has_msg_shard_tables():
            return self._get_v4_messages_by_talker(talker, limit)

        try:
            cursor = self._sqlite_conn.execute(
                """
                SELECT msg_id, msg_content, msg_type, msg_talker,
                       msg_create_time, chatroom_id
                FROM MSG
                WHERE msg_talker = ?
                ORDER BY msg_create_time DESC
                LIMIT ?
                """,
                (talker, limit),
            )

            messages: list[WeChatMessage] = []
            for row in cursor:
                messages.append(
                    WeChatMessage(
                        msg_id=str(row["msg_id"] or ""),
                        msg_type=row["msg_type"] or 0,
                        content=str(row["msg_content"] or ""),
                        sender=str(row["msg_talker"] or ""),
                        create_time=datetime.fromtimestamp(
                            (row["msg_create_time"] or 0) / 1000.0
                        ),
                    )
                )
            return messages

        except Exception as exc:
            logger.error(f"查询会话 {talker} 消息失败: {exc}")
            return []

    def _get_v4_messages_by_type(
        self,
        msg_type: int,
        limit: int = 100,
    ) -> list[WeChatMessage]:
        messages: list[WeChatMessage] = []
        try:
            for table, username in self._get_v4_msg_tables():
                if len(messages) >= limit:
                    break
                cursor = self._sqlite_conn.execute(  # type: ignore[union-attr]
                    f'SELECT local_id, create_time, real_sender_id, '
                    f'message_content, local_type, source, '
                    f'status, origin_source, server_seq '
                    f'FROM "{table}" '
                    f'WHERE local_type = ? '
                    f'ORDER BY local_id DESC '
                    f'LIMIT ?',
                    (msg_type, limit - len(messages)),
                )
                is_group = "@chatroom" in username
                for row in cursor:
                    messages.append(
                        WeChatMessage(
                            msg_id=f"{table}:{row['local_id']}",
                            msg_type=row["local_type"] or 0,
                            content=self._decode_message_content(row["message_content"]),
                            sender=username,
                            room_id=username if is_group else "",
                            create_time=datetime.fromtimestamp(row["create_time"] or 0),
                            is_group=is_group,
                            at_list=[],
                        )
                    )
            return messages
        except Exception as exc:
            logger.error(f"按类型查询 Windows 4.x 消息失败: {exc}")
            return []

    def _get_v4_messages_by_talker(
        self,
        talker: str,
        limit: int = 100,
    ) -> list[WeChatMessage]:
        table = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        messages: list[WeChatMessage] = []
        try:
            exists = self._sqlite_conn.execute(  # type: ignore[union-attr]
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                return []
            cursor = self._sqlite_conn.execute(  # type: ignore[union-attr]
                f'SELECT local_id, create_time, real_sender_id, '
                f'message_content, local_type, source, '
                f'status, origin_source, server_seq '
                f'FROM "{table}" '
                f'ORDER BY local_id DESC '
                f'LIMIT ?',
                (limit,),
            )
            is_group = "@chatroom" in talker
            for row in cursor:
                sender = talker
                if is_group and not self._is_self_sent_v4_row(row):
                    sender = self._parse_group_sender(row["source"], talker)
                messages.append(
                    WeChatMessage(
                        msg_id=f"{table}:{row['local_id']}",
                        msg_type=row["local_type"] or 0,
                        content=self._decode_message_content(row["message_content"]),
                        sender=sender,
                        room_id=talker if is_group else "",
                        create_time=datetime.fromtimestamp(row["create_time"] or 0),
                        is_group=is_group,
                        at_list=[],
                    )
                )
            return messages
        except Exception as exc:
            logger.error(f"查询 Windows 4.x 会话 {talker} 消息失败: {exc}")
            return []
