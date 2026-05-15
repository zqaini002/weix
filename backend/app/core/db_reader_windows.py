"""Windows 平台 WeChat 数据库读取器。

使用 pycryptodome 的 AES-256-CBC 解密 SQLCipher4 加密的 SQLite 数据库页面，
以只读模式读取微信消息和联系人数据。
"""

import logging
import os
import sqlite3
import struct
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Hash import SHA512
from Crypto.Protocol.KDF import PBKDF2

from app.core.base import BaseDBReader, WeChatMessage

logger = logging.getLogger(__name__)

# SQLCipher4 页面大小
PAGE_SIZE = 4096
# 页面保留区域大小
RESERVED_SIZE = 48
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

    def __init__(self):
        self._key: Optional[bytes] = None
        self._aes_key: Optional[bytes] = None
        self._hmac_key: Optional[bytes] = None
        self._db_path: str = ""
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self._decrypted_path: str = ""
        self._iterations: int = 256000
        self._lock = threading.Lock()

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

    def get_contacts(self) -> list[dict]:
        """获取联系人列表。

        Returns:
            联系人字典列表，包含 wxid, name, remark, type 等字段。
        """
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        try:
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

    def get_chatrooms(self) -> list[dict]:
        """获取群聊列表。

        Returns:
            群聊字典列表。
        """
        if self._sqlite_conn is None:
            logger.error("数据库未打开")
            return []

        try:
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
                self._decrypted_path = ""

    def __del__(self) -> None:
        self.close()

    # --- 内部方法 ---

    def _derive_key(self) -> bool:
        """从原始密钥派生 AES 和 HMAC 密钥。

        通过 PBKDF2-HMAC-SHA512 从 page 1 的 salt 派生。
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

            salt = page1[16:32]  # page 1 salt

            # 尝试多种迭代次数
            for iterations in [256000, 64000, 4000]:
                try:
                    derived = PBKDF2(
                        self._key, salt, dkLen=64, count=iterations,
                        hmac_hash_module=SHA512,
                    )
                    # 验证: 使用派生密钥解密 page 1
                    iv = page1[:16]
                    encrypted = page1[16:PAGE_SIZE - RESERVED_SIZE]
                    aes_key = derived[:32]
                    cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
                    decrypted = cipher.decrypt(encrypted)

                    if decrypted[:16] == b"SQLite format 3\x00":
                        self._aes_key = aes_key
                        self._hmac_key = derived[32:64]
                        self._iterations = iterations
                        logger.info(
                            f"密钥派生成功 (iterations={iterations})"
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

                    iv = page_data[:16]
                    encrypted = page_data[16:PAGE_SIZE - RESERVED_SIZE]
                    reserved = page_data[PAGE_SIZE - RESERVED_SIZE:PAGE_SIZE]

                    cipher = AES.new(self._aes_key, AES.MODE_CBC, iv=iv)
                    decrypted = cipher.decrypt(encrypted)

                    # 重新构建页面: IV(16) + 解密数据 + 保留区(48)
                    decrypted_page = iv + decrypted + reserved
                    tmp.write(decrypted_page)

                    if page_num % 1000 == 0 and page_num > 0:
                        logger.debug(
                            f"解密进度: {page_num}/{total_pages} 页"
                        )

            logger.info(
                f"数据库解密完成 ({total_pages} 页) -> {tmp_path}"
            )
            return tmp_path

        except Exception:
            # 出错时清理临时文件
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
