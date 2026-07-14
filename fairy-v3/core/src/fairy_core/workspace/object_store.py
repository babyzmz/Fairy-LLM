from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_WORKSPACE_BYTES = 50 * 1024 * 1024 * 1024
DEFAULT_DERIVATIVE_CACHE_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_MIN_FREE_BYTES = 10 * 1024 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024


class ObjectQuotaExceededError(ValueError):
    code = "CACHE_QUOTA_EXCEEDED"


class FileTooLargeError(ValueError):
    code = "FILE_TOO_LARGE"


@dataclass(frozen=True, slots=True)
class WorkspaceObject:
    content_hash: str
    byte_length: int
    storage_path: Path


@dataclass(frozen=True, slots=True)
class AssetMutation:
    operation: Literal["create", "update"]
    path: str
    source: Path
    expected_source_hash: str | None = None
    expected_target_hash: str | None = None

    def __post_init__(self) -> None:
        normalized = self.path.strip().replace("\\", "/")
        relative = PurePosixPath(normalized)
        if (
            not normalized
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or ":" in normalized
        ):
            raise ValueError("AssetMutation path must be Workspace-relative")
        if self.operation == "create" and self.expected_target_hash is not None:
            raise ValueError("create AssetMutation cannot expect an existing target")
        for digest in (self.expected_source_hash, self.expected_target_hash):
            if digest is not None and (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("AssetMutation hashes must be lowercase SHA-256")
        object.__setattr__(self, "path", relative.as_posix())


@dataclass(frozen=True, slots=True)
class WorkspaceStoragePolicy:
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_workspace_bytes: int = DEFAULT_MAX_WORKSPACE_BYTES
    derivative_cache_bytes: int = DEFAULT_DERIVATIVE_CACHE_BYTES
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES

    def __post_init__(self) -> None:
        if (
            min(
                self.max_file_bytes,
                self.max_workspace_bytes,
                self.derivative_cache_bytes,
                self.min_free_bytes,
            )
            <= 0
        ):
            raise ValueError("Workspace storage limits must be positive")
        if self.max_file_bytes > self.max_workspace_bytes:
            raise ValueError("per-file limit cannot exceed Workspace limit")


class FileSystemWorkspaceObjectStore:
    """Immutable content-addressed storage for large Workspace objects."""

    def __init__(
        self,
        managed_root: Path,
        *,
        policy: WorkspaceStoragePolicy | None = None,
    ) -> None:
        self._root = managed_root.resolve(strict=False) / "objects" / "sha256"
        self._staging = managed_root.resolve(strict=False) / ".transactions" / "objects"
        self._policy = policy or WorkspaceStoragePolicy()
        self._root.mkdir(parents=True, exist_ok=True)
        self._staging.mkdir(parents=True, exist_ok=True)

    @property
    def policy(self) -> WorkspaceStoragePolicy:
        return self._policy

    def put_file(
        self,
        source: Path,
        *,
        expected_hash: str | None = None,
        workspace_bytes: int = 0,
    ) -> WorkspaceObject:
        source = source.resolve(strict=True)
        if not source.is_file():
            raise ValueError("Workspace object source must be a regular file")
        with source.open("rb") as stream:
            return self.put_stream(
                stream,
                expected_hash=expected_hash,
                workspace_bytes=workspace_bytes,
            )

    def put_stream(
        self,
        source: BinaryIO,
        *,
        expected_hash: str | None = None,
        workspace_bytes: int = 0,
    ) -> WorkspaceObject:
        if workspace_bytes < 0:
            raise ValueError("Workspace byte usage cannot be negative")
        self._require_free_space()
        digest = hashlib.sha256()
        byte_length = 0
        descriptor, temporary_name = tempfile.mkstemp(prefix="object-", dir=self._staging)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as destination:
                while chunk := source.read(_COPY_CHUNK_BYTES):
                    if not isinstance(chunk, bytes):
                        raise TypeError("Workspace object streams must yield bytes")
                    byte_length += len(chunk)
                    if byte_length > self._policy.max_file_bytes:
                        raise FileTooLargeError("Workspace object exceeds the per-file quota")
                    if workspace_bytes + byte_length > self._policy.max_workspace_bytes:
                        raise ObjectQuotaExceededError(
                            "Workspace object exceeds the Workspace quota"
                        )
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            content_hash = digest.hexdigest()
            if expected_hash is not None and content_hash != expected_hash:
                raise ValueError("Workspace object digest does not match expected_hash")
            target = self._object_path(content_hash)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.stat().st_size != byte_length:
                    raise RuntimeError("content-addressed object size is inconsistent")
                temporary.unlink()
            else:
                os.replace(temporary, target)
            return WorkspaceObject(content_hash, byte_length, target)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, content_hash: str, *, expected_size: int | None = None) -> WorkspaceObject:
        target = self._object_path(content_hash)
        if not target.is_file():
            raise KeyError(f"Workspace object not found: {content_hash}")
        byte_length = target.stat().st_size
        if expected_size is not None and byte_length != expected_size:
            raise RuntimeError("content-addressed object size is inconsistent")
        return WorkspaceObject(content_hash, byte_length, target)

    def materialize(self, workspace_object: WorkspaceObject, target: Path) -> Path:
        target = target.resolve(strict=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="asset-", dir=target.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            try:
                temporary.unlink()
                os.link(workspace_object.storage_path, temporary)
            except OSError:
                shutil.copyfile(workspace_object.storage_path, temporary)
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def _object_path(self, content_hash: str) -> Path:
        if len(content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in content_hash
        ):
            raise ValueError("Workspace object digest must be lowercase SHA-256")
        return self._root / content_hash[:2] / content_hash

    def _require_free_space(self) -> None:
        free = shutil.disk_usage(self._root).free
        if free < self._policy.min_free_bytes:
            raise ObjectQuotaExceededError("Workspace conversion paused because disk space is low")


__all__ = [
    "DEFAULT_DERIVATIVE_CACHE_BYTES",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_WORKSPACE_BYTES",
    "DEFAULT_MIN_FREE_BYTES",
    "AssetMutation",
    "FileSystemWorkspaceObjectStore",
    "FileTooLargeError",
    "ObjectQuotaExceededError",
    "WorkspaceObject",
    "WorkspaceStoragePolicy",
]
