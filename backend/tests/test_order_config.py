import pytest
from fastapi import HTTPException

from app.api.config import validate_order_intake_config


def test_order_config_accepts_explicit_aliases_and_multiline_template():
    validate_order_intake_config({
        "enabled": True, "group_aliases": {"女1": "test@chatroom"},
        "trigger_keywords": ["在吗？"], "template": "发送群聊：\n发送内容：", "max_targets": 5,
    })


@pytest.mark.parametrize("settings", [
    "wrong", {"enabled": "true"}, {"max_targets": 0}, {"max_targets": True},
    {"group_aliases": {"女1": "private_wxid"}}, {"group_aliases": {"": "x@chatroom"}},
    {"trigger_keywords": "在吗"}, {"trigger_keywords": [""]}, {"template": 123},
])
def test_order_config_rejects_invalid_settings(settings):
    with pytest.raises(HTTPException) as exc:
        validate_order_intake_config(settings)
    assert exc.value.status_code == 422
