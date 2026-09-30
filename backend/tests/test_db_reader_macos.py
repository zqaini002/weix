import hashlib
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.db_reader_macos import MacOSDBReader


def _reader_with_sqlite(conn: sqlite3.Connection) -> MacOSDBReader:
    reader = MacOSDBReader()
    conn.row_factory = sqlite3.Row
    reader._sqlite_conn = conn
    reader._last_refresh = time.monotonic()
    return reader


def test_query_messages_since_keeps_incoming_and_skips_self_messages():
    """Name2Id.rowid=1 是当前账号，联系人消息不能按 real_sender_id=2 跳过。"""
    current_wxid = "wxid_current_user"
    contact_wxid = "wxid_contact_user"
    table_name = "Msg_" + hashlib.md5(contact_wxid.encode()).hexdigest()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE Name2Id (user_name TEXT)")
    conn.execute("INSERT INTO Name2Id (user_name) VALUES (?)", (current_wxid,))
    conn.execute("INSERT INTO Name2Id (user_name) VALUES (?)", (contact_wxid,))
    conn.execute(
        f'CREATE TABLE "{table_name}" ('
        "local_id INTEGER, create_time INTEGER, real_sender_id INTEGER, "
        "message_content TEXT, local_type INTEGER, source BLOB, "
        "status INTEGER, origin_source INTEGER, server_seq INTEGER)"
    )
    conn.execute(
        f'INSERT INTO "{table_name}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (1, 1778672477, 2, "联系人来信", 1, None, 3, 2, 824738737),
    )
    conn.execute(
        f'INSERT INTO "{table_name}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (2, 1778672478, 1, "自己同步出的历史消息", 1, None, 3, 2, 824739798),
    )
    conn.execute(
        f'INSERT INTO "{table_name}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            3,
            1778672479,
            1,
            "自己手动发出的消息",
            1,
            "<msgsource><alnode><fr>1</fr></alnode></msgsource>",
            2,
            1,
            0,
        ),
    )

    reader = _reader_with_sqlite(conn)

    messages = reader.query_messages_since(1778672476)

    assert [m.content for m in messages] == ["联系人来信"]
    assert messages[0].sender == contact_wxid


def test_get_my_messages_extracts_only_current_user_rows():
    """本人 skill 只能使用当前账号发出的文本，不能混入联系人发言。"""
    current_wxid = "wxid_current_user"
    contact_wxid = "wxid_contact_user"
    table_name = "Msg_" + hashlib.md5(contact_wxid.encode()).hexdigest()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE Name2Id (user_name TEXT)")
    conn.execute("INSERT INTO Name2Id (user_name) VALUES (?)", (current_wxid,))
    conn.execute("INSERT INTO Name2Id (user_name) VALUES (?)", (contact_wxid,))
    conn.execute(
        f'CREATE TABLE "{table_name}" ('
        "local_id INTEGER, create_time INTEGER, real_sender_id INTEGER, "
        "message_content TEXT, local_type INTEGER, source BLOB, "
        "status INTEGER, origin_source INTEGER, server_seq INTEGER)"
    )
    rows = [
        (1, 1778672477, 2, "朋友来信", 1, None, 3, 2, 824738737),
        (2, 1778672478, 1, "本人历史发言", 1, None, 3, 2, 824739798),
        (
            3,
            1778672479,
            1,
            "本人刚发",
            1,
            "<msgsource><alnode><fr>1</fr></alnode></msgsource>",
            2,
            1,
            0,
        ),
        (4, 1778672480, 3, "另一个联系人", 1, None, 3, 2, 824739900),
    ]
    conn.executemany(
        f'INSERT INTO "{table_name}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        rows,
    )

    reader = _reader_with_sqlite(conn)

    messages = reader.get_my_messages(limit=10, since_days=3650)

    assert [m["content"] for m in messages] == ["本人历史发言", "本人刚发"]
    assert all(m["room_id"] == "" for m in messages)
