import pytest

from live_reply_probe_windows import resolve_contact


def test_nickname_resolves_to_the_same_id_and_local_remark():
    contacts = [{"wxid": "wxid_friend", "nickname": "カタオモイ", "remark": "向崟吉"}]
    assert resolve_contact(contacts, "カタオモイ") == ("wxid_friend", "向崟吉")


def test_duplicate_names_and_missing_contacts_stop_before_gui_operations():
    contacts = [
        {"wxid": "wxid_one", "nickname": "同名"},
        {"wxid": "wxid_two", "remark": "同名"},
    ]
    with pytest.raises(ValueError, match="唯一"):
        resolve_contact(contacts, "同名")
    with pytest.raises(ValueError, match="唯一"):
        resolve_contact(contacts, "不存在")


def test_a_group_with_the_same_name_is_not_a_private_recipient():
    with pytest.raises(ValueError, match="唯一"):
        resolve_contact([{"wxid": "group@chatroom", "nickname": "目标"}], "目标")
