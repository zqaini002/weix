import json
from unittest.mock import Mock

import httpx

from app.core.vision_client import VisionClient


def client_reply(monkeypatch, payload):
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}
    response.raise_for_status.return_value = None
    post = Mock(return_value=response)
    monkeypatch.setattr(httpx, "post", post)
    return post


def test_locates_contact_row_and_sends_vision_request(monkeypatch):
    post = client_reply(monkeypatch, {"found": True, "x": 80, "y": 100, "visible_name": "小号", "result_type": "contact", "matching_contact_count": 1, "all_results_visible": True})
    hit = VisionClient("secret").locate_search_result(b"PNG", "小号", (245, 350))
    assert (hit.x, hit.y, hit.visible_name) == (80, 100, "小号")
    body = post.call_args.kwargs["json"]
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0
    instruction = body["messages"][0]["content"][0]["text"]
    assert "Only CONTACT rows count" in instruction
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_rejects_history_only_wrong_name_and_bad_coordinates(monkeypatch):
    client = VisionClient("secret")
    for payload in (
        {"found": False},
        {"found": True, "x": 80, "y": 100, "visible_name": "小号", "result_type": "history"},
        {"found": True, "x": 80, "y": 100, "visible_name": "别人", "result_type": "contact"},
        {"found": True, "x": 245, "y": 100, "visible_name": "小号", "result_type": "contact"},
        {"found": True, "x": 80, "y": 100, "visible_name": "小号", "result_type": "contact", "matching_contact_count": 2, "all_results_visible": True},
        {"found": True, "x": 80, "y": 100, "visible_name": "小号", "result_type": "contact", "matching_contact_count": 1, "all_results_visible": False},
    ):
        client_reply(monkeypatch, payload)
        assert client.locate_search_result(b"PNG", "小号", (245, 350)) is None


def test_classifies_title_from_candidates(monkeypatch):
    client_reply(monkeypatch, {"name": "向崟吉"})
    client = VisionClient("secret")
    assert client.classify_chat_title(b"PNG", ["小号", "向崟吉"]) == "向崟吉"
    client_reply(monkeypatch, {"name": "OTHER"})
    assert client.classify_chat_title(b"PNG", ["小号", "向崟吉"]) is None


def test_reads_bubbles_and_rejects_malformed_payload(monkeypatch):
    client = VisionClient("secret")
    client_reply(monkeypatch, {"bubbles": [{"side": "other", "content": "你好"}, {"side": "self", "content": "收到"}]})
    bubbles = client.read_visible_messages(b"PNG")
    assert [(b.side, b.content) for b in bubbles] == [("other", "你好"), ("self", "收到")]
    client_reply(monkeypatch, {"bubbles": []})
    assert client.read_visible_messages(b"PNG") == []
    client_reply(monkeypatch, {"bubbles": [{"side": "unknown", "content": "你好"}]})
    assert client.read_visible_messages(b"PNG") is None


def test_timeout_and_invalid_json_fail_closed(monkeypatch):
    client = VisionClient("secret")
    monkeypatch.setattr(httpx, "post", Mock(side_effect=httpx.TimeoutException("timeout")))
    assert client.locate_search_result(b"PNG", "小号", (245, 350)) is None
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "oops"}}]}
    monkeypatch.setattr(httpx, "post", Mock(return_value=response))
    assert client.read_visible_messages(b"PNG") is None
