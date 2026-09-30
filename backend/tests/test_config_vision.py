"""Local source selection and secret loading for vision mode."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Config


def _config_file(tmp_path, source_line=""):
    path = tmp_path / "config.yaml"
    path.write_text(
        "ai:\n  api_key: ${DEEPSEEK_API_KEY:-}\nmonitor:\n  poll_interval: 2\n"
        + source_line,
        encoding="utf-8",
    )
    return path


def test_missing_monitor_source_defaults_to_auto(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_base_dir", lambda: tmp_path)
    config = Config.from_yaml(str(_config_file(tmp_path)))
    assert config.monitor["source"] == "auto"


def test_legacy_vision_source_is_migrated_to_database(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_base_dir", lambda: tmp_path)
    config = Config.from_yaml(str(_config_file(tmp_path, "  source: vision\n")))
    assert config.monitor["source"] == "database"


def test_local_env_supplies_deepseek_key_without_mutating_process_env(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_base_dir", lambda: tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=local-test-key\n", encoding="utf-8")
    config = Config.from_yaml(str(_config_file(tmp_path)))
    assert config.ai["api_key"] == "local-test-key"
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_process_env_takes_priority_over_local_env(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_base_dir", lambda: tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-test-key")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=local-test-key\n", encoding="utf-8")
    config = Config.from_yaml(str(_config_file(tmp_path)))
    assert config.ai["api_key"] == "process-test-key"


def test_invalid_monitor_source_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_base_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="monitor.source"):
        Config.from_yaml(str(_config_file(tmp_path, "  source: unknown\n")))
