import hashlib
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai import tools as ai_tools


def test_search_web_missing_dependency_returns_tool_result(monkeypatch):
    """搜索依赖缺失时必须返回 ToolMessage 内容，不能让 Agent 历史损坏。"""

    class BrokenSearch:
        def __init__(self):
            raise ImportError("missing ddgs")

    monkeypatch.setattr(ai_tools, "DuckDuckGoSearchRun", BrokenSearch)

    result = ai_tools.search_web.invoke("今天天气")

    assert "搜索工具暂时不可用" in result
    assert "ddgs" in result


def test_get_weather_requires_city_before_calling_amap():
    result = ai_tools.get_weather.invoke("")

    assert "哪个城市" in result


def test_create_tools_includes_weather_tool_by_default():
    tool_names = [tool.name for tool in ai_tools.create_tools()]

    assert "get_weather" in tool_names


def test_get_weather_uses_amap_and_formats_live_weather(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "lives": [
                    {
                        "province": "贵州",
                        "city": "贵阳市",
                        "weather": "多云",
                        "temperature": "22",
                        "winddirection": "南",
                        "windpower": "≤3",
                        "humidity": "68",
                        "reporttime": "2026-05-15 15:00:00",
                    }
                ],
            }

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        ai_tools,
        "get_config",
        lambda: SimpleNamespace(
            ai={
                "amap_key": "test-key",
                "amap_security_key": "secret",
            }
        ),
    )
    monkeypatch.setattr(ai_tools.httpx, "get", fake_get)

    result = ai_tools.get_weather.invoke("贵阳")

    expected_sig_base = (
        "city=贵阳&extensions=base&key=test-key&output=JSONsecret"
    )
    assert captured["url"].endswith("/v3/weather/weatherInfo")
    assert captured["params"]["key"] == "test-key"
    assert captured["params"]["city"] == "贵阳"
    assert captured["params"]["sig"] == hashlib.md5(
        expected_sig_base.encode("utf-8")
    ).hexdigest()
    assert "贵阳市天气：多云，22°C" in result
    assert "湿度 68%" in result
