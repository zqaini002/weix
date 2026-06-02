import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.db_reader_windows import WindowsDBReader
from app.core.wechat_paths_windows import WeChatDataDir, find_wechat_data_dirs


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专属测试")
def test_windows_db_reader_find_database_files_scans_wechat_documents(tmp_path, monkeypatch):
    """Windows reader 应能在 Documents/WeChat Files 下发现微信 DB 文件。"""
    base = tmp_path / "Documents" / "WeChat Files" / "wxid_user"
    msg_dir = base / "Msg"
    contact_dir = base / "Contact"
    msg_dir.mkdir(parents=True)
    contact_dir.mkdir(parents=True)
    msg_db = msg_dir / "MSG.db"
    micro_msg_db = contact_dir / "MicroMsg.db"
    ignored = msg_dir / "ignored.txt"
    msg_db.write_text("x")
    micro_msg_db.write_text("x")
    ignored.write_text("x")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    files = WindowsDBReader().find_database_files()

    assert str(msg_db) in files
    assert str(micro_msg_db) in files
    assert str(ignored) not in files


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专属测试")
def test_windows_db_reader_find_database_files_scans_xwechat_storage(tmp_path, monkeypatch):
    """Windows reader 应能发现新版 xwechat_files/db_storage 下的 DB 文件。"""
    base = tmp_path / "xwechat_files" / "wxid_user" / "db_storage"
    msg_dir = base / "message"
    contact_dir = base / "contact"
    msg_dir.mkdir(parents=True)
    contact_dir.mkdir(parents=True)
    msg_db = msg_dir / "message_0.db"
    contact_db = contact_dir / "contact.db"
    ignored = msg_dir / "ignored.txt"
    msg_db.write_text("x")
    contact_db.write_text("x")
    ignored.write_text("x")

    monkeypatch.setattr(
        "app.core.db_reader_windows.find_wechat_data_dirs",
        lambda: [WeChatDataDir(str(tmp_path / "xwechat_files"), "test")],
    )

    files = WindowsDBReader().find_database_files()

    assert str(msg_db) in files
    assert str(contact_db) in files
    assert str(ignored) not in files


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专属测试")
def test_wechat_data_dir_discovery_scans_one_level_drive_dirs(tmp_path, monkeypatch):
    """应能发现 D:\\wxjilu\\xwechat_files 这类自定义保存目录。"""
    data_root = tmp_path / "wxjilu" / "xwechat_files"
    data_root.mkdir(parents=True)

    monkeypatch.setattr(
        "app.core.wechat_paths_windows.get_available_drives",
        lambda: [str(tmp_path) + os.sep],
    )
    monkeypatch.setattr(
        "app.core.wechat_paths_windows.read_wechat_install_path_from_registry",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.core.wechat_paths_windows.get_wechat_exe_path",
        lambda: None,
    )

    data_dirs = find_wechat_data_dirs()

    assert WeChatDataDir(str(data_root), "drive shallow scan") in data_dirs


def test_windows_v4_self_sent_uses_current_account_rowid(monkeypatch):
    """当前账号 rowid 不是 1 时，也应识别为自己发出的消息。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE Name2Id (user_name TEXT)")
    conn.execute("INSERT INTO Name2Id (rowid, user_name) VALUES (1, 'wxid_friend')")
    conn.execute("INSERT INTO Name2Id (rowid, user_name) VALUES (2, 'wxid_me')")

    reader = WindowsDBReader()
    reader._sqlite_conn = conn
    monkeypatch.setattr(WindowsDBReader, "get_current_wxid", classmethod(lambda cls: "wxid_me"))

    self_row = conn.execute(
        "SELECT 2 AS real_sender_id, 3 AS status, 0 AS origin_source, 123 AS server_seq"
    ).fetchone()
    friend_row = conn.execute(
        "SELECT 1 AS real_sender_id, 3 AS status, 0 AS origin_source, 123 AS server_seq"
    ).fetchone()

    assert reader._is_self_sent_v4_row(self_row) is True
    assert reader._is_self_sent_v4_row(friend_row) is False
    assert reader._get_current_sender_id() == 2


def test_windows_v4_self_sent_fallback_keeps_local_send_signature():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    reader = WindowsDBReader()
    reader._sqlite_conn = conn

    local_send_row = conn.execute(
        "SELECT 0 AS real_sender_id, 2 AS status, 1 AS origin_source, 0 AS server_seq"
    ).fetchone()

    assert reader._is_self_sent_v4_row(local_send_row) is True
