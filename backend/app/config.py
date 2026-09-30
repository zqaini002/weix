import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic_settings import BaseSettings

from app.utils.paths import get_base_dir, get_config_dir


class Config(BaseSettings):
    """Application configuration loaded from YAML with env override."""

    platform: str = "auto"
    workflow_engine: str = "legacy"  # "legacy" | "langgraph"
    wechat: dict[str, Any] = {}
    wcf: dict[str, Any] = {}
    macos_sender: dict[str, Any] = {}
    windows_sender: dict[str, Any] = {}
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
            path = os.getenv("WEIX_CONFIG", str(get_config_dir() / "config.yaml"))

        if not Path(path).exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        local_env = {
            key: value
            for key, value in dotenv_values(get_base_dir() / ".env").items()
            if value is not None
        }
        raw = cls._resolve_env(raw, {**local_env, **os.environ})
        monitor = raw.setdefault("monitor", {})
        monitor.setdefault("source", "auto")
        # Older packages used "vision" for incoming messages. Migrate those
        # configs to database monitoring so an upgrade never reads chats from
        # screenshots or sends a reply without a verified local DB key.
        if monitor["source"] == "vision":
            monitor["source"] = "database"
        if monitor["source"] not in {"auto", "database"}:
            raise ValueError("monitor.source must be auto or database")
        return cls(**raw)

    @staticmethod
    def _resolve_env(data: Any, env: dict[str, str] | None = None) -> Any:
        """Recursively resolve ${ENV_VAR} and ${ENV_VAR:-default} patterns in config values."""
        import re

        env_pattern = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")
        env = os.environ if env is None else env

        if isinstance(data, dict):
            return {k: Config._resolve_env(v, env) for k, v in data.items()}
        elif isinstance(data, list):
            return [Config._resolve_env(v, env) for v in data]
        elif isinstance(data, str):
            def replacer(m):
                var_name = m.group(1)
                default = m.group(2)
                return env.get(var_name, default or "")
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
