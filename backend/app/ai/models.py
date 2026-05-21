"""LLM 模型配置与工厂函数。

支持多个 LLM provider（DashScope / OpenAI / SiliconFlow），
通过统一的 create_llm 工厂函数创建 LangChain ChatOpenAI 实例。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from langchain_openai import ChatOpenAI

# Provider 类型
ProviderType = Literal["dashscope", "openai", "siliconflow", "deepseek"]

# 各 provider 默认 base_url
PROVIDER_BASE_URLS: dict[str, str] = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "deepseek": "https://api.deepseek.com",
}


@dataclass
class LLMConfig:
    """LLM 模型配置，封装不同 provider 的连接参数。

    Attributes:
        provider: LLM 提供商名称，支持 "dashscope" / "openai" / "siliconflow"。
        api_key: API 密钥，若为空则从环境变量读取。
        model: 模型名称。
        base_url: API base URL，若未指定则根据 provider 使用默认值。
        temperature: 生成温度 (0.0-2.0)，越高越随机。
        max_tokens: 单次生成最大 token 数。
        top_p: 核采样参数。
        frequency_penalty: 频率惩罚。
        presence_penalty: 存在惩罚。
    """

    provider: ProviderType = "dashscope"
    api_key: str = ""
    model: str = "qwen-plus"
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 2000
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url:
            self.base_url = PROVIDER_BASE_URLS.get(self.provider, PROVIDER_BASE_URLS["dashscope"])


def create_llm(config: LLMConfig | None = None, **kwargs) -> ChatOpenAI:
    """根据配置创建 LangChain ChatOpenAI 实例。

    支持 DashScope / OpenAI / SiliconFlow 等兼容 OpenAI 接口的 provider。

    Args:
        config: LLMConfig 实例。若为 None，则从 app.config.get_config() 读取 AI 配置。
        **kwargs: 额外的 ChatOpenAI 构造参数，会覆盖 config 中的对应字段。

    Returns:
        配置好的 ChatOpenAI 实例，可用于 LangChain Agent / Chain。

    Example:
        >>> from app.config import get_config
        >>> from app.ai.models import LLMConfig, create_llm
        >>> cfg = get_config()
        >>> llm_config = LLMConfig(
        ...     provider=cfg.ai["provider"],
        ...     api_key=cfg.ai["api_key"],
        ...     model=cfg.ai["model"],
        ... )
        >>> llm = create_llm(llm_config)
    """
    if config is None:
        from app.config import get_config
        import os

        cfg = get_config()
        ai_cfg = cfg.ai if isinstance(cfg.ai, dict) else {}
        api_key = ai_cfg.get("api_key", "") or os.getenv("DEEPSEEK_API_KEY", "")
        config = LLMConfig(
            provider=ai_cfg.get("provider", "dashscope"),
            api_key=api_key,
            base_url=ai_cfg.get("base_url", ""),
            model=ai_cfg.get("model", "qwen-plus"),
            temperature=ai_cfg.get("temperature", 0.7),
            max_tokens=ai_cfg.get("max_tokens", 2000),
        )

    # 构建 ChatOpenAI 参数
    init_kwargs: dict = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "top_p": config.top_p,
        "frequency_penalty": config.frequency_penalty,
        "presence_penalty": config.presence_penalty,
    }

    if config.api_key:
        init_kwargs["api_key"] = config.api_key
        init_kwargs["openai_api_key"] = config.api_key

    if config.base_url:
        init_kwargs["base_url"] = config.base_url
        init_kwargs["openai_api_base"] = config.base_url

    # 合并额外参数
    init_kwargs.update(config.extra)
    init_kwargs.update(kwargs)

    return ChatOpenAI(**init_kwargs)
