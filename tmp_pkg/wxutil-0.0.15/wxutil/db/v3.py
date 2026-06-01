import os
import time
from typing import Callable, Dict, List, Any, Optional, Tuple, Union

from pyee.executor import ExecutorEventEmitter
from sqlcipher3 import _sqlite3 as sqlite

from wxutil.logger import logger
from wxutil.utils import (
    deserialize_bytes_extra,
    decompress_compress_content,
    parse_xml,
    get_db_key,
    read_info,
)

ALL_MESSAGE = (0, 0)
TEXT_MESSAGE = (1, 0)  # 文本消息
IMAGE_MESSAGE = (3, 0)  # 图片消息
VOICE_MESSAGE = (34, 0)  # 语言消息
GREETING_MESSAGE = (37, 0)  # 打招呼，加好友时的自我介绍消息
FRIEND_RECOMMEND_MESSAGE = (42, 0)  # 向别人推荐好友消息
VIDEO_MESSAGE = (43, 0)  # 视频消息
EMOTION_MESSAGE = (47, 0)  # 表情消息
LOCATION_MESSAGE = (48, 0)  # 位置消息
APP_MESSAGE = (49, 1)  # 特殊文字消息消息（如阿里云盘邀请、飞书日程）
BILIBILI_SHARE_MESSAGE = (49, 4)  # 分享哔哩哔哩视频消息
CARD_LINK_MESSAGE = (49, 5)  # 卡片链接消息
FILE_MESSAGE = (49, 6)  # 文件消息
GIF_MESSAGE = (49, 8)  # GIF消息
MERGED_FORWARD_MESSAGE = (49, 19)  # 合并转发聊天记录消息
MINI_APP_MESSAGE = (49, 33)  # 小程序分享消息
MINI_APP2_MESSAGE = (49, 36)  # 小程序分享消息另一种
MICRO_VIDEO_MESSAGE = (49, 50)  # 微视频消息
MOMENT_SHARE_MESSAGE = (49, 51)  # 分享朋友圈动态消息
CHAIN_MESSAGE = (49, 53)  # 接龙消息
QUOTED_MESSAGE = (49, 57)  # 带引用的文本消息
CHANNEL_LIVE_MESSAGE = (49, 63)  # 视频号直播或回放消息
SONG_MESSAGE = (49, 76)  # 分享歌曲消息
GROUP_ANNOUNCEMENT_MESSAGE = (49, 87)  # 群公告消息
CHANNEL_LIVE2_MESSAGE = (49, 88)  # 视频号直播/直播回放消息
TRANSFER_MESSAGE = (49, 2000)  # 转账消息
RED_ENVELOPE_MESSAGE = (49, 2003)  # 红包消息
VOIP_MESSAGE = (50, 0)  # 语音电话消息
FRIEND_RECOMMENDATION_MESSAGE = (65, 0)  # 朋友推荐消息
SYSTEM_MESSAGE = (10000, 0)  # 系统通知消息
PAT_MESSAGE = (10000, 4)  # 拍一拍消息
GROUP_INVITATION_MESSAGE = (10000, 8000)  # 邀请入群通知消息


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


def get_room_member_wxid(bytes_extra: Dict[str, Any]) -> Union[str, None]:
    try:
        return bytes_extra["3"][0]["2"]
    except Exception:
        return None


def get_message(row: Tuple[Any, ...]) -> Dict[str, Any]:
    return {
        "local_id": row[0],
        "talker_id": row[1],
        "msg_svr_id": row[2],
        "type": row[3],
        "sub_type": row[4],
        "is_sender": row[5],
        "create_time": row[6],
        "sequence": row[7],
        "status_ex": row[8],
        "flag_ex": row[9],
        "status": row[10],
        "msg_server_seq": row[11],
        "msg_sequence": row[12],
        "str_talker": row[13],
        "str_content": row[14],
        "display_content": row[15],
        "reserved_0": row[16],
        "reserved_1": row[17],
        "reserved_2": row[18],
        "reserved_3": row[19],
        "reserved_4": row[20],
        "reserved_5": row[21],
        "reserved_6": row[22],
        "compress_content": row[23],
        "bytes_extra": row[24],
        "bytes_trans": row[25],
    }


class WeChatDB:
    def __init__(self, pid: Optional[int] = None) -> None:
        result = read_info(pid)
        if result:
            self.info = result[0]
        else:
            raise Exception("Not found wechat key!")
        self.pid = self.info["pid"]
        self.key = self.info["key"]
        self.data_dir = self.info["file_path"]
        self.msg_db = self.get_msg_db()
        self.conn = self.create_connection(rf"Msg\Multi\{self.msg_db}")
        self.wxid = self.data_dir.split("\\")[-1]
        self.event_emitter = ExecutorEventEmitter()

    def get_db_path(self, db_name: str) -> str:
        return os.path.join(self.data_dir, db_name)

    def get_msg_db(self) -> str:
        try:
            with open(
                    os.path.join(self.data_dir, r"Msg\Multi\config.ini"),
                    "r",
                    encoding="utf-8",
            ) as f:
                return f.read()
        except Exception:
            return "MSG0.db"

    def create_connection(self, db_name: str) -> sqlite.Connection:
        conn = sqlite.connect(self.get_db_path(db_name))
        db_key = get_db_key(self.key, self.get_db_path(db_name), "3")
        conn.execute(f"PRAGMA key = \"x'{db_key}'\";")
        conn.execute(f"PRAGMA cipher_page_size = 4096;")
        conn.execute(f"PRAGMA kdf_iter = 64000;")
        conn.execute(f"PRAGMA cipher_hmac_algorithm = HMAC_SHA1;")
        conn.execute(f"PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1;")
        return conn

    def get_labels(self):
        labels = []
        conn = self.create_connection("Msg/MicroMsg.db")
        with conn:
            rows = conn.execute("""
            SELECT 
                LabelId, 
                LabelName 
            FROM ContactLabel;
            """).fetchall()
            for row in rows:
                labels.append({"id": row[0], "name": row[1]})
        return labels

    def get_label(self, id: int) -> Optional[Dict]:
        conn = self.create_connection("Msg/MicroMsg.db")
        with conn:
            row = conn.execute(
                """
            SELECT 
                LabelId, 
                LabelName 
            FROM ContactLabel 
            WHERE LabelId = ?;""",
                (id,),
            ).fetchone()
            if row is None:
                return None
            return {"id": row[0], "name": row[1]}

    def get_corporate_contacts(self) -> List:
        corporate_contacts = []
        conn = self.create_connection("Msg/OpenIMContact.db")
        with conn:
            rows = conn.execute(
                "SELECT UserName, NickName, SmallHeadImgUrl, Sex, Remark FROM OpenIMContact WHERE Type = 1;"
            ).fetchall()
            for row in rows:
                corporate_contacts.append(
                    {
                        "wxid": row[0],
                        "account": "",
                        "nickname": row[1],
                        "remark": row[4],
                        "label_ids": [],
                        "avatar": row[2],
                        "country": "",
                        "province": "",
                        "city": "",
                        "signature": "",
                        "phone": "",
                        "sex": row[3],
                    }
                )
        return corporate_contacts

    def get_corporate_contact(self, wxid: str) -> Optional[Dict]:
        conn = self.create_connection("Msg/OpenIMContact.db")
        with conn:
            row = conn.execute(
                """SELECT UserName, NickName, SmallHeadImgUrl, Sex, Remark FROM OpenIMContact WHERE Type = 1 AND UserName = ?;""",
                (wxid,),
            ).fetchone()
            if row is None:
                return None
            return {
                "wxid": row[0],
                "account": "",
                "nickname": row[1],
                "remark": row[4],
                "label_ids": [],
                "avatar": row[2],
                "country": "",
                "province": "",
                "city": "",
                "signature": "",
                "phone": "",
                "sex": row[3],
            }

    def get_contacts(self) -> List:
        contacts = []
        conn = self.create_connection("Msg/MicroMsg.db")
        with conn:
            rows = conn.execute("""
            SELECT 
                UserName, 
                Alias,
                NickName, 
                Remark,
                LabelIDList,
                ContactHeadImgUrl.smallHeadImgUrl as avatar,
                ExtraBuf
            FROM Contact
            LEFT JOIN ContactHeadImgUrl on ContactHeadImgUrl.usrName = Contact.UserName
            WHERE type != 2 AND VerifyFlag = 0;""").fetchall()
            for row in rows:
                extra_buf = decode_extra_buf(row[-1])
                contact = {
                    "wxid": row[0],
                    "account": row[1],
                    "nickname": row[2],
                    "remark": row[3],
                    "label_ids": list(
                        map(
                            lambda x: int(x),
                            filter(lambda x: x != "", row[4].split(",")),
                        )
                    ),
                    "avatar": row[5],
                    **extra_buf,
                }
                contacts.append(contact)
        contacts.extend(self.get_corporate_contacts())
        return contacts

    def get_contact(self, wxid: str) -> Optional[Dict]:
        conn = self.create_connection("Msg/MicroMsg.db")
        with conn:
            row = conn.execute(
                """
            SELECT 
                UserName, 
                Alias,
                NickName, 
                Remark,
                LabelIDList,
                ContactHeadImgUrl.smallHeadImgUrl as avatar,
                ExtraBuf
            FROM Contact
            LEFT JOIN ContactHeadImgUrl on ContactHeadImgUrl.usrName = Contact.UserName
            WHERE type != 2 AND VerifyFlag = 0 AND Contact.UserName = ?;""",
                (wxid,),
            ).fetchone()
            if row is None:
                return None
            extra_buf = decode_extra_buf(row[-1])
            return {
                "wxid": row[0],
                "account": row[1],
                "nickname": row[2],
                "remark": row[3],
                "label_ids": list(
                    map(lambda x: int(x), filter(lambda x: x != "", row[4].split(",")))
                ),
                "avatar": row[5],
                **extra_buf,
            }

    def get_rooms(self) -> List:
        rooms = []
        conn = self.create_connection("Msg/MicroMsg.db")
        with conn:
            rows = conn.execute("""
            SELECT 
                UserName, 
                NickName, 
                ContactHeadImgUrl.smallHeadImgUrl as avatar,
                ChatRoom.UserNameList as member_wxids,
                ChatRoom.Reserved2 as owner,
                ChatRoomInfo.Announcement as announcement,
                ChatRoomInfo.AnnouncementEditor as announcement_editor,
                ChatRoomInfo.AnnouncementPublishTime as announcement_publish_time,
                ChatRoomInfo.Reserved2 as group_notice,
                ExtraBuf
            FROM Contact
            LEFT JOIN ContactHeadImgUrl on ContactHeadImgUrl.usrName = Contact.UserName
            LEFT JOIN ChatRoom on ChatRoom.ChatRoomName = Contact.UserName
            LEFT JOIN ChatRoomInfo on ChatRoomInfo.ChatRoomName = Contact.UserName
            WHERE type = 2;""").fetchall()
            for row in rows:
                rooms.append(
                    {
                        "wxid": row[0],
                        "nickname": row[1],
                        "avatar": row[2],
                        "member_list": row[3].split("^G") if row[3] else [],
                        "owner": row[4],
                        "announcement": row[5],
                        "announcement_editor": row[6],
                        "announcement_publish_time": row[7],
                        "group_notice": row[8],
                    }
                )
        return rooms

    def get_room(self, room_wxid: str, detail: bool = False) -> Optional[Dict]:
        conn = self.create_connection("Msg/MicroMsg.db")
        with conn:
            row = conn.execute(
                """
            SELECT 
                UserName, 
                NickName, 
                ContactHeadImgUrl.smallHeadImgUrl as avatar,
                ChatRoom.UserNameList as member_wxids,
                ChatRoom.Reserved2 as owner,
                ChatRoomInfo.Announcement as announcement,
                ChatRoomInfo.AnnouncementEditor as announcement_editor,
                ChatRoomInfo.AnnouncementPublishTime as announcement_publish_time,
                ChatRoomInfo.Reserved2 as group_notice,
                ExtraBuf
            FROM Contact
            LEFT JOIN ContactHeadImgUrl on ContactHeadImgUrl.usrName = Contact.UserName
            LEFT JOIN ChatRoom on ChatRoom.ChatRoomName = Contact.UserName
            LEFT JOIN ChatRoomInfo on ChatRoomInfo.ChatRoomName = Contact.UserName
            WHERE type = 2 AND Contact.UserName = ?;""",
                (room_wxid,),
            ).fetchone()
            if row is None:
                return None
            if detail:
                return {
                    "wxid": row[0],
                    "nickname": row[1],
                    "avatar": row[2],
                    "member_list": self.get_room_members(room_wxid),
                    "owner": row[4],
                    "announcement": row[5],
                    "announcement_editor": row[6],
                    "announcement_publish_time": row[7],
                    "group_notice": row[8],
                }
            else:
                return {
                    "wxid": row[0],
                    "nickname": row[1],
                    "avatar": row[2],
                    "member_list": row[3].split("^G") if row[3] else [],
                    "owner": row[4],
                    "announcement": row[5],
                    "announcement_editor": row[6],
                    "announcement_publish_time": row[7],
                    "group_notice": row[8],
                }

    def get_room_members(self, room_wxid: str) -> List:
        room = self.get_room(room_wxid)
        if not room:
            return []
        member_list = room["member_list"]
        conn = self.create_connection("Msg/MicroMsg.db")
        room_members = []
        with conn:
            for member_wxid in member_list:
                if member_wxid.endswith("@openim"):
                    contact = self.get_corporate_contact(member_wxid)
                    room_members.append(
                        {
                            "wxid": contact["wxid"],
                            "nickname": contact["nickname"],
                            "avatar": contact["avatar"],
                        }
                    )
                else:
                    row = conn.execute(
                        """
                    SELECT 
                        UserName, 
                        NickName, 
                        ContactHeadImgUrl.smallHeadImgUrl as avatar
                    FROM Contact
                    LEFT JOIN ContactHeadImgUrl on ContactHeadImgUrl.usrName = Contact.UserName
                    WHERE Contact.UserName = ?;
                    """,
                        (member_wxid,),
                    ).fetchone()
                    if row is None:
                        continue
                    room_members.append(
                        {"wxid": row[0], "nickname": row[1], "avatar": row[2]}
                    )
        return room_members

    def get_room_member_wxids(self, room_wxid: str) -> List:
        conn = self.create_connection("Msg/ChatRoomUser.db")
        room_member_wxids = []
        with conn:
            rows = conn.execute(
                """
            SELECT 
                ChatRoomUserNameToId.UsrName AS wxid
            FROM ChatRoomUser
            JOIN ChatRoomUserNameToId ON ChatRoomUser.UserId = ChatRoomUserNameToId.rowid
            WHERE ChatRoomUser.ChatRoomId = (
                SELECT 
                    rowid
                FROM ChatRoomUserNameToId
                WHERE UsrName = ?
            );""",
                (room_wxid,),
            ).fetchall()
            for row in rows:
                room_member_wxids.append(row[0])
        return room_member_wxids

    def get_event(self, row: Optional[Tuple[Any, ...]]) -> Optional[Dict[str, Any]]:
        if not row:
            return None

        message = get_message(row)
        data = {
            "id": message["local_id"],
            "msg_id": message["msg_svr_id"],
            "sequence": message["sequence"],
            "type": message["type"],
            "sub_type": message["sub_type"],
            "is_sender": message["is_sender"],
            "create_time": message["create_time"],
            "msg": message["str_content"],
            "raw_msg": None,
            "at_user_list": [],
            "room_wxid": None,
            "from_wxid": None,
            "to_wxid": None,
            "extra": None,
        }

        bytes_extra = deserialize_bytes_extra(message["bytes_extra"])
        data["extra"] = bytes_extra

        if message["compress_content"] is not None:
            data["raw_msg"] = decompress_compress_content(message["compress_content"])

        if message["is_sender"] == 1:
            data["from_wxid"] = self.wxid
        else:
            data["from_wxid"] = message["str_talker"]

        if message["str_talker"].endswith("@chatroom"):
            data["room_wxid"] = message["str_talker"]
        else:
            if data["is_sender"] == 1:
                data["to_wxid"] = message["str_talker"]
            else:
                data["to_wxid"] = self.wxid

        if data.get("room_wxid"):
            if isinstance(bytes_extra, dict) and data["is_sender"] == 0:
                data["from_wxid"] = get_room_member_wxid(bytes_extra)
            try:
                if isinstance(bytes_extra, dict):
                    idx = 0 if message["is_sender"] == 1 else 1
                    xml_data = parse_xml(bytes_extra["3"][idx]["2"])
                    data["at_user_list"] = [
                        x
                        for x in xml_data["msgsource"].get("atuserlist", "").split(",")
                        if x
                    ]
            except Exception:
                pass

        return data

    def get_recently_messages(
            self, count: int = 10, order: str = "DESC"
    ) -> List[Optional[Dict[str, Any]]]:
        with self.conn:
            rows = self.conn.execute(
                "SELECT * FROM MSG ORDER BY localId {} LIMIT ?;".format(order), (count,)
            ).fetchall()
            return [self.get_event(row) for row in rows]

    def get_latest_revoke_message(self) -> Optional[Dict[str, Any]]:
        with self.conn:
            row = self.conn.execute(
                "SELECT * FROM MSG WHERE Type = 10000 AND SubType = 0 AND StrContent like '%<revokemsg>%' ORDER BY localId DESC LIMIT 1;"
            ).fetchone()
            return self.get_event(row)

    def handle(
            self, events: Union[tuple, list] = (0, 0), once: bool = False
    ) -> Callable[[Callable[..., Any]], None]:
        def wrapper(func: Callable[..., Any]) -> None:
            listen = self.event_emitter.on if not once else self.event_emitter.once
            if isinstance(events, tuple):
                type, sub_type = events
                listen(f"{type}:{sub_type}", func)
            elif isinstance(events, list):
                for event in events:
                    type, sub_type = event
                    listen(f"{type}:{sub_type}", func)
            else:
                raise TypeError("events must be tuple or list.")

        return wrapper

    def run(self, period: float = 0.1) -> None:
        recently_messages = self.get_recently_messages(1)
        current_local_id = (
            recently_messages[0]["id"]
            if recently_messages and recently_messages[0]
            else 0
        )
        revoke_message = self.get_latest_revoke_message()
        current_revoke_local_id = revoke_message["id"] if revoke_message else 0
        logger.info("Start listening...")
        while True:
            with self.conn:
                rows = self.conn.execute(
                    "SELECT * FROM MSG where localId > ? ORDER BY localId;",
                    (current_local_id,),
                ).fetchall()
                for row in rows:
                    event = self.get_event(row)
                    logger.debug(event)
                    if event:
                        current_local_id = event["id"]
                        self.event_emitter.emit(f"0:0", self, event)
                        self.event_emitter.emit(
                            f"{event['type']}:{event['sub_type']}", self, event
                        )

            with self.conn:
                rows = self.conn.execute(
                    "SELECT * FROM MSG WHERE localId > ? AND Type = 10000 AND SubType = 0 AND StrContent like '%<revokemsg>%' ORDER BY localId;",
                    (current_revoke_local_id,),
                ).fetchall()
                for row in rows:
                    event = self.get_event(row)
                    logger.debug(event)
                    if event:
                        current_revoke_local_id = event["id"]
                        self.event_emitter.emit(f"0:0", self, event)
                        self.event_emitter.emit(
                            f"{event['type']}:{event['sub_type']}", self, event
                        )

            time.sleep(period)

    def __str__(self) -> str:
        return f"<WeChatDB pid={self.pid!r} wxid={self.wxid!r} msg_db={self.msg_db!r}>"
