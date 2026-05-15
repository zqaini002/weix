"""
Workflow state machine engine.

Manages per-user workflow instances and drives state transitions based on
incoming user messages.  Workflow definitions are loaded from the database.

Example workflow -- "peiwang_order_flow"::

    START  -> (auto) send order form
    FORM   -> user fills in order details  -> CONFIRM
    CONFIRM -> user confirms               -> FORWARD
    FORWARD -> forwarded to receiver group  -> ASSIGN
    ASSIGN  -> peer accepts                 -> DONE
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Workflow

logger = logging.getLogger(__name__)

# Regex to split order text -- supports formats like:
#   王者荣耀 钻石 3 150 带我一个
#   原神 大师级 2小时 200元 需要女陪
_ORDER_PARSE_RE = re.compile(
    r"""
    ^\s*
    (?P<game>\S+)                          # game name  (required)
    \s+
    (?P<rank>\S+)                          # rank       (required)
    \s+
    (?P<hours>\d+(?:\.\d+)?)\s*(?:小时|h)?  # duration   (required)
    \s+
    (?P<budget>\d+(?:\.\d+)?)\s*(?:元|块)?  # budget     (required)
    (?:\s+(?P<notes>.*))?                  # notes      (optional)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class WorkflowEngine:
    """Workflow state machine that manages per-user conversation flows.

    Each active workflow instance is stored as::

        {
            user_id: {
                "workflow": "peiwang_order_flow",
                "state": "CONFIRM",
                "data": {"game": "王者荣耀", ...},
            }
        }
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory
        # In-memory state storage:  {user_id: instance_dict}
        self._instances: dict[str, dict[str, Any]] = {}
        # Cached workflow definitions:  {name: {states: [...], ...}}
        self._workflows: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load_workflows(self, session: AsyncSession | None = None) -> None:
        """Load workflow definitions from the database."""
        if session is not None:
            await self._load(session)
        elif self._session_factory is not None:
            async with self._session_factory() as sess:
                await self._load(sess)
        else:
            logger.error("No session or session_factory available; cannot load workflows.")

    async def start_workflow(
        self,
        workflow_name: str,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        """Start a workflow instance for *user_id*.

        Returns the initial action dict, or ``None`` if the workflow was
        not found or is disabled.
        """
        # Lazy-load workflows on first use.
        if not self._workflows:
            await self.load_workflows(session=session)

        wf_def = self._workflows.get(workflow_name)
        if wf_def is None or not wf_def.get("enabled", True):
            logger.warning("Workflow not found or disabled: %s", workflow_name)
            return None

        # Cancel any existing workflow for this user.
        if user_id in self._instances:
            logger.info("Overriding existing workflow for user %s", user_id)

        instance: dict[str, Any] = {
            "workflow": workflow_name,
            "state": "START",
            "data": {},
        }
        self._instances[user_id] = instance

        logger.info("Workflow started | workflow=%s | user=%s | state=START", workflow_name, user_id)

        # Execute on_enter for START state (e.g. send order form).
        start_state_def = self._get_state_def(wf_def, "START")
        action = self._build_action("reply", start_state_def.get("on_enter", ""))

        # Auto-transition from START to the next state if it has no user-triggered
        # transitions (common for START states that just send a form).
        transitions = start_state_def.get("transitions", [])
        if not transitions:
            next_state = self._find_next_state(wf_def, "START")
            if next_state:
                instance["state"] = next_state
                logger.info("Auto-transition START -> %s | user=%s", next_state, user_id)

        return action

    async def process_message(
        self,
        user_id: str,
        msg: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Process an incoming message from *user_id*.

        Drives the user's active workflow forward by matching the message
        against the current state's transitions.

        Returns:
            Action dict::

                {
                    "action": "reply" | "forward" | "none",
                    "reply": str,
                    "forward_targets": list[str],
                    "ended": bool,
                }
        """
        instance = self._instances.get(user_id)
        if instance is None:
            logger.debug("No active workflow for user %s", user_id)
            return {"action": "none", "reply": "", "forward_targets": [], "ended": False}

        wf_def = self._workflows.get(instance["workflow"])
        if wf_def is None:
            logger.warning("Workflow definition missing for %s", instance["workflow"])
            self._cleanup(user_id)
            return {"action": "none", "reply": "", "forward_targets": [], "ended": False}

        current_state = instance["state"]
        state_def = self._get_state_def(wf_def, current_state)
        transitions: list[dict[str, str]] = state_def.get("transitions", [])

        # --- Parse order data if we are in a FORM / collecting state ---
        order_data = self._try_parse_order(msg)
        if order_data:
            instance["data"].update(order_data)

        # --- Try each transition ---
        matched_next: str | None = None
        for tr in transitions:
            pattern = tr.get("pattern", "")
            if not pattern:
                continue
            try:
                if re.search(pattern, msg, re.IGNORECASE):
                    matched_next = tr.get("next", "")
                    # Extract named groups into instance data.
                    m = re.search(pattern, msg, re.IGNORECASE)
                    if m:
                        instance["data"].update(m.groupdict())
                    break
            except re.error:
                logger.warning("Invalid transition regex: %s", pattern)

        if matched_next:
            logger.info(
                "State transition | user=%s | %s -> %s",
                user_id,
                current_state,
                matched_next,
            )
            instance["state"] = matched_next
            next_state_def = self._get_state_def(wf_def, matched_next)
            on_enter = next_state_def.get("on_enter", "")

            # Render on_enter with current instance data.
            try:
                reply = on_enter.format_map(_SafeDict(instance["data"]))
            except Exception:
                reply = on_enter

            ended = matched_next in ("END", "DONE", "FINISHED")
            if ended:
                logger.info("Workflow completed | user=%s | workflow=%s", user_id, instance["workflow"])
                self._cleanup(user_id)

            # Build forward targets from workflow definition.
            forward_to = wf_def.get("forward_to", "")
            forward_targets = [t.strip() for t in forward_to.split(",") if t.strip()]

            if matched_next == "FORWARD" and forward_targets:
                return {
                    "action": "forward",
                    "reply": reply,
                    "forward_targets": forward_targets,
                    "ended": False,
                }

            return {
                "action": "reply",
                "reply": reply,
                "forward_targets": forward_targets,
                "ended": ended,
            }

        # No transition matched -- keep in the same state.
        logger.debug("No transition matched in state %s for user %s", current_state, user_id)
        return {"action": "none", "reply": "", "forward_targets": [], "ended": False}

    # ------------------------------------------------------------------
    # State inspection
    # ------------------------------------------------------------------

    def get_instance(self, user_id: str) -> dict[str, Any] | None:
        """Return the current workflow instance for *user_id*, if any."""
        return self._instances.get(user_id)

    def active_count(self) -> int:
        """Return the number of currently active workflow instances."""
        return len(self._instances)

    def cancel_workflow(self, user_id: str) -> bool:
        """Cancel a user's active workflow.  Returns ``True`` if one existed."""
        if user_id in self._instances:
            logger.info("Workflow cancelled | user=%s", user_id)
            del self._instances[user_id]
            return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load(self, session: AsyncSession) -> None:
        """Load workflow definitions from the database."""
        result = await session.execute(select(Workflow).where(Workflow.enabled == True))
        rows = result.scalars().all()
        self._workflows = {}
        for r in rows:
            states_raw = r.states or []
            # Normalise states: if stored as list of dicts, index by name.
            if isinstance(states_raw, list):
                states_map: dict[str, dict[str, Any]] = {}
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

            self._workflows[r.name] = {
                "id": r.id,
                "name": r.name,
                "description": r.description or "",
                "trigger_intents": r.trigger_intents or [],
                "states": states_map,
                "forward_to": r.forward_to or "",
                "enabled": r.enabled,
            }
        logger.info("Loaded %d workflow definitions", len(self._workflows))

    @staticmethod
    def _get_state_def(wf_def: dict[str, Any], state_name: str) -> dict[str, Any]:
        """Safely retrieve a state definition from a workflow."""
        states: dict[str, Any] = wf_def.get("states", {})
        return states.get(state_name, {})

    @staticmethod
    def _find_next_state(wf_def: dict[str, Any], current: str) -> str | None:
        """Find the next state after *current* in the ordered state list."""
        states: dict[str, Any] = wf_def.get("states", {})
        keys = list(states.keys())
        try:
            idx = keys.index(current)
            if idx + 1 < len(keys):
                return keys[idx + 1]
        except ValueError:
            pass
        return None

    @staticmethod
    def _try_parse_order(msg: str) -> dict[str, Any] | None:
        """Attempt to parse a peiwang order from raw text.

        Supported format: ``游戏名 段位 时长 预算 [备注]``

        Example input: ``王者荣耀 钻石 3 150 带我一个``
        """
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

    @staticmethod
    def _build_action(action_type: str, reply: str = "") -> dict[str, Any]:
        """Build a standard action response dict."""
        return {
            "action": action_type,
            "reply": reply,
            "forward_targets": [],
            "ended": False,
        }

    def _cleanup(self, user_id: str) -> None:
        """Remove a user's workflow instance."""
        self._instances.pop(user_id, None)


class _SafeDict(dict):
    """Dict that returns ``""`` for missing keys."""

    def __missing__(self, key: str) -> str:
        return ""
