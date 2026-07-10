from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class DataDirectoryInUseError(RuntimeError):
    pass


class DataDirectoryLock:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._closed = False

    @classmethod
    def acquire(cls, data_directory: Path) -> DataDirectoryLock:
        lock_path = data_directory / ".fairy-v3.lock"
        stream = lock_path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise DataDirectoryInUseError(
                f"Fairy data directory is already in use: {data_directory}"
            ) from error
        return cls(stream)

    def close(self) -> None:
        if self._closed:
            return
        self._stream.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._closed = True
            self._stream.close()
