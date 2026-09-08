from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from uuid import UUID

from fairy_core.providers import ModelImage

_TURN_BYTES = 20 * 1024 * 1024
_TOTAL_BYTES = 80 * 1024 * 1024


class ToolImageHandoff:
    """Bounded, in-process evidence transfer. Never a durable screenshot archive."""

    def __init__(self):
        self._lock = RLock()
        self._items = OrderedDict()

    def put(self, turn_id, task_id, revision, sequence, images):
        if not images:
            return
        key = (turn_id, task_id, revision, sequence)
        if any(image.task_id != task_id for image in images):
            zero_images(images)
            raise ValueError("Tool image scope does not match its Task")
        with self._lock:
            if key in self._items:
                zero_images(images)
                return
            size = sum(image.data.nbytes for image in images)
            if size > _TURN_BYTES:
                zero_images(images)
                return
            while self._items and (
                self._size(turn_id) + size > _TURN_BYTES or self._size() + size > _TOTAL_BYTES
            ):
                victim = next(
                    (item for item in self._items if item[0] == turn_id),
                    next(iter(self._items)),
                )
                zero_images(self._items.pop(victim))
            self._items[key] = tuple(images)

    def take(self, turn_id: UUID, task_id: UUID, revision: int) -> tuple[ModelImage, ...]:
        with self._lock:
            selected = sorted((key for key in self._items if key[0] == turn_id), key=lambda k: k[3])
            if any(key[1] != task_id for key in selected):
                raise ValueError("Tool image scope does not match its Task")
            result = []
            for key in selected:
                images = self._items.pop(key)
                if key[2] != revision:
                    zero_images(images)
                else:
                    result.extend(images)
            return tuple(result)

    def release(self, turn_id):
        self._remove(lambda key: key[0] == turn_id)

    def purge_tasks(self, task_ids):
        self._remove(lambda key: key[1] in task_ids)

    def close(self):
        self._remove(lambda key: True)

    def _remove(self, matches):
        with self._lock:
            for key in tuple(self._items):
                if matches(key):
                    zero_images(self._items.pop(key))

    def _size(self, turn_id=None):
        return sum(
            image.data.nbytes
            for key, images in self._items.items()
            if turn_id is None or key[0] == turn_id
            for image in images
        )


def zero_images(images):
    for image in images:
        buffer = image.data.obj
        if isinstance(buffer, bytearray):
            buffer[:] = b"\0" * len(buffer)
