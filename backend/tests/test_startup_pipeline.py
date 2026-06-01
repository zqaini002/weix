import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import main


@pytest.fixture(autouse=True)
def reset_startup_globals():
    main._shutdown_event.clear()
    main._app_loop = None
    main._pipeline_start_lock = None
    main._pipeline = None
    yield
    main._shutdown_event.clear()
    main._app_loop = None
    main._pipeline_start_lock = None
    main._pipeline = None


@pytest.mark.asyncio
async def test_late_key_extraction_schedules_pipeline_start(monkeypatch, tmp_path):
    started = []
    pipeline = object()

    class FakeExtractor:
        def find_wechat_processes(self):
            return [1234]

        def scan_memory_for_keys(self, pid, stop_event=None):
            assert pid == 1234
            assert stop_event is main._shutdown_event
            return {"message/message_0.db": "00" * 32}

    async def fake_create(reason):
        started.append(reason)
        return pipeline

    monkeypatch.setattr(main, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_create_auto_reply_pipeline", fake_create)
    monkeypatch.setattr(
        "app.core.platform.Platform.get",
        lambda: SimpleNamespace(key_extractor=FakeExtractor()),
    )

    main._app_loop = asyncio.get_running_loop()
    main._pipeline_start_lock = asyncio.Lock()

    await asyncio.to_thread(main._try_auto_extract_keys)
    for _ in range(20):
        if main._pipeline is pipeline:
            break
        await asyncio.sleep(0.05)

    assert main._pipeline is pipeline
    assert started == ["数据库密钥提取完成"]


@pytest.mark.asyncio
async def test_pipeline_start_is_single_flight(monkeypatch):
    calls = 0
    pipeline = object()

    async def fake_create(reason):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return pipeline

    monkeypatch.setattr(main, "_create_auto_reply_pipeline", fake_create)

    result1, result2 = await asyncio.gather(
        main._ensure_auto_reply_pipeline_started("first"),
        main._ensure_auto_reply_pipeline_started("second"),
    )

    assert result1 is pipeline
    assert result2 is pipeline
    assert calls == 1
