"""Private intake uses real SQLite persistence and a recording transport."""
import asyncio
import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.base import WeChatMessage
from app.models.database import Base
from app.workflow.order_intake import OrderIntake


@pytest_asyncio.fixture
async def intake(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'orders.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = {
        "enabled": True, "private_chat_mode": "whitelist",
        "private_whitelist": ["friend"], "group_whitelist": ["one@chatroom", "二群"],
        "order_intake": {"enabled": True, "group_aliases": {"女1": "one@chatroom"}},
    }
    names = {"friend": "顾客", "one@chatroom": "一群", "two@chatroom": "二群"}
    sent = []

    async def send(text, title, **kwargs):
        sent.append((text, title, kwargs))
        return True

    def service():
        return OrderIntake(factory, send, lambda: config, lambda: names, "account-a")

    yield service, config, names, sent, factory
    await engine.dispose()


def message(text, mid="m1", **kwargs):
    return WeChatMessage(mid, 1, text, "friend", **kwargs)


def groups(sent):
    return [entry for entry in sent if entry[2]["is_group"]]


@pytest.mark.asyncio
async def test_greeting_exact_form_and_throttle(intake):
    service, _, _, sent, _ = intake
    order = service()
    assert await order.handle(message("在吗？"))
    assert sent[0][0] == (
        "如需点单 请自助填写\n发送群聊：\n发送内容：\n发送群聊：\n发送内容：\n"
        "（特殊要求备注在发送内容里）\n我是主人的小助手 其他信息辛苦等待主人在线后回复哟。"
    )
    await order.handle(message("在吗？", "m2"))
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_multiple_pairs_multiline_footer_and_empty_slots(intake):
    service, _, _, sent, _ = intake
    await service().handle(message(
        "如需点单 请自助填写\n发送群聊：女1\n发送内容：第一行\n第二行\n"
        "发送群聊:二群\n发送内容:另一单\n发送群聊：\n发送内容：\n"
        "（特殊要求备注在发送内容里）\n我是主人的小助手 其他信息辛苦等待主人在线后回复哟。"
    ))
    assert groups(sent) == [
        ("第一行\n第二行", "一群", {"is_group": True, "target_id": "one@chatroom"}),
        ("另一单", "二群", {"is_group": True, "target_id": "two@chatroom"}),
    ]
    assert "已发送" in sent[-1][0]


@pytest.mark.asyncio
async def test_split_draft_survives_restart_and_replay(intake):
    service, _, _, sent, _ = intake
    await service().handle(message("发送群聊：女1", "a"))
    assert not groups(sent)
    assert "发送内容" in sent[-1][0]
    await service().handle(message("发送内容：需要两小时", "b"))
    assert [x[0] for x in groups(sent)] == ["需要两小时"]
    await service().handle(message("发送内容：需要两小时", "b"))
    assert len(groups(sent)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text,missing", [
    ("发送内容：两小时", "发送群聊"),
    ("发送群聊：女1\n发送内容：", "发送内容"),
    ("发送群聊：\n发送内容：两小时", "发送群聊"),
])
async def test_incomplete_form_never_sends(intake, text, missing):
    service, _, _, sent, _ = intake
    await service().handle(message(text))
    assert not groups(sent)
    assert missing in sent[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["不存在", "一", "friend", "blocked@chatroom"])
async def test_all_targets_validated_before_first_send(intake, target):
    service, _, names, sent, _ = intake
    names["blocked@chatroom"] = "禁发群"
    await service().handle(message(f"发送群聊：女1\n发送内容：有效\n发送群聊：{target}\n发送内容：拒绝"))
    assert not groups(sent)


@pytest.mark.asyncio
async def test_ambiguous_gui_title_blocks_even_explicit_alias(intake):
    service, _, names, sent, _ = intake
    names["duplicate@chatroom"] = "一群"
    await service().handle(message("发送群聊：女1\n发送内容：两小时"))
    assert not groups(sent)


@pytest.mark.asyncio
async def test_alias_must_map_to_actual_group_id(intake):
    service, config, _, sent, _ = intake
    config["order_intake"]["group_aliases"]["女1"] = "一群"
    await service().handle(message("发送群聊：女1\n发送内容：两小时"))
    assert not groups(sent)


@pytest.mark.asyncio
async def test_duplicate_replay_but_new_message_is_new_order(intake):
    service, _, _, sent, _ = intake
    text = "发送群聊：女1\n发送内容：两小时"
    await service().handle(message(text))
    await service().handle(message(text))
    assert len(groups(sent)) == 1
    await service().handle(message(text, "m2"))
    assert len(groups(sent)) == 2


@pytest.mark.asyncio
async def test_unknown_delivery_durable_no_automatic_retry(intake):
    service, config, names, sent, factory = intake

    async def uncertain(text, title, **kwargs):
        sent.append((text, title, kwargs))
        if kwargs["is_group"]:
            raise RuntimeError("transport disconnected after send")
        return True

    order = OrderIntake(factory, uncertain, lambda: config, lambda: names, "account-a")
    text = "发送群聊：女1\n发送内容：两小时"
    await order.handle(message(text))
    assert "未确认" in sent[-1][0]
    await service().handle(message(text))
    assert len(groups(sent)) == 1
    assert "未确认" in sent[-1][0]


@pytest.mark.asyncio
async def test_concurrent_instances_claim_each_delivery_once(intake):
    service, _, _, sent, _ = intake
    await asyncio.gather(*[
        service().handle(message("发送群聊：女1\n发送内容：两小时")) for _ in range(3)
    ])
    assert len(groups(sent)) == 1


@pytest.mark.asyncio
async def test_revoked_whitelist_before_second_send_stops_delivery(intake):
    _, config, names, sent, factory = intake

    async def revoke(text, title, **kwargs):
        sent.append((text, title, kwargs))
        if kwargs["is_group"]:
            config["group_whitelist"] = []
        return True

    order = OrderIntake(factory, revoke, lambda: config, lambda: names, "account-a")
    await order.handle(message("发送群聊：女1\n发送内容：一\n发送群聊：二群\n发送内容：二"))
    assert len(groups(sent)) == 1
    assert "部分" in sent[-1][0]


@pytest.mark.asyncio
async def test_missing_persistence_or_source_id_refuses_forwarding(intake):
    _, config, names, sent, factory = intake

    async def send(text, title, **kwargs):
        sent.append((text, title, kwargs))
        return True

    text = "发送群聊：女1\n发送内容：两小时"
    await OrderIntake(None, send, lambda: config, lambda: names).handle(message(text))
    await OrderIntake(factory, send, lambda: config, lambda: names).handle(message(text, ""))
    assert not groups(sent)


@pytest.mark.asyncio
async def test_private_permissions_and_self_guard(intake):
    service, config, _, sent, _ = intake
    assert not await service().handle(message("在吗？", is_self=True))
    assert not await service().handle(message("在吗？", is_group=True, room_id="one@chatroom"))
    config["private_whitelist"] = []
    assert not await service().handle(message("在吗？"))
    assert not sent


@pytest.mark.asyncio
async def test_unrelated_private_message_gets_owner_wait_without_ai(intake):
    service, _, _, sent, _ = intake
    assert await service().handle(message("你吃饭了吗"))
    assert "等待主人" in sent[-1][0]
