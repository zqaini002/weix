from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class WeChatMessage:
    msg_id: str
    msg_type: int  # 1=text, 3=image, 34=voice, 49=card, 10000=system
    content: str
    sender: str  # wxid
    room_id: str = ""  # empty for private chat
    create_time: datetime = field(default_factory=datetime.now)
    is_group: bool = False
    at_list: list[str] = field(default_factory=list)

    @property
    def is_text(self) -> bool:
        return self.msg_type == 1

    @property
    def is_at_me(self, bot_wxid: str = "") -> bool:
        return bot_wxid in self.at_list if bot_wxid else bool(self.at_list)


class BaseKeyExtractor(ABC):
    """Extract WeChat database encryption keys from process memory."""

    @abstractmethod
    def find_wechat_process(self) -> Optional[int]:
        """Find WeChat process PID."""
        ...

    @abstractmethod
    def scan_memory_for_keys(self, pid: int) -> dict[str, str]:
        """Scan process memory for SQLCipher keys. Returns {db_name: hex_key}."""
        ...

    @abstractmethod
    def verify_key(self, key: bytes, db_path: str) -> bool:
        """Verify a key against a database file."""
        ...


class BaseDBReader(ABC):
    """Read decrypted WeChat SQLite databases."""

    @abstractmethod
    def open_db(self, db_path: str, key: bytes):
        """Open and decrypt a database."""
        ...

    @abstractmethod
    def query_messages_since(self, timestamp: int) -> list[WeChatMessage]:
        """Query messages newer than timestamp."""
        ...

    @abstractmethod
    def get_contacts(self) -> list[dict]:
        """Get contact list."""
        ...


class BaseMessageSender(ABC):
    """Send messages through WeChat."""

    @abstractmethod
    async def send_text(self, msg: str, receiver: str, aters: str = "") -> bool:
        """Send text message."""
        ...

    @abstractmethod
    async def send_image(self, path: str, receiver: str) -> bool:
        """Send image message."""
        ...

    @abstractmethod
    async def is_wechat_running(self) -> bool:
        """Check if WeChat is running."""
        ...
