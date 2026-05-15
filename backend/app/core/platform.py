import sys
from typing import Any

from app.config import get_config


class Platform:
    """Detect current platform and load appropriate implementations."""

    _instance: "Platform | None" = None

    def __init__(self):
        config = get_config()
        self.name = config.get_platform()
        self._sender = None
        self._key_extractor = None

    @classmethod
    def get(cls) -> "Platform":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_windows(self) -> bool:
        return self.name == "win32"

    @property
    def is_macos(self) -> bool:
        return self.name == "darwin"

    @property
    def sender(self) -> Any:
        if self._sender is None:
            if self.is_windows:
                from app.core.sender_windows import WindowsSender
                self._sender = WindowsSender()
            else:
                from app.core.sender_macos import MacOSSender
                self._sender = MacOSSender()
        return self._sender

    @property
    def key_extractor(self) -> Any:
        if self._key_extractor is None:
            if self.is_windows:
                from app.core.key_extractor_windows import WindowsKeyExtractor
                self._key_extractor = WindowsKeyExtractor()
            else:
                from app.core.key_extractor_macos import MacOSKeyExtractor
                self._key_extractor = MacOSKeyExtractor()
        return self._key_extractor

    @property
    def db_reader(self) -> Any:
        if not hasattr(self, "_db_reader") or self._db_reader is None:
            if self.is_windows:
                from app.core.db_reader_windows import WindowsDBReader
                self._db_reader = WindowsDBReader()
            else:
                from app.core.db_reader_macos import MacOSDBReader
                self._db_reader = MacOSDBReader()
        return self._db_reader
