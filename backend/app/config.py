import os
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Application configuration loaded from YAML with env override."""

    platform: str = "auto"
    workflow_engine: str = "legacy"  # "legacy" | "langgraph"
    wechat: dict[str, Any] = {}
    wcf: dict[str, Any] = {}
    macos_sender: dict[str, Any] = {}
    ai: dict[str, Any] = {}
    auto_reply: dict[str, Any] = {}
    templates: list[dict[str, Any]] = []
    workflows: list[dict[str, Any]] = []
    forward_rules: list[dict[str, Any]] = []
    anti_detect: dict[str, Any] = {}
    statistics: dict[str, Any] = {}
    admin: dict[str, Any] = {}
    database: dict[str, Any] = {}
    monitor: dict[str, Any] = {}

    @classmethod
    def from_yaml(cls, path: str | None = None) -> "Config":
        if path is None:
            path = os.getenv("WEIX_CONFIG", str(Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"))

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        raw = cls._resolve_env(raw)
        return cls(**raw)

    @staticmethod
    def _resolve_env(data: Any) -> Any:
        """Recursively resolve ${ENV_VAR} and ${ENV_VAR:-default} patterns in config values."""
        import re

        env_pattern = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")

        if isinstance(data, dict):
            return {k: Config._resolve_env(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [Config._resolve_env(v) for v in data]
        elif isinstance(data, str):
            def replacer(m):
                var_name = m.group(1)
                default = m.group(2)
                return os.getenv(var_name, default or "")
            return env_pattern.sub(replacer, data)
        return data

    def get_platform(self) -> str:
        if self.platform != "auto":
            return self.platform
        return "win32" if sys.platform == "win32" else "darwin"


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_yaml()
    return _config
