from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

from fairy_core.documents import StoredDocumentBlob
from fairy_core.runtime.review import StoredRuntimeEvidence

_SAFE_KEY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MAX_RUNTIME_EVIDENCE_BYTES = 16 * 1024 * 1024


class ObjectClient(Protocol):
    def put_object(self, **request: Any) -> Any: ...

    def head_object(self, **request: Any) -> Any: ...

    def get_object(self, **request: Any) -> Any: ...


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

    def document_blob_store(self, tenant_id: str) -> TenantS3DocumentBlobStore:
        return TenantS3DocumentBlobStore(
            client=self._client,
            bucket=self._bucket,
            tenant_id=tenant_id,
        )

    def runtime_evidence_store(self, tenant_id: str) -> TenantS3RuntimeEvidenceStore:
        return TenantS3RuntimeEvidenceStore(
            client=self._client,
            bucket=self._bucket,
            tenant_id=tenant_id,
        )

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


class TenantS3DocumentBlobStore:
    def __init__(self, *, client: ObjectClient, bucket: str, tenant_id: str) -> None:
        self._client = client
        self._bucket = bucket
        self._tenant_id = _key_part(tenant_id, name="tenant_id")

    def put(
        self,
        *,
        content_hash: str,
        content: bytes,
        media_type: str,
    ) -> StoredDocumentBlob:
        digest = _sha256(content_hash)
        if not isinstance(content, bytes):
            raise TypeError("document content must be bytes")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ObjectIntegrityError("document content does not match its declared hash")
        normalized_media_type = media_type.strip().lower()
        if not normalized_media_type or "/" not in normalized_media_type:
            raise ValueError("document media type is invalid")
        key = _document_key(tenant_id=self._tenant_id, content_hash=digest)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=normalized_media_type,
                Metadata={"sha256": digest},
                IfNoneMatch="*",
            )
        except Exception as error:
            if not _is_precondition_failure(error):
                raise
            self._verify_head(key=key, expected_hash=digest, expected_size=len(content))
        return StoredDocumentBlob(
            storage_location=f"s3://{self._bucket}/{key}",
            content_hash=digest,
            byte_length=len(content),
        )

    def read(self, blob: StoredDocumentBlob) -> bytes:
        key = _document_key(tenant_id=self._tenant_id, content_hash=blob.content_hash)
        if blob.storage_location != f"s3://{self._bucket}/{key}":
            raise ObjectIntegrityError("document object location does not match its manifest")
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as error:
            if _error_code(error) in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectIntegrityError("document object does not exist") from error
            raise
        body = response.get("Body") if isinstance(response, dict) else None
        if body is None or not callable(getattr(body, "read", None)):
            raise ObjectIntegrityError("document object response has no readable body")
        try:
            content = body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if not isinstance(content, bytes):
            raise ObjectIntegrityError("document object body is not bytes")
        if (
            len(content) != blob.byte_length
            or hashlib.sha256(content).hexdigest() != blob.content_hash
        ):
            raise ObjectIntegrityError("document object failed its integrity check")
        return content

    def _verify_head(self, *, key: str, expected_hash: str, expected_size: int) -> None:
        response = self._client.head_object(Bucket=self._bucket, Key=key)
        metadata = response.get("Metadata", {}) if isinstance(response, dict) else {}
        actual_hash = metadata.get("sha256") if isinstance(metadata, dict) else None
        actual_size = response.get("ContentLength") if isinstance(response, dict) else None
        if actual_hash != expected_hash or actual_size != expected_size:
            raise ObjectIntegrityError("existing document object failed integrity validation")


class TenantS3RuntimeEvidenceStore:
    def __init__(self, *, client: ObjectClient, bucket: str, tenant_id: str) -> None:
        self._client = client
        self._bucket = bucket
        self._tenant_id = _key_part(tenant_id, name="tenant_id")

    def put(self, *, content: bytes, media_type: str) -> StoredRuntimeEvidence:
        payload = bytes(content)
        if (
            media_type != "image/png"
            or not payload.startswith(b"\x89PNG\r\n\x1a\n")
            or len(payload) > _MAX_RUNTIME_EVIDENCE_BYTES
        ):
            raise ValueError("Runtime evidence object must be a PNG")
        digest = hashlib.sha256(payload).hexdigest()
        key = f"tenants/{self._tenant_id}/runtime-evidence/sha256/{digest[:2]}/{digest}.png"
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                ContentType=media_type,
                Metadata={"sha256": digest},
                IfNoneMatch="*",
            )
        except Exception as error:
            if not _is_precondition_failure(error):
                raise
            response = self._client.head_object(Bucket=self._bucket, Key=key)
            metadata = response.get("Metadata", {}) if isinstance(response, dict) else {}
            if (
                not isinstance(metadata, dict)
                or metadata.get("sha256") != digest
                or response.get("ContentLength") != len(payload)
            ):
                raise ObjectIntegrityError(
                    "existing Runtime evidence failed integrity validation"
                ) from error
        return StoredRuntimeEvidence.create(
            storage_location=f"s3://{self._bucket}/{key}",
            content=payload,
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


def _document_key(*, tenant_id: str, content_hash: str) -> str:
    digest = _sha256(content_hash)
    return (
        f"tenants/{_key_part(tenant_id, name='tenant_id')}/documents/sha256/{digest[:2]}/{digest}"
    )


def _sha256(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ObjectIntegrityError("document hash must be a lowercase SHA-256 digest")
    return value


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
