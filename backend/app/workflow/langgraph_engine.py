"""基于 LangGraph 的工作流引擎。

使用 StateGraph 替代自定义状态机，获得：
- 持久化状态支持 (MemorySaver)
- 可视化工作流图
- 条件分支
- 通过 thread_id 实现用户会话隔离
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from app.models.database import Workflow

logger = logging.getLogger(__name__)

_ORDER_PARSE_RE = re.compile(
    r"""
    ^\s*
    (?P<game>\S+)                          # game name
    \s+
    (?P<rank>\S+)                          # rank
    \s+
    (?P<hours>\d+(?:\.\d+)?)\s*(?:小时|h)?  # duration
    \s+
    (?P<budget>\d+(?:\.\d+)?)\s*(?:元|块)?  # budget
    (?:\s+(?P<notes>.*))?                  # notes
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class WorkflowState(TypedDict, total=False):
    """LangGraph 工作流状态。"""

    workflow_name: str
    current_state: str
    user_id: str
    data: dict[str, Any]
    action: str
    reply: str
    forward_targets: list[str]
    ended: bool
    user_message: str


class LangGraphWorkflowEngine:
    """基于 LangGraph 的工作流引擎。

    将数据库中的工作流定义编译为 StateGraph，
    使用 MemorySaver 实现跨轮次状态持久化。

    使用方式:
        engine = LangGraphWorkflowEngine(session_factory)
        await engine.load_workflows()
        result = await engine.start_workflow("peiwang_order_flow", user_id)
        result = await engine.process_message(user_id, "王者荣耀 钻石 3 150")
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory
        self._checkpointer = MemorySaver()
        self._graphs: dict[str, Any] = {}          # workflow_name -> compiled graph
        self._definitions: dict[str, dict] = {}     # workflow_name -> definition

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load_workflows(self, session=None) -> None:
        """从数据库加载工作流定义并编译为 LangGraph graphs。"""
        if session is not None:
            await self._load(session)
        elif self._session_factory is not None:
            async with self._session_factory() as sess:
                await self._load(sess)
        else:
            logger.error("No session available; cannot load workflows.")
            return

        for name, wf_def in self._definitions.items():
            try:
                self._graphs[name] = self._compile_workflow(name, wf_def)
            except Exception as exc:
                logger.error(f"Failed to compile workflow '{name}': {exc}")

    async def start_workflow(
        self,
        workflow_name: str,
        user_id: str,
        session=None,
    ) -> dict[str, Any] | None:
        """启动工作流并返回初始 action。"""
        if not self._graphs:
            await self.load_workflows(session=session)

        graph = self._graphs.get(workflow_name)
        if graph is None:
            logger.warning(f"Workflow not found: {workflow_name}")
            return None

        initial_state: WorkflowState = {
            "workflow_name": workflow_name,
            "current_state": "START",
            "user_id": user_id,
            "data": {},
            "action": "none",
            "reply": "",
            "forward_targets": [],
            "ended": False,
            "user_message": "",
        }

        config = {"configurable": {"thread_id": f"wf:{workflow_name}:{user_id}"}}
        try:
            result = await graph.ainvoke(initial_state, config)
            return self._extract_action(result)
        except Exception as exc:
            logger.error(f"Failed to start workflow '{workflow_name}' for {user_id}: {exc}")
            return None

    async def process_message(
        self,
        user_id: str,
        msg: str,
        session=None,
    ) -> dict[str, Any]:
        """处理用户消息推进工作流。"""
        if not self._graphs:
            await self.load_workflows(session=session)

        # 查找用户活跃的 workflow
        active_workflow = await self._find_active_workflow(user_id)
        if active_workflow is None:
            return {"action": "none", "reply": "", "forward_targets": [], "ended": False}

        graph = self._graphs.get(active_workflow)
        if graph is None:
            return {"action": "none", "reply": "", "forward_targets": [], "ended": False}

        config = {"configurable": {"thread_id": f"wf:{active_workflow}:{user_id}"}}
        update: dict[str, Any] = {"user_message": msg}

        # 尝试解析订单数据
        order_data = _try_parse_order(msg)
        if order_data:
            current_state = await self._get_current_state(config)
            existing_data = current_state.get("data", {}) if current_state else {}
            existing_data.update(order_data)
            update["data"] = existing_data

        try:
            result = await graph.ainvoke(update, config)
            return self._extract_action(result)
        except Exception as exc:
            logger.error(f"process_message failed for {user_id}: {exc}")
            return {"action": "none", "reply": "", "forward_targets": [], "ended": False}

    # ------------------------------------------------------------------
    # State inspection
    # ------------------------------------------------------------------

    async def get_instance(self, user_id: str) -> dict[str, Any] | None:
        """返回用户当前活跃的工作流实例状态。"""
        for name in self._graphs:
            config = {"configurable": {"thread_id": f"wf:{name}:{user_id}"}}
            state = await self._get_current_state(config)
            if state and not state.get("ended", False):
                return state
        return None

    def active_count(self) -> int:
        """返回活跃工作流数量（估算）。"""
        return len(self._graphs)

    async def cancel_workflow(self, user_id: str) -> bool:
        """取消用户活跃的工作流。"""
        for name, graph in self._graphs.items():
            config = {"configurable": {"thread_id": f"wf:{name}:{user_id}"}}
            state = await self._get_current_state(config)
            if state and not state.get("ended", False):
                update = {"ended": True, "action": "none", "reply": ""}
                await graph.ainvoke(update, config)
                return True
        return False

    # ------------------------------------------------------------------
    # Internal: 工作流编译
    # ------------------------------------------------------------------

    def _compile_workflow(self, name: str, wf_def: dict):
        """将工作流定义编译为 LangGraph StateGraph。"""
        builder = StateGraph(WorkflowState)
        states = wf_def.get("states", {})

        # 为每个状态创建节点
        for state_name, state_def in states.items():
            builder.add_node(state_name, self._make_state_node(name, state_name, state_def, wf_def))

        # 设置入口
        first_state = list(states.keys())[0] if states else "START"
        builder.set_entry_point(first_state)

        # 添加边：为每个状态添加转换（条件边或直边）
        state_keys = list(states.keys())
        for i, state_name in enumerate(state_keys):
            state_def = states[state_name]
            transitions = state_def.get("transitions", [])

            if transitions:
                # 条件路由
                route_map = {}
                for tr in transitions:
                    next_state = tr.get("next", "")
                    if next_state and next_state in states:
                        route_map[next_state] = next_state
                route_map["__stay__"] = state_name  # 无匹配时留在当前状态
                route_map["__end__"] = END
                builder.add_conditional_edges(
                    state_name,
                    self._make_router(transitions),
                    route_map,
                )
            else:
                # 无转换 -> 自动到下一状态或 END
                if i + 1 < len(state_keys):
                    builder.add_edge(state_name, state_keys[i + 1])
                else:
                    builder.add_edge(state_name, END)

        return builder.compile(checkpointer=self._checkpointer)

    def _make_state_node(
        self,
        wf_name: str,
        state_name: str,
        state_def: dict,
        wf_def: dict,
    ):
        """创建状态节点函数。"""

        async def node_fn(state: WorkflowState) -> dict[str, Any]:
            on_enter = state_def.get("on_enter", "")

            # 渲染模板
            data = state.get("data", {})
            try:
                reply = on_enter.format_map(_SafeDict(data))
            except Exception:
                reply = on_enter

            is_end = state_name in ("END", "DONE", "FINISHED")

            # 检查是否需要转发
            forward_to = wf_def.get("forward_to", "")
            forward_targets = [t.strip() for t in forward_to.split(",") if t.strip()]

            if state_name == "FORWARD" and forward_targets:
                return {
                    "current_state": state_name,
                    "reply": reply,
                    "action": "forward",
                    "forward_targets": forward_targets,
                    "ended": False,
                }

            return {
                "current_state": state_name,
                "reply": reply,
                "action": "reply" if not is_end else "none",
                "forward_targets": forward_targets,
                "ended": is_end,
            }

        return node_fn

    @staticmethod
    def _make_router(transitions: list[dict]):
        """创建条件路由函数。"""

        def router(state: WorkflowState) -> str:
            msg = state.get("user_message", "")
            for tr in transitions:
                pattern = tr.get("pattern", "")
                if not pattern:
                    continue
                try:
                    if re.search(pattern, msg, re.IGNORECASE):
                        next_state = tr.get("next", "")
                        if next_state:
                            return next_state
                except re.error:
                    pass

            # 检查是否已结束
            if state.get("ended"):
                return "__end__"
            return "__stay__"

        return router

    # ------------------------------------------------------------------
    # Internal: 数据加载
    # ------------------------------------------------------------------

    async def _load(self, session) -> None:
        """从数据库加载工作流定义。"""
        result = await session.execute(
            select(Workflow).where(Workflow.enabled == True)
        )
        rows = result.scalars().all()
        self._definitions = {}

        for r in rows:
            states_raw = r.states or []
            if isinstance(states_raw, list):
                states_map = {}
                for s in states_raw:
                    name = s.get("name", "")
                    if name:
                        states_map[name] = s
            elif isinstance(states_raw, str):
                try:
                    parsed = json.loads(states_raw)
                except json.JSONDecodeError:
                    parsed = []
                states_map = {}
                for s in parsed:
                    name = s.get("name", "")
                    if name:
                        states_map[name] = s
            else:
                states_map = {}

            self._definitions[r.name] = {
                "id": r.id,
                "name": r.name,
                "description": r.description or "",
                "trigger_intents": r.trigger_intents or [],
                "states": states_map,
                "forward_to": r.forward_to or "",
                "enabled": r.enabled,
            }

        logger.info(f"Loaded {len(self._definitions)} workflow definitions")

    # ------------------------------------------------------------------
    # Internal: 状态查询
    # ------------------------------------------------------------------

    async def _get_current_state(self, config: dict) -> dict[str, Any] | None:
        """查询指定 config 的当前状态快照。"""
        for name, graph in self._graphs.items():
            try:
                sn = graph.get_state(config)
                if sn and sn.values:
                    return dict(sn.values)
            except Exception:
                continue
        return None

    async def _find_active_workflow(self, user_id: str) -> str | None:
        """查找用户当前活跃的工作流名称。"""
        for name in self._definitions:
            config = {"configurable": {"thread_id": f"wf:{name}:{user_id}"}}
            state = await self._get_current_state(config)
            if state and state.get("current_state") and not state.get("ended", False):
                return name
        return None

    # ------------------------------------------------------------------
    # Internal: 结果提取
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_action(result: dict) -> dict[str, Any]:
        """从 graph 输出中提取 action dict。"""
        return {
            "action": result.get("action", "none"),
            "reply": result.get("reply", ""),
            "forward_targets": result.get("forward_targets", []),
            "ended": result.get("ended", False),
        }


class _SafeDict(dict):
    """Dict that returns "" for missing keys."""

    def __missing__(self, key: str) -> str:
        return ""


def _try_parse_order(msg: str) -> dict[str, Any] | None:
    """尝试解析陪玩订单信息。"""
    m = _ORDER_PARSE_RE.match(msg.strip())
    if m is None:
        return None

    d = m.groupdict()
    try:
        hours_val = float(d.get("hours", "0"))
    except (TypeError, ValueError):
        hours_val = 0.0
    try:
        budget_val = float(d.get("budget", "0"))
    except (TypeError, ValueError):
        budget_val = 0.0

    return {
        "game": d.get("game", ""),
        "rank": d.get("rank", ""),
        "hours": hours_val,
        "budget": budget_val,
        "notes": (d.get("notes") or "").strip(),
    }
