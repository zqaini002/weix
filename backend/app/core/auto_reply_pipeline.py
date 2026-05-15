"""自动回复流水线：消息监控 → 规则匹配 → 回复发送。

串联 MessageMonitor、RuleEngine、WorkflowEngine 和 MacOSSender，
实现微信消息的实时自动回复。
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from app.config import get_config
from app.core.message_monitor import MessageMonitor
from app.core.platform import Platform

logger = logging.getLogger(__name__)


class AutoReplyPipeline:
    """自动回复流水线。

    启动后后台轮询微信消息数据库，对符合条件的新消息执行规则匹配
    并自动发送回复。

    使用方式:
        pipeline = AutoReplyPipeline(session_factory)
        await pipeline.start()
        # ... 服务运行中 ...
        await pipeline.stop()
    """

    def __init__(self, session_factory=None):
        self._session_factory = session_factory
        self._monitor: Optional[MessageMonitor] = None
        self._sender = None
        self._rule_engine = None
        self._workflow_engine = None
        self._ai_agent = None  # WeixAgent 实例（延迟初始化）
        self._name_map: dict[str, str] = {}  # wxid -> 显示名
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # 消息防抖缓冲: sender_key -> [messages]
        self._buffer: dict[str, list] = {}
        self._buffer_timers: dict[str, asyncio.Task] = {}
        self._debounce_seconds = 20
        macos_cfg = get_config().macos_sender if hasattr(get_config(), "macos_sender") else {}
        self._park_after_send = macos_cfg.get("park_after_send", True)
        self._parking_receiver = macos_cfg.get("parking_receiver", "小号")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动自动回复流水线。"""
        if self._running:
            logger.warning("流水线已在运行")
            return

        platform = Platform.get()
        self._sender = platform.sender  # 兼容
        # 私聊和群聊各自独立发送器，互不干扰
        from app.core.sender_macos import PrivateChatSender, GroupChatSender
        self._private_sender = PrivateChatSender()
        self._group_sender = GroupChatSender()

        # 1. 加载密钥
        keys = self._load_keys(platform)
        if not keys:
            logger.warning("未找到数据库密钥，跳过流水线启动")
            return

        # 2. 打开消息数据库 (用于监控)
        msg_reader = self._open_message_db(platform, keys)
        if msg_reader is None:
            logger.warning("无法打开消息数据库，跳过流水线启动")
            return

        # 3. 构建名称映射 (wxid -> 显示名，用于 AppleScript 搜索)
        self._name_map = self._build_name_map(platform, keys)

        # 4. 启动消息监控
        self._monitor = MessageMonitor(msg_reader)
        await self._monitor.start()

        # 5. 加载规则引擎 (先从 YAML 同步到 DB)
        from app.workflow.rule_engine import RuleEngine

        await self._seed_rules_from_yaml()
        self._rule_engine = RuleEngine(session_factory=self._session_factory)
        await self._rule_engine.load_rules()

        # 6. 加载工作流引擎 (支持 legacy / langgraph 切换)
        wf_engine_type = get_config().workflow_engine

        if wf_engine_type == "langgraph":
            from app.workflow.langgraph_engine import LangGraphWorkflowEngine
            self._workflow_engine = LangGraphWorkflowEngine(
                session_factory=self._session_factory
            )
            logger.info("使用 LangGraph 工作流引擎")
        else:
            from app.workflow.engine import WorkflowEngine
            self._workflow_engine = WorkflowEngine(
                session_factory=self._session_factory
            )
            logger.info("使用 Legacy 工作流引擎")

        await self._workflow_engine.load_workflows()

        # 7. 启动后台处理循环
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("自动回复流水线已启动")

    async def stop(self) -> None:
        """停止自动回复流水线。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # 清理防抖缓冲
        for timer in self._buffer_timers.values():
            timer.cancel()
        self._buffer_timers.clear()
        self._buffer.clear()
        if self._monitor:
            await self._monitor.stop()
        logger.info("自动回复流水线已停止")

    # ------------------------------------------------------------------
    # Internal: 初始化
    # ------------------------------------------------------------------

    @staticmethod
    def _load_keys(platform) -> dict[str, str]:
        """加载数据库密钥。"""
        extractor = platform.key_extractor
        if hasattr(extractor, "load_keys"):
            keys = extractor.load_keys()
        else:
            keys = getattr(extractor, "_keys", {})

        if not keys:
            import json

            cache = Path("data/all_keys.json")
            if cache.exists():
                with open(cache) as f:
                    keys = json.load(f)
        return keys

    @staticmethod
    def _open_message_db(platform, keys: dict[str, str]):
        """打开消息数据库并返回 reader。"""
        from app.core.db_reader_macos import MacOSDBReader

        reader = MacOSDBReader()

        # 收集所有 DB 文件
        all_dbs: list[str] = []
        if hasattr(reader, "find_database_files"):
            all_dbs = reader.find_database_files()

        # 查找 message_0.db
        msg_db_path = None
        msg_key = None
        for full_path in all_dbs:
            for key_path, hex_key in keys.items():
                if full_path.endswith(key_path) or key_path.endswith(
                    os.path.basename(full_path)
                ):
                    if "message_0.db" in key_path or "message_0.db" in os.path.basename(
                        full_path
                    ):
                        msg_db_path = full_path
                        msg_key = hex_key
                        break
            if msg_db_path:
                break

        if not msg_db_path:
            # 回退到 MSG.db
            for full_path in all_dbs:
                for key_path, hex_key in keys.items():
                    if "MSG.db" in key_path or "MSG.db" in os.path.basename(full_path):
                        msg_db_path = full_path
                        msg_key = hex_key
                        break
                if msg_db_path:
                    break

        if not msg_db_path:
            logger.warning("未找到消息数据库")
            return None

        if not msg_key:
            msg_key = list(keys.values())[0] if keys else ""

        try:
            key_bytes = bytes.fromhex(msg_key)
            if reader.open_db(msg_db_path, key_bytes):
                logger.info(f"消息数据库已打开: {msg_db_path}")
                return reader
        except Exception as exc:
            logger.error(f"打开消息数据库失败: {exc}")

        return None

    @staticmethod
    def _build_name_map(platform, keys: dict[str, str]) -> dict[str, str]:
        """构建 wxid -> 显示名 映射 (用于 AppleScript 搜索联系人)。"""
        from app.core.db_reader_macos import MacOSDBReader

        name_map: dict[str, str] = {}

        reader = MacOSDBReader()
        all_dbs: list[str] = []
        if hasattr(reader, "find_database_files"):
            all_dbs = reader.find_database_files()

        # 查找 contact.db
        contact_db_path = None
        contact_key = None
        for full_path in all_dbs:
            for key_path, hex_key in keys.items():
                if full_path.endswith(key_path) or key_path.endswith(
                    os.path.basename(full_path)
                ):
                    if "contact.db" in key_path or "contact.db" in os.path.basename(
                        full_path
                    ):
                        contact_db_path = full_path
                        contact_key = hex_key
                        break
            if contact_db_path:
                break

        if not contact_db_path or not contact_key:
            logger.warning("未找到联系人数据库，名称映射为空")
            return name_map

        try:
            contact_reader = MacOSDBReader()
            key_bytes = bytes.fromhex(contact_key)
            if contact_reader.open_db(contact_db_path, key_bytes):
                # 联系人
                for c in contact_reader.get_contacts():
                    wxid = c.get("wxid", "")
                    if wxid:
                        # 优先备注：备注由用户自己设置，比昵称更唯一可靠
                        name_map[wxid] = (
                            c.get("remark") or c.get("nickname") or c.get("alias") or wxid
                        )
                # 群聊
                for r in contact_reader.get_chatrooms():
                    room_id = r.get("room_id", "")
                    if room_id:
                        name_map[room_id] = r.get("name", "") or room_id
                logger.info(f"名称映射已构建: {len(name_map)} 条")
                contact_reader.close()
        except Exception as exc:
            logger.error(f"构建名称映射失败: {exc}")

        return name_map

    # ------------------------------------------------------------------
    # Internal: 规则初始化
    # ------------------------------------------------------------------

    async def _seed_rules_from_yaml(self) -> None:
        """将 YAML 配置中的自动回复规则同步到数据库（如不存在）。"""
        config = get_config().auto_reply
        yaml_rules = config.get("rules", [])
        if not yaml_rules:
            logger.info("YAML 中未配置自动回复规则")
            return

        if self._session_factory is None:
            return

        from sqlalchemy import select
        from app.models.database import AutoReplyRule

        try:
            async with self._session_factory() as session:
                # 查询已有规则
                result = await session.execute(select(AutoReplyRule.name))
                existing_names = {r for r in result.scalars().all()}

                new_count = 0
                for rule in yaml_rules:
                    name = rule.get("name", "")
                    if not name or name in existing_names:
                        continue

                    record = AutoReplyRule(
                        name=name,
                        type=rule.get("type", "keyword"),
                        patterns=rule.get("patterns", []),
                        reply=rule.get("reply", ""),
                        priority=rule.get("priority", 0),
                        enabled=rule.get("enabled", True),
                        workflow=rule.get("workflow", ""),
                    )
                    session.add(record)
                    existing_names.add(name)
                    new_count += 1

                if new_count > 0:
                    await session.commit()
                    logger.info(f"从 YAML 同步了 {new_count} 条自动回复规则到数据库")

        except Exception as exc:
            logger.error(f"同步规则失败: {exc}")

    # ------------------------------------------------------------------
    # Internal: 消息处理循环
    # ------------------------------------------------------------------

    async def _process_loop(self) -> None:
        """后台消息处理循环。"""
        logger.info("消息处理循环已启动")
        while self._running:
            try:
                msg = await asyncio.wait_for(self._monitor.get_message(), timeout=1.0)
                await self._handle_message(msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"处理消息异常: {exc}", exc_info=True)

    async def _handle_message(self, msg) -> None:
        """消息入口：白名单检查通过后进入防抖缓冲，20s 内同人消息合并处理。"""
        logger.info(
            f">>> 收到消息 | sender={msg.sender} | is_group={msg.is_group} | "
            f"content={msg.content[:80]}"
        )
        config = get_config().auto_reply

        if not config.get("enabled", True):
            return

        if msg.is_group:
            receiver = msg.room_id or msg.sender
        else:
            receiver = msg.sender
        buffer_key = receiver

        if not msg.is_group:
            mode = config.get("private_chat_mode", "whitelist")
            if mode == "none":
                logger.warning(f"私聊已禁用，跳过: {msg.sender}")
                return
            if mode == "whitelist":
                whitelist = config.get("private_whitelist", [])
                if not whitelist:
                    logger.warning(f"私聊白名单为空，跳过: {msg.sender}")
                    return
                if msg.sender not in whitelist and str(msg.sender) not in whitelist:
                    logger.warning(f"私聊不在白名单，跳过: {msg.sender}")
                    return
            # mode == "all": 放行所有私聊
            logger.info(
                f"私聊放行 | sender={msg.sender} | mode={mode} | "
                f"in_whitelist={msg.sender in config.get('private_whitelist', [])}"
            )

        if msg.is_group:
            mode = config.get("group_chat_mode", "whitelist")
            if mode == "none":
                logger.warning(f"群聊已禁用，跳过: {msg.room_id}")
                return
            if mode == "whitelist":
                whitelist = config.get("group_whitelist", [])
                if not whitelist:
                    logger.warning(f"群聊白名单为空，跳过: {msg.room_id}")
                    return
                if msg.room_id not in whitelist and str(msg.room_id) not in whitelist:
                    logger.warning(f"群聊不在白名单，跳过: {msg.room_id}")
                    return
            logger.info(
                f"群聊放行 | room={msg.room_id} | mode={mode} | "
                f"in_whitelist={msg.room_id in config.get('group_whitelist', [])}"
            )

        # 防抖：取消旧定时器，入队，启动新 20s 定时器
        if buffer_key in self._buffer_timers:
            self._buffer_timers[buffer_key].cancel()

        if buffer_key not in self._buffer:
            self._buffer[buffer_key] = []
        self._buffer[buffer_key].append(msg)

        # 持久化消息到数据库
        await self._persist_message(msg)

        self._buffer_timers[buffer_key] = asyncio.create_task(
            self._flush_buffer(buffer_key)
        )
        logger.debug(
            f"消息入缓冲 | key={buffer_key} | 缓冲数={len(self._buffer[buffer_key])}"
        )

    async def _flush_buffer(self, buffer_key: str) -> None:
        """防抖到期：合并缓冲消息，执行规则匹配 + AI 回复。"""
        await asyncio.sleep(self._debounce_seconds)

        messages = self._buffer.pop(buffer_key, [])
        self._buffer_timers.pop(buffer_key, None)

        if not messages:
            return

        msg = messages[0]
        if msg.is_group:
            receiver = msg.room_id or msg.sender
        else:
            receiver = msg.sender

        parts = [m.content for m in messages]
        combined = "\n".join(parts)
        if len(combined) > 2000:
            combined = combined[:2000] + "..."

        logger.info(
            f"缓冲刷新 | key={buffer_key} | 合并 {len(messages)} 条 | "
            f"content={combined[:80]}"
        )

        config = get_config().auto_reply
        reply_mode = config.get("reply_mode", "all")
        reply_text = ""

        # 1. 规则匹配（逐条匹配，取第一条命中）
        if reply_mode in ("keyword", "all") and self._rule_engine:
            for m in messages:
                result = await self._rule_engine.match(m.content)
                if result.get("matched"):
                    reply_text = result.get("reply", "")
                    workflow_name = result.get("workflow", "")
                    if workflow_name and self._workflow_engine:
                        await self._workflow_engine.start_workflow(workflow_name, m.sender)
                    break

        # 2. AI 兜底（用合并内容调用）
        if not reply_text and reply_mode in ("ai", "all"):
            ai_msg = messages[0]
            ai_msg.content = combined
            reply_text = await self._ai_chat(ai_msg)

        # 3. 发送回复
        if reply_text:
            display_name = self._name_map.get(receiver, receiver)
            force_skip = self._is_unsearchable_name(display_name)
            if force_skip:
                logger.warning(
                    "接收者名称无法搜索，回退到当前窗口发送 | receiver=%s | display_name=%s",
                    receiver, display_name,
                )
            snd = self._group_sender if msg.is_group else self._private_sender
            success = await snd.send_text(reply_text, display_name, force_skip=force_skip)
            if success:
                if self._monitor:
                    self._monitor.remember_sent_message(receiver, reply_text)
                logger.info(
                    "自动回复已发送 | receiver=%s | reply=%s",
                    display_name,
                    reply_text[:50],
                )
                await self._park_after_reply()

    @staticmethod
    def _is_unsearchable_name(name: str) -> bool:
        """判断名称是否无法在微信搜索框中精准搜索。"""
        if not name:
            return True
        # wxid_xxx 原始 ID（微信搜索框搜不到）
        if name.startswith("wxid_"):
            return True
        # 群聊原始 ID：数字@chatroom（微信搜索框搜不到）
        # 使用 endswith 而非 in：合法群聊显示名不会以 @chatroom 结尾
        if name.endswith("@chatroom"):
            return True
        return False

    async def _park_after_reply(self) -> None:
        """自动回复完成后停靠到固定私聊，下一条消息始终重新搜索目标。"""
        if not self._park_after_send or not self._parking_receiver:
            return
        try:
            success = await self._private_sender.open_chat(self._parking_receiver)
            self._private_sender.reset_search_state()
            self._group_sender.reset_search_state()
            if success:
                logger.info("自动回复后已停靠到聊天 | receiver=%s", self._parking_receiver)
            else:
                logger.warning("自动回复后停靠聊天失败 | receiver=%s", self._parking_receiver)
        except Exception as exc:
            logger.warning("自动回复后停靠聊天异常 | receiver=%s | error=%s", self._parking_receiver, exc)

    async def _persist_message(self, msg) -> None:
        """持久化消息到数据库。"""
        if self._session_factory is None:
            return
        try:
            from app.services.message_service import MessageService
            async with self._session_factory() as session:
                service = MessageService(session)
                await service.save_message({
                    "msg_id": msg.msg_id,
                    "msg_type": msg.msg_type,
                    "content": msg.content or "",
                    "sender": msg.sender,
                    "sender_name": self._name_map.get(msg.sender, ""),
                    "room_id": msg.room_id or "",
                    "room_name": self._name_map.get(msg.room_id, "") if msg.room_id else "",
                    "is_group": msg.is_group,
                    "create_time": msg.create_time,
                })
        except Exception as exc:
            logger.error(f"持久化消息失败: {exc}")


    async def _ai_chat(self, msg) -> str:
        """调用 AI 生成聊天回复。"""
        try:
            if self._ai_agent is None:
                from app.ai.agent import WeixAgent
                self._ai_agent = WeixAgent()
                logger.info("AI 助手已初始化")

            session_id = (
                f"group:{msg.room_id}" if msg.is_group
                else f"private:{msg.sender}"
            )

            # 查找显示名
            sender_name = self._name_map.get(msg.sender, msg.sender)
            room_name = ""
            if msg.is_group and msg.room_id:
                room_name = self._name_map.get(msg.room_id, msg.room_id)

            context = {
                "is_group": msg.is_group,
                "user_name": sender_name,
                "user_wxid": msg.sender,
                "room_id": msg.room_id or "",
                "room_name": room_name,
            }

            reply = await self._ai_agent.chat(
                message=msg.content,
                session_id=session_id,
                context=context,
            )
            return reply
        except Exception as exc:
            logger.error(f"AI 回复失败: {exc}")
            return ""
