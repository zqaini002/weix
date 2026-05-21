import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api import platform_api
from app.core import db_reader_macos


class SharedReader:
    def find_database_files(self):
        return ["/wx/db_storage/contact/contact.db"]

    def open_db(self, *_args, **_kwargs):
        raise AssertionError("contacts API must not reuse platform.db_reader")


class ContactReader:
    opened = []

    def find_database_files(self):
        return ["/wx/db_storage/contact/contact.db"]

    def open_db(self, path, key):
        self.opened.append((path, key))
        return True

    def get_contacts(self):
        return [{"wxid": "wxid_a", "nickname": "A"}]

    def get_chatrooms(self):
        return [{"room_id": "room@chatroom", "name": "测试群"}]


class FakeExtractor:
    def load_keys(self):
        return {"contact/contact.db": "00" * 32}


def test_contacts_api_uses_isolated_reader(monkeypatch):
    shared_reader = SharedReader()
    platform = SimpleNamespace(
        key_extractor=FakeExtractor(),
        db_reader=shared_reader,
        is_macos=True,
    )

    monkeypatch.setattr(platform_api.Platform, "get", lambda: platform)
    monkeypatch.setattr(db_reader_macos, "MacOSDBReader", ContactReader)

    result = asyncio.run(platform_api.list_contacts(type="all", search=""))

    assert result["ready"] is True
    assert result["total_contacts"] == 1
    assert result["total_chatrooms"] == 1
    assert ContactReader.opened == [
        ("/wx/db_storage/contact/contact.db", bytes.fromhex("00" * 32))
    ]
