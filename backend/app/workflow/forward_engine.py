"""
Forward engine for matching and dispatching messages to target groups.

Supports two trigger modes:
  - workflow event triggers  (e.g. ``workflow:peiwang_order_flow.FORWARD``)
  - keyword triggers         (e.g. ``keyword:陪玩,代练``)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ForwardRule

logger = logging.getLogger(__name__)


class ForwardEngine:
    """Matches incoming contexts against forward rules and renders
    forward-target payloads ready for dispatch.
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory
        self._rules: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load_rules(self, session: AsyncSession | None = None) -> None:
        """Load forward rules from the database."""
        if session is not None:
            await self._load(session)
        elif self._session_factory is not None:
            async with self._session_factory() as sess:
                await self._load(sess)
        else:
            logger.error("No session or session_factory available; cannot load forward rules.")

    async def match_and_forward(
        self,
        context: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Match the given *context* against all forward rules.

        *context* is expected to contain at least:
          - ``trigger`` (str)  -- e.g. ``"workflow:peiwang_order_flow.FORWARD"``
          - ``data`` (dict)    -- variables for template rendering
          - ``user_id`` (str)  -- the originating user

        Returns:
            A list of forward target dicts::

                [
                    {
                        "target": "xxx@chatroom",
                        "template": "peiwang_forward_card",
                        "rendered_msg": "rendered message...",
                    }
                ]
        """
        if not self._rules:
            await self.load_rules(session=session)

        trigger = context.get("trigger", "")
        data: dict[str, Any] = context.get("data", {})
        results: list[dict[str, Any]] = []

        for rule in self._rules:
            if not rule.get("enabled"):
                continue
            if not self._trigger_matches(trigger, rule.get("trigger", "")):
                continue

            # Render the template with the data dict.
            tpl_name = rule.get("template", "")
            rendered = await self._render_template(tpl_name, data, session=session)

            for target in rule.get("targets", []):
                results.append({
                    "target": target,
                    "template": tpl_name,
                    "rendered_msg": rendered,
                })

        logger.info(
            "Forward match: trigger=%s | matched_rules=%d | total_targets=%d",
            trigger,
            len({r.get("name") for r in self._rules if self._trigger_matches(trigger, r.get("trigger", ""))}),
            len(results),
        )
        return results

    async def send_to_targets(
        self,
        forward_list: list[dict[str, Any]],
        sender: Any,
    ) -> list[dict[str, Any]]:
        """Send rendered messages to each target via *sender*.

        *sender* must have an async ``send_text(msg, receiver, aters="")``
        method (see ``BaseMessageSender``).

        Returns:
            A list of per-target send results::

                [{"target": "...", "success": True/False, "error": "..."}]
        """
        results: list[dict[str, Any]] = []
        for item in forward_list:
            target = item["target"]
            rendered = item.get("rendered_msg", "")
            try:
                success = await sender.send_text(rendered, target)
                results.append({"target": target, "success": success, "error": "" if success else "send returned False"})
                logger.info("Forwarded message to %s | success=%s", target, success)
            except Exception as exc:
                results.append({"target": target, "success": False, "error": str(exc)})
                logger.error("Failed to forward to %s: %s", target, exc)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load(self, session: AsyncSession) -> None:
        """Load forward rules from the database."""
        result = await session.execute(
            select(ForwardRule).where(ForwardRule.enabled == True)
        )
        rows = result.scalars().all()
        self._rules = [
            {
                "id": r.id,
                "name": r.name,
                "trigger": r.trigger or "",
                "targets": r.targets or [],
                "template": r.template or "",
                "enabled": r.enabled,
            }
            for r in rows
        ]
        logger.info("Loaded %d forward rules", len(self._rules))

    @staticmethod
    def _trigger_matches(ctx_trigger: str, rule_trigger: str) -> bool:
        """Check whether *ctx_trigger* matches *rule_trigger*.

        Supports these rule-trigger formats:

        - ``workflow:flow_name.STATE`` -- exact match
        - ``keyword:kw1,kw2,kw3``     -- contains any keyword
        """
        if not rule_trigger:
            return False

        # workflow:<name>.<STATE>
        if rule_trigger.startswith("workflow:"):
            # Exact match (or suffix match on state).
            if rule_trigger == ctx_trigger:
                return True
            # Partial: "workflow:flow_name" matches "workflow:flow_name.STATE"
            if not rule_trigger.endswith(".*"):
                # Match prefix e.g. "workflow:peiwang_order_flow" matches
                # "workflow:peiwang_order_flow.FORWARD"
                if ctx_trigger.startswith(rule_trigger):
                    return True
            return False

        # keyword:kw1,kw2,kw3
        if rule_trigger.startswith("keyword:"):
            kw_part = rule_trigger[len("keyword:"):]
            keywords = [k.strip() for k in kw_part.split(",") if k.strip()]
            return any(kw in ctx_trigger for kw in keywords)

        # Fallback -- substring match
        return rule_trigger.lower() in ctx_trigger.lower()

    async def _render_template(
        self,
        template_name: str,
        variables: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> str:
        """Render a named template with variables.

        If a local template engine is available it delegates rendering;
        otherwise a simple ``str.format_map`` fallback is used.
        """
        if not template_name:
            # No template -- use the raw data as a formatted string.
            parts = [f"{k}: {v}" for k, v in variables.items()]
            return "\n".join(parts)

        # Attempt to use the stored template engine.
        try:
            from app.workflow.template_engine import TemplateEngine

            engine = TemplateEngine(session_factory=self._session_factory)
            return await engine.render(template_name, variables, session=session)
        except Exception:
            logger.debug("Template engine unavailable for rename: %s", template_name)
            # Fallback: treat template_name as a literal format string.
            try:
                return template_name.format_map(_SafeDict(variables))
            except Exception:
                return template_name


class _SafeDict(dict):
    """Dict that returns ``""`` for missing keys."""

    def __missing__(self, key: str) -> str:
        return ""
