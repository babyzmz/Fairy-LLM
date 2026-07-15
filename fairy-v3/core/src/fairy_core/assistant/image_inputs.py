from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from fairy_core.perception import ImageAttachment, ImagePersistence


class ImageAttachmentInput(Protocol):
    media_type: str
    png_base64: str
    content_hash: str
    width: int
    height: int
    source_label: str | None
    captured_at_ms: int
    persistence: ImagePersistence


def build_image_attachments(
    task_id: UUID,
    values: Iterable[ImageAttachmentInput],
) -> tuple[ImageAttachment, ...]:
    attachments: list[ImageAttachment] = []
    try:
        for value in values:
            try:
                png = base64.b64decode(value.png_base64, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("screen attachment must use canonical base64") from error
            attachments.append(
                ImageAttachment.create(
                    task_id=task_id,
                    media_type=value.media_type,
                    png=png,
                    content_hash=value.content_hash,
                    width=value.width,
                    height=value.height,
                    source_label=value.source_label,
                    captured_at_ms=value.captured_at_ms,
                    persistence=value.persistence,
                )
            )
        return tuple(attachments)
    except BaseException:
        for attachment in attachments:
            attachment.zero()
        raise


__all__ = ["build_image_attachments"]
