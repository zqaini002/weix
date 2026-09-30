"""
Rule matching engine with three-tier matching strategy.

Matching priority (high to low):
  1. keyword_match  - exact keyword matching
  2. regex_match    - regex pattern matching with named group extraction
  3. intent_match   - intent keyword detection (local, no AI call)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AutoReplyRule

logger = logging.getLogger(__name__)


class RuleEngine:
    """Three-tier rule matching engine with hot-reload support.

    Loads rules from the AutoReplyRule table and applies them in priority order:
    keyword > regex > intent.  Each tier returns immediately on the first match.
    """

    def __init__(self, session_factory: Any = None) -> None:
        """Initialise the rule engine.

        Args:
            session_factory: An async session factory (e.g. async_sessionmaker)
                             used to reload rules from the database.
        """
        self._session_factory = session_factory
        self._rules: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load_rules(self, session: AsyncSession | None = None) -> None:
        """Load (or reload) rules from the database.

        Pass an explicit *session* for one-shot loading, or omit it to use
        the stored *session_factory*.
        """
        if session is not None:
            await self._load(session)
        elif self._session_factory is not None:
            async with self._session_factory() as sess:
                await self._load(sess)
        else:
            logger.error("No session or session_factory available; cannot load rules.")

    async def match(self, msg: str, session: AsyncSession | None = None) -> dict[str, Any]:
        """Match *msg* against all rules in priority order.

        Returns:
            A dict with keys:
              - matched (bool)
              - reply (str)       -- rendered reply text
              - rule (dict)       -- the matched rule row (empty on miss)
              - workflow (str)    -- workflow name to trigger (empty on miss)
        """
        # Ensure rules are loaded on first call (lazy initialisation).
        if not self._rules:
            await self.load_rules(session=session)

        msg_stripped = msg.strip()

        for matcher in (self.keyword_match, self.regex_match, self.intent_match):
            result = matcher(msg_stripped)
            if result["matched"]:
                logger.info("Rule matched | rule=%s | tier=%s", result["rule"].get("name"), matcher.__name__)
                return result

        logger.debug("No rule matched for message: %s", msg_stripped[:80])
        return {"matched": False, "reply": "", "rule": {}, "workflow": ""}

    async def hot_reload(self) -> int:
        """Reload rules from the database without restarting.

        Returns:
            The number of rules loaded.
        """
        await self.load_rules()
        logger.info("Hot-reload complete | loaded %d rules", len(self._rules))
        return len(self._rules)

    # ------------------------------------------------------------------
    # Tier 1 – Keyword matching
    # ------------------------------------------------------------------

    def keyword_match(self, msg: str) -> dict[str, Any]:
        """Exact keyword matching against the *patterns* field.

        The rule is triggered when *msg* equals one of the keywords
        (case-insensitive).  Returns as soon as a match is found.
        """
        msg_lower = msg.lower()
        for rule in self._rules:
            if rule.get("type") != "keyword" or not rule.get("enabled"):
                continue
            for kw in rule.get("patterns", []):
                if isinstance(kw, str) and kw.strip().lower() == msg_lower:
                    return {
                        "matched": True,
                        "reply": rule.get("reply", ""),
                        "rule": rule,
                        "workflow": rule.get("workflow", ""),
                    }
        return {"matched": False, "reply": "", "rule": {}, "workflow": ""}

    # ------------------------------------------------------------------
    # Tier 2 – Regex matching
    # ------------------------------------------------------------------

    def regex_match(self, msg: str) -> dict[str, Any]:
        """Regex pattern matching with named-group extraction.

        Patterns are compiled once per rule load.  Named groups (e.g.
        ``(?P<game>\\w+)``) are extracted and used to render the reply
        template via ``str.format_map``.
        """
        for rule in self._rules:
            if rule.get("type") != "regex" or not rule.get("enabled"):
                continue
            compiled = rule.get("_compiled", [])
            patterns_list = rule.get("patterns", [])
            # Compile patterns lazily.
            if not compiled and patterns_list:
                compiled = [re.compile(p, re.IGNORECASE) for p in patterns_list if isinstance(p, str)]
                rule["_compiled"] = compiled

            for pattern_obj in compiled:
                m = pattern_obj.search(msg)
                if m:
                    variables = m.groupdict()
                    reply_tpl = rule.get("reply", "")
                    try:
                        reply = reply_tpl.format_map(_SafeDict(variables))
                    except Exception:
                        reply = reply_tpl
                    return {
                        "matched": True,
                        "reply": reply,
                        "rule": rule,
                        "workflow": rule.get("workflow", ""),
                        "variables": variables,
                    }
        return {"matched": False, "reply": "", "rule": {}, "workflow": ""}

    # ------------------------------------------------------------------
    # Tier 3 – Intent matching (local keyword detection)
    # ------------------------------------------------------------------

    # Default intent keywords (used when a rule has no trigger_intents set).
    _DEFAULT_INTENTS: dict[str, list[str]] = {
        "order": ["点单", "下单", "陪玩", "代练", "排位", "上分"],
        "help":  ["帮助", "help", "说明", "怎么用", "功能"],
        "greet": ["你好", "嗨", "hello", "在吗", "hi"],
    }

    def intent_match(self, msg: str) -> dict[str, Any]:
        """Local intent detection based on keywords.

        Walks every intent-type rule and checks whether the user message
        contains any of the trigger keywords.  No AI call is made.

        Intent names are read from ``trigger_intents`` if the model has it,
        falling back to ``patterns`` (the patterns list doubles as intent
        names for intent-type rules).
        """
        msg_lower = msg.lower()
        for rule in self._rules:
            if rule.get("type") != "intent" or not rule.get("enabled"):
                continue

            # trigger_intents (if the field exists) takes priority,
            # otherwise fall back to using patterns as intent names.
            intents: list[str] = rule.get("trigger_intents", []) or rule.get("patterns", [])
            if not intents:
                continue

            for intent_name in intents:
                keywords: list[str] = self._DEFAULT_INTENTS.get(intent_name, [])
                for kw in keywords:
                    if kw in msg_lower:
                        try:
                            reply = rule.get("reply", "").format_map(
                                _SafeDict({"intent": intent_name})
                            )
                        except Exception:
                            reply = rule.get("reply", "")
                        return {
                            "matched": True,
                            "reply": reply,
                            "rule": rule,
                            "workflow": rule.get("workflow", ""),
                            "intent": intent_name,
                        }
        return {"matched": False, "reply": "", "rule": {}, "workflow": ""}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load(self, session: AsyncSession) -> None:
        """Internal loader that queries the database."""
        result = await session.execute(
            select(AutoReplyRule).order_by(AutoReplyRule.priority.desc())
        )
        rows = result.scalars().all()
        self._rules = [
            {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "patterns": r.patterns or [],
                "trigger_intents": getattr(r, "trigger_intents", None) or [],
                "reply": r.reply or "",
                "workflow": r.workflow or "",
                "priority": r.priority or 0,
                "enabled": r.enabled,
            }
            for r in rows
        ]
        logger.info("Loaded %d auto-reply rules from the database", len(self._rules))


class _SafeDict(dict):
    """A dict subclass that returns empty string for missing keys.

    Used by ``str.format_map`` to avoid ``KeyError`` when a template
    references a variable that was not captured.
    """

    def __missing__(self, key: str) -> str:
        return ""
