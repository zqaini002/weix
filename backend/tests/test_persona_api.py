import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api import persona as persona_api


SKILL = {
    "meta": {"name": "阿七"},
    "self_memory_md": "## Self Memory\n\n重视自由。",
    "persona_md": "## Layer 2：说话风格\n\n短句。",
    "runtime_prompt_private": "你正在作为阿七本人的微信镜像回复。",
    "runtime_prompt_group": "你仍然是「七七」微信助手。",
}


class FakeDistiller:
    cleared = False

    def __init__(self):
        self.has_persona = True
        self.mode = "contextual"
        self.meta = SKILL["meta"]
        self.self_memory_md = SKILL["self_memory_md"]
        self.persona_md = SKILL["persona_md"]

    @property
    def persona(self):
        return SKILL

    def build_prompt(self, is_group: bool = False):
        return SKILL["runtime_prompt_group" if is_group else "runtime_prompt_private"]

    async def analyze(self, messages, force: bool = False):
        assert messages == ["你好", "确实"]
        assert force is True
        return SKILL

    def save_edits(
        self,
        *,
        self_memory_md=None,
        persona_md=None,
        runtime_prompt_private=None,
        runtime_prompt_group=None,
        meta=None,
        mode=None,
    ):
        return {
            "mode": mode or "contextual",
            "meta": meta or SKILL["meta"],
            "self_memory_md": self_memory_md or SKILL["self_memory_md"],
            "persona_md": persona_md or SKILL["persona_md"],
            "runtime_prompt_private": runtime_prompt_private or SKILL["runtime_prompt_private"],
            "runtime_prompt_group": runtime_prompt_group or SKILL["runtime_prompt_group"],
        }

    def clear_cache(self):
        FakeDistiller.cleared = True


class FakeReader:
    def find_database_files(self):
        return ["/tmp/message_0.db"]

    def open_db(self, path, key):
        assert path == "/tmp/message_0.db"
        assert key == bytes.fromhex("aa" * 32)

    def get_my_messages(self, limit: int, since_days: int):
        assert limit == 123
        assert since_days == 45
        return [{"content": "你好"}, {"content": "确实"}]

    def close(self):
        pass


def test_get_persona_returns_skill_preview(monkeypatch):
    monkeypatch.setattr("app.ai.style_distiller.StyleDistiller", FakeDistiller)

    result = asyncio.run(persona_api.get_persona())

    assert result == {
        "ready": True,
        "mode": "contextual",
        "meta": {"name": "阿七"},
        "self_memory": SKILL["self_memory_md"],
        "persona": SKILL["persona_md"],
        "private_prompt": SKILL["runtime_prompt_private"],
        "group_prompt": SKILL["runtime_prompt_group"],
    }


def test_analyze_persona_uses_configured_limits_and_returns_skill(monkeypatch):
    monkeypatch.setattr("app.ai.style_distiller.StyleDistiller", FakeDistiller)
    monkeypatch.setattr(
        "app.core.platform.Platform.get",
        lambda: SimpleNamespace(
            key_extractor=SimpleNamespace(
                load_keys=lambda: {"message_0.db": "aa" * 32}
            )
        ),
    )
    monkeypatch.setattr("app.core.db_reader_macos.MacOSDBReader", FakeReader)
    monkeypatch.setattr(
        persona_api,
        "get_config",
        lambda: SimpleNamespace(
            ai={"persona_since_days": 45, "persona_message_limit": 123}
        ),
        raising=False,
    )

    result = asyncio.run(persona_api.analyze_persona(force=True))

    assert result["success"] is True
    assert result["total_messages"] == 2
    assert result["sample_size"] == 2
    assert result["self_memory"] == SKILL["self_memory_md"]
    assert result["persona"] == SKILL["persona_md"]
    assert result["private_prompt"] == SKILL["runtime_prompt_private"]
    assert result["group_prompt"] == SKILL["runtime_prompt_group"]


def test_analyze_persona_resets_agent_distiller_cache(monkeypatch):
    from app.ai.agent import WeixAgent

    stale_distiller = object()
    old_distiller = WeixAgent._distiller
    WeixAgent._distiller = stale_distiller
    monkeypatch.setattr("app.ai.style_distiller.StyleDistiller", FakeDistiller)
    monkeypatch.setattr(
        "app.core.platform.Platform.get",
        lambda: SimpleNamespace(
            key_extractor=SimpleNamespace(
                load_keys=lambda: {"message_0.db": "aa" * 32}
            )
        ),
    )
    monkeypatch.setattr("app.core.db_reader_macos.MacOSDBReader", FakeReader)
    monkeypatch.setattr(
        persona_api,
        "get_config",
        lambda: SimpleNamespace(
            ai={"persona_since_days": 45, "persona_message_limit": 123}
        ),
        raising=False,
    )

    try:
        result = asyncio.run(persona_api.analyze_persona(force=True))
        assert result["success"] is True
        assert WeixAgent._distiller is None
    finally:
        WeixAgent._distiller = old_distiller


def test_update_persona_saves_edits_and_resets_agent_cache(monkeypatch):
    from app.ai.agent import WeixAgent

    stale_distiller = object()
    old_distiller = WeixAgent._distiller
    WeixAgent._distiller = stale_distiller
    monkeypatch.setattr("app.ai.style_distiller.StyleDistiller", FakeDistiller)

    try:
        result = asyncio.run(
            persona_api.update_persona(
                persona_api.PersonaUpdateRequest(
                    meta={"name": "手动版"},
                    self_memory="## Self Memory\n\n人工记忆",
                    persona="## Persona\n\n人工风格",
                    private_prompt="私聊人工 prompt",
                    group_prompt="群聊人工 prompt",
                )
            )
        )

        assert result["success"] is True
        assert result["ready"] is True
        assert result["meta"] == {"name": "手动版"}
        assert result["self_memory"] == "## Self Memory\n\n人工记忆"
        assert result["persona"] == "## Persona\n\n人工风格"
        assert result["private_prompt"] == "私聊人工 prompt"
        assert result["group_prompt"] == "群聊人工 prompt"
        assert WeixAgent._distiller is None
    finally:
        WeixAgent._distiller = old_distiller


def test_clear_persona_resets_distiller_cache(monkeypatch):
    FakeDistiller.cleared = False
    monkeypatch.setattr("app.ai.style_distiller.StyleDistiller", FakeDistiller)

    result = asyncio.run(persona_api.clear_persona())

    assert result["success"] is True
    assert FakeDistiller.cleared is True
