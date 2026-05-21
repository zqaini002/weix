import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.asyncio
async def test_temp_cleanup_loop_runs_cleanup_before_sleep(monkeypatch):
    """后端启动的定时清理循环应立即执行一次清理，再进入等待。"""
    from app.core import temp_cleanup

    calls = []

    def fake_cleanup(*, stale_seconds):
        calls.append(stale_seconds)
        return 2

    async def fake_sleep(interval_seconds):
        assert interval_seconds == 12
        raise asyncio.CancelledError

    monkeypatch.setattr(temp_cleanup.MacOSDBReader, "cleanup_temp_files", fake_cleanup)
    monkeypatch.setattr(temp_cleanup.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await temp_cleanup.temp_cleanup_loop(
            interval_seconds=12,
            stale_seconds=34,
        )

    assert calls == [34]
