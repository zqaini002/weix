"""Deterministic private order forms with durable, at-most-once forwarding.

The delivery journal is claimed before touching the external transport. A crash
or an uncertain transport result remains uncertain and is never retried.
"""
import asyncio
import hashlib
import json
import re
import time
from collections import Counter

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.database import SystemConfig


DEFAULT_TEMPLATE = (
    "如需点单 请自助填写\n发送群聊：\n发送内容：\n发送群聊：\n发送内容：\n"
    "（特殊要求备注在发送内容里）\n我是主人的小助手 其他信息辛苦等待主人在线后回复哟。"
)
OWNER_WAIT = "我是主人的小助手 其他信息辛苦等待主人在线后回复哟。"
DEFAULT_TRIGGERS = ["在吗", "在吗？", "在吗?", "点单", "下单"]
_FIELD = re.compile(r"^\s*发送(群聊|内容)\s*[:：](.*)$")
_FOOTERS = {"（特殊要求备注在发送内容里）", "特殊要求备注在发送内容里）", OWNER_WAIT}


class OrderIntake:
    def __init__(self, session_factory, send_text, get_reply_config, get_name_map, account_scope=""):
        self._sessions = session_factory
        self._send = send_text
        self._config = get_reply_config
        self._names = get_name_map
        self._scope = account_scope
        self._locks = {}
        self._greetings = {}

    def _key(self, kind, *parts):
        raw = json.dumps([self._scope, *parts], ensure_ascii=False, separators=(",", ":"))
        return "order_intake:" + kind + ":" + hashlib.sha256(raw.encode()).hexdigest()

    def _permission(self, sender):
        config = self._config()
        if not config.get("enabled", True) or not config.get("order_intake", {}).get("enabled", False):
            return False
        names = self._names()
        title = names.get(sender, "")
        if not sender or sender.endswith("@chatroom") or not title:
            return False
        if sum(value == title for value in names.values()) != 1:
            return False
        mode = config.get("private_chat_mode", "whitelist")
        return mode == "all" or (mode == "whitelist" and any(
            item in (sender, title) for item in config.get("private_whitelist", [])
        ))

    async def _reply(self, sender, text):
        if not self._permission(sender):
            return
        try:
            await self._send(text, self._names()[sender], is_group=False, target_id=sender)
        except Exception:
            # An uncertain private acknowledgement must not retry group delivery.
            pass

    async def _read(self, key):
        async with self._sessions() as session:
            row = await session.scalar(select(SystemConfig).where(SystemConfig.key == key))
            return json.loads(row.value) if row else None

    async def _write(self, key, value):
        async with self._sessions() as session:
            row = await session.scalar(select(SystemConfig).where(SystemConfig.key == key))
            payload = json.dumps(value, ensure_ascii=False)
            if row:
                row.value = payload
            else:
                session.add(SystemConfig(key=key, value=payload))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                # Another instance may have initialized the sender state.
                row = await session.scalar(select(SystemConfig).where(SystemConfig.key == key))
                row.value = payload
                await session.commit()

    async def _insert(self, key, value):
        async with self._sessions() as session:
            session.add(SystemConfig(key=key, value=json.dumps(value, ensure_ascii=False)))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    @staticmethod
    def _parse(text):
        lines = text.strip().splitlines()
        while lines and (not lines[-1].strip() or lines[-1].strip() in _FOOTERS):
            lines.pop()
        pairs = []
        field = None
        for line in lines:
            match = _FIELD.match(line)
            if match:
                label, value = match.groups()
                field = "target" if label == "群聊" else "content"
                if field == "target" or not pairs or "content" in pairs[-1]:
                    pairs.append({})
                pairs[-1][field] = value.strip()
            elif pairs and field:
                pairs[-1][field] += "\n" + line
        for pair in pairs:
            for field in pair:
                pair[field] = pair[field].strip()
        # Empty template slots are harmless only when trailing a filled slot.
        while len(pairs) > 1 and not any(pairs[-1].values()):
            pairs.pop()
        return pairs

    def _resolve(self, raw):
        config = self._config()
        names = self._names()
        aliases = config.get("order_intake", {}).get("group_aliases", {})
        target = aliases.get(raw, raw)
        if not isinstance(target, str):
            return None
        if raw in aliases and (not target.endswith("@chatroom") or target not in names):
            return None
        if target not in names:
            matches = [key for key, value in names.items() if value == target and key.endswith("@chatroom")]
            if len(matches) != 1:
                return None
            target = matches[0]
        title = names.get(target)
        if not target.endswith("@chatroom") or not title:
            return None
        # The GUI transport searches by title; an explicit ID cannot cure an
        # ambiguous title in that transport.
        if Counter(names.values())[title] != 1:
            return None
        allowed = config.get("group_whitelist", [])
        if target not in allowed and title not in allowed:
            return None
        return target, title

    async def handle(self, msg):
        if (not msg.is_text or msg.is_group or msg.room_id or msg.is_self
                or not self._permission(msg.sender)):
            return False
        lock = self._locks.setdefault(msg.sender, asyncio.Lock())
        async with lock:
            if not self._permission(msg.sender):
                return True
            try:
                await self._handle(msg)
            except Exception:
                # Persistence failures always stop forwarding. No unjournaled
                # fallback is allowed, even when the SQLite database is locked.
                await self._reply(msg.sender, "暂时无法安全记录点单，未确认发送，请等待主人处理。")
            return True

    async def _handle(self, msg):
        sender = msg.sender
        options = self._config().get("order_intake", {})
        text = msg.content.strip()
        now = time.time()
        state_key = self._key("state", sender)
        state = await self._read(state_key) if self._sessions else {}
        state = state or {}
        if now - state.get("updated", 0) > 1800:
            state.pop("pairs", None)
            state.pop("ids", None)
        if text in options.get("trigger_keywords", DEFAULT_TRIGGERS):
            last = max(state.get("greeting", 0), self._greetings.get(sender, 0))
            if now - last >= 30:
                state["greeting"] = now
                state["updated"] = now
                self._greetings[sender] = now
                if self._sessions:
                    await self._write(state_key, state)
                await self._reply(sender, options.get("template") or DEFAULT_TEMPLATE)
            return

        pairs = self._parse(text)
        draft = state.get("pairs", [])
        if not pairs and not draft:
            await self._reply(sender, OWNER_WAIT)
            return
        if not self._sessions or not msg.msg_id:
            await self._reply(sender, "缺少可靠消息记录，暂不能转发点单，请等待主人处理。")
            return

        event_key = self._key("event", sender, str(msg.msg_id))
        event = await self._read(event_key)
        if event is None:
            ids = [str(msg.msg_id)]
            # A single field completes or corrects a previous draft. A full
            # form starts a fresh order, even when an old draft exists.
            if draft and (not pairs or (len(pairs) == 1 and len(pairs[0]) == 1)):
                draft = [dict(pair) for pair in draft]
                if not pairs:
                    if not draft[-1].get("content"):
                        draft[-1]["content"] = text
                    else:
                        await self._reply(sender, "请补全发送群聊和发送内容后提交。")
                        return
                else:
                    draft[-1].update(pairs[0])
                pairs = draft
                ids = list(dict.fromkeys([*state.get("ids", []), str(msg.msg_id)]))
            event = {"pairs": pairs, "ids": ids}
            if not await self._insert(event_key, event):
                event = await self._read(event_key)
        pairs, ids = event["pairs"], event["ids"]
        state.update(pairs=pairs, ids=ids, updated=now)
        await self._write(state_key, state)
        missing = set()
        for pair in pairs:
            if not pair.get("target"):
                missing.add("发送群聊")
            if not pair.get("content"):
                missing.add("发送内容")
        if missing:
            await self._reply(sender, "请补全：" + "、".join(sorted(missing)) + "。")
            return
        maximum = max(1, int(options.get("max_targets", 5)))
        if not pairs or len(pairs) > maximum:
            await self._reply(sender, f"每次点单请填写 1 至 {maximum} 组发送群聊和发送内容。")
            return
        resolved = [self._resolve(pair["target"]) for pair in pairs]
        if any(target is None for target in resolved):
            await self._reply(sender, "发送群聊不存在、名称不唯一或未获允许，请更正发送群聊。")
            return

        outcomes = []
        for index, (pair, target) in enumerate(zip(pairs, resolved)):
            # Re-read live config and contact mapping immediately before each
            # external side effect, including after persistence awaits.
            if not self._permission(sender) or self._resolve(pair["target"]) != target:
                outcomes.append("stopped")
                continue
            delivery_key = self._key("delivery", sender, ids, index)
            claimed = await self._insert(delivery_key, {"status": "failed_or_unknown"})
            if not claimed:
                previous = await self._read(delivery_key)
                outcomes.append(previous.get("status", "failed_or_unknown"))
                continue
            if not self._permission(sender) or self._resolve(pair["target"]) != target:
                outcomes.append("stopped")
                continue
            status = "failed_or_unknown"
            try:
                if await self._send(pair["content"], target[1], is_group=True, target_id=target[0]) is True:
                    status = "success"
            except Exception:
                pass
            await self._write(delivery_key, {"status": status})
            outcomes.append(status)
        state.pop("pairs", None)
        state.pop("ids", None)
        await self._write(state_key, state)
        confirmed = outcomes.count("success")
        if confirmed == len(pairs):
            await self._reply(sender, f"已发送至 {confirmed} 个群聊。")
        elif confirmed:
            await self._reply(sender, f"部分已发送：{confirmed}/{len(pairs)}；其余未确认发送，请等待主人核实，勿重复提交。")
        else:
            await self._reply(sender, "未确认发送，请等待主人核实，勿重复提交。")
