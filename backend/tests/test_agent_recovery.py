import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.agent import WeixAgent


def test_discard_checkpoint_removes_failed_thread(monkeypatch):
    agent = WeixAgent.__new__(WeixAgent)
    agent._checkpointer = SimpleNamespace(
        _checkpoints={
            "private:bad": {"checkpoint": {}, "metadata": {}, "channel_values": {}},
            "private:ok": {"checkpoint": {}, "metadata": {}, "channel_values": {}},
        }
    )
    saved = {"called": False}

    def fake_save():
        saved["called"] = True

    monkeypatch.setattr(agent, "_save_checkpoints", fake_save)

    agent._discard_checkpoint("private:bad")

    assert "private:bad" not in agent._checkpointer._checkpoints
    assert "private:ok" in agent._checkpointer._checkpoints
    assert saved["called"] is True
