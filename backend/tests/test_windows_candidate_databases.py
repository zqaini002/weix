import threading
import time

from app.core.key_extractor_windows import WindowsKeyExtractor


def test_binary_candidates_validate_all_accounts_and_contact_keys(monkeypatch):
    extractor = object.__new__(WindowsKeyExtractor)
    infos = [dict(path='/old/message_0.db', rel_path='/old/message_0.db', page1=b'old'),
             dict(path='/new/message_0.db', rel_path='/new/message_0.db', page1=b'message'),
             dict(path='/new/contact.db', rel_path='/new/contact.db', page1=b'contact')]
    monkeypatch.setattr(extractor, '_verify_direct_aes_key', lambda key, page: (key, page) in [(b'm'*32, b'message'), (b'c'*32, b'contact')])
    result = extractor._match_binary_candidates([b'm'*32, b'c'*32], infos, time.monotonic(), None)
    assert result == {'/new/message_0.db': (b'm'*32).hex().upper(), '/new/contact.db': (b'c'*32).hex().upper()}


def test_cancelled_binary_validation_does_not_accept_keys(monkeypatch):
    extractor = object.__new__(WindowsKeyExtractor)
    event = threading.Event(); event.set()
    monkeypatch.setattr(extractor, '_verify_direct_aes_key', lambda *args: True)
    assert extractor._match_binary_candidates([b'x'*32], [dict(rel_path='db', page1=b'page')], time.monotonic(), event) == {}
