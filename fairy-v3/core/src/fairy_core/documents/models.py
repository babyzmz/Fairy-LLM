from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from fairy_core.domain.ids import new_id

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DocumentVisibility(StrEnum):
    CONVERSATION = "conversation"
    PROJECT = "project"


class DocumentStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class ExtractedSection:
    ordinal: int
    title: str
    text: str
    locator: Mapping[str, str | int]

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("section ordinal cannot be negative")
        title = _required_text(self.title, "section title", maximum=1_000)
        text = _required_text(self.text, "section text", maximum=5_000_000)
        locator = _locator(self.locator)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "locator", locator)


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    media_type: str
    parser: str
    parser_version: str
    sections: tuple[ExtractedSection, ...]

    def __post_init__(self) -> None:
        media_type = _media_type(self.media_type)
        parser = _required_text(self.parser, "parser", maximum=128)
        parser_version = _required_text(self.parser_version, "parser_version", maximum=128)
        if not self.sections or len(self.sections) > 10_000:
            raise ValueError("extracted document must contain between 1 and 10,000 sections")
        if tuple(section.ordinal for section in self.sections) != tuple(range(len(self.sections))):
            raise ValueError("section ordinals must be contiguous from zero")
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "parser", parser)
        object.__setattr__(self, "parser_version", parser_version)
        object.__setattr__(self, "sections", tuple(self.sections))


@dataclass(frozen=True, slots=True)
class StoredDocumentBlob:
    storage_location: str
    content_hash: str
    byte_length: int

    def __post_init__(self) -> None:
        location = _required_text(self.storage_location, "storage_location", maximum=4_096)
        if not location.startswith(("managed://", "s3://")):
            raise ValueError("document storage location must be an opaque managed URI")
        _sha256(self.content_hash, "content_hash")
        if isinstance(self.byte_length, bool) or self.byte_length < 0:
            raise ValueError("document byte_length cannot be negative")
        object.__setattr__(self, "storage_location", location)


@dataclass(slots=True)
class ManagedDocument:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    source_task_id: UUID
    version_id: UUID | None
    filename: str
    media_type: str
    byte_length: int
    content_hash: str
    storage_location: str
    current_revision: int
    visibility: DocumentVisibility
    status: DocumentStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        self.filename = normalized_filename(self.filename)
        self.media_type = _media_type(self.media_type)
        if isinstance(self.byte_length, bool) or self.byte_length < 0:
            raise ValueError("document byte_length cannot be negative")
        _sha256(self.content_hash, "document content_hash")
        StoredDocumentBlob(
            storage_location=self.storage_location,
            content_hash=self.content_hash,
            byte_length=self.byte_length,
        )
        if isinstance(self.current_revision, bool) or self.current_revision < 1:
            raise ValueError("document current_revision must be positive")
        self.visibility = DocumentVisibility(self.visibility)
        self.status = DocumentStatus(self.status)
        if self.visibility is DocumentVisibility.PROJECT and self.project_id is None:
            raise ValueError("project-visible document requires a project")
        self.idempotency_key = _required_text(
            self.idempotency_key,
            "idempotency_key",
            maximum=255,
        )
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("document updated_at cannot precede created_at")

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID | None,
        conversation_id: UUID,
        source_task_id: UUID,
        version_id: UUID | None,
        filename: str,
        media_type: str,
        blob: StoredDocumentBlob,
        visibility: DocumentVisibility,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ManagedDocument:
        created_at = now or datetime.now(UTC)
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            source_task_id=source_task_id,
            version_id=version_id,
            filename=filename,
            media_type=media_type,
            byte_length=blob.byte_length,
            content_hash=blob.content_hash,
            storage_location=blob.storage_location,
            current_revision=1,
            visibility=visibility,
            status=DocumentStatus.ACTIVE,
            idempotency_key=idempotency_key,
            created_at=created_at,
            updated_at=created_at,
        )

    @classmethod
    def restore(cls, **values: Any) -> ManagedDocument:
        return cls(**values)

    def delete(self, *, now: datetime | None = None) -> None:
        if self.status is DocumentStatus.DELETED:
            return
        deleted_at = now or datetime.now(UTC)
        _aware(deleted_at, "deleted_at")
        if deleted_at < self.updated_at:
            raise ValueError("deleted_at cannot precede updated_at")
        self.status = DocumentStatus.DELETED
        self.updated_at = deleted_at


@dataclass(frozen=True, slots=True)
class DocumentRevision:
    document_id: UUID
    revision: int
    content_hash: str
    byte_length: int
    media_type: str
    parser: str
    parser_version: str
    section_count: int
    chunk_count: int
    created_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("document revision must be positive")
        _sha256(self.content_hash, "revision content_hash")
        if isinstance(self.byte_length, bool) or self.byte_length < 0:
            raise ValueError("revision byte_length cannot be negative")
        media_type = _media_type(self.media_type)
        parser = _required_text(self.parser, "parser", maximum=128)
        parser_version = _required_text(self.parser_version, "parser_version", maximum=128)
        if not 1 <= self.section_count <= 10_000:
            raise ValueError("revision section_count is invalid")
        if not 1 <= self.chunk_count <= 100_000:
            raise ValueError("revision chunk_count is invalid")
        _aware(self.created_at, "created_at")
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "parser", parser)
        object.__setattr__(self, "parser_version", parser_version)

    @classmethod
    def create(
        cls,
        *,
        document: ManagedDocument,
        extracted: ExtractedDocument,
        chunk_count: int,
    ) -> DocumentRevision:
        return cls(
            document_id=document.id,
            revision=document.current_revision,
            content_hash=document.content_hash,
            byte_length=document.byte_length,
            media_type=extracted.media_type,
            parser=extracted.parser,
            parser_version=extracted.parser_version,
            section_count=len(extracted.sections),
            chunk_count=chunk_count,
            created_at=document.created_at,
        )

    @classmethod
    def restore(cls, **values: Any) -> DocumentRevision:
        return cls(**values)


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: UUID
    document_id: UUID
    revision: int
    revision_hash: str
    ordinal: int
    section_ordinal: int
    locator: Mapping[str, str | int]
    text: str
    content_hash: str
    token_count: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("chunk revision must be positive")
        _sha256(self.revision_hash, "chunk revision_hash")
        if isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("chunk ordinal cannot be negative")
        if isinstance(self.section_ordinal, bool) or self.section_ordinal < 0:
            raise ValueError("chunk section_ordinal cannot be negative")
        locator = _locator(self.locator)
        text = _required_text(self.text, "chunk text", maximum=20_000)
        expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if self.content_hash != expected_hash:
            raise ValueError("chunk content_hash does not match text")
        if isinstance(self.token_count, bool) or not 1 <= self.token_count <= 20_000:
            raise ValueError("chunk token_count is invalid")
        _aware(self.updated_at, "updated_at")
        object.__setattr__(self, "locator", locator)
        object.__setattr__(self, "text", text)

    @classmethod
    def create(
        cls,
        *,
        document: ManagedDocument,
        ordinal: int,
        section_ordinal: int,
        locator: Mapping[str, str | int],
        text: str,
    ) -> DocumentChunk:
        normalized = _required_text(text, "chunk text", maximum=20_000)
        return cls(
            id=new_id(),
            document_id=document.id,
            revision=document.current_revision,
            revision_hash=document.content_hash,
            ordinal=ordinal,
            section_ordinal=section_ordinal,
            locator=locator,
            text=normalized,
            content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            token_count=max(1, len(normalized.split())),
            updated_at=document.updated_at,
        )

    @classmethod
    def restore(cls, **values: Any) -> DocumentChunk:
        return cls(**values)


@dataclass(frozen=True, slots=True)
class DocumentContext:
    document: ManagedDocument
    revision: DocumentRevision

    def __post_init__(self) -> None:
        if self.revision.document_id != self.document.id:
            raise ValueError("document revision belongs to a different document")
        if self.revision.revision != self.document.current_revision:
            raise ValueError("document revision is not current")
        if self.revision.content_hash != self.document.content_hash:
            raise ValueError("document revision hash is stale")


@dataclass(frozen=True, slots=True)
class DocumentSearchHit:
    document: ManagedDocument
    revision: DocumentRevision
    chunk: DocumentChunk
    lexical_score: float
    exact_match: bool

    def __post_init__(self) -> None:
        DocumentContext(document=self.document, revision=self.revision)
        if self.chunk.document_id != self.document.id:
            raise ValueError("search chunk belongs to a different document")
        if (
            self.chunk.revision != self.revision.revision
            or self.chunk.revision_hash != self.revision.content_hash
        ):
            raise ValueError("search chunk projection is stale")
        if not math.isfinite(self.lexical_score) or not 0 <= self.lexical_score <= 1:
            raise ValueError("lexical_score must be between zero and one")


def normalized_filename(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 255
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError("filename must be a safe basename")
    return normalized


def _locator(value: Mapping[str, str | int]) -> Mapping[str, str | int]:
    if not isinstance(value, Mapping) or not value or len(value) > 16:
        raise ValueError("locator must contain between 1 and 16 fields")
    normalized: dict[str, str | int] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name).strip()
        if not name or len(name) > 64 or isinstance(raw_value, bool):
            raise ValueError("locator field is invalid")
        if isinstance(raw_value, int):
            if raw_value < 0:
                raise ValueError("locator integer cannot be negative")
            normalized[name] = raw_value
        elif isinstance(raw_value, str):
            item = raw_value.strip()
            if not item or len(item) > 1_000:
                raise ValueError("locator text is invalid")
            normalized[name] = item
        else:
            raise ValueError("locator values must be text or integers")
    json.dumps(normalized, ensure_ascii=True, allow_nan=False, sort_keys=True)
    return MappingProxyType(normalized)


def _media_type(value: str) -> str:
    normalized = _required_text(value, "media_type", maximum=255).lower()
    if "/" not in normalized or ";" in normalized:
        raise ValueError("media_type must be a canonical type/subtype")
    return normalized


def _required_text(value: str, name: str, *, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is invalid")
    return normalized


def _sha256(value: str, name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "DocumentChunk",
    "DocumentContext",
    "DocumentRevision",
    "DocumentSearchHit",
    "DocumentStatus",
    "DocumentVisibility",
    "ExtractedDocument",
    "ExtractedSection",
    "ManagedDocument",
    "StoredDocumentBlob",
    "normalized_filename",
]
