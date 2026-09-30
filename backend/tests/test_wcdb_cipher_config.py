"""Config.Cipher recovery with a synthetic process and real page HMACs."""

import hashlib
import hmac
import struct
import threading
import time

from Crypto.Cipher import AES

from app.core.key_extractor_windows import WindowsKeyExtractor


def _page(key, salt):
    iv = b"I" * 16
    plaintext = b"\x10\x00" + bytes(3998)
    encrypted = AES.new(key, AES.MODE_CBC, iv).encrypt(plaintext)
    mac_salt = bytes(v ^ 0x3A for v in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, 32)
    digest = hmac.new(mac_key, encrypted + iv + struct.pack("<I", 1), hashlib.sha512).digest()
    return salt + encrypted + iv + digest


def _process(monkeypatch, keys, *, oversized=False):
    extractor = WindowsKeyExtractor.__new__(WindowsKeyExtractor)
    extractor.SCAN_CHUNK_SIZE = 512
    extractor.SCAN_CHUNK_OVERLAP = 64
    base = 0x10000
    memory = bytearray(8192)
    name = b"com.Tencent.WCDB.Config.Cipher"
    memory[500:500 + len(name)] = name  # crosses a chunk boundary
    mask = bytes.fromhex("d2c7442458020000004889442450488b450048844c2448488944254048584c24")
    for index, key in enumerate(keys):
        node = 1024 + index * 512
        config = node + 128
        blob_offset = node + 352
        struct.pack_into("<QQ", memory, node + 16, base + 500, len(name))
        struct.pack_into("<Q", memory, node + 40, base + config)
        blob = b"x'" + key.hex().encode() + b"'"
        encoded = bytes(v ^ mask[i % len(mask)] for i, v in enumerate(blob))
        struct.pack_into("<QQ", memory, config + 144, base + blob_offset,
                         2048 if oversized else len(encoded))
        memory[blob_offset:blob_offset + len(encoded)] = encoded

    def read(_handle, address, size):
        offset = address - base
        if offset < 0 or offset + size > len(memory):
            return None
        return bytes(memory[offset:offset + size])

    monkeypatch.setattr(extractor, "_read_process_memory", read)
    return extractor, [(base, len(memory))]


def test_recovers_each_database_key_and_rejects_unverified_material(monkeypatch):
    # Losing the XOR decode, pointer offsets, boundary overlap, or HMAC gate
    # must prevent recovery or admit the unrelated candidate.
    message_key, contact_key = bytes(range(32)), bytes(range(32, 64))
    extractor, regions = _process(monkeypatch, [b"Z" * 32, message_key, contact_key])
    infos = [
        {"rel_path": "message/message_0.db", "page1": _page(message_key, b"M" * 16)},
        {"rel_path": "contact/contact.db", "page1": _page(contact_key, b"C" * 16)},
    ]

    found, count, cancelled = extractor._scan_wcdb_cipher_config_keys(
        1, regions, infos, time.monotonic(), None,
    )

    assert found == {
        "message/message_0.db": message_key.hex().upper(),
        "contact/contact.db": contact_key.hex().upper(),
    }
    assert count <= 3
    assert not cancelled


def test_rejects_oversized_config_blob(monkeypatch):
    key = bytes(range(32))
    extractor, regions = _process(monkeypatch, [key], oversized=True)
    infos = [{"rel_path": "message/message_0.db", "page1": _page(key, b"M" * 16)}]
    found, _, _ = extractor._scan_wcdb_cipher_config_keys(1, regions, infos, time.monotonic(), None)
    assert found == {}


def test_cancelled_scan_returns_no_keys(monkeypatch):
    extractor, regions = _process(monkeypatch, [bytes(range(32))])
    stop = threading.Event()
    stop.set()
    found, _, cancelled = extractor._scan_wcdb_cipher_config_keys(1, regions, [], time.monotonic(), stop)
    assert found == {}
    assert cancelled
