import glob
import hashlib
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, NoReturn

from pyee.executor import ExecutorEventEmitter
from sqlcipher3 import dbapi2 as sqlite

from wxutil.logger import logger
from wxutil.utils import decompress, get_db_key, parse_xml

ALL_MESSAGE = 0
TEXT_MESSAGE = 1
TEXT2_MESSAGE = 2
IMAGE_MESSAGE = 3
VOICE_MESSAGE = 34
CARD_MESSAGE = 42
VIDEO_MESSAGE = 43
EMOTION_MESSAGE = 47
LOCATION_MESSAGE = 48
VOIP_MESSAGE = 50
OPEN_IM_CARD_MESSAGE = 66
SYSTEM_MESSAGE = 10000
FILE_MESSAGE = 25769803825
FILE_WAIT_MESSAGE = 317827579953
LINK_MESSAGE = 21474836529
LINK2_MESSAGE = 292057776177
SONG_MESSAGE = 12884901937
LINK4_MESSAGE = 4294967345
LINK5_MESSAGE = 326417514545
LINK6_MESSAGE = 17179869233
RED_ENVELOPE_MESSAGE = 8594229559345
TRANSFER_MESSAGE = 8589934592049
QUOTE_MESSAGE = 244813135921
MERGED_FORWARD_MESSAGE = 81604378673
APP_MESSAGE = 141733920817
APP2_MESSAGE = 154618822705
WECHAT_VIDEO_MESSAGE = 219043332145
COLLECTION_MESSAGE = 103079215153
PAT_MESSAGE = 266287972401
GROUP_ANNOUNCEMENT_MESSAGE = 373662154801


def decode_extra_buf(extra_buf_content: bytes):
    data = {
        "country": "",
        "province": "",
        "city": "",
        "signature": "",
        "phone": "",
        "sex": "",
    }
    if not extra_buf_content:
        return data
    trunk_name = {
        b"\x46\xcf\x10\xc4": "个性签名",
        b"\xa4\xd9\x02\x4a": "国家",
        b"\xe2\xea\xa8\xd1": "省份",
        b"\x1d\x02\x5b\xbf": "市区",
        # b"\x81\xAE\x19\xB4": "朋友圈背景url",
        # b"\xF9\x17\xBC\xC0": "公司名称",
        # b"\x4E\xB9\x6D\x85": "企业微信属性",
        # b"\x0E\x71\x9F\x13": "备注图片",
        b"\x75\x93\x78\xad": "手机号",
        b"\x74\x75\x2c\x06": "性别",
    }
    res = {"手机号": ""}
    off = 0
    try:
        for key in trunk_name:
            trunk_head = trunk_name[key]
            try:
                off = extra_buf_content.index(key) + 4
            except:
                pass
            char = extra_buf_content[off: off + 1]
            off += 1
            if char == b"\x04":  # 四个字节的int，小端序
                int_content = extra_buf_content[off: off + 4]
                off += 4
                int_content = int.from_bytes(int_content, "little")
                res[trunk_head] = int_content
            elif char == b"\x18":  # utf-16字符串
                length_content = extra_buf_content[off: off + 4]
                off += 4
                length_content = int.from_bytes(length_content, "little")
                strContent = extra_buf_content[off: off + length_content]
                off += length_content
                res[trunk_head] = strContent.decode("utf-16").rstrip("\x00")
        return {
            "country": res["国家"],
            "province": res["省份"],
            "city": res["市区"],
            "signature": res["个性签名"],
            "phone": res["手机号"],
            "sex": res["性别"],
        }
    except Exception:
        return data


class WeChatDB:
    def __init__(self, pid: int, key: str, data_dir: str) -> None:
        self.pid = pid
        self.key = key
        self.data_dir = data_dir
        self.info = {"pid": self.pid, "key": self.key, "data_dir": self.data_dir}
        self.com_msg_db = self.get_msg_db()
        self.com_msg_db_wal = self.get_db_path(rf"db_storage\message\{self.com_msg_db}-wal")
        self.com_conn = self.create_connection(rf"db_storage\message\{self.com_msg_db}")
        self.biz_msg_db = self.get_msg_db(biz=True)
        self.biz_msg_db_wal = self.get_db_path(rf"db_storage\message\{self.biz_msg_db}-wal")
        self.biz_conn = self.create_connection(rf"db_storage\message\{self.biz_msg_db}")
        self.conn = None
        self.wxid = self.data_dir.rstrip("\\").split("\\")[-1][:-5]
        self.event_emitter = ExecutorEventEmitter()

    def get_db_path(self, db_name: str) -> str:
        return os.path.join(self.data_dir, db_name)

    def get_msg_db(self, biz: bool = False) -> str:
        if biz:
            db_name_flag = "biz_message"
        else:
            db_name_flag = "message"

        db_files = glob.glob(
            os.path.join(
                os.path.join(self.data_dir, "db_storage", "message"), f"{db_name_flag}_*.db"
            )
        )
        db_files = [
            db_file for db_file in db_files if re.match(rf".*{db_name_flag}_\d+\.db$", db_file)
        ]

        if not db_files:
            raise Exception("No message database found.")

        latest_file = max(db_files, key=os.path.getmtime)
        return os.path.basename(latest_file)

    def create_connection(self, db_name: str) -> sqlite.Connection:
        conn = sqlite.connect(self.get_db_path(db_name), check_same_thread=False)
        db_key = get_db_key(self.key, self.get_db_path(db_name), "4")
        conn.execute(f"PRAGMA key = \"x'{db_key}'\";")
        conn.execute(f"PRAGMA cipher_page_size = 4096;")
        conn.execute(f"PRAGMA kdf_iter = 256000;")
        conn.execute(f"PRAGMA cipher_hmac_algorithm = HMAC_SHA512;")
        conn.execute(f"PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
        return conn

    def get_message(self, row: Tuple) -> Dict:
        return {
            "local_id": row[0],
            "server_id": row[1],
            "local_type": row[2],
            "sort_seq": row[3],
            "real_sender_id": row[4],
            "create_time": row[5],
            "status": row[6],
            "upload_status": row[7],
            "download_status": row[8],
            "server_seq": row[9],
            "origin_source": row[10],
            "source": row[11],
            "message_content": row[12],
            "compress_content": row[13],
            "packed_info_data": row[14],
            "WCDB_CT_message_content": row[15],
            "WCDB_CT_source": row[16],
            "sender": row[17],
        }

    def get_event(self, table: str, row: Optional[Tuple]) -> Optional[Dict]:
        if not row:
            return None

        message = self.get_message(row)
        data = {
            "table": table,
            "id": message["local_id"],
            "msg_id": message["server_id"],
            "sequence": message["sort_seq"],
            "type": message["local_type"],
            "is_sender": 1 if message["sender"] == self.wxid else 0,
            "msg": decompress(message["message_content"]),
            "source": None,
            "at_user_list": [],
            "room_wxid": None,
            "from_wxid": message["sender"],
            "to_wxid": None,
            "extra": message["packed_info_data"],
            "status": message["status"],
            "create_time": message["create_time"],
        }

        if message["source"]:
            data["source"] = parse_xml(decompress(message["source"]))
            if (
                    data["source"]
                    and data["source"].get("msgsource")
                    and data["source"]["msgsource"].get("atuserlist")
            ):
                data["at_user_list"] = data["source"]["msgsource"]["atuserlist"].split(
                    ","
                )

        if data["type"] != 1:
            try:
                data["msg"] = parse_xml(data["msg"])
            except Exception:
                pass

        if data["is_sender"] == 1:
            wxid = self.id_to_wxid(message["packed_info_data"][:4][-1])

            if wxid and wxid.endswith("@chatroom"):
                data["room_wxid"] = wxid
            else:
                data["to_wxid"] = wxid
        else:
            wxid = self.id_to_wxid(message["packed_info_data"][:4][1])

            if wxid and wxid.endswith("@chatroom"):
                data["room_wxid"] = wxid
            else:
                data["to_wxid"] = self.id_to_wxid(message["packed_info_data"][:4][-1])

        return data

    def get_msg_table(self, wxid: str) -> str:
        return f"Msg_{hashlib.md5(wxid.encode()).hexdigest()}"

    def get_text_msg(
        self,
        self_wxid: str,
        to_wxid: str,
        content: str,
        seconds: int = 30,
        decompress_limit: int = 10,
        limit: int = 1,
        biz: bool = False
    ) -> List[Optional[Dict]]:
        create_time = int(time.time()) - seconds
        table = self.get_msg_table(to_wxid)
        self.conn = self.biz_conn if biz else self.com_conn
        with self.conn:
            data = self.conn.execute(
                """
                SELECT 
                    m.*,
                    n.user_name AS sender
                FROM {} AS m
                LEFT JOIN Name2Id AS n ON m.real_sender_id = n.rowid
                WHERE m.local_type = 1 
                AND n.user_name = ? 
                AND m.message_content like ?
                AND m.create_time > ?
                ORDER BY m.local_id DESC
                LIMIT ?;
                """.format(table),
                (self_wxid, f"%{content}%", create_time, limit),
            ).fetchall()
            if not data:
                rows = self.conn.execute(
                    """
                    SELECT 
                        m.*,
                        n.user_name AS sender
                    FROM {} AS m
                    LEFT JOIN Name2Id AS n ON m.real_sender_id = n.rowid
                    WHERE m.local_type = 1 
                    AND n.user_name = ? 
                    AND m.create_time > ?
                    ORDER BY m.local_id DESC
                    LIMIT ?;
                    """.format(table),
                    (self_wxid, create_time, decompress_limit),
                ).fetchall()
                for row in rows:
                    msg = decompress(row[-6])
                    if content in msg:
                        data.append(row)
                data = data[:decompress_limit]
            return [self.get_event(table, item) for item in data]

    def get_image_msg(
        self,
        self_wxid: str,
        to_wxid: str,
        md5: str,
        seconds: int = 30,
        limit: int = 1,
        biz: bool = False
    ) -> List[Optional[Dict]]:
        data = []
        create_time = int(time.time()) - seconds
        table = self.get_msg_table(to_wxid)
        self.conn = self.biz_conn if biz else self.com_conn
        with self.conn:
            rows = self.conn.execute(
                """
                SELECT 
                    m.*,
                    n.user_name AS sender
                FROM {} AS m
                LEFT JOIN Name2Id AS n ON m.real_sender_id = n.rowid
                WHERE m.local_type = 3 
                AND n.user_name = ? 
                AND m.create_time > ?
                ORDER BY m.local_id DESC
                LIMIT ?;
                """.format(table),
                (self_wxid, create_time, limit),
            ).fetchall()
            for row in rows:
                message_content = parse_xml(decompress(row[12]))
                if message_content["msg"]["img"]["@md5"] == md5:
                    data.append(row)
        return [self.get_event(table, item) for item in data]

    def get_file_msg(
        self,
        self_wxid: str,
        to_wxid: str,
        md5: str,
        seconds: int = 30,
        biz: bool = False,
        limit: int = 1
    ) -> List[Optional[Dict]]:
        data = []
        create_time = int(time.time()) - seconds
        table = self.get_msg_table(to_wxid)
        self.conn = self.biz_conn if biz else self.com_conn
        with self.conn:
            rows = self.conn.execute(
                """
                SELECT 
                    m.*,
                    n.user_name AS sender
                FROM {} AS m
                LEFT JOIN Name2Id AS n ON m.real_sender_id = n.rowid
                WHERE m.local_type = 25769803825
                AND n.user_name = ? 
                AND m.create_time > ?
                ORDER BY m.local_id DESC
                LIMIT ?;
                """.format(table),
                (self_wxid, create_time, limit),
            ).fetchall()
            for row in rows:
                message_content = parse_xml(decompress(row[12]))
                if message_content["msg"]["appmsg"]["md5"] == md5:
                    data.append(row)
        return [self.get_event(table, item) for item in data]

    def get_recently_messages(
        self,
        table: str,
        order: str = "DESC",
        count: int = 10,
        biz: bool = False
    ) -> List[Optional[Dict]]:
        self.conn = self.biz_conn if biz else self.com_conn
        with self.conn:
            rows = self.conn.execute(
                """
                SELECT 
                    m.*,
                    n.user_name AS sender
                FROM {} AS m
                LEFT JOIN Name2Id AS n ON m.real_sender_id = n.rowid
                ORDER BY m.local_id {}
                LIMIT ?;
                """.format(table, order),
                (count,),
            ).fetchall()
            return [self.get_event(table, row) for row in rows]

    def get_msg_tables(self, biz: bool = False) -> List[str]:
        self.conn = self.biz_conn if biz else self.com_conn
        with self.conn:
            rows = self.conn.execute("""
            SELECT 
                name
            FROM sqlite_master
            WHERE type='table'
            AND name LIKE 'Msg_%';
            """).fetchall()
            return [row[0] for row in rows]

    def id_to_wxid(self, id: int, biz: bool = False) -> Optional[str]:
        self.conn = self.biz_conn if biz else self.com_conn
        with self.conn:
            row = self.conn.execute(
                """
            SELECT
                user_name 
            FROM Name2Id 
            WHERE rowid = ?;
            """,
                (id,),
            ).fetchone()
            if row is None:
                return None
            return row[0]

    def get_contacts(self) -> List:
        conn = self.create_connection("db_storage/contact/contact.db")
        contacts = []
        with conn:
            rows = conn.execute("""
            SELECT 
                username, 
                alias,
                nick_name, 
                remark,
                small_head_url as avatar,
                extra_buffer
            FROM contact
            WHERE local_type in (1, 5) 
            AND flag != 2
            AND verify_flag = 0;
            """).fetchall()
            for row in rows:
                contact = {
                    "wxid": row[0],
                    "account": row[1],
                    "nickname": row[2],
                    "remark": row[3],
                    "avatar": row[4],
                    "extra_buf": row[5],
                }
                contacts.append(contact)
        return contacts

    def get_contact(self, wxid: str) -> Optional[Dict]:
        conn = self.create_connection("db_storage/contact/contact.db")
        with conn:
            row = conn.execute(
                """
            SELECT 
                username, 
                alias,
                nick_name, 
                remark,
                small_head_url,
                extra_buffer
            FROM contact
            WHERE local_type in (1, 5) 
            AND flag != 2
            AND verify_flag = 0
            AND username = ?;
            """,
                (wxid,),
            ).fetchone()
            if row is None:
                return None
            contact = {
                "wxid": row[0],
                "account": row[1],
                "nickname": row[2],
                "remark": row[3],
                "avatar": row[4],
                "extra_buf": row[5],
            }
            return contact

    def get_rooms(self) -> List[Dict]:
        conn = self.create_connection("db_storage/contact/contact.db")
        rooms = []
        with conn:
            rows = conn.execute("""
            SELECT
                contact.username,
                contact.nick_name,
                contact.remark,
                contact.small_head_url,
                contact.extra_buffer,
                chat_room.owner,
                chat_room_info_detail.announcement_,
                chat_room_info_detail.announcement_editor_,
                chat_room_info_detail.announcement_publish_time_,
                chat_room_info_detail.chat_room_status_,
                chat_room_info_detail.xml_announcement_,
                chat_room_info_detail.ext_buffer_
             FROM chat_room
             LEFT JOIN contact on contact.username = chat_room.username
             LEFT JOIN chat_room_info_detail on chat_room_info_detail.username_ = chat_room.username
             WHERE contact.is_in_chat_room != 2;""").fetchall()
            for row in rows:
                room = {
                    "wxid": row[0],
                    "nickname": row[1],
                    "remark": row[2],
                    "avatar": row[3],
                    "extra_buffer": row[4],
                    "owner": row[5],
                    "announcement_content": row[6],
                    "announcement_editor": row[7],
                    "announcement_publish_time": row[8],
                    "announcement_xml": row[10],
                    "status": row[9],
                    "ext_buffer": row[11],
                }
                rooms.append(room)
        return rooms

    def get_room(self, room_wxid: str) -> Optional[Dict]:
        conn = self.create_connection("db_storage/contact/contact.db")
        with conn:
            row = conn.execute(
                """
            SELECT
                contact.username,
                contact.nick_name,
                contact.remark,
                contact.small_head_url,
                contact.extra_buffer,
                chat_room.owner,
                chat_room_info_detail.announcement_,
                chat_room_info_detail.announcement_editor_,
                chat_room_info_detail.announcement_publish_time_,
                chat_room_info_detail.chat_room_status_,
                chat_room_info_detail.xml_announcement_,
                chat_room_info_detail.ext_buffer_
             FROM chat_room 
             LEFT JOIN contact on contact.username = chat_room.username
             LEFT JOIN chat_room_info_detail on chat_room_info_detail.username_ = chat_room.username
             WHERE contact.is_in_chat_room != 2 
             AND chat_room.username = ?;""",
                (room_wxid,),
            ).fetchone()
            if row is None:
                return None
            room = {
                "wxid": row[0],
                "nickname": row[1],
                "remark": row[2],
                "avatar": row[3],
                "extra_buffer": row[4],
                "member_list": self.get_room_members(room_wxid),
                "owner": row[5],
                "announcement_content": row[6],
                "announcement_editor": row[7],
                "announcement_publish_time": row[8],
                "announcement_xml": row[10],
                "status": row[9],
                "ext_buffer": row[11],
            }
            return room

    def get_room_members(self, room_wxid: str) -> List[Dict]:
        conn = self.create_connection("db_storage/contact/contact.db")
        room_members = []
        with conn:
            rows = conn.execute(
                """
            SELECT 
                contact.username, 
                contact.nick_name, 
                contact.small_head_url
            FROM contact, chat_room, chatroom_member
            WHERE chatroom_member.room_id = chat_room.rowid 
            AND chatroom_member.member_id = contact.rowid
            AND contact.is_in_chat_room != 2
            AND chat_room.username = ?;
            """,
                (room_wxid,),
            ).fetchall()
            for row in rows:
                room_members.append(
                    {
                        "wxid": row[0],
                        "nickname": row[1],
                        "avatar": row[2],
                    }
                )
        return room_members

    def get_room_member(
        self,
        room_wxid: str,
        member_wxid: str
    ) -> Dict:
        conn = self.create_connection("db_storage/contact/contact.db")
        with conn:
            rows = conn.execute(
                """
            SELECT 
                contact.username, 
                contact.nick_name, 
                contact.small_head_url
            FROM contact, chat_room, chatroom_member
            WHERE chatroom_member.room_id = chat_room.rowid 
            AND chatroom_member.member_id = contact.rowid
            AND contact.is_in_chat_room != 2
            AND chat_room.username = ?;
            """,
                (room_wxid,),
            ).fetchall()
            for row in rows:
                if row[0] == member_wxid:
                    return {
                        "wxid": row[0],
                        "nickname": row[1],
                        "avatar": row[2]
                    }

    def get_labels(self) -> List[Dict]:
        conn = self.create_connection("db_storage/contact/contact.db")
        labels = []
        with conn:
            rows = conn.execute("""
                SELECT 
                    label_id_, label_name_
                FROM contact_label;
                """).fetchall()
            for row in rows:
                labels.append({"id": row[0], "name": row[1]})
        return labels

    def get_label(self, id) -> Optional[Dict]:
        conn = self.create_connection("db_storage/contact/contact.db")
        with conn:
            row = conn.execute(
                """
                SELECT 
                    label_id_, label_name_
                FROM contact_label
                WHERE label_id_ = ?;""",
                (id,),
            ).fetchone()
            if row is None:
                return None
            label = {"id": row[0], "name": row[1]}
            return label

    def handle(
            self, events: Union[int, list] = 0, once: bool = False
    ) -> Callable[[Callable[..., Any]], None]:
        def wrapper(func: Callable[..., Any]) -> None:
            listen = self.event_emitter.on if not once else self.event_emitter.once
            if isinstance(events, int):
                listen(str(events), func)
            elif isinstance(events, list):
                for event in events:
                    listen(str(event), func)
            else:
                raise TypeError("events must be int or list.")

        return wrapper

    def run(self, period: float = 0.1) -> NoReturn:
        com_msg_table_max_local_id = {}
        self.com_msg_tables = self.get_msg_tables()
        for msg_table in self.com_msg_tables:
            recently_messages = self.get_recently_messages(table=msg_table, biz=False, count=1)
            current_max_local_id = (
                recently_messages[0]["id"]
                if recently_messages and recently_messages[0]
                else 0
            )
            com_msg_table_max_local_id[msg_table] = current_max_local_id

        biz_msg_table_max_local_id = {}
        self.biz_msg_tables = self.get_msg_tables(biz=True)
        for msg_table in self.biz_msg_tables:
            recently_messages = self.get_recently_messages(table=msg_table, biz=True, count=1)
            current_max_local_id = (
                recently_messages[0]["id"]
                if recently_messages and recently_messages[0]
                else 0
            )
            biz_msg_table_max_local_id[msg_table] = current_max_local_id

        logger.info(self.info)
        logger.info("Message listening...")

        com_last_mtime = os.path.getmtime(self.com_msg_db_wal)
        biz_last_mtime = os.path.getmtime(self.biz_msg_db_wal)
        while True:
            com_mtime = os.path.getmtime(self.com_msg_db_wal)
            if com_mtime != com_last_mtime:
                current_com_msg_tables = self.get_msg_tables()
                new_com_msg_tables = list(set(current_com_msg_tables) - set(self.com_msg_tables))
                self.com_msg_tables = current_com_msg_tables
                for new_com_msg_table in new_com_msg_tables:
                    com_msg_table_max_local_id[new_com_msg_table] = 0

                for table, max_local_id in com_msg_table_max_local_id.items():
                    with self.com_conn:
                        rows = self.com_conn.execute(
                            """
                        SELECT 
                            m.*,
                            n.user_name AS sender
                        FROM {} AS m
                        LEFT JOIN Name2Id AS n ON m.real_sender_id = n.rowid
                        WHERE local_id > ?;
                        """.format(table),
                            (max_local_id,),
                        ).fetchall()
                        for row in rows:
                            event = self.get_event(table, row)
                            logger.debug(event)
                            if event:
                                com_msg_table_max_local_id[table] = event["id"]
                                self.event_emitter.emit("0", self, event)
                                self.event_emitter.emit(f"{event['type']}", self, event)

                com_last_mtime = com_mtime

            biz_mtime = os.path.getmtime(self.biz_msg_db_wal)
            if biz_mtime != biz_last_mtime:
                current_biz_msg_tables = self.get_msg_tables(biz=True)
                new_biz_msg_tables = list(set(current_biz_msg_tables) - set(self.biz_msg_tables))
                self.biz_msg_tables = current_biz_msg_tables
                for new_biz_msg_table in new_biz_msg_tables:
                    biz_msg_table_max_local_id[new_biz_msg_table] = 0

                for table, max_local_id in biz_msg_table_max_local_id.items():
                    with self.biz_conn:
                        rows = self.com_conn.execute(
                            """
                        SELECT 
                            m.*,
                            n.user_name AS sender
                        FROM {} AS m
                        LEFT JOIN Name2Id AS n ON m.real_sender_id = n.rowid
                        WHERE local_id > ?;
                        """.format(table),
                            (max_local_id,),
                        ).fetchall()
                        for row in rows:
                            event = self.get_event(table, row)
                            logger.debug(event)
                            if event:
                                biz_msg_table_max_local_id[table] = event["id"]
                                self.event_emitter.emit("0", self, event)
                                self.event_emitter.emit(f"{event['type']}", self, event)

                biz_last_mtime = biz_mtime

            time.sleep(period)

    def __str__(self) -> str:
        return f"<WeChatDB pid={self.pid!r} wxid={self.wxid!r} com_msg_db={self.com_msg_db!r} biz_msg_db={self.biz_msg_db!r}>"
