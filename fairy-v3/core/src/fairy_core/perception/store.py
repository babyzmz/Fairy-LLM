from __future__ import annotations

import os
import shutil
import stat
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

    def task_size(self, task_ids: tuple[UUID, ...]) -> int:
        if self._root is None or not self._root.exists():
            return 0
        return sum(self._task_directory_size(task_id) for task_id in frozenset(task_ids))

    def purge_tasks(self, task_ids: tuple[UUID, ...]) -> int:
        selected = frozenset(task_ids)
        if not selected:
            return 0
        with self._lock:
            released = tuple(
                self._attachments.pop(turn_id)
                for turn_id, attachments in tuple(self._attachments.items())
                if any(attachment.task_id in selected for attachment in attachments)
            )
        for attachments in released:
            _zero(attachments)
        if self._root is None or not self._root.exists():
            return 0
        released_bytes = 0
        for task_id in selected:
            directory = self._task_directory(task_id)
            if not directory.exists():
                continue
            self._reject_reparse_tree(directory)
            released_bytes += self._task_directory_size(task_id)
            shutil.rmtree(directory)
        return released_bytes

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

    def _task_directory(self, task_id: UUID) -> Path:
        if self._root is None:
            raise ValueError("conversation image persistence is unavailable")
        guard = PathGuard(
            project_root=self._root,
            allowed_roots=(self._root,),
            forbidden_roots=(),
        )
        return guard.validate_write(Path(str(task_id)))

    def _task_directory_size(self, task_id: UUID) -> int:
        directory = self._task_directory(task_id)
        if not directory.exists():
            return 0
        self._reject_reparse_tree(directory)
        return sum(
            path.stat(follow_symlinks=False).st_size
            for path in directory.rglob("*")
            if path.is_file()
        )

    @staticmethod
    def _reject_reparse_tree(directory: Path) -> None:
        for path in (directory, *directory.rglob("*")):
            metadata = path.stat(follow_symlinks=False)
            attributes = getattr(metadata, "st_file_attributes", 0)
            is_junction = getattr(path, "is_junction", lambda: False)
            if (
                path.is_symlink()
                or is_junction()
                or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
            ):
                raise ValueError("persisted attachment path contains a reparse point")


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
