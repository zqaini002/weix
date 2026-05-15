"""Token 计数和上下文窗口管理模块。

管理 LLM 上下文窗口的 token 使用量：
- 统计消息的 token 数量
- 在达到上下文窗口上限时自动裁剪历史消息
- 预留 30% 上下文给输出 token
"""

from __future__ import annotations

import logging
from typing import Any

import tiktoken
from langchain_core.messages import BaseMessage, trim_messages

logger = logging.getLogger(__name__)

# 各模型默认上下文窗口大小
MODEL_CONTEXT_SIZES: dict[str, int] = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 4096,
    "deepseek-v4-pro": 128000,
    "deepseek-chat": 64000,
    "deepseek-reasoner": 64000,
    "qwen-plus": 131072,
    "qwen-max": 32768,
    "qwen-turbo": 8192,
}

OUTPUT_RESERVE_RATIO = 0.3


class TokenManager:
    """管理 LLM 上下文窗口的 Token 使用量。

    Attributes:
        model_name: 模型名称。
        context_size: 模型总上下文窗口大小。
        max_input_tokens: 输入 token 上限（已预留输出空间）。
    """

    def __init__(
        self,
        model_name: str,
        max_input_tokens: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.context_size = max_input_tokens or self._guess_context_size(model_name)
        self.max_input_tokens = int(
            self.context_size * (1 - OUTPUT_RESERVE_RATIO)
        )

        try:
            self._encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            self._encoding = tiktoken.get_encoding("cl100k_base")

        logger.debug(
            f"TokenManager: model={model_name}, context={self.context_size}, "
            f"max_input={self.max_input_tokens}"
        )

    # ------------------------------------------------------------------
    # Token 计数
    # ------------------------------------------------------------------

    def count_tokens(self, content: str | list[BaseMessage] | Any) -> int:
        """统计文本或消息列表的总 token 数。"""
        if isinstance(content, str):
            return len(self._encoding.encode(content))

        if isinstance(content, list):
            total = 0
            for msg in content:
                if hasattr(msg, "content"):
                    text = msg.content or ""
                elif isinstance(msg, str):
                    text = msg
                else:
                    text = str(msg)
                total += len(self._encoding.encode(str(text)))
            return total

        text = str(content) if content else ""
        return len(self._encoding.encode(text))

    # ------------------------------------------------------------------
    # 上下文裁剪
    # ------------------------------------------------------------------

    def trim(
        self,
        messages: list[BaseMessage],
        system_prompt: str = "",
    ) -> list[BaseMessage]:
        """按上下文窗口限制裁剪消息，保留 system prompt 空间和最近的消息。

        Args:
            messages: 消息列表。
            system_prompt: 系统提示词文本（不计入裁剪但占据 token 配额）。

        Returns:
            裁剪后的消息列表。
        """
        system_tokens = len(self._encoding.encode(system_prompt)) if system_prompt else 0
        available = max(self.max_input_tokens - system_tokens - 500, 500)

        try:
            trimmed = trim_messages(
                messages,
                max_tokens=available,
                strategy="last",
                token_counter=self._encoding,
                include_system=False,
            )
            if len(trimmed) < len(messages):
                logger.debug(
                    f"Trimmed {len(messages) - len(trimmed)} messages "
                    f"(available={available} tokens)"
                )
            return trimmed
        except Exception as e:
            logger.warning(f"trim_messages failed, falling back to last 20 messages: {e}")
            return messages[-20:]

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @classmethod
    def _guess_context_size(cls, model_name: str) -> int:
        """根据模型名猜测上下文窗口大小。"""
        for key, size in MODEL_CONTEXT_SIZES.items():
            if key in model_name.lower():
                return size
        logger.warning(f"Unknown model '{model_name}', defaulting to 8192 context")
        return 8192
