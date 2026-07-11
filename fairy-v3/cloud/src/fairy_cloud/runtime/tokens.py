from __future__ import annotations

import base64
import binascii
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fairy_core.persistence.tenant import normalize_tenant_id


@dataclass(frozen=True, slots=True)
class CloudPreviewBinding:
    tenant_id: str
    runtime_id: UUID
    preview_id: UUID
    task_id: UUID
    version_id: UUID
    lease_fence: int
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", normalize_tenant_id(self.tenant_id))
        if self.lease_fence < 1:
            raise ValueError("Preview token lease fence must be positive")
        if self.expires_at.tzinfo is None:
            raise ValueError("Preview token expiry requires a timezone")
        object.__setattr__(
            self,
            "expires_at",
            self.expires_at.astimezone(UTC).replace(microsecond=0),
        )


class CloudPreviewSigner:
    def __init__(self, key: bytes) -> None:
        secret = bytes(key)
        if len(secret) < 32:
            raise ValueError("Preview signing key must contain at least 32 bytes")
        self._key = secret

    def issue(self, binding: CloudPreviewBinding) -> str:
        return _base32(hmac.digest(self._key, _binding_bytes(binding), "sha256"))

    def verify(
        self,
        token: str,
        binding: CloudPreviewBinding,
        *,
        now: datetime | None = None,
        expected_tenant_id: str | None = None,
    ) -> None:
        if not isinstance(token, str) or len(token) != 52:
            raise ValueError("Preview token schema is invalid")
        expected = hmac.digest(self._key, _binding_bytes(binding), "sha256")
        supplied = _decode_base32(token)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("Preview token signature does not match")
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        if observed_at >= binding.expires_at:
            raise ValueError("Preview token has expired")
        if expected_tenant_id is not None and binding.tenant_id != normalize_tenant_id(
            expected_tenant_id
        ):
            raise ValueError("Preview token tenant does not match")


def _binding_bytes(binding: CloudPreviewBinding) -> bytes:
    return "\0".join(
        (
            "fairy-preview-route-v1",
            binding.tenant_id,
            str(binding.runtime_id),
            str(binding.preview_id),
            str(binding.task_id),
            str(binding.version_id),
            str(binding.lease_fence),
            str(int(binding.expires_at.timestamp())),
        )
    ).encode("ascii")


def _base32(value: bytes) -> str:
    return base64.b32encode(value).rstrip(b"=").decode("ascii").lower()


def _decode_base32(value: str) -> bytes:
    if not value or any(character not in _BASE32 for character in value):
        raise ValueError("Preview token base32 is invalid")
    try:
        decoded = base64.b32decode(
            value.upper() + "=" * (-len(value) % 8),
            casefold=False,
        )
    except (binascii.Error, ValueError) as error:
        raise ValueError("Preview token base32 is invalid") from error
    if _base32(decoded) != value:
        raise ValueError("Preview token base32 is not canonical")
    return decoded


_BASE32 = frozenset("abcdefghijklmnopqrstuvwxyz234567")

__all__ = ["CloudPreviewBinding", "CloudPreviewSigner"]
