from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fairy_core.domain.ids import new_id


@dataclass(frozen=True, slots=True)
class AssetVariant:
    path: str
    content_hash: str
    byte_length: int
    media_type: str
    role: str
    label: str


@dataclass(frozen=True, slots=True)
class AssetSet:
    id: UUID
    workspace_id: UUID
    version_id: UUID
    kind: str
    title: str
    variants: tuple[AssetVariant, ...]
    idempotency_key: str
    input_digest: str
    provenance: dict[str, Any] = field(default_factory=dict)
    generation_parameters: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        *,
        workspace_id: UUID,
        version_id: UUID,
        kind: str,
        title: str,
        variants: tuple[AssetVariant, ...],
        provenance: dict[str, Any],
        generation_parameters: dict[str, Any],
        idempotency_key: str,
        input_digest: str,
    ) -> AssetSet:
        if kind not in {"image", "audio", "video"}:
            raise ValueError("Asset Set kind must be image, audio, or video")
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > 200:
            raise ValueError("Asset Set title must be between 1 and 200 characters")
        if not 1 <= len(variants) <= 64:
            raise ValueError("Asset Set must contain between 1 and 64 variants")
        paths = [variant.path for variant in variants]
        if len(paths) != len(set(paths)):
            raise ValueError("Asset Set variant paths must be unique")
        if not idempotency_key.strip() or len(idempotency_key) > 512:
            raise ValueError("Asset Set idempotency key is invalid")
        if len(input_digest) != 64 or any(
            value not in "0123456789abcdef" for value in input_digest
        ):
            raise ValueError("Asset Set input digest must be lowercase SHA-256")
        return cls(
            id=new_id(),
            workspace_id=workspace_id,
            version_id=version_id,
            kind=kind,
            title=normalized_title,
            variants=variants,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            provenance=dict(provenance),
            generation_parameters=dict(generation_parameters),
        )


__all__ = ["AssetSet", "AssetVariant"]
