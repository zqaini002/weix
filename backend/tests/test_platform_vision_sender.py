from types import SimpleNamespace

from app.core.platform import Platform


def test_platform_sender_uses_local_vision_key(monkeypatch):
    monkeypatch.setattr("app.core.platform.get_config", lambda: SimpleNamespace(
        get_platform=lambda: "win32", ai={"api_key": "local-key"}, windows_sender={}
    ))
    platform = Platform()
    assert platform.sender._vision_client is not None
