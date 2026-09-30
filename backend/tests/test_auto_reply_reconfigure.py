from types import SimpleNamespace

import pytest
import yaml

from app import main
from app.api import config as config_api


@pytest.mark.asyncio
async def test_reconfigure_stops_old_pipeline_and_starts_from_current_config(monkeypatch):
    events = []

    class OldPipeline:
        async def stop(self):
            events.append("stop")

    async def create(reason, *, lookback_seconds=60.0):
        events.append((reason, lookback_seconds))
        return object()

    monkeypatch.setattr(main, "_pipeline", OldPipeline())
    monkeypatch.setattr(main, "_shutdown_event", SimpleNamespace(is_set=lambda: False))
    monkeypatch.setattr(main, "_create_auto_reply_pipeline", create)

    result = await main.reconfigure_auto_reply_pipeline()

    assert events == ["stop", ("Web 聊天配置已保存", 0.0)]
    assert result["running"] is True


@pytest.mark.asyncio
async def test_web_chat_save_reconfigures_running_pipeline(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("auto_reply:\n  private_whitelist: []\n", encoding="utf-8")
    cfg = SimpleNamespace(auto_reply={"private_whitelist": []})
    monkeypatch.setattr(config_api, "get_config", lambda: cfg)
    monkeypatch.setattr(config_api, "_get_config_path", lambda: str(path))
    calls = []

    async def reconfigure():
        calls.append(list(cfg.auto_reply["private_whitelist"]))
        return {"running": True, "reason": ""}

    monkeypatch.setattr(main, "reconfigure_auto_reply_pipeline", reconfigure, raising=False)

    result = await config_api.update_chat_config({"private_whitelist": ["任意联系人"]})

    assert calls == [["任意联系人"]]
    assert result["auto_reply_running"] is True
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["auto_reply"]["private_whitelist"] == ["任意联系人"]
