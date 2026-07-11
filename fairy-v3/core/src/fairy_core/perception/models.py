from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 33_177_600
MAX_IMAGE_DIMENSION = 16_384
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ImagePersistence(StrEnum):
    EPHEMERAL = "ephemeral"
    CONVERSATION = "conversation"


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    task_id: UUID
    media_type: str
    content_hash: str
    width: int
    height: int
    source_label: str
    captured_at_ms: int
    persistence: ImagePersistence
    label: str = "untrusted_screen_content"
    untrusted_data: bool = True
    _buffer: bytearray = field(default_factory=bytearray, repr=False, compare=False)

    @classmethod
    def create(
        cls,
        *,
        task_id: UUID,
        media_type: str,
        png: bytes,
        content_hash: str,
        width: int,
        height: int,
        source_label: str,
        captured_at_ms: int,
        persistence: ImagePersistence,
    ) -> ImageAttachment:
        if media_type != "image/png":
            raise ValueError("screen attachment media type must be image/png")
        content = bytes(png)
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise ValueError("screen attachment exceeds the byte limit")
        parsed_width, parsed_height = parse_png_dimensions(content)
        if (parsed_width, parsed_height) != (width, height):
            raise ValueError("screen attachment dimensions do not match the PNG")
        expected_hash = hashlib.sha256(content).hexdigest()
        if not _SHA256.fullmatch(content_hash) or content_hash != expected_hash:
            raise ValueError("screen attachment hash does not match the PNG")
        normalized_label = source_label.strip()
        if not normalized_label or len(normalized_label) > 255:
            raise ValueError("screen attachment source label is invalid")
        if (
            isinstance(captured_at_ms, bool)
            or captured_at_ms < 0
            or captured_at_ms > 100_000_000_000_000
        ):
            raise ValueError("screen attachment timestamp is invalid")
        return cls(
            task_id=task_id,
            media_type=media_type,
            content_hash=content_hash,
            width=width,
            height=height,
            source_label=normalized_label,
            captured_at_ms=captured_at_ms,
            persistence=ImagePersistence(persistence),
            _buffer=bytearray(content),
        )

    @property
    def data(self) -> memoryview:
        return memoryview(self._buffer)

    def zero(self) -> None:
        self._buffer[:] = b"\0" * len(self._buffer)


def parse_png_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 33 or content[:8] != _PNG_SIGNATURE:
        raise ValueError("screen attachment is not a PNG")
    offset = 8
    chunks = 0
    width: int | None = None
    height: int | None = None
    saw_data = False
    while offset < len(content):
        if chunks >= 4_096 or offset + 12 > len(content):
            raise ValueError("screen attachment PNG structure is invalid")
        chunk_length = int.from_bytes(content[offset : offset + 4], "big")
        chunk_type = content[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + chunk_length
        crc_end = data_end + 4
        if data_end < data_start or crc_end > len(content):
            raise ValueError("screen attachment PNG chunk exceeds its bounds")
        expected_crc = int.from_bytes(content[data_end:crc_end], "big")
        actual_crc = zlib.crc32(chunk_type + content[data_start:data_end]) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError("screen attachment PNG checksum is invalid")
        if chunks == 0:
            if chunk_type != b"IHDR" or chunk_length != 13:
                raise ValueError("screen attachment PNG must start with IHDR")
            width = int.from_bytes(content[data_start : data_start + 4], "big")
            height = int.from_bytes(content[data_start + 4 : data_start + 8], "big")
            _validate_dimensions(width, height)
        elif chunk_type == b"IHDR":
            raise ValueError("screen attachment PNG has duplicate IHDR")
        if chunk_type == b"IDAT":
            saw_data = True
        if chunk_type == b"IEND":
            if chunk_length != 0 or crc_end != len(content) or not saw_data:
                raise ValueError("screen attachment PNG ending is invalid")
            assert width is not None and height is not None
            return width, height
        offset = crc_end
        chunks += 1
    raise ValueError("screen attachment PNG has no IEND chunk")


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width < 1
        or height < 1
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ValueError("screen attachment dimensions exceed the pixel limit")


__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_DIMENSION",
    "MAX_IMAGE_PIXELS",
    "ImageAttachment",
    "ImagePersistence",
    "parse_png_dimensions",
]
