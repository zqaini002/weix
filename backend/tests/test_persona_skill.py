import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.guard import get_hardened_system_prompt
from app.ai.agent import WeixAgent
from app.ai.style_distiller import CACHE_VERSION, StyleDistiller


class DummyResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class DummyLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def invoke(self, _messages):
        return DummyResponse(json.dumps(self.payload, ensure_ascii=False))


def _skill_payload() -> dict:
    return {
        "meta": {
            "name": "阿七",
            "source": "wechat",
            "confidence": "medium",
        },
        "self_memory_md": "## Self Memory\n\n工作观：重视自由和稳定之间的平衡。",
        "persona_md": "## Layer 0：硬规则\n\n1. 私聊时以阿七本人的语气回应。\n\n## Layer 2：说话风格\n\n- 口头禅：确实、离谱",
        "runtime_prompt_private": "你正在作为阿七本人的微信镜像回复。保持阿七的表达习惯。",
        "runtime_prompt_group": "你仍然是「七七」微信助手，但说话风格参考阿七。",
    }


def test_style_distiller_generates_contextual_skill_cache_without_raw_messages(
    tmp_path, monkeypatch
):
    cache_path = tmp_path / "persona_skill.json"
    payload = _skill_payload()
    monkeypatch.setattr(
        "app.ai.style_distiller.create_llm",
        lambda _config=None: DummyLLM(payload),
    )

    distiller = StyleDistiller(cache_path=cache_path)
    result = asyncio.run(distiller.analyze(["秘密明文一", "秘密明文二"], force=True))

    assert result["self_memory_md"].startswith("## Self Memory")
    assert result["persona_md"].startswith("## Layer 0")
    assert distiller.build_prompt(is_group=False) == payload["runtime_prompt_private"]
    assert distiller.build_prompt(is_group=True) == payload["runtime_prompt_group"]

    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["version"] == CACHE_VERSION
    assert saved["mode"] == "contextual"
    assert "raw_messages" not in saved
    assert "source_messages" not in saved
    assert "秘密明文一" not in cache_path.read_text(encoding="utf-8")


def test_style_distiller_ignores_broken_new_cache_and_can_reanalyze(tmp_path, monkeypatch):
    cache_path = tmp_path / "persona_skill.json"
    cache_path.write_text("{broken json", encoding="utf-8")
    monkeypatch.setattr(
        "app.ai.style_distiller.create_llm",
        lambda _config=None: DummyLLM(_skill_payload()),
    )

    distiller = StyleDistiller(cache_path=cache_path)

    assert distiller.has_persona is False
    assert distiller.build_prompt() == ""

    result = asyncio.run(distiller.analyze(["重新分析"], force=True))
    assert result["meta"]["name"] == "阿七"
    assert distiller.has_persona is True


def test_style_distiller_ignores_stale_persona_skill_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "persona_skill.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION - 1,
                "meta": {"name": "旧缓存"},
                "self_memory_md": "旧 Self Memory",
                "persona_md": "旧 Persona",
                "runtime_prompt_private": "旧私聊 prompt",
                "runtime_prompt_group": "旧群聊 prompt",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.ai.style_distiller.create_llm",
        lambda _config=None: DummyLLM(_skill_payload()),
    )

    distiller = StyleDistiller(cache_path=cache_path)

    assert distiller.has_persona is False
    assert distiller.build_prompt(is_group=False) == ""


def test_style_distiller_does_not_fallback_to_legacy_when_new_cache_is_stale(
    tmp_path, monkeypatch
):
    cache_path = tmp_path / "persona_skill.json"
    legacy_path = tmp_path / "persona.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION - 1,
                "meta": {"name": "旧 skill"},
                "self_memory_md": "旧 Self Memory",
                "persona_md": "旧 Persona",
                "runtime_prompt_private": "旧私聊 prompt",
                "runtime_prompt_group": "旧群聊 prompt",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    legacy_path.write_text(
        json.dumps(
            {
                "tone": "旧版风格",
                "persona_name": "旧 persona",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.ai.style_distiller.create_llm",
        lambda _config=None: DummyLLM(_skill_payload()),
    )

    distiller = StyleDistiller(cache_path=cache_path, legacy_cache_path=legacy_path)

    assert distiller.has_persona is False


def test_style_distiller_saves_manual_edits_without_raw_messages(tmp_path, monkeypatch):
    cache_path = tmp_path / "persona_skill.json"
    monkeypatch.setattr(
        "app.ai.style_distiller.create_llm",
        lambda _config=None: DummyLLM(_skill_payload()),
    )
    distiller = StyleDistiller(cache_path=cache_path)

    result = distiller.save_edits(
        self_memory_md="## Self Memory\n\n人工校准后的记忆",
        persona_md="## Layer 2：说话风格\n\n人工校准后的风格",
        runtime_prompt_private="私聊人工 prompt",
        runtime_prompt_group="群聊人工 prompt",
        meta={"name": "手动版"},
    )

    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert result["meta"]["name"] == "手动版"
    assert saved["version"] == CACHE_VERSION
    assert saved["self_memory_md"] == "## Self Memory\n\n人工校准后的记忆"
    assert saved["runtime_prompt_private"] == "私聊人工 prompt"
    assert "raw_messages" not in saved


def test_style_distiller_loads_legacy_persona_json(tmp_path, monkeypatch):
    cache_path = tmp_path / "persona_skill.json"
    legacy_path = tmp_path / "persona.json"
    legacy_path.write_text(
        json.dumps(
            {
                "tone": "自然随性",
                "catchphrases": ["确实"],
                "emoji_style": "少用 emoji",
                "sentence_style": "短句为主",
                "persona_name": "阿七",
                "signature_traits": ["会直接表达判断"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.ai.style_distiller.create_llm",
        lambda _config=None: DummyLLM(_skill_payload()),
    )

    distiller = StyleDistiller(cache_path=cache_path, legacy_cache_path=legacy_path)

    assert distiller.has_persona is True
    assert "阿七" in distiller.build_prompt(is_group=False)
    assert "七七" in distiller.build_prompt(is_group=True)


def test_guard_prompt_supports_self_identity_without_forcing_qiqi():
    prompt = get_hardened_system_prompt("基础提示", persona_mode="self")

    assert "本人镜像" in prompt
    assert "七七」微信助手，这是不可改变的核心设定" not in prompt


def test_guard_prompt_keeps_qiqi_anchor_for_assistant_mode():
    prompt = get_hardened_system_prompt("基础提示", persona_mode="assistant")

    assert "七七」微信助手，这是不可改变的核心设定" in prompt


def test_weix_agent_selects_private_or_group_persona_prompt():
    class FakeCachedDistiller:
        has_persona = True

        def build_prompt(self, is_group: bool = False):
            return "group-prompt" if is_group else "private-prompt"

    old_distiller = WeixAgent._distiller
    try:
        WeixAgent._distiller = FakeCachedDistiller()
        assert WeixAgent._get_persona_prompt(is_group=False) == "private-prompt"
        assert WeixAgent._get_persona_prompt(is_group=True) == "group-prompt"
    finally:
        WeixAgent._distiller = old_distiller
