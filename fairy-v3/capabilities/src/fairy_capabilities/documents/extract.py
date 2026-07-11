from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from fairy_core.documents import StoredDocumentBlob

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOCATION_PREFIX = "managed://sha256/"


class DocumentBlobIntegrityError(RuntimeError):
    pass


class ManagedFileDocumentStore:
    """Content-addressed document blobs kept below Fairy's managed data root."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve(strict=True)

    def put(
        self,
        *,
        content_hash: str,
        content: bytes,
        media_type: str,
    ) -> StoredDocumentBlob:
        del media_type
        digest = _digest(content_hash)
        if not isinstance(content, bytes):
            raise TypeError("document content must be bytes")
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != digest:
            raise DocumentBlobIntegrityError("document content does not match its declared hash")
        path = self._blob_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._require_managed_parent(path.parent)
        if path.exists():
            self._verify_path(path, expected_hash=digest, expected_size=len(content))
        else:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=path.parent,
                prefix=".document-",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self._verify_path(path, expected_hash=digest, expected_size=len(content))
        return StoredDocumentBlob(
            storage_location=f"{_LOCATION_PREFIX}{digest}",
            content_hash=digest,
            byte_length=len(content),
        )

    def read(self, blob: StoredDocumentBlob) -> bytes:
        if not blob.storage_location.startswith(_LOCATION_PREFIX):
            raise DocumentBlobIntegrityError("document blob location is not managed locally")
        location_hash = _digest(blob.storage_location.removeprefix(_LOCATION_PREFIX))
        if location_hash != blob.content_hash:
            raise DocumentBlobIntegrityError("document blob manifest has inconsistent hashes")
        path = self._blob_path(location_hash)
        return self._verify_path(
            path,
            expected_hash=location_hash,
            expected_size=blob.byte_length,
        )

    def _blob_path(self, digest: str) -> Path:
        return self._root / "sha256" / digest[:2] / digest

    def _require_managed_parent(self, parent: Path) -> None:
        resolved = parent.resolve(strict=True)
        if not resolved.is_relative_to(self._root) or parent.is_symlink():
            raise DocumentBlobIntegrityError("document blob path escaped the managed root")

    def _verify_path(self, path: Path, *, expected_hash: str, expected_size: int) -> bytes:
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise DocumentBlobIntegrityError("document blob is unavailable") from error
        if not resolved.is_relative_to(self._root) or path.is_symlink() or not resolved.is_file():
            raise DocumentBlobIntegrityError("document blob path failed integrity validation")
        content = resolved.read_bytes()
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
            raise DocumentBlobIntegrityError("document blob failed its integrity check")
        return content


def _digest(value: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise DocumentBlobIntegrityError("document hash must be a lowercase SHA-256 digest")
    return value


__all__ = ["DocumentBlobIntegrityError", "ManagedFileDocumentStore"]
