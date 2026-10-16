"""Decode plain and WeChat 4.x Zstandard message bodies."""
import logging

import zstandard

logger = logging.getLogger(__name__)
_MAX_MESSAGE_BYTES = 8 * 1024 * 1024


def decode_message_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, (bytes, bytearray, memoryview)):
        content = bytes(content)
        if content.startswith(b"\x28\xb5\x2f\xfd"):
            try:
                size = zstandard.frame_content_size(content)
                if size != zstandard.CONTENTSIZE_UNKNOWN and size > _MAX_MESSAGE_BYTES:
                    logger.warning("微信压缩消息超出读取上限，跳过")
                    return ""
                content = zstandard.ZstdDecompressor().decompress(
                    content, max_output_size=_MAX_MESSAGE_BYTES, allow_extra_data=False,
                )
            except zstandard.ZstdError:
                logger.warning("微信压缩消息解码失败，跳过")
                return ""
        return content.decode("utf-8", errors="replace")
    return str(content)
