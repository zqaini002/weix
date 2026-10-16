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


class EmptyExtractor:
    def load_keys(self):
        return {}


def test_legacy_vision_contacts_without_db_key_show_database_requirement(tmp_path, monkeypatch):
    platform = SimpleNamespace(
        key_extractor=EmptyExtractor(), is_macos=False, is_windows=True,
    )
    monkeypatch.setattr(platform_api.Platform, "get", lambda: platform)
    monkeypatch.setattr(platform_api, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        platform_api, "get_config",
        lambda: SimpleNamespace(monitor={"source": "vision"}),
        raising=False,
    )

    result = asyncio.run(platform_api.list_contacts(type="all", search=""))

    assert result["ready"] is False
    assert "WEIX_WECHAT_DB_KEY" in result["error"]
    assert result["hint"] == ""


def test_windows_contacts_without_db_key_never_suggest_sudo(tmp_path, monkeypatch):
    platform = SimpleNamespace(
        key_extractor=EmptyExtractor(), is_macos=False, is_windows=True,
    )
    monkeypatch.setattr(platform_api.Platform, "get", lambda: platform)
    monkeypatch.setattr(platform_api, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        platform_api, "get_config",
        lambda: SimpleNamespace(monitor={"source": "auto"}),
        raising=False,
    )

    result = asyncio.run(platform_api.list_contacts(type="all", search=""))

    assert "sudo" not in result["error"]
    assert "Windows" in result["error"]


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


def test_failed_contact_key_never_reads_closed_database_or_reports_ready(monkeypatch):
    from app.core import db_reader_windows

    reads = []

    class FailedReader(ContactReader):
        def open_db(self, path, key):
            return False

        def get_contacts(self):
            reads.append("contacts")
            return []

        def get_chatrooms(self):
            reads.append("chatrooms")
            return []

    platform = SimpleNamespace(
        key_extractor=FakeExtractor(), is_macos=False, is_windows=True,
    )
    monkeypatch.setattr(platform_api.Platform, "get", lambda: platform)
    monkeypatch.setattr(db_reader_windows, "WindowsDBReader", FailedReader)

    result = asyncio.run(platform_api.list_contacts(type="all", search=""))

    assert result["ready"] is False
    assert "密钥" in result["error"]
    assert result["total_contacts"] == result["total_chatrooms"] == 0
    assert reads == []


def test_disk_cache_fallback_cannot_bypass_account_validation(tmp_path, monkeypatch):
    import json

    class Extractor(EmptyExtractor):
        def validate_cached_keys(self, keys):
            return keys

        def select_account_keys(self, keys):
            return {}  # Keys belong to different accounts.

    (tmp_path / "all_keys.json").write_text(
        json.dumps({"contact/contact.db": "ab" * 32}), encoding="utf-8",
    )
    platform = SimpleNamespace(key_extractor=Extractor(), is_macos=False, is_windows=True)
    monkeypatch.setattr(platform_api.Platform, "get", lambda: platform)
    monkeypatch.setattr(platform_api, "get_data_dir", lambda: tmp_path)
    assert asyncio.run(platform_api.list_contacts(type="all", search=""))["ready"] is False
