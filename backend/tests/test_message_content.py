import pytest
import zstandard

from app.core.message_content import decode_message_content


@pytest.mark.parametrize("include_size", [True, False])
def test_decodes_compressed_multiline_order(include_size):
    text = "发送群聊：女1\n发送内容：【Weix点单流程测试】现消试音\n" * 8
    compressed = zstandard.ZstdCompressor(write_content_size=include_size).compress(text.encode())
    assert decode_message_content(compressed) == text


def test_corrupt_compressed_content_is_not_returned_as_binary_text():
    assert decode_message_content(b'\x28\xb5\x2f\xfd\x00') == ''


def test_truncated_compressed_form_is_not_forwardable_partial_text():
    compressed = zstandard.ZstdCompressor(write_checksum=True).compress('发送群聊：女1\n发送内容：测试'.encode())
    assert decode_message_content(compressed[:-1]) == ''


def test_limits_decompressed_message_size():
    assert decode_message_content(zstandard.ZstdCompressor().compress(b'x' * (8 * 1024 * 1024 + 1))) == ''


@pytest.mark.parametrize("value,expected", [(None, ''), ('在吗', '在吗'), ('在吗'.encode(), '在吗')])
def test_preserves_plain_messages(value, expected):
    assert decode_message_content(value) == expected
