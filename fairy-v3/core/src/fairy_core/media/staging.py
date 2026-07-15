from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID


class MediaStagingStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=False)
        self._root.mkdir(parents=True, exist_ok=True)

    def stage(self, *, job_id: UUID, content: bytes, expected_hash: str) -> Path:
        if not content:
            raise ValueError("media staging content is empty")
        content_hash = hashlib.sha256(content).hexdigest()
        if content_hash != expected_hash:
            raise ValueError("media staging digest does not match expected_hash")
        job_root = self._root / str(job_id)
        job_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="media-", dir=job_root)
        temporary = Path(temporary_name)
        target = job_root / content_hash
        try:
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, target)
            return target.resolve(strict=True)
        finally:
            temporary.unlink(missing_ok=True)

    def cleanup(self, job_id: UUID) -> None:
        shutil.rmtree(self._root / str(job_id), ignore_errors=True)

    def cleanup_all(self) -> None:
        for path in self._root.iterdir():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


__all__ = ["MediaStagingStore"]
