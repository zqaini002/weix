from app.core.key_extractor_windows import WindowsKeyExtractor


def test_cache_identity_keeps_account_and_database_path(tmp_path):
    extractor = WindowsKeyExtractor.__new__(WindowsKeyExtractor)
    root = tmp_path / "xwechat_files"
    first = root / "account_a/db_storage/contact/contact.db"
    second = root / "account_b/db_storage/contact/contact.db"

    first_key = extractor._key_path_for_db(str(first), [str(root)])
    second_key = extractor._key_path_for_db(str(second), [str(root)])

    assert first_key != second_key
    assert "account_a" in first_key
    assert "account_b" in second_key


def test_legacy_mixed_account_cache_cannot_pass_readiness(tmp_path):
    extractor = WindowsKeyExtractor.__new__(WindowsKeyExtractor)
    extractor._keys = {}
    extractor._all_keys_file = tmp_path / "keys.json"
    a_msg = str(tmp_path / "a/db_storage/message/message_0.db")
    b_contact = str(tmp_path / "b/db_storage/contact/contact.db")
    infos = [
        {"path": a_msg, "rel_path": a_msg},
        {"path": b_contact, "rel_path": b_contact},
    ]
    extractor._collect_validation_dbs = lambda: infos
    extractor.verify_key = lambda key, path: True
    valid = extractor.validate_cached_keys({
        "message/message_0.db": "ab" * 32,
        "contact/contact.db": "cd" * 32,
    })

    assert extractor.select_account_keys(valid) == {}


def test_select_only_complete_account_never_mixes_partial_keys():
    keys = {
        "C:/wx/a/db_storage/message/message_0.db": "ab" * 32,
        "C:/wx/a/db_storage/contact/contact.db": "cd" * 32,
        "C:/wx/b/db_storage/contact/contact.db": "ef" * 32,
    }
    assert WindowsKeyExtractor.select_account_keys(keys) == {
        k: v for k, v in keys.items() if "/a/" in k
    }


def test_multiple_complete_accounts_are_ambiguous():
    keys = {
        f"C:/wx/{account}/db_storage/{db}": "ab" * 32
        for account in ("a", "b")
        for db in ("message/message_0.db", "contact/contact.db")
    }
    assert WindowsKeyExtractor.select_account_keys(keys) == {}


def test_account_bound_key_never_matches_other_account(tmp_path):
    a = str(tmp_path / "a/db_storage/contact/contact.db")
    b = str(tmp_path / "b/db_storage/contact/contact.db")
    keys = {a: "ab" * 32}
    assert WindowsKeyExtractor._lookup_key_for_rel_path(keys, b, b) is None
    assert WindowsKeyExtractor._lookup_key_for_rel_path(keys, a, a) == "ab" * 32


def test_account_bound_legacy_msg_cache_remains_supported():
    keys = {"C:/wx/account/Msg/MSG.db": "ab" * 32}
    assert WindowsKeyExtractor.select_account_keys(keys) == keys
