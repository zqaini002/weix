import threading

from app.core.key_extractor_macos import MacOSKeyExtractor


def test_macos_accepts_startup_cancellation_before_accessing_process():
    # The shared startup caller supplies stop_event on both platforms.
    extractor = MacOSKeyExtractor.__new__(MacOSKeyExtractor)
    stop = threading.Event()
    stop.set()
    assert extractor.scan_memory_for_keys(123, stop_event=stop) == {}
