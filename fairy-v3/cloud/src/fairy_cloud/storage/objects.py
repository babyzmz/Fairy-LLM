from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

_SAFE_KEY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ObjectClient(Protocol):
    def put_object(self, **request: Any) -> Any: ...

    def head_object(self, **request: Any) -> Any: ...


class ImmutableObjectConflict(RuntimeError):
    """Raised when an immutable version object already exists."""


class ObjectIntegrityError(RuntimeError):
    """Raised when an object is missing or does not match its manifest."""


@dataclass(frozen=True, slots=True)
class ObjectLocation:
    bucket: str
    key: str
    sha256: str
    size: int


class S3ObjectStore:
    def __init__(self, *, client: ObjectClient, bucket: str) -> None:
        if not bucket.strip():
            raise ValueError("bucket is required")
        self._client = client
        self._bucket = bucket

    def put_version_snapshot(
        self,
        *,
        user_id: str,
        project_id: str,
        version_id: str,
        payload: bytes,
    ) -> ObjectLocation:
        key = _version_snapshot_key(
            user_id=user_id,
            project_id=project_id,
            version_id=version_id,
        )
        checksum = hashlib.sha256(payload).hexdigest()
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                ContentType="application/zstd",
                Metadata={"sha256": checksum},
                IfNoneMatch="*",
            )
        except Exception as error:
            if _is_precondition_failure(error):
                raise ImmutableObjectConflict(
                    f"immutable object already exists: {self._bucket}/{key}"
                ) from error
            raise

        return ObjectLocation(
            bucket=self._bucket,
            key=key,
            sha256=checksum,
            size=len(payload),
        )

    def verify_version_snapshot(
        self,
        *,
        user_id: str,
        project_id: str,
        version_id: str,
        expected_sha256: str,
        expected_size: int,
    ) -> ObjectLocation:
        key = _version_snapshot_key(
            user_id=user_id,
            project_id=project_id,
            version_id=version_id,
        )
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as error:
            if _error_code(error) in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectIntegrityError(f"version snapshot does not exist: {key}") from error
            raise

        metadata = response.get("Metadata", {})
        actual_sha256 = metadata.get("sha256") if isinstance(metadata, dict) else None
        actual_size = response.get("ContentLength")
        if actual_sha256 != expected_sha256 or actual_size != expected_size:
            raise ObjectIntegrityError(f"version snapshot does not match its manifest: {key}")
        return ObjectLocation(
            bucket=self._bucket,
            key=key,
            sha256=expected_sha256,
            size=expected_size,
        )


def _key_part(value: str, *, name: str) -> str:
    if not _SAFE_KEY_PART.fullmatch(value):
        raise ValueError(f"{name} contains unsupported characters")
    return value


def _version_snapshot_key(*, user_id: str, project_id: str, version_id: str) -> str:
    return (
        f"users/{_key_part(user_id, name='user_id')}"
        f"/projects/{_key_part(project_id, name='project_id')}"
        f"/versions/{_key_part(version_id, name='version_id')}/snapshot.zst"
    )


def _is_precondition_failure(error: Exception) -> bool:
    return _error_code(error) in {"PreconditionFailed", "412"}


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return ""
    details = response.get("Error")
    if not isinstance(details, dict):
        return ""
    return str(details.get("Code", ""))
