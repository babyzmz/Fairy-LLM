from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fairy_core.runtime.review import StoredRuntimeEvidence

_MAX_EVIDENCE_BYTES = 16 * 1024 * 1024


class FileRuntimeEvidenceStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve(strict=False)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, *, content: bytes, media_type: str) -> StoredRuntimeEvidence:
        payload = bytes(content)
        if (
            media_type != "image/png"
            or not payload.startswith(b"\x89PNG\r\n\x1a\n")
            or len(payload) > _MAX_EVIDENCE_BYTES
        ):
            raise ValueError("Runtime evidence store accepts PNG screenshots only")
        digest = hashlib.sha256(payload).hexdigest()
        directory = self._root / "sha256" / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.png"
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
        except FileExistsError as error:
            if target.read_bytes() != payload:
                raise RuntimeError(
                    "existing Runtime evidence failed integrity validation"
                ) from error
        else:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        return StoredRuntimeEvidence.create(
            storage_location=f"fairy-evidence://sha256/{digest[:2]}/{digest}.png",
            content=payload,
        )


__all__ = ["FileRuntimeEvidenceStore"]
