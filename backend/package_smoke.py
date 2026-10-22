"""Opt-in frozen-build check. Never accesses WeChat or a user's credentials."""
import os

if os.environ.get("WEIX_PACKAGE_SMOKE") == "1":
    import sys
    from pathlib import Path
    from app.main import app
    from app.config import get_config
    from app.utils.paths import get_frontend_dir
    from passlib.context import CryptContext
    from app.core.message_content import decode_message_content
    import zstandard

    cfg = get_config()
    assert not cfg.ai.get("api_key"), "Release must have no API key"
    assert not cfg.auto_reply.get("private_whitelist")
    assert not cfg.auto_reply.get("group_whitelist")
    assert (get_frontend_dir() / "index.html").is_file()
    passwords = CryptContext(schemes=["bcrypt"])
    assert passwords.verify("package-smoke", passwords.hash("package-smoke"))
    assert decode_message_content(zstandard.ZstdCompressor().compress(b"smoke")) == "smoke"
    if sys.platform == "win32":
        from PyQt6.QtWidgets import QApplication, QWidget
        qt = QApplication([])
        widget = QWidget()
        widget.close()
    Path(os.environ["WEIX_SMOKE_RESULT"]).write_text("ok", encoding="utf-8")
    sys.exit(0)
