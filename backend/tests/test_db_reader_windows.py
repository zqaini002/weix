import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.db_reader_windows import WindowsDBReader


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
