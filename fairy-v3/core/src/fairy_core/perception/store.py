from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.perception.models import ImageAttachment, ImagePersistence
from fairy_core.security.path_guard import PathGuard


class ImageAttachmentStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root.resolve() if root is not None else None
        self._attachments: dict[UUID, tuple[ImageAttachment, ...]] = {}
        self._lock = RLock()

    def register(
        self,
        turn_id: UUID,
        attachments: tuple[ImageAttachment, ...],
    ) -> None:
        values = tuple(attachments)
        if not values:
            return
        with self._lock:
            existing = self._attachments.get(turn_id)
            if existing is not None:
                if _fingerprint(existing) != _fingerprint(values):
                    _zero(values)
                    raise IdempotencyConflictError(
                        "Assistant Turn image attachments changed during replay"
                    )
                _zero(values)
                return
            self._attachments[turn_id] = values

    def prepare(self, attachments: tuple[ImageAttachment, ...]) -> None:
        values = tuple(attachments)
        try:
            for attachment in values:
                if attachment.persistence is ImagePersistence.CONVERSATION:
                    self._persist(attachment)
        except BaseException:
            _zero(values)
            raise

    def for_turn(self, turn_id: UUID) -> tuple[ImageAttachment, ...]:
        with self._lock:
            return self._attachments.get(turn_id, ())

    def release(self, turn_id: UUID) -> None:
        with self._lock:
            attachments = self._attachments.pop(turn_id, ())
        _zero(attachments)

    def close(self) -> None:
        with self._lock:
            values = tuple(self._attachments.values())
            self._attachments.clear()
        for attachments in values:
            _zero(attachments)

    def _persist(self, attachment: ImageAttachment) -> None:
        if self._root is None:
            raise ValueError("conversation image persistence is unavailable")
        self._root.mkdir(parents=True, exist_ok=True)
        guard = PathGuard(
            project_root=self._root,
            allowed_roots=(self._root,),
            forbidden_roots=(),
        )
        relative = Path(str(attachment.task_id)) / f"{attachment.content_hash}.png"
        destination = guard.validate_write(relative)
        directory = destination.parent
        directory.mkdir(parents=True, exist_ok=True)
        lease = guard.issue_write_lease(relative)
        if destination.exists():
            if destination.read_bytes() != bytes(attachment.data):
                raise IdempotencyConflictError("persisted screen attachment hash changed")
            return
        temporary = directory / f".{attachment.content_hash}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(attachment.data)
                stream.flush()
                os.fsync(stream.fileno())
            guard.revalidate_write_lease(lease)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def _fingerprint(attachments: tuple[ImageAttachment, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            attachment.task_id,
            attachment.content_hash,
            attachment.width,
            attachment.height,
            attachment.source_label,
            attachment.captured_at_ms,
            attachment.persistence,
        )
        for attachment in attachments
    )


def _zero(attachments: tuple[ImageAttachment, ...]) -> None:
    for attachment in attachments:
        attachment.zero()


__all__ = ["ImageAttachmentStore"]
