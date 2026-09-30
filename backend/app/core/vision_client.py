"""Fail-closed DeepSeek visual decisions shared by both desktop platforms."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    x: int
    y: int
    visible_name: str


@dataclass(frozen=True)
class VisualBubble:
    side: Literal["self", "other"]
    content: str


class VisionClient:
    def __init__(self, api_key: str, *, timeout: float = 30):
        self._api_key = api_key
        self._timeout = timeout

    def _ask(self, image_png: bytes, instruction: str) -> dict | None:
        if not self._api_key or not image_png:
            return None
        encoded = base64.b64encode(image_png).decode("ascii")
        try:
            response = httpx.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": "deepseek-flash",
                    "thinking": {"type": "disabled"},
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "max_tokens": 800,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                    ]}],
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            logger.warning("视觉模型请求或响应失败；已停止本次操作")
            return None

    def locate_search_result(
        self, image_png: bytes, target: str, image_size: tuple[int, int]
    ) -> SearchHit | None:
        if not target or len(image_size) != 2:
            return None
        width, height = image_size
        data = self._ask(
            image_png,
            f"Locate the WeChat CONTACT search result for {json.dumps(target, ensure_ascii=False)} "
            f"in this {width}x{height} screenshot. Match the visible contact row visually; "
            "do not use a chat-history match, search input, web result, or group. "
            "Return JSON {found:boolean,x:integer,y:integer,visible_name:string,"
            "result_type:string,matching_contact_count:integer,all_results_visible:boolean}. "
            "Coordinates must be the center of the contact row relative to the image. "
            "Contact rows may appear under Contacts or Recent/Frequently Used (最近使用). "
            "Only CONTACT rows count for matching_contact_count and all_results_visible. "
            "Group, history, favorites and web sections extending below the screenshot "
            "do not make contact results incomplete. "
            "Count ALL contact rows matching the target, including duplicate names. "
            "If more than one matches, if CONTACT rows continue below the screenshot, or if "
            "you cannot determine uniqueness, return found:false and all_results_visible:false. "
            "When the visual identity is clear, set visible_name to the requested name; "
            "when unsure, return found:false.",
        )
        if (not data or data.get("found") is not True
                or data.get("result_type") != "contact"
                or type(data.get("matching_contact_count")) is not int
                or data["matching_contact_count"] != 1
                or data.get("all_results_visible") is not True):
            logger.warning("视觉搜索拒绝 | found=%s | unique_contact=%s | contact_results_complete=%s",
                           data.get("found") if data else None,
                           data.get("matching_contact_count") == 1 if data else False,
                           data.get("all_results_visible") if data else None)
            return None
        x, y = data.get("x"), data.get("y")
        if (type(x) is not int or type(y) is not int or not 0 <= x < width or not 0 <= y < height
                or data.get("visible_name") != target):
            return None
        return SearchHit(x, y, target)

    def classify_chat_title(self, image_png: bytes, candidates: list[str]) -> str | None:
        if not candidates:
            return None
        data = self._ask(
            image_png,
            "Identify the open WeChat chat title visually. Choose exactly one of "
            f"{json.dumps(candidates + ['OTHER'], ensure_ascii=False)}. "
            "Return JSON {name:string}. Choose OTHER if ambiguous."
        )
        name = data.get("name") if data else None
        if name not in candidates:
            logger.warning("视觉聊天标题未匹配允许的目标；已停止本次操作")
        return name if name in candidates else None

    def read_visible_messages(self, image_png: bytes) -> list[VisualBubble] | None:
        data = self._ask(
            image_png,
            "Read visible WeChat TEXT message bubbles in order top to bottom. "
            "Classify by bubble alignment and avatar side, not by text meaning: "
            "green bubbles aligned to the right beside the right avatar are self; "
            "white bubbles aligned to the left beside the left avatar are other. "
            "Exclude timestamps, images, system notices and composer text. "
            "Return JSON {bubbles:[{side:'other'|'self',content:string}]}. "
            "If no message bubbles are visible, return {bubbles:[]}. "
            "If uncertain about a bubble, omit it; only if the screenshot is unreadable, return {bubbles:null}."
        )
        if not data or not isinstance(data.get("bubbles"), list):
            return None
        output = []
        for item in data["bubbles"]:
            if (not isinstance(item, dict) or item.get("side") not in ("self", "other")
                    or not isinstance(item.get("content"), str)
                    or not item["content"].strip()):
                return None
            output.append(VisualBubble(item["side"], item["content"].strip()))
        return output
