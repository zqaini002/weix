"""Local database key configuration on Windows."""

from app.core.key_extractor_windows import WindowsKeyExtractor


def test_windows_key_loader_reads_local_env_without_process_env(tmp_path, monkeypatch):
    monkeypatch.delenv("WEIX_WECHAT_DB_KEY", raising=False)
    monkeypatch.delenv("WEIX_WECHAT_CONTACT_DB_KEY", raising=False)
    monkeypatch.setattr("app.utils.paths.get_base_dir", lambda: tmp_path)
    (tmp_path / ".env").write_text(
        "WEIX_WECHAT_DB_KEY=" + "ab" * 32 + "\n"
        "WEIX_WECHAT_CONTACT_DB_KEY=" + "cd" * 32 + "\n",
        encoding="utf-8",
    )

    extractor = WindowsKeyExtractor.__new__(WindowsKeyExtractor)
    extractor._keys = {}
    extractor._all_keys_file = tmp_path / "all_keys.json"
    assert extractor.load_keys() == {
        "message_0.db": "AB" * 32,
        "contact.db": "CD" * 32,
    }
